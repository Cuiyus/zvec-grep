#!/usr/bin/env python3
"""One predeclared native-Claude confirmation pair, using frozen live bodies.

The confirmation plan carries references and pair order only. Solver settings,
preparation, slot execution, cleanup and collection come from live-plan-v2 and
the unmodified live.py. Importing or validating this module makes no calls.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import live

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROTOCOL = 'native-claude-q001-confirmation-v1'
PREFIX = 'benchmarks/native-claude-qa-pilot/'
BASE_PLAN = PREFIX + 'live-plan-v2.json'
BASE_PLAN_SHA256 = 'b4e8fb15b6cae82a9083add53ad7c04e67673720f7a50825fde587b7538ad026'
DEVELOPMENT_SUMMARY = PREFIX + 'results/20261020-live-v2/summary.json'
WORKFLOW = '.github/workflows/native-claude-qa-confirmation-v1.yml'
DECISION = PREFIX + 'confirmation-decision-v1.json'
GRADE_CONTRACT = PREFIX + 'grader-contract-v1.json'
PAIRS = [
    {'id': 'c01', 'profiles': ['baseline', 'zvec-grep']},
    {'id': 'c02', 'profiles': ['zvec-grep', 'baseline']},
]
REQUIRED_KEYS = {
    'schema_version', 'protocol_id', 'frozen', 'base_plan',
    'development_summary', 'pairs', 'n_attempts', 'max_retries',
    'execution_workflow', 'execution_decision', 'grade_contract',
    'frozen_harness_sha256',
}


def pair_profiles(pair):
    for record in PAIRS:
        if pair == record['id']:
            return list(record['profiles'])
    raise ValueError('undeclared_confirmation_pair')


def validate_plan(plan_path, pair, main_root, prepared, candidate, repo_root=REPO):
    # Validate the original protocol before interpreting the confirmation plan.
    # In particular, do not validate it with the reversed private PROFILES.
    base_path = live.pilot.safe_file(repo_root, BASE_PLAN)
    if live.pilot.digest(base_path) != BASE_PLAN_SHA256:
        raise ValueError('frozen_base_plan_changed')
    base, proof = live.validate_plan(base_path, main_root, prepared, candidate, repo_root)
    plan = live.pilot.load(plan_path)
    pair_profiles(pair)
    if (not isinstance(plan, dict) or not REQUIRED_KEYS <= plan.keys()
            or set(plan) - REQUIRED_KEYS - {'frozen_at_utc'}):
        raise ValueError('confirmation_reference_schema_only_no_overrides')
    if ('frozen_at_utc' in plan and (not isinstance(plan['frozen_at_utc'], str)
                                    or not plan['frozen_at_utc'])):
        raise ValueError('invalid_freeze_provenance')
    if (type(plan['schema_version']) is not int or plan['schema_version'] != 1
            or plan['protocol_id'] != PROTOCOL or plan['frozen'] is not True
            or plan['pairs'] != PAIRS
            or type(plan['n_attempts']) is not int or plan['n_attempts'] != 1
            or type(plan['max_retries']) is not int or plan['max_retries'] != 0):
        raise ValueError('confirmation_schedule_or_protocol_mismatch')
    if plan['base_plan'] != {'path': BASE_PLAN, 'sha256': BASE_PLAN_SHA256}:
        raise ValueError('confirmation_base_reference_mismatch')
    ref = plan['development_summary']
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'} or ref['path'] != DEVELOPMENT_SUMMARY:
        raise ValueError('development_summary_reference_mismatch')
    summary_path = live.pilot.safe_file(repo_root, ref['path'])
    if live.pilot.digest(summary_path) != ref['sha256']:
        raise ValueError('development_summary_changed')
    summary = live.pilot.load(summary_path)
    if (summary.get('protocol_id') != live.PROTOCOL
            or summary.get('plan_sha256') != BASE_PLAN_SHA256
            or summary.get('evaluation', {}).get('reported_input_development_candidate') is not True):
        raise ValueError('accepted_development_candidate_required')
    if (plan['execution_workflow'] != WORKFLOW or plan['execution_decision'] != DECISION
            or plan['grade_contract'] != GRADE_CONTRACT):
        raise ValueError('confirmation_control_path_mismatch')
    pins = plan['frozen_harness_sha256']
    required = {PREFIX + name for name in ('confirmation_v1.py', 'test_confirmation_v1.py')}
    required.update((WORKFLOW, DECISION, GRADE_CONTRACT))
    if not isinstance(pins, dict) or not required <= pins.keys():
        raise ValueError('confirmation_control_pins_missing')
    for relative, expected in pins.items():
        if live.pilot.digest(live.pilot.safe_file(repo_root, relative)) != expected:
            raise ValueError('confirmation_control_changed:' + relative)
    decision = live.pilot.load(live.pilot.safe_file(repo_root, DECISION))
    if (decision.get('protocol_id') != PROTOCOL
            or decision.get('status') != 'approved_for_two_fresh_pairs'
            or decision.get('base_plan_sha256') != BASE_PLAN_SHA256
            or decision.get('pairs') != PAIRS
            or type(decision.get('n_attempts')) is not int or decision['n_attempts'] != 1
            or type(decision.get('max_retries')) is not int or decision['max_retries'] != 0):
        raise ValueError('confirmation_decision_mismatch')
    return plan, base, proof


def private_live(pair):
    """Load the frozen source afresh; change only protocol and scheduled order."""
    profiles = pair_profiles(pair)
    spec = importlib.util.spec_from_file_location('_native_claude_confirmation_' + pair, HERE / 'live.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROTOCOL = PROTOCOL
    module.PROFILES = profiles
    return module


def context_path(output):
    return output.with_name(output.name + '-confirmation-context.json')


def context_record(args, plan, base, *, dry_run):
    return {
        'schema_version': 1, 'protocol_id': PROTOCOL, 'pair_id': args.pair,
        'profiles': pair_profiles(args.pair), 'plan_sha256': live.pilot.digest(args.plan),
        'base_plan_sha256': plan['base_plan']['sha256'],
        'development_summary_sha256': plan['development_summary']['sha256'],
        'offline_proof_sha256': base['offline_proof']['proof_sha256'],
        'candidate_sha256': base['candidate_sha256'], 'dry_run': dry_run,
    }


def execute(args, plan, base, proof):
    module = private_live(args.pair)
    context = context_record(args, plan, base, dry_run=args.command == 'dry-run')
    sidecar = context_path(args.output)
    if args.command == 'collect':
        if sidecar.is_symlink() or not sidecar.is_file() or live.pilot.load(sidecar) != context:
            raise ValueError('confirmation_context_missing_or_mismatched')
        # collect itself checks new protocol/plan SHA and exact slot order.
        report = module.collect(args, base, proof)
        return 0 if report['structural_valid'] else 1
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError('confirmation_output_already_exists')
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    # This records pair identity even if run_pair fails before its first slot.
    # Exclusivity forbids retrying/replacing this invocation at the same path.
    with sidecar.open('x') as handle:
        json.dump(context, handle, indent=2, sort_keys=True)
        handle.write('\n')
    record = module.run_pair(args, base, dry_run=args.command == 'dry-run')
    return 0 if args.command == 'dry-run' or all(
        slot.get('status') == 'exited' and slot.get('returncode') == 0
        and module.cleanup_complete(slot) for slot in record['slots']) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'main-root', 'prepared', 'candidate', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--pair', choices=('c01', 'c02'), required=True)
    parser.add_argument('command', choices=('dry-run', 'run', 'collect'))
    args = parser.parse_args(argv)
    for name in ('plan', 'main_root', 'prepared', 'candidate', 'output'):
        setattr(args, name, getattr(args, name).resolve())
    plan, base, proof = validate_plan(args.plan, args.pair, args.main_root,
                                     args.prepared, args.candidate)
    return execute(args, plan, base, proof)


if __name__ == '__main__':
    raise SystemExit(main())
