"""Read-only head-bound repeat audit; never dispatches, grades or changes raw data."""
from pathlib import Path,PurePosixPath
import argparse,datetime,hashlib,importlib.util,inspect,json,math,subprocess,sys,tarfile,textwrap
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');H=W/'benchmarks/native-claude-qa-pilot'
REFERENCE=B/'native-claude-live-preflight-v1/prepared';GROUP=B/'native-expansion-prepared-v1';M=B/'upstream-main/benchmarks/swe-qa-bench'
HEAD='aa230d91eb426eb295cf95fe0fa2d80d52f588e2';PLAN_SHA='dd3493b0a8b1253fe20cbbd7d6dd2256e4ea0ba7ca4d637c8ba0296dbbce2898';RUN=39364155852
METHOD=H/'results/20261020-expansion-v1/method/review-execution.py';METHOD_SHA='5659223ccebe9c6e5d421c3d400ff98b7a1dfd2743090cc5377bdfd4734a8085'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def write(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
assert sha(METHOD)==METHOD_SHA
spec=importlib.util.spec_from_file_location('_original_reserve_execution_audit',METHOD);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
sys.path[:0]=[str(H),str(W/'benchmarks/opencode-qa-pilot'),str(M),str(Path(__file__).parent)]
import repeat_live_v1 as driver
import repeat_inputs_v1 as preparation
import repeat_identity_v1 as identity
import accounting_v2
import live
from command_replay_v1 import expected_command
old.RUN,old.HEAD,old.PLAN_SHA,old.PROTOCOL=RUN,HEAD,PLAN_SHA,driver.PROTOCOL
old.PAIR_KEYS=[p['key'] for p in driver.schedule() if p['task_id']=='q001']
origin_source=textwrap.dedent(inspect.getsource(old.Audit.verify_origin))
origin_changes=[("j.get('name') == 'expansion (' + self.task + ')'","self.pair['key'] in j.get('name', '')"),
 ("f'native-claude-expansion-v1-{RUN}-{self.task}'","f'native-q001-repeat-v1-{RUN}-{self.pair[\"key\"]}'"),
 ("j.get('name') in ('expansion (q002)', 'expansion (q003)')","any(key in j.get('name', '') for key in PAIR_KEYS)"),
 ('len(peers) < 2','len(peers) < 10'),('across both final artifacts when both are available.','across all 10 final artifacts when available.')]
for before,after in origin_changes:
 assert before in origin_source,before;origin_source=origin_source.replace(before,after)
exec(compile(origin_source,str(Path(__file__))+'[origin-adapter]','exec'),old.__dict__)
profile_source=textwrap.dedent(inspect.getsource(old.Audit.profile))
old_prefix="""    pcmd = list(next(x['command'] for x in self.pre['slots'] if x['profile'] == profile))
    pi = pcmd.index('--jobs-dir') + 1
    pcmd[pi] = str(PurePosixPath(pcmd[pi]).parent.parent / 'live' / 'runs')"""
assert old_prefix in profile_source
profile_source=profile_source.replace(old_prefix,"    pcmd = self.expected_commands[profile]")
profile_changes=[('actual_command_exact_preflight_and_native_task','actual_command_exact_frozen_builder_and_native_task'),("r / 'preflight.json'","self.command_replay_path"),('GROUP / self.task','self.selected_root')]
for before,after in profile_changes:
 assert before in profile_source,before;profile_source=profile_source.replace(before,after)
exec(compile(profile_source,str(Path(__file__))+'[unchanged-profile-checks-with-bindings]','exec'),old.__dict__)

class Audit(old.Audit):
 verify_origin=old.__dict__['verify_origin']
 profile=old.__dict__['profile']
 def __init__(self,key,output_root):
  self.pair=next(p for p in driver.schedule() if p['key']==key);self.task=self.pair['task_id']
  self.c=B/'native-zvec-q001-repeat-v1-ci'/key;self.raw=self.c/'raw';self.live=self.raw/'live'
  self.out=output_root/key;self.out.mkdir(parents=True,exist_ok=True)
  self.checks,self.catalog,self.profiles,self.inits=[],{},{},{}
  self.profiles=[]
 def prepare(self):
  from types import SimpleNamespace
  r=self.raw
  self.plan=load(r/'repeat-plan-v1.json');self.base=load(r/'live-plan-v2.json');inputs=load(r/'inputs.json')
  self.check('exact_frozen_repeat_plan_and_decision',sha(r/'repeat-plan-v1.json')==PLAN_SHA and (r/'repeat-plan-v1.json').read_bytes()==old.gitbytes('benchmarks/native-claude-qa-pilot/repeat-plan-v1.json') and (r/'repeat-decision-v1.json').read_bytes()==old.gitbytes('benchmarks/native-claude-qa-pilot/repeat-decision-v1.json'),[r/'repeat-plan-v1.json',r/'repeat-decision-v1.json'],category='binding')
  assert sha(r/'repeat-plan-v1.json')==PLAN_SHA
  allpins={**inputs['repository_files'],**self.base['frozen_harness_sha256'],**self.plan['frozen_harness_sha256']}
  bad=[p for p,d in allpins.items() if hashlib.sha256(old.gitbytes(p)).hexdigest()!=d]
  self.check('all_repository_controls_at_exact_execution_commit',not bad,[r/'repeat-plan-v1.json',r/'live-plan-v2.json',r/'inputs.json'],category='binding',checked_files=len(allpins),mismatches=bad);assert not bad
  local_bad=[p for p,d in allpins.items() if p.endswith('.py') and sha(W/p)!=d]
  self.check('local_replay_code_matches_execution_pins',not local_bad,[W/p for p in allpins if p.endswith('.py')],category='binding',mismatches=local_bad);assert not local_bad
  args=SimpleNamespace(plan=H/'repeat-plan-v1.json',main_root=M,reference_prepared=REFERENCE,prepared_group=GROUP,candidate=r/'zg-install.tgz',task_id=self.task,repetition=self.pair['trial_index'])
  checked,base,self.proof=driver.validate_plan(args);assert checked==self.plan and base==self.base
  self.check('full_final_repeat_and_original_native_plan_revalidated',True,[r/'repeat-plan-v1.json',r/'inputs.json',r/'live-plan-v2.json'],category='binding')
  self.accounting,self.identity,self.collector=accounting_v2,identity,live
  self.metadata,files,self.selected_root=preparation.selected_inputs(GROUP,REFERENCE,self.task)
  self.manifest=load(self.selected_root/'source-manifest.json');self.canonical={f['path']:f for f in files};self.source=self.selected_root/'tasks'/('zvec-'+self.task)/'environment/source'
  self.question_path=W/'benchmarks/zvec-issue-qa'/self.metadata['question']['path'];self.question=self.question_path.read_text()
  refs=self.plan['prepared']
  self.check('all_three_retained_prepared_identities',sha(r/'prepared-group.json')==refs['group_sha256'] and all(sha(r/(t+'-prepared.json'))==refs['metadata_sha256'][t] and sha(r/(t+'-source-manifest.json'))==refs['source_manifest_sha256'][t] for t in preparation.TASK_IDS),[r/'prepared-group.json',*[r/(t+'-prepared.json') for t in preparation.TASK_IDS],*[r/(t+'-source-manifest.json') for t in preparation.TASK_IDS]],category='binding')
  self.ctx=load(r/'live-repeat-context.json');self.execution=load(self.live/'execution.json');self.collection=load(self.live/'collection/collection.json')
  remote=self.ctx['recorded_prepared_root'];rt=PurePosixPath('/home/runner/work/_temp');expected_selected=rt/'qa-prepared' if self.task=='q001' else rt/'qa-reserve-group'/self.task
  expected_context={'schema_version':1,'protocol_id':driver.PROTOCOL,**self.pair,'plan_sha256':PLAN_SHA,'prepared':refs,'recorded_prepared_root':str(expected_selected),'dry_run':False}
  self.check('exclusive_repeat_context_and_actual_native_record_binding',self.ctx==expected_context and all(x['protocol_id']==driver.PROTOCOL and x['plan_sha256']==PLAN_SHA and x['offline_proof_sha256']==self.base['offline_proof']['proof_sha256'] for x in [self.execution,self.collection]) and self.execution['dry_run'] is False and [x['profile'] for x in self.execution['slots']]==self.pair['profiles'] and [x['profile'] for x in self.collection['profiles']]==self.pair['profiles'],[r/'live-repeat-context.json',self.live/'execution.json',self.live/'collection/collection.json'],category='binding')
  replay=identity.verify(GROUP,self.task,self.live,reference_prepared=REFERENCE,recorded_prepared_root=remote,expected_profiles=self.pair['profiles'],repo_root=W)
  rp=self.out/'task-identity-replay.json';write(rp,replay)
  expected_saved=dict(replay,native_collection_completed=True,native_collection_error_type=None,native_structural_valid=self.collection.get('structural_valid') is True,context_sha256=sha(r/'live-repeat-context.json'),pair_key=self.pair['key'],trial_index=self.pair['trial_index']);expected_saved['joint_gate_valid']=expected_saved['native_structural_valid'] and replay['task_identity_valid']
  self.check('exact_actual_task_identity_replay_and_joint_gate',replay['task_identity_valid'] is True and load(self.live/'task-identity.json')==expected_saved and expected_saved['joint_gate_valid'],[rp,self.live/'task-identity.json'],category='binding',errors=replay['errors'])
  remote_live=rt/('native-claude-repeat-v1-'+self.pair['key'])/'live'
  self.expected_commands={p:expected_command(p,self.selected_root,remote,str(remote_live)) for p in self.pair['profiles']}
  self.command_replay_path=self.out/'frozen-command-replay.json';write(self.command_replay_path,{'method':'Unchanged native command builder, explicit local-to-recorded source/main/output prefix mapping; not a new native execution.','commands':self.expected_commands,'helper_sha256':sha(Path(__file__).parent/'command_replay_v1.py')})
  slots=self.execution['slots']
  self.check('serial_profile_slots_and_four_fresh_volumes',len(slots)==2 and slots[0].get('finished_at_unix',math.inf)<=slots[1].get('started_at_unix',-math.inf) and len([v for s in slots for v in s.get('fresh_volumes',[])])==4 and len({v for s in slots for v in s.get('fresh_volumes',[])})==4,[self.live/'execution.json'])
  system=self.execution['system'];self.check('pinned_native_system_versions',system['harbor_version']=='0.18.0' and system['docker_architecture']=='x86_64' and system['python'].startswith('3.12.'),[self.live/'execution.json'])
  import q001_repeat_v1 as cohort
  outer_args=SimpleNamespace(plan=H/'q001-repeat-plan-v1.json',main_root=M,reference_prepared=REFERENCE,prepared_group=GROUP,candidate=r/'zg-install.tgz',repetition=self.pair['trial_index'],command='run')
  outer,reference,base,proof=cohort.validate_plan(outer_args)
  self.check('authoritative_twenty_trial_q001_cohort',self.task=='q001' and (r/'q001-repeat-plan-v1.json').read_bytes()==old.gitbytes('benchmarks/native-claude-qa-pilot/q001-repeat-plan-v1.json') and (r/'q001-repeat-decision-v1.json').read_bytes()==old.gitbytes('benchmarks/native-claude-qa-pilot/q001-repeat-decision-v1.json') and load(r/'live-q001-repeat-context.json')==cohort.context_record(outer_args),[r/'q001-repeat-plan-v1.json',r/'q001-repeat-decision-v1.json',r/'live-q001-repeat-context.json'],category='binding')
  self.outer_plan_sha256=sha(r/'q001-repeat-plan-v1.json')
  self.pkg=load(r/'package-derivation.json');self.package_sha=sha(r/'zg-install.tgz');self.payloads={x['path'].removeprefix('package/'):{'bytes':x['derived_bytes'],'sha256':x['derived_sha256'],'mode':x['mode']} for x in self.pkg['runtime_payloads']}
  with tarfile.open(r/'zg-install.tgz') as z:ok=all(hashlib.sha256(z.extractfile('package/'+k).read()).hexdigest()==v['sha256'] for k,v in self.payloads.items())
  self.check('original_canonical_package_and_unchanged_payloads',ok and len(self.payloads)==4 and self.package_sha==self.pkg['install_package_sha256']==self.base['candidate_sha256']==self.execution['candidate_sha256'] and self.pkg['upstream_commit']==inputs['upstream_commit']==self.base['upstream_main'] and all(x['unchanged'] is True for x in self.pkg['runtime_payloads']),[r/'zg-install.tgz',r/'package-derivation.json',r/'inputs.json'])
 def run(self):
  dst=self.out/'execution-review.json'
  if dst.exists() or (self.out/'task-identity-replay.json').exists():raise FileExistsError('audit_output_exists')
  self.verify_origin()
  try:
   self.prepare()
   for profile in self.pair['profiles']:
    try:self.profile(profile)
    except Exception as e:self.check(profile+':required_evidence_read_or_schema',False,error_type=type(e).__name__,error=str(e))
   roots=[p.get('root_session_id') for p in self.profiles];a,b=self.inits.get('baseline',{}),self.inits.get('zvec-grep',{})
   tools_ok=isinstance(a.get('tools'),list) and isinstance(b.get('tools'),list) and set(b['tools'])-set(a['tools'])=={'mcp__zvec_grep__zvec_grep_search'} and not set(a['tools'])-set(b['tools'])
   self.check('two_distinct_roots_and_unchanged_native_toolsets',len(roots)==2 and all(roots) and len(set(roots))==2 and tools_ok and self.collection.get('structural_valid') is True,[self.live/'execution.json',self.live/'collection/collection.json'])
  except Exception as e:self.check('required_global_evidence_read_or_schema',False,error_type=type(e).__name__,error=str(e))
  ev=all(c['passed'] for c in self.checks if c['category']!='metric');mv=all(c['passed'] for c in self.checks if c['category']=='metric')
  result={'schema_version':1,'reviewed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'protocol_id':driver.PROTOCOL,**self.origin,'pair_key':self.pair['key'],'trial_index':self.pair['trial_index'],'inner_runtime_plan_sha256':PLAN_SHA,'cohort_plan_sha256':getattr(self,'outer_plan_sha256',None),'execution_valid':ev,'metric_reconciliation_valid':mv,'reported_metric_pair_eligible':ev and mv and len(self.profiles)==2 and all(p.get('reported_metric_eligible') is True for p in self.profiles),'passed':all(c['passed'] for c in self.checks),'check_count':len(self.checks),'failed_checks':[c['check'] for c in self.checks if not c['passed']],'checks':self.checks,'profiles':self.profiles,'evidence_catalog':self.catalog,'quality_evaluated':False,'source_scope_audit_status':'separate_review_required','method':{'original_path':str(METHOD),'original_sha256':METHOD_SHA,'origin_adaptations':origin_changes,'profile_adaptations':profile_changes,'command_comparison':'Frozen native command replay replaces old per-job dry-run artifact; six real historical commands matched before new data review.','adapted_profile_source_sha256':hashlib.sha256(profile_source.encode()).hexdigest(),'audit_sha256':sha(Path(__file__))},'limitations':['Post-run source-tool semantic audit and original blinded answer grading are separate requirements.','Reported usage is not provider billing or proof of unlogged request coverage.','Cleanup and freshness are assessed from retained records; no expired host inspection.','No provider, Docker, CI execution, assignment access, regrading or raw mutation.']}
  write(dst,result);print(json.dumps({'pair_key':self.pair['key'],'execution_valid':ev,'metric_reconciliation_valid':mv,'checks':len(self.checks),'failed_checks':result['failed_checks']}));return 0 if result['passed'] else 1
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--pair',choices=old.PAIR_KEYS,required=True);p.add_argument('--output-root',type=Path,default=Path(__file__).parent/'reviews');a=p.parse_args();raise SystemExit(Audit(a.pair,a.output_root).run())
