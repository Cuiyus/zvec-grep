"""Copy two exact final answers into the prospectively randomized packet."""
import argparse,datetime,hashlib,json,os,shutil,tempfile
from pathlib import Path,PurePosixPath
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
H=W/'benchmarks/native-claude-qa-pilot'
HEAD='19c532cfeddcdf4c9374539acf4c2be3b9eebfee'
PLAN='803750ff8050adf770325345b907ef662c578d6dff6e0fd480f92f78d3a1148d'
ASSIGNMENT='e272b6980c32711ff29016722de2d1099ec00682f8a54015ba32fb70a8715f25'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_bytes())
def write(p,d):
 with p.open('x') as f:json.dump(d,f,ensure_ascii=False,indent=2);f.write('\n')
def safe(root,relative):
 r=PurePosixPath(relative)
 if r.is_absolute() or '..' in r.parts or str(r)!=relative:raise ValueError('unsafe path')
 p=root/relative
 if p.is_symlink() or not p.is_file() or root.resolve() not in p.resolve().parents:raise ValueError('unsafe input')
 return p

def build(run):
 private=B/'native-q004-live-v1-grading-private';assignment=private/'assignment.json'
 assert sha(assignment)==ASSIGNMENT and sha(H/'architecture-live-plan-v1.json')==PLAN
 rows=load(assignment)['rows'];assert len(rows)==2 and {r['profile'] for r in rows}=={'baseline','zvec-grep'} and len({r['anonymous_id'] for r in rows})==2
 root=B/f'native-q004-live-v1-ci/{run}/pilot';receipt=load(root/'artifact-byte-verification.json')
 assert receipt['complete'] and receipt['exact_extracted_inventory'] and receipt['run_id']==run and receipt['head_sha']==HEAD and receipt['run_attempt']==1
 raw=root/'raw';context=load(raw/'live-q004-context.json')
 assert context['plan_sha256']==PLAN and context['task_id']=='q004' and context['dry_run'] is False
 collection=raw/'live/collection';col=load(collection/'collection.json')
 prepared=B/'native-architecture-q004-preparation-v1/prepared/q004'
 destination=B/'native-q004-live-v1-grading/packet';assert not destination.exists()
 temporary=Path(tempfile.mkdtemp(prefix='.packet-',dir=destination.parent))
 for src,name in [(W/'benchmarks/zvec-issue-qa/tasks/q004/instruction.md','question.md'),(W/'benchmarks/zvec-issue-qa/evaluator/q004.json','rubric.json'),(H/'architecture-grader-contract-v1.json','grader-contract.json'),(prepared/'source-manifest.json','source-manifest.json')]:shutil.copyfile(src,temporary/name)
 answers=[];bindings=[]
 for row in rows:
  matches=[r for r in col['profiles'] if r['profile']==row['profile']];assert len(matches)<=1
  record=matches[0] if matches else {};answer=None
  if record.get('answer_file'):
   answer=safe(collection,record['answer_file']);assert sha(answer)==record['answer_sha256']
  item={'blinded_answer_id':row['anonymous_id'],'file':row['anonymous_id']+'.txt' if answer else None,'answer_sha256':sha(answer) if answer else None,'bytes':answer.stat().st_size if answer else None,'final_answer_available':answer is not None}
  if answer:shutil.copyfile(answer,temporary/item['file'])
  answers.append(item);bindings.append({**row,'answer_sha256':item['answer_sha256'],'source':str(answer) if answer else None})
 metadata=load(prepared/'prepared.json')
 manifest={'schema_version':1,'task_id':'q004','source_commit':metadata['source_commit'],'source_inventory_sha256':metadata['source_inventory_sha256'],'source_root':str(prepared/'tasks/zvec-q004/environment/source'),'source_manifest_sha256':sha(temporary/'source-manifest.json'),'question_sha256':sha(temporary/'question.md'),'rubric_sha256':sha(temporary/'rubric.json'),'grader_contract_sha256':sha(temporary/'grader-contract.json'),'planned_answers':2,'available_answers':sum(x['final_answer_available'] for x in answers),'answers':answers,'scope':'Exact anonymized final answers and frozen grading inputs; no profile, trajectory, timing, usage or historical result. Missing answers remain unavailable.'}
 write(temporary/'manifest.json',manifest)
 os.replace(temporary,destination)
 write(private/'packet-construction.json',{'schema_version':1,'built_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'assignment_sha256':ASSIGNMENT,'run_id':run,'head_sha':HEAD,'plan_sha256':PLAN,'artifact_receipt_sha256':sha(root/'artifact-byte-verification.json'),'packet_manifest_sha256':sha(destination/'manifest.json'),'rows':bindings})
 os.chmod(private/'packet-construction.json',0o600)
 print(json.dumps({'packet':str(destination),'available_answers':manifest['available_answers'],'manifest_sha256':sha(destination/'manifest.json')}))
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-id',type=int,required=True);a=p.parse_args();build(a.run_id)
