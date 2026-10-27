"""Real Qwen format probes using the production OpenCode/MCP adapter.

Only these controlled probes use a loopback forwarding recorder. It captures
actual provider-visible tools and raw tool-call argument fragments, excluding
credentials, message text and reasoning. No probe answers enter formal scores.
"""
from __future__ import annotations
import argparse
import asyncio
import copy
import importlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time
from unittest.mock import patch
import uuid

from zg_bench.rg_environment_probe import agent_spec, command_value, write_json
from zg_bench.mcp_description_e2e import entry

PORT = 18765
PROBES = {
    'queries': ['dependency graph lifecycle', 'package recipe configuration'],
    'fts': ['ConanFile', 'GraphManager'],
    'vector': ['dependency graph lifecycle', 'package recipe configuration'],
}


def proxy_source(upstream):
    return 'const upstream = ' + json.dumps(upstream.rstrip('/')) + ';\n' + r'''
const http = require('http');
const fs = require('fs');
let sequence = 0;
const save = (name, value) => fs.appendFileSync('/logs/agent/' + name + '.jsonl', JSON.stringify(value) + '\n');
http.createServer(async (request, response) => {
  const id = ++sequence;
  try {
    let raw = '';
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    save('model-requests', {id, model: body.model, tools: body.tools,
      controls: Object.fromEntries(Object.entries(body).filter(([k]) =>
        ['temperature', 'reasoning_effort', 'max_tokens', 'max_completion_tokens', 'stream', 'enable_thinking', 'seed'].includes(k)))});
    const headers = {'content-type': 'application/json'};
    if (request.headers.authorization) headers.authorization = request.headers.authorization;
    const result = await fetch(upstream + request.url.replace(/^\/v1/, ''), {method: 'POST', headers, body: raw});
    response.writeHead(result.status, {'content-type': result.headers.get('content-type') || 'text/event-stream'});
    const decoder = new TextDecoder();
    let pending = '';
    for await (const chunk of result.body) {
      response.write(chunk);
      pending += decoder.decode(chunk, {stream: true});
      const lines = pending.split('\n');
      pending = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;
        try {
          const data = JSON.parse(line.slice(6));
          for (const choice of data.choices || []) {
            const calls = choice.delta?.tool_calls || choice.message?.tool_calls;
            if (calls) save('raw-tool-call-fragments', {request: id, model: data.model, calls});
          }
          if (data.usage) save('provider-usage', {request: id, usage: data.usage, model: data.model});
        } catch {}
      }
    }
    response.end();
  } catch (error) {
    save('proxy-errors', {id, type: error.name});
    if (!response.headersSent) response.writeHead(502);
    response.end('Forwarding recorder failed');
  }
}).listen(18765, '127.0.0.1', () => fs.writeFileSync('/logs/agent/proxy.ready', 'ready'));
'''


def validate(field, snapshot, requests):
    assert requests and all(r.get('model') == 'qwen3.8-max' for r in requests), 'Unexpected model ID'
    rows = [(r['kind'], json.loads(r['data']) if isinstance(r['data'], str) else r['data']) for r in snapshot]
    tools = [r for kind,r in rows if kind == 'tool' and 'zvec_grep_search' in r['name']]
    visible = [t['function'] for r in requests for t in r.get('tools', [])
               if 'zvec_grep_search' in t['function']['name']]
    assert visible, 'No actual provider-visible MCP tool definition captured'
    for tool in visible:
        assert 'Do not JSON-stringify arrays.' in tool['description']
        for name in PROBES:
            assert 'Do not JSON-stringify arrays.' in tool['parameters']['properties'][name]['description']
    assert tools, 'Qwen did not use the search tool'
    accepted = []
    for tool in tools:
        inputs = tool.get('input') or {}
        for name in PROBES:
            value = inputs.get(name)
            if isinstance(value, str):
                try:
                    assert not isinstance(json.loads(value), list), f'{name} was a JSON-stringified array'
                except json.JSONDecodeError:
                    pass
        values = inputs.get(field)
        output = tool.get('output') or ''
        if isinstance(values, list) and len(values) == 2 and all(isinstance(v,str) for v in values):
            assert tool['status'] == 'completed', 'List call failed'
            assert 'query groups (2):' in output, 'Two real query groups not demonstrated in reply'
            accepted.append(dict(call_id=tool.get('call_id'), input=inputs,
                query_groups=2, returned_items=len(re.findall(r'^#\d+', output, re.M)),
                returned_chars=len(output), output=output))
    assert accepted, f'No real two-string array for {field}; scalar-only use is not success'
    return dict(passed=True, field=field, search_calls=len(tools), provider_definitions=len(visible),
                accepted_calls=accepted, raw_model_identities=sorted({r['model'] for r in requests}))


