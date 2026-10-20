import datetime,hashlib,json,os,sys
from pathlib import Path
from types import SimpleNamespace
w=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');h=w/'benchmarks/native-claude-qa-pilot';b=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot');main=b/'upstream-main/benchmarks/swe-qa-bench';out=b/'native-runtime-q005-preparation-v1'
sys.path[:0]=[str(h),str(w/'benchmarks/opencode-qa-pilot'),str(main)];os.environ['QA_MAIN_ROOT']=str(main)
import live,runtime_preparation_v1 as preparation,runtime_identity_v1 as identity
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):
 with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
reference=b/'native-claude-live-preflight-v1/prepared';group=out/'prepared';prepared=group/'q005';candidate=b/'repair-v2/revision-1-ci/raw/zg-install.tgz';plan_path=h/'live-plan-v2.json'
base,proof=live.validate_plan(plan_path,main,reference,candidate)
preparation.validate(group,reference)
rows=[]
for profile,agent in [('baseline','QABaseline'),('zvec-grep','QAZvec')]:
 with live.live_environment(base,profile,main,require_credentials=False):
  runner=live.pilot.native(main)
  cache=SimpleNamespace(zvec_grep_package='/tmp/zg-bench-zvec-grep.tgz' if profile=='zvec-grep' else None,zvec_grep_package_sha256=live.PACKAGE_SHA if profile=='zvec-grep' else None)
  command=live.build_command(runner,runner.load_suite(prepared/'suite.yaml'),profile,prepared,out/'command-preview-output',cache,base['embedding']['endpoint'])
 assert command[command.index('--include-task-name')+1]=='zvec-q005'
 assert command[command.index('--path')+1]==str(prepared/'tasks')
 assert command[command.index('--agent')+1]=='native_claude.agents:'+agent
 assert command[command.index('--n-attempts')+1]=='1' and command[command.index('--max-retries')+1]=='0'
 assert 'qa_source_manifest='+str(prepared/'source-manifest.json') in command
 rows.append({'profile':profile,'command':command,'actual_task_name':'zvec-q005','actual_task_path':str(prepared/'tasks'),'actual_source_manifest':str(prepared/'source-manifest.json')})
write(out/'native-command-preview.json',{'schema_version':1,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'task_id':'q005','dry_run_only':True,'solver_executed':False,'provider_calls':0,'docker_invoked':False,'execution_authorized':False,'scope':'Actual unchanged native command generation plus task/source argument checks only; not a frozen execution plan or real run.','base_plan_sha256':sha(plan_path),'candidate_sha256':sha(candidate),'commands':rows,'current_upstream_rust_must_be_rechecked_before_execution':True})
old=b/'native-zvec-q001-repeat-v1-ci/q001-r03/raw/live'
result=identity.verify(group,'q005',old,reference_prepared=reference,recorded_prepared_root='/home/runner/work/_temp/qa-prepared')
assert result['task_identity_valid'] is False
failed=[x['code'] for x in result['checks'] if not x['passed']]
assert 'baseline:command:--include-task-name' in failed and 'zvec-grep:command:--include-task-name' in failed
result.update(execution_authorized=False,scope='Negative replay: original real q001 evidence is not a q005 execution. Original evidence was read only and not modified.')
write(out/'q001-evidence-rejection.json',result)
metadata=live.pilot.load(prepared/'prepared.json');g=live.pilot.load(group/'group.json')
verification={'schema_version':1,'checked_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'task_id':'q005','question_sha256':metadata['question']['sha256'],'rubric_sha256':metadata['rubric']['sha256'],'source_commit':metadata['source_commit'],'source_file_count':metadata['source_file_count'],'source_inventory_sha256':metadata['source_inventory_sha256'],'group_sha256':sha(group/'group.json'),'prepared_metadata_sha256':sha(prepared/'prepared.json'),'source_manifest_sha256':sha(prepared/'source-manifest.json'),'native_task_hashes':metadata['native_task_hashes'],'native_original_templates_reference':metadata['reference'],'input_integrity_tests_passed':4,'actual_native_commands_verified':2,'real_q001_evidence_rejected':True,'q001_replay_failed_identity_checks':failed,'provider_calls':0,'docker_invoked':False,'solver_executed':False,'paid_execution_authorized':False}
write(out/'verification.json',verification)
print(json.dumps({k:verification[k] for k in ['task_id','source_file_count','group_sha256','prepared_metadata_sha256','native_task_hashes','input_integrity_tests_passed','actual_native_commands_verified','real_q001_evidence_rejected']}))
