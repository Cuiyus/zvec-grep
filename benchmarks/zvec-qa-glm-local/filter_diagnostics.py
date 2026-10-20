"""Explain selection, order balance, CI widths and fixed-task influence.

Consumes normalized completed analysis inputs/results only. It never modifies
observations, statistical estimates, validity gates or the accepted task bank.
"""
from pathlib import Path
import argparse,hashlib,json,statistics,sys
import statistics_v1 as methods

def variability(values):
 known=sum(v is not None for v in values)
 if known!=len(values):return {'n':len(values),'known':known,'mean':None,'sample_sd':None,'cv':None,'median':None,'minimum':None,'maximum':None}
 center=statistics.mean(values);sd=statistics.stdev(values) if len(values)>1 else None
 return {'n':len(values),'known':known,'mean':center,'sample_sd':sd,'cv':None if center==0 or sd is None else sd/center,'median':statistics.median(values),'minimum':min(values),'maximum':max(values)}
def average(values):return None if any(v is None for v in values) else statistics.mean(values)
def effect(profiles):
 out={}
 for name,key in [('input_reduction_pct','input_tokens'),('agent_time_reduction_pct','agent_wall_seconds'),('outer_slot_reduction_pct','outer_slot_seconds')]:
  b,z=[profiles[p][key] for p in methods.PROFILES];out[name]=None if b in (None,0) or z is None else 100*(1-z/b)
 return out

def diagnose(payload,report):
 tasks={case['task_id']:case for case in payload['cases']}
 if set(tasks)!=set(methods.TASKS):raise ValueError('all_five_original_questions_required')
 per_task={}
 for task in methods.TASKS:
  case=tasks[task];retained={};items={}
  for p in methods.PROFILES:
   rows=case['profiles'][p]['trials'];ordered=sorted(rows,key=lambda r:r['trial_index'])
   known=all(methods.score_bounds(r)[0] is not None for r in rows)
   if known:
    filt=methods.legacy.profile_statistics({'trials':rows})['filter'];indexes=filt['retained_trial_indexes'];retained[p]=set(indexes)
   else:filt={'status':'unavailable_unresolved_original_score'};indexes=None
   position=methods.TASKS.index(task)
   first=lambda i:((position+i)%2==1 and p=='baseline') or ((position+i)%2==0 and p=='zvec-grep')
   items[p]={'raw_count':len(rows),'quality_unknown_count':sum(methods.score_bounds(r)[0] is None for r in rows),
    'raw_first_profile_count':sum(first(r['trial_index']) for r in rows),'raw_second_profile_count':sum(not first(r['trial_index']) for r in rows),
    'resource_variability':{metric:{'raw':variability([r.get('metrics',{}).get(metric) for r in rows]),'score_conditioned_subset':None if indexes is None else variability([r.get('metrics',{}).get(metric) for r in ordered if r['trial_index'] in indexes])} for metric in methods.METRICS},
    'original_score_filter':filt,'filtered_first_profile_count':None if indexes is None else sum(first(i) for i in indexes),
    'filtered_second_profile_count':None if indexes is None else sum(not first(i) for i in indexes),
    'all_original_invalid_or_unknown_rows_remain_visible':[r['trial_index'] for r in ordered if r.get('execution_and_source_snapshot_valid') is not True or r.get('reported_metric_eligible') is not True]}
  joint=None
  if set(retained)==set(methods.PROFILES):
   b,z=[retained[p] for p in methods.PROFILES]
   joint={'shared_original_pair_indexes':sorted(b&z),'baseline_only_indexes':sorted(b-z),'zg_only_indexes':sorted(z-b),
    'interpretation':'Separate score-conditioned means; asymmetric retention is not a new equally retained paired sample.'}
  per_task[task]={'profiles':items,'selected_pair_overlap':joint}
 intervals={}
 for task,views in report['pointwise_intervals'].items():
  intervals[task]={}
  for metric in methods.COMPARISONS:
   values={}
   for view in ('raw','pr261_score_trimmed'):
    row=views[view][metric];values[view]=None if row['status']!='available' else row['upper']-row['lower']
   b,z=values['raw'],values['pr261_score_trimmed']
   values['trimmed_vs_raw_width_change_pct']=None if b in (None,0) or z is None else 100*(z/b-1)
   intervals[task][metric]=values
 influence=[]
 for omitted in methods.TASKS:
  names=[t for t in methods.TASKS if t!=omitted]
  profiles={p:{k:average([report['estimates'][t]['raw']['profiles'][p].get(k) for t in names]) for k in methods.METRICS} for p in methods.PROFILES}
  influence.append({'omitted_task_for_diagnostic_only':omitted,'remaining_task_count':4,'raw_resource_comparison':effect(profiles)})
 return {'schema_version':1,'scope':'Fixed-bank diagnostic only. No omitted task or selected trial is removed from the primary result.',
  'per_task_selection_and_order':per_task,'pointwise_interval_widths':intervals,'leave_one_task_out_resource_influence':influence,
  'diagnostic_interpretation':['Subset CV/SD describes a different score-conditioned sample; a smaller subset variance is not proof of lower stochastic noise. Incomplete original metrics remain null, without a complete-case submean.',
    'Interval narrowing is not presumed or required. Median/Winsorized paired-log contrasts estimate different quantities from raw mean resource ratios.',
    'Any original scope, accounting or execution failure still blocks the original favorable validity gate, including when its tuple is trimmed.',
    'Leave-one-task-out values assess the five accepted questions; they are not a new task selection or population interval.',
    'The original fifty pairs are all retained; no previous campaign or supplementary wave is pooled.']}

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('input',type=Path);parser.add_argument('analysis',type=Path);parser.add_argument('output',type=Path);args=parser.parse_args()
 data=args.input.read_bytes();payload=json.loads(data);analysis=json.loads(args.analysis.read_text())
 if analysis['input_sha256']!=hashlib.sha256(data).hexdigest():raise ValueError('analysis_input_identity_mismatch')
 result={'input_sha256':hashlib.sha256(data).hexdigest(),'analysis_sha256':hashlib.sha256(args.analysis.read_bytes()).hexdigest(),'method_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),**diagnose(payload,analysis)}
 with args.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
if __name__=='__main__':main()
