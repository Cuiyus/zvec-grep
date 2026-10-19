from pathlib import Path
import json,hashlib,shutil,datetime
wt=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
raw=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/architecture-pilot/execution-v1')
base=wt/'benchmarks/claude-qa-pilot/results/20261019'
out=base/'architecture-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
files={
 'blinded-review.json':Path('/tmp/zg-architecture-q004-blinded-review.json'),
 'review-mapping.json':raw/'review-mapping.json',
 'judge-preparation.json':raw/'judge-preparation.json',
 'provider-audit.json':raw/'provider-audit.json',
 'independent-audit.json':Path('/tmp/zg-architecture-q004-execution-audit.json'),
 'trajectory-audit.json':Path('/tmp/zg-architecture-q004-trajectory-audit.json'),
 'trajectory-audit.py':Path('/tmp/zg-architecture-q004-trajectory-audit.py'),
 'artifact-byte-verification.json':raw/'independent-execution-audit/artifact-byte-verification.json',
 'independent-tool-id-audit.json':raw/'independent-execution-audit/independent-tool-id-audit.json',
}
for name,p in files.items():
 assert not (out/name).exists(),name
 shutil.copyfile(p,out/name)
 assert sha(p)==sha(out/name)
review=read(out/'blinded-review.json');mapping=read(out/'review-mapping.json')
reviews={r['blinded_answer_id']:r for r in review['reviews']}
checks=[];by_profile={}
for m in mapping['records']:
 r=reviews[m['blinded_answer_id']];profile=m['profile'];archived=out/f'r01-{profile}/answer.txt'
 hs={k:sha(p) for k,p in [('raw',Path(m['raw_path'])),('anonymous',Path(m['blinded_path'])),('archived',archived)]}
 assert set(hs.values())=={m['answer_sha256']}=={r['answer_sha256']}
 assert sum(x['points'] for x in r['criterion_scores'])==r['total_points']
 assert r['source_grounded_success']==(len(r['failed_gates'])==0)
 checks.append({'blinded_answer_id':m['blinded_answer_id'],'profile':profile,'sha256':hs,'declared_sha256':m['answer_sha256'],'review_sha256':r['answer_sha256'],'all_equal':True,'bytes':archived.stat().st_size})
 by_profile[profile]=r
for label,relative in [('prompt','benchmarks/zvec-issue-qa/tasks/q004/instruction.md'),('rubric','benchmarks/zvec-issue-qa/evaluator/q004.json')]:
 assert sha(wt/relative)==review['inputs'][label+'_sha256']
