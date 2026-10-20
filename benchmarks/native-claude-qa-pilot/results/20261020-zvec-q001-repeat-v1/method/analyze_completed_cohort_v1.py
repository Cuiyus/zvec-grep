"""Unblind only after saved grading checks, then apply frozen q001 statistics."""
import datetime
import copy
import hashlib
import json
import sys
from pathlib import Path

B = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
H = W / 'benchmarks/native-claude-qa-pilot'
A = B / 'native-zvec-q001-repeat-v1-audit'
G = B / 'native-zvec-q001-repeat-v1-grading'
P = B / 'native-zvec-q001-repeat-v1-blind-packets/q001'
PRIVATE = B / 'native-zvec-q001-repeat-v1-grading-private'
OUT = A / 'final-analysis'
ASSIGNMENT_SHA = 'fd370680e83301e2f3cd349cd988084078989d92a451396545794c404d789e28'
GRADE_SHA = '311594dc4073257eb621307c23271d5bde105b9de67734dd7cecada27dc281e1'
references = {}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load(path):
    data = path.read_bytes()
    references[str(path)] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    return json.loads(data)


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


completion = load(G / 'grading-completion-check.json')
assert completion['original_grades_sha256'] == GRADE_SHA
assert completion['original_grade_count'] == completion['consistency_coverage'] == 20
assert completion['original_grade_bytes_unchanged'] is True
assert completion['original_scoring_semantics_preserved'] is True
assert completion['c7_concordant_answer_count'] == 17
assert completion['agreed_original_rubric_c7_corrections'] is False
grades = load(G / 'original/grades.json')
assert references[str(G / 'original/grades.json')]['sha256'] == GRADE_SHA
consistency_path = Path(completion['consistency_report_path'])
consistency = load(consistency_path)
assert references[str(consistency_path)]['sha256'] == completion['consistency_report_sha256']
c7_path = G / 'original/c7-clarification.json'
c7 = load(c7_path)
assert references[str(c7_path)]['sha256'] == completion['c7_clarification_sha256'] == '9fb566bc34a4e15f0123b2c53e6f7e733d98724a8b17ed2d7f2db39b1ab73451'
concordance_path = Path(completion['c7_concordance_path'])
concordance = load(concordance_path)
assert references[str(concordance_path)]['sha256'] == completion['c7_concordance_sha256']
decision_path = G / 'grading-interpretation-decision.json'
load(decision_path)
packet = load(P / 'manifest.json')
assert references[str(P / 'manifest.json')]['sha256'] == '702bfb2007d9e5ef2a23efdf24a91f6e154febdf58a0f904a7cd23133f0a77aa'
resources = load(A / 'reported-resource-evidence.json')
scope = load(A / 'source-scope-evidence.json')
execution = load(A / 'cohort-execution-verification.json')
assert execution['passed'] and execution['solver_execution_count'] == 20
assert len(resources['rows']) == len(scope['rows']) == len(grades['answers']) == 20
assert not OUT.exists()
OUT.mkdir()
write(OUT / 'unblinding-start.json', {
    'schema_version': 1, 'recorded_at_utc': now(), 'assignment_read_next': True,
    'original_grades_sha256': GRADE_SHA,
    'consistency_report_sha256': completion['consistency_report_sha256'],
    'c7_clarification_sha256': completion['c7_clarification_sha256'],
    'c7_concordance_sha256': completion['c7_concordance_sha256'],
    'grading_completion_check_sha256': references[str(G / 'grading-completion-check.json')]['sha256'],
    'expected_assignment_sha256': ASSIGNMENT_SHA,
    'root_exposure': 'Parent already inspected labelled execution/source/resource audits; parent is not claimed blind. Packet construction internally used the private assignment without emitting mapping. This is the first explicit analysis mapping read.',
    'scorer_blindness': 'Self-reported in original grades and consistency files; no independent complete tool-history audit.'})
assignment = load(PRIVATE / 'assignment.json')
assert references[str(PRIVATE / 'assignment.json')]['sha256'] == ASSIGNMENT_SHA
assert len(assignment['rows']) == 20
write(OUT / 'unblinding-receipt.json', {
    'schema_version': 1, 'recorded_at_utc': now(), 'assignment_read': True,
    'assignment_sha256': ASSIGNMENT_SHA, 'rows': 20,
    'original_grades_sha256': GRADE_SHA,
    'consistency_report_sha256': completion['consistency_report_sha256'],
    'c7_clarification_sha256': completion['c7_clarification_sha256'],
    'c7_concordance_sha256': completion['c7_concordance_sha256'],
    'both_reports_persisted_before_mapping_read': True,
    'initial_grades_preserved_and_separately_analyzed': True,
    'primary_quality_view': 'Unavailable complete scoring: 17 agreed C7 judgments and 3 explicit unknowns. Initial C1-C6/core/critical gates unchanged; diagnostic interpretations are separate.'})
