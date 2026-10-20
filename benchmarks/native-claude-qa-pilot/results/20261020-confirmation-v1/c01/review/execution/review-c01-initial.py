from pathlib import Path, PurePosixPath
import json, hashlib, zipfile, tarfile, subprocess, sys, math, collections, datetime
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
C=B/'native-claude-confirmation-v1-ci/c01'; R=C/'raw'; L=R/'live'; O=B/'native-claude-confirmation-v1-audit'
HEAD='fd0c94c37c95e2afb4b5482530904574f5719c5f'; PLAN='cd5f20711d92615fb75aebffd5a295261b4b341d0f558cd6a9c36a5efd472e5a'; PROTOCOL='native-claude-q001-confirmation-v1'
profiles=['baseline','zvec-grep']; checks=[]; refs={}; details=[]
def h(b):return hashlib.sha256(b).hexdigest()
def sha(p):return h(p.read_bytes())
def load(p):return json.loads(p.read_text())
def ref(p):
 p=Path(p); key=str(p); refs[key]={'path':key,'sha256':sha(p),'bytes':p.stat().st_size}; return key
def ck(name,ok,paths,**observed):
 checks.append({'check':name,'passed':bool(ok),'evidence':[ref(p) for p in paths],'observed':observed})
def number(v):return type(v) in (int,float) and math.isfinite(v) and v>=0
def gitbytes(p):return subprocess.check_output(['git','show',f'{HEAD}:{p}'],cwd=W)
def rows(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def text(c):return c if isinstance(c,str) else '\n'.join(x['text'] for x in c if x.get('type')=='text')
run=load(C/'run-metadata.json'); jobs=load(C/'job-metadata.json'); job=next(x for x in jobs['jobs'] if x['id']==118371691990); am=load(C/'artifact-metadata.json'); receipt=load(C/'artifact-byte-verification.json')
ck('c01_origin_identity',run['id']==39343873279 and run['run_attempt']==1 and run['head_sha']==HEAD and run['event']=='workflow_dispatch' and run['repository']['full_name']=='Cuiyus/zvec-grep' and job['run_id']==run['id'] and job['head_sha']==HEAD and job['run_attempt']==1 and job['name']=='confirmation (c01)' and job['conclusion']=='success',[C/'run-metadata.json',C/'job-metadata.json'],global_run_status_at_collection=run['status'],global_conclusion_at_collection=run['conclusion'],job_id=job['id'])
ck('artifact_origin_identity',am['id']==12231279241 and am['workflow_run']['id']==run['id'] and am['workflow_run']['head_sha']==HEAD and am['size_in_bytes']==43361609,[C/'artifact-metadata.json'])
zp=C/'artifact.zip'; zsha=sha(zp); ck('original_zip_digest',zsha=='0a8272626ba3053fd00a5f489c27bf94998e7cfeaffc0da8667acca3155b89e2' and am['digest']=='sha256:'+zsha,[zp,C/'artifact-metadata.json'])
with zipfile.ZipFile(zp) as z:
 names=[x.filename for x in z.infolist() if not x.is_dir()]; files=[str(p.relative_to(R)) for p in R.rglob('*') if p.is_file()]; bad=[n for n in names if not (R/n).is_file() or z.read(n)!=(R/n).read_bytes()]
 ck('all_raw_files_equal_original_zip',z.testzip() is None and len(names)==130 and len(set(names))==130 and sorted(names)==sorted(files) and not bad,[zp,C/'artifact-byte-verification.json'],file_count=len(names),mismatches=bad)
plan=load(R/'confirmation-plan-v1.json'); base=load(R/'live-plan-v2.json'); inp=load(R/'inputs.json'); ctx=load(R/'live-confirmation-context.json'); pre=load(R/'preflight.json'); execution=load(L/'execution.json'); col=load(L/'collection/collection.json')
ck('frozen_plan_bytes_match_execution_commit',sha(R/'confirmation-plan-v1.json')==PLAN and gitbytes('benchmarks/native-claude-qa-pilot/confirmation-plan-v1.json')==(R/'confirmation-plan-v1.json').read_bytes() and plan['frozen'] is True,[R/'confirmation-plan-v1.json',C/'run-metadata.json'])
ck('base_plan_bytes_match_frozen_reference',sha(R/'live-plan-v2.json')==plan['base_plan']['sha256']=='b4e8fb15b6cae82a9083add53ad7c04e67673720f7a50825fde587b7538ad026',[R/'live-plan-v2.json',R/'confirmation-plan-v1.json'])
allpins={**inp['repository_files'],**base['frozen_harness_sha256'],**plan['frozen_harness_sha256']}; mismatches=[p for p,s in allpins.items() if h(gitbytes(p))!=s]
ck('frozen_repository_inputs_at_execution_head',not mismatches,[R/'inputs.json',R/'live-plan-v2.json',R/'confirmation-plan-v1.json',C/'run-metadata.json'],checked_files=len(allpins),mismatches=mismatches,git_revision=HEAD)
ck('preflight_execution_collection_same_protocol',all(d['protocol_id']==PROTOCOL and d['plan_sha256']==PLAN and d['offline_proof_sha256']==base['offline_proof']['proof_sha256'] for d in [ctx,pre,execution,col]) and pre['dry_run'] is True and execution['dry_run'] is False and ctx['dry_run'] is False and ctx['pair_id']=='c01' and ctx['profiles']==profiles and ctx['base_plan_sha256']==sha(R/'live-plan-v2.json') and ctx['development_summary_sha256']==plan['development_summary']['sha256'],[R/'preflight.json',R/'live-confirmation-context.json',L/'execution.json',L/'collection/collection.json'])
ck('prescribed_order_and_attempts',plan['n_attempts']==1 and plan['max_retries']==0 and next(x for x in plan['pairs'] if x['id']=='c01')['profiles']==profiles and [x['profile'] for x in execution['slots']]==profiles and [x['profile'] for x in col['profiles']]==profiles,[R/'confirmation-plan-v1.json',L/'execution.json',L/'collection/collection.json'])
ck('sequential_slots',execution['slots'][0]['finished_at_unix']<=execution['slots'][1]['started_at_unix'],[L/'execution.json'])
ck('fresh_profile_volumes_disjoint',len(set(sum((s['fresh_volumes'] for s in execution['slots']),[])))==4,[L/'execution.json'])
ck('system_runtime_versions',execution['system']['harbor_version']=='0.18.0' and execution['system']['docker_architecture']=='x86_64' and execution['system']['python'].startswith('3.12.'),[L/'execution.json'])
manifestpath=B/'native-claude-live-preflight-v1/prepared/source-manifest.json'; manifest=load(manifestpath); source=manifestpath.parent/'tasks/zvec-q001/environment/source'; canonical={x['path']:x for x in manifest['files']}
ck('frozen_source_manifest_and_local_bytes',len(canonical)==1232 and manifest['source_commit']=='ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d' and manifest['inventory_sha256']==inp['source_inventory_sha256'] and all((source/k).stat().st_size==v['bytes'] and sha(source/k)==v['sha256'] for k,v in canonical.items()),[manifestpath,R/'inputs.json'],source_root=str(source))
pkg=load(R/'package-derivation.json'); package_sha=sha(R/'zg-install.tgz'); payloads={x['path'].removeprefix('package/'): {'bytes':x['derived_bytes'],'sha256':x['derived_sha256'],'mode':x['mode']} for x in pkg['runtime_payloads']}
with tarfile.open(R/'zg-install.tgz') as tar: validpayloads=all(h(tar.extractfile('package/'+k).read())==v['sha256'] for k,v in payloads.items())
ck('exact_install_package_and_upstream_payloads',validpayloads and len(payloads)==4 and package_sha==pkg['install_package_sha256']==base['candidate_sha256']==execution['candidate_sha256'] and pkg['upstream_commit']==inp['upstream_commit']=='a424e0ce06868dc9ad8f142f57de59b8f9b58699' and all(x['unchanged'] for x in pkg['runtime_payloads']),[R/'zg-install.tgz',R/'package-derivation.json',R/'inputs.json',L/'execution.json'])
qpath=W/'benchmarks/zvec-issue-qa/tasks/q001/instruction.md'; question=qpath.read_text();ck('unchanged_question_bytes',sha(qpath)==base['question_sha256']==inp['repository_files']['benchmarks/zvec-issue-qa/tasks/q001/instruction.md'],[qpath,R/'live-plan-v2.json',R/'inputs.json'])
sys.path.insert(0,str(W/'benchmarks/native-claude-qa-pilot')); import accounting_v2
ck('replay_reader_frozen_identity',sha(Path(accounting_v2.__file__))==base['frozen_harness_sha256']['benchmarks/native-claude-qa-pilot/accounting_v2.py'],[Path(accounting_v2.__file__),R/'live-plan-v2.json'])
inits=[]; sessionids=[]
for n,profile in enumerate(profiles):
 cp=next(x for x in col['profiles'] if x['profile']==profile); slot=execution['slots'][n]; t=L/cp['trial_directory']; agent=t/'agent';sp=agent/'claude-code.txt'; stream=rows(sp); persistedpaths=sorted((agent/'sessions').rglob('*.jsonl')); allpersisted=[(p,i+1,row) for p in persistedpaths for i,row in enumerate(rows(p))]; init=[x for x in stream if x.get('type')=='system' and x.get('subtype')=='init']; terminal=[x for x in stream if x.get('type')=='result']; term=terminal[0]; sid=init[0]['session_id'];inits.append(init[0]);sessionids.append(sid)
 ap=L/'collection'/f'{profile}-strict-accounting.json'; acc=load(ap); recomputed=accounting_v2.analyze(agent); cfg=load(t/'config.json');lock=load(t/'lock.json');tr=load(t/'result.json'); atif=load(agent/'trajectory.json'); audit=load(t/'qa-source-audit.json'); answer=L/'collection'/cp['answer_file']; rr=L/'collection'/f'{profile}-root-result.json'
 ck(profile+':one_native_trial',len(list((L/'runs'/('q001-'+profile)).glob('*/result.json')))==1 and t.name==tr['trial_name']==cfg['trial_name'] and tr['exception_info'] is None,[t/'config.json',t/'result.json'])
 ck(profile+':slot_terminal_and_cleanup',slot==load(L/f'{profile}-slot.json') and slot['status']=='exited' and slot['returncode']==0 and slot['container_cleanup']=={'complete':True,'remaining_running':[],'stopped':[]} and slot['process_cleanup']['complete'] is True,[L/'execution.json',L/f'{profile}-slot.json',t/'trial.log',L/f'{profile}-job.log'],evidence_limit='Recorded process-group and Docker cleanup checks, not a new live host inspection.')
 cmd=slot['command']; pcmd=pre['slots'][n]['command'];pcmd=[x.replace('/dry-run/runs','/live/runs') for x in pcmd];
 ck(profile+':preflight_command_exact',cmd==pcmd and cmd[cmd.index('--n-attempts')+1]=='1' and cmd[cmd.index('--max-retries')+1]=='0' and cmd[cmd.index('--n-concurrent')+1]=='1' and cmd[cmd.index('--include-task-name')+1]=='zvec-q001',[R/'preflight.json',L/'execution.json'])
 ck(profile+':task_identity_config_lock_result',cfg['task']['path']==lock['task']['path']==tr['task_id']['path']=='/home/runner/work/_temp/qa-prepared/tasks/zvec-q001' and lock['task']['name']==tr['task_name']=='zvec-q001' and lock['task']['digest']=='sha256:59bd7cc5f70b054d1305954fa720636dc6230003acf4826d39be13f1a44f90e9' and tr['task_checksum']=='ff1810e432e6de5d6b111ee6e02f0fb55d131d88201d6adbcb30113d72cd6e9a' and cfg['agent']['model_name']==lock['agent']['model_name']=='qwen3.8-max' and cfg['agent']['name']==cmd[cmd.index('--agent')+1] and all(cfg['agent']['kwargs'][k]==v for k,v in {'qa_profile':profile,'version':'2.1.212','reasoning_effort':'high','max_budget_usd':4.0}.items()),[t/'config.json',t/'lock.json',t/'result.json',L/'execution.json'])
 containers=load(L/f'{profile}-containers.json'); compose=load(L/f'{profile}-compose.json'); mountbad=[]
 for c in containers.values():
  mounts={x['Destination']:x for x in c['mounts']}
  for dest,name in zip(['/root/.nvm','/root/.local'],slot['fresh_volumes']):
   if mounts.get(dest,{}).get('Name')!=name or mounts[dest]['Type']!='volume':mountbad.append(dest)
  for dest,m in mounts.items():
   src=PurePosixPath(m['Source']);trial=PurePosixPath(c['trial_dir'])
   if dest in ['/root/.nvm','/root/.local']:continue
   if dest=='/tmp/zg-bench-zvec-grep.tgz':ok=profile=='zvec-grep' and m['RW'] is False and str(src)==slot['install_package_source'] and slot['install_package_sha256']==package_sha
   else:ok=dest.startswith('/logs/') and (trial==src or trial in src.parents)
   if not ok:mountbad.append(dest)
 ck(profile+':mounts_contain_no_host_gold',len(containers)==1 and not mountbad and {v['name'] for v in compose['volumes'].values()}==set(slot['fresh_volumes']),[L/f'{profile}-containers.json',L/f'{profile}-compose.json',L/f'{profile}-slot.json'],invalid_destinations=mountbad)
 ck(profile+':native_root_identity_and_success',len(init)==len(terminal)==1 and stream[-1]==term and init[0]['cwd']=='/workspace' and init[0]['model']=='qwen3.8-max' and init[0]['claude_code_version']=='2.1.212' and term['session_id']==sid and term['subtype']=='success' and term['is_error'] is False and term['terminal_reason']=='completed' and term['stop_reason']=='end_turn' and not term['permission_denials'] and load(rr)==term,[sp,rr,t/'result.json'],root_session_id=sid)
 ck(profile+':root_question_exact',len(persistedpaths)==1 and next(x['message']['content'] for _,_,x in allpersisted if x.get('type')=='user')==question and atif['steps'][0]['message']==question,[*persistedpaths,agent/'trajectory.json',qpath])
 ck(profile+':accounting_replay_equals_retained',recomputed==acc and acc['complete'] and acc['complete_tree'] and not acc['errors'],[ap,sp,*persistedpaths,Path(accounting_v2.__file__)])
 ck(profile+':answer_exact_native_equality',answer.read_text()==term['result']==acc['final']['text'] and atif['steps'][-1]['message']==term['result'] and sha(answer)==cp['answer_sha256'] and atif['session_id']==sid,[answer,rr,ap,agent/'trajectory.json'],answer_sha256=sha(answer))
 latest={}; regress=[]; tools={}; results={}; pids=set(); models=set()
 for p,i,x in allpersisted:
  if x.get('type')=='assistant':
   m=x['message'];mid=m['id'];models.add(m.get('model'));pids.add(x.get('sessionId')); u=m.get('usage',{})
   if not all(number(u.get(k)) for k in ['input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens']):regress.append([mid,i,'invalid'])
   if mid in latest and any(u.get(k,-1)<latest[mid]['usage'].get(k,-1) for k in ['input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens']):regress.append([mid,i,'regression'])
   latest[mid]={'usage':u,'file':str(p.relative_to(agent)),'terminal_line':i}
   for b in m.get('content',[]):
    if b.get('type')=='tool_use':
     if b['id'] in tools and tools[b['id']]!=b:regress.append([b['id'],i,'tool_conflict'])
     tools[b['id']]=b
  if x.get('type')=='user' and isinstance(x.get('message',{}).get('content'),list):
   for b in x['message']['content']:
    if b.get('type')=='tool_result':
     if b['tool_use_id'] in results and results[b['tool_use_id']]!=b:regress.append([b['tool_use_id'],i,'result_conflict'])
     results[b['tool_use_id']]=b
 fields=['input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens']; totals={k:sum(v['usage'][k] for v in latest.values()) for k in fields}; strictmap={'uncached_input_tokens':totals['input_tokens'],'output_tokens':totals['output_tokens'],'cache_read_tokens':totals['cache_read_input_tokens'],'cache_write_tokens':totals['cache_creation_input_tokens'],'input_tokens':sum(totals[k] for k in ['input_tokens','cache_read_input_tokens','cache_creation_input_tokens'])}
 ck(profile+':independent_terminal_message_usage_sum',not regress and models=={'qwen3.8-max'} and pids=={sid} and len(latest)==acc['usage']['llm_calls'] and set(latest)=={m['message_id'] for m in acc['messages']} and all(latest[m['message_id']]['usage']==m['usage'] for m in acc['messages']) and all(acc['usage'][k]==v==cp['strict_usage'][k] for k,v in strictmap.items()),[*persistedpaths,ap,L/'collection/collection.json'],message_count=len(latest),usage=strictmap,issues=regress)
 streamlatest={x['message']['id']:x['message'].get('usage') for x in stream if x.get('type')=='assistant'}
 ck(profile+':stream_and_persisted_terminal_usage_match',streamlatest=={k:v['usage'] for k,v in latest.items()},[sp,*persistedpaths],stream_assistant_rows=sum(x.get('type')=='assistant' for x in stream),counted_unique_messages=len(latest))
 launches=[k for k,v in tools.items() if v['name'] in ['Agent','Task']]; childfiles=[p for p in persistedpaths if 'subagents' in p.parts]
 ck(profile+':tool_completion_and_child_scope',set(tools)==set(results)=={x['id'] for x in acc['tools']} and all(x['completed'] for x in acc['tools']) and not launches and not childfiles and acc['child_count']==0 and acc['session_count']==1 and acc['root_only'] and all(x.get('sessionId') in [None,sid] for _,_,x in allpersisted),[*persistedpaths,ap],tool_count=len(tools),child_launch_ids=launches,retained_child_files=len(childfiles))
 mu=term['modelUsage']; raw=mu.get('qwen3.8-max',{}); fm={'inputTokens':'uncached_input_tokens','outputTokens':'output_tokens','cacheReadInputTokens':'cache_read_tokens','cacheCreationInputTokens':'cache_write_tokens'}
 ck(profile+':reported_four_fields_exact_and_corroborated',set(mu)=={'qwen3.8-max'} and all(number(raw.get(k)) and raw[k]==strictmap[v] for k,v in fm.items()) and all(cp['claude_reported_model_usage']['usage'][k]==v for k,v in strictmap.items()) and cp['claude_reported_model_usage']['scope']=='claude-reported-model-usage' and cp['metric_consistency']['corroboration_status']=='complete-and-matching' and not cp['metric_consistency']['contradictions'] and cp['claude_reported_model_usage']['cost_usd'] is None,[rr,*persistedpaths,L/'collection/collection.json'],required_fields={k:raw[k] for k in fm},primary_input=strictmap['input_tokens'],scope='Claude-reported sole-model aggregate corroborated against retained root-only tree; not provider billing or independent proof of background request coverage.')
 phasecounts={}
 for phase in ['source-before','source-after','logs-before','logs-after']:
  e=t/'qa-native-claude-evidence'/phase; cap=load(e/'capture.json'); data=load(e/'stdout.json');status=(e/'status.txt').read_text()
  ck(profile+':capture_'+phase,status=='0\n' and load(e/'transport.json')['return_code']==0 and cap['valid'] and all(v['complete'] and sha(e/k)==v['sha256'] and (e/k).stat().st_size==v['bytes'] for k,v in cap['files'].items()) and sha(e/'transport.json')==cap['transport_file']['sha256'],[e/'capture.json',e/'stdout.json',e/'stderr.txt',e/'status.txt',e/'transport.json'])
  if phase.startswith('source'):
   observed={x['path']:x for x in data['files']}; additions=set(observed)-set(canonical); sourceok=all(observed.get(k,{}).get('kind')=='file' and all(observed[k].get(f)==v[f] for f in ['sha256','bytes']) for k,v in canonical.items()); addok=all(profile=='zvec-grep' and (k.startswith('.zvec-grep/') or k=='.zg-bench-index.ignore') and observed[k]['kind']=='file' for k in additions)
   ck(profile+':frozen_tree_'+phase,sourceok and addok and len(observed)==len(data['files']) and audit[phase.split('-')[1]]['observed']==data,[e/'stdout.json',manifestpath,t/'qa-source-audit.json'],original_files=1232,total_files=len(observed),allowed_addition_count=len(additions));phasecounts[phase]=len(observed)
   runtime=data['runtime'];claude=runtime['claude'];rtok=claude['version']=='2.1.212 (Claude Code)' and claude['binary_sha256']==claude['entrypoint_sha256']=='044a88cf3a5180776617fd3da1238dcbf9141ddec449a39cf7d2af1ac78e684e'
   if profile=='zvec-grep':rtok &= runtime['zg']['payloads']==payloads and runtime['zg']['binary_sha256']==pkg['binary_sha256']
   else:rtok &= set(runtime)=={'claude'} and all(data.get(k) is False for k in ['zg_executable_present','zg_home_present','mcp_config_present']) and data['embedding_environment_names']==[]
   ck(profile+':pinned_runtime_'+phase,rtok,[e/'stdout.json',R/'package-derivation.json'],claude_binary_sha256=claude['binary_sha256'])
  else:
   native=t/'qa-native-claude-logs'; expected={x['path'] for x in data['files']}; copyok=all(sha(native/x['path'])==sha(agent/x['path'])==x['sha256'] and (native/x['path']).stat().st_size==x['bytes'] for x in data['files']); present={str(p.relative_to(native)) for p in native.rglob('*') if p.is_file()}
   ck(profile+':native_log_export_'+phase,copyok and present==expected and data['root']=='/logs/agent',[e/'stdout.json',t/'qa-source-audit.json',*persistedpaths],exported_files=len(expected))
 # Recorded timings: recompute all timestamp-based intervals, retain instrumentation scopes separately.
 timing={};timeok=True
 for key in ['environment_setup','agent_setup','agent_execution','verifier','trial']:
  ts=tr if key=='trial' else tr[key]; start=datetime.datetime.fromisoformat(ts['started_at'].replace('Z','+00:00'));end=datetime.datetime.fromisoformat(ts['finished_at'].replace('Z','+00:00'));secs=(end-start).total_seconds();timing[key]=secs;timeok &= secs>=0 and abs(secs-cp['harbor_timing'][key]['seconds'])<1e-6
 outer=slot['finished_at_unix']-slot['started_at_unix'];timeok &= abs(outer-cp['outer_slot_wall_seconds'])<1e-6 and all(cp[k]==audit[ak] and number(cp[k]) for k,ak in [('native_run_wall_seconds','native_run_wall_seconds'),('source_audit_wall_seconds','audit_wall_seconds'),('log_export_wall_seconds','export_wall_seconds')])
 ck(profile+':separate_timing_scopes_match_raw',timeok,[t/'result.json',t/'qa-source-audit.json',L/f'{profile}-slot.json',L/'collection/collection.json'],harbor_seconds=timing,outer_slot_wall_seconds=outer,native_run_wall_seconds=audit['native_run_wall_seconds'],native_stream_duration_ms=term['duration_ms'],audit_wall_seconds=audit['audit_wall_seconds'],export_wall_seconds=audit['export_wall_seconds'])
 if profile=='zvec-grep':
  setup=load(agent/'zvec-grep-setup.json');ck(profile+':native_zg_setup_timing_matches',all(setup[k]==v for k,v in cp['native_zg_setup_timing'].items()),[agent/'zvec-grep-setup.json',L/'collection/collection.json'],timing=cp['native_zg_setup_timing'])
 if profile=='zvec-grep':
  ck(profile+':fresh_zg_index_and_setup_identity',setup['status']=='ready' and setup['workdir']=='/workspace' and setup['mcp_target']=='claude-code' and setup['package_sha256']==package_sha and setup['index_cache_hit'] is False and setup['index_seed_available'] is False and setup['install_reused_cache'] is False and setup['embedding_model']=='qwen/qwen3.7-text-embedding' and setup['binding_package']=='@zvec/bindings-linux-x64@0.5.0' and setup['index_ignore_sha256']==sha(manifestpath.parent/'index.ignore'),[agent/'zvec-grep-setup.json',manifestpath.parent/'index.ignore',R/'live-plan-v2.json'])
 details.append({'profile':profile,'trial_directory':str(t),'root_session_id':sid,'answer_sha256':sha(answer),'strict_complete':acc['complete'],'session_count':1,'child_count':0,'independent_unique_messages':len(latest),'tool_count':len(tools),'reported_required_fields':{k:raw[k] for k in fm},'usage':strictmap,'source_inventory_counts':phasecounts,'timing_seconds':{**timing,'outer_slot':outer,'native_run':audit['native_run_wall_seconds'],'source_audit':audit['audit_wall_seconds'],'log_export':audit['export_wall_seconds']}})
ck('independent_root_sessions',len(set(sessionids))==2,[L/'collection/baseline-strict-accounting.json',L/'collection/zvec-grep-strict-accounting.json'])
ck('same_native_tool_inventory_except_zg',set(inits[1]['tools'])-set(inits[0]['tools'])=={'mcp__zvec_grep__zvec_grep_search'} and not(set(inits[0]['tools'])-set(inits[1]['tools'])),[L/c['trial_directory']/'agent/claude-code.txt' for c in col['profiles']])
report={'schema_version':1,'reviewed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Independent c01 execution/runtime/accounting review only; no quality grading, semantic source/tool audit or whole confirmation decision.','repository':'Cuiyus/zvec-grep','run_id':39343873279,'run_attempt':1,'head_sha':HEAD,'pair_id':'c01','protocol_id':PROTOCOL,'plan_sha256':PLAN,'raw_directory':str(R),'original_artifact_sha256':zsha,'artifact_id':12231279241,'global_run_complete_at_collection':False,'global_confirmation_decision':'pending','passed':all(c['passed'] for c in checks),'check_count':len(checks),'passed_checks':sum(c['passed'] for c in checks),'failed_checks':[c['check'] for c in checks if not c['passed']],'checks':checks,'profiles':details,'evidence_catalog':refs,'limitations':['Each check references exact retained file bytes through evidence_catalog; no collector validity flag alone establishes a check.','No provider requests, Docker execution, CI dispatch, retries, raw mutation, Git writes or regrading.','Source/tool semantic audit is independently assigned and remains outside this execution review.','Cleanup and freshness are verified from recorded native controller/Docker evidence and frozen code, not live inspection of the expired runner.','Primary usage is CLI-reported sole-model aggregate. Complete retained tree corroborates all four fields here; it is not provider billing or proof of all background requests.','No quality, candidate or overall confirmation conclusion is made from c01.','No raw evidence or answers from c02 inspected.']}
(O/'c01-execution-review.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n');print(json.dumps({'passed':report['passed'],'checks':len(checks),'failed':report['failed_checks'],'path':str(O/'c01-execution-review.json'),'sha256':sha(O/'c01-execution-review.json')},indent=2))