verification={'schema_version':1,'task_id':'q004','run_id':39297180874,'verified_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'checks':checks,'prompt_sha256':review['inputs']['prompt_sha256'],'rubric_sha256':review['inputs']['rubric_sha256'],'review_file_sha256':sha(out/'blinded-review.json'),'mapping_file_sha256':sha(out/'review-mapping.json'),'fact_totals_verified':True,'pass_flags_match_failed_gates':True,'root_source_spotcheck':{'zg_C4_error':'At diskann_builder.cc:51-55 a positive explicit build_thread_count of 1 is retained; hardware>=4 only affects the zero fallback. Thus the answer\'s explicit >=2 claim is unsupported, as judged.','grading_rules_changed':False},'scope':'Byte identity, sum and frozen-input verification; preserves independent blind judgment without post-hoc repair.'}
save(out/'review-input-verification.json',verification)
setup=read(out/'setup.json');metrics={p:read(out/f'r01-{p}/metrics.json') for p in ['baseline','zg']}
b,z=metrics['baseline'],metrics['zg']
setupseconds=sum(setup[k] for k in ['embedding_authorization_seconds','index_setup_seconds','mcp_preflight_seconds'])
verdict={'schema_version':1,'run_id':39297180874,'head_sha':'b986001e7bf658eaa0598b053d94f525e367a0cb','task_id':'zvec-q004','phase':'architecture-development','plan':{'path':'../../../experiment-architecture-v1.json','sha256':sha(wt/'benchmarks/claude-qa-pilot/experiment-architecture-v1.json')},'quality':{p:{'factual_points':r['total_points'],'maximum':10,'source_grounded_success':r['source_grounded_success'],'criterion_scores':{c['id']:c['points'] for c in r['criterion_scores']},'failed_gates':r['failed_gates'],'critical_contradictions':r['critical_contradictions']} for p,r in by_profile.items()},'execution':{'both_completed':True,'both_protocol_valid':all(m['protocol_valid'] for m in metrics.values()),'both_usage_complete':all(m['usage_complete'] for m in metrics.values()),'source_unchanged_reported':True,'zg_correct_root_successes':2,'independent_audit':'independent-audit.json','independent_audit_sha256':sha(out/'independent-audit.json')},'frozen_efficiency_conditions':{'both_source_grounded_success':False,'zg_factual_points_not_lower':True,'zg_input_including_cache_lower':False,'protocol_and_usage_valid':True,'correct_root_retrieval_succeeded':True},'efficiency_improvement':False,'primary_source_grounded_success_gain':False,'resources':{'input_tokens_including_cache':{'baseline':b['input_tokens_including_cache'],'zg':z['input_tokens_including_cache'],'change_percent':100*(z['input_tokens_including_cache']/b['input_tokens_including_cache']-1)},'agent_wall_seconds':{'baseline':b['wall_seconds'],'zg':z['wall_seconds'],'change_percent':100*(z['wall_seconds']/b['wall_seconds']-1)},'output_tokens':{'baseline':b['output_tokens'],'zg':z['output_tokens'],'change_percent':100*(z['output_tokens']/b['output_tokens']-1)},'setup_seconds':{k:setup[k] for k in ['embedding_authorization_seconds','index_setup_seconds','mcp_preflight_seconds']},'setup_seconds_sum':setupseconds,'zg_agent_plus_measured_setup_seconds':setupseconds+z['wall_seconds'],'setup_scope':'Grant, index/readiness and MCP preflight; excludes image/build/download and other orchestration, so not a full first-install measurement. Baseline uses none of this retrieval setup.'},'interpretation':'Factual points improve 7 to 9 and agent wall falls, but both answers fail the fixed source-grounded endpoint and input rises. No efficiency improvement or stable favorable suite is established.','trajectory':{'path':'trajectory-audit.json','sha256':sha(out/'trajectory-audit.json'),'tool_calls':[25,21],'tool_message_rounds':[16,11],'all_tool_return_characters':[144989,169852],'mcp_tool_calls':2,'mcp_query_strings':4,'note':'Fewer rounds/output and more early context are compatible with wall down/input up, not causal proof. Native matchedBy annotations do not isolate embedding effects.'},'limitations':['One purposively selected architecture case, baseline first, no random sampling or held-out generalization claim.','Both final answers delivered; low scores, native tool path errors and budget denial retained.','Index setup excluded from agent wall; measured setup plus zg agent exceeds baseline agent time.','No verified combined dollar costs, immutable provider weight identity or contamination-free claim.','Every older outcome and phase-specific rule is preserved; no automatic repeat or favorable-outcome replacement.']}
assert not any(r['source_grounded_success'] for r in by_profile.values())
save(out/'pair-verdict.json',verdict)
results=read(base/'results.json');oldrows=results['rows'][:];assert len(oldrows)==18
for p in ['baseline','zg']:
 m=metrics[p];r=by_profile[p]
 row={'task_id':'zvec-q004','protocol':'completion-v2','phase':'architecture-development','run_id':39297180874,'repeat':1,'profile':p,'quality_score':r['total_points'],'quality_maximum':10,'criterion_scores':{c['id']:c['points'] for c in r['criterion_scores']},'source_grounded_success':r['source_grounded_success'],'quality_status':'fresh blinded Codex source audit; factual points and core citation gates evaluated separately','quality_failed_gates':r['failed_gates'],'quality_critical_contradictions':[x['contradiction_id'] for x in r['critical_contradictions']],'protocol_valid':m['protocol_valid'],'completed':True,'usage_source':m['usage_source'],'usage_complete':m['usage_complete'],'efficiency_comparable':m['efficiency_comparable'],'input_tokens_including_cache':m['input_tokens_including_cache'],'input_tokens_excluding_cache_read':m['usage']['input_tokens']+m['usage']['cache_creation_input_tokens'],'output_tokens':m['output_tokens'],'tool_calls':m['tool_calls'],'tool_errors':m['tool_error_count'],'wall_seconds':m['wall_seconds'],'zg_tool_attempts':m['zg_tool_attempts'],'zg_tool_successes':m['zg_tool_successes'],'zg_tool_errors':m['zg_tool_errors'],'zg_tool_unresolved':m['zg_tool_unresolved'],'tool_budget_admitted':m['tool_budget']['admitted'],'tool_budget_denied':m['tool_budget']['denied'],'evidence_directory':'architecture-v1','evidence_trial_directory':f'r01-{p}','source_unchanged':True,'secret_scan_passed':True,'paired_retrieval_embedding':'qwen/qwen3.7-text-embedding','active_embedding':'qwen/qwen3.7-text-embedding' if p=='zg' else None}
 for k in ['index_setup_seconds','embedding_authorization_seconds','mcp_preflight_seconds']:row[k]=setup[k]
 results['rows'].append(row)
