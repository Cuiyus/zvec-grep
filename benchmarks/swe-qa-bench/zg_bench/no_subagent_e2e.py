"""One GLM-5.2 four-arm round isolating delegation availability and ZG access.

The smoke experiment is separate evidence (one conan-19 block, four trials).
The formal experiment is the frozen 20-task corpus, ten blocks each (800 trials).
One native baseline permits delegation without forcing the model to use it;
the other native baseline and both ZG arms deny delegation at the tool layer.
Existing product, model, collection and judging implementations are reused.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

from zg_bench import three_arm_e2e as three_arm
from zg_bench import native_baseline_e2e as native
from zg_bench import version_e2e as versions
from zg_bench.swe_qa.pairs import load_references

ARMS = ('baseline_allowed', 'baseline', 'node', 'rust')
MODEL = three_arm.MODEL
DATA = three_arm.DATA
SOURCE_COMMIT = '9baeaa134b50080b621dc1834f467b2f14f9dc6d'
SMOKE_TASK = 'conan-19'
SOURCE_EXPERIMENT = copy.deepcopy(three_arm.EXPERIMENT)
PHASE_REPETITIONS = {'smoke': 1, 'formal': 10}


def selected_tasks(phase):
    if phase not in PHASE_REPETITIONS:
        raise ValueError(f'Unknown experiment phase: {phase}')
    tasks = [row['task_slug'] for row in json.loads((DATA / 'selection.json').read_text())['tasks']]
    if len(tasks) != 20 or len(set(tasks)) != 20 or SMOKE_TASK not in tasks:
        raise ValueError('Expected the frozen 20-task corpus including the smoke task')
    return [SMOKE_TASK] if phase == 'smoke' else tasks


def configure(phase):
    """Configure only this process; never edit the historical protocol files."""
    selected_tasks(phase)
    repetitions = PHASE_REPETITIONS[phase]
    # Copy only still-applicable controls. Historical trimming, round labels,
    # validation counts and planned sample counts must not leak into this run.
    preserved = (
        'node', 'rust', 'models', 'embeddings', 'embedding_model',
        'embedding_dimension', 'agent_runtime', 'node_native_binding_package',
        'locked_input_sha256', 'max_parallel_task_jobs',
        'per_task_agent_parallelism', 'judge_concurrency_per_task',
        'agent_timeout_seconds', 'mcp_timeout_seconds', 'max_harbor_retries',
        'fresh_executions', 'container_fix_pr', 'container_fix_commit', 'container_init',
    )
    experiment = {key: copy.deepcopy(SOURCE_EXPERIMENT[key]) for key in preserved}
    experiment.update(
        kind='glm52-subagent-ablation', phase=phase, round=1, groups=list(ARMS),
        e2e_repetitions=repetitions, expected_tasks=len(selected_tasks(phase)),
        planned_agent_trials=len(selected_tasks(phase)) * repetitions * len(ARMS),
        created_at='2026-10-22', harness_base=SOURCE_COMMIT,
        task_selection={'source': 'frozen historical 20-task selection.json',
                        'scheduled_tasks': selected_tasks(phase)},
        package_source={'repository': 'Cuiyus/zvec-grep', 'run_id': 38700270964},
        source_harness_commit=SOURCE_COMMIT,
        source_experiment_kind=SOURCE_EXPERIMENT['kind'],
        source_experiment_round=SOURCE_EXPERIMENT['round'],
        no_subagent={'enabled': True, 'mechanism': 'permission.task=deny',
                     'denied_arms': ['baseline', 'node', 'rust'],
                     'allowed_control': 'baseline_allowed',
                     'allowed_control_policy': 'Original OpenCode permissions; delegation permitted, not forced'},
        subagent_policy={arm: 'allowed_not_forced' if arm == 'baseline_allowed' else 'denied' for arm in ARMS},
        execution_order='Rotate all four arms within each task and repetition; each arm starts 50 of 200 formal blocks',
        aggregation_policy='All trials without trimming or task exclusion; equal-weight task means; paired task bootstrap',
        analysis_policy='All completed scheduled trials; no quality or resource trimming; retain all failed attempts',
        historical_comparison='Descriptive only: historical rounds ran on different dates',
    )
    for module in (three_arm, native, versions):
        module.REPETITIONS = repetitions
    three_arm.ARMS = ARMS
    three_arm.EXPERIMENT = experiment
    versions.EXPERIMENT = experiment
    os.environ['ZG_BENCH_DISABLE_SUBAGENTS'] = '1'
    return experiment


def ordered_arms(task, repetition):
    tasks = [row['task_slug'] for row in json.loads((DATA / 'selection.json').read_text())['tasks']]
    shift = (tasks.index(task) + repetition - 1) % len(ARMS)
    return ARMS[shift:] + ARMS[:shift]


def command(task, arm, output, packages, repetition):
    if arm == 'baseline_allowed':
        return native.command(task, MODEL, output / 'baseline_allowed', repetition)
    if arm not in ARMS:
        raise ValueError(f'Unknown arm: {arm}')
    return three_arm.command(task, arm, output, packages, repetition)


def validate_report(report, path):
    # The historical validator covers the two ZG-versus-denied-baseline pairs
    # and validates the execution slots and aggregate judge usage for all arms.
    three_arm.validate_report(report, path)
    # Also validate allowed-versus-denied native baselines with the same full
    # quality and session-usage checks. The generic pair schema uses historical
    # baseline/zvec-grep slot names only internally; neither arm here has ZG.
    native_comparison = dict(report, profiles={
        'baseline': report['profiles']['baseline_allowed'],
        'baseline_allowed': report['profiles']['baseline'],
    })
    pair = three_arm.paired_report(native_comparison, 'baseline_allowed')
    three_arm.validate_task_report(copy.deepcopy(pair), path)
    if any(profile['trial_count'] != three_arm.REPETITIONS
           for profile in pair['cases'][0]['profiles'].values()):
        raise ValueError('Wrong native baseline trial count')


def summarize(reports, output, *, phase, expected_tasks):
    from zg_bench.no_subagent_summary import summarize as summarize_all
    return summarize_all(reports, output, expected_tasks=expected_tasks,
                         repetitions=PHASE_REPETITIONS[phase])


def run(args):
    experiment = configure(args.phase)
    if args.task not in selected_tasks(args.phase):
        raise ValueError(f'Task {args.task} is not scheduled for phase {args.phase}')
    repetitions = PHASE_REPETITIONS[args.phase]
    native.validate_inputs()
    output, packages = args.output.resolve(), args.packages.resolve()
    identities = {arm: versions.identity(packages / arm, arm) for arm in ('node', 'rust')}
    if args.collect_only:
        meta = json.loads((output / 'provenance.json').read_text())
        if (meta['experiment'] != experiment or meta['identities'] != identities
                or meta['task'] != args.task or meta['repetitions'] != repetitions):
            raise ValueError('Cannot resume different execution evidence')
    else:
        if (output / 'provenance.json').exists():
            raise ValueError('Existing execution evidence; do not overwrite or rerun it')
        meta = dict(task=args.task, model=MODEL, repetitions=repetitions,
                    identities=identities, experiment=experiment, execution=[],
                    harness_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip())
        versions.write_json(output / 'provenance.json', meta)
        meta['task_image'] = versions.prebuild_task_image(versions.ROOT / 'datasets' / args.task, output)
        versions.write_json(output / 'provenance.json', meta)
        for repetition in range(1, repetitions + 1):
            for arm in ordered_arms(args.task, repetition):
                env = dict(os.environ, ZG_BENCH_DISABLE_SUBAGENTS='0' if arm == 'baseline_allowed' else '1')
                for key in ('ZG_BENCH_CLI_RUNTIME', 'ZG_BENCH_EXPECTED_CLI_SHA256', 'ZG_BENCH_CAPTURE_INDEX_OUTPUT'):
                    env.pop(key, None)
                if arm in ('node', 'rust'):
                    env.update(ZG_BENCH_CLI_RUNTIME=arm,
                               ZG_BENCH_EXPECTED_CLI_SHA256=identities[arm]['cli_sha256'],
                               ZG_BENCH_CAPTURE_INDEX_OUTPUT='1')
                execution_command = command(args.task, arm, output, packages, repetition)
                started_at = datetime.now(timezone.utc).isoformat()
                started = time.monotonic()
                print(f'START {args.phase} {args.task} {arm} repetition={repetition}/{repetitions}', flush=True)
                result = subprocess.run(execution_command, env=env)
                meta['execution'].append(dict(
                    arm=arm, repetition=repetition, returncode=result.returncode,
                    command=execution_command, started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    wall_seconds_including_setup_and_retries=time.monotonic() - started,
                ))
                versions.write_json(output / 'provenance.json', meta)
                print(f'COMPLETE {args.phase} {args.task} {arm} repetition={repetition} returncode={result.returncode}', flush=True)
    # Inspect every attempt, including retries. This precedes all collection and
    # paid judging; an execution failure never leads to a partial quality report.
    from zg_bench.no_subagent_audit import audit
    meta['no_subagent_audit'] = audit(output, expected_slots=len(ARMS) * repetitions)
    versions.write_json(output / 'provenance.json', meta)
    if len(meta['execution']) != repetitions * len(ARMS) or any(row['returncode'] for row in meta['execution']):
        raise RuntimeError('Execution slots failed or missing; preserve all evidence and no complete-quality report')
    # collect() has a definition-time default of ten; pass the count explicitly
    # so smoke collection cannot accidentally expect ten repetitions.
    pair = versions.collect(output, args.task, identities, repetitions=repetitions,
                            expected_model=MODEL, expected_embedding=experiment['embedding_model'])
    trials = {'baseline_allowed': native.collect(output / 'baseline_allowed', args.task, MODEL),
              'baseline': native.collect(output / 'baseline', args.task, MODEL),
              'node': pair['profiles']['baseline']['trials'],
              'rust': pair['profiles']['zvec-grep']['trials']}
    versions.write_json(output / 'trials.json', trials)
    reference = load_references(DATA / 'references.json')[args.task]
    profiles = three_arm.judge_profiles(trials, reference, output)
    report = dict(schema_version=1, kind=experiment['kind'], model=MODEL,
                  task_id=reference['task_id'], profiles=profiles,
                  judge=three_arm.judge_metadata(profiles), provenance=meta)
    validate_report(report, output / 'report/report.json')
    versions.write_json(output / 'report/report.json', report)
    summary = summarize([report], output / 'report', phase=args.phase, expected_tasks=1)
    if not summary['complete']:
        raise ValueError('Task summary is incomplete; preserve evidence and inspect report/comparison.json')


def aggregate(args):
    configure(args.phase)
    paths = sorted(args.reports.rglob('report.json'))
    reports = [json.loads(path.read_text()) for path in paths]
    allowed = set(selected_tasks(args.phase))
    for path, report in zip(paths, reports):
        validate_report(report, path)
        if report['provenance']['task'] not in allowed:
            raise ValueError(f'Unexpected task in {args.phase} aggregate: {path}')
    return summarize(reports, args.output, phase=args.phase, expected_tasks=len(allowed))


def main():
    parser = argparse.ArgumentParser(__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    task = sub.add_parser('run')
    task.add_argument('--phase', required=True, choices=PHASE_REPETITIONS)
    task.add_argument('--task', required=True)
    task.add_argument('--packages', required=True, type=Path)
    task.add_argument('--output', required=True, type=Path)
    task.add_argument('--collect-only', action='store_true')
    merge = sub.add_parser('aggregate')
    merge.add_argument('--phase', required=True, choices=PHASE_REPETITIONS)
    merge.add_argument('--reports', required=True, type=Path)
    merge.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'run':
        run(args)
    else:
        summary = aggregate(args)
        raise SystemExit(0 if summary['complete'] else 1)


if __name__ == '__main__':
    main()
