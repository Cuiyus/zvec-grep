#!/usr/bin/env python3
"""Correct only the host-lexical context replay; preserve first audit and raw bytes."""
from pathlib import Path,PurePosixPath
from types import SimpleNamespace
import copy,datetime,hashlib,importlib.util,inspect,json,sys,textwrap
sys.dont_write_bytecode=True
OUT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_q005_original_method',OUT/'q005_execution_audit_v1.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ORIGINAL_SHA='b78bd6276fe20cb60db2146597c0a5d70f082bc4b9bcd6aa3eae3db3734544b1'
METHOD_SHA='1330b52942923f5973864967defaab8a1d7287aeb7cfbbae3d01f2f4c2840d83'
previous=OUT/'execution-review.json';destination=OUT/'execution-review-v2.json'
assert m.sha(previous)==ORIGINAL_SHA and m.sha(OUT/'q005_execution_audit_v1.py')==METHOD_SHA
assert not destination.exists()
original=m.load(previous);result=copy.deepcopy(original)
failed=[p+':frozen_context_controls_and_single_native_slot' for p in m.PROFILES]
assert original['failed_checks']==failed
assert all(c['passed'] for c in original['checks'] if c['check'] not in failed)
source=textwrap.dedent(inspect.getsource(m.runtime.worker_context))
assert source.count('.resolve().as_posix()')==2
adapted=source.replace('.resolve().as_posix()','.as_posix()')
namespace=dict(m.runtime.__dict__);exec(compile(adapted,str(Path(__file__))+'[recorded-posix-context]','exec'),namespace)
lexical_context=namespace['worker_context']
r=m.CI/'joint/raw';pre=m.CI/'preflight/raw';plan=m.load(r/'runtime-live-plan-v1.json');base=m.load(r/'live-plan-v2.json')
common=['runtime-live-plan-v1.json','runtime-execution-decision-v1.json','runtime-grader-contract-v1.json','live-plan-v2.json','inputs.json','q005-prepared-group.json','q005-prepared.json','q005-source-manifest.json','q001-reference-prepared.json','package-derivation.json']
contexts={}
for profile in m.PROFILES:
    wr=m.CI/profile/'raw';context=m.load(wr/'live-q005-worker-context.json');record=m.load(wr/'live/execution.json')
    expected=lexical_context(SimpleNamespace(plan=r/'runtime-live-plan-v1.json',prepared_group=PurePosixPath('/home/runner/work/_temp/qa-runtime'),output=PurePosixPath('/home/runner/work/_temp/native-claude-q005-v1-pilot/live')),plan,profile,dry_run=False)
    controls=all((wr/n).read_bytes()==(r/n).read_bytes()==(pre/n).read_bytes() for n in common)
    passed=context==expected and controls and len(record['slots'])==1 and record['slots'][0]['profile']==profile and record['protocol_id']==m.PROTOCOL and record['plan_sha256']==m.PLAN_SHA and record['offline_proof_sha256']==base['offline_proof']['proof_sha256'] and record['offline_inputs_sha256']==base['offline_proof']['inputs_sha256'] and record['dry_run'] is False
    check=next(c for c in result['checks'] if c['check']==profile+':frozen_context_controls_and_single_native_slot')
    check['passed']=passed
    check['observed']['read_only_host_path_correction']='Frozen worker_context body, exactly two resolve().as_posix() calls replaced by as_posix() on explicit recorded PurePosixPath inputs; Linux paths are not resolved through the macOS filesystem.'
    contexts[profile]={'actual':context,'expected':expected,'all_retained_control_bytes_equal':controls,'all_original_predicates_passed':passed}
correction_checks=[]
def check(name,passed,**details):correction_checks.append({'check':name,'passed':bool(passed),'details':details})
check('all_original_context_predicates_pass_after_host_lexical_replay',all(v['all_original_predicates_passed'] for v in contexts.values()))
bad=[]
for key,entry in original['evidence_catalog'].items():
    p=Path(key)
    if p.is_symlink() or not p.is_file() or p.stat().st_size!=entry.get('bytes') or m.sha(p)!=entry.get('sha256'):bad.append(key)
