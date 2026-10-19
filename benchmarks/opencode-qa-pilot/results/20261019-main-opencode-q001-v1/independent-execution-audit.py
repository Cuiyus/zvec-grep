#!/usr/bin/env python3
"""One-off, offline analysis of retained failed run 39308807224; no execution or grading."""
from __future__ import annotations
import collections, datetime, hashlib, importlib.util, json, re, sys, tarfile
from pathlib import Path
sys.dont_write_bytecode=True
BASE=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
LIVE=BASE/'live-v1'; RAW=LIVE/'raw'; RUN=RAW/'qa-run'; REPORTS=RAW/'qa-reports'
REPO=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
MAIN=BASE/'upstream-main/benchmarks/swe-qa-bench'
OUT=Path('/tmp/zg-opencode-live-execution-audit.json')
def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def hb(b): return hashlib.sha256(b).hexdigest()
def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':')).encode()
def req(v,s):
 if not v: raise ValueError(s)
def rows(s,k): return [json.loads(x['data']) if isinstance(x['data'],str) else x['data'] for x in s if x['kind']==k]
def sec(x): return (datetime.datetime.fromisoformat(x['finished_at'].replace('Z','+00:00'))-datetime.datetime.fromisoformat(x['started_at'].replace('Z','+00:00'))).total_seconds() if x else None
def ref(p): return {'path':str(p),'sha256':sha(p),'bytes':p.stat().st_size}
req(not OUT.exists(),'refuse overwrite')
prepath=Path('/tmp/zg-opencode-live-execution-audit-precheck.json'); pre=load(prepath)
req(sha(prepath)=='754b6c815261e1c2cfb943eae081f74731234f97d78678eaf145e84f8b9e3671','precheck changed')
for rel,v in pre['raw_inventory'].items(): req(sha(RAW/rel)==v['sha256'] and (RAW/rel).stat().st_size==v['bytes'],'raw changed '+rel)
plan=load(REPORTS/'experiment.json'); execution=load(RUN/'execution.json'); collection=load(REPORTS/'collection/collection.json')
req(sha(REPORTS/'experiment.json')==pre['plan_sha256'],'plan changed')
req(sha(REPO/'benchmarks/opencode-qa-pilot/experiment.json')==pre['plan_sha256'],'repo plan changed')
B=RUN/'runs/q001-baseline/zvec-q001__AWjMY7j'; Z=RUN/'runs/q001-zvec-grep/zvec-q001__XWvyzhA'
source_audit=load(B/'qa-source-audit.json'); source_summary={}
for when in ('before','after'):
 x=source_audit[when]; observed=x['observed']; source=[{k:r[k] for k in ('path','bytes','sha256')} for r in observed['files']]
 req(x['valid'] and not x['errors'] and not x['additions'],'source snapshot invalid')
 req(all(r['kind']=='file' for r in observed['files']),'nonfile source entry')
 source.sort(key=lambda r:Path(r['path'])); req(len(source)==plan['source_file_count'],'source count')
 req(hb(canon(source))==plan['source_inventory_sha256'],'source inventory')
 req(observed['embedding_environment_names']==[],'embedding environment present')
 req(all(observed[k] is False for k in ('zg_executable_present','zg_home_present','mcp_config_present')),'baseline isolation')
 source_summary[when]={'valid':True,'source_count':len(source),'inventory_sha256':hb(canon(source)),'additions':[],
  'embedding_environment_names':[],'zg_executable_present':False,'zg_home_present':False,'mcp_config_present':False,'runtime':observed['runtime']}
req(source_audit['errors']==['session_export:JSONDecodeError'] and not source_audit['session_exports_complete'],'unexpected source audit failure')
host=load(REPORTS/'opencode-runtime.json')
for when in ('before','after'):
 runtime=source_summary[when]['runtime']['opencode'];req(runtime['version']==plan['agent']['version'],'runtime version')
 for k in ('binary_sha256','entrypoint_sha256'): req(runtime[k]==host[k],'runtime differs from host mock '+k)