async def run_case(field, task, output, packages, identity):
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.agent.context import AgentContext
    from harbor.models.trial.paths import TrialPaths
    from zg_bench import runner
    from zg_bench.engines.registry import resolve_opencode_model
    from zg_bench.agents.opencode_usage import summarize_session_usage
    from zg_bench.agents.ripgrep import validate_ripgrep_preflight
    qwen = entry()
    experiment = qwen.configure('smoke')
    runtime = resolve_opencode_model('custom-openai/qwen3.8-max', require_credentials=True)
    paths = TrialPaths(output)
    paths.mkdir(); paths.chmod_dir()
    changes = dict(ZG_BENCH_DISABLE_SUBAGENTS='1', ZG_BENCH_CLI_RUNTIME='rust',
                   ZG_BENCH_EXPECTED_CLI_SHA256=identity['cli_sha256'], ZG_BENCH_CAPTURE_INDEX_OUTPUT='1')
    with patch.dict(os.environ, changes):
        source = qwen.command(task.name, 'rust', output, packages, 1)
        package = command_value(source, '--zvec-grep-package')
        prepared = runner.prepare_setup_cache('opencode', 'zvec-grep',
            zvec_grep_package=package, embedding_model=experiment['embedding_model'])
        suite = runner.load_suite('swe-qa-bench', tier='full', task_overrides=[task.name])
        command = runner.build_harbor_command(suite, profile='zvec-grep', agent='opencode',
            model='custom-openai/qwen3.8-max', jobs_dir=output, job_name='mcp-description-probe',
            zvec_grep_package=prepared.zvec_grep_package or package,
            zvec_grep_package_sha256=prepared.zvec_grep_package_sha256,
            embedding_model=experiment['embedding_model'])
        adapter, model, kwargs = agent_spec(command)
        config = copy.deepcopy(kwargs['opencode_config'])
        options = config['provider']['custom-openai']['options']
        upstream = options['baseURL']
        options['baseURL'] = f'http://127.0.0.1:{PORT}/v1'
        kwargs['opencode_config'] = config
        module, name = adapter.split(':')
        agent = getattr(importlib.import_module(module), name)(logs_dir=paths.agent_dir, model_name=model, **kwargs)
        environment = DockerEnvironment(environment_dir=task.paths.environment_dir,
            environment_name=task.name, session_id='mcp-probe-'+uuid.uuid4().hex[:16],
            trial_paths=paths, task_env_config=task.config.environment,
            extra_docker_compose=[prepared.compose_path], mounts=[
                {'type':'bind','source':str(paths.agent_dir),'target':'/logs/agent'},
                {'type':'bind','source':str(paths.verifier_dir),'target':'/logs/verifier'},
                {'type':'bind','source':str(paths.artifacts_dir),'target':'/logs/artifacts'}])
        (paths.agent_dir/'proxy.cjs').write_text(proxy_source(upstream))
        prompt = (f'Use zvec_grep_search once with the {field} field for two independent searches: '
                  + ' and '.join(repr(x) for x in PROBES[field])
                  + '. Keep them independent. Then report the query-group count from the tool reply.')
        write_json(output/'probe-protocol.json', dict(field=field, prompt=prompt,
            excluded_from_formal=True, original_model_settings=qwen.validate_model_settings(),
            transport_only_change='Loopback streaming forwarder records provider-visible tool definitions and raw tool-call arguments'))
        evidence = dict(passed=False, field=field, excluded_from_formal=True)
        started = time.monotonic()
        try:
            await environment.start(force_build=False)
            with environment.with_default_user(task.config.agent.user), environment.scoped_exec_env({
                'OPENAI_API_KEY':runtime.api_key, 'OPENCODE_DISABLE_MODELS_FETCH':'true',
                'OPENCODE_DISABLE_EXTERNAL_SKILLS':'true', 'OPENCODE_DISABLE_PROJECT_CONFIG':'true'}):
                await asyncio.wait_for(agent.setup(environment), timeout=1800)
                await agent.exec_as_agent(environment, command=(
                    '. ~/.nvm/nvm.sh; nohup node /logs/agent/proxy.cjs </dev/null '
                    '>/logs/agent/proxy.log 2>&1 &'), timeout_sec=10)
                for _ in range(50):
                    if (paths.agent_dir/'proxy.ready').exists(): break
                    await asyncio.sleep(0.1)
                else: raise RuntimeError('Forwarding recorder not ready')
                await asyncio.wait_for(agent.run(prompt, environment, AgentContext()), timeout=600)
            await environment.prepare_logs_for_host()
            preflight = json.loads((paths.agent_dir/'ripgrep-preflight.json').read_text())
            validate_ripgrep_preflight(preflight)
            snapshot = json.loads((paths.agent_dir/'opencode-session-snapshot.json').read_text())
            requests = [json.loads(line) for line in (paths.agent_dir/'model-requests.jsonl').read_text().splitlines()]
            evidence.update(validate(field, snapshot, requests))
            roots = [json.loads(r['data']) if isinstance(r['data'],str) else r['data'] for r in snapshot if r['kind']=='session']
            root = [r for r in roots if not r.get('parent_id')]
            assert len(root)==1 and len(roots)==1, 'Unexpected subagent in format probe'
            evidence['usage'] = summarize_session_usage(snapshot, root[0]['id'])
            evidence['ripgrep_preflight'] = preflight
        except Exception as error:
            evidence.update(passed=False, error_type=type(error).__name__, error=str(error))
        finally:
            try: await environment.stop(delete=True)
            except Exception as error: evidence.update(passed=False, cleanup_error=type(error).__name__)
            evidence['wall_seconds_including_setup'] = time.monotonic()-started
            write_json(output/'summary.json',evidence)
        return evidence


