"""Complete-five primary scores/resources and frozen robust sensitivities.

The primary retains all ten observations on all five tasks. Resampling shares
whole-pair draws within each fixed task. The PR261 view estimates separately
score-conditioned means; robust log-ratio contrasts are different estimands,
not replacements for arithmetic-mean resource savings or invalid executions.
"""
from __future__ import annotations
import math
import random
import statistics
import robust_statistics_v1 as legacy

TASKS=('q001','q002','q003','q004','q005')
PROFILES=('baseline','zvec-grep')
METRICS=('input_tokens','output_tokens','tool_calls','agent_wall_seconds','outer_slot_seconds')
COMPARISONS=('score_delta','input_reduction_pct','agent_time_reduction_pct','outer_slot_reduction_pct')

def number(v):
    try:return type(v) in (int,float) and math.isfinite(v)
    except OverflowError:return False

def mean(values):return None if not values or any(v is None for v in values) else statistics.mean(values)
def winsor(values):
    if not values or any(v is None for v in values):return None
    ordered=sorted(values)
    return statistics.mean([ordered[1],*ordered[1:-1],ordered[-2]])

def score_bounds(trial):
    judge=trial.get('judge',{});value=judge.get('total')
    if value is not None:
        if not number(value) or value!=int(value) or not 0<=value<=10:raise ValueError('original_integer_rubric_score_required')
        if judge.get('lower',value)!=value or judge.get('upper',value)!=value:raise ValueError('known_score_bounds_disagree')
        return value,value,value
    lo,hi=judge.get('lower',0),judge.get('upper',10)
    if not number(lo) or not number(hi) or not 0<=lo<=hi<=10:raise ValueError('unresolved_score_bounds_invalid')
    return None,lo,hi

def profile(rows):
    scores=[score_bounds(t) for t in rows];values=[s[0] for s in scores]
    result={'n':len(rows),'score_mean':mean(values),'score_mean_lower':mean([s[1] for s in scores]),
        'score_mean_upper':mean([s[2] for s in scores]),'known_scores':sum(v is not None for v in values),
        'score_sample_sd':statistics.stdev(values) if len(values)>1 and all(v is not None for v in values) else None,
        'score_median':statistics.median(values) if all(v is not None for v in values) else None}
    result['metric_known_counts']={key:sum(t.get('metrics',{}).get(key) is not None for t in rows) for key in METRICS}
    for key in METRICS:result[key]=mean([t.get('metrics',{}).get(key) for t in rows])
    return result

def comparison(profiles):
    b,z=[profiles[p] for p in PROFILES]
    result={'score_delta':None if b['score_mean'] is None or z['score_mean'] is None else z['score_mean']-b['score_mean']}
    if 'score_mean_lower' in b and 'score_mean_lower' in z:
        result.update(score_delta_lower=z['score_mean_lower']-b['score_mean_upper'],
            score_delta_upper=z['score_mean_upper']-b['score_mean_lower'])
    for output,key in (('input_reduction_pct','input_tokens'),('agent_time_reduction_pct','agent_wall_seconds'),('outer_slot_reduction_pct','outer_slot_seconds')):
        left,right=b.get(key),z.get(key)
        result[output]=None if left in (None,0) or right is None else 100*(1-right/left)
    return result

def paired_robust(profiles):
    pairs=list(zip(profiles['baseline'],profiles['zvec-grep']));arrays={}
    arrays['score_delta']=[None if score_bounds(b)[0] is None or score_bounds(z)[0] is None else score_bounds(z)[0]-score_bounds(b)[0] for b,z in pairs]
    for name,key in (('input','input_tokens'),('agent_time','agent_wall_seconds'),('outer_slot','outer_slot_seconds')):
        values=[]
        for b,z in pairs:
            left,right=b.get('metrics',{}).get(key),z.get('metrics',{}).get(key)
            values.append(math.log(right)-math.log(left) if left is not None and right is not None and left>0 and right>0 else None)
        arrays[name]=values
    result={}
    for key,values in arrays.items():
        for label,estimate in (('median',None if any(v is None for v in values) else statistics.median(values)),('winsorized_mean',winsor(values))):
            name=key+'_'+label+('_reduction_pct' if key!='score_delta' else '')
            result[name]=estimate if key=='score_delta' or estimate is None else 100*(1-math.exp(estimate))
            if key!='score_delta':result[key+'_'+label+'_log_ratio']=estimate
    return result

