"""Retain the completed q004 grading, explicit unblinding and final analysis phase."""
import datetime, gzip, hashlib, json, os, shutil, tempfile
from pathlib import Path

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
DEST=W/'benchmarks/native-claude-qa-pilot/results/20261020-zvec-q004-repeat-v1/grading-analysis'
CI=B/'native-q004-repeat-v1-ci/39380248003'
AUDIT=B/'native-q004-repeat-v1-audit'

def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1048576), b''):h.update(chunk)
    return h.hexdigest()

def archive():
    assert not DEST.exists()
    check=json.loads((B/'native-q004-repeat-v1-grading/parent-grading-verification.json').read_text())
    assert check['both_report_records_verified'] and sum(r['answers_verified'] for r in check['reports'])==40
    final=json.loads((B/'native-q004-repeat-v1-final-analysis/analysis-report.json').read_text())
    assert final['retained_solver_executions']==20 and final['primary_unknown_scores']==4 and final['primary_unknown_quality_decisions']==4
    groups=[(B/'native-q004-repeat-v1-grading','grading'),(B/'native-q004-repeat-v1-final-analysis','analysis'),(B/'native-q004-repeat-v1-grading-private','unblinded-assignment')]
    singles=[AUDIT/'verify_completed_grades_v1.py',AUDIT/'analyze_completed_cohort_v1.py',AUDIT/'finish_completed_analysis_v2.py',AUDIT/'analysis-numeric-reconciliation-correction.json',Path(__file__)]
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
    manifest={'schema_version':1,'archived_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'run_id':39380248003,'head_sha':'3978d4bbbbb1e37961c277cdf76696b84c69e3bc','plan_sha256':'c2d102654a6f11ab330f843c501b1820c0c7385595e0fdb1d69635880f218c76','scope':'Completed independent anonymous grading, comparison, parent quote/source arithmetic verification, explicit post-seal unblinding and final fixed-cohort analysis. Four material original-rule score/quality unknowns are retained; the complete primary score-trim statistic is unavailable. Original primary numeric interpretation is a separate diagnostic only. No favorable valid benchmark conclusion.','all_twenty_original_anonymous_answers_and_two_original_reports_retained':True,'all_post_seal_unblinding_and_analysis_inputs_retained':True,'compression_restored_bytes_verified':True,'original_numeric_equality_failure_method_and_narrow_continuation_retained':True,'records':records}
    with (temp/'retained-evidence-manifest.json').open('x') as f:json.dump(manifest,f,indent=2);f.write('\n')
    manifest_sha=digest(temp/'retained-evidence-manifest.json');os.replace(temp,DEST)
    print(json.dumps({'archived_grading_answers':20,'scheduled_solver_trials':20,'retained_records':len(records),'archive_bytes':sum(r.get('archive_bytes',0) for r in records),'manifest_sha256':manifest_sha,'quality_reviews_completed_with_four_unknowns':True,'source_semantics_concluded':True}))

if __name__=='__main__':archive()
