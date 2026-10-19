"""Capture exactly one native baseline default-background Explore with fake services.

The slot controller below is the frozen R3 controller with only FakeServices
changed to ProbeServices. Native command, model, setup, deadline and cleanup stay
identical. Legacy accounting is retained as diagnosis, not changed to pass.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import subprocess
import time

import pilot
from native_claude.integration import (
    PACKAGE_SHA, MODEL, build_command, fixture_environment, stop_process_group,
    validate_inputs, docker_info,
)
from native_claude.accounting import analyze
from native_claude.fake_services import FINAL_SENTINEL
from .services import ProbeServices, PREFIX

def run_slot(args, profile, system):
    slot = {'profile': profile, 'status': 'planned', 'started_at_unix': time.time()}
    known, proc = {}, None
    with ProbeServices(host='0.0.0.0', profile=profile, output=args.output / (profile + '-fake-service')) as services:
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


def observe(args, slot):
    """Keep unknown shapes verbatim; an old accounting rejection is not repaired."""
    observations = {'execution_completed': False, 'observed_async_launch': False,
                    'background_coverage': False, 'task_output_covered': False,
                    'quality_evaluated': False, 'all_native_child_modes_validated': False,
                    'legacy_accounting_used_as_execution_gate': False, 'event_evidence': [], 'checks': {}}
    def check(name, value):
        observations['checks'][name] = bool(value)
    check('native_controller_exited', slot.get('status') == 'exited' and slot.get('returncode') == 0)
    check('process_cleanup_complete', slot.get('process_cleanup', {}).get('complete') is True)
    check('container_cleanup_complete', slot.get('container_cleanup', {}).get('complete') is True)
    check('fake_transport_completed', slot.get('provider_validation', {}).get('valid') is True)
    roots = list((args.output / 'runs' / 'q001-baseline').glob('*/agent'))
    check('exactly_one_trial', len(roots) == 1)
    if len(roots) != 1:
        return observations
    agent = roots[0]
    trial = pilot.load(agent.parent / 'result.json') if (agent.parent / 'result.json').exists() else {}
    check('native_trial_no_exception', bool(trial) and not trial.get('exception_info'))
    audit = pilot.load(agent.parent / 'qa-source-audit.json') if (agent.parent / 'qa-source-audit.json').exists() else {}
    check('source_and_native_log_capture_valid', audit.get('valid') is True)
    legacy = analyze(agent, expected_model=MODEL)
    pilot.write(args.output / 'legacy-accounting.json', legacy)
    observations['legacy_accounting'] = {'complete': legacy['complete'], 'errors': legacy['errors'],
                                       'root_session_id': legacy['root_session_id'], 'child_count': legacy['child_count']}
    relevant = lambda p: p.name == 'claude-code.txt' or (p.suffix == '.jsonl' and 'projects' in p.parts)
    native = {p.relative_to(agent).as_posix(): pilot.digest(p) for p in agent.rglob('*') if p.is_file() and relevant(p)}
    snapshot = agent.parent / 'qa-native-claude-logs'
    copied = {p.relative_to(snapshot).as_posix(): pilot.digest(p) for p in snapshot.rglob('*') if p.is_file() and relevant(p)}
    check('native_bytes_match_separate_capture', bool(native) and native == copied)
    observations['native_files'] = native
    check('actual_persisted_child', any('/subagents/agent-' in p for p in native))
    stream = agent / 'claude-code.txt'
    terminal = []
    task_outputs, launch_statuses = [], []
    for relative in sorted(native):
        path = agent / relative
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            event = json.loads(line)
            message = event.get('message', {})
            blocks = message.get('content', []) if isinstance(message, dict) else []
            if relative == 'claude-code.txt' and event.get('type') == 'result':
                terminal.append(event)
            tool_blocks = [b for b in blocks if isinstance(b, dict) and b.get('type') in ('tool_use', 'tool_result')] if isinstance(blocks, list) else []
            launch_results = [b for b in tool_blocks if b.get('tool_use_id') == PREFIX + 'root_child']
            waits = [b for b in tool_blocks if b.get('name') == 'TaskOutput' or str(b.get('tool_use_id', '')).startswith(PREFIX + 'root_wait_')]
            # Completion notifications and unknown system event shapes are all
            # retained, not filtered through an invented completion schema.
            is_notification = '<task-notification>' in line or event.get('type') not in ('assistant', 'user')
            if launch_results or waits or is_notification:
                observations['event_evidence'].append({'file': relative, 'line': number,
                    'file_sha256': native[relative], 'line_text_sha256': __import__('hashlib').sha256(line.encode()).hexdigest(),
                    'event': event, 'launch_result': bool(launch_results), 'task_output': bool(waits)})
            if launch_results:
                metadata = event.get('toolUseResult', event.get('tool_use_result', {}))
                launch_statuses.append({'file': relative, 'line': number, 'metadata': metadata})
            task_outputs.extend(b for b in waits if b.get('type') == 'tool_use')
    unique_waits = {b['id']: b for b in task_outputs}
    observations['launch_status_evidence'] = launch_statuses
    # Only a concrete native async_launched status establishes this observation.
    # Other shapes remain visible for offline inspection without being approved.
    observations['observed_async_launch'] = any(
        isinstance(row['metadata'], dict) and row['metadata'].get('status') == 'async_launched'
        for row in launch_statuses)
    observations['task_output_covered'] = bool(unique_waits)
    observations['task_output_calls'] = list(unique_waits.values())
    check('exact_native_root_completion', len(terminal) == 1 and terminal[0].get('subtype') == 'success'
          and terminal[0].get('is_error') is False and terminal[0].get('result') == FINAL_SENTINEL)
    observations['execution_completed'] = all(observations['checks'].values())
    observations['background_coverage'] = (observations['execution_completed']
        and observations['observed_async_launch'] and observations['task_output_covered'])
    observations['limitations'] = [
        'Default-background Explore was requested without model override; actual async observation is recorded separately.',
        'A synchronous completion can complete this capture without establishing background coverage.',
        'Unknown returned IDs, tool schemas or completion shapes fail scripting but remain in raw provider/native evidence.',
        'Legacy accounting is diagnostic and may reject an otherwise completed native async trace.',
        'No QA quality or all-native-child completeness conclusion follows from this finite fake-provider probe.']
    return observations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo-root', 'main-root', 'prepared', 'candidate-tgz', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    for key, value in vars(args).items():
        setattr(args, key, value.resolve())
    args.output.mkdir(parents=True, exist_ok=False)
    proof = {'protocol': 'native-claude-async-probe-v1', 'execution_completed': False,
             'observed_async_launch': False, 'background_coverage': False,
             'quality_evaluated': False, 'real_provider_calls_authorized': False,
             'errors': [], 'slots': [], 'network_scope': 'Fake inference only; public dependency downloads allowed.'}
    try:
        validate_inputs(args.repo_root, args.main_root)
        if pilot.digest(args.candidate_tgz) != PACKAGE_SHA:
            raise ValueError('candidate_package_mismatch')
        original_plan = args.repo_root / 'benchmarks/opencode-qa-pilot/experiment.json'
        pilot.validate_prepared(args.prepared, original_plan)
        decision = args.repo_root / 'benchmarks/native-claude-qa-pilot/async-probe-decision-v1.json'
        declaration = pilot.load(decision)
        if declaration.get('protocol') != proof['protocol'] or declaration.get('status') != 'offline_capture_only':
            raise ValueError('probe_decision_mismatch')
        proof['decision_sha256'] = pilot.digest(decision)
        proof['probe_sources'] = {p.name: pilot.digest(p) for p in Path(__file__).parent.glob('*.py')}
        system = docker_info()
        proof['system'] = system
        slot = run_slot(args, 'baseline', system)
        proof['slots'].append(slot)
        observations = observe(args, slot)
        proof['observations'] = observations
        for key in ('execution_completed', 'observed_async_launch', 'background_coverage'):
            proof[key] = observations[key]
    except Exception as exc:
        proof['errors'].append({'type': type(exc).__name__, 'message': str(exc)})
    finally:
        proof['evidence_files'] = [row for row in pilot.file_inventory(args.output) if row.get('path') != 'probe.json']
        pilot.write(args.output / 'probe.json', proof)
    print(json.dumps({key: proof[key] for key in ('execution_completed', 'observed_async_launch', 'background_coverage', 'errors')}))
    return 0 if proof['execution_completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
