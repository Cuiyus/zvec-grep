"""Exercise unmodified main Claude agents with local scripted providers in Docker.

The explicit routing adapter changes the supported main Claude/Opus command's
model to qwen3.8-max. It does not modify the registry or the native agent loop.
Sentinel responses are infrastructure evidence, never benchmark quality results.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pilot
from repair_v2.integration import docker_info, verify_mcp_source
from repair_v2.package import EXPECTED_MEMBERS, RUNTIME_PATHS
from .accounting import analyze
from .fake_services import FakeServices, CHAT_KEY, EMBEDDING_KEY, FINAL_SENTINEL

HERE = Path(__file__).resolve().parent
MODEL = 'qwen3.8-max'
PACKAGE_SHA = 'fbe4bd201c8a0520f2373f1039e1d0805a38781c6e2f3ce11b83310d23dc6522'


@contextmanager
def fixture_environment(chat_url, embedding_url, main_root):
    saved = dict(os.environ)
    allow = {'PATH', 'HOME', 'USER', 'SHELL', 'LANG', 'LC_ALL', 'TMPDIR',
             'SSL_CERT_FILE', 'SSL_CERT_DIR'}
    env = {k: v for k, v in saved.items() if k in allow}
    env.update(ANTHROPIC_API_KEY=CHAT_KEY, ANTHROPIC_BASE_URL=chat_url,
               ZVEC_GREP_API_KEY=EMBEDDING_KEY, ZVEC_GREP_ENDPOINT=embedding_url,
               PYTHONPATH=os.pathsep.join([str(HERE.parent), str(pilot.HERE), str(main_root)]))
    os.environ.clear()
    os.environ.update(env)
    try:
        yield env
    finally:
        os.environ.clear()
        os.environ.update(saved)


def stop_process_group(proc):
    """Stop descendants even when the session leader already exited."""
    result = {'complete': False, 'process_group_id': proc.pid, 'signals': []}
    def exists():
        try:
            os.killpg(proc.pid, 0)
            return True
        except ProcessLookupError:
            return False
    try:
        if not proc.pid or proc.pid == os.getpgrp():
            raise ValueError('unsafe_process_group')
        for sig, wait in ((signal.SIGTERM, 3), (signal.SIGKILL, 2)):
            if exists():
                try:
                    os.killpg(proc.pid, sig)
                    result['signals'].append(sig.name)
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                proc.poll()
                if proc.returncode is not None and not exists():
                    result['complete'] = True
                    return result
                time.sleep(0.05)
        proc.poll()
        result['complete'] = proc.returncode is not None and not exists()
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
    return result


def build_command(runner, suite, profile, prepared, output, cache, embedding_url):
    command = runner.build_harbor_command(
        suite, profile=profile, agent='claude-code', model='claude-opus-5',
        jobs_dir=output / 'runs', job_name='q001-' + profile,
        n_attempts=1, max_retries=0,
        harbor_executable=str(Path(sys.executable).parent / 'harbor'),
        zvec_grep_package=cache.zvec_grep_package or '',
        zvec_grep_package_sha256=cache.zvec_grep_package_sha256,
        embedding_model='qwen/qwen3.7-text-embedding', embedding_endpoint=embedding_url)
    # Explicit unsupported-combination adapter, with the remaining native command
    # retained verbatim. No registry mutation or native-method monkeypatch.
    command[command.index('--model') + 1] = MODEL
    command[command.index('--agent') + 1] = 'native_claude.agents:' + (
        'QABaseline' if profile == 'baseline' else 'QAZvec')
    command += ['--agent-kwarg', 'qa_source_manifest=' + str(prepared / 'source-manifest.json'),
                '--agent-kwarg', 'qa_profile=' + profile]
    return command


def validate_inputs(repo_root, main_root):
    manifest = pilot.load(HERE.parent / 'inputs.json')
    for key, root in [('repository_files', repo_root), ('upstream_files', main_root)]:
        for relative, expected in manifest[key].items():
            if pilot.digest(pilot.safe_file(root, relative)) != expected:
                raise ValueError('input_changed:' + relative)
    for distribution, version in manifest['distributions'].items():
        if importlib.metadata.version(distribution) != version:
            raise ValueError('distribution_version:' + distribution)
    return manifest


def run_slot(args, profile, system):
    slot = {'profile': profile, 'status': 'planned', 'started_at_unix': time.time()}
    known, proc = {}, None
    with FakeServices(host='0.0.0.0', profile=profile, output=args.output / (profile + '-fake-service')) as services:
        base = f"http://{system['docker_gateway']}:{services.port}"
        chat, embedding = base + '/anthropic', base + '/v1/embeddings'
        slot['provider_endpoints'] = {'anthropic': chat, 'embedding': embedding}
        old_handler = signal.signal(signal.SIGALRM, pilot.deadline_expired)
        signal.setitimer(signal.ITIMER_REAL, 3600)
        deadline = time.monotonic() + 3600
        try:
            with fixture_environment(chat, embedding, args.main_root) as env:
                runner = pilot.native(args.main_root)
                # Both native Claude volumes must be absent, including ~/.local.
                package = runner.prepare_zvec_grep_package(str(args.candidate_tgz))
                volume = runner.setup_cache_volume_name('claude-code', profile,
                    zvec_grep_package=package.install_spec,
                    zvec_grep_package_sha256=package.sha256 if profile == 'zvec-grep' else None,
                    embedding_model='qwen/qwen3.7-text-embedding')
                volumes = [volume, runner.claude_code_install_cache_volume_name(profile)]
                for name in volumes:
                    found = subprocess.run(['docker', 'volume', 'inspect', name], capture_output=True, timeout=20)
                    if found.returncode == 0:
                        raise ValueError('install_cache_not_fresh:' + name)
                cache = runner.prepare_setup_cache('claude-code', profile,
                    zvec_grep_package=str(args.candidate_tgz), embedding_model='qwen/qwen3.7-text-embedding')
                overlay = pilot.load(cache.compose_path)
                actual = sorted(v['name'] for v in overlay['volumes'].values())
                if actual != sorted(volumes):
                    raise ValueError('fresh_cache_identity_mismatch')
                slot['fresh_volumes'] = volumes
                pilot.write(args.output / (profile + '-compose.json'), overlay)
                command = build_command(runner, runner.load_suite(args.prepared / 'suite.yaml'),
                    profile, args.prepared, args.output, cache, embedding)
                slot.update(command=command, status='running')
                pilot.write(args.output / (profile + '-slot.json'), slot)
                if profile == 'baseline':
                    env = {k: v for k, v in env.items() if not k.startswith('ZVEC_GREP_')}
                with (args.output / (profile + '-job.log')).open('wb', buffering=0) as log:
                    proc = subprocess.Popen(command, cwd=args.main_root, env=env,
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    while proc.poll() is None and time.monotonic() < deadline:
                        pilot.capture_slot_containers(args.output, profile, known)
                        try:
                            proc.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            pass
                    slot.update(status='timeout' if proc.poll() is None else 'exited',
                                returncode=124 if proc.poll() is None else proc.returncode)
        except TimeoutError:
            slot.update(status='timeout', returncode=124)
        except Exception as exc:
            slot.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            slot['process_cleanup'] = stop_process_group(proc) if proc is not None else {'complete': True, 'no_process_started': True}
            try:
                slot['container_cleanup'] = pilot.cleanup_slot_containers(args.output, profile, known)
            except Exception as exc:
                slot['container_cleanup'] = {'complete': False, 'error': type(exc).__name__}
            pilot.write(args.output / (profile + '-containers.json'), known)
            slot['finished_at_unix'] = time.time()
            try:
                slot['provider_validation'] = services.validate()
            except Exception as exc:
                slot['provider_validation'] = {'valid': False, 'error': str(exc)}
            services.dump_trace(args.output / (profile + '-provider-trace.json'))
            pilot.write(args.output / (profile + '-slot.json'), slot)
    return slot


def verify_slot(args, slot):
    profile, checks = slot['profile'], []
    def check(name, value):
        checks.append({'name': name, 'passed': bool(value)})
    check('native_process_exited', slot.get('status') == 'exited' and slot.get('returncode') == 0)
    check('containers_stopped', slot.get('container_cleanup', {}).get('complete') is True)
    check('private_process_group_stopped', slot.get('process_cleanup', {}).get('complete') is True)
    check('scripted_native_sequence', slot.get('provider_validation', {}).get('valid') is True)
    containers = pilot.load(args.output / (profile + '-containers.json'))
    mounts_valid = bool(containers)
    for container in containers.values():
        mounts = container['mounts']
        by_target = {m.get('Destination'): m for m in mounts}
        for target, volume in zip(('/root/.nvm', '/root/.local'), slot.get('fresh_volumes', [])):
            mount = by_target.get(target, {})
            mounts_valid &= mount.get('Type') == 'volume' and mount.get('Name') == volume
        mounts_valid &= len(slot.get('fresh_volumes', [])) == 2
        for target, mount in by_target.items():
            if target in ('/root/.nvm', '/root/.local'):
                continue
            source = Path(mount.get('Source', '/')).resolve()
            if target == '/tmp/zg-bench-zvec-grep.tgz':
                mounts_valid &= profile == 'zvec-grep' and mount.get('RW') is False and pilot.digest(source) == PACKAGE_SHA
            elif target.startswith('/logs/'):
                trial_root = Path(container['trial_dir']).resolve()
                mounts_valid &= source == trial_root or trial_root in source.parents
            else:
                mounts_valid = False
        if profile == 'zvec-grep':
            mounts_valid &= '/tmp/zg-bench-zvec-grep.tgz' in by_target
    check('actual_profile_cache_package_and_log_mounts', mounts_valid)
    roots = list((args.output / 'runs' / ('q001-' + profile)).glob('*/agent'))
    check('exactly_one_trial', len(roots) == 1)
    result = {'profile': profile, 'checks': checks, 'valid': False}
    if len(roots) != 1:
        return result
    agent = roots[0]
    trial = pilot.load(agent.parent / 'result.json')
    check('native_trial_no_exception', not trial.get('exception_info'))
    accounting = analyze(agent, expected_model=MODEL)
    pilot.write(args.output / (profile + '-accounting.json'), accounting)
    result['accounting'] = accounting
    check('complete_session_tree', accounting.get('complete') is True)
    check('exact_fixture_final', accounting.get('final', {}).get('text') == FINAL_SENTINEL)
    check('actual_child_persisted', accounting.get('child_count', 0) >= 1)
    # Runtime/source proof is written by an instrumentation-only subclass.
    audit_path = agent.parent / 'qa-source-audit.json'
    audit = pilot.load(audit_path) if audit_path.is_file() else {}
    result['source_audit'] = audit
    check('source_runtime_integrity', audit.get('valid') is True)
    observed = audit.get('before', {}).get('observed', {})
    runtime = observed.get('runtime', {})
    check('actual_claude_version', runtime.get('claude', {}).get('version', '').split()[0] == '2.1.212')
    trace = pilot.load(args.output / (profile + '-provider-trace.json'))
    chats = [r for r in trace.get('rows', []) if r.get('kind') == 'chat']
    roots = [r for r in chats if r.get('classification') == 'root']
    children = [r for r in chats if r.get('classification') == 'child']
    check('actual_root_and_child_requests', bool(roots) and bool(children))
    check('all_actual_models_are_qwen', chats and all(r['body'].get('model') == MODEL for r in chats))
    check('actual_root_high_effort', roots and all(r['body'].get('output_config', {}).get('effort') == 'high' for r in roots))
    counted = roots + children
    sums = {k: sum(r['usage'][k] for r in counted) for k in (
        'input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens')}
    expected_usage = {'uncached_input_tokens': sums['input_tokens'],
                      'cache_read_tokens': sums['cache_read_input_tokens'],
                      'cache_write_tokens': sums['cache_creation_input_tokens'],
                      'input_tokens': sums['input_tokens'] + sums['cache_read_input_tokens'] + sums['cache_creation_input_tokens'],
                      'output_tokens': sums['output_tokens'], 'llm_calls': len(counted)}
    result['provider_expected_tree_usage'] = expected_usage
    result['unrelated_background_request_ids'] = [r['message_id'] for r in chats if r.get('classification') == 'background']
    check('tree_usage_equals_actual_provider', all(accounting.get('usage', {}).get(k) == v for k, v in expected_usage.items()))
    provider_messages = {r['message_id']: r['usage'] for r in counted}
    retained_messages = {r['message_id']: {k: r['usage'].get(k) for k in sums}
                         for r in accounting.get('messages', []) if isinstance(r.get('usage'), dict)}
    check('each_counted_message_matches_provider', provider_messages == retained_messages)
    trajectory_path = agent / 'trajectory.json'
    trajectory = pilot.load(trajectory_path) if trajectory_path.is_file() else {}
    final_steps = [s for s in trajectory.get('steps', []) if s.get('source') == 'agent' and not s.get('tool_calls')]
    check('native_atif_final_matches', final_steps and final_steps[-1].get('message', '').strip() == FINAL_SENTINEL)
    snapshot = agent.parent / 'qa-native-claude-logs'
    relevant = lambda p: p.name == 'claude-code.txt' or (p.suffix == '.jsonl' and 'projects' in p.parts)
    native_files = {p.relative_to(agent).as_posix(): pilot.digest(p) for p in agent.rglob('*') if p.is_file() and relevant(p)}
    copied_files = {p.relative_to(snapshot).as_posix(): pilot.digest(p) for p in snapshot.rglob('*') if p.is_file() and relevant(p)}
    check('accounted_native_bytes_equal_verified_copy', bool(native_files) and native_files == copied_files)
    def all_text(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return '\n'.join(all_text(v) for v in value)
        if isinstance(value, dict):
            return '\n'.join(all_text(v) for v in value.values())
        return ''
    first_request = all_text(roots[0]['body']) if roots else ''
    guidance = observed.get('configurations', {}).get('/logs/agent/sessions/CLAUDE.md', {}).get('content', '')
    if profile == 'baseline':
        check('baseline_no_installed_zg_guidance', 'Choose the evidence source before the retrieval mode.' not in first_request)
        check('baseline_no_zg_tool', not any('zvec_grep' in t.get('name', '') for r in roots for t in r['body'].get('tools', [])))
    else:
        check('entire_installed_guidance_loaded_once', isinstance(guidance, str) and bool(guidance.strip()) and first_request.count(guidance.strip()) == 1)
        payloads = runtime.get('zg', {}).get('payloads', {})
        check('all_original_runtime_payloads', all(
            payloads.get(p.removeprefix('package/'), {}).get('sha256') == EXPECTED_MEMBERS[p][1]
            and payloads.get(p.removeprefix('package/'), {}).get('bytes') == EXPECTED_MEMBERS[p][0]
            for p in RUNTIME_PATHS))
        mcp = [r for r in roots if r.get('stage') == 'root_mcp']
        check('one_actual_correct_workspace_mcp', len(mcp) == 1 and mcp[0]['response']['content'][0]['input'].get('root') == '/workspace')
        source_match = None
        if mcp:
            tool_id = mcp[0]['response']['content'][0]['id']
            for request in roots:
                for message in request['body'].get('messages', []):
                    blocks = message.get('content', [])
                    for block in blocks if isinstance(blocks, list) else []:
                        if block.get('type') == 'tool_result' and block.get('tool_use_id') == tool_id and not block.get('is_error'):
                            try:
                                source_match = verify_mcp_source(all_text(block.get('content')), args.prepared)
                            except ValueError:
                                pass
        result['mcp_source_match'] = source_match
        check('actual_mcp_frozen_source_text', source_match is not None)
    result['valid'] = all(c['passed'] for c in checks)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo-root', 'main-root', 'prepared', 'candidate-tgz', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    for key, value in vars(args).items():
        setattr(args, key, value.resolve())
    args.output.mkdir(parents=True, exist_ok=False)
    proof = {'schema_version': 1, 'protocol': 'native-claude-offline-v1', 'valid': False,
             'quality_evaluated': False, 'errors': [], 'slots': [], 'verification': [],
             'network_scope': 'Local fake inference and embedding only; public dependency installs allowed. Not air-gapped.'}
    try:
        validate_inputs(args.repo_root, args.main_root)
        if pilot.digest(args.candidate_tgz) != PACKAGE_SHA:
            raise ValueError('candidate_package_mismatch')
        pilot.validate_prepared(args.prepared, args.repo_root / 'benchmarks/opencode-qa-pilot/experiment.json')
        system = docker_info()
        proof['system'] = system
        for profile in ('baseline', 'zvec-grep'):
            if proof['slots'] and not all(proof['slots'][-1].get(k, {}).get('complete') for k in ('container_cleanup', 'process_cleanup')):
                raise RuntimeError('previous_slot_cleanup_unconfirmed')
            slot = run_slot(args, profile, system)
            proof['slots'].append(slot)
            try:
                proof['verification'].append(verify_slot(args, slot))
            except Exception as exc:
                proof['verification'].append({'profile': profile, 'valid': False, 'error': str(exc)})
            pilot.write(args.output / 'proof.json', proof)
        proof['valid'] = len(proof['verification']) == 2 and all(v['valid'] for v in proof['verification'])
    except Exception as exc:
        proof['errors'].append({'type': type(exc).__name__, 'message': str(exc)})
    finally:
        proof['evidence_files'] = pilot.file_inventory(args.output)
        pilot.write(args.output / 'proof.json', proof)
    print(json.dumps({'valid': proof['valid'], 'errors': proof['errors'], 'output': str(args.output)}))
    return 0 if proof['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
