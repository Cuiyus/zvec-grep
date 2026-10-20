"""Check retained q005 reports and original evidence before condition unblinding."""
import datetime
import hashlib
import json
from pathlib import Path

B = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
G = B / 'native-q005-live-v1-grading'
P = G / 'packet'
A = B / 'native-q005-live-v1-audit'

def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()

def load(p):
    return json.loads(p.read_text())

def ref(record):
    p = Path(record['path'])
    assert p.is_file() and p.stat().st_size == record['bytes'] and sha(p) == record['sha256'], str(p)

def write(p, data):
    with p.open('x') as f:
        json.dump(data, f, indent=2)
        f.write('\n')

seal = load(G / 'pre-unblinding-reports-receipt.json')
assert seal['both_independent_reports_completed_before_parent_read']
assert seal['both_completion_notifications_received'] and not seal['private_assignment_read_by_parent']
assert sha(P / 'manifest.json') == seal['packet_manifest_sha256']
manifest = load(P / 'manifest.json')
expected = {a['blinded_answer_id']: a for a in manifest['answers']}
assert len(expected) == manifest['planned_answers'] == manifest['available_answers'] == 2
source = Path(manifest['source_root'])
source_manifest = load(P / 'source-manifest.json')
assert sha(P / 'source-manifest.json') == manifest['source_manifest_sha256']
for row in source_manifest['files']:
    ref({**row, 'path': str(source / row['path'])})
assert len(source_manifest['files']) == 1596

execution_path = A / 'execution/execution-review-v2.json'
execution = load(execution_path)
assert execution['execution_valid'] and execution['metric_reconciliation_valid']
assert execution['check_count'] == execution['passed_checks'] == 96 and not execution['failed_checks']
for row in execution['evidence_catalog'].values():
    ref(row)
assert len(execution['evidence_catalog']) == 200
assert all(c['passed'] for c in execution['audit_revision']['checks'])
replay_path = A / 'execution/production-replay/verification.json'
replay = load(replay_path)
assert replay['all_fidelity_checks_passed'] and replay['native_collection_returncode'] == 0
assert replay['solver_calls'] == 0 and not replay['docker_invoked']
assert len(replay['checks']) == 28
assert all(p['session_count'] == 1 and p['child_count'] == 0 for p in execution['profiles'])

source_path = A / 'source-scope/report.json'
scope = load(source_path)
references_path = A / 'source-scope/reference-verification.json'
references = load(references_path)
ref(references['report'])
for row in references['checks']:
    ref(row)
assert references['all_references_match'] and references['reference_count'] == 1854 and not references['errors']
assert scope['root_tool_count'] == scope['tool_count'] == 64 and scope['child_tool_count'] == 0
assert len(scope['reviewed_tools']) == 64
assert scope['strict_source_scope_valid'] is False and scope['scope_and_retrieval_gate_passed'] is False
assert scope['factual_evidence_scope_valid'] and scope['source_unchanged']
assert not scope['external_content_observed'] and not scope['external_code_or_answer_facts_observed']
assert sorted((p['profile'],p['tools']) for p in scope['profiles']) == [('baseline',44),('zvec-grep',20)]

def spans(registry):
    for record in registry.values():
        p = source / record['path']
        assert p.is_file() and source.resolve() in p.resolve().parents
        data = p.read_bytes()
        assert hashlib.sha256(data).hexdigest() == record['file_sha256']
        lines = data.decode('utf8').splitlines(keepends=True)
        lo, hi = record['start_line'], record['end_line']
        assert 1 <= lo <= hi <= len(lines)
        text = ''.join(lines[lo-1:hi])
        assert text == record['source_text'] and hashlib.sha256(text.encode()).hexdigest() == record['span_sha256']
    return len(registry)

def quotes(node, p, data):
    count = 0
    if isinstance(node, dict):
        lines = data.decode('utf8').splitlines()
        if 'exact_text' in node:
            text = node['exact_text']
            assert text in data.decode('utf8')
            if 'answer_path' in node:
                assert Path(node['answer_path']).resolve() == p.resolve()
            if 'start_line' in node:
                lo, hi = node['start_line'], node['end_line']
                assert 1 <= lo <= hi <= len(lines) and text in '\n'.join(lines[lo-1:hi])
            count += 1
        if {'answer_line','text'} <= set(node):
            assert node['text'] == lines[node['answer_line']-1]
            count += 1
        if {'answer_line','answer_line_quote'} <= set(node):
            assert node['answer_line_quote'] == lines[node['answer_line']-1]
            for citation in node.get('citations', []):
                assert citation['submitted_token'] in node['answer_line_quote']
            count += 1
        for value in node.values():
            count += quotes(value,p,data)
    elif isinstance(node,list):
        count += sum(quotes(v,p,data) for v in node)
    return count

