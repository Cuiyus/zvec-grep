"""Six upstream generations, paired original/reference-inlined MCP metadata.

Titles and post-tool summaries are local synthetic stop responses in both arms.
Native MCP execution is real; this is excluded from answer/quality evaluation.
"""
from __future__ import annotations
import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
from unittest.mock import patch

from zg_bench import mcp_description_probe as base
from zg_bench.rg_environment_probe import write_json

APPROVED_SCHEMA_SHA256 = '22cc7083170c144a57f9a8578d3f62686cf1cda6ba1614dac23765c042aff287'


def inline_query_refs(original):
    result = copy.deepcopy(original)
    schema = result['parameters']
    definition = schema['$defs']['QueryListInput']
    for field in base.PROBES:
        assert schema['properties'][field]['anyOf'][0] == {'$ref': '#/$defs/QueryListInput'}
        schema['properties'][field]['anyOf'][0] = copy.deepcopy(definition)
    del schema['$defs']['QueryListInput']
    restored = copy.deepcopy(result)
    for field in base.PROBES:
        restored['parameters']['properties'][field]['anyOf'][0] = {'$ref': '#/$defs/QueryListInput'}
    restored['parameters']['$defs']['QueryListInput'] = definition
    assert restored == original
    return result


def proxy_source(upstream, arm, expected, proposal):
    config = dict(upstream=upstream.rstrip('/'), arm=arm, expected=expected,
                  proposal=proposal, logs='/logs/agent', port=base.PORT)
    template = Path(__file__).with_name('mcp_schema_reference_proxy.cjs').read_text()
    return 'const config = ' + json.dumps(config) + ';\n' + template


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def inspect_case(root, field, arm, expected, proposal):
    agent = root / 'agent'
    requests = read_lines(agent / 'model-requests.jsonl')
    attempts = read_lines(agent / 'outbound-attempts.jsonl')
    started = [row for row in attempts if row['event'] == 'started']
    assert len(started) == len(requests) == 1, 'Exactly one upstream attempt per case'
    assert any(row['event'] == 'completed' and row['status'] == 200 for row in attempts), 'Upstream completion required; never retry automatically'
    assert not read_lines(agent / 'proxy-errors.jsonl'), 'Transport integrity failed'
    request = requests[0]
    visible = [tool['function'] for tool in request['tools'] if tool['function']['name'] == expected['name']]
    assert visible == [expected if arm == 'original' else proposal]
    assert request['schema_only_change_verified'] and not request['local_text_in_messages']
    assert request['model'] == 'qwen3.8-max'
    assert request['controls'] == dict(max_tokens=32000, temperature=0, seed=42,
                                    enable_thinking=True, reasoning_effort='high', stream=True)
    raw = {}
    for fragment in read_lines(agent / 'raw-tool-call-fragments.jsonl'):
        for call in fragment['calls']:
            key = (fragment['request'], call.get('index', 0))
            item = raw.setdefault(key, dict(id='', name='', arguments=''))
            if call.get('id'): item['id'] = call['id']
            fn = call.get('function') or {}
            item['name'] += fn.get('name') or ''
            item['arguments'] += fn.get('arguments') or ''
    snapshot = json.loads((agent / 'opencode-session-snapshot.json').read_text())
    rows = [(r['kind'], json.loads(r['data']) if isinstance(r['data'], str) else r['data']) for r in snapshot]
    sessions = [r for kind, r in rows if kind == 'session']
    assert len(sessions) == 1 and not sessions[0].get('parent_id')
    tools = [r for kind, r in rows if kind == 'tool' and 'zvec_grep_search' in r['name']]
    calls = []
    for tool in tools:
        matched = [r for r in raw.values() if r['id'] == tool['call_id']]
        assert len(matched) == 1 and json.loads(matched[0]['arguments']) == tool['input'], 'API-to-MCP arguments changed'
        output = tool.get('output') or ''
        groups = re.search(r'^query groups \((\d+)\):', output, re.M)
        calls.append(dict(input=tool['input'], status=tool['status'],
                          group_count_header=int(groups[1]) if groups else None,
                          observed_group_labels=sorted(set(re.findall(r'^groups:.*?\b(Q\d+)#', output, re.M))),
                          raw_arguments=matched[0]['arguments'], call_id=tool['call_id'],
                          actual_output=output))
    only_requested_search = False
    if len(calls) == len(raw) == 1:
        arguments = calls[0]['input']
        only_requested_search = (
            arguments.get('root') == '/app'
            and arguments.get(field) == base.PROBES[field]
            and all(arguments.get(other) in (None, [], '')
                    for other in ('query', *base.PROBES) if other != field)
            and arguments.get('fuse') is not True)
    passed = (only_requested_search and calls[0]['status'] == 'completed'
              and calls[0]['group_count_header'] == 2)
    return dict(field=field, arm=arm, passed=passed, upstream_attempts=1,
                normalized_body_sha256=request['normalized_body_sha256'],
                messages_sha256=request['messages_sha256'],
                outbound_body_sha256=request['outbound_body_sha256'],
                raw_calls=list(raw.values()), search_calls=calls,
                actual_provider_tool_definition=visible[0],
                provider_usage=read_lines(agent / 'provider-usage.jsonl'),
                local_synthetic_responses=read_lines(agent / 'local-responses.jsonl'))


