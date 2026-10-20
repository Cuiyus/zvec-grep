"""Verify both saved anonymous reports without reading the condition assignment."""
import datetime, hashlib, json
from pathlib import Path

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
G=B/'native-q004-repeat-v1-grading';P=G/'q004'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())

seal=load(G/'pre-unblinding-reports-receipt.json')
assert seal['both_independent_reports_completed_before_parent_read'] and seal['private_assignment_read_by_parent'] is False
manifest=load(P/'manifest.json');assert sha(P/'manifest.json')==seal['packet_manifest_sha256']
expected={a['blinded_answer_id']:a for a in manifest['answers']};assert len(expected)==20
source=Path(manifest['source_root']);records=[]

for sealed in seal['reports']:
    path=Path(sealed['path']);assert sha(path)==sealed['sha256'] and path.stat().st_size==sealed['bytes']
    report=load(path);answers=report['answers'];assert len(answers)==20 and {a['blinded_answer_id'] for a in answers}==set(expected)
    inputs=report.get('input_files',report.get('input_hashes'))
    inputs=inputs if isinstance(inputs,list) else list(inputs.values())
    for item in inputs:
        p=Path(item['path']);assert p.resolve().parent==P.resolve() and p.stat().st_size==item['bytes'] and sha(p)==item['sha256']
    source_count=0;invalid_citations=0
    registry=report.get('source_evidence_registry',report.get('source_span_registry'))
    for record in registry.values():
        rel=record.get('path',record.get('source_path'));p=source/rel
        assert p.is_file() and not p.is_symlink() and source.resolve() in p.resolve().parents
        data=p.read_bytes();lines=data.decode('utf8').splitlines(keepends=True)
        assert hashlib.sha256(data).hexdigest()==record['file_sha256']
        if 'start_line' in record:
            lo,hi=record['start_line'],record['end_line'];assert 1<=lo<=hi<=len(lines)
            span=''.join(lines[lo-1:hi]);assert span==record['source_text'] and hashlib.sha256(span.encode()).hexdigest()==record['span_sha256']
        else:
            lo,hi=record['requested_lines'];valid=1<=lo<=hi<=len(lines)
            assert record['requested_range_within_bounds'] is valid and record['file_bytes']==len(data) and record['file_line_count']==len(lines)
            alo,ahi=record['available_lines'];assert [alo,ahi]==[max(1,lo),min(hi,len(lines))]
            span=''.join(lines[alo-1:ahi]);assert span==record['exact_available_text_utf8']
            assert hashlib.sha256(span.encode()).hexdigest()==record['available_span_sha256']
            assert (record['requested_span_sha256']==hashlib.sha256(span.encode()).hexdigest()) if valid else record['requested_span_sha256'] is None
            invalid_citations+=not valid
        source_count+=1
    quote_count=[0];null_units=0;null_totals=0;null_success=0
    def quotes(node,answer_path,data,lines):
        if isinstance(node,dict):
            if 'exact_text' in node:
                text=node['exact_text'];assert isinstance(text,str) and text in data.decode('utf8')
                if 'answer_path' in node:assert Path(node['answer_path']).resolve()==answer_path.resolve()
                if {'start_line','end_line'}<=set(node):
                    lo,hi=node['start_line'],node['end_line'];assert 1<=lo<=hi<=len(lines) and text in ''.join(lines[lo-1:hi])
                if {'byte_start_zero_based','byte_end_exclusive'}<=set(node):
                    lo,hi=node['byte_start_zero_based'],node['byte_end_exclusive'];assert data[lo:hi]==text.encode('utf8')
                    if 'sha256' in node:assert hashlib.sha256(data[lo:hi]).hexdigest()==node['sha256']
                quote_count[0]+=1
            for value in node.values():quotes(value,answer_path,data,lines)
        elif isinstance(node,list):
            for value in node:quotes(value,answer_path,data,lines)
    for answer in answers:
        original=expected[answer['blinded_answer_id']];p=P/original['file'];data=p.read_bytes();text=data.decode('utf8');lines=text.splitlines(keepends=True)
        assert sha(p)==original['answer_sha256']==answer['answer_sha256'] and len(data)==original['bytes']==answer['answer_bytes']
        if 'answer_path' in answer:assert Path(answer['answer_path']).resolve()==p.resolve()
        for key in ('exact_answer_utf8','final_answer_exact_text'):
            if key in answer:assert answer[key]==text
        units=answer.get('point_unit_facts',answer.get('ten_unit_facts'));indexed={u['unit_id']:u for u in units}
        assert len(indexed)==len(units)==10 and set(indexed)=={f'C{i}.{j}' for i in range(1,6) for j in (1,2)}
        for u in units:assert u['maximum_points']==1 and (u['points'] is None or type(u['points']) is int and u['points'] in (0,1))
        criteria=answer['criterion_scores'];assert set(criteria)=={f'C{i}' for i in range(1,6)}
        for criterion,total in criteria.items():
            values=[indexed[criterion+'.'+str(j)]['points'] for j in (1,2)]
            assert total is (None) if any(v is None for v in values) else total==sum(values)
        values=[u['points'] for u in units];total=answer['total_points']
        assert (total is None) if any(v is None for v in values) else total==sum(values)
        assert answer['maximum_points']==10 and answer['source_grounded_success'] in (True,False,None)
        null_units+=sum(v is None for v in values);null_totals+=total is None;null_success+=answer['source_grounded_success'] is None
        for citation in answer.get('citation_support_by_claim',answer.get('submitted_citations',[])):
            assert citation['submitted_token'] in text
        quotes(answer,p,data,lines)
    records.append({'path':str(path),'sha256':sha(path),'answers_verified':20,'input_files_verified':len(inputs),'source_spans_verified':source_count,'submitted_ranges_explicitly_out_of_bounds_retained':invalid_citations,'exact_answer_quote_occurrences_verified':quote_count[0],'null_units_preserved':null_units,'null_totals_preserved':null_totals,'null_quality_decisions_preserved':null_success})

result={'schema_version':1,'verified_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'reports':records,'both_report_records_verified':True,'private_assignment_accessed':False,'semantic_grading_replaced':False,'unknowns_zero_filled_or_dropped':False,'scope':'Input/answer identities, exact original answer quotes, source file/span hashes and known/null point arithmetic. This does not replace semantic citation or contradiction review, establish blindness from full tool histories, or adjudicate any material grading disagreement.','method_sha256':sha(Path(__file__))}
with (G/'parent-grading-verification.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
print(json.dumps({'reports_verified':2,'answer_records_verified':40,'source_spans_verified':sum(r['source_spans_verified'] for r in records),'exact_quotes_verified':sum(r['exact_answer_quote_occurrences_verified'] for r in records),'mapping_read':False}))
