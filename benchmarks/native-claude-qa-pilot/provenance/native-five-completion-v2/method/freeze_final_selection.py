"""Freeze one complete pre-agent-only supplemental selection after every original setup settles.

Reads only original run/job states, byte receipts and unstarted-pair records.
Never reads quality reports, answer packets or either private condition mapping.
"""
from pathlib import Path
import datetime,hashlib,importlib.util,json,os,secrets,subprocess,sys,uuid
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');H=W/'benchmarks/native-claude-qa-pilot'
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
sys.path[:0]=[str(H),str(W/'benchmarks/opencode-qa-pilot'),str(B/'upstream-main/benchmarks/swe-qa-bench')]
import five_completion_v2 as completion
spec=importlib.util.spec_from_file_location('_original_pair_byte_verification',B/'native-five-repeat-v1-method/build_blind_packets.py')
original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def api(path):return json.loads(subprocess.run(['gh','api',path],capture_output=True,text=True,check=True).stdout)

def freeze():
 if completion.PLAN.is_file():
  plan=json.loads(completion.PLAN.read_text());completion.validate_completion(plan)
  print(json.dumps({'ready':True,'already_frozen':True,'plan_sha256':sha(completion.PLAN),'supplemental_pairs':len(plan['pairs']),'private_mapping_read_by_parent':False,'paid_dispatch_performed':False}));return 0
 run=api('repos/Cuiyus/zvec-grep/actions/runs/39392884840')
 jobs=api('repos/Cuiyus/zvec-grep/actions/runs/39392884840/jobs?per_page=100')
 try:selected=completion.missing_pairs(run,jobs)
 except ValueError as error:
  print(json.dumps({'ready':False,'reason':str(error),'run_id':39392884840,'quality_or_mapping_read':False}));return 3
 if not completion.parallel_capacity(jobs,len(selected)):
  print(json.dumps({'ready':False,'reason':'waiting_for_combined_twenty_pair_capacity','selected_pairs':len(selected),'quality_or_mapping_read':False}));return 3
 receipts={p['key']:original.CI/p['key']/'artifact-byte-verification.json' for p in completion.five.schedule()}
 selected_receipts={pair['key']:receipts[pair['key']] for pair in selected}
 if not all(p.is_file() for p in selected_receipts.values()):
  print(json.dumps({'ready':False,'reason':'waiting_for_selected_unstarted_pair_byte_receipts','retained':sum(p.is_file() for p in receipts.values()),'quality_or_mapping_read':False}));return 3
 for pair in selected:
  raw=original.verify_artifact(pair['key'])
  assert not (raw/'live').exists() and not (raw/'live-five-context.json').exists()
 if not selected:
  print(json.dumps({'ready':True,'supplemental_pairs':0,'no_extra_solver_calls_required':True}));return 0
 root=B/'native-five-completion-v2-ci';root.mkdir(exist_ok=False)
 public=H/'provenance/native-five-completion-v2/admission';public.mkdir(parents=True,exist_ok=False)
 for name,data in [('original-admission-run.json',run),('original-admission-jobs.json',jobs)]:
  write(root/name,data);write(public/name,data)
 evidence={'schema_version':1,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'original_run_id':39392884840,'original_head':completion.ORIGINAL_HEAD,'pairs':selected,
  'scheduled_supplemental_solver_trials':2*len(selected),'selection_basis':'Every original setup failure with the native execution step skipped; exact final set, no quality/resource inputs.',
  'original_primary_missing_rows_preserved':True,'quality_or_mapping_read':False,'executed_agent_outcome_retries':0,
  'selected_unstarted_original_receipt_sha256':{key:sha(p) for key,p in selected_receipts.items()},'all_fifty_original_setup_steps_settled':True,'combined_original_plus_supplement_parallel_pairs_at_most':20,
  'original_admission_run_sha256':sha(root/'original-admission-run.json'),'original_admission_jobs_sha256':sha(root/'original-admission-jobs.json'),
  'original_missing_pairs_verified_no_native_live_directory_or_context':True,'method_sha256':sha(Path(__file__))}
 write(root/'final-selection.json',evidence);write(public/'final-selection.json',evidence)
 oldruns=api('repos/Cuiyus/zvec-grep/actions/workflows/native-five-completion-v1.yml/runs?event=workflow_dispatch&per_page=100')
 assert oldruns['total_count']==0
 retired={'schema_version':1,'prototype_plan_sha256':sha(H/'five-completion-plan-v1.json'),
  'prototype_linux_preflight_run':39395235566,'prototype_explicit_paid_dispatches':0,
  'reason':'Additional pre-agent setup failures appeared after the q004-only provider-free prototype. This final complete selection supersedes it before any supplemental solver execution.',
  'prototype_assignment_reused':False,'prototype_original_evidence_preserved':True}
 write(root/'prototype-retirement.json',retired);write(public/'prototype-retirement.json',retired)
 private=B/'native-five-completion-v2-grading-private';private.mkdir(mode=0o700,exist_ok=False)
 rows=[{'task_id':pair['task_id'],'pair_key':pair['key'],'profile':profile,'repetition':pair['trial_index'],'anonymous_id':str(uuid.uuid4())} for pair in selected for profile in pair['profiles']]
 secrets.SystemRandom().shuffle(rows)
 assignment=private/'assignment.json';write(assignment,{'schema_version':1,'rows':rows});os.chmod(assignment,0o600)
 plan={'schema_version':1,'protocol_id':completion.PROTOCOL,'frozen':True,'created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'original_run_id':39392884840,'original_head':completion.ORIGINAL_HEAD,'original_plan_sha256':completion.ORIGINAL_PLAN_SHA,
  'pairs':selected,'scheduled_supplemental_solver_trials':len(rows),'n_attempts_per_pair':1,'max_retries':0,'max_parallel_pairs':20,
  'replace_original_missing_primary_rows':False,'final_selection_evidence':{'path':str((public/'final-selection.json').relative_to(W)),'sha256':sha(public/'final-selection.json')},
  'purpose':'Meet ten actual native executions per question/profile by completing only original whole pairs that failed setup before either agent started. No started agent is repeated.',
  'admission':'All fifty original setup steps must settle and every selected failed setup must end with native execution skipped. Original model jobs may still run only when their active-pair count plus the supplemental maximum is <=20; this count can only fall after all original jobs started. Same-commit provider-free Linux preflight before the sole explicit supplemental dispatch.',
  'stack':'Unchanged original five questions, source, native agent commands, model, current-at-original-admission Rust payload and common workspace context. Only the source-fetch preparation uses one operation without auto-maintenance. Later upstream drift is recorded without changing the cohort binary.',
  'analysis':'Original 100-row primary retains every absence. A separately named post-dispatch infrastructure-completion view may overlay these new observations at the originally unstarted repetition identities; preserve both inputs and disclose timing/wave differences. Not an original predeclared complete primary, score-tail filtering, a retry of a started agent, or a pool with historical data.',
  'grading':{'planned_answers':len(rows),'assignment_sha256':sha(assignment),'reports_before_unblinding':True,'quality_contract':'benchmarks/native-claude-qa-pilot/five-quality-contract-v1.json'},
  'quality_outcomes_or_mapping_read_for_selection':False,'supplement_solver_answers_exist':False,
  'frozen_harness_sha256':{p:sha(W/p) for p in sorted(completion.CONTROLS)}}
 write(H/'five-completion-plan-v2.json',plan)
 completion.validate_completion(plan)
 print(json.dumps({'ready':True,'selected_pre_agent_pairs':[p['key'] for p in selected],'supplemental_agent_executions':len(rows),'plan_sha256':sha(H/'five-completion-plan-v2.json'),'private_assignment_sha256':sha(assignment),'private_mapping_read_by_parent':False,'paid_dispatch_performed':False}))
 return 0
if __name__=='__main__':raise SystemExit(freeze())
