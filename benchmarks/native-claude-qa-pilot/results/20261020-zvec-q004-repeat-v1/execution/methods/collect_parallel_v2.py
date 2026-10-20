"""Read-only collection of every fixed repeat artifact, including failed pairs."""
import argparse,datetime,hashlib,json,os,stat,subprocess,zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path,PurePosixPath
REPOSITORY='Cuiyus/zvec-grep'
KEYS=[f'q004-r{i:02d}' for i in range(1,11)]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def api(endpoint):return json.loads(subprocess.run(['gh','api',endpoint],capture_output=True,text=True,check=True).stdout)
def write(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def same_or_write(p,x):
 if p.exists():assert json.loads(p.read_text())==x
 else:write(p,x)
def collect(run_id,head,root):
 root.mkdir(parents=True,exist_ok=True)
 run=api(f'repos/{REPOSITORY}/actions/runs/{run_id}')
 assert run['head_sha']==head and run['run_attempt']==1 and run['event'] in ('workflow_dispatch','push') and run['repository']['full_name']==REPOSITORY
 assert run['path']=='.github/workflows/native-claude-q004-repeat-v1.yml'
 jobs=api(f'repos/{REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100')
 artifacts=api(f'repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100')
 assert jobs['total_count']<=100 and artifacts['total_count']<=100
 new=[]
 def collect_one(key):
  out=root/key
  if (out/'artifact-byte-verification.json').is_file():return None
  matches=[a for a in artifacts['artifacts'] if a['name']==f'native-q004-repeat-v1-{run_id}-{key}']
  if not matches:return None
  assert run['event']=='workflow_dispatch' or key=='preflight'
  assert len(matches)==1;meta=matches[0]
  jm=[j for j in jobs['jobs'] if j['name']==('preflight' if key=='preflight' else 'repeat ('+key+')')]
  if not jm or jm[0]['status']!='completed':return None
  assert len(jm)==1;job=jm[0]
  assert job['head_sha']==head and job['run_id']==run_id and job['run_attempt']==1
  assert meta['workflow_run']['head_sha']==head and meta['workflow_run']['id']==run_id
  out.mkdir(exist_ok=True)
  zp=out/'artifact.zip'
  if not zp.exists():
   tmp=out/'artifact.zip.partial'
   with tmp.open('wb') as f:subprocess.run(['gh','api',f'repos/{REPOSITORY}/actions/artifacts/{meta["id"]}/zip'],stdout=f,stderr=subprocess.PIPE,check=True)
   os.replace(tmp,zp)
  digest=sha(zp);assert meta['digest']=='sha256:'+digest and zp.stat().st_size==meta['size_in_bytes']
  raw=out/'raw';raw.mkdir(exist_ok=True)
  with zipfile.ZipFile(zp) as z:
   infos=z.infolist();names=[i.filename for i in infos if not i.is_dir()]
   assert len(set(names))==len(names) and z.testzip() is None
   for info in infos:
    p=PurePosixPath(info.filename)
    assert not p.is_absolute() and '..' not in p.parts and str(p)==info.filename.rstrip('/') and not stat.S_ISLNK(info.external_attr>>16)
    if info.is_dir():continue
    target=raw/info.filename;target.parent.mkdir(parents=True,exist_ok=True);content=z.read(info)
    if target.exists():assert not target.is_symlink() and target.read_bytes()==content
    else:
     with target.open('xb') as f:f.write(content)
   actual=sorted(p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file())
   assert sorted(names)==actual and not any(p.is_symlink() for p in raw.rglob('*'))
   files=[{'path':n,'bytes':(raw/n).stat().st_size,'sha256':sha(raw/n),'matches_zip':True} for n in actual]
  for name,data in [('artifact-metadata.json',meta),('run-metadata.json',run),('job-metadata.json',jobs)]:same_or_write(out/name,data)
  receipt={'schema_version':1,'complete':True,'exact_extracted_inventory':True,'repository':REPOSITORY,'event':run['event'],'run_id':run_id,'head_sha':head,'run_attempt':1,'artifact_key':key,'pair_key':None if key=='preflight' else key,'task':None if key=='preflight' else 'q004','trial_index':None if key=='preflight' else int(key.rsplit('r',1)[1]),'job_id':job['id'],'job_conclusion':job['conclusion'],'artifact_id':meta['id'],'artifact_sha256':digest,'artifact_bytes':zp.stat().st_size,'matches_api_digest':True,'artifact_metadata_sha256':sha(out/'artifact-metadata.json'),'files':files}
  write(out/'artifact-byte-verification.json',receipt);return key
 with ThreadPoolExecutor(max_workers=3) as executor:
  new=[value for value in executor.map(collect_one,KEYS+['preflight']) if value is not None]
 completed=[k for k in KEYS if (root/k/'artifact-byte-verification.json').is_file()]
 status={'schema_version':1,'checked_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'run_id':run_id,'head_sha':head,'run_status':run['status'],'run_conclusion':run['conclusion'],'new_artifacts':new,'retained_pairs':completed,'retained_count':len(completed),'planned_pairs':10,'jobs':[{'name':j['name'],'id':j['id'],'status':j['status'],'conclusion':j['conclusion']} for j in jobs['jobs']]}
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');write(root/('status-'+stamp+'.json'),status)
 if run['status']=='completed':
  for name,data in [('final-run-metadata.json',run),('final-job-metadata.json',jobs),('final-artifacts-metadata.json',artifacts)]:same_or_write(root/name,data)
 print(json.dumps({'run_status':run['status'],'run_conclusion':run['conclusion'],'new_artifacts':new,'retained_pairs':len(completed),'planned_pairs':10}))
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-id',type=int,required=True);p.add_argument('--head',required=True);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
 a=p.parse_args();collect(a.run_id,a.head,a.root)