async def run(args):
    from harbor.models.task.task import Task
    from zg_bench import version_e2e as versions
    output = args.output.resolve(); output.mkdir(parents=True,exist_ok=True)
    identity = versions.identity(args.packages.resolve()/'rust','rust')
    task_copy = output/'task'/'conan-19'
    shutil.copytree(versions.ROOT/'datasets/conan-19',task_copy)
    image = versions.prebuild_task_image(task_copy,output/'task')
    task = Task(task_copy)
    summary = dict(kind='real-qwen-mcp-description-format-probes', passed=False,
                   excluded_from_formal=True, task_image=image, package_identity=identity, cases=[])
    write_json(output/'summary.json',summary)
    for field in PROBES:
        print('FORMAT PROBE START '+field,flush=True)
        case = await run_case(field,task,output/field,args.packages.resolve(),identity)
        summary['cases'].append(case)
        write_json(output/'summary.json',summary)
        print('FORMAT PROBE COMPLETE '+field+' passed='+str(case['passed']),flush=True)
    summary['passed'] = len(summary['cases'])==3 and all(c['passed'] for c in summary['cases'])
    write_json(output/'summary.json',summary)
    return summary


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--packages',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    raise SystemExit(0 if asyncio.run(run(args))['passed'] else 1)

if __name__ == '__main__': main()