answers = {a['blinded_answer_id']: a for a in grades['answers']}
expected = {a['blinded_answer_id']: a for a in packet['answers']}
resource_rows = {(r['profile'], r['trial_index']): r for r in resources['rows']}
scope_rows = {(r['profile'], r['trial_index']): r for r in scope['rows']}
assert len(answers) == len(expected) == len(resource_rows) == len(scope_rows) == 20
profiles = {name: {'trials': []} for name in ('baseline', 'zvec-grep')}
seen = set()
for mapping in sorted(assignment['rows'], key=lambda r: (r['profile'], r['trial_index'])):
    assert mapping['task_id'] == 'q001'
    key = (mapping['profile'], mapping['trial_index'])
    assert key not in seen
    seen.add(key)
    anonymous = mapping['anonymous_id']
    answer, raw, audited_scope = answers[anonymous], resource_rows[key], scope_rows[key]
    assert expected[anonymous]['answer_sha256'] == answer['answer_sha256'] == raw['answer_sha256']
    assert type(answer['original_quality_pass']) is bool
    source_gate = audited_scope['all_source_and_required_retrieval_gates_pass']
    trial = {'trial_index': mapping['trial_index'], 'trial_name': raw['pair_key'] + '-' + mapping['profile'],
             'pair_key': raw['pair_key'], 'anonymous_answer_id': anonymous,
             'answer_sha256': answer['answer_sha256'],
             'judge': {'total': answer['total_points'], 'maximum': answer['maximum_points']},
             'quality_pass': answer['original_quality_pass'],
             'criterion_scores': {c['criterion_id']: c['points'] for c in answer['criterion_scores']},
             'core_citation_gates': answer['core_citation_gates'],
             'critical_contradictions': answer['critical_contradictions'],
             'c6_minimal_lifecycle_sensitivity': answer['c6_minimal_lifecycle_sensitivity'],
             'metrics': raw['metrics'], 'execution_valid': raw['execution_valid'],
             'execution_and_scope_valid': raw['execution_valid'] is True and source_gate is True,
             'reported_metric_eligible': raw['reported_metric_eligible'],
             'source_scope': audited_scope}
    profiles[mapping['profile']]['trials'].append(trial)
for profile in profiles.values():
    assert [t['trial_index'] for t in profile['trials']] == list(range(1, 11))
initial_payload = {'schema_version': 1, 'assembled_at_utc': now(),
           'measurement_scope': resources['measurement_scope'], 'time_scope': resources['time_scope'],
           'cases': [{'task_id': 'q001', 'profiles': profiles}],
           'all_original_20_rows_preserved': True, 'historical_or_cancelled_samples_pooled': False,
           'rating_view': 'initial_blind_grades_preserved',
           'grade_source': 'Unchanged initial grades; known C7 interpretation issue is separately reviewed before unblinding. This view is retained rather than silently overwritten.',
           'references': references}
write(OUT / 'analysis-input-initial-grades.json', initial_payload)
c7_by_id = {r['blinded_answer_id']: r for r in c7['answers']}
concordance_by_id = {r['blinded_answer_id']: r for r in concordance['all_20_rows']}
assert len(c7_by_id) == len(concordance_by_id) == 20 and set(c7_by_id) == set(answers)


def rating_view(label, point_function):
    payload = copy.deepcopy(initial_payload)
    payload['rating_view'] = label
    payload['grade_source'] = 'C7-only blind review interpretation. Initial C1-C6/core/critical gates and all20 solver/resource/scope rows retained. No C6 sensitivity substitution.'
    changes = []
    for name, profile in payload['cases'][0]['profiles'].items():
        for trial in profile['trials']:
            identifier = trial['anonymous_answer_id']
            old_point = trial['criterion_scores']['C7']
            trial['initial_judge_total'] = trial['judge']['total']
            trial['initial_quality_pass'] = trial['quality_pass']
            trial['initial_C7_points'] = old_point
            point = point_function(identifier)
            assert point is None or (type(point) is int and point in (0, 1))
            base = trial['initial_judge_total'] - old_point
            core = all(g['mandatory_gate_passed'] for g in trial['core_citation_gates'])
            gate = core and not trial['critical_contradictions']
            trial['criterion_scores']['C7'] = point
            if point is None:
                trial['judge']['total'] = None
                low, high = bool(base >= 8 and gate), bool(base + 1 >= 8 and gate)
                trial['quality_pass'] = low if low == high else None
                trial['score_bounds_not_observations'] = [base, base + 1]
                trial['quality_pass_bounds_not_observations'] = [low, high]
                trial['C7_status'] = 'unknown_independent_blind_review_disagreement'
            else:
                trial['judge']['total'] = base + point
                trial['quality_pass'] = bool(base + point >= 8 and gate)
                trial['C7_status'] = 'known_within_named_rating_view'
            if point != old_point:
                changes.append({'profile': name, 'trial_index': trial['trial_index'], 'anonymous_answer_id': identifier,
                                'initial_C7': old_point, 'view_C7': point,
                                'initial_total': trial['initial_judge_total'], 'view_total': trial['judge']['total'],
                                'initial_quality_pass': trial['initial_quality_pass'], 'view_quality_pass': trial['quality_pass']})
    payload['C7_changes_or_unknowns'] = changes
    return payload


