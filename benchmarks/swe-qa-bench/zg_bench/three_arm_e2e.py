"""Fork-only GLM/local three-arm execution, using PR #261 statistics unchanged."""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from zg_bench import native_baseline_e2e as native
from zg_bench import version_e2e as versions
from zg_bench.core.protocol import SCORE_KEYS, judge_label
from zg_bench.engines.judge import judge_generation_metadata, judge_temperature
from zg_bench.metrics.comparison import case_comparison
from zg_bench.metrics.summary import aggregate_cases, summarize_profile
from zg_bench.metrics.numbers import sum_or_none
from zg_bench.metrics.usage import usage_scope
from zg_bench.reports.validation import validate_task_report
from zg_bench.settings import BENCHMARK_SEED
from zg_bench.swe_qa.pairs import load_references

ARMS = ('baseline', 'node', 'rust')
MODEL = 'glm-5.2'
LABELS = {'baseline': 'OpenCode baseline', 'node': 'Node 0.2.2 + zg', 'rust': 'Rust 732a440 + zg'}
ROOT, DATA = versions.ROOT, versions.DATA
REPETITIONS = versions.REPETITIONS
EXPERIMENT = versions.EXPERIMENT


def ordered_arms(task, repetition):
    tasks = json.loads((DATA / 'selection.json').read_text())['tasks']
    position = next(i for i, row in enumerate(tasks) if row['task_slug'] == task)
    # Each successive repetition/task rotates the first group on the same host.
    shift = (position + repetition - 1) % 3
    return ARMS[shift:] + ARMS[:shift]


def command(task, arm, output, packages, repetition):
    if arm == 'baseline':
        return native.command(task, MODEL, output / 'baseline', repetition)
    return [
        'zg-bench', 'run', 'swe-qa-bench', '--tier', 'full', '--task', task,
        '--agent', 'opencode', '--model', f'custom-openai/{MODEL}',
        '--profile', 'zvec-grep', '--n-attempts', '1', '--max-retries', '2',
        '--embedding-model', EXPERIMENT['embedding_model'],
        '--zvec-grep-package', str(packages / arm / 'candidate.tgz'),
        '--jobs-dir', str(output / 'runs' / arm / f'r{repetition}'),
        '--job-name', f'iqr-{task}-{arm}-r{repetition}',
    ]


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
    return dict(schema_version=3, benchmark='peng-weihan/SWE-QA-Bench',
                usage_scope=usage_scope(profiles['baseline']['metrics']),
                judge=judge_metadata(profiles), cases=[case],
                gate=dict(passed=True, report_only=True, successful_judgements=REPETITIONS * 2))


def validate_report(report, path):
    if report.get('kind') != EXPERIMENT['kind'] or report.get('model') != MODEL:
        raise ValueError('Wrong experiment or model in report')
    meta = report['provenance']
    if meta['experiment'] != EXPERIMENT or meta['repetitions'] != REPETITIONS:
        raise ValueError('Report protocol differs from frozen experiment')
    if len(meta['execution']) != len(ARMS) * REPETITIONS or any(r['returncode'] for r in meta['execution']):
        raise ValueError('Incomplete or failed execution evidence')
    expected = {(arm, rep) for arm in ARMS for rep in range(1, REPETITIONS + 1)}
    if {(r['arm'], r['repetition']) for r in meta['execution']} != expected:
        raise ValueError('Missing/duplicate execution slots')
    for arm in ('node', 'rust'):
        pair = paired_report(report, arm)
        validate_task_report(copy.deepcopy(pair), path)
        if any(p['trial_count'] != REPETITIONS for p in pair['cases'][0]['profiles'].values()):
            raise ValueError('Wrong trial count')
    if report['judge'] != judge_metadata(report['profiles']):
        raise ValueError('Actual judge accounting mismatch')


