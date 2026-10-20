"""Presentation only: display frozen estimates, intervals and selection records."""
from pathlib import Path
import argparse
import csv
import html
import json

TASKS = ('q001', 'q002', 'q003', 'q004', 'q005', 'all_tasks')
PROFILES = ('baseline', 'zvec-grep')


def fmt(value, digits=2):
    return '—' if value is None else f'{value:,.{digits}f}'


def interval(report, task, view, metric, scale=1):
    row = report['statistics']['pointwise_intervals'][task][view][metric]
    if row['status'] != 'available':
        return '区间不可用'
    return f"[{fmt(row['lower'] / scale)}, {fmt(row['upper'] / scale)}]"


def score(profile):
    if profile.get('score_mean') is not None:
        return fmt(profile['score_mean'] / 10)
    if profile.get('score_mean_lower') is not None:
        return f"{fmt(profile['score_mean_lower'] / 10)}–{fmt(profile['score_mean_upper'] / 10)}"
    return '—'


def table(headers, rows):
    return '<table><thead><tr>' + ''.join('<th>' + html.escape(v) + '</th>' for v in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + html.escape(str(v)) + '</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table>'


def render(report, output):
    body = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            '<title>Zvec QA：GLM-5.2 / local</title>',
            '<style>body{font:15px/1.55 system-ui;max-width:1400px;margin:32px auto;padding:0 24px;color:#17212b}h1{font-size:28px}h2{margin-top:36px;font-size:21px}table{border-collapse:collapse;width:100%;margin:16px 0 28px;font-variant-numeric:tabular-nums}th,td{border-bottom:1px solid #d6dee5;padding:10px;text-align:right;vertical-align:top}th:first-child,td:first-child{text-align:left}th{background:#edf2f6}small{color:#52606d}.scroll{overflow-x:auto}.notice{padding:16px;background:#edf2f6;border-left:4px solid #396d97}</style>',
            '<h1>Zvec QA · OpenCode / GLM-5.2 / local embedding</h1>',
            '<p>固定主干 ae1ac57；OpenCode 1.18.4；custom-openai/glm-5.2；local/potion-code-16m-v2。现有五题，每题 baseline 10 次、zg 10 次，共 100 个计划位置。没有历史样本混入或成功子集补均值。</p>',
            '<div class="notice">' + html.escape('；'.join(f'{k}: {v}' for k, v in report['counts'].items())) + '</div>',
            '<p>质量为主干 SWE-QA 五维 100 分，以下折算为 10 分：正确性、完整性、相关性、清晰度、连贯性各占 20 分。使用同模型自评；与上一轮原 rubric 事实分及严格 e2e gate 口径不同。正的资源减少率表示 zg 更少；负值表示增加。</p>']
    if report.get('analysis_view')=='posthoc_infrastructure_completion':
        body.append('<div class="notice">本页是单列的基础设施补全视图：原始计划99次完成、1次在agent启动前DNS失败，原始失败记录保持不变。仅为这个未启动位置补了首次执行，使每侧实际10次、共100次agent执行。质量全部在agent测试结束后按同一主干方法评分，未采用原始并发评分的成功子集。q004-r09冷启动指标累计33.73秒失败准备耗时与补全执行耗时，排除两次尝试间的等待；该逻辑测试对跨主机和批次，计划的先后顺序与实际agent顺序不同。置信区间依计划索引重采样，按补全敏感性结果解读。原始100计划位视图另行保留。</div>')
    elif report.get('quality_evaluation_wave'):
        body.append('<div class="notice">两组结果均采用全部原始 agent 测试结束后的统一评分：主干默认 3 并发，每个可用答案最多 3 次主干评分尝试。原始并发评分出现的限流缺失及全部评分日志另行保留。该统一评分覆盖所有可用答案，不依据初始分数或初始评分成功与否挑选；agent 仍是原始同批 100 个执行位置。</div>')
    macro=report['statistics']['estimates']['all_tasks']['raw']
    if macro and all(macro['comparison'].get(k) is not None for k in ('input_reduction_pct','agent_time_reduction_pct','outer_slot_reduction_pct')):
        def direction(key):
            value=macro['comparison'][key]
            return ('减少' if value>=0 else '增加')+fmt(abs(value))+'%'
        widths=report['filter_variability_diagnostics']['pointwise_interval_widths']
        resource_keys=('input_reduction_pct','agent_time_reduction_pct','outer_slot_reduction_pct')
        wider=sum(v[k]['trimmed_vs_raw_width_change_pct'] is not None and v[k]['trimmed_vs_raw_width_change_pct']>0 for v in widths.values() for k in resource_keys)
        body.append('<p><strong>本轮未观察到整体稳定收益。</strong>原始总体输入 token '+direction('input_reduction_pct')+'，agent 耗时 '+direction('agent_time_reduction_pct')+'，累计冷启动执行耗时 '+direction('outer_slot_reduction_pct')+'。整体三项资源的95%区间均跨0；过滤后 '+str(wider)+' 个资源指标区间变宽。过滤结果应作为敏感性分析，不能据此认定随机性下降。</p>')
    csv_rows = []
    for title, view in [('第一组：原始统计（每侧全部 10 个计划位置）', 'raw'),
                        ('第二组：异常值过滤后（每侧按质量分去掉一低一高）', 'pr261_score_trimmed')]:
        body += ['<h2>' + title + '</h2>']
        rows = []
        for task in TASKS:
            estimate = report['statistics']['estimates'][task][view]
            if estimate is None:
                rows.append([task, '不可用：存在缺失分数或资源', '—', '—', '—', '—', '—'])
                continue
            profiles = estimate['profiles']; comparison = estimate['comparison']
            row = [task, score(profiles['baseline']), score(profiles['zvec-grep'])]
            delta = comparison['score_delta']
            row.append(fmt(None if delta is None else delta / 10) + ' / ' + interval(report, task, view, 'score_delta', 10))
            for metric in ('input_reduction_pct', 'agent_time_reduction_pct', 'outer_slot_reduction_pct'):
                value = comparison.get(metric)
                row.append(('—' if value is None else fmt(value) + '%') + ' / ' + interval(report, task, view, metric))
            rows.append(row)
            for profile in PROFILES:
                record = {'group': view, 'task': task, 'profile': profile,
                          'score_10': None if profiles[profile].get('score_mean') is None else profiles[profile]['score_mean'] / 10}
                record.update({key: profiles[profile].get(key) for key in ('input_tokens','output_tokens','tool_calls','agent_wall_seconds','outer_slot_seconds')})
                csv_rows.append(record)
        body += ['<div class="scroll">', table(['题目','baseline 质量 / 10','zg 质量 / 10','质量差 / 95% 区间','输入 token 减少 / 95% 区间','agent 耗时减少 / 95% 区间','累计冷启动执行耗时减少 / 95% 区间'], rows), '</div>']
        rows = []
        for task in TASKS:
            estimate = report['statistics']['estimates'][task][view]
            if estimate is None:
                continue
            for profile in PROFILES:
                p = estimate['profiles'][profile]
                rows.append([task, profile, fmt(p.get('input_tokens'),0), fmt(p.get('output_tokens'),0),
                             fmt(p.get('tool_calls'),1), fmt(p.get('agent_wall_seconds')), fmt(p.get('outer_slot_seconds'))])
        body += [table(['题目','配置','平均输入 token','平均输出 token','平均工具调用','平均 agent 秒数','平均冷启动秒数'], rows)]
    body += ['<h2>过滤、波动与保留记录</h2>',
             '<p>同分按原始重复序号稳定排序；每侧独立选取，资源指标沿用同一批保留执行。该过滤估计的是按分数条件筛选后的均值。样本 SD/CV 变小不能单独证明随机性下降。95% 区间来自题内完整配对重采样 10,000 次，每次重新选择过滤样本；它不代表全部 QA 问题总体。</p>']
    rows = []
    diagnostic = report['filter_variability_diagnostics']
    for task in TASKS[:-1]:
        item = diagnostic['per_task_selection_and_order'][task]
        widths = diagnostic['pointwise_interval_widths'][task]
        for profile in PROFILES:
            p = item['profiles'][profile]; filt = p['original_score_filter']
            retained = filt.get('retained_trial_indexes')
            excluded = '不可用' if retained is None else ', '.join(f'r{i:02d}' for i in range(1,11) if i not in retained)
            variability = p['resource_variability']['input_tokens']
            before = variability['raw']['cv']; after = (variability['score_conditioned_subset'] or {}).get('cv')
            rows.append([task,profile,excluded,fmt(before,3)+' → '+fmt(after,3),
                         fmt(widths['input_reduction_pct']['trimmed_vs_raw_width_change_pct'])+'%',
                         ', '.join(f'r{i:02d}' for i in p['all_original_invalid_or_unknown_rows_remain_visible']) or '无'])
    body += [table(['题目','配置','过滤排除序号','输入 token CV：原始 → 过滤','输入减少率区间宽度变化','原始执行/计量无效或未知序号'], rows),
             '<p>区间宽度变化为负表示变窄，为正表示变宽。任务 MAD 仅作为 report.json 中单列的诊断，不替代以上固定五题统计。过滤不能修复执行失败、错误计量或目录访问范围偏差。</p>',
             '<p>agent 时间使用主干指标：Harbor agent_execution 减去原生会话计量采集耗时，仍含另行记录的源码/会话完整性审计开销。冷启动时间包含容器启动、安装、本地模型下载、索引和清理，不含 CI 排队。token 覆盖选定根会话及其后代，包含缓存读取；不是供应商账单。源码未改检查不等于严格目录访问范围审计通过。</p>',
             '<p><a href="report.json">完整统计与逐项区间</a> · <a href="normalized-input.json">全部 100 个计划位置</a> · <a href="means.csv">两组均值 CSV</a></p></html>']
    (output / 'summary-zh.html').write_text('\n'.join(body))
    if csv_rows:
        with (output / 'means.csv').open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0]), lineterminator='\n'); writer.writeheader(); writer.writerows(csv_rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report_directory', type=Path)
    args = parser.parse_args()
    render(json.loads((args.report_directory / 'report.json').read_text()), args.report_directory)