async def run(args):
    from harbor.models.task.task import Task
    from zg_bench import version_e2e as versions
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    schema_audit = json.loads(args.schema_audit.read_text())
    assert schema_audit['passed']
    native = next(t for t in schema_audit['after']['toolsets']['agent']['tools'] if t['name'] == 'zvec_grep_search')
    expected = dict(name='zvec_grep_zvec_grep_search', description=native['description'], parameters=native['inputSchema'])
    proposal = inline_query_refs(expected)
    digest = hashlib.sha256(json.dumps(proposal, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert digest == APPROVED_SCHEMA_SHA256, 'Diagnostic must match the approved schema exactly'
    identity = versions.identity(args.packages.resolve() / 'rust', 'rust')
    task_copy = output / 'task' / 'conan-19'
    shutil.copytree(versions.ROOT / 'datasets/conan-19', task_copy)
    image = versions.prebuild_task_image(task_copy, output / 'task')
    task = Task(task_copy)
    summary = dict(kind='six-generation-schema-reference-diagnostic', excluded_from_formal=True,
                   complete=False, package_identity=identity, task_image=image,
                   budget=6, cases=[], pairs=[], titles_and_summaries='local synthetic stop, both arms')
    write_json(output / 'summary.json', summary)
    try:
        # Adjacent pairs reduce temporal differences; each uses a fresh session.
        for field in base.PROBES:
            pair = []
            for arm in ('original', 'inline'):
                case_root = output / arm / field
                print(f'SCHEMA PROBE START {field} {arm}', flush=True)
                with patch.object(base, 'proxy_source', lambda upstream: proxy_source(upstream, arm, expected, proposal)):
                    original_summary = await base.run_case(field, task, case_root, args.packages.resolve(), identity)
                protocol_path = case_root / 'probe-protocol.json'
                protocol = json.loads(protocol_path.read_text())
                protocol['transport_only_change'] = (
                    'Paired diagnostic: at most one upstream generation; auxiliary title and post-tool '
                    'summary are synthetic local stops in both arms. Inline arm changes only three '
                    'QueryListInput references and removes their unused definition.')
                protocol.update(arm=arm, upstream_attempt_limit=1, full_e2e=False)
                write_json(protocol_path, protocol)
                case = inspect_case(case_root, field, arm, expected, proposal)
                case['base_probe_passed'] = original_summary['passed']
                summary['cases'].append(case)
                pair.append(case)
                write_json(output / 'summary.json', summary)
                print(f'SCHEMA PROBE COMPLETE {field} {arm} passed={case["passed"]}', flush=True)
            assert pair[0]['normalized_body_sha256'] == pair[1]['normalized_body_sha256'], 'Paired request context differs beyond schema'
            summary['pairs'].append(dict(field=field, normalized_requests_identical=True,
                                         original_passed=pair[0]['passed'], inline_passed=pair[1]['passed']))
        assert sum(c['upstream_attempts'] for c in summary['cases']) == 6
        summary['complete'] = True
        summary['original_all_passed'] = all(c['passed'] for c in summary['cases'] if c['arm'] == 'original')
        summary['inline_all_passed'] = all(c['passed'] for c in summary['cases'] if c['arm'] == 'inline')
    finally:
        write_json(output / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--schema-audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args))['complete'] else 1)


if __name__ == '__main__':
    main()
