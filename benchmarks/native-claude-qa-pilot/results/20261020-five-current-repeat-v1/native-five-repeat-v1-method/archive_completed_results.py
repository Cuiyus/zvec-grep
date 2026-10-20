"""Archive reviewed results and exact provenance; retain large raw evidence.

No provider, workflow dispatch, test retry, deletion, or report rewriting.
The large native ZIPs and full scope-reference captures remain at their
verified local paths, with their verification receipts copied for review.
"""
from pathlib import Path
import datetime,hashlib,json,shutil
B=Path(__file__).resolve().parent.parent
H=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep/benchmarks/native-claude-qa-pilot')
OUT=H/'results/20261020-five-current-repeat-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def allfiles(root):return sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
def main():
    summary=B/'native-five-repeat-v1-analysis/phase-result-summary-zh.json'
    assert summary.is_file()
    OUT.mkdir(exist_ok=False);files=[];bulk=[]
    def copy(p):
        assert p.is_file() and not p.is_symlink()
        relative=p.relative_to(B);dest=OUT/relative;dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():assert sha(dest)==sha(p);return
        shutil.copyfile(p,dest);assert sha(dest)==sha(p)
        files.append({'archive_path':str(relative),'original_path':str(p),'bytes':p.stat().st_size,'sha256':sha(dest)})
    for root in [B/'native-five-repeat-v1-analysis',B/'native-five-repeat-v1-grading',B/'native-five-completion-v2-grading',
                 B/'native-five-repeat-v1-method',B/'native-five-completion-v2-method']:
        for p in allfiles(root):
            if p.suffix in ('.json','.py','.jsonl','.txt'):copy(p)
    for run,ci,audit,expected in [
        (39392884840,B/'native-five-repeat-v1-ci/39392884840',B/'native-five-repeat-v1-audit',50),
        (39401348683,B/'native-five-completion-v2-ci/39401348683',B/'native-five-completion-v2-audit/39401348683',5),
    ]:
        receipts=sorted(ci.glob('q*/artifact-byte-verification.json'));assert len(receipts)==expected
        for receipt in receipts:
            pair=receipt.parent
            for name in ('artifact-byte-verification.json','artifact-metadata.json','job-metadata.json','run-metadata.json'):copy(pair/name)
            bulk.append({'kind':'exact_native_job_artifact','run_id':run,'pair_key':pair.name,
                'retained_zip_path':str(pair/'artifact.zip'),'retained_raw_root':str(pair/'raw'),
                'byte_verification_path':str(receipt),'byte_verification_sha256':sha(receipt)})
        for pair in sorted((audit/'execution').glob('q*')):
            for p in sorted(pair.glob('*.json')):copy(p)
        scope=audit/'source-scope'
        for p in sorted(scope.glob('*.json')):copy(p)
        for pair in sorted((scope/'pairs').glob('q*')):
            selected=set(pair.glob('report*.json'))
            selected.update(pair/name for name in ('input-identity-check.json','source-file-verification.json',
                'source-metadata-checks.json','explicit-review-decisions.json','pair-inputs.json') if (pair/name).is_file())
            for p in sorted(selected):copy(p)
            references=sorted(pair.glob('reference-verification*.json'))
            bulk.append({'kind':'full_independent_source_scope_capture','run_id':run,'pair_key':pair.name,
                'retained_root':str(pair),'reference_verifications':[{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)} for p in references]})
        for p in allfiles(scope/'method'):
            if p.suffix in ('.py','.json','.txt'):copy(p)
        snapshots=sorted((scope/'snapshots').glob('*.json'))
        if snapshots:copy(snapshots[-1])
    result={'schema_version':1,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'summary_original_path':str(summary),'summary_sha256':sha(summary),'archived_files':files,
        'retained_large_evidence':bulk,'raw_evidence_deleted':False,'history_pooled':False,
        'large_binary_policy':'Exact native archives, full persisted sessions and full per-tool/reference captures remain at the recorded verified local paths. This archive contains their byte-verification receipts, all grades, comparisons, selected audit reports and postprocessing methods. No compressed ZIP is replaced with a reconstructed receipt.',
        'method_sha256':sha(Path(__file__))}
    out=OUT/'archive-manifest.json'
    with out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({'archive_root':str(OUT),'archive_manifest_sha256':sha(out),
        'archived_files':len(files),'retained_bulk_entries':len(bulk),'copied_bytes':sum(r['bytes'] for r in files)}))
if __name__=='__main__':main()
