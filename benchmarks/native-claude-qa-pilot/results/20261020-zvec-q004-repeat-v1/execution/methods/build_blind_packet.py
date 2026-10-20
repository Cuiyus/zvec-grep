"""Build exact-byte anonymous packets; no grading, condition printout or dispatch."""
import argparse,collections,datetime,hashlib,json,os,shutil,sys,tempfile,zipfile
from pathlib import Path,PurePosixPath
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot');H=W/'benchmarks/native-claude-qa-pilot'
ASSIGNMENT=B/'native-q004-repeat-v1-grading-private/assignment.json';ASSIGNMENT_SHA='ee7284ebb7531c79023f45a132f37ff6b6e608337ea3321a1f7564c40ed79e47';PLAN_SHA='c2d102654a6f11ab330f843c501b1820c0c7385595e0fdb1d69635880f218c76';RUN=39380248003;HEAD='3978d4bbbbb1e37961c277cdf76696b84c69e3bc'
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def write(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def safe(root,relative):
 p=PurePosixPath(relative)
 if not relative or p.is_absolute() or '..' in p.parts or str(p)!=relative:raise ValueError('unsafe_relative')
 path=root/relative
 if path.is_symlink() or not path.is_file() or root.resolve() not in path.resolve().parents:raise ValueError('unsafe_input')
 return path

def build(task):
 pair_keys=[f'{task}-r{i:02d}' for i in range(1,11)]
 receipts=[B/'native-q004-repeat-v1-ci/39380248003'/k/'artifact-byte-verification.json' for k in pair_keys]
 if not all(p.is_file() for p in receipts):
  print(json.dumps({'task_id':task,'ready':False,'retained_pairs':sum(p.is_file() for p in receipts),'required_pairs':10}));return
 assert h(H/'architecture-repeat-plan-v1.json')==PLAN_SHA and h(ASSIGNMENT)==ASSIGNMENT_SHA
 plan=load(H/'architecture-repeat-plan-v1.json');assert plan['grading_policy']['assignment_sha256']==ASSIGNMENT_SHA
 assignment=load(ASSIGNMENT);rows=assignment['rows'];assert len(rows)==20 and len({r['anonymous_id'] for r in rows})==20
 for t in ['q004']:
  for p in ['baseline','zvec-grep']:assert {r['repetition'] for r in rows if r['task_id']==t and r['profile']==p}==set(range(1,11))
 selected=[r for r in rows if r['task_id']==task];assert len(selected)==20
 packet_root=B/'native-q004-repeat-v1-grading';packet_root.mkdir(exist_ok=True);destination=packet_root/task
 if destination.exists():raise FileExistsError('packet_already_exists')
 private=ASSIGNMENT.parent;construction=private/(task+'-packet-construction.json')
 if construction.exists():raise FileExistsError('construction_exists')
 out=Path(tempfile.mkdtemp(prefix='.'+task+'-',dir=packet_root));public=[];private_rows=[];verified={}
 prepared=B/'native-architecture-q004-preparation-v1/prepared/q004'
 source=prepared/'tasks'/('zvec-'+task)/'environment/source';metadata=load(prepared/'prepared.json')
 task_record={'source_commit':metadata['source_commit'],'source_inventory_sha256':metadata['source_inventory_sha256'],
              'instruction':metadata['question']['path'],'instruction_sha256':metadata['question']['sha256'],
              'evaluator':metadata['rubric']['path'],'evaluator_sha256':metadata['rubric']['sha256']}
 inventory=load(prepared/'source-manifest.json');files=inventory['files']
 assert len(files)==1596 and inventory['inventory_sha256']==task_record['source_inventory_sha256']=='129579aae937735ed79c444bd6f83a05e42058a679e0077ff39599c9ce5d4eef'
 assert sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file())==sorted(r['path'] for r in files)
 assert not any(p.is_symlink() for p in source.rglob('*'))
 for record in files:
  source_file=safe(source,record['path']);assert h(source_file)==record['sha256'] and source_file.stat().st_size==record['bytes']
 for key,filename in [('instruction','question.md'),('evaluator','rubric.json')]:
  src=safe(W/'benchmarks/zvec-issue-qa',task_record[key]);assert h(src)==task_record[key+'_sha256'];shutil.copyfile(src,out/filename)
 contract=H/'architecture-grader-contract-v1.json';shutil.copyfile(contract,out/'grader-contract.json');shutil.copyfile(prepared/'source-manifest.json',out/'source-manifest.json')
 for row in selected:
  key=f'{task}-r{row["repetition"]:02d}';pair=B/'native-q004-repeat-v1-ci/39380248003'/key;receipt=load(pair/'artifact-byte-verification.json')
  assert receipt['complete'] and receipt['exact_extracted_inventory'] and receipt['run_id']==RUN and receipt['head_sha']==HEAD and receipt['run_attempt']==1 and receipt['pair_key']==key
  raw=pair/'raw'
  if key not in verified:
   zp=pair/'artifact.zip';meta=load(pair/'artifact-metadata.json')
   assert h(zp)==receipt['artifact_sha256'] and meta['digest']=='sha256:'+receipt['artifact_sha256'] and meta['size_in_bytes']==zp.stat().st_size
   with zipfile.ZipFile(zp) as archive:
    for record in receipt['files']:
     f=safe(raw,record['path']);assert h(f)==record['sha256'] and f.stat().st_size==record['bytes'] and archive.read(record['path'])==f.read_bytes()
   verified[key]=True
  context_path=raw/'live-q004-repeat-context.json';context=load(context_path) if context_path.is_file() else None
  if context is not None:assert context['plan_sha256']==PLAN_SHA and context['task_id']==task and context['trial_index']==row['repetition'] and context['dry_run'] is False and context['protocol_id']=='native-claude-zvec-q004-repeat-v1' and context['pair_key']==key
  collection_path=raw/'live/collection/collection.json';answer=None;reason=None
  if context is None:reason='no_execution_context_or_exact_final_answer_available'
  elif collection_path.is_file():
   matches=[p for p in load(collection_path)['profiles'] if p['profile']==row['profile']]
   assert len(matches)<=1
   if matches and matches[0].get('answer_file'):
    cp=matches[0];candidate=safe(raw/'live/collection',cp['answer_file']);assert h(candidate)==cp['answer_sha256'];answer=candidate
   else:reason='no_exact_final_answer_available'
  else:reason='collection_not_available'
  item={'blinded_answer_id':row['anonymous_id'],'file':row['anonymous_id']+'.txt' if answer else None,'answer_sha256':h(answer) if answer else None,'bytes':answer.stat().st_size if answer else None,'final_answer_available':answer is not None}
  if answer:shutil.copyfile(answer,out/item['file']);assert h(out/item['file'])==item['answer_sha256']
  public.append(item);private_rows.append({**row,'pair_key':key,'source_path':str(answer) if answer else None,'absence_reason':reason,'answer_sha256':item['answer_sha256'],'receipt_sha256':h(pair/'artifact-byte-verification.json')})
 manifest={'schema_version':1,'task_id':task,'scope':'Anonymous exact final answers and original grading inputs only; no conditions, repeat IDs, trajectories or resource totals. Absence is retained as unavailable and is never an invented zero score.','source_commit':task_record['source_commit'],'source_root':str(source),'source_inventory_sha256':task_record['source_inventory_sha256'],'source_manifest_sha256':h(out/'source-manifest.json'),'question_sha256':h(out/'question.md'),'rubric_sha256':h(out/'rubric.json'),'grader_contract_sha256':h(out/'grader-contract.json'),'answers':public,'planned_answers':20,'available_answers':sum(a['final_answer_available'] for a in public),'quality_or_scope_judgments_included':False}
 write(out/'manifest.json',manifest)
 record={'schema_version':1,'built_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'task_id':task,'assignment_sha256':ASSIGNMENT_SHA,'plan_sha256':PLAN_SHA,'run_id':RUN,'head_sha':HEAD,'packet_manifest_sha256':h(out/'manifest.json'),'builder_sha256':h(Path(__file__)),'rows':private_rows}
 os.replace(out,destination);write(construction,record);os.chmod(construction,0o600)
 print(json.dumps({'task_id':task,'ready':True,'packet':str(destination),'manifest_sha256':h(destination/'manifest.json'),'planned_answers':20,'available_answers':manifest['available_answers'],'quality_graded':False}))
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task',choices=['q004'],required=True);a=p.parse_args();build(a.task)