req(source_summary['before']['runtime']['opencode']==source_summary['after']['runtime']['opencode'],'runtime drift')
snapshot=load(B/'agent/opencode-session-snapshot.json'); usage=load(B/'agent/session-usage.json')
sys.path.insert(0,str(MAIN))
from zg_bench.agents.opencode_usage import summarize_session_usage
from zg_bench.metrics.usage import validate_session_usage
from zg_bench.swe_qa.collect import _profile_result, _trajectory_metrics
validate_session_usage(usage,require_identity=True)
u2=summarize_session_usage(snapshot,usage['root_session_id'],capture_complete=True)
for k in ('root_session_id','sessions','root','descendants','total','complete','errors'):req(usage[k]==u2[k],'usage DB mismatch '+k)
sessions=rows(snapshot,'session'); messages=rows(snapshot,'message'); dbtools=rows(snapshot,'tool')
req(len(sessions)==1 and sessions[0]['parent_id'] is None and sessions[0]['id']==usage['root_session_id'],'session tree')
req(len(messages)==50 and all(x['finished'] and not x['has_error'] and x['tokens_present'] for x in messages),'message completion')
req(all((x['provider_id'],x['model_id'])==('custom-openai','qwen3.8-max') for x in messages),'message model')
req(all(x['child_session_id'] is None for x in dbtools),'unexpected child')
stream=B/'agent/opencode.txt'; events=[json.loads(l) for l in stream.read_text().splitlines() if l.strip()]
req(all(e['sessionID']==usage['root_session_id'] for e in events),'stream root')
turn=None; turns=[]
for e in events:
 k=e['type']; p=e['part']
 if k=='step_start':
  req(turn is None,'nested turn');turn={'message_id':p['messageID'],'texts':[],'tools':[]}
 else:
  req(turn is not None and turn['message_id']==p['messageID'],'part outside turn')
  if k=='text':turn['texts'].append(p['text'])
  elif k=='tool_use':turn['tools'].append(p)
  elif k=='step_finish':turn['reason']=p['reason'];turns.append(turn);turn=None
  else:req(k=='reasoning','unexpected stream type')
req(turn is None and len(turns)==50 and events[-1]['type']=='step_finish','incomplete terminal stream')
last=turns[-1];answer='\n'.join(last['texts']).strip();req(last['reason']=='stop' and not last['tools'] and answer,'not final answer')
req({t['message_id'] for t in turns}=={m['id'] for m in messages},'message ID mismatch')
final_db=[m for m in messages if m['id']==last['message_id']];req(len(final_db)==1 and final_db[0]['finished'] and not final_db[0]['has_error'],'final DB incomplete')
trajectory=load(B/'agent/trajectory.json');req(trajectory['session_id']==usage['root_session_id'],'ATIF root')
req(_trajectory_metrics(trajectory)['answer']==answer,'ATIF final mismatch')
req(collection['profiles'][0]['metrics']['answer']==answer,'collector answer mismatch')
question=(REPO/'benchmarks/zvec-issue-qa/tasks/q001/instruction.md').read_text().strip()
usersteps=[s for s in trajectory['steps'] if s['source']=='user']; req(len(usersteps)==1 and usersteps[0]['message'].strip()==question,'prompt differs')
req(hb((REPO/'benchmarks/zvec-issue-qa/tasks/q001/instruction.md').read_bytes())==plan['question_sha256'],'prompt hash')
tool_events=[(i,e['part']) for i,e in enumerate(events,1) if e['type']=='tool_use']
req(len(tool_events)==77 and {p['id'] for _,p in tool_events}=={p['id'] for p in dbtools},'tool IDs incomplete')
dbbyid={p['id']:p for p in dbtools}; tools=[]; url_flags=[]; read_verified=0
source_root=BASE/'prepared-v2/tasks/zvec-q001/environment/source'
for i,p in tool_events:
 st=p['state'];d=dbbyid[p['id']];req((p['callID'],p['messageID'],p['tool'],st['status'])==(d['call_id'],d['message_id'],d['name'],d['status']),'tool mismatch')
 req(st['status']=='completed' and p['tool'] in ('bash','read'),'unexpected tool/status')
 inp=st['input'];out=st.get('output',''); serialized=json.dumps(inp)+out
 leak_markers=['reference-answer','pair-verdict','source-manifest.json','qa-prepared','qa-reports','qa-run','/logs/','/root/','experiment.json','OPENAI_API_KEY','QWEN_EMBEDDING_API_KEY']
 req(not any(x.lower() in serialized.lower() for x in leak_markers),'sensitive/evaluator marker in tool evidence')
 if p['tool']=='read':
  fp=inp['filePath'];req(fp.startswith('/workspace/') and '..' not in Path(fp).parts,'outside source read')
  display=st['metadata']['display']; local=source_root/fp.removeprefix('/workspace/')
  req(local.is_file(),'read file not in frozen source'); req(sha(local)==next(r['sha256'] for r in source if r['path']==fp.removeprefix('/workspace/')),'local source differs')
  segment='\n'.join(local.read_text().splitlines()[display['lineStart']-1:display['lineEnd']])
  req(segment.strip()==display['text'].strip(),'read output differs from source');read_verified+=1
 else:
  cmd=inp['command'];req(cmd.startswith('cd /workspace && ') or cmd.startswith('ls -la /workspace && cat /workspace/README*'),'unexpected shell cwd')
  req(not re.search(r'\b(curl|wget|python|node|npm|ssh|scp|rm|touch|mkdir|sed\s+-i|git\s+(clone|fetch|pull|checkout|reset))\b',cmd),'non-read command')
  req(st['metadata'].get('exit')==0,'nonzero bash exit')
 flags=[x for x in ('https://','http://') if x in out]
 if flags:url_flags.append({'event_line':i,'tool':p['tool'],'input':inp,'urls_in_local_source_text':True})
 tools.append({'event_line':i,'part_id':p['id'],'call_id':p['callID'],'message_id':p['messageID'],'tool':p['tool'],'status':st['status'],
  'input':inp,'input_sha256':hb(canon(inp)),'output_sha256':hb(out.encode()),'output_bytes':len(out.encode()),'output_characters':len(out),'metadata_truncated':st.get('metadata',{}).get('truncated')})
