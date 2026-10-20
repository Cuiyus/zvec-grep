"""Verify persisted anonymous reviews and preserve three unknown C7 judgments."""
import datetime
import hashlib
import json
from pathlib import Path

B = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
G = B / 'native-zvec-q001-repeat-v1-grading'
P = B / 'native-zvec-q001-repeat-v1-blind-packets/q001'
pins = {'original/grades.json': '311594dc4073257eb621307c23271d5bde105b9de67734dd7cecada27dc281e1',
        'original/c7-clarification.json': '9fb566bc34a4e15f0123b2c53e6f7e733d98724a8b17ed2d7f2db39b1ab73451',
        'consistency/consistency-review.json': 'e4d4e5c20e231a49281b5964d18ff9a77631557adaa93149f0078f910ab6f217',
        'consistency/consistency-errata.json': 'f0ce2dae0763843732e80563979500bf9d50f2edf9de3bdf027765739b814b35',
        'consistency/c7-concordance.json': 'e990108fb65378a4284c22d4ecc8b2c6aab15fe2e006e800a3e6a25e96666c04'}
objects = {}
for relative, sha in pins.items():
    data = (G / relative).read_bytes()
    assert hashlib.sha256(data).hexdigest() == sha
    objects[relative] = json.loads(data)
grades = objects['original/grades.json']
clarification = objects['original/c7-clarification.json']
review = objects['consistency/consistency-review.json']
concordance = objects['consistency/c7-concordance.json']
originals = {a['blinded_answer_id']: a for a in grades['answers']}
clarified = {a['blinded_answer_id']: a for a in clarification['answers']}
independent = {a['blinded_answer_id']: a for a in review['answer_reviews']}
assert len(originals) == len(clarified) == len(independent) == len(concordance['all_20_rows']) == 20
assert set(originals) == set(clarified) == set(independent)
assert review['checks']['answers_reviewed'] == 20
assert review['checks']['original_sum_or_pass_formula_errors'] == 0
assert review['checks']['actual_core_citation_gate_changes'] == 0
assert review['checks']['original_grades_changed'] is False
reference_count = 0
for document in [review, concordance]:
    for reference in document['inputs']:
        p = Path(reference['path']).resolve()
        assert 'grading-private' not in str(p) and p.name != 'assignment.json'
        assert G.resolve() in p.parents or P.resolve() in p.parents
        data = p.read_bytes()
        assert len(data) == reference['bytes'] and hashlib.sha256(data).hexdigest() == reference['sha256']
        reference_count += 1
quote_count = 0
for quote in review['exact_answer_quote_ledger']:
    p = Path(quote['answer_path']).resolve()
    assert p.parent == P.resolve()
    text = p.read_text()
    selected = text[quote['char_start_0_based']:quote['char_end_0_based_exclusive']]
    assert selected == quote['exact_quote']
    assert hashlib.sha256(selected.encode()).hexdigest() == quote['quote_sha256_utf8']
    quote_count += 1
agreed = {}
unknown = []
for row in concordance['all_20_rows']:
    identifier = row['blinded_answer_id']
    old, checked, own = originals[identifier], clarified[identifier], independent[identifier]
    assert row['answer_sha256'] == old['answer_sha256'] == checked['answer_sha256']
    assert row['unchanged_C1_C6_and_core_critical_fingerprint_verified'] is True
    assert row['other_criteria_or_gates_disagreement'] is False
    original_point = next(c['points'] for c in old['criterion_scores'] if c['criterion_id'] == 'C7')
    assert row['original'] == {'C7_points': original_point, 'total_points': old['total_points'], 'quality_pass': old['original_quality_pass']}
    left = row['independent_consistency_reviewer_precomparison_result']
    right = row['original_reviewer_independent_clarification_result']
    assert left == own['separate_C7_erratum_result']
    assert right == {'C7_points': checked['reviewed_C7_points'], 'total_points': checked['reviewed_total_points'], 'quality_pass': checked['reviewed_quality_pass']}
    for result in [left, right]:
        point = result['C7_points']
        assert type(point) is int and point in (0, 1)
        assert result['total_points'] == old['total_points'] - original_point + point
        quality = result['total_points'] >= 8 and old['all_mandatory_core_gates_passed'] and not old['critical_contradictions']
        assert result['quality_pass'] is quality
    agree = left['C7_points'] == right['C7_points']
    assert row['C7_agreement'] is agree
    if agree:
        assert row['agreed_C7_point_if_any'] == left['C7_points']
        agreed[identifier] = left['C7_points']
    else:
        assert row['agreed_C7_point_if_any'] is None and row['unified_or_adjudicated_score'] is None
        unknown.append(identifier)
assert len(agreed) == 17 and len(unknown) == 3
assert set(unknown) == set(concordance['summary']['unresolved_anonymous_ids'])
assert concordance['summary']['corrected_scoring_view_created'] is False
result = {'schema_version': 1, 'verified_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'original_grades_sha256': pins['original/grades.json'], 'original_grade_count': 20,
          'consistency_coverage': 20, 'original_grade_bytes_unchanged': True,
          'original_scoring_semantics_preserved': True,
          'meaning_of_semantics_preserved': 'Original question/rubric/contract, C1-C6/core/critical gates and initial bytes preserved; ambiguous C7 judgments are unknown in the primary view, never invented.',
          'consistency_report_path': str(G / 'consistency/consistency-review.json'),
          'consistency_report_sha256': pins['consistency/consistency-review.json'],
          'c7_clarification_sha256': pins['original/c7-clarification.json'],
          'c7_concordance_path': str(G / 'consistency/c7-concordance.json'),
          'c7_concordance_sha256': pins['consistency/c7-concordance.json'],
          'c7_concordant_answer_count': 17, 'agreed_original_rubric_c7_corrections': False,
          'agreed_C7_points_by_anonymous_id': agreed, 'unresolved_anonymous_ids': unknown,
          'complete_primary_scoring_view_available': False,
          'primary_statistics': 'unavailable for all20 score/score-trimming/quality comparisons because three C7 scores are unknown; retain all20 executions.',
          'diagnostic_views': ['initial_blind_ratings', 'original_scorer_c7_clarification', 'independent_consistency_c7_interpretation'],
          'unknowns_zero_filled': False, 'private_assignment_accessed': False,
          'review_input_references_verified': reference_count, 'independent_exact_quotes_verified': quote_count,
          'pinned_artifacts': pins, 'method_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
with (G / 'grading-completion-check.json').open('x') as stream:
    json.dump(result, stream, indent=2, allow_nan=False)
    stream.write('\n')
print(json.dumps({'all20_original_and_consistency_rows_verified': True, 'C7_agreements': 17, 'C7_unknowns_preserved': 3, 'mapping_accessed': False}))
