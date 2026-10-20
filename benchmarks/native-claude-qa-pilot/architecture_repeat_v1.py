"""Ten new q004 pairs; retain the original native pair execution and collector."""
from __future__ import annotations

import argparse
from pathlib import Path
import random

import architecture_live_v1 as native

HERE = Path(__file__).resolve().parent
REPO = native.REPO
PREFIX = native.PREFIX
PROTOCOL = 'native-claude-zvec-q004-repeat-v1'
REFERENCE = PREFIX + 'architecture-live-plan-v1.json'
REFERENCE_SHA = '803750ff8050adf770325345b907ef662c578d6dff6e0fd480f92f78d3a1148d'
WORKFLOW = '.github/workflows/native-claude-q004-repeat-v1.yml'
DECISION = PREFIX + 'architecture-repeat-decision-v1.json'
SEED = 20261020
CONTROLS = {REFERENCE, WORKFLOW, DECISION} | {PREFIX + p for p in (
    'architecture_repeat_v1.py', 'architecture_repeat_analysis_v1.py',
    'test_architecture_repeat_v1.py', 'repeat_analysis_v1.py', 'robust_statistics_v1.py')}
FIXED = {
    'schema_version': 1, 'protocol_id': PROTOCOL, 'frozen': True,
    'task_ids': ['q004'], 'repetitions_per_profile': 10, 'scheduled_pairs': 10,
    'scheduled_solver_trials': 20, 'n_attempts_per_pair': 1, 'max_retries': 0,
    'max_parallel_pairs': 10, 'schedule_seed': SEED,
    'runtime_reference': {'path': REFERENCE, 'sha256': REFERENCE_SHA},
    'execution_workflow': WORKFLOW, 'execution_decision': DECISION,
}
EXTRA_KEYS = {'created_at_utc', 'schedule', 'frozen_harness_sha256', 'statistics',
              'grading_policy', 'historical_evidence', 'user_authorization', 'stopping',
              'prerequisites', 'base_plan', 'dataset', 'prepared_group'}


def schedule():
    pairs = [{'key': f'q004-r{i:02d}', 'task_id': 'q004', 'trial_index': i,
              'profiles': ['baseline', 'zvec-grep'] if i % 2 else ['zvec-grep', 'baseline']}
             for i in range(1, 11)]
    random.Random(SEED).shuffle(pairs)
    return pairs


def selected_pair(repetition):
    if type(repetition) is not int:
        raise ValueError('q004_repeat_integer_required')
    found = [p for p in schedule() if p['trial_index'] == repetition]
    if len(found) != 1:
        raise ValueError('undeclared_q004_repetition')
    return found[0]


def validate_plan(args, repo_root=REPO):
    selected_pair(args.repetition)
    reference = native.live.pilot.safe_file(repo_root, REFERENCE)
    if native.live.pilot.digest(reference) != REFERENCE_SHA:
        raise ValueError('original_q004_runtime_reference_changed')
    original, base, proof = native.validate_plan(reference, args.main_root,
        args.reference_prepared, args.prepared_group, args.candidate, repo_root)
    plan = native.live.pilot.load(args.plan)
    if (not isinstance(plan, dict) or set(plan) != set(FIXED) | EXTRA_KEYS
            or any(type(plan.get(k)) is not type(v) or plan[k] != v for k, v in FIXED.items())
            or plan['schedule'] != schedule()):
        raise ValueError('q004_frozen_repeat_cohort_mismatch')
    for key in ('base_plan', 'dataset', 'prepared_group'):
        if plan[key] != original[key]:
            raise ValueError('q004_original_task_or_runtime_changed:' + key)
    pins = plan['frozen_harness_sha256']
    if not isinstance(pins, dict) or not CONTROLS <= set(pins):
        raise ValueError('q004_repeat_control_pins_missing')
    for path, digest in pins.items():
        if native.live.pilot.digest(native.live.pilot.safe_file(repo_root, path)) != digest:
            raise ValueError('q004_repeat_control_changed:' + path)
    prerequisites = plan['prerequisites']
    if set(prerequisites) != {'development_conclusion'}:
        raise ValueError('q004_repeat_prerequisite_set_changed')
    prerequisite = prerequisites['development_conclusion']
    if (set(prerequisite) != {'path', 'sha256'} or pins.get(prerequisite['path']) != prerequisite['sha256']):
        raise ValueError('q004_repeat_prerequisite_not_pinned')
    conclusion = native.live.pilot.load(native.live.pilot.safe_file(repo_root, prerequisite['path']))
    if (conclusion.get('protocol_id') != native.PROTOCOL or conclusion.get('task_id') != 'q004'
            or conclusion.get('shared_gates_passed') is not True
            or conclusion.get('input_efficiency_candidate') is not True
            or conclusion.get('development_pair_in_confirmation') is not False):
        raise ValueError('q004_repeat_development_prerequisite_not_met')
    decision = native.live.pilot.load(native.live.pilot.safe_file(repo_root, DECISION))
    for key in set(FIXED) | {'schedule', 'statistics', 'grading_policy', 'stopping', 'prerequisites'}:
        if type(decision.get(key)) is not type(plan[key]) or decision[key] != plan[key]:
            raise ValueError('q004_repeat_decision_mismatch:' + key)
    if decision.get('paid_execution_authorized') is not True:
        raise ValueError('q004_repeat_execution_not_authorized')
    grading = plan['grading_policy']
    digest = grading.get('assignment_sha256', '')
    if (grading.get('planned_answers') != 20 or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)
            or grading.get('contract') != {'path': native.CONTRACT,
                'sha256': native.live.pilot.digest(repo_root / native.CONTRACT)}):
        raise ValueError('q004_repeat_grading_not_frozen')
    return plan, base, proof


def execution_implementation(repetition):
    pair = selected_pair(repetition)
    module = native.execution_implementation()
    module.PROTOCOL = PROTOCOL
    module.TASKS = [{'id': 'q004', 'profiles': pair['profiles']}]
    module.context_path = lambda output: output.with_name(output.name + '-q004-repeat-context.json')
    original_context = module.context_record

    def cohort_context(*args, **kwargs):
        result = original_context(*args, **kwargs)
        result.update(pair_key=pair['key'], trial_index=pair['trial_index'],
                      scheduled_pairs=10, scheduled_solver_trials=20)
        return result

    module.context_record = cohort_context
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'main-root', 'reference-prepared', 'prepared-group', 'candidate', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--repetition', type=int, required=True)
    parser.add_argument('command', choices=('dry-run', 'run', 'collect'))
    args = parser.parse_args(argv)
    for name in ('plan', 'main_root', 'reference_prepared', 'prepared_group', 'candidate', 'output'):
        setattr(args, name, getattr(args, name).resolve())
    args.task_id = 'q004'
    plan, base, proof = validate_plan(args)
    return execution_implementation(args.repetition).execute(args, plan, base, proof)


if __name__ == '__main__':
    raise SystemExit(main())