result=load(B/'result.json'); metrics=_profile_result(profile='baseline',job_dir=B.parent,trial_dir=B,result=result,trial_index=1);metrics.pop('answer',None)
req(metrics['session_usage']['total']==usage['total'],'native result usage mismatch')
zresult=load(Z/'result.json');zsetup=load(Z/'agent/zvec-grep-setup.json')
req(all(zresult[k] is None for k in ('agent_execution','agent_result','verifier','verifier_result')),'unexpected treatment execution')
req(zresult['exception_info']['exception_type']=='RuntimeError' and 'ZG_BIN_PATH' in zresult['exception_info']['exception_message'],'unexpected treatment failure')
slots=[];mount_reports=[]
for p,s in zip(['baseline','zvec-grep'],execution['slots']):
 req(s['profile']==p and s['status']=='exited' and s['returncode']==0 and s['container_cleanup']=={'complete':True,'remaining_running':[],'stopped':[]},'slot or cleanup')
 req(s['finished_at_unix']-s['started_at_unix']<plan['slot_timeout_sec'],'slot timeout')
 job=RUN/'runs'/('q001-'+p); req(len([x for x in job.iterdir() if x.is_dir()])==1,'extra trial')
 slots.append({'profile':p,'started_at_unix':s['started_at_unix'],'finished_at_unix':s['finished_at_unix'],'wall_seconds':s['finished_at_unix']-s['started_at_unix'],'process_exit':s['returncode'],'cleanup':s['container_cleanup'],'trials_retained':1})
 containers=load(RUN/(p+'-containers.json'));req(len(containers)==1,'container count')
 for c in containers.values():
  for m in c['mounts']:
   dest=m['Destination'];src=Path(m['Source']);trial=Path(c['trial_dir'])
   if dest=='/root/.nvm':req(m['Type']=='volume' and ('-'+p+'-') in m['Name'],'cache isolation')
   elif dest=='/tmp/zg-bench-zvec-grep.tgz':req(p=='zvec-grep' and not m['RW'] and src.name=='zvec-grep-'+plan['candidate_sha256'][:16]+'.tgz','candidate mount')
   elif dest.startswith('/logs/'):req(trial in src.parents,'logs mount not trial')
   else:raise ValueError('unexpected mount '+dest)
  mount_reports.append({'profile':p,'container_id':c['id'],'image':c['image'],'mount_destinations':[m['Destination'] for m in c['mounts']],'no_evaluator_or_gold_mount_observed':True})
req(slots[0]['finished_at_unix']<=slots[1]['started_at_unix'],'overlapping slots')
req(mount_reports[0]['image']==mount_reports[1]['image'],'different images')
base_diagnosis=Path('/tmp/zg-opencode-baseline-export-diagnosis.json'); bd=load(base_diagnosis)
req(sha(base_diagnosis)=='36baf4fdd3bf4cedf5238b56fbaede10191bf46315e3af17991d02c7106b2a4a','diagnosis changed')
for x in bd['source_evidence']:req(sha(Path(x['path']))==x['sha256'],'diagnostic source changed')
for x in bd['fixture_probe']:
 stdout=Path(x['stdout_path']);stderr=Path(x['stderr_path']);req(sha(stdout)==x['stdout_sha256'] and sha(stderr)==x['stderr_sha256'],'fixture changed')
 json.loads(stdout.read_text())
 try:json.loads(stderr.read_text()+stdout.read_text());raise AssertionError('merged parsed')
 except json.JSONDecodeError:pass
