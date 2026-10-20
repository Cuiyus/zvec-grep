"""One q005 development pair on independent profile jobs, using native bodies."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
from pathlib import Path, PurePosixPath

import live
import runtime_preparation_v1 as preparation
import runtime_identity_v1 as identity
import profile_assembly_v1 as assembly
import profile_artifacts_v1 as artifacts

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PREFIX = 'benchmarks/native-claude-qa-pilot/'
PROTOCOL = 'native-claude-q005-development-v1'
BASE_PLAN = PREFIX + 'live-plan-v2.json'
BASE_PLAN_SHA = 'b4e8fb15b6cae82a9083add53ad7c04e67673720f7a50825fde587b7538ad026'
LIVE_SHA = '6f625a7ee0c090063f295c780f2eefb275f5fa1a29d8137fcf4f076a6ee1906b'
WORKFLOW = '.github/workflows/native-claude-q005-v1.yml'
SETUP = '.github/actions/native-claude-q005-setup/action.yml'
DECISION = PREFIX + 'runtime-execution-decision-v1.json'
CONTRACT = PREFIX + 'runtime-grader-contract-v1.json'
PROFILES = ('baseline', 'zvec-grep')
CONTROLS = {WORKFLOW, SETUP, DECISION, CONTRACT} | {PREFIX + p for p in (
    'runtime_live_v1.py', 'runtime_preparation_v1.py', 'runtime_identity_v1.py',
    'profile_assembly_v1.py', 'profile_artifacts_v1.py', 'test_runtime_live_v1.py',
    'test_runtime_preparation_v1.py', 'test_profile_assembly_v1.py',
    'test_profile_artifacts_v1.py', 'repeat_secret_scan_v1.py')}
FIXED = {'schema_version': 1, 'protocol_id': PROTOCOL, 'frozen': True,
         'task_id': 'q005', 'profiles': list(PROFILES), 'mode': 'independent_profile_jobs',
         'scheduled_solver_trials': 2, 'n_attempts_per_profile': 1, 'max_retries': 0,
         'max_parallel_profiles': 2, 'execution_workflow': WORKFLOW,
         'execution_decision': DECISION, 'grade_contract': CONTRACT}
EXTRA_KEYS = {'frozen_at_utc', 'base_plan', 'dataset', 'prepared_group', 'grading',
              'frozen_harness_sha256', 'prerequisites', 'evaluation', 'selection_disclosure'}


def prepared_reference(group):
    return {'group_sha256': live.pilot.digest(group / 'group.json'),
            'metadata_sha256': {'q005': live.pilot.digest(group / 'q005/prepared.json')}}


def validate_plan(args, repo_root=REPO):
    original = live.pilot.safe_file(repo_root, BASE_PLAN)
    if live.pilot.digest(original) != BASE_PLAN_SHA:
        raise ValueError('original_native_plan_changed')
    base, proof = live.validate_plan(original, args.main_root, args.reference_prepared,
                                     args.candidate, repo_root)
    plan = live.pilot.load(args.plan)
    if (not isinstance(plan, dict) or set(plan) != set(FIXED) | EXTRA_KEYS
            or any(type(plan.get(k)) is not type(v) or plan[k] != v for k, v in FIXED.items())):
        raise ValueError('q005_frozen_pair_or_mode_mismatch')
    if plan['base_plan'] != {'path': BASE_PLAN, 'sha256': BASE_PLAN_SHA}:
        raise ValueError('q005_original_runtime_reference_changed')
    if plan['dataset'] != {'path': preparation.DATASET, 'sha256': preparation.PINS[preparation.DATASET]}:
        raise ValueError('q005_original_dataset_changed')
    preparation.validate(args.prepared_group, args.reference_prepared, repo_root)
    if plan['prepared_group'] != prepared_reference(args.prepared_group):
        raise ValueError('q005_source_or_task_identity_changed')
    pins = plan['frozen_harness_sha256']
    if not isinstance(pins, dict) or not CONTROLS <= set(pins):
        raise ValueError('q005_control_pins_missing')
    for path, expected in pins.items():
        if live.pilot.digest(live.pilot.safe_file(repo_root, path)) != expected:
            raise ValueError('q005_control_changed:' + path)
    for name, reference in plan['prerequisites'].items():
        if set(reference) != {'path', 'sha256'} or pins.get(reference['path']) != reference['sha256']:
            raise ValueError('q005_prerequisite_not_frozen:' + name)
    if set(plan['prerequisites']) != {'canonical_asset_review', 'native_preparation',
                                     'grading_readiness', 'collector_assembly_fidelity'}:
        raise ValueError('q005_prerequisite_set_changed')
    contract = live.pilot.load(repo_root / CONTRACT)
    metadata = live.pilot.load(args.prepared_group / 'q005/prepared.json')
    if (contract['protocol_id'] != PROTOCOL or contract['task_id'] != 'q005'
            or contract['frozen_before_native_solver_answers'] is not True
            or contract['source_commit'] != metadata['source_commit']
            or contract['source_inventory_sha256'] != metadata['source_inventory_sha256']):
        raise ValueError('q005_grading_source_changed')
    for key in ('question', 'rubric'):
        if contract[key] != {'path': 'benchmarks/zvec-issue-qa/' + metadata[key]['path'],
                              'sha256': metadata[key]['sha256']}:
            raise ValueError('q005_grading_input_changed:' + key)
    grading = plan['grading']
    digest = grading.get('assignment_sha256')
    if (grading.get('planned_answers') != 2 or not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('q005_anonymous_assignment_invalid')
    decision = live.pilot.load(repo_root / DECISION)
    for key in set(FIXED) | {'base_plan', 'dataset', 'prepared_group', 'grading',
                              'prerequisites', 'evaluation', 'selection_disclosure'}:
        if type(decision.get(key)) is not type(plan[key]) or decision[key] != plan[key]:
            raise ValueError('q005_execution_decision_mismatch:' + key)
    if decision.get('paid_execution_authorized') is not True:
        raise ValueError('q005_pair_not_authorized')
    return plan, base, proof


def native_implementation(profiles):
    profiles = tuple(profiles)
    if profiles not in ((PROFILES[0],), (PROFILES[1],), PROFILES):
        raise ValueError('q005_native_profile_set_invalid')
    path = HERE / 'live.py'
    if hashlib.sha256(path.read_bytes()).hexdigest() != LIVE_SHA:
        raise ValueError('original_native_execution_or_collector_changed')
    spec = importlib.util.spec_from_file_location('_q005_native_' + '_'.join(profiles), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROTOCOL, module.PROFILES = PROTOCOL, list(profiles)
    return module


def worker_context(args, plan, profile, *, dry_run):
    return {'schema_version': 1, 'protocol_id': PROTOCOL, 'mode': FIXED['mode'],
            'task_id': 'q005', 'profile': profile, 'plan_sha256': live.pilot.digest(args.plan),
            'prepared_group': plan['prepared_group'], 'candidate_sha256': live.PACKAGE_SHA,
            'recorded_prepared_root': (args.prepared_group / 'q005').resolve().as_posix(),
            'recorded_output_root': args.output.resolve().as_posix(), 'dry_run': dry_run}


def worker(args, plan, base, *, dry_run):
    if args.profile not in PROFILES:
        raise ValueError('exact_one_worker_profile_required')
    if not dry_run:
        prior = assembly.load(args.output.parent / 'q004-confirmation-execution.json')
        if (prior.get('id') != 39380248003 or prior.get('run_attempt') != 1
                or prior.get('head_sha') != '3978d4bbbbb1e37961c277cdf76696b84c69e3bc'
                or prior.get('status') != 'completed'):
            raise ValueError('q004_confirmation_must_be_terminal_before_q005')
    native_args = copy.copy(args)
    native_args.prepared = args.prepared_group / 'q005'
    context = args.output.with_name(args.output.name + '-q005-worker-context.json')
    context.parent.mkdir(parents=True, exist_ok=True)
    assembly.write(context, worker_context(args, plan, args.profile, dry_run=dry_run))
    module = native_implementation([args.profile])
    record = module.run_pair(native_args, base, dry_run=dry_run)
    return 0 if dry_run or (len(record['slots']) == 1 and all(
        s.get('status') == 'exited' and s.get('returncode') == 0 and module.cleanup_complete(s)
        for s in record['slots'])) else 1


def assemble_and_collect(args, plan, base, proof):
    worker_roots = {p: args.workers_root / p / 'raw' for p in PROFILES}
    receipts = {p: artifacts.verify_retained(args.workers_root / p, run_id=args.run_id,
                                            head_sha=args.head_sha, profile=p) for p in PROFILES}
    contexts = {p: assembly.load(root / 'live-q005-worker-context.json')
                for p, root in worker_roots.items()}
    for profile in PROFILES:
        receipt, context = receipts[profile], contexts[profile]
        if (receipt['complete'] is not True or receipt['exact_extracted_inventory'] is not True
                or receipt['run_id'] != args.run_id or receipt['head_sha'] != args.head_sha
                or receipt['run_attempt'] != 1 or receipt['profile'] != profile):
            raise ValueError('q005_worker_origin_mismatch')
        expected = worker_context(args, plan, profile, dry_run=False)
        for name in ('recorded_prepared_root', 'recorded_output_root'):
            value = context.get(name, '')
            parsed = PurePosixPath(value)
            if not parsed.is_absolute() or '..' in parsed.parts or str(parsed) != value:
                raise ValueError('q005_worker_recorded_path_invalid')
            expected[name] = value
        if context != expected:
            raise ValueError('q005_worker_context_mismatch')
    if len({r['job_id'] for r in receipts.values()}) != 2 or len({r['runner_id'] for r in receipts.values()}) != 2:
        raise ValueError('q005_independent_worker_jobs_not_proven')
    if any(contexts['baseline'][k] != contexts['zvec-grep'][k]
           for k in ('recorded_prepared_root', 'recorded_output_root')):
        raise ValueError('q005_worker_paths_differ_no_rewriting_allowed')
    header = {'schema_version': 1, 'protocol_id': PROTOCOL,
              'plan_sha256': live.pilot.digest(args.plan), 'candidate_sha256': live.PACKAGE_SHA,
              'offline_proof_sha256': base['offline_proof']['proof_sha256'],
              'offline_inputs_sha256': base['offline_proof']['inputs_sha256'],
              'dry_run': False, 'quality_evaluated': False}
    report = assembly.assemble({p: root / 'live' for p, root in worker_roots.items()},
                               args.output, expected_header=header)
    native_args = copy.copy(args)
    native_args.prepared = args.prepared_group / 'q005'
    collected = native_implementation(PROFILES).collect(native_args, base, proof)
    verified = identity.verify(args.prepared_group, 'q005', args.output,
        reference_prepared=args.reference_prepared,
        recorded_prepared_root=contexts['baseline']['recorded_prepared_root'])
    verified.update(native_structural_valid=collected['structural_valid'],
                    assembly_receipt_sha256=assembly.digest(args.output / 'profile-assembly.json'),
                    worker_receipt_sha256={p: assembly.digest(args.workers_root / p /
                        'artifact-byte-verification.json') for p in PROFILES},
                    shared_host_claimed=False, aggregate_pair_time_invented=False)
    verified['joint_gate_valid'] = verified['task_identity_valid'] and verified['native_structural_valid']
    assembly.write(args.output / 'task-identity.json', verified)
    return 0 if verified['joint_gate_valid'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('plan', 'main-root', 'reference-prepared', 'prepared-group', 'candidate', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--profile', choices=PROFILES)
    parser.add_argument('--workers-root', type=Path)
    parser.add_argument('--run-id', type=int)
    parser.add_argument('--head-sha')
    parser.add_argument('command', choices=('preview', 'worker', 'assemble'))
    args = parser.parse_args(argv)
    for key in ('plan', 'main_root', 'reference_prepared', 'prepared_group', 'candidate', 'output'):
        setattr(args, key, getattr(args, key).resolve())
    plan, base, proof = validate_plan(args)
    if args.command in ('preview', 'worker'):
        return worker(args, plan, base, dry_run=args.command == 'preview')
    if args.workers_root is None or args.run_id is None or not args.head_sha:
        raise ValueError('q005_assembly_origin_arguments_required')
    args.workers_root = args.workers_root.resolve()
    return assemble_and_collect(args, plan, base, proof)


if __name__ == '__main__':
    raise SystemExit(main())
