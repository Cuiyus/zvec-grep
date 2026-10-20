"""Close complete original-question comparisons before condition unblinding."""
from pathlib import Path
import datetime,json
import verify_anonymous_grades as v
G=v.G
TASKS=('q001','q002','q003','q004','q005')
def records(report):return report.get('records',report.get('answers'))
def question(task):
    manifest_path=G/task/'manifest.json'
    if not manifest_path.is_file():return None
    manifest=v.load(manifest_path);expected={r['blinded_answer_id'] for r in manifest['answers']}
    assert len(expected)==manifest['planned_answers']==20
    paths=sorted((G/'chunks'/task).glob('*/manifest.json'));comparison=[];bindings=[]
    for p in paths:
        cp=G/'comparison/chunks'/task/p.parent.name/'comparison.json'
        if not cp.is_file():return None
        c=v.load(cp);assert c['manifest_sha256']==v.sha(p) and c['condition_mapping_read'] is False
        comparison+=c['records'];bindings.append({'path':str(cp),'sha256':v.sha(cp)})
    assert len(comparison)==20 and {r['blinded_answer_id'] for r in comparison}==expected
    full_reports={};consolidation=[]
    for role in ('primary','secondary'):
        full_path=G/role/task/'report.json'
        if not full_path.is_file():return None
        full=records(v.load(full_path));pieces=[];piece_bindings=[]
        for p in paths:
            rp=G/role/task/'chunks'/p.parent.name/'report.json';r=v.load(rp);pieces+=records(r)
            proof=G/'verification'/role/task/p.parent.name/'report.json';pr=v.load(proof)
            assert pr['review_sha256']==v.sha(rp) and pr['evidence_bytes_and_score_arithmetic_valid']
            piece_bindings.append({'report_path':str(rp),'report_sha256':v.sha(rp),'verification_sha256':v.sha(proof)})
        assert len(pieces)==len(full)==20 and {r['blinded_answer_id'] for r in full}==expected
        indexed={r['blinded_answer_id']:r for r in pieces};assert len(indexed)==20
        for row in full:
            original=indexed[row['blinded_answer_id']]
            changes=[k for k in original if row.get(k)!=original[k]]
            # Consolidated reports may add provenance, but cannot silently edit
            # any original judgment, literal quote or evidence attribution.
            assert not changes,(role,task,row['blinded_answer_id'],changes)
        full_reports[role]={'path':str(full_path),'sha256':v.sha(full_path),'chunks':piece_bindings}
        consolidation.append({'role':role,'original_rows':20,'complete_rows':20,'all_original_fields_identical':True})
    result={'schema_version':1,'task_id':task,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'manifest_sha256':v.sha(manifest_path),'original_full_report_bindings':full_reports,'chunk_comparison_bindings':bindings,
        'consolidation_checks':consolidation,'records':sorted(comparison,key=lambda r:r['blinded_answer_id']),
        'complete_original_planned_records':20,'available_original_finals':manifest['available_answers'],
        'condition_mapping_read':False,'comparison_sealed_before_unblinding':True,'method_sha256':v.sha(Path(__file__))}
    if task=='q002':
        correction=G/'verification/primary/q002/2fc8313d-e669-430b-baf3-dc2ecda3d431/attribution-correction-verification.json'
        assert v.load(correction)['all_mechanical_checks_passed']
        result['retained_attribution_correction_verification']={'path':str(correction),'sha256':v.sha(correction)}
    out=G/'comparison'/task/'complete-comparison.json'
    if out.exists():
        old=v.load(out);assert old['manifest_sha256']==result['manifest_sha256'] and old['original_full_report_bindings']==full_reports and old['records']==result['records']
    else:v.write(out,result)
    return {'task_id':task,'path':str(out),'sha256':v.sha(out),'records':20,'available_finals':manifest['available_answers']}
def main():
    completed=[x for task in TASKS if (x:=question(task)) is not None]
    if len(completed)<5:
        print(json.dumps({'ready':False,'completed_questions':[r['task_id'] for r in completed],'pending_questions':[t for t in TASKS if t not in {r['task_id'] for r in completed}],'condition_mapping_read':False}));return
    allrows=[{'task_id':row['task_id'],**record} for row in completed for record in v.load(Path(row['path']))['records']]
    assert len(allrows)==len({r['blinded_answer_id'] for r in allrows})==100
    result={'schema_version':1,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'original_run_id':39392884840,
        'original_plan_sha256':'cbd1e29c7badfe005c5e36392b9eeb7e26b016b890a92e44d5251096f6af9d54','questions':completed,
        'records':allrows,'all_original_planned_records':100,'condition_mapping_read':False,'independent_comparisons_sealed_before_unblinding':True,'method_sha256':v.sha(Path(__file__))}
    out=G/'comparison/final-original-comparison.json'
    if out.exists():assert v.load(out)['questions']==completed
    else:v.write(out,result)
    print(json.dumps({'ready':True,'records':100,'master_comparison_sha256':v.sha(out),'condition_mapping_read':False}))
if __name__=='__main__':main()