def legacy_profiles(profiles):
    if any(score_bounds(t)[0] is None or any(t.get('metrics',{}).get(k) is None for k in legacy.REQUIRED_METRICS)
        for rows in profiles.values() for t in rows):return None
    result={}
    for name,rows in profiles.items():
        statistics_row=legacy.profile_statistics({'trials':rows})['filtered']
        result[name]={**statistics_row,'score_mean':statistics_row['judge']}
    return result

def estimates(profiles):
    raw={name:profile(rows) for name,rows in profiles.items()};trimmed=legacy_profiles(profiles)
    return {'raw':{'profiles':raw,'comparison':comparison(raw)},
        'pr261_score_trimmed':None if trimmed is None else {'profiles':trimmed,'comparison':comparison(trimmed)},
        'paired_robust':paired_robust(profiles)}

def macro(estimates_by_task):
    result={}
    for view in ('raw','pr261_score_trimmed'):
        rows=[estimates_by_task[t][view] for t in TASKS]
        if any(row is None for row in rows):result[view]=None;continue
        fields=set(METRICS)|{'score_mean'}
        if view=='raw':fields|={'score_mean_lower','score_mean_upper'}
        profiles={p:{k:mean([row['profiles'][p].get(k) for row in rows]) for k in fields} for p in PROFILES}
        result[view]={'profiles':profiles,'comparison':comparison(profiles)}
    keys=estimates_by_task[TASKS[0]]['paired_robust']
    # Aggregate log-ratio contrast estimators on their log scale before the
    # percentage transformation, retaining equal weights for the fixed tasks.
    robust={}
    for key in keys:
        values=[estimates_by_task[t]['paired_robust'][key] for t in TASKS]
        if any(v is None for v in values):robust[key]=None
        elif key.startswith('score_delta') or key.endswith('_log_ratio'):robust[key]=mean(values)
        else:
            logs=[estimates_by_task[t]['paired_robust'][key.removesuffix('_reduction_pct')+'_log_ratio'] for t in TASKS]
            robust[key]=100*(1-math.exp(mean(logs)))
    result['paired_robust']=robust
    return result

def interval(values,expected):
    valid=[v for v in values if number(v)]
    if len(valid)!=expected:return {'status':'unavailable_incomplete_resamples','valid_resamples':len(valid),'lower':None,'upper':None}
    ordered=sorted(valid)
    def quantile(p):
        pos=p*(len(ordered)-1);lo=math.floor(pos);hi=math.ceil(pos)
        return ordered[lo]+(ordered[hi]-ordered[lo])*(pos-lo)
    return {'status':'available','valid_resamples':expected,'lower':quantile(.025),'upper':quantile(.975)}

