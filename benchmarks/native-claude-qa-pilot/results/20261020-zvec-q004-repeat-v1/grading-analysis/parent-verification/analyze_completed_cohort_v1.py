"""Join the complete fixed q004 cohort only after both anonymous reviews seal."""
import copy, datetime, hashlib, json, sys
from collections import Counter
from pathlib import Path

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
H=W/'benchmarks/native-claude-qa-pilot';A=B/'native-q004-repeat-v1-audit'
G=B/'native-q004-repeat-v1-grading';P=G/'q004';PRIVATE=B/'native-q004-repeat-v1-grading-private'
OUT=B/'native-q004-repeat-v1-final-analysis'
ASSIGNMENT_SHA='ee7284ebb7531c79023f45a132f37ff6b6e608337ea3321a1f7564c40ed79e47'
COMPARISON_SHA='05894da1c64121c782e05a40a91d6ff0bd8f218f3030dfc20bbd17bd00869cb2'
references={}
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):
    data=p.read_bytes();references[str(p)]={'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
    return json.loads(data)
def write(p,value):
    with p.open('x') as f:json.dump(value,f,indent=2,allow_nan=False);f.write('\n')

seal=load(G/'pre-unblinding-reports-receipt.json')
assert seal['both_independent_reports_completed_before_parent_read'] and seal['private_assignment_read_by_parent'] is False
grade_verification=load(G/'parent-grading-verification.json');assert grade_verification['both_report_records_verified'] and grade_verification['private_assignment_accessed'] is False
for record in seal['reports']:assert sha(Path(record['path']))==record['sha256']
primary=load(G/'primary-report.json');independent=load(G/'consistency-independent-report.json')
comparison=load(G/'consistency-comparison.json');assert sha(G/'consistency-comparison.json')==COMPARISON_SHA
assert comparison['coverage']['answers_compared']==20 and not comparison['coverage']['numeric_arithmetic_or_hash_errors']
assert comparison['coverage']['units']['determinate_conflicts']==0 and comparison['summary']['original_unknown_cases_retained']==4
for name,expected in comparison['original_report_hashes_after_comparison'].items():assert sha(G/name)==expected
manifest=load(P/'manifest.json');assert sha(P/'manifest.json')==seal['packet_manifest_sha256']
source=load(A/'source-cohort-evidence.json');assert source['source_reviews_verified']==10 and source['profile_rows']==20
execution=load(A/'execution-parent-verification.json');assert execution['passed'] and execution['pairs_verified']==10
resources=load(A/'raw-resource-statistics.json');assert resources['retained_solver_trials']==20
assert not OUT.exists();OUT.mkdir()
write(OUT/'unblinding-start.json',{'schema_version':1,'recorded_at_utc':now(),'assignment_read_next':True,
    'assignment_sha256':ASSIGNMENT_SHA,'packet_manifest_sha256':sha(P/'manifest.json'),
    'both_original_independent_reports_sealed_before_mapping_read':True,'comparison_sha256':COMPARISON_SHA,
    'source_cohort_evidence_sha256':sha(A/'source-cohort-evidence.json'),'parent_grading_verification_sha256':sha(G/'parent-grading-verification.json'),
    'parent_exposure':'Parent already knows labelled execution/source/resource records and has inspected the two saved reports only after their seal. Parent is not claimed blind. Anonymous graders report their own prior exposure; complete independent tool-history blindness is not certified. This is the first explicit condition assignment read for analysis.'})
assignment=load(PRIVATE/'assignment.json');assert sha(PRIVATE/'assignment.json')==ASSIGNMENT_SHA and len(assignment['rows'])==20
construction=load(PRIVATE/'q004-packet-construction.json');assert construction['packet_manifest_sha256']==sha(P/'manifest.json')
write(OUT/'unblinding-receipt.json',{'schema_version':1,'recorded_at_utc':now(),'assignment_sha256':ASSIGNMENT_SHA,'rows':20,'both_original_reports_and_comparison_completed_before_mapping_read':True,'four_material_unknowns_retained':True,'unknowns_zero_filled_or_dropped':False})
p_by={a['blinded_answer_id']:a for a in primary['answers']};i_by={a['blinded_answer_id']:a for a in independent['answers']}
expected={a['blinded_answer_id']:a for a in manifest['answers']}
scope_by={(r['profile'],r['trial_index']):r for r in source['rows']}
profiles={p:{'trials':[]} for p in ('baseline','zvec-grep')}
consensus=copy.deepcopy(profiles);joined=[]
for mapping in sorted(assignment['rows'],key=lambda r:(r['profile'],r['repetition'])):
    name,index,identifier=mapping['profile'],mapping['repetition'],mapping['anonymous_id'];key=f'q004-r{index:02d}'
    assert mapping['task_id']=='q004'
    report_path=A/'execution/pairs'/key/'execution-review.json';r=load(report_path)
    raw=next(x for x in r['profiles'] if x['profile']==name)
    p,i=p_by[identifier],i_by[identifier];s=scope_by[(name,index)]
    assert p['answer_sha256']==i['answer_sha256']==expected[identifier]['answer_sha256']==raw['answer_sha256']
    metrics={'input_tokens':raw['primary_usage']['input_tokens'],'output_tokens':raw['primary_usage']['output_tokens'],'tool_calls':raw['tool_count'],
        'agent_wall_seconds':raw['timing_seconds']['agent_execution'],'outer_slot_seconds':raw['timing_seconds']['outer_slot'],'cost_usd':None}
    trial={'trial_index':index,'trial_name':key+'-'+name,'pair_key':key,'anonymous_answer_id':identifier,'answer_sha256':p['answer_sha256'],
        'judge':{'total':p['total_points'],'maximum':10},'quality_pass':p['original_quality_pass'],
        'unit_points':{u['unit_id']:u['points'] for u in p['point_unit_facts']},'criterion_scores':p['criterion_scores'],
        'metrics':metrics,'execution_valid':r['execution_valid'],'reported_metric_eligible':raw['reported_metric_eligible'],
        'execution_and_scope_valid':r['execution_valid'] is True and s['scope_and_retrieval_gate_passed'] is True,'source_scope':s}
    profiles[name]['trials'].append(trial)
    c=copy.deepcopy(trial);iu={u['unit_id']:u['points'] for u in i['ten_unit_facts']}
    c['original_primary_total']=p['total_points'];c['original_primary_quality_pass']=p['original_quality_pass']
    c['independent_total']=i['total_points'];c['independent_quality_pass']=i['source_grounded_success']
    c['unit_points']={unit:value if value==iu[unit] else None for unit,value in c['unit_points'].items()}
    c['criterion_scores']={f'C{k}':None if any(c['unit_points'][f'C{k}.{j}'] is None for j in (1,2)) else sum(c['unit_points'][f'C{k}.{j}'] for j in (1,2)) for k in range(1,6)}
    known=[v for v in c['unit_points'].values() if v is not None];unknown=sum(v is None for v in c['unit_points'].values())
    c['judge']['total']=sum(known) if unknown==0 else None
    c['quality_pass']=p['original_quality_pass'] if p['original_quality_pass'] is i['source_grounded_success'] else None
    c['score_bounds_not_observations']=[sum(known),sum(known)+unknown]
    c['quality_bounds_not_observations']=[c['quality_pass'],c['quality_pass']] if c['quality_pass'] is not None else [False,True]
    c['unknown_status']='material_original_rule_interpretation_unresolved' if unknown else None
    consensus[name]['trials'].append(c)
    joined.append({'profile':name,'trial_index':index,'anonymous_answer_id':identifier,'answer_sha256':p['answer_sha256'],
        'original_primary_points':p['total_points'],'original_primary_pass':p['original_quality_pass'],
        'independent_points':i['total_points'],'independent_pass':i['source_grounded_success'],
        'consensus_points':c['judge']['total'],'consensus_pass':c['quality_pass'],'score_bounds_not_observations':c['score_bounds_not_observations'],
        'execution_and_scope_valid':c['execution_and_scope_valid']})
for rows in (profiles,consensus):
    for p in rows.values():assert [r['trial_index'] for r in p['trials']]==list(range(1,11))
common={'schema_version':1,'assembled_at_utc':now(),'measurement_scope':resources['measurement_scope'],'time_scope':resources['time_scope'],
    'all_original_20_rows_preserved':True,'historical_development_or_cancelled_samples_pooled':False}
diagnostic={**common,'rating_view':'diagnostic_original_primary_report_only','primary_consensus_available':False,'cases':[{'task_id':'q004','profiles':profiles}]}
main={**common,'rating_view':'primary_two_independent_reviews_agreement_with_explicit_unknowns','cases':[{'task_id':'q004','profiles':consensus}],
    'unknowns_zero_filled_or_dropped':False,'references':references}
write(OUT/'analysis-input-original-primary-diagnostic.json',diagnostic);write(OUT/'analysis-input.json',main);write(OUT/'joined-original-grades.json',{'rows':joined})
sys.path.insert(0,str(H));import architecture_repeat_analysis_v1 as frozen
report=frozen.analyze(diagnostic['cases']);report.update(rating_view=diagnostic['rating_view'],diagnostic_only=True,analysis_input_sha256=sha(OUT/'analysis-input-original-primary-diagnostic.json'))
write(OUT/'analysis-original-primary-diagnostic.json',report)
raw=report['repeated_estimates']['q004']['raw']
for key in ('input_reduction_pct','agent_time_reduction_pct','outer_slot_reduction_pct'):
    assert raw['comparison'][key]==resources['comparison'][key] and raw['intervals'][key]==resources['intervals'][key]
quality={};validity={}
for name,profile in consensus.items():
    rows=profile['trials'];passes=sum(r['quality_pass'] is True for r in rows);unknown=sum(r['quality_pass'] is None for r in rows)
    quality[name]={'known_pass':passes,'known_fail':sum(r['quality_pass'] is False for r in rows),'unknown':unknown,'possible_pass_count_not_an_observed_rate':[passes,passes+unknown],
        'score_mean_bounds_not_observed_means':[sum(r['score_bounds_not_observations'][j] for r in rows)/10 for j in (0,1)],
        'original_primary_diagnostic_passes':sum(r['original_primary_quality_pass'] for r in rows)}
    validity[name]={'strict_scope_counts':dict(Counter('true' if r['source_scope']['strict_source_scope_valid'] is True else 'false' if r['source_scope']['strict_source_scope_valid'] is False else 'unknown' for r in rows)),
        'scope_and_required_retrieval_passes':sum(r['source_scope']['scope_and_retrieval_gate_passed'] is True for r in rows)}
unknown_scores=sum(r['judge']['total'] is None for p in consensus.values() for r in p['trials'])
unknown_quality=sum(r['quality_pass'] is None for p in consensus.values() for r in p['trials'])
assert unknown_scores==unknown_quality==4
main_report={'schema_version':1,'status':'complete_execution_and_review_with_unavailable_complete_primary_quality_and_score_trim_statistics',
    'planned_solver_executions':20,'retained_solver_executions':20,'primary_unknown_scores':unknown_scores,'primary_unknown_quality_decisions':unknown_quality,
    'reason':'Four C4.2 interpretations remain unresolved after the two independent source-grounded reviews. No complete primary score-tail selection or quality comparison is invented.',
    'quality_by_profile':quality,'validity_by_profile':validity,'raw_all20_resource_comparison':resources['comparison'],'raw_all20_resource_intervals':resources['intervals'],
    'diagnostic_original_primary':{'path':str(OUT/'analysis-original-primary-diagnostic.json'),'sha256':sha(OUT/'analysis-original-primary-diagnostic.json'),
        'raw':report['repeated_estimates']['q004']['raw'],'score_trimmed':report['repeated_estimates']['q004']['score_trimmed'],
        'interval_width_change_pct':report['repeated_estimates']['q004']['interval_width_change_pct']},
    'all_execution_scope_and_reported_metrics_valid':False,'stable_reported_input_with_quality_preserved_on_q004':False,
    'task_mad':'unavailable_insufficient_tasks: one fixed task; repetitions are not distinct tasks',
    'scope':'One fixed architecture QA case and pinned native stack. Scope failures, unknowns and missing relevant retrieval remain visible independently of score filtering. No population, provider-billing or favorable valid benchmark-set claim.',
    'analysis_input_sha256':sha(OUT/'analysis-input.json'),'method_sha256':sha(Path(__file__))}
write(OUT/'analysis-report.json',main_report)
write(OUT/'method-bindings.json',{'files':{str(p):sha(p) for p in (Path(__file__),H/'architecture_repeat_analysis_v1.py',H/'repeat_analysis_v1.py',H/'robust_statistics_v1.py')},'statistical_controls_changed':False,'invalid_trials_rehabilitated_by_trimming':False})
print(json.dumps({'retained_solver_trials':20,'quality_by_profile':quality,'validity_by_profile':validity,'diagnostic_filter_interval_width_change_pct':main_report['diagnostic_original_primary']['interval_width_change_pct'],'stable_favorable_valid_benchmark':False}))
