#!/usr/bin/env python3
"""One independently frozen q001 pair through the exact upstream OpenCode stack."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
MAIN = 'a424e0ce06868dc9ad8f142f57de59b8f9b58699'
FIXED = {
    'schema_version': 1, 'protocol_id': 'main-opencode-q001-v1',
    'phase': 'main-opencode-development', 'upstream_main': MAIN, 'task_id': 'q001',
    'dataset_sha256': '3b1f43387e7dd4f86f911b44fbd781b3f24a8747f0560ee2233cda3dd989f685',
    'question_sha256': '94aad1d075ef44984c2e6c330bca8d3dc6c273c1c7044c20b4bdd47c6c7854ef',
    'rubric_sha256': '6da038c670a93213095bb3ac07c77a67f9863defa23f8ac4a3b6fd2af8a81cb9',
    'source_commit': 'ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d',
    'source_file_count': 1232,
    'source_inventory_sha256': '741d06d8fc216150933c5ed87f9a100e0d2120b2dc09a9109084365e92d307ba',
    'profiles': ['baseline', 'zvec-grep'], 'n_attempts': 1, 'max_retries': 0,
    'agent_timeout_sec': 1800,
    'slot_timeout_sec': 3600,
    'agent': {'name': 'opencode', 'version': '1.18.4',
              'model': 'custom-openai/qwen3.8-max', 'output_limit': 32000},
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text())


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def safe_file(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('unsafe relative path')
    if not path.is_file() or path.is_symlink() or root.resolve() not in path.resolve().parents:
        raise ValueError('file missing or escaped: ' + relative)
    return path


def file_inventory(root: Path) -> list[dict]:
    rows = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('symlink forbidden: ' + str(path))
        if path.is_file():
            rows.append({'path': path.relative_to(root).as_posix(),
                         'bytes': path.stat().st_size, 'sha256': digest(path)})
    return rows


def validate_plan(plan_path: Path, repo_root: Path, main_root: Path) -> dict:
    plan = load(plan_path)
    if plan.get('frozen') is not True:
        raise ValueError('plan must be explicitly frozen before setup/provider work')
    for key, value in FIXED.items():
        if plan.get(key) != value or type(plan.get(key)) is not type(value):
            raise ValueError('frozen protocol mismatch: ' + key)
    embedding = plan.get('embedding', {})
    if embedding.get('model') != 'qwen/qwen3.7-text-embedding':
        raise ValueError('only the declared native remote embedding is allowed')
    if not isinstance(embedding.get('endpoint'), str) or not embedding['endpoint'].startswith('https://'):
        raise ValueError('explicit HTTPS embedding endpoint required')
    candidate_hash = plan.get('candidate_sha256', '')
    if len(candidate_hash) != 64 or any(c not in '0123456789abcdef' for c in candidate_hash):
        raise ValueError('candidate SHA256 required')
    qa = repo_root / 'benchmarks/zvec-issue-qa'
    for rel, key in [('dataset.json', 'dataset_sha256'),
                     ('tasks/q001/instruction.md', 'question_sha256'),
                     ('evaluator/q001.json', 'rubric_sha256')]:
        if digest(safe_file(qa, rel)) != plan[key]:
            raise ValueError('frozen q001 asset mismatch: ' + rel)
    expected = plan.get('main_file_sha256', {})
    required = {p.relative_to(main_root).as_posix()
                for p in (main_root / 'zg_bench').rglob('*.py')}
    required |= {'pyproject.toml', 'uv.lock'}
    if not isinstance(expected, dict) or not required <= set(expected) or len(required) < 10:
        raise ValueError('pin all main Python files plus pyproject.toml and uv.lock')
    for rel, sha in expected.items():
        if digest(safe_file(main_root, rel)) != sha:
            raise ValueError('main file hash mismatch: ' + rel)
    for rel, sha in plan.get('frozen_harness_sha256', {}).items():
        if digest(safe_file(repo_root, rel)) != sha:
            raise ValueError('frozen pilot harness hash mismatch: ' + rel)
    return plan


def native(main_root: Path):
    main_root = main_root.resolve()
    sys.path[:0] = [str(main_root), str(HERE)]
    module = importlib.import_module('zg_bench.runner')
    if main_root not in Path(module.__file__).resolve().parents:
        raise ValueError('loaded an unpinned zg_bench module')
    return module


def prepare(plan: dict, plan_path: Path, repo_root: Path, main_root: Path, zvec_repo: Path, output: Path):
    if output.exists():
        raise ValueError('prepared output must not exist')
    output.mkdir(parents=True)
    task = output / 'tasks/zvec-q001'
    env = task / 'environment'
    env.mkdir(parents=True)
    subprocess.run([sys.executable, str(repo_root / 'benchmarks/zvec-issue-qa/prepare_corpus.py'),
                    '--repo', str(zvec_repo), '--output', str(env / 'source'),
                    '--manifest', str(output / 'source-manifest.json'), '--task', 'q001',
                    '--dataset', str(repo_root / 'benchmarks/zvec-issue-qa/dataset.json')], check=True)
    manifest = load(output / 'source-manifest.json')
    if manifest['inventory_sha256'] != plan['source_inventory_sha256'] or manifest['source_file_count'] != 1232:
        raise ValueError('exported corpus identity mismatch')
    shutil.copyfile(repo_root / 'benchmarks/zvec-issue-qa/tasks/q001/instruction.md', task / 'instruction.md')
    (env / 'Dockerfile').write_text(
        'FROM ubuntu:24.04\nARG DEBIAN_FRONTEND=noninteractive\n'
        'RUN apt-get update && apt-get install -y --no-install-recommends '
        'ca-certificates git python3 libgomp1 libstdc++6 && rm -rf /var/lib/apt/lists/*\n'
        'COPY source/ /workspace/\nWORKDIR /workspace\n')
    (task / 'task.toml').write_text(
        'version = "1.0"\n[metadata]\ntask_id = "zvec-q001"\n'
        '[agent]\ntimeout_sec = 1800.0\n[verifier]\ntimeout_sec = 60.0\n'
        '[environment]\nbuild_timeout_sec = 1200.0\n')
    (task / 'tests').mkdir()
    (task / 'tests/test.sh').write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nmkdir -p /logs/verifier\n'
        'test -d /workspace\necho 1 > /logs/verifier/reward.txt\n')
    shutil.copyfile(main_root / 'zg_bench/swe_qa/data/index.ignore', output / 'index.ignore')
    (output / 'suite.yaml').write_text(
        'name: opencode-q001-development\npath: tasks\nindex_ignore_file: index.ignore\n'
        'tiers:\n  smoke:\n    tasks: [zvec-q001]\n')
    rows = file_inventory(output)
    write(output / 'prepared.json', {'plan_sha256': digest(plan_path), 'files': rows,
                                    'source_inventory_sha256': plan['source_inventory_sha256']})


def validate_prepared(prepared: Path, plan_path: Path):
    metadata = load(prepared / 'prepared.json')
    if metadata['plan_sha256'] != digest(plan_path):
        raise ValueError('prepared plan identity changed')
    observed = [r for r in file_inventory(prepared) if r['path'] != 'prepared.json']
    if observed != metadata['files']:
        raise ValueError('prepared files changed or gained unexpected content')
    return metadata


def build_command(runner, suite, profile, prepared, output, candidate, candidate_hash,
                  plan, harbor_executable):
    command = runner.build_harbor_command(
        suite, profile=profile, agent='opencode', model=plan['agent']['model'],
        jobs_dir=output / 'runs', job_name='q001-' + profile,
        n_attempts=1, max_retries=0, harbor_executable=str(harbor_executable),
        zvec_grep_package=str(candidate), zvec_grep_package_sha256=candidate_hash,
        embedding_model=plan['embedding']['model'], embedding_endpoint=plan['embedding']['endpoint'])
    # This subclass changes only host integrity instrumentation, not native run/config.
    command[command.index('--agent') + 1] = 'qa_agents:' + ('QABaseline' if profile == 'baseline' else 'QAZvec')
    command += ['--agent-kwarg', 'qa_source_manifest=' + str(prepared / 'source-manifest.json'),
                '--agent-kwarg', 'qa_profile=' + profile]
    return command


def _execute_slot(main_root, prepared, output, candidate, plan, profile, harbor, dry_run):
    os.setsid()
    slot = {'profile': profile, 'status': 'running'}
    try:
        with (output / (profile + '-job.log')).open('ab', buffering=0) as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            runner = native(main_root)
            suite = runner.load_suite(prepared / 'suite.yaml')
            environment = runner.execution_environment(agent='opencode', model=plan['agent']['model'])
            environment['PYTHONPATH'] = os.pathsep.join([str(HERE), str(main_root)])
            package, package_hash = str(candidate), plan['candidate_sha256']
            if not dry_run:
                cache = runner.prepare_setup_cache('opencode', profile,
                    zvec_grep_package=str(candidate), embedding_model=plan['embedding']['model'])
                if profile == 'zvec-grep':
                    package, package_hash = cache.zvec_grep_package, cache.zvec_grep_package_sha256
                    if package_hash != plan['candidate_sha256']:
                        raise ValueError('prepared Rust package identity mismatch')
            command = build_command(runner, suite, profile, prepared, output, package,
                                    package_hash, plan, harbor)
            slot['command'] = command
            write(output / (profile + '-slot.json'), slot)
            if dry_run:
                slot['status'] = 'dry_run'
            else:
                proc = subprocess.run(command, cwd=main_root, env=environment, check=False)
                slot.update(returncode=proc.returncode, status='exited')
    except Exception as exc:
        slot.update(status='failed', error_type=type(exc).__name__)
    finally:
        write(output / (profile + '-slot.json'), slot)


def capture_slot_containers(output: Path, profile: str, known: dict):
    """Inspect only exact Harbor project names belonging to this slot."""
    for config in (output / 'runs' / ('q001-' + profile)).glob('*/config.json'):
        project = re.sub(r'[^a-z0-9_-]', '-', (config.parent.name + '__env').lower())
        if not re.match(r'^[a-z0-9]', project):
            project = '0' + project
        ids = subprocess.check_output(['docker', 'ps', '-aq', '--filter',
            'label=com.docker.compose.project=' + project], text=True, timeout=20).split()
        for cid in ids:
            if cid in known:
                continue
            fields = subprocess.check_output(['docker', 'inspect', '--format',
                '{{json .Id}}\t{{json .Name}}\t{{json .Image}}\t{{json .Mounts}}\t{{json .State.Running}}',
                cid], text=True, timeout=20).strip().split('\t')
            identifier, name, image, mounts, running = map(json.loads, fields)
            known[cid] = {'id': identifier, 'name': name, 'image': image,
                          'mounts': mounts, 'running_when_observed': running,
                          'compose_project': project, 'trial_dir': str(config.parent)}


def cleanup_slot_containers(output: Path, profile: str, known: dict) -> dict:
    capture_slot_containers(output, profile, known)
    stopped, remaining = [], []
    for cid in known:
        found = subprocess.check_output(['docker', 'ps', '-q', '--filter', 'id=' + cid],
                                        text=True, timeout=20).strip()
        if found:
            result = subprocess.run(['docker', 'stop', '--time', '5', cid],
                                    capture_output=True, timeout=20, check=False)
            stopped.append({'id': cid, 'returncode': result.returncode})
        if subprocess.check_output(['docker', 'ps', '-q', '--filter', 'id=' + cid],
                                   text=True, timeout=20).strip():
            remaining.append(cid)
    return {'complete': not remaining, 'stopped': stopped, 'remaining_running': remaining}


def check_mounts(containers: dict, profile: str, plan: dict):
    if not containers:
        raise ValueError('no container identity/mount evidence')
    for container in containers.values():
        for mount in container['mounts']:
            destination = mount.get('Destination', '')
            source = Path(mount.get('Source', '/')).resolve()
            if destination == '/root/.nvm':
                if mount.get('Type') != 'volume' or ('-' + profile + '-') not in mount.get('Name', ''):
                    raise ValueError('wrong profile install-cache mount')
            elif destination == '/tmp/zg-bench-zvec-grep.tgz':
                if profile != 'zvec-grep' or mount.get('RW') is not False or digest(source) != plan['candidate_sha256']:
                    raise ValueError('invalid candidate mount')
            elif destination.startswith('/logs/'):
                trial = Path(container['trial_dir']).resolve()
                if source != trial and trial not in source.parents:
                    raise ValueError('logs mount outside this trial')
            else:
                raise ValueError('unapproved container mount: ' + destination)


def stop_group(process):
    if process.is_alive():
        os.killpg(process.pid, signal.SIGTERM)
        process.join(5)
        if process.is_alive():
            os.killpg(process.pid, signal.SIGKILL)
            process.join(5)


def validate_runtime_inputs(plan, main_root):
    runner = native(main_root)
    runner.validate_profile_credentials(plan['profiles'], agent='opencode',
        model=plan['agent']['model'], embedding_model=plan['embedding']['model'],
        embedding_endpoint=plan['embedding']['endpoint'])
    from zg_bench.engines.registry import resolve_opencode_model
    resolved = resolve_opencode_model(plan['agent']['model'], require_credentials=True)
    if resolved.configuration.base_url != plan.get('provider', {}).get('base_url'):
        raise ValueError('resolved provider endpoint differs from frozen plan')
    if os.environ.get('ZVEC_GREP_ENDPOINT') != plan['embedding']['endpoint']:
        raise ValueError('embedding endpoint environment differs from frozen plan')


def deadline_expired(signum, frame):
    raise TimeoutError('whole slot deadline exceeded')


def run_pair(plan, plan_path, main_root, prepared, output, candidate, dry_run=False):
    validate_prepared(prepared, plan_path)
    if candidate.is_symlink() or digest(candidate) != plan['candidate_sha256']:
        raise ValueError('Rust candidate tgz hash mismatch')
    if not dry_run:
        validate_runtime_inputs(plan, main_root)
    if output.exists():
        raise ValueError('run output must not exist; reruns/replacements forbidden')
    output.mkdir(parents=True)
    harbor = Path(sys.executable).parent / 'harbor'
    if not harbor.is_file():
        raise ValueError('use the exact-main locked Harbor virtualenv Python')
    record = {'schema_version': 1, 'protocol_id': plan['protocol_id'],
              'plan_sha256': digest(plan_path), 'prepared': str(prepared),
              'candidate_sha256': digest(candidate), 'dry_run': dry_run, 'slots': []}
    write(output / 'execution.json', record)
    # Profile-specific install cache paths from upstream; source and HOME are fresh per trial.
    for profile in plan['profiles']:
        slot = {'profile': profile, 'started_at_unix': time.time(), 'status': 'planned'}
        record['slots'].append(slot)
        write(output / 'execution.json', record)
        containers = {}
        process = None
        old_handler = signal.signal(signal.SIGALRM, deadline_expired)
        try:
            process = multiprocessing.get_context('fork').Process(target=_execute_slot,
                args=(main_root, prepared, output, candidate, plan, profile, harbor, dry_run))
            process.start()
            # Interrupt even a stalled Docker-inspection subprocess at the slot deadline.
            signal.setitimer(signal.ITIMER_REAL, plan['slot_timeout_sec'])
            deadline = time.monotonic() + plan['slot_timeout_sec']
            while process.is_alive() and time.monotonic() < deadline:
                process.join(min(1, max(0, deadline - time.monotonic())))
                if not dry_run:
                    capture_slot_containers(output, profile, containers)
                    write(output / (profile + '-containers.json'), containers)
            if process.is_alive():
                stop_group(process)
                slot.update(status='timeout', returncode=124)
            else:
                slot.update(load(output / (profile + '-slot.json')))
        except TimeoutError:
            slot.update(status='timeout', returncode=124)
        except Exception as exc:
            slot.update(status='failed', error_type=type(exc).__name__)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            if process is not None:
                stop_group(process)
            if not dry_run:
                try:
                    slot['container_cleanup'] = cleanup_slot_containers(output, profile, containers)
                except Exception as exc:
                    slot['container_cleanup'] = {'complete': False, 'error_type': type(exc).__name__}
                write(output / (profile + '-containers.json'), containers)
            slot['finished_at_unix'] = time.time()
            write(output / 'execution.json', record)
        if not dry_run and slot.get('container_cleanup', {}).get('complete') is not True:
            for pending in plan['profiles'][len(record['slots']):]:
                record['slots'].append({'profile': pending, 'status': 'not_started',
                                        'reason': 'previous_slot_cleanup_unconfirmed', 'returncode': 125})
            write(output / 'execution.json', record)
            break
    return record


def final_answer(stream: Path, trajectory: dict, usage: dict, snapshot: list, root_export: dict) -> dict:
    root = usage['root_session_id']
    events = []
    for line in stream.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            if line.lstrip().startswith(('{', '[')):
                raise ValueError('malformed JSON event in stream')
            continue  # Native CLI startup diagnostics are not event text.
        if not isinstance(event, dict) or 'type' not in event:
            raise ValueError('unrecognized stream object')
        if event.get('sessionID') != root:
            raise ValueError('stream contains a non-root or missing session identity')
        events.append(event)
    turn = None
    finished = []
    for event in events:
        kind, part = event['type'], event.get('part', {})
        if kind == 'error':
            raise ValueError('root error event')
        if kind == 'step_start':
            if turn is not None:
                raise ValueError('nested/unfinished root turn')
            turn = {'message_id': part.get('messageID'), 'texts': [], 'tools': []}
        elif kind in ('text', 'reasoning', 'tool_use', 'step_finish'):
            if turn is None or not turn['message_id'] or part.get('messageID') != turn['message_id']:
                raise ValueError('part outside matching root turn')
            if kind == 'text':
                turn['texts'].append(part.get('text', ''))
            elif kind == 'tool_use':
                turn['tools'].append(part)
            elif kind == 'step_finish':
                turn['reason'] = part.get('reason')
                finished.append(turn)
                turn = None
        elif kind != 'user':
            raise ValueError('unknown event type')
    if turn is not None or not finished or events[-1]['type'] != 'step_finish':
        raise ValueError('root lacks a complete terminal turn')
    final = finished[-1]
    answer = '\n'.join(final['texts']).strip()
    if final['reason'] != 'stop' or final['tools'] or not answer:
        raise ValueError('terminal turn is not a tool-free final answer')
    messages = []
    for row in snapshot:
        data = row.get('data', {})
        if isinstance(data, str):
            data = json.loads(data)
        if row.get('kind') == 'message' and data.get('id') == final['message_id']:
            messages.append(data)
    if len(messages) != 1 or messages[0].get('session_id') != root or not messages[0].get('finished') or messages[0].get('has_error'):
        raise ValueError('final root message not completed in DB snapshot')
    from zg_bench.swe_qa.collect import _trajectory_metrics
    if _trajectory_metrics(trajectory)['answer'] != answer:
        raise ValueError('ATIF final answer disagrees with root terminal event')
    if trajectory.get('session_id') != root:
        raise ValueError('ATIF root identity mismatch')
    assistants = [m for m in root_export.get('messages', []) if m.get('info', {}).get('role') == 'assistant']
    if root_export.get('info', {}).get('id') != root or not assistants:
        raise ValueError('root export identity mismatch or missing assistant')
    last = assistants[-1]
    info = last.get('info', {})
    exported_text = '\n'.join(p.get('text', '') for p in last.get('parts', []) if p.get('type') == 'text').strip()
    if (info.get('id') != final['message_id'] or info.get('sessionID') != root
            or not info.get('time', {}).get('completed') or info.get('error')
            or info.get('finish') != 'stop' or exported_text != answer):
        raise ValueError('root export does not confirm the same complete final answer')
    return {'root_session_id': root, 'message_id': final['message_id'], 'answer': answer,
            'answer_sha256': hashlib.sha256(answer.encode()).hexdigest()}


def collect(plan, plan_path, main_root, run_dir, output):
    if output.exists():
        raise ValueError('collection output must not exist')
    output.mkdir(parents=True)
    native(main_root)
    from zg_bench.swe_qa.collect import collect_pair, _profile_result
    from zg_bench.metrics.usage import validate_session_usage
    record = load(run_dir / 'execution.json')
    errors = []
    if record.get('plan_sha256') != digest(plan_path) or record.get('dry_run') is not False:
        errors.append('execution_identity_or_dry_run')
    if [s.get('profile') for s in record.get('slots', [])] != plan['profiles']:
        errors.append('expected_exactly_two_ordered_slots')
    if any(s.get('status') != 'exited' or s.get('returncode') != 0
           or s.get('container_cleanup', {}).get('complete') is not True for s in record.get('slots', [])):
        errors.append('a_slot_failed_timed_out_or_cleanup_unconfirmed')
    reports = []
    for profile in plan['profiles']:
        report = {'profile': profile, 'structural_valid': False, 'protocol_valid': None,
                  'protocol_status': 'pending_tool_audit', 'errors': []}
        job = run_dir / 'runs' / ('q001-' + profile)
        trials = sorted(p.parent for p in job.glob('*/result.json'))
        report['retained_trials'] = [p.name for p in trials]
        try:
            if len(trials) != 1:
                raise ValueError('expected exactly one trial, including failures')
            trial = trials[0]
            check_mounts(load(run_dir / (profile + '-containers.json')), profile, plan)
            result = load(trial / 'result.json')
            report['metrics'] = _profile_result(profile=profile, job_dir=job,
                trial_dir=trial, result=result, trial_index=1)
            audit = load(trial / 'qa-source-audit.json')
            if audit.get('valid') is not True or audit.get('profile') != profile:
                raise ValueError('source/isolation audit incomplete or invalid')
            report['source_audit'] = audit
            usage = validate_session_usage(load(trial / 'agent/session-usage.json'), require_identity=True)
            selected_snapshot = load(trial / 'agent/opencode-session-snapshot.json')
            selected_ids = {s['session_id'] for s in usage['sessions']}
            for row in selected_snapshot:
                data = row.get('data', {})
                if isinstance(data, str):
                    data = json.loads(data)
                if row.get('kind') == 'message' and data.get('session_id') in selected_ids:
                    if (data.get('provider_id'), data.get('model_id')) != ('custom-openai', 'qwen3.8-max'):
                        raise ValueError('session tree contains an unexpected provider/model')
            exports = audit.get('exported_sessions', [])
            usage_sessions = [s['session_id'] for s in usage['sessions']]
            if (audit.get('session_exports_complete') is not True
                    or sorted(exports) != sorted(usage_sessions)
                    or audit.get('exported_root_session_id') != usage['root_session_id']):
                raise ValueError('recursive session evidence missing or disagrees with usage')
            for session_id in exports:
                evidence = load(trial / 'qa-session-exports' / (session_id + '.json'))
                if evidence.get('info', {}).get('id') != session_id or not isinstance(evidence.get('messages'), list):
                    raise ValueError('session export structure/identity invalid')
                counted = next(s for s in usage['sessions'] if s['session_id'] == session_id)
                if evidence['info'].get('parentID') != counted.get('parent_session_id'):
                    raise ValueError('session export parent identity differs from usage')
                exported_messages = {m.get('info', {}).get('id') for m in evidence['messages']
                                     if m.get('info', {}).get('role') == 'assistant'}
                exported_tools = {p.get('id') for m in evidence['messages'] for p in m.get('parts', [])
                                  if p.get('type') == 'tool'}
                if (set(counted['message_ids']) != exported_messages
                        or set(counted['tool_ids']) != exported_tools):
                    raise ValueError('session export omitted or added counted messages/tools')
            report['final'] = final_answer(trial / 'agent/opencode.txt',
                load(trial / 'agent/trajectory.json'), usage,
                load(trial / 'agent/opencode-session-snapshot.json'),
                load(trial / 'qa-session-exports' / (usage['root_session_id'] + '.json')))
            before_runtime = audit['before']['observed']['runtime']
            runtime = audit['after']['observed']['runtime']
            if (runtime.get('opencode', {}).get('version') != '1.18.4'
                    or runtime.get('opencode') != before_runtime.get('opencode')
                    or not runtime.get('resolved_config_sha256')):
                raise ValueError('OpenCode runtime/config attestation invalid')
            if profile == 'zvec-grep':
                if not runtime.get('zg') or runtime['zg'] != before_runtime.get('zg'):
                    raise ValueError('zg runtime attestation invalid')
                expected_binary = plan.get('candidate', {}).get('binary_sha256')
                if expected_binary and runtime['zg']['binary_sha256'] != expected_binary:
                    raise ValueError('zg binary differs from frozen candidate')
            report['runtime'] = runtime
            report['metrics']['integrity_audit_wall_seconds'] = audit['audit_wall_seconds']
            report['metrics']['session_export_wall_seconds'] = audit['export_wall_seconds']
            report['metrics']['agent_wall_without_integrity_audit_seconds'] = (
                report['metrics']['agent_wall_seconds'] - audit['audit_wall_seconds'] - audit['export_wall_seconds'])
            if report['metrics']['agent_wall_without_integrity_audit_seconds'] <= 0:
                raise ValueError('invalid agent wall after audit overhead')
            setup = trial / 'agent/zvec-grep-setup.json'
            if setup.exists():
                report['zg_setup'] = load(setup)
            report['structural_valid'] = True
        except Exception as exc:
            report['errors'].append(type(exc).__name__ + ': ' + str(exc))
        reports.append(report)
    if all(r['structural_valid'] for r in reports):
        if reports[0]['runtime']['opencode']['binary_sha256'] != reports[1]['runtime']['opencode']['binary_sha256']:
            errors.append('OpenCode_binary_differs_between_profiles')
        if reports[0]['final']['root_session_id'] == reports[1]['final']['root_session_id']:
            errors.append('profiles_reused_root_session_identity')
    if all(r['structural_valid'] for r in reports) and not errors:
        try:
            collect_pair(runs_dir=run_dir / 'runs', task='zvec-q001',
                         expected_trials=1, output=output / 'native-pair.json')
        except Exception as exc:
            errors.append('native_pair_collection:' + type(exc).__name__ + ': ' + str(exc))
    # No copying arbitrary HOME/runtime trees: the only inventory is this run's output.
    raw_files = file_inventory(run_dir)
    summary = {'schema_version': 1, 'protocol_id': plan['protocol_id'],
               'plan_sha256': digest(plan_path),
               'structural_valid': not errors and all(r['structural_valid'] for r in reports),
               'protocol_valid': None, 'protocol_status': 'pending_tool_audit',
               'errors': errors, 'profiles': reports, 'raw_files': raw_files,
               'quality_evaluated': False, 'source_only_tool_audit_required': True,
               'usage_scope': 'opencode-session-tree-v1'}
    write(output / 'collection.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--main-root', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('validate')
    p = sub.add_parser('prepare')
    p.add_argument('--zvec-repo', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('run')
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--candidate-tgz', type=Path, required=True)
    p.add_argument('--dry-run', action='store_true')
    p = sub.add_parser('collect')
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    plan = validate_plan(args.plan, args.repo_root, args.main_root)
    if args.command == 'prepare':
        prepare(plan, args.plan, args.repo_root, args.main_root, args.zvec_repo, args.output)
    elif args.command == 'run':
        result = run_pair(plan, args.plan, args.main_root, args.prepared, args.output,
                          args.candidate_tgz, args.dry_run)
        return int(any(s['status'] in ('failed', 'timeout') or s.get('returncode', 0) != 0 for s in result['slots']))
    elif args.command == 'collect':
        result = collect(plan, args.plan, args.main_root, args.run_dir, args.output)
        return int(not result['structural_valid'])
    else:
        print(json.dumps({'valid': True, 'plan_sha256': digest(args.plan)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