def run(args):
    native.validate_inputs()
    output, packages = args.output.resolve(), args.packages.resolve()
    identities = {arm: versions.identity(packages / arm, arm) for arm in ('node', 'rust')}
    if args.collect_only:
        meta = json.loads((output / 'provenance.json').read_text())
        if meta['experiment'] != EXPERIMENT or meta['identities'] != identities or meta['task'] != args.task:
            raise ValueError('Cannot resume different execution evidence')
    else:
        if (output / 'provenance.json').exists():
            raise ValueError('Existing execution evidence; do not overwrite or rerun it')
        meta = dict(task=args.task, model=MODEL, repetitions=REPETITIONS, identities=identities,
                    experiment=EXPERIMENT, execution=[],
                    harness_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip())
        versions.write_json(output / 'provenance.json', meta)
        meta['task_image'] = versions.prebuild_task_image(ROOT / 'datasets' / args.task, output)
        versions.write_json(output / 'provenance.json', meta)
        for repetition in range(1, REPETITIONS + 1):
            for arm in ordered_arms(args.task, repetition):
                env = dict(os.environ)
                for key in ('ZG_BENCH_CLI_RUNTIME', 'ZG_BENCH_EXPECTED_CLI_SHA256', 'ZG_BENCH_CAPTURE_INDEX_OUTPUT'):
                    env.pop(key, None)
                if arm != 'baseline':
                    env.update(ZG_BENCH_CLI_RUNTIME=arm,
                               ZG_BENCH_EXPECTED_CLI_SHA256=identities[arm]['cli_sha256'],
                               ZG_BENCH_CAPTURE_INDEX_OUTPUT='1')
                started = time.monotonic()
                print(f'START {args.task} {arm} repetition={repetition}/{REPETITIONS}', flush=True)
                result = subprocess.run(command(args.task, arm, output, packages, repetition), env=env)
                meta['execution'].append(dict(arm=arm, repetition=repetition, returncode=result.returncode,
                    wall_seconds_including_setup_and_retries=time.monotonic() - started))
                versions.write_json(output / 'provenance.json', meta)
                print(f'COMPLETE {args.task} {arm} repetition={repetition} returncode={result.returncode}', flush=True)
    if len(meta['execution']) != REPETITIONS * 3 or any(row['returncode'] for row in meta['execution']):
        raise RuntimeError('Execution slots failed or missing; preserve all evidence and no complete-quality report')
    pair = versions.collect(output, args.task, identities, expected_model=MODEL,
                            expected_embedding=EXPERIMENT['embedding_model'])
    trials = {'baseline': native.collect(output / 'baseline', args.task, MODEL),
              'node': pair['profiles']['baseline']['trials'], 'rust': pair['profiles']['zvec-grep']['trials']}
    versions.write_json(output / 'trials.json', trials)
    reference = load_references(DATA / 'references.json')[args.task]
    profiles = judge_profiles(trials, reference, output)
    report = dict(schema_version=1, kind=EXPERIMENT['kind'], model=MODEL, task_id=reference['task_id'],
                  profiles=profiles, judge=judge_metadata(profiles), provenance=meta)
    validate_report(report, output / 'report/report.json')
    versions.write_json(output / 'report/report.json', report)
    summarize([report], output / 'report')


