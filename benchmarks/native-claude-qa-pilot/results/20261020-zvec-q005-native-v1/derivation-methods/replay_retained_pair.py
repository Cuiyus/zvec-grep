"""Replay retained q005 assembly/collection without solvers or original writes."""
import argparse, datetime, hashlib, importlib.util, json, sys
from pathlib import Path
from types import SimpleNamespace

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
H=W/'benchmarks/native-claude-qa-pilot'
CI=B/'native-q005-live-v1-ci/39383511290'
OUT=B/'native-q005-live-v1-audit/retained-replay'
RUN=39383511290
HEAD='30fe7fc4b80027cf62006bbe9d3de59445f10a16'
PROFILES=('baseline','zvec-grep')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def write(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')

def audit():
    keys=(*PROFILES,'joint')
    if not all((CI/k/'artifact-byte-verification.json').is_file() for k in keys):
        print(json.dumps({'ready':False,'original_raw_read':False,'new_solver_calls':0}));return
    assert not OUT.exists()
    spec=importlib.util.spec_from_file_location('_q005_packet_byte_verifier',Path(__file__).with_name('build_blind_packet.py'))
    verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
    roots={k:verifier.verify_artifact(CI/k,k) for k in keys}
    sys.path.insert(0,str(H));import runtime_live_v1 as runtime
    assembly=runtime.assembly
    before={k:assembly.inventory(root) for k,root in roots.items()}
    worker_receipts={p:runtime.artifacts.verify_retained(roots['joint']/'workers'/p,run_id=RUN,head_sha=HEAD,profile=p) for p in PROFILES}
    assert len({r['job_id'] for r in worker_receipts.values()})==2 and len({r['runner_id'] for r in worker_receipts.values()})==2
    for p in PROFILES:
        retained=roots['joint']/'workers'/p
        assert sha(retained/'artifact.zip')==sha(CI/p/'artifact.zip')==worker_receipts[p]['artifact_sha256']
        assert assembly.inventory(retained/'raw')==before[p]
    args=SimpleNamespace(plan=H/'runtime-live-plan-v1.json',main_root=B/'upstream-main/benchmarks/swe-qa-bench',
        reference_prepared=B/'native-claude-live-preflight-v1/prepared',prepared_group=B/'native-runtime-q005-preparation-v1/prepared',
        candidate=B/'repair-v2/revision-1-ci/raw/zg-install.tgz',workers_root=roots['joint']/'workers',
        output=OUT/'live',run_id=RUN,head_sha=HEAD,profile=None,command='assemble')
    plan,base,proof=runtime.validate_plan(args)
    OUT.mkdir(parents=True)
    result=runtime.assemble_and_collect(args,plan,base,proof)
    old=roots['joint']/'live';new=args.output
    checks={'original_slots_exact':load(old/'execution.json')['slots']==load(new/'execution.json')['slots'],
        'entire_assembled_execution_record_exact_bytes':(old/'execution.json').read_bytes()==(new/'execution.json').read_bytes(),
        'original_assembly_receipt_exact_bytes':(old/'profile-assembly.json').read_bytes()==(new/'profile-assembly.json').read_bytes()}
    prior=load(old/'collection/collection.json');col=load(new/'collection/collection.json')
    for p in PROFILES:
        a=next(x for x in prior['profiles'] if x['profile']==p);b=next(x for x in col['profiles'] if x['profile']==p)
        for key in ['answer_sha256','strict_usage','claude_reported_model_usage','harbor_timing','outer_slot_wall_seconds','native_init_tools','structural_valid','reported_metric_eligible']:
            checks[p+':'+key]=a[key]==b[key]
        checks[p+':answer_exact_bytes']=(old/'collection'/a['answer_file']).read_bytes()==(new/'collection'/b['answer_file']).read_bytes() if a.get('answer_file') and b.get('answer_file') else a.get('answer_file')==b.get('answer_file')
    for p in PROFILES:
        record=load(roots[p]/'live/execution.json');slot=record['slots'][0];cmd=slot['command']
        checks[p+':one_attempt_no_retries']=len(record['slots'])==1 and slot['profile']==p and cmd[cmd.index('--n-attempts')+1]=='1' and cmd[cmd.index('--max-retries')+1]=='0'
        checks[p+':expected_model_and_task']=cmd[cmd.index('--model')+1]=='qwen3.8-max' and cmd[cmd.index('--include-task-name')+1]=='zvec-q005'
    for k,root in roots.items():checks[k+':original_all_files_unchanged']=assembly.inventory(root)==before[k]
    identity=load(new/'task-identity.json')
    report={'schema_version':1,'checked_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'run_id':RUN,'head_sha':HEAD,
        'method_sha256':sha(Path(__file__)),'plan_sha256':sha(args.plan),'checks':checks,'all_fidelity_checks_passed':all(checks.values()),
        'original_files_rehashed_before_and_after':{k:len(v) for k,v in before.items()},'native_collection_returncode':result,
        'native_structural_valid':col['structural_valid'],'reported_metric_pair_eligible':col['reported_metric_pair_eligible'],
        'native_task_identity_valid':identity['task_identity_valid'],'independent_worker_job_ids':[worker_receipts[p]['job_id'] for p in PROFILES],
        'independent_worker_runner_ids':[worker_receipts[p]['runner_id'] for p in PROFILES],
        'solver_calls':0,'docker_invoked':False,'private_assignment_or_grades_accessed':False,'shared_host_claimed':False,'aggregate_pair_time_invented':False,
        'scope':'Actual retained worker/joint origin, slot and byte fidelity plus original native accounting/task-identity replay. Strict source semantics and source-grounded quality need separate review. No favorable effect claim.'}
    write(OUT/'verification.json',report)
    print(json.dumps({k:report[k] for k in ['all_fidelity_checks_passed','native_collection_returncode','native_structural_valid','reported_metric_pair_eligible','native_task_identity_valid','solver_calls']}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--audit',action='store_true');args=parser.parse_args()
    if args.audit:audit()
    else:print(json.dumps({'syntax_prepared':True,'audit_executed':False,'new_solver_calls':0}))
