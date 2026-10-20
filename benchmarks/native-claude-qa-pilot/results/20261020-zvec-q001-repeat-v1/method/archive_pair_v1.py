"""Copy immutable per-pair evidence; retain duplicate large binaries locally."""
import argparse,datetime,hashlib,json,os,shutil,tempfile
from pathlib import Path
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot');W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');A=B/'native-zvec-q001-repeat-v1-audit';CI=B/'native-zvec-q001-repeat-v1-ci';R=W/'benchmarks/native-claude-qa-pilot/results/20261020-zvec-q001-repeat-v1'

def load(p):return json.loads(p.read_text())
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return {'sha256':h.hexdigest(),'bytes':p.stat().st_size}
def write(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')

def archive(key):
 ci=CI/key;receipt=load(ci/'artifact-byte-verification.json');assert receipt['pair_key']==key and receipt['complete'] is True and receipt['run_id']==39364155852 and receipt['head_sha']=='aa230d91eb426eb295cf95fe0fa2d80d52f588e2'
 assert (A/'reviews'/key/'execution-review.json').is_file() and (A/'source-reviews'/key/'report.json').is_file()
 destination=R/'pairs'/key;destination.parent.mkdir(parents=True,exist_ok=True)
 if destination.exists():raise FileExistsError('pair_archive_already_exists')
 temporary=Path(tempfile.mkdtemp(prefix='.'+key+'-',dir=destination.parent));copies=[];omitted=[]
 groups=[(ci,'ci'),(A/'reviews'/key,'execution-audit'),(A/'scope'/key,'tool-evidence'),(A/'source-reviews'/key,'source-review')]
 for root,prefix in groups:
  for source in sorted(root.rglob('*')):
   if source.is_dir():continue
   assert source.is_file() and not source.is_symlink()
   relative=source.relative_to(root);target=Path(prefix)/relative;identity=digest(source)
   if prefix=='ci' and str(relative) in ('artifact.zip','raw/zg-install.tgz'):
    expected=receipt['artifact_sha256'] if str(relative)=='artifact.zip' else next(i['sha256'] for i in receipt['files'] if i['path']=='zg-install.tgz')
    assert identity['sha256']==expected
    omitted.append({'path':str(target),'original_path':str(source),**identity,'reason':'Original ZIP/package retained locally; API digest and all-file byte verification retained in Git. Duplicate large binary omitted from Git.'});continue
   target=temporary/target;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
   assert digest(target)==identity
   copies.append({'path':str(target.relative_to(temporary)),'original_path':str(source),**identity})
 write(temporary/'retention.json',{'schema_version':1,'archived_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pair_key':key,'task_id':'q001','run_id':receipt['run_id'],'execution_head':receipt['head_sha'],'copied_files':len(copies),'copies':copies,'large_files_retained_locally':omitted,'quality_graded':False,'scope':'Immutable execution, usage and strict source review evidence; original blinded grades and full-cohort conclusions are separate.'})
 files=[{'path':str(p.relative_to(temporary)),**digest(p)} for p in sorted(temporary.rglob('*')) if p.is_file()]
 write(temporary/'manifest.json',{'schema_version':1,'files':files});os.replace(temporary,destination)
 print(json.dumps({'pair_key':key,'copied_files':len(copies),'archived_bytes':sum(i['bytes'] for i in files),'large_originals_retained_locally':len(omitted),'manifest_sha256':digest(destination/'manifest.json')['sha256']}))

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--keys',nargs='+',required=True,choices=[f'q001-r{i:02d}' for i in range(1,11)]);args=p.parse_args()
 for key in args.keys:archive(key)
