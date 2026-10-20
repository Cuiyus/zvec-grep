"""Fixed-five QA on the pinned main OpenCode/GLM/local-embedding stack.

Only source/session evidence is instrumented. The installed agent, native loop,
request configuration, setup, usage accounting and answer conversion come from
the pinned upstream benchmark. Original and score-trimmed views stay separate.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OLD = REPO / 'benchmarks/opencode-qa-pilot'
CLAUDE = REPO / 'benchmarks/native-claude-qa-pilot'
sys.path[:0] = [str(OLD), str(CLAUDE)]
import pilot
import five_inputs_v1 as five

MAIN = 'ae1ac57d489542508e016bba5d868bc38c3c095a'
MODEL = 'custom-openai/glm-5.2'
EMBEDDING = 'local/potion-code-16m-v2'
PROTOCOL = 'zvec-five-main-opencode-glm52-local-v1'
PACKAGE_SHA = '03af5790a2e7a1e1eb4e9c857e64218b72c8a9c3ebbed046a177e5600de460cb'
BINARY_SHA = '209e7d99d8f76ccdc8d3e91dbdb8363fba985cc36ce219df98abe2db00500463'
PROFILES = ('baseline', 'zvec-grep')


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path): return json.loads(Path(path).read_text())
def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    temp.replace(path)


def schedule():
    rng = random.Random(20261020); rows = []
    for index in range(1, 11):
        tasks = list(five.TASK_IDS); rng.shuffle(tasks)
        for task in tasks:
            position = five.TASK_IDS.index(task)
            rows.append({'key': f'{task}-r{index:02d}', 'task_id': task,
                'trial_index': index, 'profiles': list(PROFILES if (position + index) % 2 else reversed(PROFILES))})
    return rows


def native(main):
    main = Path(main).resolve(); sys.path.insert(0, str(main))
    module = importlib.import_module('zg_bench.runner')
    if main not in Path(module.__file__).resolve().parents:
        raise ValueError('unpinned_main_module')
    return module


def prepare(args):
    # Reuse only the established source/question exporter, not its old runtime
    # validator, candidate, model, execution commands or provider configuration.
    original = REPO / 'benchmarks/opencode-qa-pilot/experiment.json'
    pilot.prepare(load(original), original, REPO, args.main_root,
        args.zvec_repo, args.prepared / 'q001')
    five.prepare(args.prepared / 'q001', args.zvec_repo, args.prepared / 'five', REPO)
    refs = five.references(args.prepared / 'five', args.prepared / 'q001', REPO)
    expected = load(CLAUDE / 'five-repeat-plan-v1.json')['prepared']
    if refs != expected: raise ValueError('existing_five_question_or_source_identity_changed')
    write(args.prepared / 'accepted-five-references.json', refs)
    return refs


def freeze(args):
    if args.plan.exists(): raise FileExistsError('plan_already_frozen')
    original = load(CLAUDE / 'five-repeat-plan-v1.json')
    main_files = [p for p in sorted(args.main_root.rglob('*'))
        if p.is_file() and not any(x in p.parts for x in ('.venv', '.cache', '__pycache__'))]
    controls = [p for p in sorted(HERE.rglob('*')) if p.is_file() and '__pycache__' not in p.parts
        and p != args.plan]
    controls += [REPO / '.github/workflows/zvec-qa-glm-local-v1.yml',
        REPO / '.github/actions/zvec-qa-glm-local-setup/action.yml',
        OLD / 'pilot.py', OLD / 'qa_agents.py', OLD / 'repair_v2/qa_agents.py', OLD / 'repair_v2/evidence.py',
        OLD / 'repair_v2/fake_services.py', OLD / 'repair_v2/integration.py',
        CLAUDE / 'five_inputs_v1.py', CLAUDE / 'expansion_v1.py',
        CLAUDE / 'repeat_inputs_v1.py', CLAUDE / 'architecture_preparation_v1.py',
        CLAUDE / 'runtime_preparation_v1.py',
        CLAUDE / 'robust_statistics_v1.py']
    for task in five.TASK_IDS:
        controls += [REPO / f'benchmarks/zvec-issue-qa/tasks/{task}/instruction.md',
            REPO / f'benchmarks/zvec-issue-qa/evaluator/{task}.json']
    plan = {'schema_version': 1, 'protocol_id': PROTOCOL, 'frozen': True,
        'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'harness_main': MAIN, 'rust_main': MAIN, 'model': MODEL, 'embedding_model': EMBEDDING,
        'agent_version': '1.18.4', 'harbor_version': '0.18.0',
        'request_parameters': {'temperature': 0, 'seed': 42, 'enable_thinking': True,
            'reasoning_effort': 'high', 'max_tokens': 32000, 'response_format': None},
        'task_ids': list(five.TASK_IDS), 'repetitions_per_task_profile': 10,
        'scheduled_trials': 100, 'schedule': schedule(), 'max_parallel_pairs': 20,
        'n_attempts': 1, 'max_retries': 0, 'slot_timeout_sec': 3600,
        'prepared': original['prepared'], 'candidate_install_sha256': PACKAGE_SHA,
        'candidate_binary_sha256': BINARY_SHA,
        'main_files': {str(p.relative_to(args.main_root)): sha(p) for p in main_files},
        'controls': {str(p.relative_to(REPO)): sha(p) for p in controls},
        'quality': {'method': 'pinned-main GLM-5.2 SWE-QA self-judge',
            'dimensions': ['correctness', 'completeness', 'relevance', 'clarity', 'coherence'],
            'each_dimension': 'integer 1 through 20', 'maximum_total': 100,
            'ten_point_display': 'total / 10; not the previous original-rubric factual score',
            'references': 'existing q001-q004 reference_answer; q005 all ten frozen rubric units',
            'judge_attempts': 3, 'judge_calls_separate_from_agent_trials': True},
        'statistics': {'primary': 'all ten scheduled rows on all five questions; never available-only means',
            'secondary': 'PR261-compatible one-low/one-high quality trim per profile; identical retained tuples for resources; fixed repetition-index ties',
            'task_screen': 'single-pass maximum residual dispersion MAD; diagnostic and separately labelled screened macro; minimum five tasks, zero-MAD/missing guards',
            'bootstrap': {'whole_pair_draws_within_fixed_task': True, 'reselect_each_draw': True,
                'resamples': 10000, 'seed': 20261020}},
        'isolation': 'fresh profile container, HOME/session tree and native setup cache; no restored index; original task and native main tools/config only',
        'limits': ['Native request settings are not verified immutable model weights or provider billing.',
            'Same-model self-judging is not independent human grading.',
            'Retained directory/scope and execution/usage defects cannot be repaired by filtering.'],
        'history_pooled': False, 'new_questions': False, 'ci_repository': 'Cuiyus/zvec-grep'}
    write(args.plan, plan); return plan


def validate(args):
    plan = load(args.plan)
    if (plan.get('protocol_id') != PROTOCOL or plan.get('frozen') is not True
        or plan['schedule'] != schedule() or plan['model'] != MODEL
        or plan['embedding_model'] != EMBEDDING or plan['harness_main'] != MAIN
        or plan['n_attempts'] != 1 or plan['max_retries'] != 0):
        raise ValueError('frozen_execution_configuration_changed')
    for rel, digest in plan['controls'].items():
        if sha(REPO / rel) != digest: raise ValueError('control_changed:' + rel)
    for rel, digest in plan['main_files'].items():
        if sha(args.main_root / rel) != digest: raise ValueError('main_changed:' + rel)
    refs = five.references(args.prepared / 'five', args.prepared / 'q001', REPO)
    if refs != plan['prepared']: raise ValueError('accepted_five_changed')
    if sha(args.candidate) != plan['candidate_install_sha256']:
        raise ValueError('candidate_changed')
    return plan


def prepared_root(args, task):
    return five.task_root(args.prepared / 'five', args.prepared / 'q001', task)


def command(args, plan, pair, profile, output, *, real=False):
    runner = native(args.main_root); root = prepared_root(args, pair['task_id'])
    suite = runner.load_suite(root / 'suite.yaml')
    package = str(args.candidate); digest = plan['candidate_install_sha256']
    if real:
        cache = runner.prepare_setup_cache('opencode', profile,
            zvec_grep_package=package, embedding_model=EMBEDDING)
        if profile == 'zvec-grep':
            package, digest = cache.zvec_grep_package, cache.zvec_grep_package_sha256
            if digest != plan['candidate_install_sha256']: raise ValueError('prepared_package_changed')
    cmd = runner.build_harbor_command(suite, profile=profile, agent='opencode', model=MODEL,
        jobs_dir=output / 'runs', job_name=pair['task_id'] + '-' + profile,
        n_attempts=1, max_retries=0, harbor_executable=str(Path(sys.executable).parent / 'harbor'),
        zvec_grep_package=package, zvec_grep_package_sha256=digest,
        embedding_model=EMBEDDING, embedding_endpoint=None)
    cmd[cmd.index('--agent') + 1] = 'repair_v2.qa_agents:' + ('QABaseline' if profile == 'baseline' else 'QAZvec')
    cmd += ['--agent-kwarg', 'qa_source_manifest=' + str(root / 'source-manifest.json'),
        '--agent-kwarg', 'qa_profile=' + profile]
    return cmd


def capture(output, pair, profile, known):
    job = output / 'runs' / (pair['task_id'] + '-' + profile)
    for config in job.glob('*/config.json'):
        trial = config.parent
        import re
        project = re.sub(r'[^a-z0-9_-]', '-', (trial.name + '__env').lower())
        if not re.match(r'^[a-z0-9]', project): project = '0' + project
        ids = subprocess.check_output(['docker', 'ps', '-aq', '--filter',
            'label=com.docker.compose.project=' + project], text=True, timeout=20).split()
        for identity in ids:
            if identity in known: continue
            inspected = json.loads(subprocess.check_output(['docker', 'inspect', identity], text=True, timeout=20))[0]
            known[identity] = {'id': identity, 'project': project,
                'mounts': inspected.get('Mounts', []), 'state': inspected.get('State', {}),
                'labels': inspected.get('Config', {}).get('Labels', {})}


def run_pair(args, plan, pair, *, dry=False, services=None):
    if args.output.exists(): raise FileExistsError('never_restart_or_overwrite_a_pair')
    args.output.mkdir(parents=True)
    record = {'schema_version': 1, 'protocol_id': PROTOCOL, 'plan_sha256': sha(args.plan),
        'pair': pair, 'dry_run': dry, 'slots': []}
    write(args.output / 'execution.json', record)
    runner = native(args.main_root)
    for profile in pair['profiles']:
        if record['slots'] and not dry and record['slots'][-1].get('container_cleanup', {}).get('complete') is not True:
            record['slots'].append({'profile': profile, 'status': 'not_started', 'reason': 'cleanup_unconfirmed'})
            write(args.output / 'execution.json', record); break
        slot = {'profile': profile, 'status': 'planned', 'started_at_unix': time.time()}
        record['slots'].append(slot); containers = {}; proc = None
        previous = signal.signal(signal.SIGALRM, pilot.deadline_expired)
        try:
            if services is not None: services.profile = profile
            if not dry:
                volume = runner.setup_cache_volume_name('opencode', profile,
                    zvec_grep_package='/tmp/zg-bench-zvec-grep.tgz',
                    zvec_grep_package_sha256=PACKAGE_SHA if profile == 'zvec-grep' else None,
                    embedding_model=EMBEDDING)
                found = subprocess.run(['docker', 'volume', 'inspect', volume], capture_output=True, timeout=20)
                if found.returncode == 0: raise ValueError('fresh_native_install_cache_required')
            cmd = command(args, plan, pair, profile, args.output, real=not dry)
            slot['command'] = cmd; write(args.output / (profile + '-command.json'), cmd)
            if dry: slot['status'] = 'dry_run'; continue
            environment = runner.execution_environment(agent='opencode', model=MODEL)
            environment['PYTHONPATH'] = os.pathsep.join([str(OLD), str(CLAUDE), str(HERE), str(args.main_root)])
            environment.pop('ZG_BENCH_INDEX_SEED_DIR', None)
            for key in ('ZVEC_GREP_API_KEY', 'QWEN_EMBEDDING_API_KEY', 'DASHSCOPE_API_KEY', 'QWEN_API_KEY', 'ZVEC_GREP_ENDPOINT'):
                environment.pop(key, None)
            slot.update(status='running', native_embedding='local', restored_index=False)
            write(args.output / 'execution.json', record)
            signal.setitimer(signal.ITIMER_REAL, plan['slot_timeout_sec'])
            with (args.output / (profile + '-job.log')).open('wb', buffering=0) as log:
                proc = subprocess.Popen(cmd, cwd=args.main_root, env=environment,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                while proc.poll() is None:
                    capture(args.output, pair, profile, containers)
                    try: proc.wait(timeout=1)
                    except subprocess.TimeoutExpired: pass
            slot.update(status='exited', returncode=proc.returncode)
        except TimeoutError: slot.update(status='timeout', returncode=124)
        except Exception as exc: slot.update(status='failed', error_type=type(exc).__name__)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0); signal.signal(signal.SIGALRM, previous)
            if proc is not None and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: os.killpg(proc.pid, signal.SIGKILL); proc.wait(timeout=5)
            if not dry:
                try:
                    capture(args.output, pair, profile, containers)
                    slot['container_cleanup'] = pilot.cleanup_slot_containers(args.output, profile, containers)
                except Exception as exc: slot['container_cleanup'] = {'complete': False, 'error_type': type(exc).__name__}
                write(args.output / (profile + '-containers.json'), containers)
            slot['finished_at_unix'] = time.time(); write(args.output / 'execution.json', record)
    return record


def collect(args, plan, pair):
    native(args.main_root)
    from zg_bench.swe_qa.collect import _profile_result
    from zg_bench.metrics.usage import validate_session_usage
    record = load(args.output / 'execution.json'); rows = []
    for profile in pair['profiles']:
        slot = next((s for s in record['slots'] if s['profile'] == profile), {})
        row = {'task_id': pair['task_id'], 'trial_index': pair['trial_index'], 'profile': profile,
            'execution_valid': False, 'answer': None, 'answer_sha256': None, 'metrics': None, 'errors': []}
        trials = sorted(p.parent for p in (args.output / 'runs' / (pair['task_id'] + '-' + profile)).glob('*/result.json'))
        row['retained_trials'] = [p.name for p in trials]
        try:
            if len(trials) != 1: raise ValueError('exactly_one_native_trial_required')
            trial = trials[0]; result = load(trial / 'result.json')
            row['metrics'] = _profile_result(profile=profile,
                job_dir=trial.parent, trial_dir=trial, result=result, trial_index=pair['trial_index'])
            row['metrics']['outer_slot_seconds'] = slot['finished_at_unix'] - slot['started_at_unix']
            usage = validate_session_usage(load(trial / 'agent/session-usage.json'), require_identity=True)
            snapshot = load(trial / 'agent/opencode-session-snapshot.json')
            selected = {s['session_id'] for s in usage['sessions']}
            for item in snapshot:
                data = item['data']; data = json.loads(data) if isinstance(data, str) else data
                if item['kind'] == 'message' and data.get('session_id') in selected:
                    if (data.get('provider_id'), data.get('model_id')) != ('custom-openai', 'glm-5.2'):
                        raise ValueError('unexpected_provider_or_model')
            audit = load(trial / 'qa-source-audit.json'); row['source_audit'] = audit
            row['metrics']['integrity_audit_wall_seconds'] = audit['audit_wall_seconds']
            row['metrics']['session_export_wall_seconds'] = audit['export_wall_seconds']
            row['metrics']['agent_wall_without_integrity_audit_seconds'] = (
                row['metrics']['agent_wall_seconds'] - audit['audit_wall_seconds'] - audit['export_wall_seconds'])
            pilot.check_mounts(load(args.output / (profile + '-containers.json')), profile,
                {'candidate_sha256': PACKAGE_SHA})
            if not audit.get('valid'): raise ValueError('source_or_session_export_invalid')
            if sorted(audit['exported_sessions']) != sorted(selected): raise ValueError('session_tree_not_fully_exported')
            final = pilot.final_answer(trial / 'agent/opencode.txt', load(trial / 'agent/trajectory.json'),
                usage, snapshot, load(trial / 'qa-session-exports' / (usage['root_session_id'] + '.json')))
            row.update(answer=final['answer'], answer_sha256=final['answer_sha256'], root_session_id=usage['root_session_id'])
            runtime = audit['after']['observed']['runtime']; row['runtime'] = runtime
            if runtime['opencode']['version'] != '1.18.4': raise ValueError('opencode_version_changed')
            if profile == 'zvec-grep' and runtime['zg']['binary_sha256'] != BINARY_SHA:
                raise ValueError('rust_binary_changed')
            if slot.get('status') != 'exited' or slot.get('returncode') != 0 or not slot.get('container_cleanup', {}).get('complete'):
                raise ValueError('native_execution_or_cleanup_failed')
            row['execution_valid'] = True
            setup = trial / 'agent/zvec-grep-setup.json'
            if setup.is_file(): row['zg_setup'] = load(setup)
        except Exception as exc: row['errors'].append(type(exc).__name__ + ':' + str(exc))
        rows.append(row)
    result = {'schema_version': 1, 'protocol_id': PROTOCOL, 'plan_sha256': sha(args.plan),
        'pair': pair, 'profiles': rows, 'all_valid': all(r['execution_valid'] for r in rows),
        'source_scope_semantic_audit': 'retained exported tools; separate from structural source unchanged checks',
        'quality_evaluated': False, 'native_raw_retained': True}
    write(args.output / 'collection.json', result); return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare', 'freeze', 'matrix', 'dry-run', 'run', 'collect'])
    for name in ('main-root', 'prepared', 'plan', 'candidate', 'output', 'zvec-repo'):
        p.add_argument('--' + name, type=Path)
    p.add_argument('--pair'); args = p.parse_args()
    for k, v in vars(args).items():
        if isinstance(v, Path): setattr(args, k, v.resolve())
    if args.command == 'prepare': result = prepare(args)
    elif args.command == 'freeze': result = freeze(args)
    elif args.command == 'matrix': result = {'include': load(args.plan)['schedule']}
    else:
        plan = validate(args); pair = next(r for r in plan['schedule'] if r['key'] == args.pair)
        result = collect(args, plan, pair) if args.command == 'collect' else run_pair(args, plan, pair, dry=args.command == 'dry-run')
    print(json.dumps(result if args.command == 'matrix' else {'command': args.command, 'valid': True, 'solver_calls_in_prepare': 0}))


if __name__ == '__main__': main()