jobs=load(LIVE/'job-metadata.json')['jobs']; gates=[{k:s[k] for k in ('number','name','conclusion','started_at','completed_at')} for s in jobs[0]['steps'] if 6<=s['number']<=19]
report={'schema_version':1,'kind':'independent_failed_live_execution_audit','created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'run_id':pre['run_id'],'head':pre['head'],'repository':'Cuiyus/zvec-grep','run_attempt':1,'plan_sha256':pre['plan_sha256'],'phase':plan['phase'],'protocol_id':plan['protocol_id'],
 'conclusion':{'protocol_valid':False,'complete_valid_pair':False,'failure_classification':'two harness integration failures; no evidence of model/provider response failure',
  'baseline':'Native QA reached a terminal answer, but required full session export was not retained after JSONDecodeError.',
  'zvec-grep':'Agent setup failed resolving the installed zg executable before QA, embedding grant/index and MCP readiness.',
  'comparative_quality_evaluable':False,'comparative_efficiency_evaluable':False,'benefit_claim_supported':False,'quality_evaluated_by_this_audit':False,
  'baseline_diagnostic_grading_eligible':True,'baseline_diagnostic_grading_scope':'Only the retained terminal answer, clearly labeled diagnostic from an invalid pair; no candidate/pass/recovery of frozen endpoint.',
  'treatment_diagnostic_answer_grading_eligible':False,'required_disposition':'Close this phase with both failed/partial scheduled slots retained; no retry, repair, replacement or benefit claim.'},
 'artifact_integrity':pre['zip'],'precheck_report':ref(prepath),'script':ref(Path(__file__)),
 'input_files':pre['input_files'],'raw_inventory':pre['raw_inventory'],'frozen_identities':{k:plan[k] for k in ('upstream_main','source_commit','source_file_count','source_inventory_sha256','dataset_sha256','question_sha256','rubric_sha256','candidate_sha256','candidate','main_file_sha256','frozen_harness_sha256')},
 'workflow_gate_receipts':gates,'execution_slots':slots,'mounts':mount_reports,
 'baseline':{'trial':B.name,'protocol_valid':False,'structural_verifier_reward':result['verifier_result']['rewards'],'reward_is_quality_grade':False,
  'source_and_isolation_before_after':source_summary,'source_audit_failure':source_audit['errors'],'full_session_exports_retained':0,
  'native_usage_recomputed_from_persisted_rows':True,'usage':usage,'snapshot_rows':dict(collections.Counter(x['kind'] for x in snapshot)),
  'stream_event_types':dict(collections.Counter(e['type'] for e in events)),'terminal_answer':{'sha256':hb(answer.encode()),'bytes_without_trailing_newline':len(answer.encode()),'root_session_id':usage['root_session_id'],'final_message_id':last['message_id'],
   'stream_equals_atif_equals_collector_bytes':True,'db_confirms_same_message_completed_without_error':True,'db_contains_final_text':False,'native_export_agreement_verifiable':False,'finish_reason':'stop','final_turn_tool_free':True},
  'prompt':{'frozen_question_sha256':plan['question_sha256'],'atif_user_prompt_matches':True,'native_export_user_prompt_unavailable':True},
  'native_metrics':metrics,'timing':{'trial_wall_seconds':sec({'started_at':result['started_at'],'finished_at':result['finished_at']}),'environment_setup_seconds':sec(result['environment_setup']),'agent_setup_seconds':sec(result['agent_setup']),'harbor_agent_execution_seconds':sec(result['agent_execution']),'native_agent_wall_seconds_after_usage_subtraction':metrics['agent_wall_seconds'],'usage_collection_seconds':usage['collection_wall_seconds'],'source_audit_seconds':source_audit['audit_wall_seconds'],'export_attempt_seconds':source_audit['export_wall_seconds'],'verifier_seconds':sec(result['verifier']),
   'scope':'Native reported agent wall subtracts usage collection only; source auditing/export overhead remains in Harbor execution and is reported separately. No cold or comparative speed claim.'},
  'tool_audit':{'tool_count':len(tools),'names':dict(collections.Counter(x['tool'] for x in tools)),'terminal_statuses':dict(collections.Counter(x['status'] for x in tools)),
   'stream_and_db_tool_ids_call_ids_message_ids_match':True,'read_display_segments_verified_against_frozen_source':read_verified,'all_bash_inputs_reviewed':'Read-only listing/search/content inspection under /workspace; initial git inspection only reports absence of .git. No write, network, external source, evaluator or zg command observed.',
   'no_web_calls_observed':True,'source_only_observed':True,'no_evaluator_path_access_observed':True,'url_flags':url_flags,'actual_successful_zg_retrieval':False,'tools':tools},
  'failure_diagnosis':{'evidence':ref(base_diagnosis),'proven':'Required export parsing raised JSONDecodeError; source/isolation snapshots themselves passed.',
   'mechanism':'Wrapper parses environment.exec stdout as pure JSON. Harbor Docker merges stderr into stdout. All 12 retained native fixtures emit an Exporting session line on stderr; merging those existing bytes independently reproduced JSONDecodeError.',
   'confidence':'Concrete integration defect strongly consistent with live failure; exact live export stdout/stderr was not retained, so exact payload and any additional causes remain unproven.'}},
 'zvec-grep':{'trial':Z.name,'protocol_valid':False,'exception':zresult['exception_info'],'setup':zsetup,
  'agent_execution':None,'agent_result':None,'verifier':None,'usage':None,'answer':None,'full_session_exports_retained':0,'source_before_after':None,'correct_root_successful_retrieval':False,
  'timing':{'trial_wall_seconds':sec({'started_at':zresult['started_at'],'finished_at':zresult['finished_at']}),'environment_setup_seconds':sec(zresult['environment_setup']),'agent_setup_seconds':sec(zresult['agent_setup'])},
  'failure_diagnosis':{'proven_stage':'Harbor _prepare -> _setup_agent -> ZvecGrepMixin.setup -> _install_zvec_grep -> _parse_install_path(ZG_BIN_PATH)',
   'mechanism':'Frozen candidate is a platform package containing package/bin/zg but no package.json bin/directories.bin/install script. Native harness npm install -g plus command -v zg requires a global CLI entry. This is a structural package/installer contract mismatch.',
   'not_model_provider_failure':'No QA agent_execution or provider-error response is retained. Source order places this exception before embedding configuration, grant, index and MCP initialization.',
   'smoke_gap':'The successful source-image smoke directly untarred and executed package/bin/zg --version; it did not exercise npm global CLI linking or the native installer.',
   'limitation':'Actual install stdout/stderr and ZG_BIN_PATH were not retained. Do not claim npm itself succeeded or that absent QA means total network/cost zero.'}},
 'limitations':['Full native root/child exports are missing; this audit does not manufacture replacement exports or upgrade structural_valid.',
  'Baseline persisted DB snapshot records IDs, counters and completion, not final answer text. Terminal text equality is only stream/ATIF/collector.',
  'Baseline observed source-only tools and source hashes do not prove OS/network enforcement or absence of unrecorded external/background activity. Installation uses network.',
  'Live HTTP bodies, provider response headers, billable totals and embedding usage are not retained. Model/request settings are native config plus prior mock contract, not a live wire capture.',
  'Before/after source hashes are controller observations, not an independently retained post-run tree; treatment never reaches that audit.',
  'Configured-value secret scan succeeded before upload; unknown or encoded secrets are outside its assurance. No secret values were accessed by this audit.',
  'Container cleanup observations confirm no remaining running trial containers at checks; no long-term process tracing was retained.',
  'Collection contains the retained answer and was incidentally printed during metadata inspection; no semantic grading or quality conclusion is performed or included here.',
  'Partial baseline metrics are diagnostic only. Missing treatment usage is null, never zero and never comparable.'],
 'actions':{'new_model_or_provider_calls':0,'new_ci_dispatches':0,'repository_edits':0,'trial_retries':0,'grading':False,'offline_artifact_and_existing_fixture_checks_only':True}}
child=Path('/tmp/zg-opencode-treatment-failure-diagnosis.json')
if child.exists():report['zvec-grep']['failure_diagnosis']['independent_child_report']=ref(child)
with OUT.open('x') as f:json.dump(report,f,indent=2,ensure_ascii=False);f.write('\n')
print(json.dumps({'report':str(OUT),'sha256':sha(OUT),'script_sha256':sha(Path(__file__)),'protocol_valid':False,'baseline_tokens':usage['total'],'tool_names':report['baseline']['tool_audit']['names'],'source_read_segments_verified':read_verified},ensure_ascii=False))
