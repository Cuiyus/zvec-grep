"""Unfiltered, equal-task-weight summaries for the no-subagent experiment."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics

from zg_bench.metrics.usage import validate_usage_metrics
from zg_bench.no_subagent_audit import ARMS

METRICS = ('quality', 'input_tokens', 'answer_output', 'reasoning', 'generated_total',
           'agent_wall_seconds', 'tool_calls', 'llm_calls', 'cache_read_tokens',
           'uncached_input_tokens', 'zg_calls', 'zg_used', 'native_search_calls',
           'read_calls', 'bash_calls', 'bash_search_or_read_calls', 'zg_output_chars',
           'native_output_chars')


def _mean(values):
    return statistics.fmean(values) if values and all(v is not None for v in values) else None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _delta(base, current):
    return dict(delta=None if base is None or current is None else current - base,
                change_pct=None if base in (None, 0) or current is None else 100 * (current / base - 1))


def _bootstrap(tasks, arm, metric, iterations=2000):
    """Resample paired tasks; uncertainty concerns task composition, not new runs."""
    pairs = [(t['arms']['baseline']['means'][metric], t['arms'][arm]['means'][metric]) for t in tasks]
    if len(pairs) < 2 or any(a is None or b is None for a, b in pairs):
        return None
    rng = random.Random(42)
    changes, deltas = [], []
    for _ in range(iterations):
        chosen = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        base, cur = (statistics.fmean(row[i] for row in chosen) for i in (0, 1))
        deltas.append(cur - base)
        if base:
            changes.append(100 * (cur / base - 1))
    def interval(values):
        values.sort()
        return [values[int(.025 * (len(values) - 1))], values[int(.975 * (len(values) - 1))]] if values else None
    return dict(method='paired_task_percentile_bootstrap', seed=42, draws=iterations,
                delta_95pct=interval(deltas), change_pct_95pct=interval(changes))


def summarize(reports: list, output: Path, expected_tasks: int = 20, repetitions: int = 10) -> dict:
    """Keep every trial, including zero-ZG trials; never trim or screen tasks.

    Invalid identities/protocols raise. Missing tasks, trials, or failed execution
    slots produce an explicitly partial report with no full-population claim.
    """
    if expected_tasks < 1 or repetitions < 1:
        raise ValueError('Positive expected task/repetition counts are required')
    output.mkdir(parents=True, exist_ok=True)
    issues, tasks, seen = [], [], set()
    identities, protocol, commits = None, None, set()
    for report in sorted(reports, key=lambda r: r['task_id']):
        task_id, meta = report['task_id'], report['provenance']
        if task_id in seen:
            raise ValueError(f'Duplicate task: {task_id}')
        seen.add(task_id)
        experiment = meta.get('experiment', {})
        contract = experiment.get('no_subagent', {})
        if contract.get('enabled') is not True or contract.get('mechanism') != 'permission.task=deny':
            raise ValueError(f'Missing no-subagent protocol: {task_id}')
        if meta.get('repetitions') != repetitions:
            raise ValueError(f'Repetition protocol mismatch: {task_id}')
        if report.get('model') != 'glm-5.2':
            raise ValueError('Only GLM5.2 reports are supported')
        if protocol is not None and experiment != protocol:
            raise ValueError('Mixed experiment protocols')
        protocol = experiment
        if identities is not None and meta.get('identities') != identities:
            raise ValueError('Mixed product identities')
        identities = meta.get('identities')
        commits.add(meta.get('harness_commit', 'unknown'))
        audit = meta.get('no_subagent_audit', {})
        if audit.get('complete') is not True or audit.get('task_tool_calls') != 0 or audit.get('child_sessions') != 0:
            issues.append(f'{task_id}:no_subagent_audit_not_passed')
        if audit.get('expected_slots') != repetitions * 3:
            issues.append(f'{task_id}:audit_expected_slots_mismatch')
        execution = meta.get('execution', [])
        slots = [(r.get('arm'), r.get('repetition')) for r in execution]
        expected = {(arm, rep) for arm in ARMS for rep in range(1, repetitions + 1)}
        if len(slots) != len(expected) or set(slots) != expected:
            issues.append(f'{task_id}:missing_or_duplicate_execution_slots')
        if any(r.get('returncode') != 0 for r in execution):
            issues.append(f'{task_id}:execution_failures')
        attempts = audit.get('attempts', [])
        active = {(r['arm'], r['repetition']): r for r in attempts if not r.get('retry_history')}
        task = dict(task_id=task_id, arms={}, execution_failures=sum(r.get('returncode') != 0 for r in execution),
                    physical_attempts=audit.get('physical_attempts'), retry_attempts=audit.get('retry_attempts'),
                    failed_attempts=audit.get('failed_attempts'), audit_complete=audit.get('complete') is True)
        for arm in ARMS:
            profile = report.get('profiles', {}).get(arm, {})
            trials = profile.get('trials', [])
            indexes = [r.get('trial_index') for r in trials]
            if len(indexes) != len(set(indexes)):
                raise ValueError(f'Duplicate trial: {task_id}/{arm}')
            if len(trials) != repetitions or set(indexes) != set(range(1, repetitions + 1)):
                issues.append(f'{task_id}/{arm}:incomplete_trials')
            if profile.get('trial_count') != len(trials):
                raise ValueError(f'Trial count mismatch: {task_id}/{arm}')
            values, counts = [], Counter()
            for trial in sorted(trials, key=lambda r: r['trial_index']):
                metrics = trial['metrics']
                validate_usage_metrics(metrics, integer=True)
                if metrics.get('usage_scope') != 'opencode-session-tree-v1':
                    raise ValueError('Session tree accounting is required')
                quality = trial['judge']['total']
                if not _number(quality):
                    raise ValueError('Invalid judge score')
                attempt = active.get((arm, trial['trial_index']))
                if not attempt or attempt.get('trial_name') != trial.get('trial_name'):
                    issues.append(f'{task_id}/{arm}/{trial["trial_index"]}:missing_matching_audited_attempt')
                    attempt = {}
                tool_counts, behavior = attempt.get('tool_counts'), attempt.get('behavior')
                if tool_counts is not None:
                    counts.update(tool_counts)
                    zg_calls = sum(v for k, v in tool_counts.items() if 'zvec_grep' in k)
                else:
                    zg_calls = None
                row = dict(trial_index=trial['trial_index'], trial_name=trial.get('trial_name'), quality=quality,
                           **{key: metrics[key] for key in ('input_tokens', 'agent_wall_seconds', 'tool_calls',
                              'llm_calls', 'cache_read_tokens', 'uncached_input_tokens')},
                           answer_output=metrics['text_output_tokens'], reasoning=metrics['reasoning_tokens'],
                           generated_total=metrics['output_tokens'], zg_calls=zg_calls,
                           zg_used=None if zg_calls is None else int(zg_calls > 0),
                           native_search_calls=None if tool_counts is None else sum(tool_counts.get(k, 0) for k in ('grep', 'glob')),
                           read_calls=None if tool_counts is None else tool_counts.get('read', 0),
                           bash_calls=None if tool_counts is None else tool_counts.get('bash', 0),
                           **{key: None if behavior is None else behavior.get(key) for key in
                              ('bash_search_or_read_calls', 'zg_output_chars', 'native_output_chars')})
                if attempt.get('usage') and any(attempt['usage'].get(k) != metrics[k]
                                              for k in ('input_tokens', 'output_tokens', 'tool_calls')):
                    raise ValueError(f'Audit/report usage mismatch: {task_id}/{arm}')
                values.append(row)
            task['arms'][arm] = dict(samples=len(values), means={key: _mean([v[key] for v in values]) for key in METRICS},
                                    tool_counts=dict(counts), trials=values)
        tasks.append(task)
    if len(tasks) != expected_tasks:
        issues.append('missing_or_unexpected_tasks')
    expected_ids = None
    if expected_tasks == 20:
        selection = Path(__file__).parent / 'swe_qa/data/selection.json'
        expected_ids = [row['task_id'] for row in json.loads(selection.read_text())['tasks']]
        if seen != set(expected_ids):
            issues.append('frozen_task_set_incomplete_or_changed')
    rows = []
    for arm in ARMS:
        group = [t['arms'][arm] for t in tasks]
        rows.append(dict(group=arm, tasks=len(tasks), samples=sum(g['samples'] for g in group),
                         means={key: _mean([g['means'][key] for g in group]) for key in METRICS},
                         tool_counts=dict(sum((Counter(g['tool_counts']) for g in group), Counter()))))
    comparisons = {}
    for row in rows[1:]:
        comparisons[row['group']] = {key: _delta(rows[0]['means'][key], row['means'][key]) for key in METRICS}
        for key in ('quality', 'input_tokens', 'generated_total', 'agent_wall_seconds'):
            comparisons[row['group']][key]['task_bootstrap'] = _bootstrap(tasks, row['group'], key)
    data = dict(schema_version=1, kind='glm52-no-subagent-unfiltered', complete=not issues,
                expected_tasks=expected_tasks, expected_repetitions=repetitions,
                expected_samples=expected_tasks * repetitions * 3,
                observed_samples=sum(r['samples'] for r in rows), completed_tasks=sorted(seen),
                missing_tasks=None if expected_ids is None else sorted(set(expected_ids) - seen),
                issues=sorted(set(issues)), filter_policy='none; retain all trials and tasks, including zero-ZG trials',
                aggregation='equal_weight_task_means', output_definition={
                    'answer_output': 'OpenCode raw tokens.output; excludes reasoning',
                    'reasoning': 'OpenCode raw tokens.reasoning',
                    'generated_total': 'answer_output + reasoning'},
                scope_notes=['Agent session-tree input includes cache; output includes reasoning.',
                             'Primary quality/resource means describe retained physical answers, not retry-inclusive cost.',
                             'Every retry is audited; execution wall time includes setup and retries.',
                             'Bootstrap intervals resample tasks; they do not isolate changes versus earlier run dates.'],
                experiment=protocol, identities=identities, source_harness_commits=sorted(commits),
                report_sha256=[hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest() for r in reports],
                rows=rows, comparisons=comparisons, task_statistics=tasks,
                execution=dict(failed_slots=sum(t['execution_failures'] for t in tasks),
                               physical_attempts=sum(t['physical_attempts'] or 0 for t in tasks),
                               retry_attempts=sum(t['retry_attempts'] or 0 for t in tasks),
                               failed_attempts=sum(t['failed_attempts'] or 0 for t in tasks),
                               wall_seconds_including_setup_and_retries=sum(
                                   x.get('wall_seconds_including_setup_and_retries', 0)
                                   for r in reports for x in r['provenance'].get('execution', []))))
    (output / 'comparison.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    def fmt(value):
        return '未确认' if value is None else f'{value:,.2f}'
    lines = ['# GLM5.2 禁用子 agent 对照', '',
             f"状态：{'完整' if data['complete'] else '部分结果，尚不能作为完整对照结论'}；"
             f"任务 {len(tasks)}/{expected_tasks}，样本 {data['observed_samples']}/{data['expected_samples']}。", '',
             '全部样本按任务等权汇总，不按分数、资源消耗或是否调用 ZG 筛选。', '',
             '| 组 | 样本 | 质量 | 输入 token | 回答 token | 推理 token | 生成合计 | Agent 秒 | 工具次数 | ZG 使用率 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        means = row['means']
        lines.append(f"| {row['group']} | {row['samples']} | " + ' | '.join(fmt(means[k]) for k in
                     ('quality', 'input_tokens', 'answer_output', 'reasoning', 'generated_total', 'agent_wall_seconds', 'tool_calls')) +
                     ' | ' + ('未确认' if means['zg_used'] is None else f"{means['zg_used']:.1%}") + ' |')
    lines += ['', '输入包括缓存；生成合计 = 回答 + 推理。主表为最终答案对应执行的均值；失败、重试保留于审计，'
              'setup/重试成本另见 JSON execution。任务重采样区间衡量题目组成的不确定性，不证明跨日期因果效应。', '',
              '| ZG 组相对 Baseline | 质量差 | 输入变化 | 生成变化 | 时间变化 |', '|---|---:|---:|---:|---:|']
    for arm in ('node', 'rust'):
        comp = comparisons[arm]
        lines.append(f"| {arm} | {fmt(comp['quality']['delta'])} | " + ' | '.join(
            fmt(comp[k]['change_pct']) + '%' for k in ('input_tokens', 'generated_total', 'agent_wall_seconds')) + ' |')
    if issues:
        lines += ['', '**未完成/不一致项：** ' + '; '.join(sorted(set(issues)))]
    lines += ['', '<details><summary>任务级完整均值</summary>', '',
              '| 任务 | 组 | 样本 | 质量 | 输入 token | 生成 token | Agent 秒 | 工具次数 |',
              '|---|---|---:|---:|---:|---:|---:|---:|']
    for task in tasks:
        for arm in ARMS:
            group = task['arms'][arm]
            lines.append(f"| {task['task_id']} | {arm} | {group['samples']} | " + ' | '.join(
                fmt(group['means'][k]) for k in ('quality', 'input_tokens', 'generated_total', 'agent_wall_seconds', 'tool_calls')) + ' |')
    lines += ['', '</details>', '']
    markdown = '\n'.join(lines)
    (output / 'report.md').write_text(markdown)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
            stream.write(markdown)
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-tasks', type=int, default=20)
    parser.add_argument('--repetitions', type=int, default=10)
    args = parser.parse_args()
    result = summarize([json.loads(p.read_text()) for p in sorted(args.reports.rglob('report.json'))],
                       args.output, args.expected_tasks, args.repetitions)
    raise SystemExit(0 if result['complete'] else 1)
