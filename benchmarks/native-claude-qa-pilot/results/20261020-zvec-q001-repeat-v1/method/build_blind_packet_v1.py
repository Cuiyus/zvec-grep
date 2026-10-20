"""Build exact-byte anonymous packets; no grading, condition printout or dispatch."""
import argparse,collections,datetime,hashlib,json,os,shutil,sys,tempfile
from pathlib import Path,PurePosixPath
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot');H=W/'benchmarks/native-claude-qa-pilot'
ASSIGNMENT=B/'native-zvec-q001-repeat-v1-grading-private/assignment.json';ASSIGNMENT_SHA='fd370680e83301e2f3cd349cd988084078989d92a451396545794c404d789e28';PLAN_SHA='ee43d475b53e6290a3ddad8e670106ffbbab65536baefa6324ea9194aa3f3b79';RUN=39364155852;HEAD='aa230d91eb426eb295cf95fe0fa2d80d52f588e2'
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
 receipts=[B/'native-zvec-q001-repeat-v1-ci'/k/'artifact-byte-verification.json' for k in pair_keys]
 if not all(p.is_file() for p in receipts):
  print(json.dumps({'task_id':task,'ready':False,'retained_pairs':sum(p.is_file() for p in receipts),'required_pairs':10}));return
 assert h(H/'q001-repeat-plan-v1.json')==PLAN_SHA and h(ASSIGNMENT)==ASSIGNMENT_SHA
 plan=load(H/'q001-repeat-plan-v1.json');assert plan['grading_policy']['assignment_sha256']==ASSIGNMENT_SHA
 assignment=load(ASSIGNMENT);rows=assignment['rows'];assert len(rows)==20 and len({r['anonymous_id'] for r in rows})==20
 for t in ['q001']:
  for p in ['baseline','zvec-grep']:assert {r['trial_index'] for r in rows if r['task_id']==t and r['profile']==p}==set(range(1,11))
 selected=[r for r in rows if r['task_id']==task];assert len(selected)==20
 packet_root=B/'native-zvec-q001-repeat-v1-blind-packets';packet_root.mkdir(exist_ok=True);destination=packet_root/task
 if destination.exists():raise FileExistsError('packet_already_exists')
 private=ASSIGNMENT.parent;construction=private/(task+'-packet-construction.json')
 if construction.exists():raise FileExistsError('construction_exists')
 out=Path(tempfile.mkdtemp(prefix='.'+task+'-',dir=packet_root));public=[];private_rows=[]
 prepared=B/'native-claude-live-preflight-v1/prepared' if task=='q001' else B/'native-expansion-prepared-v1'/task
 source=prepared/'tasks'/('zvec-'+task)/'environment/source';task_record=next(t for t in load(H/'repeat-plan-v1.json')['tasks'] if t['id']==task)
 for key,filename in [('instruction','question.md'),('evaluator','rubric.json')]:
  src=safe(W/'benchmarks/zvec-issue-qa',task_record[key]);assert h(src)==task_record[key+'_sha256'];shutil.copyfile(src,out/filename)
 contract=H/('grader-contract-v1.json' if task=='q001' else 'expansion-grader-contract-v1.json');shutil.copyfile(contract,out/'grader-contract.json');shutil.copyfile(prepared/'source-manifest.json',out/'source-manifest.json')
 for row in selected:
  key=f'{task}-r{row["trial_index"]:02d}';pair=B/'native-zvec-q001-repeat-v1-ci'/key;receipt=load(pair/'artifact-byte-verification.json')
  assert receipt['complete'] and receipt['exact_extracted_inventory'] and receipt['run_id']==RUN and receipt['head_sha']==HEAD and receipt['run_attempt']==1 and receipt['pair_key']==key
  raw=pair/'raw';context_path=raw/'live-q001-repeat-context.json';context=load(context_path) if context_path.is_file() else None
  if context is not None:assert context['cohort_plan_sha256']==PLAN_SHA and context['task_id']==task and context['trial_index']==row['trial_index'] and context['dry_run'] is False
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
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task',choices=['q001'],required=True);a=p.parse_args();build(a.task)
