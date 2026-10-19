"""Audit only retained c01 contents; final global provenance is deliberately pending."""
from pathlib import Path
import importlib.util
import json
import sys
sys.dont_write_bytecode=True
BASE=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
HERE=BASE/'confirmation-v1-audit'
REPO=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
MAIN=BASE/'upstream-main/benchmarks/swe-qa-bench'
spec=importlib.util.spec_from_file_location('audit',HERE/'audit.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
I=BASE/'confirmation-v1-interim';R=I/'c01';raw=R/'raw';out=HERE/'c01/interim-machine-pass'
a.require(not out.exists(),'refuse overwrite')
run=a.load(I/'run-observed.json');a.require(run['id']==a.EXPECTED_RUN and run['head_sha']==a.EXPECTED_HEAD and run['run_attempt']==1,'wrong observed run')
a.require(run['status']!='completed','interim input unexpectedly claims whole run completed')
jobs=a.load(I/'jobs-observed.json')['jobs'];job=[j for j in jobs if j['name']=='confirmation c01'];a.require(len(job)==1 and job[0]['conclusion']=='success' and job[0]['status']=='completed','c01 job not completed successfully')
meta=a.load(R/'artifact-metadata.json');expected_sha='2cda61479b6e6ebbf9cdaa124639f6a8c1bb17ac0bdb6035325ec9517eef5a34'
a.require(meta['id']==12221083676 and meta['workflow_run']['id']==a.EXPECTED_RUN and meta['workflow_run']['head_sha']==a.EXPECTED_HEAD and meta['digest']=='sha256:'+expected_sha,'wrong c01 artifact')
artifact=a.verify_zip(R/'artifact.zip',raw,expected_sha)
reports=raw/'qa-confirmation-reports';run_dir=raw/'qa-confirmation-run';plan_path=reports/'confirmation-v1.json'
a.require(a.sha(plan_path)==a.PLAN_SHA==a.sha(REPO/'benchmarks/opencode-qa-pilot/confirmation-v1.json'),'confirmation plan differs');plan=a.load(plan_path)
for rel,h in plan['frozen_harness_sha256'].items():a.require(a.sha(REPO/rel)==h,'harness differs:'+rel)
for rel,h in plan['main_file_sha256'].items():a.require(a.sha(MAIN/rel)==h,'main differs:'+rel)
source=BASE/'confirmation-v1-local/c01/prepared/tasks/zvec-q001/environment/source';hashes=a.task_identity(source,REPO,plan)
execution=a.load(run_dir/'execution.json');a.identity(execution,'c01');collection=a.load(reports/'collection/collection.json');a.identity(collection,'c01')
sys.path[:0]=[str(REPO/'benchmarks/opencode-qa-pilot'),str(MAIN)]
import pilot
from repair_v2 import qa_agents as qa
from zg_bench.agents.opencode_usage import summarize_session_usage
question=(REPO/'benchmarks/zvec-issue-qa/tasks/q001/instruction.md').read_text();evidence=a.load(REPO/'benchmarks/zvec-issue-qa/evaluator/q001.json')['evidence_spans']
results=[];tools=[]
for profile in plan['profiles']:
 report,rows=a.audit_profile(profile,run_dir,collection,plan,source,evidence,question,pilot,qa,summarize_session_usage,hashes)
 report['repeat']='c01';slot=next(s for s in execution['slots'] if s['profile']==profile);a.identity(slot,'c01');report['slot']=slot
 if slot.get('status')!='exited' or slot.get('returncode')!=0 or slot.get('process_cleanup',{}).get('complete') is not True or slot.get('container_cleanup',{}).get('complete') is not True or slot.get('install_cache_absent_before_prepare') is not True:
  report['machine_evidence_valid']=False;report['errors'].append('slot/cleanup/install-cache invalid')
 report['protocol_valid']=None
 for t in rows:t['id']='c01/'+t['id'];t['repeat']='c01'
 results.append(report);tools.extend(rows)
queue=a.digest(a.canon(tools))
result={'schema_version':1,'kind':'interim_native_source_contents_audit','protocol_id':a.PROTOCOL,'phase':a.PHASE,'repeat':'c01','run_id':a.EXPECTED_RUN,'head':a.EXPECTED_HEAD,'plan_sha256':a.PLAN_SHA,'artifact_id':meta['id'],'artifact_integrity':artifact,'audit_helpers_sha256':a.sha(HERE/'audit.py'),'script_sha256':a.sha(Path(__file__)),'observed_global_status':run['status'],'observed_c01_job_id':job[0]['id'],'provenance_valid':None,'protocol_valid':None,'final_confirmation_decision':None,'status':'interim_only_pending_completed_global_proof_and_scope_review','profiles':results,'tool_queue_sha256':queue,'tools':tools,'quality_evaluated':False,'scope':'Only completed c01 contents from immutable interim artifact; global retained_binding was not called or altered, and final validity cannot yet be concluded.'}
out.mkdir(parents=True);(out/'audit.json').write_text(json.dumps(result,indent=2)+'\n');(out/'tool-review-queue.json').write_text(json.dumps({'protocol_id':a.PROTOCOL,'repeat':'c01','tool_queue_sha256':queue,'tools':tools},indent=2)+'\n')
print(json.dumps({'interim':True,'output':str(out),'protocol_valid':None,'profiles':[{'profile':p['profile'],'contents_checks_pass':p['machine_evidence_valid'],'errors':p['errors'],'tools':p.get('tools',{}).get('count')} for p in results]}))
