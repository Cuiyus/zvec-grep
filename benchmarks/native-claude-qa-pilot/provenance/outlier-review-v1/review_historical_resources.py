"""Resource-only post-hoc review of retained historical cohorts and synthetic nulls."""
from pathlib import Path
import copy,datetime,hashlib,json,math,random,statistics,sys
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');H=W/'benchmarks/native-claude-qa-pilot'
sys.path.insert(0,str(H));import five_analysis_v1 as methods

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def summary(profiles):
 raw={p:methods.profile(rows) for p,rows in profiles.items()}
 return {**{k:v for k,v in methods.comparison(raw).items() if k.endswith('_reduction_pct')},
         **{k:v for k,v in methods.paired_robust(profiles).items() if k.endswith('_reduction_pct')}}

hist=[]
for task,rel in [('q001','20261020-zvec-q001-repeat-v1/analysis/analysis-input.json'),('q004','20261020-zvec-q004-repeat-v1/grading-analysis/analysis/analysis-input.json')]:
 p=H/'results'/rel;data=json.loads(p.read_text());case=data['cases'][0];assert case['task_id']==task
 profiles={k:sorted(case['profiles'][k]['trials'],key=lambda row:row['trial_index']) for k in methods.PROFILES}
 assert all(len(rows)==10 and {r['trial_index'] for r in rows}==set(range(1,11)) for rows in profiles.values())
 point=summary(profiles);draws={k:[] for k in point};rng=random.Random(20261020)
 for _ in range(10000):
  indexes=[rng.randrange(10) for _ in range(10)]
  estimates=summary({k:[rows[i] for i in indexes] for k,rows in profiles.items()})
  for key,value in estimates.items():draws[key].append(value)
 hist.append({'task_id':task,'input':str(p),'input_sha256':sha(p),'primary_original_quality_not_regraded':True,
   'all_original_resource_pairs_retained':10,'resource_effects':{k:{'point':point[k],'pointwise_interval':methods.interval(v,10000)} for k,v in draws.items()},
   'not_pooled_with_current_cohort':True,'raw_arithmetic_mean_and_robust_paired_log_estimands_differ':True})

rng=random.Random(20261020);effects=[]
for _ in range(1000):
 resources=[math.exp(rng.gauss(5,1.2)) for i in range(10)]
 def arm():
  return {'trials':[{'trial_index':i+1,'judge':{'total':rng.randrange(11)},'metrics':{'input_tokens':v,'output_tokens':1,'tool_calls':1,'agent_wall_seconds':v}} for i,v in enumerate(resources)]}
 b,z=arm(),arm()
 left=methods.legacy.profile_statistics(b)['filtered']['input_tokens'];right=methods.legacy.profile_statistics(z)['filtered']['input_tokens']
 effects.append(100*(1-right/left))
ordered=sorted(effects)
synthetic={'scope':'Controlled synthetic demonstrations only; every original baseline/zg resource pair is identical.',
 'seed':20261020,'independent_datasets':1000,'pairs_per_dataset':10,'raw_reduction_pct_every_dataset':0,
 'paired_log_reduction_pct_every_dataset':0,'separate_integer_quality_scores_each_arm':'uniform 0..10; unrelated to identical resources',
 'pr261_score_trim_reduction_pct_distribution':{'median':statistics.median(effects),'minimum':min(effects),'maximum':max(effects),
 'central_95pct_empirical_range':[ordered[25],ordered[974]],'absolute_effect_over_10pct_count':sum(abs(x)>10 for x in effects)},
 'interpretation':'Score selection adds variation to a resource comparison that had none. The selected conditional estimand differs. This does not establish inherent directional favoritism, realistic null calibration or a defect in shared-pair bootstrap draws.'}

result={'schema_version':1,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'analysis_is_post_hoc_for_historical_data':True,'current_cohort_answers_or_grades_read_by_parent':False,
 'new_solver_calls':0,'historical_resource_sensitivities':hist,'controlled_simulation':synthetic,
 'bootstrap':{'resamples':10000,'seed':20261020,'unit':'whole original pair within each distinct fixed task','pointwise_only':True},
 'no_task_screening_of_repetitions_as_tasks':True,'current_statistics_plan_changed':False,
 'method_sha256':sha(Path(__file__)),'predeclared_current_estimator_implementation_sha256':sha(H/'five_analysis_v1.py')}
out=H/'provenance/outlier-review-v1/historical-resource-sensitivity.json'
with out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
print(json.dumps({'path':str(out),'sha256':sha(out),'synthetic_effect_over_10pct_datasets':synthetic['pr261_score_trim_reduction_pct_distribution']['absolute_effect_over_10pct_count'],'historical_tasks_reviewed':[r['task_id'] for r in hist]}))
