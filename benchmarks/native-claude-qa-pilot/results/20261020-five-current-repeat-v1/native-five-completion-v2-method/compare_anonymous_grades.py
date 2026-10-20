"""Seal anonymous independent-review agreement and conservative disagreements."""
from pathlib import Path
import datetime,hashlib,json,sys
METHOD=Path(__file__).resolve().parent;B=METHOD.parent;G=B/'native-five-completion-v2-grading'
sys.path.insert(0,str(METHOD))
import verify_anonymous_grades as verify

def normalize(row):
    return {**row,'criterion_score_bounds':{k:verify.bounds(v) for k,v in row['criterion_score_bounds'].items()}}
def compare(manifest_path):
    task,chunk=manifest_path.parent.name,'complete-supplement'
    reports={role:G/role/task/'report.json' for role in ('primary','secondary')}
    proofs={role:G/'verification'/role/task/'report.json' for role in reports}
    if not all(p.is_file() for p in [*reports.values(),*proofs.values()]):return
    out=G/'comparison'/task/'comparison.json'
    if out.exists():
        old=verify.load(out)
        assert all(verify.sha(p)==old['input_reports'][role]['sha256'] for role,p in reports.items())
        return
    inputs={};rows={}
    for role,p in reports.items():
        proof=verify.load(proofs[role]);assert proof['evidence_bytes_and_score_arithmetic_valid'] and proof['review_sha256']==verify.sha(p)
        report=verify.load(p);records=report.get('records',report.get('answers'))
        rows[role]={r['blinded_answer_id']:normalize(r) for r in records}
        inputs[role]={'path':str(p),'sha256':verify.sha(p),'verification_sha256':verify.sha(proofs[role]),
            'ancillary_json':[{'path':str(f),'sha256':verify.sha(f)} for f in sorted(p.parent.glob('*.json')) if f.name!='report.json']}
    assert set(rows['primary'])==set(rows['secondary'])
    rubric=verify.load(manifest_path.parent/'rubric.json');results=[];disagreements=[]
    for identity in sorted(rows['primary']):
        primary,secondary=[rows[r][identity] for r in reports]
        assert primary['answer_sha256']==secondary['answer_sha256']
        points={};bounds={};different=[]
        for criterion,pvalue in primary['criterion_points'].items():
            svalue=secondary['criterion_points'][criterion]
            a,b=primary['criterion_score_bounds'][criterion],secondary['criterion_score_bounds'][criterion]
            points[criterion]=pvalue if pvalue is not None and pvalue==svalue else None
            bounds[criterion]=[min(a[0],b[0]),max(a[1],b[1])]
            if pvalue!=svalue or a!=b:different.append(criterion)
        gates=[r['source_grounded_success'] for r in (primary,secondary)]
        gate=gates[0] if gates[0] is gates[1] else None
        conservative=verify.scores.score({'criterion_points':points,'criterion_score_bounds':bounds,'source_grounded_success':gate},rubric)
        item={'blinded_answer_id':identity,'answer_sha256':primary['answer_sha256'],
            'criterion_points':points,'criterion_score_bounds':bounds,**conservative,
            'reviewer_scores':{r:{k:rows[r][identity][k] for k in ('criterion_points','criterion_score_bounds','score_exact','score_lower','score_upper','source_grounded_success')} for r in reports},
            'point_or_bounds_disagreement_criteria':different,'success_gate_disagreement':gates[0] is not gates[1],
            'resolution':'Exact shared known criterion decisions are retained; all other criterion decisions retain the union of the two original bounds. Shared unknowns remain unknown. No condition labels or favorable-value selection.'}
        results.append(item)
        if different or gates[0] is not gates[1]:disagreements.append({'blinded_answer_id':identity,'criteria':different,'gates':gates,'reviewer_total_bounds':[[r['score_lower'],r['score_upper']] for r in (primary,secondary)]})
    result={'schema_version':1,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'task_id':task,'chunk':chunk,
        'manifest_sha256':verify.sha(manifest_path),'input_reports':inputs,'method_sha256':verify.sha(Path(__file__)),
        'condition_mapping_read':False,'independent_report_comparison_sealed_before_unblinding':True,
        'scope':'Conservative anonymous comparison, not a new semantic grader or a replacement of either original report. Evidence-attribution corrections remain separate and must be reconciled before final acceptance.',
        'records':results,'disagreements':disagreements,'total_records':len(results)}
    verify.write(out,result)
    print(json.dumps({'task':task,'chunk':chunk,'records':len(results),'disagreements':disagreements,'comparison_sha256':verify.sha(out)}),flush=True)
if __name__=='__main__':
    for manifest in sorted(G.glob('q*/manifest.json')):compare(manifest)
