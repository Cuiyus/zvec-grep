"""Preserve the complete q004 execution phase without reading future grades."""
import datetime, gzip, hashlib, json, os, shutil, tempfile
from pathlib import Path

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
DEST=W/'benchmarks/native-claude-qa-pilot/results/20261020-zvec-q004-repeat-v1/execution'
CI=B/'native-q004-repeat-v1-ci/39380248003'
AUDIT=B/'native-q004-repeat-v1-audit'

def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1048576), b''):h.update(chunk)
    return h.hexdigest()

def archive():
    assert not DEST.exists()
    check=json.loads((AUDIT/'execution-parent-verification.json').read_text())
    assert check['passed'] and check['pairs_verified']==10 and check['original_checks_verified']==730
    cohort=json.loads((AUDIT/'execution/cohort-execution-summary-v2.json').read_text())
    assert hashlib.sha256((AUDIT/'execution/cohort-execution-summary-v2.json').read_bytes()).hexdigest()=='17b6d9fd66de3da36bbb6ae9ec2fe39f33be8100b98997991005622dbf123b8d'
    groups=[(CI,'ci'),(AUDIT/'execution','independent-audit'),(B/'native-q004-repeat-v1-method','methods')]
    singles=[AUDIT/'verify_execution_reports_v1.py',AUDIT/'verify_execution_reports_v2.py',AUDIT/'execution-parent-method-correction.json',AUDIT/'execution-parent-verification.json']
    DEST.parent.mkdir(exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='.execution-',dir=DEST.parent));records=[]
    def copy(src,relative):
        assert src.is_file() and not src.is_symlink()
        identity={'original_path':str(src),'original_bytes':src.stat().st_size,'original_sha256':digest(src)}
        if src.name=='artifact.zip' or (src.name=='zg-install.tgz' and 'raw' in src.parts):
            records.append({**identity,'retention':'exact_original_retained_locally','archive_path':None,'reason':'Duplicate static install package or original API-digest-bound ZIP. All experimental contents and metadata are copied individually.'});return
        compress=src.suffix in ('.txt','.log','.jsonl') or src.stat().st_size>2_000_000
        target=temp/(relative+'.gz' if compress else relative);target.parent.mkdir(parents=True,exist_ok=True)
        if compress:
            with src.open('rb') as f, target.open('xb') as raw:
                with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as z:shutil.copyfileobj(f,z)
            h=hashlib.sha256();size=0
            with gzip.open(target,'rb') as f:
                for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk);size+=len(chunk)
            assert h.hexdigest()==identity['original_sha256'] and size==identity['original_bytes']
        else:shutil.copyfile(src,target);assert digest(target)==identity['original_sha256']
        records.append({**identity,'retention':'gzip_exact_restoration' if compress else 'exact_copy','archive_path':target.relative_to(temp).as_posix(),'archive_sha256':digest(target),'archive_bytes':target.stat().st_size})
    for root,prefix in groups:
        for src in sorted(root.rglob('*')):
            if src.is_file() and '__pycache__' not in src.parts:copy(src,prefix+'/'+src.relative_to(root).as_posix())
    for src in singles:copy(src,'parent-verification/'+src.name)
    manifest={'schema_version':1,'archived_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'run_id':39380248003,'head_sha':'3978d4bbbbb1e37961c277cdf76696b84c69e3bc','plan_sha256':'c2d102654a6f11ab330f843c501b1820c0c7385595e0fdb1d69635880f218c76','scope':'Immutable completed execution phase only. All 20 finals and complete raw trajectories are retained; source semantics and anonymous grading are still in progress and are not concluded by this phase. No private assignment or new anonymous IDs/grades read.','all_ten_original_artifact_receipts_retained':True,'all_experimental_raw_files_retained':True,'compression_restored_bytes_verified':True,'original_failed_verifier_and_superseded_cohort_summary_retained':True,'records':records}
    with (temp/'retained-evidence-manifest.json').open('x') as f:json.dump(manifest,f,indent=2);f.write('\n')
    manifest_sha=digest(temp/'retained-evidence-manifest.json');os.replace(temp,DEST)
    print(json.dumps({'archived_execution_pairs':10,'scheduled_solver_trials':20,'retained_records':len(records),'archive_bytes':sum(r.get('archive_bytes',0) for r in records),'manifest_sha256':manifest_sha,'quality_graded':False,'source_semantics_concluded':False}))

if __name__=='__main__':archive()
