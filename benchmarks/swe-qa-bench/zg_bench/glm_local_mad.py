"""Frozen three-arm reports and a common six-group cross-round MAD comparison."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import statistics

from zg_bench import three_arm_e2e as runner
from zg_bench.metrics.summary import profile_statistics
from zg_bench.metrics.task_screen import screen_tasks

METRICS = ('judge', 'agent_wall_seconds', 'input_tokens', 'tool_calls')
PREVIOUS_COMMIT = '614384b03b1793d452de1815643e851589bf232c'
PREVIOUS_RUN = 38700270964
LOCKED = ('zg_bench/swe_qa/data/selection.json', 'zg_bench/swe_qa/data/references.json',
          'zg_bench/swe_qa/data/index.ignore', 'ci-config.json', 'uv.lock')
PROTOCOL = ('e2e_repetitions', 'models', 'embeddings', 'embedding_model',
            'embedding_dimension', 'agent_runtime', 'node_native_binding_package',
            'planned_agent_trials', 'groups', 'max_parallel_task_jobs',
            'per_task_agent_parallelism', 'judge_concurrency_per_task',
            'agent_timeout_seconds', 'mcp_timeout_seconds', 'max_harbor_retries')


def expected_tasks():
    return [t['task_id'] for t in json.loads((runner.DATA / 'selection.json').read_text())['tasks']]


def statistics_for(reports):
    return {r['task_id']: {a: profile_statistics(r['profiles'][a]) for a in runner.ARMS}
            for r in reports}


def means(tasks, profiles, name, view):
    return {m: statistics.fmean(profiles[t][name][view][m] for t in tasks) if tasks else None
            for m in METRICS}


def stage_rows(profiles, screen):
    rows = []
    tasks = list(profiles)
    for arm in runner.ARMS:
        counts = screen['profiles'][arm]
        rows.append(dict(group=arm, label=runner.LABELS[arm], tasks=len(tasks),
                         original_count=counts['original_count'],
                         after_trim_count=counts['after_trim_count'],
                         retained_count=counts['final_count'],
                         excluded_count=counts['original_count'] - counts['final_count'],
                         raw=means(tasks, profiles, arm, 'raw'),
                         trimmed=means(tasks, profiles, arm, 'filtered'),
                         filtered=means(screen['retained_task_ids'], profiles, arm, 'filtered')))
    return rows


def number(value):
    return 'N/A' if value is None else f'{value:,.2f}'


def screen_lines(screen):
    lines = ['', f"Common tasks: {screen['retained_task_count']}/{screen['original_task_count']}; "
             f"status: {screen['status']}.", '',
             '| Excluded task | Source group | Metric | Dispersion | Modified Z | Upper threshold |',
             '|---|---|---|---:|---:|---:|']
    for task in screen['excluded_task_ids']:
        for reason in screen['task_decisions'][task]['reasons']:
            lines.append(f"| {task} | {reason['source_profile']} | {reason['metric']} | "
                         f"{reason['dispersion']:.6f} | {reason['modified_z']:.6f} | {reason['upper_threshold']:.6f} |")
    if not screen['excluded_task_ids']:
        lines += ['', 'No tasks excluded.']
    lines += ['', 'References: ' + json.dumps(screen['references'], sort_keys=True), '',
              'PR #261 at 17d84c4: trim exactly one minimum and maximum score per task/group; '
              'all metrics use the same retained tuples. Score sample SD; time/input CV; '
              'maximum over supplied groups; one-pass upper modified Z > 3.5; exclude '
              'the whole task from every group. No refit or replacement executions. '
              'Task means are equally weighted. Raw evidence, failures and all Judge calls remain archived.']
    return lines


def summarize(reports, output):
    expected = expected_tasks()
    by_task = {}
    for report in reports:
        runner.validate_report(report, output)
        task = report['task_id']
        if task not in expected or task in by_task:
            raise ValueError('Unknown or duplicate task')
        by_task[task] = report
    if not reports:
        raise ValueError('No completed reports')
    if any(r['provenance']['identities'] != reports[0]['provenance']['identities'] for r in reports):
        raise ValueError('Mixed package identities')
    ordered = [by_task[t] for t in expected if t in by_task]
    profiles = statistics_for(ordered)
    screen = screen_tasks([dict(task_id=t, profiles=p) for t, p in profiles.items()])
    missing = [t for t in expected if t not in by_task]
    data = dict(kind=runner.EXPERIMENT['kind'], complete=not missing, expected_tasks=expected,
                completed_tasks=list(profiles), missing_tasks=missing,
                judged_trials=sum(r['judge']['usage']['calls'] for r in reports),
                experiment=runner.EXPERIMENT, rows=stage_rows(profiles, screen),
                task_filter=screen, task_statistics=profiles,
                source_harness_commits=sorted({r['provenance']['harness_commit'] for r in reports}))
    runner.versions.write_json(output / 'comparison.json', data)
    rounds = sorted({r['provenance'].get('round_index', 1) for r in reports})
    if len(rounds) != 1:
        raise ValueError('A single-round summary cannot mix rounds')
    data['round_index'] = rounds[0]
    runner.versions.write_json(output / 'comparison.json', data)
    lines = [f'# Qwen3.8-Max / local embedding / Round {rounds[0]}', '',
             f"Completed tasks: {len(profiles)}/20; judged trials: {data['judged_trials']}/600.", '',
             'Raw statistics: all completed judged trials, equally weighted task means. '
             'No sample trimming or task exclusion is applied to the table below. '
             'Full trim/MAD diagnostics remain in comparison.json.', '',
             '| Group | Mean Judge score | Input tokens / execution | Tool calls / execution | Agent time (s) / execution |',
             '|---|---:|---:|---:|---:|']
    for row in data['rows']:
        lines.append(f"| {row['label']} | " + ' | '.join(number(row['raw'][m])
                     for m in ('judge', 'input_tokens', 'tool_calls', 'agent_wall_seconds')) + ' |')
    lines += ['', 'Baseline is native OpenCode without zg. Node is @zvec/zvec-grep@0.2.2; '
              'Rust is frozen upstream main 732a440. OpenCode 1.18.4; local/potion-code-16m-v2 '
              '256 dimensions; 20 main tasks x 10 executions x 3 groups. '
              'Eight parallel task jobs; one Agent per task; three Judge workers per task. '
              'Per-execution time excludes container cleanup. Input includes the complete '
              'OpenCode session tree and cache reads, excluding Judge usage.', '',
              '| Task | Group | Mean Judge score | Input tokens / execution | Tool calls / execution | Agent time (s) / execution |',
              '|---|---|---:|---:|---:|---:|']
    for task, groups in profiles.items():
        for arm, row in groups.items():
            lines.append(f"| {task} | {arm} | " + ' | '.join(number(row['raw'][m])
                         for m in ('judge', 'input_tokens', 'tool_calls', 'agent_wall_seconds')) + ' |')
    markdown = '\n'.join(lines) + '\n'
    (output / 'report.md').write_text(markdown)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
            stream.write(markdown)
    return data


def load_round(directory, *, previous):
    reports = []
    for path in sorted(directory.rglob('report.json')):
        report = json.loads(path.read_text())
        experiment = report['provenance']['experiment']
        for key in PROTOCOL:
            if experiment[key] != runner.EXPERIMENT[key]:
                raise ValueError(f'Execution protocol changed: {key}')
        for key in LOCKED:
            if experiment['locked_input_sha256'][key] != runner.EXPERIMENT['locked_input_sha256'][key]:
                raise ValueError(f'Frozen input changed: {key}')
        for arm in ('node', 'rust'):
            for key in ('source_commit', 'tarball_sha256', 'cli_sha256'):
                if experiment[arm][key] != runner.EXPERIMENT[arm][key]:
                    raise ValueError('Product changed')
            identity = report['provenance']['identities'][arm]
            for key, expected_key in [('source_commit','source_commit'),
                                      ('package_sha256','tarball_sha256'), ('cli_sha256','cli_sha256')]:
                if identity[key] != runner.EXPERIMENT[arm][expected_key]:
                    raise ValueError('Actual package/binary changed')
        if previous and report['provenance']['harness_commit'] != PREVIOUS_COMMIT:
            raise ValueError('Not the immediately previous round')
        normalized = copy.deepcopy(report)
        if previous:
            normalized['provenance']['experiment'] = runner.EXPERIMENT
        runner.validate_report(normalized, path)
        reports.append(report)
    if len(reports) != 20 or {r['task_id'] for r in reports} != set(expected_tasks()):
        raise ValueError('Two complete matching 20-task rounds are required')
    if len({r['provenance']['harness_commit'] for r in reports}) != 1:
        raise ValueError('Mixed harness commits')
    return {r['task_id']: r for r in reports}


def compare(reference, repeat, output):
    tasks = expected_tasks()
    groups = {}
    for task in tasks:
        old, new = reference[task], repeat[task]
        if old['provenance']['identities'] != new['provenance']['identities']:
            raise ValueError('Different product identities')
        for key in ('base_image', 'repository_commit', 'dockerfile_sha256', 'original_task_config_sha256'):
            if old['provenance']['task_image'][key] != new['provenance']['task_image'][key]:
                raise ValueError('Task image inputs differ')
        if {k:v for k,v in old['judge'].items() if k!='usage'} != {k:v for k,v in new['judge'].items() if k!='usage'}:
            raise ValueError('Judge parameters differ')
        groups[task] = {f'{name}:{arm}': profile_statistics(reports[task]['profiles'][arm])
                        for name, reports in [('previous',reference),('current',repeat)] for arm in runner.ARMS}
    screen = screen_tasks([dict(task_id=t, profiles=groups[t]) for t in tasks])
    rounds = {name: {} for name in ('previous','current')}
    changes = {}
    for arm in runner.ARMS:
        changes[arm] = {}
        for name in rounds:
            rounds[name][arm] = {
                stage: means(screen['retained_task_ids'] if stage=='filtered' else tasks,
                             groups, f'{name}:{arm}', 'raw' if stage=='raw' else 'filtered')
                for stage in ('raw','trimmed','filtered')}
        for stage in ('raw','trimmed','filtered'):
            changes[arm][stage] = {}
            for m in METRICS:
                old, new = rounds['previous'][arm][stage][m], rounds['current'][arm][stage][m]
                changes[arm][stage][m] = dict(previous=old,current=new,
                    delta=None if old is None or new is None else new-old,
                    change_pct=None if old in (None,0) or new is None else 100*(new/old-1))
    data = dict(kind='glm-local-init-previous-vs-current-mad', reference_run_id=PREVIOUS_RUN,
                reference_harness_commit=PREVIOUS_COMMIT,
                repeat_harness_commit=next(iter(repeat.values()))['provenance']['harness_commit'],
                task_filter=screen, rounds=rounds, round_changes=changes,
                source_reports={name:[dict(task_id=t,sha256=hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest())
                                      for t,r in reports.items()] for name,reports in [('previous',reference),('current',repeat)]})
    runner.versions.write_json(output / 'stability.json', data)
    lines = ['# Previous round vs PR #261 + #274 E2E repeat', '',
             'One common six-group MAD task mask; all groups retain the same tasks. '
             'Execution and Judge parameters are frozen; only container init changed.', '',
             '| Group | Stage | Score previous → current | Agent seconds previous → current | Input previous → current |',
             '|---|---|---:|---:|---:|']
    for arm in runner.ARMS:
        for stage in ('raw','trimmed','filtered'):
            cells=[]
            for m in METRICS:
                row=changes[arm][stage][m]
                delta='N/A' if row['delta'] is None else f"{row['delta']:+.2f}"
                pct='N/A' if row['change_pct'] is None else f"{row['change_pct']:+.2f}%"
                cells.append(f"{number(row['previous'])} → {number(row['current'])} ({delta}; {pct})")
            lines.append(f'| {arm} | {stage} | '+' | '.join(cells)+' |')
    lines += screen_lines(screen)
    markdown='\n'.join(lines)+'\n'
    (output/'stability.md').write_text(markdown)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
            stream.write(markdown)
    return data


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--reference-reports',required=True,type=Path)
    parser.add_argument('--repeat-reports',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    compare(load_round(args.reference_reports,previous=True),
            load_round(args.repeat_reports,previous=False),args.output)


if __name__=='__main__':
    main()
