"""Extract a reviewable five-question report without changing estimators.

The original primary and the later technical-completion view stay separate.
Unknown complete-ten means are never replaced by available-answer means.
"""
from pathlib import Path
import datetime,hashlib,json
B=Path(__file__).resolve().parent.parent
A=B/'native-five-repeat-v1-analysis'
TASKS=('q001','q002','q003','q004','q005')
PROFILES=('baseline','zvec-grep')
LABELS={
 'q001':'WAL 与 optimize 的持久化语义（issue #594）',
 'q002':'全文检索空过滤条件（issue #583）',
 'q003':'删除、purge 与 ID/向量行号映射（issue #724）',
 'q004':'optimize 线程池与 PQ 容量检查（issue #758）',
 'q005':'UTF-8 标识符、批处理校验与重复项（issue #395）',
}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def ref(p):return {'path':str(p),'sha256':sha(p)}
def counts(rows,key):
 return {'true':sum(r.get(key) is True for r in rows),
  'false':sum(r.get(key) is False for r in rows),
  'unknown':sum(r.get(key) is not True and r.get(key) is not False for r in rows)}
def build(name):
 ip=A/(name+'-input.json');ap=A/(name+'-statistics.json');dp=A/(name+'-filter-diagnostics.json')
 data,analysis,diagnostic=load(ip),load(ap),load(dp)
 assert analysis['input_sha256']==diagnostic['input_sha256']==sha(ip)
 assert diagnostic['analysis_sha256']==sha(ap)
 cases={c['task_id']:c for c in data['cases']};assert set(cases)==set(TASKS)
 allrows=[];questions=[]
 for task in TASKS:
  estimate=analysis['estimates'][task];intervals=analysis['pointwise_intervals'][task];profiles={}
  for profile in PROFILES:
   rows=cases[task]['profiles'][profile]['trials'];allrows+=rows
   assert len(rows)==10 and {r['trial_index'] for r in rows}==set(range(1,11))
   p=estimate['raw']['profiles'][profile]
   profiles[profile]={
    'planned_rows':10,'native_attempted_rows':sum(r['native_execution_status']=='exited' for r in rows),
    'retained_claude_session_trees':sum(r['retained_claude_session_tree_available'] is True for r in rows),
    'final_answers':sum(r['final_answer_available'] for r in rows),
    'known_exact_scores':p['known_scores'],'mean_score':p['score_mean'],
    'mean_score_identification_bounds':[p['score_mean_lower'],p['score_mean_upper']],
    'score_sample_sd':p['score_sample_sd'],'score_median':p['score_median'],
    'raw_resource_means':{k:p[k] for k in ('input_tokens','output_tokens','tool_calls','agent_wall_seconds','outer_slot_seconds')},
    'metric_known_counts':p['metric_known_counts'],
    'gates':{key:counts(rows,key) for key in ('quality_pass','native_execution_valid',
     'strict_source_scope_valid','factual_evidence_scope_valid','source_unchanged',
     'scope_and_retrieval_gate_passed','reported_metric_eligible','end_to_end_valid_success')},
    'criterion_disagreement_rows':sum(bool(r['quality_independent_disagreement_criteria']) for r in rows),
    'quality_gate_disagreement_rows':sum(r['quality_gate_disagreement'] for r in rows),
    'native_failures_or_absences':[{'trial_index':r['trial_index'],
     'execution_wave':r['execution_wave'],'status':r['native_execution_status'],
     'final_answer_available':r['final_answer_available'],
     'strict_usage_available':r['strict_usage_available'],
     'strict_accounting_errors':r['strict_accounting_errors']} for r in rows
     if r['native_execution_valid'] is not True],
   }
  questions.append({'task_id':task,'description':LABELS[task],'profiles':profiles,
   'raw_comparison':estimate['raw']['comparison'],'raw_pointwise_95pct_intervals':intervals['raw'],
   'pr261_score_conditioned_comparison':None if estimate['pr261_score_trimmed'] is None else estimate['pr261_score_trimmed']['comparison'],
   'pr261_pointwise_95pct_intervals':intervals['pr261_score_trimmed'],
   'all_pair_robust_contrasts':estimate['paired_robust'],
   'robust_pointwise_95pct_intervals':intervals['paired_robust'],
   'filter_diagnostic':diagnostic['per_task_selection_and_order'][task],
   'interval_width_comparison':diagnostic['pointwise_interval_widths'][task]})
 assert len(allrows)==100
 return {'view':data['view'],'inputs':{'normalized':ref(ip),'statistics':ref(ap),'filter_diagnostics':ref(dp)},
  'counts':{'planned_rows':100,'native_attempted_rows':sum(r['native_execution_status']=='exited' for r in allrows),
   'retained_claude_session_trees':sum(r['retained_claude_session_tree_available'] is True for r in allrows),
   'final_answers':sum(r['final_answer_available'] for r in allrows),
   'known_exact_scores':sum(r['judge']['total'] is not None for r in allrows),
   'end_to_end_valid_success':counts(allrows,'end_to_end_valid_success')},
  'questions':questions,'equal_task_weight_macro':analysis['estimates']['all_tasks'],
  'macro_pointwise_95pct_intervals':analysis['pointwise_intervals']['all_tasks'],
  'bootstrap':analysis['bootstrap'],'measurement_scope':analysis['measurement_scope'],
  'time_scope':analysis['time_scope'],'limitations':analysis['limits']}
