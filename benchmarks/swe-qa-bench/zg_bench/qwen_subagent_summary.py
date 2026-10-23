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
from zg_bench.qwen_subagent_audit import ARMS, DENIED_ARMS
from zg_bench.qwen_subagent_qualification import qualification_allows_incomplete_arm, verify_report_qualification

METRICS = ('quality', 'input_tokens', 'answer_output', 'reasoning', 'generated_total',
           'agent_wall_seconds', 'tool_calls', 'llm_calls', 'cache_read_tokens',
           'uncached_input_tokens', 'zg_calls', 'zg_used', 'native_search_calls',
           'read_calls', 'bash_calls', 'root_bash_search_or_read_calls', 'root_zg_output_chars',
           'root_native_output_chars', 'root_input_tokens', 'child_input_tokens',
           'task_calls', 'child_sessions', 'child_used',
           'root_answer_output', 'child_answer_output', 'root_reasoning', 'child_reasoning',
           'root_generated_total', 'child_generated_total', 'tree_zg_output_chars',
           'tree_native_output_chars', 'tree_zg_output_bytes', 'tree_native_output_bytes',
           'tree_bash_search_or_read_calls')


def _mean(values):
    return statistics.fmean(values) if values and all(v is not None for v in values) else None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _delta(base, current):
    return dict(delta=None if base is None or current is None else current - base,
                change_pct=None if base in (None, 0) or current is None else 100 * (current / base - 1))