scorer_payload = rating_view('diagnostic_original_scorer_c7_clarification', lambda i: c7_by_id[i]['reviewed_C7_points'])
consistency_payload = rating_view('diagnostic_independent_consistency_c7_interpretation', lambda i: concordance_by_id[i]['independent_consistency_reviewer_precomparison_result']['C7_points'])
primary_payload = rating_view('primary_original_rubric_with_3_unknown_c7_judgments', lambda i: completion['agreed_C7_points_by_anonymous_id'].get(i))
for profile in scorer_payload['cases'][0]['profiles'].values():
    for trial in profile['trials']:
        row = c7_by_id[trial['anonymous_answer_id']]
        assert row['answer_sha256'] == trial['answer_sha256']
        assert trial['judge']['total'] == row['reviewed_total_points']
        assert trial['quality_pass'] is row['reviewed_quality_pass']
unknown_scores = sum(t['judge']['total'] is None for p in primary_payload['cases'][0]['profiles'].values() for t in p['trials'])
unknown_quality = sum(t['quality_pass'] is None for p in primary_payload['cases'][0]['profiles'].values() for t in p['trials'])
assert unknown_scores == 3 and unknown_quality == 2
write(OUT / 'analysis-input.json', primary_payload)
write(OUT / 'analysis-input-scorer-c7.json', scorer_payload)
write(OUT / 'analysis-input-consistency-c7.json', consistency_payload)
sys.path.insert(0, str(H))
import q001_repeat_analysis_v1
reports = {}
for name, payload, input_name, output_name in [
    ('initial', initial_payload, 'analysis-input-initial-grades.json', 'analysis-report-initial-grades.json'),
    ('scorer_c7', scorer_payload, 'analysis-input-scorer-c7.json', 'analysis-report-scorer-c7.json'),
    ('consistency_c7', consistency_payload, 'analysis-input-consistency-c7.json', 'analysis-report-consistency-c7.json')]:
    report = q001_repeat_analysis_v1.analyze(payload['cases'])
    assert report['bootstrap']['seed'] == 20261020 and report['bootstrap']['resamples'] == 10000
    report = {'rating_view': payload['rating_view'], 'diagnostic_only_due_to_primary_c7_unknowns': True,
              'analysis_input_sha256': hashlib.sha256((OUT / input_name).read_bytes()).hexdigest(),
              'measurement_scope': payload['measurement_scope'], 'time_scope': payload['time_scope'], **report}
    write(OUT / output_name, report)
    reports[name] = report
raw_comparisons = {}
for metric in ('input_reduction_pct', 'agent_time_reduction_pct', 'outer_slot_reduction_pct'):
    rows = [r['repeated_estimates']['q001']['raw'] for r in reports.values()]
    assert all(row['comparison'][metric] == rows[0]['comparison'][metric] and row['intervals'][metric] == rows[0]['intervals'][metric] for row in rows)
    raw_comparisons[metric] = {'point': rows[0]['comparison'][metric], 'interval': rows[0]['intervals'][metric]}
report = {'schema_version': 1, 'rating_view': primary_payload['rating_view'],
          'status': 'unavailable_complete_primary_quality_and_score_trimmed_statistics',
          'reason': 'Three C7 scores remain unknown after independent blind reviews; no complete quality/score-tail selection is invented. All20 executions remain retained.',
          'analysis_input_sha256': hashlib.sha256((OUT / 'analysis-input.json').read_bytes()).hexdigest(),
          'planned_solver_executions': 20, 'retained_solver_executions': 20,
          'exact_scores_available': 17, 'unknown_scores': unknown_scores,
          'exact_quality_pass_decisions_available': 18, 'unknown_quality_pass_decisions': unknown_quality,
          'diagnostic_views': {name: r['rating_view'] for name, r in reports.items()},
          'unknowns_zero_filled_or_dropped': False,
          'raw_resource_comparisons_rating_invariant': raw_comparisons,
          'all_execution_scope_and_reported_metrics_valid': False,
          'stable_reported_input_with_quality_preserved_on_q001': False,
          'source_scope_summary': scope['summary'],
          'measurement_scope': initial_payload['measurement_scope'], 'time_scope': initial_payload['time_scope'],
          'task_mad': 'unavailable_insufficient_tasks: one fixed task; ten repetitions are not ten tasks',
          'scope': 'One fixed q001 case and pinned stack only. Whole-cohort strict validity already fails independently of C7.'}
write(OUT / 'analysis-report.json', report)
write(OUT / 'method-bindings.json', {
    'schema_version': 1,
    'files': {str(p): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
              for p in [Path(__file__), H / 'q001_repeat_analysis_v1.py', H / 'repeat_analysis_v1.py', H / 'robust_statistics_v1.py']},
    'statistical_controls_changed': False, 'quality_or_scope_failures_rehabilitated_by_trimming': False})
print(json.dumps({'all20_executions_retained': True, 'primary_unknown_scores': unknown_scores,
                  'diagnostic_views': 3, 'bootstrap_resamples_per_view': 10000,
                  'stable_reported_input_with_quality_preserved_on_q001': False, 'output': str(OUT)}))
