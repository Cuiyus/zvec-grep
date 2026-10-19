"""One real Linux Harbor/Docker pair against local scripted providers only.

No model answers are graded. The exact source question remains in the task image;
the fake service ignores its meaning and deterministically exercises transport.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import importlib.metadata
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid

import pilot
from repair_v2.fake_services import (CHAT_KEY, EMBEDDING_KEY, FINAL_SENTINEL,
    CHILD_SENTINEL, FakeServices, QUERY)
from repair_v2.gate_inputs import validate as validate_inputs

HERE = Path(__file__).resolve().parent
PROVIDER_KEY_NAMES = ('OPENAI_API_KEY', 'GLM_API_KEY', 'ZVEC_GREP_API_KEY',
    'QWEN_API_KEY', 'DASHSCOPE_API_KEY', 'QWEN_EMBEDDING_API_KEY',
    'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'GEMINI_API_KEY')
OPENCODE_LINUX_SHA256 = '6ce6570e7db9a40e7bd3304ebdfff607920bde8cafd2eb5587bd7a26f89ba0b5'


@contextmanager
def fake_environment(chat_url, embedding_url):
    saved = dict(os.environ)
    # Host credentials never enter child Harbor processes. Keep runtime paths only.
    allowed = {'PATH', 'HOME', 'USER', 'SHELL', 'LANG', 'LC_ALL', 'TMPDIR',
               'XDG_CACHE_HOME', 'PYTHONPATH', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
    base = {k: v for k, v in saved.items() if k in allowed}
    base.update(GLM_API_KEY=CHAT_KEY, OPENAI_API_KEY=CHAT_KEY,
                ZVEC_GREP_API_KEY=EMBEDDING_KEY,
                SWE_QA_MODEL_BASE_URL=chat_url, GLM_BASE_URL=chat_url,
                OPENAI_BASE_URL=chat_url, ZVEC_GREP_ENDPOINT=embedding_url)
    os.environ.clear()
    os.environ.update(base)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def docker_info():
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise ValueError('real Linux x86_64 Docker is required; no host/mock fallback')
    info = json.loads(subprocess.check_output(['docker', 'info', '--format', '{{json .}}'], text=True, timeout=30))
    if info.get('OSType') != 'linux' or info.get('Architecture') not in ('x86_64', 'amd64'):
        raise ValueError('Docker daemon must be Linux x86_64')
    gateway = subprocess.check_output(['docker', 'network', 'inspect', 'bridge', '--format', '{{(index .IPAM.Config 0).Gateway}}'], text=True, timeout=30).strip()
    address = ipaddress.ip_address(gateway)
    if not address.is_private or address.is_loopback or address.version != 4:
        raise ValueError('expected private IPv4 host bridge gateway')
    return {'os': platform.platform(), 'python': platform.python_version(),
            'docker_server_version': info['ServerVersion'], 'docker_architecture': info['Architecture'],
            'docker_gateway': gateway, 'harbor_version': importlib.metadata.version('harbor'),
            'compose_version': subprocess.check_output(['docker', 'compose', 'version', '--short'], text=True, timeout=30).strip()}


def assert_can_start_next(cleanup):
    if cleanup.get('complete') is not True:
        raise RuntimeError('previous_slot_cleanup_unconfirmed')


def terminate_process(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


def validate_provider_command(command, chat_url, profile):
    configs = [json.loads(x.split('=', 1)[1]) for x in command if x.startswith('opencode_config=')]
    if len(configs) != 1:
        raise ValueError('exactly one native OpenCode config required')
    config = configs[0]
    providers = config.get('provider', {})
    if set(providers) != {'custom-openai'} or providers['custom-openai']['options']['baseURL'] != chat_url:
        raise ValueError('provider destination is not the local fixture')
    if config.get('model') not in (None, 'custom-openai/qwen3.8-max'):
        raise ValueError('unexpected default model')
    # All inherited agent/background models must remain in the same explicit provider.
    for name, agent in config.get('agent', {}).items():
        if agent.get('model') not in (None, 'custom-openai/qwen3.8-max'):
            raise ValueError('unexpected agent model:' + name)
    return config


def native_pair(main_root, prepared, output, candidate, plan, plan_path, services):
    output.mkdir()
    runner = pilot.native(main_root)
    suite = runner.load_suite(prepared / 'suite.yaml')
    harbor = Path(sys.executable).parent / 'harbor'
    if not harbor.is_file():
        raise ValueError('use the exact-main locked Harbor Python')
    record = {'schema_version': 1, 'protocol_id': plan['protocol_id'], 'plan_sha256': pilot.digest(plan_path),
              'prepared': str(prepared), 'candidate_sha256': pilot.digest(candidate),
              'dry_run': False, 'slots': [], 'quality_evaluated': False}
    pilot.write(output / 'execution.json', record)
    for profile in plan['profiles']:
        if record['slots']:
            try:
                assert_can_start_next(record['slots'][-1]['container_cleanup'])
            except RuntimeError:
                record['slots'].append({'profile': profile, 'status': 'not_started',
                    'reason': 'previous_slot_cleanup_unconfirmed', 'returncode': 125})
                pilot.write(output / 'execution.json', record)
                break
        services.profile = profile
        slot = {'profile': profile, 'status': 'planned', 'started_at_unix': time.time()}
        record['slots'].append(slot)
        containers, proc = {}, None
        old_handler = signal.signal(signal.SIGALRM, pilot.deadline_expired)
        signal.setitimer(signal.ITIMER_REAL, plan['slot_timeout_sec'])
        try:
            # Never silently reuse a preexisting success marker/zg installation.
            volume = runner.setup_cache_volume_name('opencode', profile,
                zvec_grep_package=str(candidate), zvec_grep_package_sha256=plan['candidate_sha256'] if profile == 'zvec-grep' else None,
                embedding_model=plan['embedding']['model'])
            exists = subprocess.run(['docker', 'volume', 'inspect', volume], capture_output=True, timeout=20)
            if exists.returncode == 0:
                raise ValueError('offline gate requires a fresh native install cache:' + volume)
            slot['install_cache_absent_before_prepare'] = True
            slot['install_cache_volume'] = volume
            cache = runner.prepare_setup_cache('opencode', profile,
                zvec_grep_package=str(candidate), embedding_model=plan['embedding']['model'])
            overlay = pilot.load(cache.compose_path)
            actual_volume = overlay['volumes']['agent-setup-cache']['name']
            if actual_volume != volume:
                raise ValueError('cache identity differs from checked-fresh volume')
            pilot.write(output / (profile + '-compose-overlay.json'), overlay)
            package = cache.zvec_grep_package if profile == 'zvec-grep' else str(candidate)
            package_sha = cache.zvec_grep_package_sha256 if profile == 'zvec-grep' else plan['candidate_sha256']
            if package_sha != plan['candidate_sha256']:
                raise ValueError('native package preparation changed candidate identity')
            command = pilot.build_command(runner, suite, profile, prepared, output,
                package, package_sha, plan, harbor)
            command[command.index('--agent') + 1] = 'repair_v2.qa_agents:' + ('QABaseline' if profile == 'baseline' else 'QAZvec')
            config = validate_provider_command(command, plan['provider']['base_url'], profile)
            pilot.write(output / (profile + '-native-config.json'), config)
            environment = runner.execution_environment(agent='opencode', model=plan['agent']['model'])
            environment['PYTHONPATH'] = os.pathsep.join([str(HERE.parent), str(main_root)])
            if profile == 'baseline':
                environment.pop('ZVEC_GREP_API_KEY', None)
                environment.pop('ZVEC_GREP_ENDPOINT', None)
            slot.update(command=command, status='running',
                provider_environment_names=sorted(k for k in PROVIDER_KEY_NAMES if environment.get(k)))
            pilot.write(output / (profile + '-slot.json'), slot)
            pilot.write(output / 'execution.json', record)
            with (output / (profile + '-job.log')).open('wb', buffering=0) as log:
                proc = subprocess.Popen(command, cwd=main_root, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + plan['slot_timeout_sec']
                while proc.poll() is None and time.monotonic() < deadline:
                    pilot.capture_slot_containers(output, profile, containers)
                    pilot.write(output / (profile + '-containers.json'), containers)
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                if proc.poll() is None:
                    terminate_process(proc)
                    slot.update(status='timeout', returncode=124)
                else:
                    slot.update(status='exited', returncode=proc.returncode)
        except TimeoutError:
            slot.update(status='timeout', returncode=124)
        except Exception as exc:
            slot.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            if proc is not None:
                terminate_process(proc)
            try:
                slot['container_cleanup'] = pilot.cleanup_slot_containers(output, profile, containers)
            except Exception as exc:
                slot['container_cleanup'] = {'complete': False, 'error_type': type(exc).__name__}
            pilot.write(output / (profile + '-containers.json'), containers)
            slot['finished_at_unix'] = time.time()
            pilot.write(output / (profile + '-slot.json'), slot)
            pilot.write(output / 'execution.json', record)
    services.profile = 'complete'
    return record


def verify_mcp_source(output, prepared):
    # Native a424 renders both numbered entity lines and lexical `123:\t` /
    # `123-\t` lines. At least one untruncated source line must match the corpus.
    for block in re.split(r'(?m)(?=^#\d+[ \[])', output):
        header = re.search(r'^#\d+[^\n]*? ([^\s]+):(\d+)(?:-(\d+))?$', block, re.MULTILINE)
        if not header:
            continue
        relative = Path(header.group(1))
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('MCP returned a source outside the fixed corpus')
        source = prepared / 'tasks/zvec-q001/environment/source' / relative
        if not source.is_file():
            raise ValueError('MCP returned a path absent from the fixed corpus')
        source_lines = source.read_text().splitlines()
        for number, text in re.findall(r'^(\d+)[:-]?\t(.*)$', block, re.MULTILINE):
            if text and 1 <= int(number) <= len(source_lines) and source_lines[int(number) - 1] == text:
                return {'path': relative.as_posix(), 'line': int(number)}
    raise ValueError('MCP source text does not match frozen corpus')


def validate_native_proof(collection, run_dir, services, package_manifest, prepared):
    if collection.get('structural_valid') is not True:
        raise ValueError('native collector rejected one or both synthetic slots')
    rows = {}
    for report in collection['profiles']:
        profile = report['profile']
        trial = run_dir / 'runs' / ('q001-' + profile) / report['retained_trials'][0]
        usage = pilot.load(trial / 'agent/session-usage.json')
        if len(usage['sessions']) != 2 or sum(s.get('parent_session_id') is not None for s in usage['sessions']) != 1:
            raise ValueError('exactly one root and one real child session required')
        calls = 5 if profile == 'baseline' else 6
        for key, expected in {'llm_calls': calls, 'input_tokens': calls * 100,
                              'output_tokens': calls * 50, 'cache_read_tokens': calls * 60,
                              'reasoning_tokens': calls * 20}.items():
            if usage['total'].get(key) != expected:
                raise ValueError('native recursive usage mismatch:' + profile + ':' + key)
        if report['final']['answer'] != FINAL_SENTINEL:
            raise ValueError('native final sentinel mismatch:' + profile)
        if report['runtime']['opencode']['binary_sha256'] != OPENCODE_LINUX_SHA256:
            raise ValueError('unexpected Linux OpenCode executable')
        child = next(s for s in usage['sessions'] if s.get('parent_session_id'))
        child_export = pilot.load(trial / 'qa-session-exports' / (child['session_id'] + '.json'))
        text = [p['text'] for m in child_export['messages'] for p in m.get('parts', []) if p.get('type') == 'text' and m.get('info', {}).get('role') == 'assistant']
        if CHILD_SENTINEL not in text:
            raise ValueError('native child final sentinel missing')
        tools = [p for session in usage['sessions']
                 for m in pilot.load(trial / 'qa-session-exports' / (session['session_id'] + '.json'))['messages']
                 for p in m.get('parts', []) if p.get('type') == 'tool']
        expected_tools = ['read', 'read', 'task']
        ordinary_tools = sorted(p.get('tool') for p in tools if 'zvec_grep_search' not in p.get('tool', ''))
        if ordinary_tools != expected_tools or len(tools) != (3 if profile == 'baseline' else 4):
            raise ValueError('native tool set/count differs from scripted sequence')
        if any(p.get('state', {}).get('status') != 'completed' for p in tools):
            raise ValueError('synthetic native tool did not complete')
        mcp = [p for p in tools if 'zvec_grep_search' in p.get('tool', '')]
        if profile == 'baseline' and mcp:
            raise ValueError('baseline unexpectedly accessed MCP')
        if profile == 'zvec-grep':
            setup = report['zg_setup']
            if (setup.get('status') != 'ready' or setup.get('install_reused_cache') is not False
                    or setup.get('index_cache_hit') is not False or setup.get('authorization_scope') != 'workspace'):
                raise ValueError('fresh real zg grant/index setup not proven')
            expected_payloads = {row['path'].removeprefix('package/'): {
                'sha256': row['derived_sha256'], 'bytes': row['derived_bytes'], 'mode': row['mode']}
                for row in package_manifest['runtime_payloads']}
            if report['runtime']['zg'].get('payloads') != expected_payloads:
                raise ValueError('installed native runtime payload hashes differ from derived archive')
            if len(mcp) != 1 or mcp[0]['state'].get('input') != {'root': '/workspace', 'query': QUERY}:
                raise ValueError('exactly one real local-root MCP query required')
            output = mcp[0]['state'].get('output', '')
            if 'freshness:' not in output:
                raise ValueError('real MCP query did not return an index freshness result')
            verify_mcp_source(output, prepared)
        rows[profile] = {'root_session_id': usage['root_session_id'], 'child_session_id': child['session_id'],
                         'llm_calls': calls, 'tool_count': len(tools), 'mcp_tool_count': len(mcp),
                         'final_sentinel_confirmed': True, 'quality_evaluated': False}
    return {'valid': True, 'profiles': rows, 'stub': services.validate()}


async def docker_negative_cases(prepared, output):
    """Exercise capture/download through Harbor's actual multiplexing Docker backend."""
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.task.config import EnvironmentConfig
    from harbor.models.trial.paths import TrialPaths
    from repair_v2.evidence import capture_json, download_json
    trial = output / 'docker-negative-trial'
    paths = TrialPaths(trial_dir=trial)
    paths.mkdir()
    session = 'qa-v2-negative-' + uuid.uuid4().hex[:12]
    env = DockerEnvironment(environment_dir=prepared / 'tasks/zvec-q001/environment',
        environment_name='zvec-q001', session_id=session, trial_paths=paths,
        task_env_config=EnvironmentConfig(build_timeout_sec=1200.0))
    record = {'backend': 'harbor.environments.docker.docker.DockerEnvironment', 'session': session,
              'cases': [], 'complete': False}
    pilot.write(output / 'docker-negative-cases.json', record)
    startup_complete = False
    try:
        await asyncio.wait_for(env.start(force_build=False), timeout=1200)
        startup_complete = True
        cases = [
            ('stderr-json', "python3 -c 'import sys; print(\"{\\\"ok\\\":true}\"); print(\"native warning\",file=sys.stderr)'", True, 10),
            ('nonzero-json', "python3 -c 'import sys; print(\"{\\\"ok\\\":true}\"); sys.exit(7)'", False, 10),
            ('malformed-json', "python3 -c 'print(\"{\\\"partial\\\":\")'", False, 10),
            ('timeout-partial', "python3 -u -c 'import time; print(\"{\\\"partial\\\":\"); time.sleep(300)'", False, 1),
        ]
        for label, command, should_pass, timeout in cases:
            row = {'label': label, 'expected_success': should_pass}
            try:
                value = await capture_json(env, output / 'docker-evidence', label, command, timeout)
                row['succeeded'] = True
                if value.data != {'ok': True}:
                    raise ValueError('unexpected parsed capture value')
            except Exception as exc:
                row['succeeded'] = False
                row['error_type'] = type(exc).__name__
                row['error_code'] = getattr(exc, 'code', None)
            capture_dir = output / 'docker-evidence' / label
            metadata = pilot.load(capture_dir / 'capture.json')
            row['capture_sha256'] = pilot.digest(capture_dir / 'capture.json')
            if label == 'stderr-json':
                if (capture_dir / 'stderr.txt').read_bytes() != b'native warning\n' or (capture_dir / 'status.txt').read_bytes() != b'0\n':
                    raise ValueError('stderr or status was not preserved')
            elif label == 'nonzero-json':
                if row.get('error_code') != 'command_status_not_zero' or (capture_dir / 'status.txt').read_bytes() != b'7\n':
                    raise ValueError('nonzero status was not preserved and rejected')
            elif label == 'malformed-json':
                if row.get('error_code') != 'malformed_json' or not (capture_dir / 'stdout.json').read_bytes().startswith(b'{"partial":'):
                    raise ValueError('malformed bytes were not preserved and rejected')
            elif label == 'timeout-partial':
                if row.get('error_code') != 'command_transport_failed' or not (capture_dir / 'stdout.json').read_bytes().startswith(b'{"partial":'):
                    raise ValueError('timeout partial bytes were not preserved')
                if metadata['files']['status.txt']['complete']:
                    raise ValueError('timeout unexpectedly produced terminal status')
            record['cases'].append(row)
            pilot.write(output / 'docker-negative-cases.json', record)
            if row['succeeded'] != should_pass:
                raise ValueError('Docker evidence negative outcome mismatch:' + label)
        row = {'label': 'missing-child-download', 'expected_success': False}
        try:
            await download_json(env, output / 'docker-evidence', 'missing-child-download', '/tmp/no-such-offline-v2-child.json')
            row['succeeded'] = True
        except Exception as exc:
            row.update(succeeded=False, error_type=type(exc).__name__)
        record['cases'].append(row)
        if row['succeeded']:
            raise ValueError('missing file accepted')
    finally:
        try:
            await asyncio.wait_for(env.stop(delete=True), timeout=60)
            remaining = subprocess.check_output(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + session], text=True, timeout=20).split()
            record['cleanup'] = {'complete': startup_complete and not remaining, 'remaining_running': remaining,
                                 'startup_complete': startup_complete,
                                 'limit': 'A failed/timed-out startup is never treated as confirmed cleanup.'}
        except Exception as exc:
            record['cleanup'] = {'complete': False, 'error_type': type(exc).__name__}
        pilot.write(output / 'docker-negative-cases.json', record)
    assert_can_start_next(record['cleanup'])
    # Inject uncertainty only at the controller policy boundary after a real timeout
    # and verified real cleanup. No Docker stop error is fabricated as an observation.
    next_slot_started = False
    try:
        assert_can_start_next({'complete': False})
        next_slot_started = True
    except RuntimeError:
        pass
    if next_slot_started:
        raise ValueError('cleanup uncertainty did not stop the next slot')
    record.update(complete=True, uncertain_cleanup_policy={'injected_status': {'complete': False},
        'next_slot_started': next_slot_started, 'scope': 'controller branch; actual Docker timeout/cleanup recorded above'})
    pilot.write(output / 'docker-negative-cases.json', record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('main-root', 'repo-root', 'prepared', 'candidate-tgz', 'package-manifest', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    for key, value in vars(args).items():
        setattr(args, key, value.resolve())
    if args.output.exists():
        raise ValueError('integration output already exists; no overwrite or implicit retry')
    args.output.mkdir(parents=True)
    proof = {'schema_version': 1, 'scope': 'provider-offline-infrastructure-only',
             'valid': False, 'quality_evaluated': False, 'live_provider_calls_authorized': False,
             'started_at_unix': time.time(), 'errors': []}
    try:
        if any(os.environ.get(k) for k in PROVIDER_KEY_NAMES):
            raise ValueError('live/provider credential environment must be absent before offline integration')
        inputs = validate_inputs(args.repo_root, args.main_root)
        proof.update(inputs_revision=inputs['revision'], inputs_sha256=pilot.digest(HERE / 'inputs.json'),
                     input_files=inputs['files'])
        if pilot.digest(args.candidate_tgz) != inputs['install_package_sha256']:
            raise ValueError('derived install package differs from frozen input')
        manifest = pilot.load(args.package_manifest)
        if (manifest.get('install_package_sha256') != inputs['install_package_sha256']
                or manifest.get('upstream_archive_sha256') != inputs['original_package_sha256']
                or manifest.get('binary_sha256') != inputs['rust_binary_sha256']):
            raise ValueError('package manifest identity differs from frozen input')
        from repair_v2.package import EXPECTED_MEMBERS, RUNTIME_PATHS
        actual_payloads = {r['path']: (r['derived_bytes'], r['derived_sha256'], r['mode']) for r in manifest['runtime_payloads']}
        expected_payloads = {p: (EXPECTED_MEMBERS[p][0], EXPECTED_MEMBERS[p][1], f'{EXPECTED_MEMBERS[p][2]:04o}') for p in RUNTIME_PATHS}
        if actual_payloads != expected_payloads or len(manifest['runtime_payloads']) != 4:
            raise ValueError('package manifest must bind all four exact original runtime payloads')
        pilot.write(args.output / 'package-manifest.json', manifest)
        original_plan = args.repo_root / 'benchmarks/opencode-qa-pilot/experiment.json'
        pilot.validate_prepared(args.prepared, original_plan)
        system = docker_info()
        if system['harbor_version'] != '0.18.0':
            raise ValueError('Harbor must be exactly 0.18.0')
        proof['system'] = system
        proof['source_manifest_sha256'] = pilot.digest(args.prepared / 'source-manifest.json')
        proof['prepared_manifest_sha256'] = pilot.digest(args.prepared / 'prepared.json')
        proof['candidate_sha256'] = pilot.digest(args.candidate_tgz)
        with FakeServices(args.output / 'fake-services') as services:
            base = f"http://{system['docker_gateway']}:{services.port}/v1"
            plan = pilot.load(original_plan)
            plan.update(protocol_id='main-opencode-q001-v2-offline-infrastructure', phase='provider-offline',
                        frozen=False, candidate_sha256=inputs['install_package_sha256'])
            plan['provider']['base_url'] = base
            plan['embedding']['endpoint'] = base + '/embeddings'
            plan_path = args.output / 'offline-plan.json'
            pilot.write(plan_path, plan)
            proof['network_scope'] = {'provider_base_url': base, 'embedding_endpoint': base + '/embeddings',
                'provider_config_checked_before_slots': True, 'credentials': 'hardcoded local fake keys only',
                'dependency_downloads': 'Native apt/curl/nvm/npm installation may access public dependency endpoints.',
                'airgapped': False, 'network_isolation_claimed': False,
                'limitation': 'This proves explicit provider configuration and observed stub requests, not a packet-level audit of every native background connection.'}
            with fake_environment(base, base + '/embeddings'):
                pilot.native(args.main_root)
                proof['negative_proof'] = asyncio.run(docker_negative_cases(args.prepared, args.output))
                run_dir = args.output / 'run'
                record = native_pair(args.main_root, args.prepared, run_dir, args.candidate_tgz, plan, plan_path, services)
                proof['execution'] = record
                collection = pilot.collect(plan, plan_path, args.main_root, run_dir, args.output / 'collection')
                proof['native_proof'] = validate_native_proof(collection, run_dir, services, manifest, args.prepared)
        proof['valid'] = True
    except Exception as exc:
        proof['errors'].append({'type': type(exc).__name__, 'message': str(exc)})
    finally:
        proof['finished_at_unix'] = time.time()
        proof['evidence_files'] = pilot.file_inventory(args.output)
        pilot.write(args.output / 'proof.json', proof)
    print(json.dumps({'valid': proof['valid'], 'proof': str(args.output / 'proof.json'), 'errors': proof['errors']}))
    return 0 if proof['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