def analyze(cases,*,resamples=10000,seed=20261020):
    if type(resamples) is not int or resamples<1:raise ValueError('positive_resample_count_required')
    if len(cases)!=5 or {c['task_id'] for c in cases}!=set(TASKS):raise ValueError('all_five_fixed_questions_required')
    indexed={};validity={};clean=[]
    for case in cases:
        task=case['task_id'];indexed[task]={};validity[task]={};clean_profiles={}
        if set(case['profiles'])!=set(PROFILES):raise ValueError('both_profiles_required')
        for p in PROFILES:
            rows=case['profiles'][p]['trials']
            if (len(rows)!=10 or any(type(t.get('trial_index')) is not int for t in rows)
                or {t['trial_index'] for t in rows}!=set(range(1,11))):raise ValueError('all_ten_planned_rows_required_including_unknowns')
            for t in rows:
                score_bounds(t)
                if any(v is not None and (not number(v) or v<0) for v in t.get('metrics',{}).values()):raise ValueError('invalid_resource_value')
            indexed[task][p]={t['trial_index']:t for t in rows}
            validity[task][p]={key:{'true':sum(t.get(key) is True for t in rows),'false':sum(t.get(key) is False for t in rows),'unknown':sum(t.get(key) is not True and t.get(key) is not False for t in rows)} for key in ('quality_pass','execution_and_scope_valid','reported_metric_eligible')}
            clean_profiles[p]={'trials':[{'trial_index':t['trial_index'],'judge':dict(t.get('judge',{})),'metrics':dict(t.get('metrics',{}))} for t in rows]}
        clean.append({'task_id':task,'profiles':clean_profiles})
    point={t:estimates({p:[indexed[t][p][i] for i in range(1,11)] for p in PROFILES}) for t in TASKS}
    point['all_tasks']=macro(point)
    distributions={t:{view:{key:[] for key in (COMPARISONS if view!='paired_robust' else point[t][view])} for view in ('raw','pr261_score_trimmed','paired_robust')} for t in point}
    rng=random.Random(seed)
    for _ in range(resamples):
        draw={}
        for t in TASKS:
            indexes=[rng.randrange(1,11) for _ in range(10)]
            draw[t]=estimates({p:[indexed[t][p][i] for i in indexes] for p in PROFILES})
        draw['all_tasks']=macro(draw)
        for task in draw:
            for view,measures in distributions[task].items():
                values=draw[task][view]
                if view!='paired_robust':values=None if values is None else values['comparison']
                for key in measures:measures[key].append(None if values is None else values.get(key))
    intervals={t:{view:{key:interval(values,resamples) for key,values in measures.items()} for view,measures in views.items()} for t,views in distributions.items()}
    complete_legacy=all(point[t]['pr261_score_trimmed'] is not None for t in TASKS)
    compatibility=legacy.aggregate_cases(clean) if complete_legacy else {'status':'unavailable_incomplete_original_scores_or_metrics','fixed_tasks_not_dropped':True}
    return {'schema_version':1,'primary':'all_original_ten_per_profile_on_all_five_tasks','scheduled_solver_rows':100,'score_scale':10,
        'estimates':point,'pointwise_intervals':intervals,'validity_counts':validity,'pr261_compatibility':compatibility,
        'bootstrap':{'resamples':resamples,'seed':seed,'unit':'whole original pair within each fixed task','task_resampling':False,'selection_recomputed':True},
        'macro_resource_basis':'ratio of equal-weight task/profile arithmetic means; robust contrasts aggregate their fixed-task log estimates separately',
        'limits':['Score bounds are identification bounds, not confidence intervals.','Robust paired-log contrasts and score-conditioned trimmed means estimate different quantities from raw arithmetic-mean resource ratios.','Task MAD is a separately labelled diagnostic; the primary never drops a question.','Filtering cannot repair scope, accounting or retrieval failures.','Pointwise fixed-bank intervals are not population or multiple-comparison guarantees.']}

def main():
    import argparse,hashlib,json
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('input',type=Path);parser.add_argument('output',type=Path);args=parser.parse_args()
    data=args.input.read_bytes();payload=json.loads(data)
    for key in ('measurement_scope','time_scope'):
        if not isinstance(payload.get(key),str) or not payload[key].strip():raise ValueError('explicit_scope_required:'+key)
    result={'input_sha256':hashlib.sha256(data).hexdigest(),'measurement_scope':payload['measurement_scope'],'time_scope':payload['time_scope'],**analyze(payload['cases'])}
    with args.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')

if __name__=='__main__':main()
