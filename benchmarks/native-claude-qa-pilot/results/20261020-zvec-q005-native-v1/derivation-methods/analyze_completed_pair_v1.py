"""Unblind a sealed q005 development pair and retain its original gates."""
import datetime, hashlib, json
from pathlib import Path

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
G=B/'native-q005-live-v1-grading'
A=B/'native-q005-live-v1-audit'
F=B/'native-q005-live-v1-final-analysis'
PRIVATE=B/'native-q005-live-v1-grading-private'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def write(p,d):
    with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')

assert not F.exists()
verified=load(G/'parent-evidence-verification.json')
assert verified['both_quality_decisions_agree'] and verified['material_unknowns']==0
for row in verified['grade_reports']:
    assert sha(Path(row['path']))==row['sha256']
assert sha(Path(verified['comparison']['path']))==verified['comparison']['sha256']
assignment=PRIVATE/'assignment.json'
assert sha(assignment)=='d2bad030dbf0bd964fe2c5052418c87a54238f3488f60a50f5941168bd10c502'
F.mkdir()
write(F/'unblinding-start.json',{'schema_version':1,'started_at_utc':now(),'reports_completed_and_compared_before_parent_mapping_read':True,'verification_sha256':sha(G/'parent-evidence-verification.json'),'assignment_sha256':sha(assignment),'method_sha256':sha(Path(__file__))})
rows=load(assignment)['rows']
write(F/'unblinding-receipt.json',{'schema_version':1,'unblinded_at_utc':now(),'assignment_path':str(assignment),'assignment_sha256':sha(assignment),'rows':rows,'parent_is_not_claimed_blind':True})
construction=load(PRIVATE/'packet-construction.json')
manifest=load(G/'packet/manifest.json')
primary=load(G/'primary-report.json');secondary=load(G/'consistency-independent-report.json')
execution=load(A/'execution/execution-review-v2.json');source=load(A/'source-scope/report.json')
joined=[]
for row in rows:
    ident=row['anonymous_id'];profile=row['profile']
    p=next(a for a in primary['answers'] if a['blinded_answer_id']==ident)
    s=next(a for a in secondary['answers'] if a['blinded_answer_id']==ident)
    m=next(a for a in manifest['answers'] if a['blinded_answer_id']==ident)
    c=next(a for a in construction['rows'] if a['anonymous_id']==ident)
    e=next(a for a in execution['profiles'] if a['profile']==profile)
    scope=next(a for a in source['profiles'] if a['profile']==profile)
    assert p['answer_sha256']==s['answer_sha256']==m['answer_sha256']==c['answer_sha256']==e['answer_sha256']==sha(Path(c['source_path']))
    assert p['success']==s['source_grounded_success'] is True
    joined.append({'task_id':'q005','profile':profile,'repetition':1,'anonymous_id':ident,'answer_sha256':m['answer_sha256'],'primary_points':p['factual_total'],'independent_points':s['total_points'],'original_quality_pass':p['success'],'strict_source_scope_valid':scope['strict_source_scope_valid'],'factual_evidence_scope_valid':scope['factual_evidence_scope_valid'],'scope_and_retrieval_gate_passed':scope['scope_and_retrieval_gate_passed'],'reported_usage':e['primary_usage'],'strict_tool_errors':e['strict_tool_errors'],'timing_seconds':e['timing_seconds'],'zg_setup_timing':e['native_zg_setup_timing']})
assert len(joined)==2 and {a['profile'] for a in joined}=={'baseline','zvec-grep'}
base=next(a for a in joined if a['profile']=='baseline');zg=next(a for a in joined if a['profile']=='zvec-grep')
def reduced(b,z):return 100*(1-z/b)
metrics={}
for key in ('input_tokens','output_tokens'):
    b=base['reported_usage'][key];z=zg['reported_usage'][key]
    metrics[key]={'baseline':b,'zvec-grep':z,'reduction_percent':reduced(b,z)}
for key in ('agent_execution','native_run','outer_slot'):
    b=base['timing_seconds'][key];z=zg['timing_seconds'][key]
    metrics[key+'_seconds']={'baseline':b,'zvec-grep':z,'reduction_percent':reduced(b,z)}
result={'schema_version':1,'analyzed_at_utc':now(),'task_id':'q005','run_id':39383511290,'run_attempt':1,'execution_head_sha':'30fe7fc4b80027cf62006bbe9d3de59445f10a16','protocol_id':'native-claude-q005-development-v1','phase':'completed_minimal_development_pair','mode':'independent_profile_jobs','retained_solver_executions':2,'executions_per_profile':1,'whole_trial_retries':0,'replacement_trials':0,'original_quality_pass':{'baseline':True,'zvec-grep':True},'original_quality_points':{'baseline':10,'zvec-grep':10},'material_grading_unknowns':0,'execution_and_metric_audits_passed':True,'strict_source_scope_valid':{'baseline':False,'zvec-grep':False},'factual_evidence_scope_valid':{'baseline':True,'zvec-grep':True},'external_content_or_answer_facts_observed':False,'required_workspace_zg_retrieval_passed':True,'original_valid_pair_candidate':False,'candidate_failure_reasons':['Both profiles enumerate directory metadata outside supplied source under the unchanged strict policy.'],'resource_metrics':metrics,'joined_original_records':joined,'filtering':{'score_tail_trim_applied':False,'reason':'Only one observation per profile; retain both original records.','task_mad_applied':False,'task_mad_reason':'One distinct task, below the frozen five-task guard.'},'confidence_intervals_available':False,'stability_established':False,'quality_gain_established':False,'billing_and_embedding_usage_known':False,'aggregate_pair_duration_invented':False,'pooled_with_serial_same_host_results':False,'question_bank_expanded':False,'question_bank_scope':'Existing accepted q001–q005 only. User stopped q006.','goal_complete':False,'limits':['A single development pair is not a ten-per-profile confirmation.','Workers ran on separate fresh hosted runners; durations are per-worker measurements.','Reported input includes uncached, cache-read and cache-write tokens, not billed cost.','Metadata-only out-of-source navigation is a strict deviation even though no external content/code/answer facts were observed.','Original tool error and reviewer nonmaterial presentation findings are retained.'],'method_sha256':sha(Path(__file__)),'verification_sha256':sha(G/'parent-evidence-verification.json')}
write(F/'analysis-report.json',result)
write(B/'native-q005-live-v1-ci/COMPLETED.json',{'schema_version':1,'recorded_at_utc':now(),'phase':'execution_source_quality_and_parent_analysis_complete','run_id':39383511290,'run_status':'completed','run_conclusion':'success','scheduled_solver_executions':2,'completed_solver_executions':2,'paid_dispatches':1,'private_mapping_read_after_reports_compared':True,'original_valid_pair_candidate':False,'final_analysis_path':str(F/'analysis-report.json'),'final_analysis_sha256':sha(F/'analysis-report.json'),'goal_complete':False})
print(json.dumps({'quality_points':result['original_quality_points'],'metrics':metrics,'original_valid_pair_candidate':False,'confidence_intervals_available':False}))
