"""Retain 100 planned rows, then emit raw and filtered report groups."""
from __future__ import annotations
import argparse
import datetime
import html
import json
from pathlib import Path
import statistics
import cohort
import statistics_v1 as methods
import filter_diagnostics as diagnostics

METRICS = ('input_tokens', 'output_tokens', 'tool_calls', 'agent_wall_seconds', 'outer_slot_seconds')


def assemble(args):
    plan = cohort.load(args.plan); collected = {}; proofs = []
    for path in sorted(args.raw_root.rglob('collection.json')):
        item = cohort.load(path)
        if item.get('protocol_id') != cohort.PROTOCOL: continue
        if item['plan_sha256'] != cohort.sha(args.plan): raise ValueError('wrong_cohort_plan')
        key = item['pair']['key']
        if key in collected: raise ValueError('duplicate_pair_evidence:' + key)
        judged_path = path.parent / 'judgements.json'
        judged = cohort.load(judged_path) if judged_path.is_file() else {'profiles': []}
        if judged_path.is_file() and judged['collection_sha256'] != cohort.sha(path):
            raise ValueError('judged_collection_changed')
        collected[key] = (item, {r['profile']: r for r in judged['profiles']})
        proofs.append({'pair_key': key, 'collection_path': str(path), 'collection_sha256': cohort.sha(path),
            'judgements_path': str(judged_path) if judged_path.exists() else None,
            'judgements_sha256': cohort.sha(judged_path) if judged_path.exists() else None})
    records = []
    for pair in plan['schedule']:
        collection, judgments = collected.get(pair['key'], ({'profiles': []}, {}))
        for profile in cohort.PROFILES:
            native = next((r for r in collection['profiles'] if r['profile'] == profile), {})
            judged = judgments.get(profile, {}); judge = judged.get('judge') or {}
            score = judge.get('total')
            metrics = {k: (native.get('metrics') or {}).get(k) for k in METRICS}
            records.append({'task_id': pair['task_id'], 'profile': profile,
                'trial_index': pair['trial_index'], 'pair_key': pair['key'],
                'scheduled_first_profile': pair['profiles'][0],
                'judge': {'total': score, 'lower': 0 if score is None else score,
                    'upper': 100 if score is None else score},
                'score_scale': 'main SWE-QA five dimensions, total100; ten-point display=total/10',
                'metrics': metrics, 'native_execution_valid': native.get('execution_valid'),
                'execution_and_source_snapshot_valid': native.get('execution_valid'),
                'reported_metric_eligible': native.get('execution_valid'),
                'quality_pass': None, 'binary_quality_gate_defined': False,
                'source_only_semantic_tool_audit': 'separate_uncompleted_diagnostic_not_a_pass',
                'answer_sha256': native.get('answer_sha256'),
                'final_answer_available': native.get('answer') is not None,
                'native_errors': native.get('errors', ['original_pair_artifact_missing']),
                'judge_status': judged.get('status', 'unavailable_missing_evidence')})
    assert len(records) == 100
    cases = [{'task_id': task, 'profiles': {p: {'trials': sorted(
        (r for r in records if r['task_id'] == task and r['profile'] == p), key=lambda r: r['trial_index'])}
        for p in cohort.PROFILES}} for task in cohort.five.TASK_IDS]
    return {'schema_version': 1, 'protocol_id': cohort.PROTOCOL,
        'plan_sha256': cohort.sha(args.plan), 'cases': cases, 'evidence': proofs,
        'measurement_scope': 'Pinned-main opencode-session-tree-v1: recursively counted root/descendant input (uncached+cache-read+cache-write), output and tool calls. Not provider billing or all unlinked background requests. Local embeddings have no remote embedding API token total.',
        'time_scope': 'Harbor agent_execution.seconds including native recursive usage collection and separately retained source/session audit overhead. Independent outer-slot wall includes cold container, native installation/local model download/index, collection and cleanup; excludes CI queue and host source export.',
        'quality_scope': 'Same-model pinned-main GLM SWE-QA self-judge total100, not the prior original-rubric factual score or strict e2e gate.',
        'original_failures_removed': False, 'historical_trials_pooled': False}


