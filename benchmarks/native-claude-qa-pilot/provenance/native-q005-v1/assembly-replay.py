"""Offline assembly/collector fidelity check; no new solver or hardware trial."""
import argparse,copy,datetime,hashlib,importlib.util,json,shutil,sys
from pathlib import Path
from types import SimpleNamespace
parser=argparse.ArgumentParser(description=__doc__)
for key in ('repo-root','main-root','reference-prepared','candidate','source-pair','output'):parser.add_argument('--'+key,type=Path,required=True)
cli=parser.parse_args()
w=cli.repo_root.resolve();h=w/'benchmarks/native-claude-qa-pilot';main=cli.main_root.resolve()
sys.path[:0]=[str(h),str(w/'benchmarks/opencode-qa-pilot'),str(main)]
import live
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_bytes())
def write(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,ensure_ascii=False);f.write('\n')
source=cli.source_pair.resolve();raw=source/'raw/live';receipt=load(source/'artifact-byte-verification.json')
assert receipt['run_id']==39364155852 and receipt['pair_key']=='q001-r03' and receipt['complete'] and receipt['exact_extracted_inventory']
original_files={x['path'][5:]:x for x in receipt['files'] if x['path'].startswith('live/')}
for relative,row in original_files.items():assert sha(raw/relative)==row['sha256'] and (raw/relative).stat().st_size==row['bytes']
assert {str(p.relative_to(raw)) for p in raw.rglob('*') if p.is_file()}==set(original_files)
assert not any(p.is_symlink() for p in raw.rglob('*'))
assert sha(h/'live.py')=='6f625a7ee0c090063f295c780f2eefb275f5fa1a29d8137fcf4f076a6ee1906b'
out=cli.output.resolve();out.mkdir();record=load(raw/'execution.json');profiles=['baseline','zvec-grep'];assert [x['profile'] for x in record['slots']]==profiles
workers=[]
for profile in profiles:
 worker=out/profile;worker.mkdir();(worker/'runs').mkdir()
 for p in raw.iterdir():
  if p.is_file() and p.name.startswith(profile+'-'):shutil.copyfile(p,worker/p.name)
 shutil.copytree(raw/'runs'/('q001-'+profile),worker/'runs'/('q001-'+profile))
 r=copy.deepcopy(record);r['slots']=[x for x in r['slots'] if x['profile']==profile];write(worker/'execution.json',r);workers.append(worker)
import profile_assembly_v1 as assembly
joint=out/'assembled'
expected={k:record[k] for k in assembly.HEADER_KEYS}
assembly_receipt=assembly.assemble(dict(zip(profiles,workers)),joint,expected_header=expected)
assert assembly_receipt['slots_preserved_exactly'] and assembly_receipt['original_worker_files_unchanged']
reference=cli.reference_prepared.resolve();candidate=cli.candidate.resolve();base,proof=live.validate_plan(h/'live-plan-v2.json',main,reference,candidate)
plan=h/'repeat-plan-v1.json';assert sha(plan)==record['plan_sha256']
spec=importlib.util.spec_from_file_location('_assembly_replay_live',h/'live.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.PROTOCOL=record['protocol_id']
args=SimpleNamespace(output=joint,plan=plan,main_root=main,prepared=reference,candidate=candidate)
col=module.collect(args,base,proof);prior=load(raw/'collection/collection.json')
checks={}
for profile in profiles:
 new=next(x for x in col['profiles'] if x['profile']==profile);old=next(x for x in prior['profiles'] if x['profile']==profile)
 for k in ['answer_sha256','strict_usage','claude_reported_model_usage','harbor_timing','outer_slot_wall_seconds','native_init_tools','structural_valid','reported_metric_eligible']:
  checks[profile+':'+k]=new[k]==old[k]
 checks[profile+':answer_exact_bytes']=(joint/'collection'/new['answer_file']).read_bytes()==(raw/'collection'/old['answer_file']).read_bytes()
for relative,row in original_files.items():assert sha(raw/relative)==row['sha256']
assert all(checks.values()) and col['structural_valid'] and col['reported_metric_pair_eligible'] and col['same_native_tools_except_zg']
write(out/'verification.json',{'schema_version':1,'checked_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'original_run_id':39364155852,'original_pair_key':'q001-r03','original_artifact_receipt_sha256':sha(source/'artifact-byte-verification.json'),'original_live_files_rehashed_before_and_after':len(original_files),'original_files_changed':False,'runtime_collector_sha256':sha(h/'live.py'),'unchanged_collector_body':True,'production_assembly_sha256':sha(h/'profile_assembly_v1.py'),'production_assembly_used':True,'assembly_receipt_sha256':sha(joint/'profile-assembly.json'),'separately_split_fixture_records':2,'joined_slots':2,'checks':checks,'all_checks_passed':True,'structural_valid':col['structural_valid'],'same_native_tools_except_zg':col['same_native_tools_except_zg'],'reported_metric_pair_eligible':col['reported_metric_pair_eligible'],'solver_calls':0,'docker_invoked':False,'new_parallel_runtime_executed':False,'q005_execution_authorized':False,'scope':'Parser/assembly fidelity only using an old completed q001 pair. No hardware-allocation, source-scope, QA quality or new-case effect claim; original trials remain in their original cohort.'})
print(json.dumps({'checks_passed':sum(checks.values()),'original_live_files_unchanged':len(original_files),'structural_valid':col['structural_valid'],'reported_metric_pair_eligible':col['reported_metric_pair_eligible'],'new_solver_calls':0}))
