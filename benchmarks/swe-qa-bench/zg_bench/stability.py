"""Compare two complete GLM/local rounds without running Agents or judges."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import re

from zg_bench import three_arm_e2e as runner
from zg_bench.metrics.summary import profile_statistics

REFERENCE_RUN = 38679000248
REFERENCE_HARNESS = 'ce5db0556634d3357033606c1eea0f3b27745d01'
REFERENCE_KIND = 'three-arm-glm-local-iqr'
REPEAT_KIND = 'three-arm-glm-local-trim-repeat'
BOOTSTRAP_SEED = 26120261015
ARMS = runner.ARMS
METRICS = ('judge', 'agent_wall_seconds', 'input_tokens')
VIEWS = ('raw', 'trimmed')
PROTOCOL_FIELDS = (
    'e2e_repetitions', 'models', 'embeddings', 'embedding_model',
    'embedding_dimension', 'agent_runtime', 'node_native_binding_package',
    'planned_agent_trials', 'groups', 'max_parallel_task_jobs',
    'per_task_agent_parallelism', 'judge_concurrency_per_task',
    'agent_timeout_seconds', 'mcp_timeout_seconds', 'max_harbor_retries',
    'fresh_executions',
)
LOCKED_INPUTS = (
    'zg_bench/swe_qa/data/selection.json',
    'zg_bench/swe_qa/data/references.json',
    'zg_bench/swe_qa/data/index.ignore', 'uv.lock', 'ci-config.json',
)
TASK_IMAGE_FIELDS = (
    'base_image', 'repository_commit', 'dockerfile_sha256',
    'original_task_config_sha256',
)


def _expected_tasks():
    tasks = json.loads((runner.DATA / 'selection.json').read_text())['tasks']
    if len(tasks) != 20 or len({t['task_id'] for t in tasks}) != 20:
        raise ValueError('Expected the frozen main 20-task selection')
    return tasks


def _validate_protocol(report, *, reference):
    experiment = report['provenance']['experiment']
    expected_kind = REFERENCE_KIND if reference else REPEAT_KIND
    if report.get('kind') != expected_kind or experiment.get('kind') != expected_kind:
        raise ValueError('Wrong round kind')
    if report.get('model') != 'glm-5.2' or report['provenance'].get('model') != 'glm-5.2':
        raise ValueError('Both rounds must use GLM-5.2')
    for key in PROTOCOL_FIELDS:
        if key not in experiment or experiment[key] != runner.EXPERIMENT[key]:
            raise ValueError(f'Protocol differs: {key}')
    for key in LOCKED_INPUTS:
        if experiment['locked_input_sha256'].get(key) != runner.EXPERIMENT['locked_input_sha256'][key]:
            raise ValueError(f'Locked input differs: {key}')
        actual = hashlib.sha256((runner.ROOT / key).read_bytes()).hexdigest()
        if actual != runner.EXPERIMENT['locked_input_sha256'][key]:
            raise ValueError(f'Local locked input changed: {key}')
    for arm in ('node', 'rust'):
        expected = runner.EXPERIMENT[arm]
        identity = report['provenance']['identities'][arm]
        for key, frozen_key in (('source_commit', 'source_commit'),
                                ('package_sha256', 'tarball_sha256'),
                                ('cli_sha256', 'cli_sha256')):
            if identity.get(key) != expected[frozen_key]:
                raise ValueError(f'{arm} {key} differs from pinned package')
        if experiment[arm]['source_commit'] != expected['source_commit']:
            raise ValueError(f'{arm} source commit differs')
    if experiment['node'].get('version') != '0.2.2':
        raise ValueError('Node must be 0.2.2')
    commit = report['provenance'].get('harness_commit', '')
    if reference and commit != REFERENCE_HARNESS:
        raise ValueError('Reference harness does not match run 38679000248')
    if not reference and (not re.fullmatch('[0-9a-f]{40}', commit) or commit == REFERENCE_HARNESS):
        raise ValueError('Repeat requires a new, recorded harness commit')
    if reference:
        # Validate the same raw evidence under the new runner after checking the
        # old protocol explicitly. Statistical source hashes are allowed to differ.
        normalized = copy.deepcopy(report)
        normalized['kind'] = runner.EXPERIMENT['kind']
        normalized['provenance']['experiment'] = runner.EXPERIMENT
        runner.validate_report(normalized, Path('reference/report.json'))
    else:
        runner.validate_report(report, Path('repeat/report.json'))
    if set(report['profiles']) != set(ARMS):
        raise ValueError('Exactly baseline, node and rust profiles are required')
    for arm in ARMS:
        profile = report['profiles'][arm]
        if profile['trial_count'] != 10 or len(profile['trials']) != 10:
            raise ValueError('Each task/profile must contain ten original trials')


def load_round(directory, *, reference):
    package_source = runner.EXPERIMENT.get('package_source', {})
    if (runner.EXPERIMENT.get('reference_run_id') != REFERENCE_RUN
            or package_source.get('run_id') != REFERENCE_RUN
            or package_source.get('harness_commit') != REFERENCE_HARNESS
            or package_source.get('repository') != 'Cuiyus/zvec-grep'):
        raise ValueError('Frozen experiment must identify the original fork run and harness')
    expected = {t['task_id']: t['task_slug'] for t in _expected_tasks()}
    reports, sources = {}, []
    paths = [directory] if directory.is_file() else sorted(directory.rglob('report.json'))
    for path in paths:
        report = json.loads(path.read_text())
        _validate_protocol(report, reference=reference)
        task = report['task_id']
        if task not in expected or task in reports:
            raise ValueError(f'Unknown or duplicate task: {task}')
        if report['provenance'].get('task') != expected[task]:
            raise ValueError(f'Task identity differs: {task}')
        reports[task] = report
        sources.append(dict(task_id=task, path=str(path.resolve()),
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    if set(reports) != set(expected):
        raise ValueError('Incomplete 20-task round; missing: ' + ', '.join(sorted(set(expected) - set(reports))))
    commits = {r['provenance']['harness_commit'] for r in reports.values()}
    if len(commits) != 1:
        raise ValueError('Mixed harness commits within round')
    first = next(iter(reports.values()))
    for report in reports.values():
        for field in ('experiment', 'identities'):
            if report['provenance'][field] != first['provenance'][field]:
                raise ValueError(f'Mixed {field} within round')
        if {k: v for k, v in report['judge'].items() if k != 'usage'} != {
            k: v for k, v in first['judge'].items() if k != 'usage'
        }:
            raise ValueError('Mixed judge configuration within round')
    return reports, sources


def _delta(reference, repeat):
    return {key: dict(reference=reference[key], repeat=repeat[key],
                     delta=repeat[key] - reference[key],
                     change_pct=None if reference[key] == 0 else
                     100 * (repeat[key] - reference[key]) / reference[key])
            for key in METRICS}


def _round_summary(reports):
    tasks, aggregate = {}, {}
    for task, report in reports.items():
        tasks[task] = {}
        for arm in ARMS:
            statistics = profile_statistics(report['profiles'][arm])
            if statistics['filter']['retained_count'] != 8:
                raise ValueError('Current statistics must retain 8/10 trials')
            tasks[task][arm] = dict(
                raw={key: statistics['raw'][key] for key in METRICS},
                trimmed={key: statistics['filtered'][key] for key in METRICS},
                selection=statistics['filter'])
    for arm in ARMS:
        aggregate[arm] = {
            view: {key: sum(t[arm][view][key] for t in tasks.values()) / 20 for key in METRICS}
            for view in VIEWS
        }
        aggregate[arm].update(original_count=200, retained_count=160)
    return aggregate, tasks


def _percentile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _bootstrap(rounds, repetitions):
    if repetitions < 2:
        raise ValueError('Bootstrap repetitions must be at least two')
    rng = random.Random(BOOTSTRAP_SEED)
    observations = {}
    for name, reports in rounds.items():
        observations[name] = {
            arm: [[(trial['judge']['total'], trial.get('trial_index', i + 1),
                    trial['metrics']['agent_wall_seconds'], trial['metrics']['input_tokens'])
                   for i, trial in enumerate(reports[task]['profiles'][arm]['trials'])]
                  for task in sorted(reports)] for arm in ARMS
        }
    names = ['round_change/' + arm for arm in ARMS] + [
        'rust_minus_node/reference', 'rust_minus_node/repeat', 'rust_minus_node/change']
    draws = {name: {view: {key: [] for key in METRICS} for view in VIEWS} for name in names}
    for _ in range(repetitions):
        means = {}
        for name, arms in observations.items():
            means[name] = {}
            for arm, tasks in arms.items():
                sums = {view: [0.0, 0.0, 0.0] for view in VIEWS}
                for original in tasks:
                    # Independent resampling across rounds, tasks and arms.
                    # Repeated trial indices keep original-position tie fallback.
                    sample = [original[rng.randrange(10)] for _ in range(10)]
                    ordered = sorted(enumerate(sample), key=lambda item: (item[1][0], item[1][1], item[0]))
                    retained = [row for _, row in ordered[1:-1]]
                    for view, rows in (('raw', sample), ('trimmed', retained)):
                        for metric_index, tuple_index in enumerate((0, 2, 3)):
                            sums[view][metric_index] += sum(row[tuple_index] for row in rows) / len(rows) / 20
                means[name][arm] = {view: dict(zip(METRICS, sums[view])) for view in VIEWS}
        for view in VIEWS:
            for key in METRICS:
                for arm in ARMS:
                    draws['round_change/' + arm][view][key].append(
                        means['repeat'][arm][view][key] - means['reference'][arm][view][key])
                gaps = {name: means[name]['rust'][view][key] - means[name]['node'][view][key]
                        for name in ('reference', 'repeat')}
                for name in gaps:
                    draws['rust_minus_node/' + name][view][key].append(gaps[name])
                draws['rust_minus_node/change'][view][key].append(gaps['repeat'] - gaps['reference'])
    return {name: {view: {key: [_percentile(values, .025), _percentile(values, .975)]
                          for key, values in metrics.items()} for view, metrics in views.items()}
            for name, views in draws.items()}


def compare_rounds(reference, repeat, *, bootstrap_repetitions=2000):
    if set(reference) != set(repeat) or len(reference) != 20:
        raise ValueError('Two complete matching 20-task rounds are required')
    for task in reference:
        old, new = reference[task], repeat[task]
        if old['provenance']['identities'] != new['provenance']['identities']:
            raise ValueError(f'Package or CLI identities differ: {task}')
        for key in TASK_IMAGE_FIELDS:
            old_value = old['provenance'].get('task_image', {}).get(key)
            new_value = new['provenance'].get('task_image', {}).get(key)
            if not old_value or old_value != new_value:
                raise ValueError(f'Task image protocol differs: {task}/{key}')
        old_judge = {k: v for k, v in old['judge'].items() if k != 'usage'}
        new_judge = {k: v for k, v in new['judge'].items() if k != 'usage'}
        if old_judge != new_judge:
            raise ValueError(f'Judge configuration differs: {task}')
    old, old_tasks = _round_summary(reference)
    new, new_tasks = _round_summary(repeat)
    changes = {arm: {view: _delta(old[arm][view], new[arm][view]) for view in VIEWS} for arm in ARMS}
    relative = {}
    for view in VIEWS:
        relative[view] = {}
        for name, rows in (('reference', old), ('repeat', new)):
            contrast = _delta(rows['node'][view], rows['rust'][view])
            relative[view][name] = {
                key: dict(node=value['reference'], rust=value['repeat'],
                          delta=value['delta'], change_pct=value['change_pct'])
                for key, value in contrast.items()
            }
        relative[view]['gap_change'] = {
            key: relative[view]['repeat'][key]['delta'] - relative[view]['reference'][key]['delta']
            for key in METRICS}
    return dict(
        kind='three-arm-glm-local-two-round-stability', reference_run_id=REFERENCE_RUN,
        reference_harness_commit=REFERENCE_HARNESS,
        repeat_harness_commit=next(iter(repeat.values()))['provenance']['harness_commit'],
        groups=ARMS, task_count=20, trials_per_task_profile=10,
        statistics='Drop exactly one minimum and one maximum by score/trial index; same retained trials for every metric; equal-weight 20 task means.',
        rounds=dict(reference=old, repeat=new), round_changes=changes,
        rust_minus_node=relative,
        tasks={task: {arm: dict(reference=old_tasks[task][arm], repeat=new_tasks[task][arm],
                               changes={view: _delta(old_tasks[task][arm][view], new_tasks[task][arm][view])
                                        for view in VIEWS}) for arm in ARMS} for task in sorted(reference)},
        bootstrap=dict(repetitions=bootstrap_repetitions, seed=BOOTSTRAP_SEED,
                       method='Independent within-task/profile/round resampling of all ten trials; recompute trimming in every replicate; fixed 20 tasks; 95% percentile intervals for absolute differences.',
                       limitations='Does not cover task-population uncertainty or systematic model/judge bias. An interval containing zero does not establish equivalence. No stability pass/fail threshold is imposed.',
                       intervals=_bootstrap(dict(reference=reference, repeat=repeat), bootstrap_repetitions)))


def render(data):
    def number(value):
        return 'N/A' if value is None else f'{value:,.2f}'
    def cell(value):
        return f"{number(value['reference'])} → {number(value['repeat'])} ({value['delta']:+.2f}; {number(value['change_pct'])}%)"
    lines = ['# GLM-5.2 / local embedding — repeat stability', '',
             'Each round: main 20 tasks × baseline / Node 0.2.2 / Rust 732a440 × 10 trials = 600 judgments.',
             'Both rounds are recalculated from their original ten trials using the same min/max trimming rule; eight retained per task and group. Score, Agent time and input tokens use the same retained trials. All 20 tasks receive equal weight.', '',
             '| Group | View | Score round 1 → 2 (Δ; %) | Agent seconds round 1 → 2 (Δ; %) | Input tokens round 1 → 2 (Δ; %) |',
             '|---|---|---:|---:|---:|']
    for arm in ARMS:
        for view in VIEWS:
            lines.append(f'| {runner.LABELS[arm]} | {view} | ' + ' | '.join(cell(data['round_changes'][arm][view][key]) for key in METRICS) + ' |')
    lines += ['', '## Rust minus Node', '',
              'Positive score differences favor Rust; positive resource differences mean Rust used more.', '',
              '| View | Metric | Round 1 Δ (%) | Round 2 Δ (%) | Change in Δ | 95% interval for change |',
              '|---|---|---:|---:|---:|---|']
    for view in VIEWS:
        for key in METRICS:
            item = data['rust_minus_node'][view]
            interval = data['bootstrap']['intervals']['rust_minus_node/change'][view][key]
            lines.append(f"| {view} | {key} | {item['reference'][key]['delta']:+.2f} ({number(item['reference'][key]['change_pct'])}%) | {item['repeat'][key]['delta']:+.2f} ({number(item['repeat'][key]['change_pct'])}%) | {item['gap_change'][key]:+.2f} | [{number(interval[0])}, {number(interval[1])}] |")
    lines += ['', '## Uncertainty in round-to-round changes', '',
              '| Group | View | Score Δ 95% interval | Agent seconds Δ 95% interval | Input tokens Δ 95% interval |',
              '|---|---|---|---|---|']
    for arm in ARMS:
        for view in VIEWS:
            intervals = data['bootstrap']['intervals']['round_change/' + arm][view]
            lines.append(f'| {runner.LABELS[arm]} | {view} | ' + ' | '.join(f'[{number(intervals[key][0])}, {number(intervals[key][1])}]' for key in METRICS) + ' |')
    lines += ['', f"Bootstrap: {data['bootstrap']['repetitions']} replicates, fixed seed {BOOTSTRAP_SEED}. " + data['bootstrap']['method'],
              data['bootstrap']['limitations'], '',
              '<details>', '<summary>Per-task raw and trimmed changes</summary>', '',
              '| Task | Group | View | Score round 1 → 2 (Δ; %) | Agent seconds round 1 → 2 (Δ; %) | Input tokens round 1 → 2 (Δ; %) |',
              '|---|---|---|---:|---:|---:|']
    for task, groups in data['tasks'].items():
        for arm in ARMS:
            for view in VIEWS:
                lines.append(f'| {task} | {runner.LABELS[arm]} | {view} | ' + ' | '.join(cell(groups[arm]['changes'][view][key]) for key in METRICS) + ' |')
    lines += ['', '</details>', '',
              f'Reference run: {REFERENCE_RUN}; reference harness: `{REFERENCE_HARNESS}`; repeat harness: `{data["repeat_harness_commit"]}`.', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--reference-reports', required=True, type=Path)
    parser.add_argument('--repeat-reports', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--bootstrap-repetitions', type=int, default=2000)
    args = parser.parse_args(argv)
    reference, old_sources = load_round(args.reference_reports, reference=True)
    repeat, new_sources = load_round(args.repeat_reports, reference=False)
    data = compare_rounds(reference, repeat, bootstrap_repetitions=args.bootstrap_repetitions)
    data['source_reports'] = dict(reference=old_sources, repeat=new_sources)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'stability.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    markdown = render(data)
    (args.output / 'stability.md').write_text(markdown)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
            stream.write(markdown)
    print(json.dumps(dict(tasks=20, original_trials_per_round=600, retained_per_round=480,
                          output=str(args.output.resolve()))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
