"""Verify report identity, original score arithmetic and literal evidence bytes.

This is not an automatic semantic grader or a condition-mapping reader.
Raw review reports and corrections remain immutable.
"""
from pathlib import Path
import datetime,hashlib,json,sys
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
B=Path(__file__).resolve().parent.parent
G=B/'native-five-completion-v2-grading'
sys.path.insert(0,str(W/'benchmarks/native-claude-qa-pilot'))
import quality_scores_v1 as scores

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def write(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def bounds(value):
    return [value['lower'],value['upper']] if isinstance(value,dict) else value
def walk(v,path=()):
    if isinstance(v,dict):
        yield path,v
        for k,x in v.items():yield from walk(x,path+(str(k),))
    elif isinstance(v,list):
        for i,x in enumerate(v):yield from walk(x,path+(str(i),))
def verify(role,task,chunk,manifest_path):
    report_path=G/role/task/'report.json'
    if not report_path.is_file():return
    destination=G/'verification'/role/task/'report.json'
    if destination.is_file():
        previous=load(destination);assert previous['review_sha256']==sha(report_path)
        return
    manifest=load(manifest_path);rubric=load(manifest_path.parent/'rubric.json');report=load(report_path)
    expected={r['blinded_answer_id']:r for r in manifest['answers']}
    rows=report.get('records',report.get('answers'));checks=[];normalized=[]
    def check(name,passed,**details):checks.append({'check':name,'passed':bool(passed),**details})
    check('exact_anonymous_record_set',isinstance(rows,list) and len(rows)==len(expected) and {r['blinded_answer_id'] for r in rows}==set(expected))
    check('unchanged_rubric_packet_hash',sha(manifest_path.parent/'rubric.json')==manifest['rubric_sha256'])
    registry=report.get('source_evidence_registry');external_registry=None
    if registry is None:
        pointer=report['source_registry']
        if 'path' in pointer:
            external_registry=Path(pointer['path'])
            check('external_source_registry_hash',sha(external_registry)==pointer['sha256'])
            registry=load(external_registry)['spans']
        else:registry=pointer.get('spans',pointer)
    source_root=Path(manifest['source_root']);source_files={};source_spans=0;answer_quotes=0
    for ref,span in registry.items():
        relative=span.get('source_path',span.get('path'));p=source_root/relative
        check('source_path_within_frozen_root',source_root.resolve() in p.resolve().parents and not p.is_symlink(),ref=ref)
        if str(p) not in source_files:source_files[str(p)]=p.read_bytes()
        data=source_files[str(p)];lines=data.splitlines(keepends=True);start,end=span['start_line'],span['end_line']
        selected=b''.join(lines[start-1:end]);text=span.get('exact_source_text',span.get('text'))
        check('source_file_and_span_bytes',1<=start<=end<=len(lines) and hashlib.sha256(data).hexdigest()==span['file_sha256'] and hashlib.sha256(selected).hexdigest()==span['span_sha256'] and (text is None or selected==text.encode('utf8')),ref=ref)
        source_spans+=1
    for row in rows:
        identity=row['blinded_answer_id'];item=expected[identity];available=item['final_answer_available']
        check('answer_identity',row.get('answer_sha256')==item['answer_sha256'] and row.get('answer_bytes',row.get('bytes'))==item['bytes'],answer=identity)
        normalized_row={**row,'criterion_score_bounds':{k:bounds(v) for k,v in row['criterion_score_bounds'].items()}}
        try:
            score=scores.score(normalized_row,rubric)
            check('original_score_arithmetic',score['score']==row['score_exact'] and score['score_lower']==row['score_lower'] and score['score_upper']==row['score_upper'],answer=identity)
        except Exception as error:
            score=None;check('original_score_arithmetic',False,answer=identity,error=type(error).__name__+':'+str(error))
        if not available:
            check('missing_answer_remains_unknown',row['score_exact'] is None and row['score_lower']==0 and row['score_upper']==10 and row['source_grounded_success'] is None,answer=identity)
        else:
            data=(manifest_path.parent/item['file']).read_bytes();lines=data.decode('utf8').splitlines(keepends=True)
            check('exact_final_bytes',hashlib.sha256(data).hexdigest()==item['answer_sha256'] and len(data)==item['bytes'],answer=identity)
            for path,quote in walk(row):
                text=quote.get('exact_quote',quote.get('exact_text',quote.get('quote')))
                if not isinstance(text,str):continue
                start=quote.get('utf8_start_byte',quote.get('byte_start'))
                end=quote.get('utf8_end_byte_exclusive',quote.get('byte_end_exclusive'))
                if start is not None and end is not None:
                    valid=type(start) is int and type(end) is int and 0<=start<=end<=len(data) and data[start:end]==text.encode('utf8')
                elif any('answer_quote' in part or 'answer_span' in part for part in path):
                    start=quote.get('answer_start_line',quote.get('start_line',quote.get('line')));end=quote.get('answer_end_line',quote.get('end_line',start))
                    if start is None:continue
                    valid=type(start) is int and type(end) is int and 1<=start<=end<=len(lines) and text in ''.join(lines[start-1:end])
                else:continue
                check('literal_answer_quote',valid,answer=identity,field_path='/'.join(path));answer_quotes+=1
        normalized.append({'blinded_answer_id':identity,'answer_sha256':item['answer_sha256'],'final_answer_available':available,'quality':score,'original_source_grounded_success':row['source_grounded_success']})
    result={'schema_version':1,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'role':role,'task_id':task,'chunk':chunk,
        'manifest_sha256':sha(manifest_path),'review_sha256':sha(report_path),'external_source_registry_sha256':sha(external_registry) if external_registry else None,
        'method_sha256':sha(Path(__file__)),'evidence_bytes_and_score_arithmetic_valid':all(c['passed'] for c in checks),
        'checks':checks,'source_spans_verified':source_spans,'answer_quotes_checked':answer_quotes,'normalized_records':normalized,
        'condition_mapping_read':False,'semantic_grade_adjudication_performed':False,'corrections_applied_to_original_report':False}
    write(destination,result)
    print(json.dumps({'role':role,'task':task,'chunk':chunk,'records':len(rows),'source_spans':source_spans,'answer_quotes':answer_quotes,
        'failed_checks':[c for c in checks if not c['passed']],'verification_sha256':sha(destination)}),flush=True)
if __name__=='__main__':
    assert (B/'native-five-repeat-v1-grading/seals/parent-quality-read-admission-after-technical-selection.json').is_file()
    for manifest in sorted(G.glob('q*/manifest.json')):
        for role in ('primary','secondary'):verify(role,manifest.parent.name,'complete-supplement',manifest)
