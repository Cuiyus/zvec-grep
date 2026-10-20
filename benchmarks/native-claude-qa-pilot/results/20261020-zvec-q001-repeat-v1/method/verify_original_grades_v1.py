"""Check anonymous grading identities, exact quotes, source hashes and arithmetic.

This check does not replace semantic source support or consistency review.
It never accesses treatment assignments, trajectories or resource evidence.
"""
import datetime
import hashlib
import json
from pathlib import Path

B = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
G = B / 'native-zvec-q001-repeat-v1-grading'
P = B / 'native-zvec-q001-repeat-v1-blind-packets/q001'
path = G / 'original/grades.json'
data = path.read_bytes()
grade_sha = hashlib.sha256(data).hexdigest()
assert grade_sha == '311594dc4073257eb621307c23271d5bde105b9de67734dd7cecada27dc281e1'
grades = json.loads(data)
manifest = json.loads((P / 'manifest.json').read_text())
assert hashlib.sha256((P / 'manifest.json').read_bytes()).hexdigest() == '702bfb2007d9e5ef2a23efdf24a91f6e154febdf58a0f904a7cd23133f0a77aa'
source = Path(manifest['source_root']).resolve()
expected = {r['blinded_answer_id']: r for r in manifest['answers']}
assert len(grades['answers']) == len({r['blinded_answer_id'] for r in grades['answers']}) == 20
assert {r['blinded_answer_id'] for r in grades['answers']} == set(expected)
cache = {}


def file_data(p):
    p = p.resolve()
    if p not in cache:
        cache[p] = p.read_bytes()
    return cache[p]


def digest(p):
    return hashlib.sha256(file_data(p)).hexdigest()


input_count = 0
for entry in grades['inputs'].values():
    p = Path(entry['path'])
    assert p.resolve() in [q.resolve() for q in P.iterdir()] or p.resolve() == (G / 'source-preparation.json').resolve()
    assert digest(p) == entry['sha256'] and len(file_data(p)) == entry['bytes']
    input_count += 1
source_count = 0
for record in grades['source_registry'].values():
    p = Path(record['absolute_path']).resolve()
    assert p == (source / record['repository_relative_path']).resolve() and source in p.parents
    assert digest(p) == record['file_sha256']
    lines = file_data(p).decode('utf-8').splitlines(keepends=True)
    lo, hi = record['start_line'], record['end_line']
    assert 1 <= lo <= hi <= len(lines)
    text = ''.join(lines[lo - 1:hi])
    assert text == record['exact_source'] and hashlib.sha256(text.encode()).hexdigest() == record['span_sha256']
    source_count += 1
quote_count = 0


def verify_quotes(node, answer_path):
    global quote_count
    if isinstance(node, dict):
        if {'answer_path', 'start_line', 'end_line', 'exact_quote'} <= set(node):
            p = Path(node['answer_path']).resolve()
            assert p == answer_path.resolve()
            lines = file_data(p).decode('utf-8').splitlines(keepends=True)
            lo, hi = node['start_line'], node['end_line']
            assert 1 <= lo <= hi <= len(lines)
            text = ''.join(lines[lo - 1:hi])
            assert node['exact_quote'] in (text, text[:-1] if text.endswith('\n') else text)
            quote_count += 1
        for value in node.values():
            verify_quotes(value, answer_path)
    elif isinstance(node, list):
        for value in node:
            verify_quotes(value, answer_path)


citation_count = 0
for answer in grades['answers']:
    original = expected[answer['blinded_answer_id']]
    p = P / original['file']
    assert digest(p) == answer['answer_sha256'] == original['answer_sha256']
    assert len(file_data(p)) == answer['answer_bytes'] == original['bytes']
    verify_quotes(answer, p)
    criteria = {r['criterion_id']: r for r in answer['criterion_scores']}
    assert len(criteria) == len(answer['criterion_scores']) == 7 and set(criteria) == {f'C{i}' for i in range(1, 8)}
    for key, criterion in criteria.items():
        maximum = 2 if key in ('C1', 'C2', 'C3') else 1
        assert criterion['maximum_points'] == maximum
        assert type(criterion['points']) is int and 0 <= criterion['points'] <= maximum
    assert sum(c['points'] for c in criteria.values()) == answer['total_points'] and answer['maximum_points'] == 10
    citations = {r['id']: r for r in answer['verified_citations']}
    assert len(citations) == len(answer['verified_citations'])
    for citation in citations.values():
        assert citation['citation_token_exact'] in citation['answer_location']['exact_quote']
        actual = citation['resolved_source']
        registry = grades['source_registry'][actual['source_registry_id']]
        assert actual['path'] == registry['repository_relative_path']
        assert all(actual[k] == registry[k] for k in ('start_line', 'end_line', 'file_sha256', 'span_sha256'))
        citation_count += 1
    gates = {r['criterion_id']: r for r in answer['core_citation_gates']}
    assert len(gates) == len(answer['core_citation_gates']) == 3 and set(gates) == {'C1', 'C2', 'C3'}
    for key, gate in gates.items():
        assert type(gate['mandatory_gate_passed']) is bool
        assert gate['factual_full_credit'] is (criteria[key]['points'] == 2)
        assert gate['mandatory_gate_passed'] is (gate['factual_full_credit'] and gate['supporting_citations_sufficient'])
        if gate['mandatory_gate_passed']:
            assert gate['no_reviewer_added_citations_used_to_fill_gap'] is True
            ids = gate['verified_actual_answer_citation_ids']
            assert ids and all(i in citations and citations[i]['origin'] == 'answer_cited' and citations[i]['line_range_resolves'] is True for i in ids)
    core = all(g['mandatory_gate_passed'] for g in gates.values())
    assert answer['all_mandatory_core_gates_passed'] is core
    primary = answer['total_points'] >= 8 and core and not answer['critical_contradictions']
    assert answer['original_quality_pass'] is primary and answer['source_grounded_success'] is primary
    assert len(answer['critical_contradiction_audit']) == 5
report = {'schema_version': 1, 'verified_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'grades_sha256': grade_sha, 'answers_verified': 20, 'original_inputs_verified': input_count,
          'source_spans_verified': source_count, 'answer_quote_occurrences_verified': quote_count,
          'answer_cited_source_records_verified': citation_count, 'passed': True,
          'private_assignment_accessed': False, 'semantics_replaced': False,
          'scorer_blindness': 'Self-reported, not independently established by this structural verifier.',
          'method_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
with (G / 'original/parent-verification.json').open('x') as stream:
    json.dump(report, stream, indent=2, allow_nan=False)
    stream.write('\n')
print(json.dumps({k: report[k] for k in ['passed', 'answers_verified', 'source_spans_verified', 'answer_quote_occurrences_verified', 'answer_cited_source_records_verified']}))
