"""One prospective native q004 pair using unchanged execution/collection bodies."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import live
import architecture_preparation_v1 as preparation
import architecture_identity_v1 as identity

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PREFIX = 'benchmarks/native-claude-qa-pilot/'
PROTOCOL = 'native-claude-q004-development-v1'
BASE_PLAN = PREFIX + 'live-plan-v2.json'
BASE_PLAN_SHA = 'b4e8fb15b6cae82a9083add53ad7c04e67673720f7a50825fde587b7538ad026'
BASE_DRIVER = HERE / 'expansion_live_v1.py'
BASE_DRIVER_SHA = '1be64db3037025c8b2a6ee48d1967114a0f99917a021f0a5518d5304e94c9565'
WORKFLOW = '.github/workflows/native-claude-q004-v1.yml'
SETUP = '.github/actions/native-claude-q004-setup/action.yml'
DECISION = PREFIX + 'architecture-execution-decision-v1.json'
CONTRACT = PREFIX + 'architecture-grader-contract-v1.json'
TASKS = [{'id': 'q004', 'profiles': ['baseline', 'zvec-grep']}]
CONTROLS = {WORKFLOW, SETUP, DECISION, CONTRACT} | {PREFIX + name for name in (
    'architecture_live_v1.py', 'architecture_preparation_v1.py', 'architecture_identity_v1.py',
    'test_architecture_live_v1.py', 'test_architecture_preparation_v1.py',
    'expansion_live_v1.py', 'expansion_v1.py', 'expansion_identity_v1.py', 'repeat_secret_scan_v1.py')}
KEYS = {'schema_version', 'protocol_id', 'frozen', 'frozen_at_utc', 'base_plan', 'dataset',
        'prepared_group', 'tasks', 'n_attempts', 'max_retries', 'execution_workflow',
        'execution_decision', 'grade_contract', 'grading', 'frozen_harness_sha256'}


def prepared_reference(group):
    return {'group_sha256': live.pilot.digest(group / 'group.json'),
            'metadata_sha256': {'q004': live.pilot.digest(group / 'q004/prepared.json')}}


def validate_plan(plan_path, main_root, reference_prepared, group, candidate, repo_root=REPO):
    base_path = live.pilot.safe_file(repo_root, BASE_PLAN)
    if live.pilot.digest(base_path) != BASE_PLAN_SHA:
        raise ValueError('original_native_plan_changed')
    base, proof = live.validate_plan(base_path, main_root, reference_prepared, candidate, repo_root)
    plan = live.pilot.load(plan_path)
    if (not isinstance(plan, dict) or set(plan) != KEYS or type(plan['schema_version']) is not int or plan['schema_version'] != 1
            or plan['protocol_id'] != PROTOCOL or plan['frozen'] is not True
            or not isinstance(plan['frozen_at_utc'], str) or not plan['frozen_at_utc']
            or plan['tasks'] != TASKS or type(plan['n_attempts']) is not int or plan['n_attempts'] != 1
            or type(plan['max_retries']) is not int or plan['max_retries'] != 0):
        raise ValueError('q004_schema_or_schedule_changed')
    if plan['base_plan'] != {'path': BASE_PLAN, 'sha256': BASE_PLAN_SHA}:
        raise ValueError('q004_base_reference_changed')
    dataset = {'path': preparation.DATASET, 'sha256': preparation.PINS[preparation.DATASET]}
    if plan['dataset'] != dataset:
        raise ValueError('q004_dataset_changed')
    preparation.validate(group, reference_prepared, repo_root)
    if plan['prepared_group'] != prepared_reference(group):
        raise ValueError('q004_prepared_identity_changed')
    if (plan['execution_workflow'] != WORKFLOW or plan['execution_decision'] != DECISION
            or plan['grade_contract'] != CONTRACT):
        raise ValueError('q004_control_paths_changed')
    pins = plan['frozen_harness_sha256']
    if not isinstance(pins, dict) or not CONTROLS <= set(pins):
        raise ValueError('q004_control_pins_missing')
    for relative, expected in pins.items():
        if live.pilot.digest(live.pilot.safe_file(repo_root, relative)) != expected:
            raise ValueError('q004_control_changed:' + relative)
    decision = live.pilot.load(live.pilot.safe_file(repo_root, DECISION))
    if (decision.get('protocol_id') != PROTOCOL or decision.get('frozen') is not True
            or decision.get('status') != 'approved_for_one_fresh_development_pair'
            or decision.get('paid_execution_authorized') is not True
            or type(decision.get('n_attempts')) is not int or decision.get('n_attempts') != 1
            or type(decision.get('max_retries')) is not int or decision.get('max_retries') != 0
            or any(decision.get(k) != plan[k] for k in ('base_plan', 'dataset', 'prepared_group', 'tasks', 'grading'))):
        raise ValueError('q004_execution_decision_mismatch')
    prerequisites = decision.get('prerequisites', {})
    for name in ('q001_repetition_conclusion', 'q004_preparation_proof', 'q004_source_review', 'q004_grading_readiness'):
        reference = prerequisites.get(name, {})
        if (not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}
                or live.pilot.digest(live.pilot.safe_file(repo_root, reference['path'])) != reference['sha256']):
            raise ValueError('q004_prerequisite_changed:' + name)
    grading = plan['grading']
    if (not isinstance(grading, dict) or set(grading) != {'assignment_sha256', 'planned_answers'}
            or grading['planned_answers'] != 2 or not isinstance(grading['assignment_sha256'], str)
            or len(grading['assignment_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in grading['assignment_sha256'])):
        raise ValueError('q004_grading_assignment_invalid')
    contract = live.pilot.load(live.pilot.safe_file(repo_root, CONTRACT))
    if (contract.get('protocol_id') != PROTOCOL or contract.get('task_id') != 'q004'
            or contract.get('frozen_before_native_solver_answers') is not True
            or contract.get('dataset') != dataset):
        raise ValueError('q004_grading_contract_changed')
    metadata = live.pilot.load(group / 'q004/prepared.json')
    if (contract.get('source_commit') != metadata['source_commit']
            or contract.get('source_inventory_sha256') != metadata['source_inventory_sha256']):
        raise ValueError('q004_grading_source_changed')
    for key in ('question', 'rubric'):
        expected = {'path': 'benchmarks/zvec-issue-qa/' + metadata[key]['path'],
                    'sha256': metadata[key]['sha256']}
        if contract.get(key) != expected:
            raise ValueError('q004_grading_input_changed:' + key)
    return plan, base, proof


def context_path(output):
    return output.with_name(output.name + '-q004-context.json')


def execution_implementation():
    if hashlib.sha256(BASE_DRIVER.read_bytes()).hexdigest() != BASE_DRIVER_SHA:
        raise ValueError('original_native_driver_changed')
    spec = importlib.util.spec_from_file_location('_native_q004_execution', BASE_DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROTOCOL, module.TASKS = PROTOCOL, TASKS
    module.preparation = preparation
    module.identity = identity.implementation()
    module.context_path = context_path
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'main-root', 'reference-prepared', 'prepared-group', 'candidate', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('command', choices=['dry-run', 'run', 'collect'])
    args = parser.parse_args(argv)
    for name in ('plan', 'main_root', 'reference_prepared', 'prepared_group', 'candidate', 'output'):
        setattr(args, name, getattr(args, name).resolve())
    args.task_id = 'q004'
    plan, base, proof = validate_plan(args.plan, args.main_root, args.reference_prepared,
                                     args.prepared_group, args.candidate)
    return execution_implementation().execute(args, plan, base, proof)


if __name__ == '__main__':
    raise SystemExit(main())