def main():
 out=A/'phase-result-summary-zh.json'
 result={'schema_version':1,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'scope':'五道已接受题目，Claude Code + Qwen3.8-max，每题 baseline/zg 各 10 个计划位置。题库未扩充；历史试验未合并。',
  'original_run':{'id':39392884840,'url':'https://github.com/Cuiyus/zvec-grep/actions/runs/39392884840'},
  'technical_completion_run':{'id':39401348683,'url':'https://github.com/Cuiyus/zvec-grep/actions/runs/39401348683'},
  'stack':{'rust_source_commit':'ae1ac57d489542508e016bba5d868bc38c3c095a',
   'original_campaign_commit':'df0d37796435a596b4f6d62215a8fe28f401774a',
   'completion_campaign_commit':'1bcd2b80df42de344fed9ff67e9f5c326fb19c24'},
  'quality_scale':{'maximum':10,'unit':'原 rubric 整数分；与完成率、关键项/引用 gate 分开',
   'bounds':'分数边界保留未知和评审分歧；不是置信区间。缺失答案未填零，未拿成功子集代替完整十次均值。'},
  'separate_views':{'original_primary':build('original-primary'),'technical_completion':build('infrastructure-completion')},
  'completion_limit':'补测只填原始五组启动前失败的位置。原始执行中失败保留；补测晚一波运行且五组均沿原顺序 baseline 先执行，因此不能视为新增的随机可交换样本或完整原始主分析。',
  'filter_interpretation':['原始全十次结果为主，质量两端裁剪只作敏感性分析。',
   '中位数、配对 log-ratio Winsorized 均值与原始算术均值估计不同的量。',
   '过滤后 CV/SD 或区间宽度不一定降低；样本选择后降低也不能单独证明模型随机性降低。',
   '质量分同分时按固定序号删首尾，不意味着首尾是资源离群值。',
   '未解决的分数边界会阻止需要精确质量排序的裁剪；不得任意选边界使结果更有利。',
   '执行、源码范围、检索和计量失败不会通过过滤修复。'],
  'unblinding_admission':ref(A/'unblinding-admission.json'),
  'normalization_receipt':ref(A/'normalization-receipt.json'),'method_sha256':sha(Path(__file__))}
 with out.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
 print(json.dumps({'summary':str(out),'sha256':sha(out),'separate_views':2,'historical_pooling':False}))
if __name__=='__main__':main()
