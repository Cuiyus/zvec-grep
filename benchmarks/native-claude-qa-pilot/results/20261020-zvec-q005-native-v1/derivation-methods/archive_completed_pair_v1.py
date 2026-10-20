"""Retain exact q005 outputs, audits, unblinded grades and analysis in the fork."""
import datetime, gzip, hashlib, json, os, shutil, tempfile
from pathlib import Path

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
H=W/'benchmarks/native-claude-qa-pilot'
D=H/'results/20261020-zvec-q005-native-v1'

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()

def load(p):return json.loads(p.read_text())
def write(p,d):
    with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')

assert not D.exists()
final=load(B/'native-q005-live-v1-final-analysis/analysis-report.json')
assert final['retained_solver_executions']==2 and not final['original_valid_pair_candidate']
assert final['original_quality_points']=={'baseline':10,'zvec-grep':10}
ci=B/'native-q005-live-v1-ci/39383511290'
original_files=0
for key in ('preflight','baseline','zvec-grep','joint'):
    folder=ci/key;r=load(folder/'artifact-byte-verification.json');metadata=load(folder/'artifact-metadata.json')
    assert r['complete'] and r['exact_extracted_inventory'] and r['matches_api_digest']
    assert r['run_id']==39383511290 and r['head_sha']==final['execution_head_sha']
    assert sha(folder/'artifact.zip')==r['artifact_sha256'] and metadata['digest']=='sha256:'+r['artifact_sha256']
    raw=folder/'raw'
    assert sorted(p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file())==sorted(f['path'] for f in r['files'])
    for row in r['files']:
        p=raw/row['path'];assert sha(p)==row['sha256'] and p.stat().st_size==row['bytes']
    original_files+=len(r['files'])

D.parent.mkdir(exist_ok=True)
temp=Path(tempfile.mkdtemp(prefix='.q005-',dir=D.parent));records=[]
def copy(src,relative):
    assert src.is_file() and not src.is_symlink()
    identity={'original_path':str(src),'original_sha256':sha(src),'original_bytes':src.stat().st_size}
    if src.suffix=='.zip' or src.name=='zg-install.tgz':
        records.append({**identity,'retention':'exact_original_retained_locally','archive_path':None,'reason':'Original digest-bound artifact ZIP or duplicate reusable static install input. All experimental records are archived individually; original container bytes remain retained at the stated path.'})
        return
    compress=src.suffix in ('.txt','.log','.jsonl') or src.stat().st_size>2_000_000
    target=temp/(relative+'.gz' if compress else relative);target.parent.mkdir(parents=True,exist_ok=True)
    if compress:
        with src.open('rb') as f,target.open('xb') as raw:
            with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as z:shutil.copyfileobj(f,z)
        h=hashlib.sha256();size=0
        with gzip.open(target,'rb') as f:
            for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk);size+=len(chunk)
        assert h.hexdigest()==identity['original_sha256'] and size==identity['original_bytes']
    else:
        shutil.copyfile(src,target);assert sha(target)==identity['original_sha256']
    records.append({**identity,'retention':'gzip_exact_restoration' if compress else 'exact_copy','archive_path':target.relative_to(temp).as_posix(),'archive_bytes':target.stat().st_size,'archive_sha256':sha(target)})

groups=[('native-q005-live-v1-ci','execution-ci'),('native-q005-live-v1-audit','independent-and-parent-audits'),('native-q005-live-v1-grading','grading'),('native-q005-live-v1-grading-private','unblinded-assignment'),('native-q005-live-v1-final-analysis','analysis'),('native-q005-live-v1-method','derivation-methods')]
for folder,prefix in groups:
    root=B/folder
    for src in sorted(root.rglob('*')):
        if src.is_file() and '__pycache__' not in src.parts:copy(src,prefix+'/'+src.relative_to(root).as_posix())
for name in ('runtime-live-plan-v1.json','runtime-grader-contract-v1.json','repeat-scope-policy-v1.json','question-bank-scope-v1.json'):
    copy(H/name,'frozen-controls/'+name)

manifest={'schema_version':1,'archived_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'task_id':'q005','run_id':39383511290,'execution_head_sha':final['execution_head_sha'],'original_artifact_count':4,'original_artifact_raw_file_count':original_files,'all_original_experimental_records_retained':True,'all_gzip_restored_bytes_verified':True,'static_install_and_artifact_container_duplicates_retained_locally_by_hash':True,'independent_profile_jobs_not_claimed_shared_host':True,'scope':'Completed fixed-bank q005 minimal development pair. Both independently graded answers score 10/10. Original strict scope gate fails for both profiles. No stable favorable conclusion and no additional model runs.','records':records}
write(temp/'retained-evidence-manifest.json',manifest)
summary={**final,'retained_evidence_manifest_sha256':sha(temp/'retained-evidence-manifest.json'),'archive_records':len(records),'archive_bytes':sum(r.get('archive_bytes',0) for r in records),'original_artifact_raw_file_count':original_files}
write(temp/'summary.json',summary)
os.replace(temp,D)
print(json.dumps({'archive_path':str(D),'records':len(records),'archive_bytes':summary['archive_bytes'],'original_raw_files':original_files,'manifest_sha256':summary['retained_evidence_manifest_sha256']}))