check('entire_original_evidence_catalog_unchanged',not bad,entries=len(original['evidence_catalog']),mismatches=bad)
raw_counts={};raw_bad=[]
for key in m.KEYS:
    c=m.CI/key;receipt=m.load(c/'artifact-byte-verification.json');raw=c/'raw'
    expected={f['path']:f for f in receipt['files']}
    actual=sorted(p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file())
    if receipt.get('complete') is not True or receipt.get('exact_extracted_inventory') is not True or len(expected)!=len(receipt['files']) or sorted(expected)!=actual or any(p.is_symlink() for p in raw.rglob('*')):raw_bad.append(key+':inventory')
    for name,f in expected.items():
        p=raw/name
        if not p.is_file() or p.stat().st_size!=f['bytes'] or m.sha(p)!=f['sha256']:raw_bad.append(key+':'+name)
    raw_counts[key]=len(expected)
check('all_four_original_raw_inventories_rehashed_without_changes',not raw_bad,file_counts=raw_counts,mismatches=raw_bad)
replay_path=OUT/'production-replay/verification.json';replay=m.load(replay_path)
check('previous_production_replay_confirmed_without_reexecution',replay['all_fidelity_checks_passed'] is True and all(replay['checks'].values()) and replay['native_collection_returncode']==0 and replay['solver_calls']==0 and replay['docker_invoked'] is False and replay['shared_host_claimed'] is False and replay['aggregate_pair_time_invented'] is False,path=str(replay_path),sha256=m.sha(replay_path),checks=len(replay['checks']))
assert result['profiles']==original['profiles'] and result['evidence_catalog']==original['evidence_catalog']
assert [c for c in result['checks'] if c['check'] not in failed]==[c for c in original['checks'] if c['check'] not in failed]
result['reviewed_at_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
result['failed_checks']=[c['check'] for c in result['checks'] if not c['passed']]
result['passed_checks']=sum(c['passed'] for c in result['checks'])
result['execution_valid']=all(c['passed'] for c in result['checks'] if c['category']!='metric') and all(c['passed'] for c in correction_checks)
result['reported_metric_pair_eligible']=bool(result['execution_valid'] and result['metric_reconciliation_valid'] and all(p.get('reported_metric_eligible') is True for p in result['profiles']))
result['passed']=all(c['passed'] for c in result['checks']) and all(c['passed'] for c in correction_checks)
result['audit_revision']={'previous_report_path':str(previous),'previous_report_sha256':ORIGINAL_SHA,'correction_method_path':str(Path(__file__)),'correction_method_sha256':m.sha(Path(__file__)),'reason':'The original audit resolved recorded Linux /home paths against macOS, introducing /System/Volumes/Data. Production context and all raw bytes were correct. Corrected only the reviewer host-path model; native source, plan, source/task/rubric/budgets and execution evidence are unchanged.','unadapted_worker_context_source_sha256':hashlib.sha256(source.encode()).hexdigest(),'lexical_worker_context_source_sha256':hashlib.sha256(adapted.encode()).hexdigest(),'contexts':contexts,'checks':correction_checks,'all_other_94_original_checks_and_metrics_unchanged':True,'production_replayed_again':False,'solver_calls':0}
m.write(destination,result)
print(json.dumps({'path':str(destination),'sha256':m.sha(destination),'passed':result['passed'],'original_checks':result['check_count'],'passed_original_checks':result['passed_checks'],'correction_checks':len(correction_checks),'failed_checks':result['failed_checks'],'original_evidence_catalog_entries':len(result['evidence_catalog']),'raw_file_counts':raw_counts},indent=2))
raise SystemExit(0 if result['passed'] else 1)