assert results['rows'][:18]==oldrows
results['planned_experiments'].append({'task_id':'zvec-q004','phase':'architecture-development','protocol':'completion-v2','run_id':39297180874,'head_sha':verdict['head_sha'],'plan':'../../experiment-architecture-v1.json','status':'complete and independently audited/blindly graded; both fail source-grounded quality, efficiency_improvement=false; both outcomes retained','evidence_directory':'architecture-v1'})
results['status']='All 20 planned trials retained across separately frozen phases; q004 execution and blind review complete, both answers fail its source-grounded endpoint and frozen efficiency improvement is false. Stable favorable suite benefit remains unestablished.'
results['architecture_verdict']={'path':'architecture-v1/pair-verdict.json','sha256':sha(out/'pair-verdict.json'),'primary_source_grounded_success_gain':False,'efficiency_improvement':False}
results['overall_finding']+=' Architecture q004 scores baseline 7/10 versus zg 9/10, but both fail the frozen source-grounded endpoint. zg input increases 29.91%, agent wall falls 30.32%, and its measured setup plus agent time exceeds baseline agent time. This is a factual-score/latency/input tradeoff, not passing-quality or efficiency success.'
results['interpretation_notes'].append('q004 both fail source-grounded success despite factual score 7→9 and lower zg agent wall. More input and 201.74 s of separate setup prohibit an efficiency or cold-start speedup claim. Old rules and all previous rows remain unchanged.')
save(base/'results.json',results)
# Authoritative completion evidence replaces the stale live-state summary, never restarts a run.
run=read(raw/'run-metadata.json');assert run['status']=='completed' and run['conclusion']=='success'
save(raw/'turn-status-observation.json',{'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'progress_classification':'progress','run_id':run['id'],'status':run['status'],'conclusion':run['conclusion'],'authoritative_evidence':'run-metadata.json and complete downloaded artifacts independently audited','live_run':False,'restart_performed':False,'quality_pass':{'baseline':False,'zg':False},'goal_complete':False})
print(json.dumps({'rows':len(results['rows']),'previous_rows_unchanged':results['rows'][:18]==oldrows,'verdict_sha256':sha(out/'pair-verdict.json'),'measured_setup_seconds':setupseconds,'zg_agent_plus_setup_seconds':setupseconds+z['wall_seconds']}))