def _bootstrap(tasks, arm, metric, iterations=2000, *, base_arm='baseline'):
    """Resample paired tasks; uncertainty concerns task composition, not new runs."""
    pairs = [(t['arms'][base_arm]['means'][metric], t['arms'][arm]['means'][metric]) for t in tasks]
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
        if (contract.get('enabled') is not True or contract.get('mechanism') != 'permission.task=deny'
                or contract.get('allowed_control') != 'baseline_allowed'
                or contract.get('denied_arms') != list(DENIED_ARMS)):
            raise ValueError(f'Missing no-subagent protocol: {task_id}')
        if meta.get('repetitions') != repetitions:
            raise ValueError(f'Repetition protocol mismatch: {task_id}')
        if report.get('model') != 'qwen3.8-max':
            raise ValueError('Only Qwen3.8max reports are supported')
        if protocol is not None and experiment != protocol:
            raise ValueError('Mixed experiment protocols')
        protocol = experiment
        if identities is not None and meta.get('identities') != identities:
            raise ValueError('Mixed product identities')
        identities = meta.get('identities')
        commits.add(meta.get('harness_commit', 'unknown'))
        audit = meta.get('no_subagent_audit', {})
        qualification = None
        if meta.get('final_answer_qualification') is not None:
            qualification = verify_report_qualification(report)
        if audit.get('complete') is not True and qualification is None:
            issues.append(f'{task_id}:subagent_policy_audit_not_passed')
        for arm in ARMS:
            arm_audit = audit.get('per_arm', {}).get(arm, {})
            expected_policy = 'allow' if arm == 'baseline_allowed' else 'deny'
            qualified_failure = qualification_allows_incomplete_arm(qualification, arm)
            if (arm_audit.get('complete') is not True and not qualified_failure) or arm_audit.get('task_policy') != expected_policy:
                issues.append(f'{task_id}/{arm}:permission_policy_not_verified')
            if arm in DENIED_ARMS and not qualified_failure and (arm_audit.get('task_tool_calls') != 0 or arm_audit.get('child_sessions') != 0):
                issues.append(f'{task_id}/{arm}:delegation_disable_not_verified')
        if audit.get('expected_slots') != repetitions * len(ARMS):
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
        task = dict(task_id=task_id, arms={}, per_arm_audit=audit.get('per_arm', {}), execution_failures=sum(r.get('returncode') != 0 for r in execution),
                    physical_attempts=audit.get('physical_attempts'), retry_attempts=audit.get('retry_attempts'),
                    failed_attempts=audit.get('failed_attempts'), audit_complete=audit.get('complete') is True,
                    final_answer_qualification=qualification)
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
                tree_behavior = attempt.get('tree_behavior')
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
                           root_input_tokens=metrics['session_usage']['root']['input_tokens'],
                           child_input_tokens=metrics['session_usage']['descendants']['input_tokens'],
                           **{prefix + label: metrics['session_usage'][scope][key]
                              for prefix, scope in (('root_', 'root'), ('child_', 'descendants'))
                              for label, key in (('answer_output', 'text_output_tokens'), ('reasoning', 'reasoning_tokens'),
                                                 ('generated_total', 'output_tokens'))},
                           **{'tree_' + key: None if tree_behavior is None else tree_behavior.get(key) for key in
                              ('zg_output_chars', 'native_output_chars', 'zg_output_bytes', 'native_output_bytes', 'bash_search_or_read_calls')},
                           delegations=None if tree_behavior is None else tree_behavior.get('delegations'),
                           task_calls=attempt.get('task_tool_calls'), child_sessions=attempt.get('child_sessions'),
                           child_used=None if attempt.get('child_sessions') is None else int(attempt['child_sessions'] > 0),
                           zg_used=None if zg_calls is None else int(zg_calls > 0),
                           native_search_calls=None if tool_counts is None else sum(tool_counts.get(k, 0) for k in ('grep', 'glob')),
                           read_calls=None if tool_counts is None else tool_counts.get('read', 0),
                           bash_calls=None if tool_counts is None else tool_counts.get('bash', 0),
                           **{'root_' + key: None if behavior is None else behavior.get(key) for key in
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
    row_by_arm = {row['group']: row for row in rows}
    comparison_definitions = {
        'subagent_availability': {'base': 'baseline_allowed', 'current': 'baseline',
                                 'estimand': 'disabling optional delegation; all assigned trials'},
        'rust': {'base': 'baseline', 'current': 'rust', 'estimand': 'ZG Rust with delegation disabled'},
    }
    for name, definition in comparison_definitions.items():
        base, current = definition['base'], definition['current']
        comparisons[name] = {key: _delta(row_by_arm[base]['means'][key], row_by_arm[current]['means'][key])
                             for key in METRICS}
        for key in ('quality', 'input_tokens', 'generated_total', 'agent_wall_seconds'):
            comparisons[name][key]['task_bootstrap'] = _bootstrap(tasks, current, key, base_arm=base)
    def observed_sum(values):
        return sum(values) if values and all(value is not None for value in values) else None
    per_arm_audit = {}
    for arm in ARMS:
        arm_audits = [task['per_arm_audit'].get(arm, {}) for task in tasks]
        per_arm_audit[arm] = dict(task_policy='allow' if arm == 'baseline_allowed' else 'deny',
                                  complete=bool(arm_audits) and all(a.get('complete') is True for a in arm_audits),
                                  **{key: observed_sum([a.get(key) for a in arm_audits])
                                     for key in ('physical_attempts', 'task_tool_calls', 'child_sessions')},
                                  physical_usage={key: dict(
                                      known_subtotal=sum(a.get('usage', {}).get(key, {}).get('known_subtotal', 0) for a in arm_audits),
                                      unknown_attempts=observed_sum([a.get('usage', {}).get(key, {}).get('unknown_attempts') for a in arm_audits]),
                                      total=observed_sum([a.get('usage', {}).get(key, {}).get('total') for a in arm_audits]))
                                      for key in ('input_tokens', 'output_tokens', 'text_output_tokens', 'reasoning_tokens', 'tool_calls', 'llm_calls')},
                                  wall_seconds_including_setup_and_retries=sum(
                                      row.get('wall_seconds_including_setup_and_retries', 0)
                                      for report in reports for row in report['provenance'].get('execution', []) if row.get('arm') == arm))
    qualified_tasks = [task['task_id'] for task in tasks if not task['audit_complete'] and task['final_answer_qualification'] is not None]
    limitations = sorted({text for task in tasks for text in (task['final_answer_qualification'] or {}).get('limitations', [])})
    data = dict(schema_version=1, kind='qwen38-subagent-ablation-unfiltered', complete=not issues,
                completeness_scope='complete final-answer statistics; full physical-attempt accounting is separate',
                full_attempt_audit_complete=bool(tasks) and all(task['audit_complete'] for task in tasks),
                qualified_tasks=qualified_tasks, limitations=limitations,
                failed_attempt_categories={
                    'pre_agent_install_failures': sum(len((task['final_answer_qualification'] or {}).get('pre_agent_install_failures', [])) for task in tasks),
                    'agent_timeout_incomplete_capture': sum(len((task['final_answer_qualification'] or {}).get('agent_timeout_failures', [])) for task in tasks)},
                expected_tasks=expected_tasks, expected_repetitions=repetitions,
                expected_samples=expected_tasks * repetitions * len(ARMS),
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
                             'baseline_allowed permits delegation without forcing it; comparisons use all assigned trials.',
                             'Disabling delegation changes the overall strategy; it is not merely subtracting child tokens.',
                             'Tool counts and tree_* capture the full session tree; missing tool arguments/outputs remain unknown. root_* trajectory behavior/characters cover only the root.',
                             'Delegation trigger context quotes recorded agent reasoning; it does not identify a causal trigger.',
                             'Model self-scores are not independent objective quality and are not comparable in absolute value across models.',
                             'Bootstrap intervals resample tasks; they do not isolate changes versus earlier run dates.'],
                experiment=protocol, identities=identities, source_harness_commits=sorted(commits),
                report_sha256=[hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest() for r in reports],
                rows=rows, comparisons=comparisons, comparison_definitions=comparison_definitions,
                per_arm_audit=per_arm_audit, task_statistics=tasks,
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
    lines = ['# Qwen3.8max 子 agent 可用性与 Rust ZG 三组对照', '',
             f"状态：{'最终答案统计完整' if data['complete'] else '部分结果，尚不能作为完整对照结论'}；"
             f"任务 {len(tasks)}/{expected_tasks}，样本 {data['observed_samples']}/{data['expected_samples']}。", '',
             '三组同期执行：baseline_allowed 允许自主委派；baseline、rust 禁用委派。'
             '允许不代表强迫调用，按全部分配样本比较，不筛选实际使用子 agent 的子集。', '',
             '全部样本按任务等权汇总，不按分数、资源消耗或是否调用 ZG 筛选。', '',
             '| 组 | 样本 | 模型评分 | 输入 token | 非推理输出（含工具调用） | 推理 token | 生成合计 | Agent 秒 | 工具次数 | ZG 使用率 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    if limitations:
        lines[4:4] = ['**限定：全物理执行审计仍未通过。** ' + ' '.join(limitations), '']
    for row in rows:
        means = row['means']
        lines.append(f"| {row['group']} | {row['samples']} | " + ' | '.join(fmt(means[k]) for k in
                     ('quality', 'input_tokens', 'answer_output', 'reasoning', 'generated_total', 'agent_wall_seconds', 'tool_calls')) +
                     ' | ' + ('未确认' if means['zg_used'] is None else f"{means['zg_used']:.1%}") + ' |')
    lines += ['', '输入包括缓存；生成合计 = 非推理输出（含工具调用） + 推理。主表为最终答案对应执行的均值；失败、重试保留于审计，'
              'setup/重试成本另见 JSON execution。禁用委派的影响包括主 agent 策略变化，不能等同于删除子 agent token。', '',
              '| 对比（当前 − 参照） | 模型评分点差 | 输入变化 | 生成变化 | 时间变化 |', '|---|---:|---:|---:|---:|']
    labels = {'subagent_availability': '禁用委派 baseline − 允许委派 baseline_allowed',
              'rust': 'ZG Rust − 禁用委派 baseline'}
    for name, label in labels.items():
        comp = comparisons[name]
        lines.append(f"| {label} | {fmt(comp['quality']['delta'])} | " + ' | '.join(
            fmt(comp[k]['change_pct']) + '%' for k in ('input_tokens', 'generated_total', 'agent_wall_seconds')) + ' |')
    lines += ['', '| 组 | 主会话输入 | 子会话输入 | task 调用均值 | 子会话使用率 |',
              '|---|---:|---:|---:|---:|']
    for row in rows:
        means = row['means']
        lines.append(f"| {row['group']} | " + ' | '.join(fmt(means[k]) for k in
                     ('root_input_tokens', 'child_input_tokens', 'task_calls')) + ' | ' +
                     ('未确认' if means['child_used'] is None else f"{means['child_used']:.1%}") + ' |')
    lines += ['', '工具名称计数覆盖主会话及子会话；JSON 中 root_* 检索行为与返回字符仅来自主会话轨迹。'
              'tree_* 返回大小覆盖全会话树，字段缺失记未知；委派 prompt 和原始触发上下文见 JSON。'
              '任务重采样区间衡量题目组成的不确定性，跨日期历史比较仅作描述。模型自评分不是独立客观质量，跨模型绝对值不可直接比较。']
    if issues:
        lines += ['', '**未完成/不一致项：** ' + '; '.join(sorted(set(issues)))]
    lines += ['', '<details><summary>任务级完整均值</summary>', '',
              '| 任务 | 组 | 样本 | 模型评分 | 输入 token | 生成 token | Agent 秒 | 工具次数 |',
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
