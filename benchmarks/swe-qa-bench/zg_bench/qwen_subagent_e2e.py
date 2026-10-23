"""Qwen3.8max delegation availability and Rust ZG: three contemporaneous arms.

Smoke is one task once (3 answers); formal is the frozen 20 tasks ten times
(600 answers). Preserve all physical attempts. Collection cannot rerun agents.
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

from zg_bench import native_baseline_e2e as native
from zg_bench import version_e2e as versions
from zg_bench.swe_qa.pairs import load_references
from zg_bench.swe_qa.collect import _completed_job, _profile_result, _select_trials
from zg_bench.core.protocol import SCORE_KEYS, judge_label
from zg_bench.engines.judge import judge_generation_metadata, judge_temperature
from zg_bench.engines.registry import resolve_opencode_model
from zg_bench.metrics.comparison import case_comparison
from zg_bench.metrics.summary import summarize_profile
from zg_bench.metrics.numbers import sum_or_none
from zg_bench.metrics.usage import usage_scope
from zg_bench.reports.validation import validate_task_report
from zg_bench.settings import BENCHMARK_SEED, OPENCODE_VERSION
from zg_bench.qwen_subagent_audit import audit
from zg_bench.qwen_subagent_qualification import LIFECYCLE_CONTRACT, qualify
from concurrent.futures import ThreadPoolExecutor
import hashlib

ARMS = ('baseline_allowed', 'baseline', 'rust')
REPETITIONS = 10
MODEL = 'qwen3.8-max'
DATA = versions.DATA
SOURCE_COMMIT = 'ee94f3782b6a6853c6037af4498e8532ebdc55d3'
SMOKE_TASK = 'conan-19'
SOURCE_EXPERIMENT = json.loads((versions.ROOT.parent / 'version-comparison/experiment.json').read_text())
EXPERIMENT = None
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
    global REPETITIONS, EXPERIMENT
    selected_tasks(phase)
    repetitions = PHASE_REPETITIONS[phase]
    # Copy only still-applicable controls. Historical trimming, round labels,
    # validation counts and planned sample counts must not leak into this run.
    preserved = (
        'rust', 'embeddings', 'embedding_model',
        'embedding_dimension', 'agent_runtime',
        'locked_input_sha256', 'max_parallel_task_jobs',
        'per_task_agent_parallelism', 'judge_concurrency_per_task',
        'agent_timeout_seconds', 'mcp_timeout_seconds', 'max_harbor_retries',
        'fresh_executions', 'container_fix_pr', 'container_fix_commit', 'container_init',
    )
    experiment = {key: copy.deepcopy(SOURCE_EXPERIMENT[key]) for key in preserved}
    experiment.update(
        kind='qwen38-subagent-ablation', phase=phase, round=1, groups=list(ARMS),
        e2e_repetitions=repetitions, expected_tasks=len(selected_tasks(phase)),
        planned_agent_trials=len(selected_tasks(phase)) * repetitions * len(ARMS),
        created_at='2026-10-23', models=[MODEL], harness_base=SOURCE_COMMIT,
        task_selection={'source': 'frozen historical 20-task selection.json',
                        'scheduled_tasks': selected_tasks(phase)},
        package_source={'repository': 'Cuiyus/zvec-grep', 'run_id': 38700270964},
        source_harness_commit=SOURCE_COMMIT,
        source_experiment_kind=SOURCE_EXPERIMENT['kind'],
        source_experiment_round=SOURCE_EXPERIMENT['round'],
        no_subagent={'enabled': True, 'mechanism': 'permission.task=deny',
                     'denied_arms': ['baseline', 'rust'],
                     'allowed_control': 'baseline_allowed',
                     'allowed_control_policy': 'Original OpenCode permissions; delegation permitted, not forced'},
        subagent_policy={arm: 'allowed_not_forced' if arm == 'baseline_allowed' else 'denied' for arm in ARMS},
        execution_order='Rotate all three arms within each task and repetition; first-arm frequencies 67/67/66 across 200 formal blocks',
        aggregation_policy='All trials without trimming or task exclusion; equal-weight task means; paired task bootstrap',
        analysis_policy='All completed scheduled trials; no quality or resource trimming; retain all failed attempts',
        historical_comparison='Descriptive only: historical rounds ran on different dates',
    )
    REPETITIONS = native.REPETITIONS = repetitions
    EXPERIMENT = experiment
    os.environ['ZG_BENCH_DISABLE_SUBAGENTS'] = '1'
    return experiment


def ordered_arms(task, repetition):
    tasks = [row['task_slug'] for row in json.loads((DATA / 'selection.json').read_text())['tasks']]
    shift = (tasks.index(task) + repetition - 1) % len(ARMS)
    return ARMS[shift:] + ARMS[:shift]


def command(task, arm, output, packages, repetition):
    if arm not in ARMS:
        raise ValueError(f'Unknown arm: {arm}')
    if arm.startswith('baseline'):
        return native.command(task, MODEL, output / arm, repetition)
    return [
        'zg-bench', 'run', 'swe-qa-bench', '--tier', 'full', '--task', task,
        '--agent', 'opencode', '--model', f'custom-openai/{MODEL}',
        '--profile', 'zvec-grep', '--n-attempts', '1', '--max-retries', '2',
        '--embedding-model', EXPERIMENT['embedding_model'],
        '--zvec-grep-package', str(packages / 'rust' / 'candidate.tgz'),
        '--jobs-dir', str(output / 'runs' / 'rust' / f'r{repetition}'),
        '--job-name', f'qwen38-{task}-rust-r{repetition}',
    ]


def validate_model_settings():
    value = resolve_opencode_model(f'custom-openai/{MODEL}', require_credentials=False).configuration
    actual = (value.provider, value.model_id, value.temperature, value.enable_thinking,
              value.reasoning_effort, value.output_limit, value.interleaved, OPENCODE_VERSION, BENCHMARK_SEED)
    expected = ('custom-openai', MODEL, 0, True, 'high', 32000, True, '1.18.4', 42)
    if actual != expected:
        raise ValueError('Qwen model/runtime settings differ from verified historical requests')
    return dict(model=value.harbor_model, temperature=value.temperature, enable_thinking=value.enable_thinking,
                reasoning_effort=value.reasoning_effort, output_limit=value.output_limit,
                interleaved='reasoning_content', opencode_version=OPENCODE_VERSION, seed=BENCHMARK_SEED)


def collect_rust(root, task, identity):
    variant = 'rust'
    trials = []
    for rep in range(1, REPETITIONS + 1):
        repetition_root = root / "runs" / variant / f"r{rep}"
        # Harbor only appends profile suffixes for multi-profile runs.
        # Single-profile jobs retain --job-name verbatim. Identify their
        # immediate job directory, then apply the unchanged evidence gates.
        jobs = [p for p in repetition_root.glob("*") if p.is_dir()
                and (p / "result.json").is_file()
                and any(p.glob("*/agent/trajectory.json"))]
        if len(jobs) != 1:
            raise ValueError(f"{variant} repetition {rep}: expected one zg job, got {len(jobs)}")
        job = jobs[0]
        _completed_job(job, expected_trials=1)
        selected = _select_trials(job, task, expected_trials=1)
        trial_dir, result = selected[0]
        setup = json.loads((trial_dir / "agent/zvec-grep-setup.json").read_text())
        if setup.get("status") != "ready":
            raise ValueError("zg setup did not reach ready")
        if setup.get("embedding_model") != EXPERIMENT["embedding_model"]:
            raise ValueError("Installed embedding model differs from the experiment")
        for key in ("package_sha256", "cli_sha256"):
            if setup.get(key) != identity[key]:
                raise ValueError(f"{variant}: installed {key} mismatch")
        if MODEL:
            usage = json.loads((trial_dir / "agent/session-usage.json").read_text())
            models = {(m["provider_id"], m["model_id"]) for s in usage["sessions"] for m in s["provider_models"]}
            if models != {("custom-openai", MODEL)}:
                raise ValueError(f"Actual session models differ from the experiment: {models}")
        row = _profile_result(
            profile="zvec-grep", job_dir=job, trial_dir=trial_dir,
            result=result, trial_index=rep,
        )
        row.update(variant=variant, provenance=identity)
        trials.append(row)
    return trials


def judge_profiles(trials, reference, output):
    # Score each physical answer once, checkpoint before building pairwise views.
    work = [(arm, trial) for arm in ARMS for trial in trials[arm]]
    def score(item):
        arm, trial = item
        payload = dict(trial=trial, reference=reference, model=MODEL,
                       generation=judge_generation_metadata(MODEL), seed=BENCHMARK_SEED)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        path = output / 'judgements' / arm / f"{trial['trial_index']}.json"
        if path.exists():
            cached = json.loads(path.read_text())
            if cached['input_sha256'] != fingerprint:
                raise ValueError('Cached judgment has different input evidence')
            return arm, cached['result']
        result = native.judge([trial], reference, MODEL)[0]
        versions.write_json(path, dict(input_sha256=fingerprint, result=result))
        return arm, result
    profiles = {arm: [] for arm in ARMS}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for arm, result in pool.map(score, work):
            profiles[arm].append(result)
    return {arm: summarize_profile(rows) for arm, rows in profiles.items()}


def judge_metadata(profiles):
    usages = [p['judge']['usage'] for p in profiles.values()]
    return dict(label=judge_label(MODEL), model=MODEL, self_judge=True,
                temperature=judge_temperature(MODEL), seed=BENCHMARK_SEED,
                **judge_generation_metadata(MODEL), rubric=list(SCORE_KEYS),
                usage={key: sum_or_none([u[key] for u in usages]) for key in
                       ('calls', 'input_tokens', 'output_tokens', 'cost_usd')})


def paired_report(report, arm):
    profiles = {'baseline': report['profiles']['baseline'], 'zvec-grep': report['profiles'][arm]}
    case = dict(task_id=report['task_id'], trial_count=REPETITIONS, profiles=profiles)
    case['comparison'] = case_comparison(case)
    return dict(schema_version=4, benchmark='peng-weihan/SWE-QA-Bench',
                usage_scope=usage_scope(profiles['baseline']['metrics']),
                judge=judge_metadata(profiles), cases=[case],
                gate=dict(passed=True, report_only=True, successful_judgements=REPETITIONS * 2))


def validate_report(report, path):
    if report.get('kind') != EXPERIMENT['kind'] or report.get('model') != MODEL:
        raise ValueError('Wrong experiment or model in report')
    meta = report['provenance']
    if meta['experiment'] != EXPERIMENT or meta['repetitions'] != REPETITIONS:
        raise ValueError('Report protocol differs from frozen experiment')
    if set(report.get('profiles', {})) != set(ARMS):
        raise ValueError('Exactly three Qwen profiles are required')
    expected = {(arm, rep) for arm in ARMS for rep in range(1, REPETITIONS + 1)}
    if (len(meta['execution']) != len(expected) or any(r['returncode'] for r in meta['execution'])
            or {(r['arm'], r['repetition']) for r in meta['execution']} != expected):
        raise ValueError('Missing, duplicate or failed execution slots')
    for arm in ('baseline_allowed', 'rust'):
        pair = paired_report(report, arm)
        validate_task_report(copy.deepcopy(pair), path)
        if any(p['trial_count'] != REPETITIONS for p in pair['cases'][0]['profiles'].values()):
            raise ValueError('Wrong trial count')
    if report['judge'] != judge_metadata(report['profiles']):
        raise ValueError('Actual judge accounting mismatch')


def summarize(reports, output, *, phase, expected_tasks):
    from zg_bench.qwen_subagent_summary import summarize as summarize_all
    return summarize_all(reports, output, expected_tasks=expected_tasks,
                         repetitions=PHASE_REPETITIONS[phase])


def run(args):
    experiment = configure(args.phase)
    if args.task not in selected_tasks(args.phase):
        raise ValueError(f'Task {args.task} is not scheduled for phase {args.phase}')
    repetitions = PHASE_REPETITIONS[args.phase]
    native.validate_inputs()
    settings = validate_model_settings()
    output, packages = args.output.resolve(), args.packages.resolve()
    identities = {arm: versions.identity(packages / arm, arm) for arm in ('rust',)}
    if args.collect_only:
        meta = json.loads((output / 'provenance.json').read_text())
        if (meta['experiment'] != experiment or meta['identities'] != identities
                or meta['task'] != args.task or meta['repetitions'] != repetitions
                or meta.get('model') != MODEL or meta.get('model_settings') != settings):
            raise ValueError('Cannot resume different execution evidence')
    else:
        if (output / 'provenance.json').exists():
            raise ValueError('Existing execution evidence; do not overwrite or rerun it')
        meta = dict(task=args.task, model=MODEL, repetitions=repetitions,
                    identities=identities, experiment=experiment, model_settings=settings, execution=[],
                    installation_lifecycle_contract=copy.deepcopy(LIFECYCLE_CONTRACT),
                    harness_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip())
        versions.write_json(output / 'provenance.json', meta)
        meta['task_image'] = versions.prebuild_task_image(versions.ROOT / 'datasets' / args.task, output)
        versions.write_json(output / 'provenance.json', meta)
        for repetition in range(1, repetitions + 1):
            for arm in ordered_arms(args.task, repetition):
                env = dict(os.environ, ZG_BENCH_DISABLE_SUBAGENTS='0' if arm == 'baseline_allowed' else '1')
                for key in ('ZG_BENCH_CLI_RUNTIME', 'ZG_BENCH_EXPECTED_CLI_SHA256', 'ZG_BENCH_CAPTURE_INDEX_OUTPUT'):
                    env.pop(key, None)
                if arm == 'rust':
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
    finish_collection(output, args.task, identities, meta, phase=args.phase)


def audit_and_qualify(output, meta):
    """Keep a failed strict audit; qualify only proved pre-agent install failures."""
    slots = len(ARMS) * meta['repetitions']
    try:
        evidence = audit(output, expected_slots=slots)
    except ValueError:
        evidence = json.loads((output / 'no-subagent-audit.json').read_text())
    meta['no_subagent_audit'] = evidence
    versions.write_json(output / 'provenance.json', meta)
    if not evidence['complete']:
        qualification = qualify(output, expected_slots=slots, audit=evidence)
        versions.write_json(output / 'qualification.json', qualification)
        if not qualification['eligible']:
            raise ValueError('Failed physical-attempt audit cannot qualify final answers; preserve all evidence')
        meta['final_answer_qualification'] = qualification
        versions.write_json(output / 'provenance.json', meta)
    expected = {(arm, rep) for arm in ARMS for rep in range(1, meta['repetitions'] + 1)}
    if (len(meta['execution']) != slots or any(row['returncode'] for row in meta['execution'])
            or {(row['arm'], row['repetition']) for row in meta['execution']} != expected):
        raise RuntimeError('Execution slots failed or missing; preserve all evidence without partial quality claim')


def collect_trials(output, task, identities):
    return {'baseline_allowed': native.collect(output / 'baseline_allowed', task, MODEL),
            'baseline': native.collect(output / 'baseline', task, MODEL),
            'rust': collect_rust(output, task, identities['rust'])}


def finish_collection(output, task, identities, meta, *, phase):
    audit_and_qualify(output, meta)
    trials = collect_trials(output, task, identities)
    versions.write_json(output / 'trials.json', trials)
    reference = load_references(DATA / 'references.json')[task]
    profiles = judge_profiles(trials, reference, output)
    report = dict(schema_version=1, kind=EXPERIMENT['kind'], model=MODEL,
                  task_id=reference['task_id'], profiles=profiles,
                  judge=judge_metadata(profiles), provenance=meta)
    validate_report(report, output / 'report/report.json')
    versions.write_json(output / 'report/report.json', report)
    summary = summarize([report], output / 'report', phase=phase, expected_tasks=1)
    if not summary['complete']:
        raise ValueError('Task summary is incomplete; preserve evidence and inspect report/comparison.json')
    return report


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