primary = load(G / 'primary-report.json')
secondary = load(G / 'consistency-independent-report.json')
comparison_path = G / 'consistency-comparison.json'
comparison = load(comparison_path)
for row in seal['reports']:
    ref(row)
for report in (primary, secondary):
    for row in report.get('input_artifacts', report.get('input_files')):
        ref(row)
    assert {a['blinded_answer_id'] for a in report['answers']} == set(expected)
ref(primary['source_citation_audit'])
citation_audit = load(Path(primary['source_citation_audit']['path']))
assert not citation_audit['unresolved_citation_locations']
span_counts = [spans(citation_audit['source_spans']), spans(secondary['source_evidence_registry']), spans(comparison['source_evidence_registry'])]
quote_counts = []
for report, is_primary in ((primary, True), (secondary, False)):
    count = 0
    for answer in report['answers']:
        ident = answer['blinded_answer_id']; original = expected[ident]
        p = P / original['file']; data = p.read_bytes()
        assert sha(p) == original['answer_sha256'] == answer['answer_sha256'] and len(data) == original['bytes']
        count += quotes(answer,p,data)
        units = answer['units'] if is_primary else answer['point_unit_facts']
        indexed = {u['unit_id']: u for u in units}
        assert len(indexed) == len(units) == 10
        assert set(indexed) == {f'C{i}.{j}' for i in range(1,6) for j in (1,2)}
        for unit in units:
            assert unit.get('maximum',unit.get('maximum_points')) == 1
            assert type(unit['points']) is int and unit['points'] in (0,1)
        criteria = answer['criterion_sums'] if is_primary else answer['criterion_scores']
        for i in range(1,6):
            assert criteria[f'C{i}'] == sum(indexed[f'C{i}.{j}']['points'] for j in (1,2))
        total = answer['factual_total'] if is_primary else answer['total_points']
        assert total == sum(u['points'] for u in units) == 10
        assert all(indexed[f'C{i}.{j}']['points'] == 1 for i in (3,4,5) for j in (1,2))
        core = answer['core_citation_gates'] if is_primary else answer['core_citation_gate_results']
        assert len(core) == 4 and all(c['passed'] is True for c in core)
        assert (answer['success'] if is_primary else answer['source_grounded_success']) is True
    quote_counts.append(count)
for row in citation_audit['submitted_citations']:
    original = expected[row['blinded_answer_id']]; p = P / original['file']
    assert row['answer_sha256'] == original['answer_sha256']
    quotes(row,p,p.read_bytes())
assert not comparison['material_disagreements'] and not comparison['material_unknowns'] and not comparison['blockers']
assert comparison['coverage_and_counts']['answers_covered'] == 2
assert comparison['coverage_and_counts']['units_compared'] == 20
assert comparison['coverage_and_counts']['core_relationships_compared'] == 8
for ident in expected:
    pa = next(a for a in primary['answers'] if a['blinded_answer_id'] == ident)
    sa = next(a for a in secondary['answers'] if a['blinded_answer_id'] == ident)
    assert {u['unit_id']:u['points'] for u in pa['units']} == {u['unit_id']:u['points'] for u in sa['point_unit_facts']}
    assert pa['success'] == sa['source_grounded_success']

result = {
    'schema_version': 1, 'verified_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'task_id':'q005','run_id':39383511290,'method_sha256':sha(Path(__file__)),
    'execution_report': {'path':str(execution_path),'sha256':sha(execution_path),'catalog_references_rehashed':200,'original_checks_passed':96,'narrow_context_correction_checks_passed':4},
    'production_replay':{'path':str(replay_path),'sha256':sha(replay_path),'checks_passed':28,'rerun_by_parent':False},
    'source_report':{'path':str(source_path),'sha256':sha(source_path),'references_rehashed':1854,'tools_covered':64,'strict_valid':False,'factual_scope_valid':True},
    'source_manifest_files_rehashed':1596,'grade_reports':seal['reports'],
    'grading_source_span_occurrences_verified':sum(span_counts),'grading_source_spans_by_report':span_counts,
    'exact_answer_quotes_verified_by_report':quote_counts,
    'both_quality_decisions_agree':True,'both_answers_score':10,'material_unknowns':0,
    'comparison':{'path':str(comparison_path),'sha256':sha(comparison_path)},
    'private_assignment_accessed':False,
    'limits':'Parent identity, file/span/quote and arithmetic verification retains original independent semantic reviews, scope findings, source audit limitations and reviewer exposure declarations. It does not certify blindness from full tool histories or alter original gates.'
}
write(G / 'parent-evidence-verification.json',result)
print(json.dumps({k:result[k] for k in ('task_id','source_manifest_files_rehashed','grading_source_span_occurrences_verified','exact_answer_quotes_verified_by_report','private_assignment_accessed')}))
