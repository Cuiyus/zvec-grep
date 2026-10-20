"""Prospective two-task driver; frozen native execution bodies remain unchanged.

No real expansion plan or approval is supplied by this module. Each invocation
requires separately frozen references and runs at most the selected task pair.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path, PurePosixPath

import live
import expansion_v1 as preparation
import expansion_identity_v1 as identity

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PREFIX = 'benchmarks/native-claude-qa-pilot/'
PROTOCOL = 'native-claude-reserves-expansion-v1'
BASE_PLAN = PREFIX + 'live-plan-v2.json'
BASE_PLAN_SHA256 = 'b4e8fb15b6cae82a9083add53ad7c04e67673720f7a50825fde587b7538ad026'
WORKFLOW = '.github/workflows/native-claude-qa-expansion-v1.yml'
DECISION = PREFIX + 'expansion-decision-v1.json'
GRADE_CONTRACT = PREFIX + 'expansion-grader-contract-v1.json'
TASKS = [{'id': 'q002', 'profiles': ['baseline', 'zvec-grep']},
         {'id': 'q003', 'profiles': ['zvec-grep', 'baseline']}]
CONTROLS = {PREFIX + name for name in (
    'expansion_live_v1.py', 'test_expansion_live_v1.py', 'expansion_v1.py', 'test_expansion_v1.py',
    'expansion_identity_v1.py', 'test_expansion_identity_v1.py')} | {WORKFLOW, DECISION, GRADE_CONTRACT}
KEYS = {'schema_version', 'protocol_id', 'frozen', 'base_plan', 'dataset', 'prepared_group',
        'tasks', 'n_attempts', 'max_retries', 'execution_workflow', 'execution_decision',
        'grade_contract', 'frozen_harness_sha256'}


def task_profiles(task_id):
    for task in TASKS:
        if task['id'] == task_id:
            return list(task['profiles'])
    raise ValueError('undeclared_expansion_task')


def prepared_reference(group):
    return {'group_sha256': live.pilot.digest(group / 'group.json'),
            'metadata_sha256': {task['id']: live.pilot.digest(group / task['id'] / 'prepared.json') for task in TASKS}}


def validate_prerequisites(decision, repo_root):
    prerequisites = decision.get('execution_prerequisites')
    if (not isinstance(prerequisites, dict)
            or type(prerequisites.get('current_q001_confirmation_run_id')) is not int
            or prerequisites['current_q001_confirmation_run_id'] != 39343873279):
        raise ValueError('expansion_confirmation_prerequisite_missing')
    conclusion = prerequisites.get('confirmation_conclusion')
    if (not isinstance(conclusion, dict) or set(conclusion) != {'path', 'sha256'}
            or not isinstance(conclusion['path'], str) or not conclusion['path']):
        raise ValueError('expansion_confirmation_conclusion_reference_missing')
    if live.pilot.digest(live.pilot.safe_file(repo_root, conclusion['path'])) != conclusion['sha256']:
        raise ValueError('expansion_confirmation_conclusion_changed')
    upstream = prerequisites.get('upstream_verification')
    if (not isinstance(upstream, dict) or upstream.get('repo') != 'zvec-ai/zvec-grep'
            or upstream.get('ref') != 'main' or upstream.get('sha') != live.pilot.MAIN
            or not isinstance(upstream.get('checked_at_utc'), str) or not upstream['checked_at_utc'].strip()):
        raise ValueError('expansion_upstream_verification_missing_or_mismatched')
    host_decision = prerequisites.get('host_execution_decision')
    if not isinstance(host_decision, str) or not host_decision.strip():
        raise ValueError('expansion_host_execution_decision_missing')


def validate_plan(plan_path, task_id, main_root, reference_prepared, prepared_group, candidate, repo_root=REPO):
    base_path = live.pilot.safe_file(repo_root, BASE_PLAN)
    if live.pilot.digest(base_path) != BASE_PLAN_SHA256:
        raise ValueError('frozen_base_plan_changed')
    # Original q001 inputs and canonical order validate R3/runtime first.
    base, proof = live.validate_plan(base_path, main_root, reference_prepared, candidate, repo_root)
    task_profiles(task_id)
    plan = live.pilot.load(plan_path)
    if not isinstance(plan, dict) or not KEYS <= plan.keys() or set(plan) - KEYS - {'frozen_at_utc'}:
        raise ValueError('expansion_reference_schema_only_no_overrides')
    if 'frozen_at_utc' in plan and (not isinstance(plan['frozen_at_utc'], str) or not plan['frozen_at_utc']):
        raise ValueError('invalid_freeze_provenance')
    if (type(plan['schema_version']) is not int or plan['schema_version'] != 1
            or plan['protocol_id'] != PROTOCOL or plan['frozen'] is not True or plan['tasks'] != TASKS
            or type(plan['n_attempts']) is not int or plan['n_attempts'] != 1
            or type(plan['max_retries']) is not int or plan['max_retries'] != 0):
        raise ValueError('expansion_schedule_or_protocol_mismatch')
    dataset = {'path': preparation.DATASET, 'sha256': preparation.PINS[preparation.DATASET]}
    if plan['base_plan'] != {'path': BASE_PLAN, 'sha256': BASE_PLAN_SHA256} or plan['dataset'] != dataset:
        raise ValueError('expansion_reference_mismatch')
    preparation.validate(prepared_group, reference_prepared, repo_root)
    if plan['prepared_group'] != prepared_reference(prepared_group):
        raise ValueError('expansion_prepared_reference_mismatch')
    if (plan['execution_workflow'] != WORKFLOW or plan['execution_decision'] != DECISION
            or plan['grade_contract'] != GRADE_CONTRACT):
        raise ValueError('expansion_control_path_mismatch')
    pins = plan['frozen_harness_sha256']
    if not isinstance(pins, dict) or not CONTROLS <= pins.keys():
        raise ValueError('expansion_control_pins_missing')
    for relative, expected in pins.items():
        if live.pilot.digest(live.pilot.safe_file(repo_root, relative)) != expected:
            raise ValueError('expansion_control_changed:' + relative)
    decision = live.pilot.load(live.pilot.safe_file(repo_root, DECISION))
    if (decision.get('protocol_id') != PROTOCOL or decision.get('status') != 'approved_for_two_fresh_task_pairs'
            or decision.get('frozen') is not True or decision.get('paid_execution_authorized') is not True
            or decision.get('base_plan_sha256') != BASE_PLAN_SHA256
            or any(decision.get(k) != plan[k] for k in ('dataset', 'prepared_group', 'tasks'))
            or type(decision.get('n_attempts')) is not int or decision['n_attempts'] != 1
            or type(decision.get('max_retries')) is not int or decision['max_retries'] != 0):
        raise ValueError('expansion_decision_mismatch')
    validate_prerequisites(decision, repo_root)
    contract = live.pilot.load(live.pilot.safe_file(repo_root, GRADE_CONTRACT))
    if contract.get('frozen') is not True or contract.get('dataset') != dataset or contract.get('task_ids') != list(preparation.TASK_IDS):
        raise ValueError('expansion_grade_contract_not_frozen_or_mismatched')
    for task_id in preparation.TASK_IDS:
        metadata = live.pilot.load(prepared_group / task_id / 'prepared.json')
        expected = {key: {'path': 'benchmarks/zvec-issue-qa/' + metadata[key]['path'],
                          'sha256': metadata[key]['sha256']} for key in ('question', 'rubric')}
        if contract.get('inputs', {}).get(task_id) != expected:
            raise ValueError('expansion_grade_assets_mismatch:' + task_id)
    return plan, base, proof


def private_live(task_id):
    profiles = task_profiles(task_id)
    spec = importlib.util.spec_from_file_location('_native_claude_expansion_' + task_id, HERE / 'live.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROTOCOL, module.PROFILES = PROTOCOL, profiles
    return module


def context_path(output):
    return output.with_name(output.name + '-expansion-context.json')


def context_record(args, plan, base, *, dry_run, recorded_prepared_root):
    root = PurePosixPath(recorded_prepared_root)
    if not root.is_absolute() or '..' in root.parts or str(root) != recorded_prepared_root:
        raise ValueError('invalid_recorded_prepared_root')
    return {'schema_version': 1, 'protocol_id': PROTOCOL, 'task_id': args.task_id,
            'profiles': task_profiles(args.task_id), 'plan_sha256': live.pilot.digest(args.plan),
            'base_plan_sha256': plan['base_plan']['sha256'], 'dataset_sha256': plan['dataset']['sha256'],
            'prepared_group_sha256': plan['prepared_group']['group_sha256'],
            'prepared_metadata_sha256': plan['prepared_group']['metadata_sha256'][args.task_id],
            'offline_proof_sha256': base['offline_proof']['proof_sha256'],
            'candidate_sha256': base['candidate_sha256'], 'recorded_prepared_root': recorded_prepared_root,
            'dry_run': dry_run}


def execute(args, plan, base, proof, repo_root=REPO):
    module = private_live(args.task_id)
    native_args = copy.copy(args)
    native_args.prepared = args.prepared_group / args.task_id
    sidecar = context_path(args.output)
    if args.command == 'collect':
        if sidecar.is_symlink() or not sidecar.is_file():
            raise ValueError('expansion_context_missing_or_mismatched')
        saved = live.pilot.load(sidecar)
        if not isinstance(saved, dict):
            raise ValueError('expansion_context_missing_or_mismatched')
        context = context_record(args, plan, base, dry_run=False,
                                 recorded_prepared_root=saved.get('recorded_prepared_root', ''))
        if saved != context:
            raise ValueError('expansion_context_missing_or_mismatched')
        destination = args.output / 'task-identity.json'
        if destination.exists() or destination.is_symlink():
            raise FileExistsError('expansion_identity_output_already_exists')
        # A failed run can have written its exclusive context before any slot.
        args.output.mkdir(parents=True, exist_ok=True)
        collected, collection_error = None, None
        try:
            collected = module.collect(native_args, base, proof)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            collection_error = type(exc).__name__
        result = identity.verify(args.prepared_group, args.task_id, args.output,
            reference_prepared=args.reference_prepared, recorded_prepared_root=context['recorded_prepared_root'],
            expected_profiles=task_profiles(args.task_id), repo_root=repo_root)
        result.update(native_collection_completed=collected is not None, native_collection_error_type=collection_error,
                      native_structural_valid=bool(collected and collected.get('structural_valid') is True),
                      context_sha256=live.pilot.digest(sidecar))
        result['joint_gate_valid'] = result['native_structural_valid'] and result['task_identity_valid']
        with destination.open('x') as handle:
            json.dump(result, handle, indent=2, sort_keys=True); handle.write('\n')
        return 0 if result['joint_gate_valid'] else 1
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError('expansion_output_already_exists')
    context = context_record(args, plan, base, dry_run=args.command == 'dry-run',
                             recorded_prepared_root=native_args.prepared.resolve().as_posix())
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open('x') as handle:
        json.dump(context, handle, indent=2, sort_keys=True); handle.write('\n')
    record = module.run_pair(native_args, base, dry_run=args.command == 'dry-run')
    return 0 if args.command == 'dry-run' or all(
        slot.get('status') == 'exited' and slot.get('returncode') == 0 and module.cleanup_complete(slot)
        for slot in record['slots']) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'main-root', 'reference-prepared', 'prepared-group', 'candidate', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--task', dest='task_id', choices=preparation.TASK_IDS, required=True)
    parser.add_argument('command', choices=('dry-run', 'run', 'collect'))
    args = parser.parse_args(argv)
    for name in ('plan', 'main_root', 'reference_prepared', 'prepared_group', 'candidate', 'output'):
        setattr(args, name, getattr(args, name).resolve())
    plan, base, proof = validate_plan(args.plan, args.task_id, args.main_root,
        args.reference_prepared, args.prepared_group, args.candidate)
    return execute(args, plan, base, proof)


if __name__ == '__main__':
    raise SystemExit(main())