def summarize(reports, output):
    expected = [t['task_id'] for t in json.loads((DATA / 'selection.json').read_text())['tasks']]
    by_task = {}
    for report in reports:
        validate_report(report, output)
        if report['task_id'] not in expected or report['task_id'] in by_task:
            raise ValueError('Unknown or duplicate task report')
        by_task[report['task_id']] = report
    reports = [by_task[t] for t in expected if t in by_task]
    if reports and any(r['provenance']['identities'] != reports[0]['provenance']['identities'] for r in reports):
        raise ValueError('Cannot combine different package/binary identities')
    if not reports:
        raise ValueError('No completed reports')
    pairwise = {arm: aggregate_cases([paired_report(r, arm)['cases'][0] for r in reports]) for arm in ('node', 'rust')}
    rows = []
    for arm in ARMS:
        agg = pairwise['node' if arm == 'baseline' else arm]
        slot = 'baseline' if arm == 'baseline' else 'zvec-grep'
        rows.append(dict(group=arm, label=LABELS[arm], tasks=len(reports), **agg['filter']['profiles'][slot],
                         raw=agg['raw']['profiles'][slot], filtered=agg['profiles'][slot]))
    missing = [t for t in expected if t not in by_task]
    data = dict(kind=EXPERIMENT['kind'], complete=not missing, expected_tasks=expected,
                completed_tasks=list(by_task), missing_tasks=missing, experiment=EXPERIMENT,
                judged_trials=sum(r['judge']['usage']['calls'] for r in reports), rows=rows,
                pairwise=pairwise, source_harness_commits=sorted({r['provenance']['harness_commit'] for r in reports}))
    versions.write_json(output / 'comparison.json', data)
    lines = ['# GLM-5.2 · local embedding · E2E', '',
        f"Completed tasks: **{len(reports)}/20**; scored trials: **{data['judged_trials']}/600**.", '',
        '| Group | Original | Retained | Excluded | Score raw → IQR | Agent seconds raw → IQR | Input tokens raw → IQR |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        cells = [f"{r['raw'][key]:,.2f} → {r['filtered'][key]:,.2f}" for key in ('judge','agent_wall_seconds','input_tokens')]
        lines.append(f"| {r['label']} | {r['original_count']} | {r['retained_count']} | {r['excluded_count']} | " + ' | '.join(cells) + ' |')
    lines += ['', 'PR #261: one-pass, two-sided 1.5×IQR on score within each task/group; inclusive type-7 quartiles. '
        'The same retained trials supply score, time and token means. All completed tasks are weighted equally. '
        'Raw results, failures, retry history and original judge usage remain archived.', '',
        f"Node: 0.2.2; Rust: {EXPERIMENT['rust']['source_commit']}; OpenCode: 1.18.4; local embedding: potion-code-16m-v2 (256D).",
        'Baseline has no zg or embedding. Each group runs 10 fresh trials per main-branch task.', '']
    if missing:
        lines += ['**Incomplete:** ' + ', '.join(missing) + '. No missing values are replaced with zero.', '']
    lines += ['<details>', '<summary>Per-task filtered results and exclusions</summary>', '',
              '| Task | Group | Retained | Score | Agent seconds | Input tokens | Excluded trial indices / scores |',
              '|---|---|---:|---:|---:|---:|---|']
    for index, report in enumerate(reports):
        for arm in ARMS:
            agg = pairwise['node' if arm == 'baseline' else arm]
            slot = 'baseline' if arm == 'baseline' else 'zvec-grep'
            row = agg['task_statistics'][index]['profiles'][slot]
            f, m = row['filter'], row['filtered']
            exclusions = ', '.join(f"r{t['trial_index']}={t['score']} ({t['direction']})" for t in f['excluded_trials']) or '—'
            lines.append(f"| {report['task_id']} | {LABELS[arm]} | {f['retained_count']}/{f['original_count']} | {m['judge']:.2f} | {m['agent_wall_seconds']:.2f} | {m['input_tokens']:,.2f} | {exclusions} |")
    lines += ['', '</details>', '']
    markdown = '\n'.join(lines)
    (output / 'report.md').write_text(markdown)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
            stream.write(markdown)
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    task = sub.add_parser('run')
    task.add_argument('--task', required=True)
    task.add_argument('--packages', required=True, type=Path)
    task.add_argument('--output', required=True, type=Path)
    task.add_argument('--collect-only', action='store_true')
    merge = sub.add_parser('aggregate')
    merge.add_argument('--reports', required=True, type=Path)
    merge.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'run':
        run(args)
    else:
        reports = [json.loads(p.read_text()) for p in args.reports.rglob('report.json')]
        summary = summarize(reports, args.output)
        raise SystemExit(0 if summary['complete'] else 1)