def render(report, output):
    def fmt(v, digits=2): return '—' if v is None else f'{v:.{digits}f}'
    body = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Zvec QA GLM/local</title>',
        '<style>body{font:15px system-ui;max-width:1200px;margin:36px auto;padding:0 24px;color:#17212b}table{border-collapse:collapse;width:100%;margin:18px 0 30px}td,th{padding:10px;border-bottom:1px solid #d8dde3;text-align:right}td:first-child,th:first-child{text-align:left}small{color:#52606d}h2{margin-top:34px}.notice{padding:16px;background:#eef3f7}</style>',
        '<h1>Zvec QA · OpenCode / GLM-5.2 / local embedding</h1>',
        '<p>固定五题，每题每侧 10 个计划位置；失败和缺失保留。质量采用主干 SWE-QA 五维总分 100，表内折算为十分制；不等同于上一批原 rubric 事实分。</p>',
        '<div class="notice">' + html.escape(json.dumps(report['counts'], ensure_ascii=False)) + '</div>']
    for title, view in [('第一组：原始统计（全部 10 次）', 'raw'), ('第二组：异常值过滤后（按质量分去掉一低一高）', 'pr261_score_trimmed')]:
        body += ['<h2>' + title + '</h2><table><thead><tr><th>题目</th><th>baseline 分数 / 10</th><th>zg 分数 / 10</th><th>输入 token 减少</th><th>agent 耗时减少</th><th>冷启动耗时减少</th></tr></thead><tbody>']
        for task in (*cohort.five.TASK_IDS, 'all_tasks'):
            row = report['statistics']['estimates'][task][view]
            if row is None:
                body += ['<tr><td>' + task + '</td><td colspan="5">存在缺失分数或资源，不能按成功子集补均值或过滤。</td></tr>']; continue
            profiles = row['profiles']; comp = row['comparison']
            values = [task]
            for p in cohort.PROFILES:
                s = profiles[p].get('score_mean')
                if s is not None: values.append(fmt(s / 10))
                elif profiles[p].get('score_mean_lower') is not None:
                    values.append(fmt(profiles[p]['score_mean_lower'] / 10) + '–' + fmt(profiles[p]['score_mean_upper'] / 10))
                else: values.append('—')
            for key in ('input_reduction_pct', 'agent_time_reduction_pct', 'outer_slot_reduction_pct'):
                values.append(fmt(comp.get(key)) + '%' if comp.get(key) is not None else '—')
            body += ['<tr>' + ''.join('<td>' + html.escape(v) + '</td>' for v in values) + '</tr>']
        body += ['</tbody></table>']
    body += ['<p>正的减少率表示 zg 更少；负值表示增加。评分边界不是置信区间。点态 95% 配对 bootstrap 区间、SD/CV、逐条分数、保留/排除序号及任务 MAD 诊断见 report.json。</p>',
        '<p>两组均保留执行/计量失败的状态；过滤不修复范围偏差。任务 MAD 若触发仅作另列的相对波动诊断和 screened macro，不替代第一组全五题结果。原生源码未改检查不等于目录访问范围审计通过。</p></html>']
    (output / 'report.html').write_text('\n'.join(body))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True); p.add_argument('--raw-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    payload = assemble(args); cohort.write(args.output / 'normalized-input.json', payload)
    statistics_result = methods.analyze(payload['cases'])
    rows = [r for c in payload['cases'] for p in c['profiles'].values() for r in p['trials']]
    report = {'schema_version': 1, 'protocol_id': cohort.PROTOCOL,
        'recorded_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'plan_sha256': cohort.sha(args.plan), 'normalized_input_sha256': cohort.sha(args.output / 'normalized-input.json'),
        'counts': {'planned': 100, 'retained_pairs': len(payload['evidence']),
            'native_valid': sum(r['native_execution_valid'] is True for r in rows),
            'exact_final_answers': sum(r['final_answer_available'] for r in rows),
            'scored': sum(r['judge']['total'] is not None for r in rows)},
        'measurement_scope': payload['measurement_scope'], 'time_scope': payload['time_scope'],
        'quality_scope': payload['quality_scope'], 'statistics': statistics_result,
        'filter_variability_diagnostics': diagnostics.diagnose(payload, statistics_result),
        'two_requested_groups': ['raw', 'pr261_score_trimmed'],
        'task_MAD_screened_macro_diagnostic': statistics_result['pr261_compatibility'],
        'strict_e2e_gate': 'not_computed_by_main_completion_only_self_judging; semantic tool-scope audit remains separate',
        'historical_trials_pooled': False}
    cohort.write(args.output / 'report.json', report); render(report, args.output)
    print(json.dumps({'report': str(args.output / 'report.json'), 'counts': report['counts']}))


if __name__ == '__main__': main()
