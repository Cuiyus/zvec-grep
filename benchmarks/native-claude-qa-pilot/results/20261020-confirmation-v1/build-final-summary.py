import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

B = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
R = W / 'benchmarks/native-claude-qa-pilot/results/20261020-confirmation-v1'
PLAN_SHA = 'cd5f20711d92615fb75aebffd5a295261b4b341d0f558cd6a9c36a5efd472e5a'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def read(p):
    return json.loads(p.read_text())

def ref(p):
    return {'path': str(p.relative_to(W)) if p.is_relative_to(W) else str(p), 'sha256': sha(p)}

gp = B / 'native-claude-confirmation-v1-grading/grades.json'
cp = gp.parent / 'consistency-review.json'
ap = B / 'native-claude-confirmation-v1-grading-private/assignment.json'
assert sha(gp) == '82e4b138748ca6b8e6f86980e0c622ad6ea998bac945cf1e76a50820c20f655c'
assert sha(cp) == '5d3c80365db0dc3973ddf4e4537cd22e6f0a5b95663c67394744b3050b6a8949'
assert sha(ap) == '0047206e32d9c2b9e89e3d9ae17160ac4456a6b15529ef4e9a118c4d112f8d9f'
g, c, assignment = read(gp), read(cp), read(ap)
assert c['review_status'] == 'passed_with_non_scoring_documentation_erratum'
assert c['original_grades_unchanged'] and not c['new_scores_assigned']
for key in ('scoring_changes_established', 'core_citation_gate_changes_established', 'primary_gate_changes_established', 'sensitivity_gate_changes_established', 'original_grade_bytes_changed'):
    assert c['impact_summary'][key] is False
assert assignment['created_before_answers'] and assignment['plan_sha256'] == PLAN_SHA
assert datetime.fromisoformat(assignment['created_at_utc'].replace('Z', '+00:00')) < datetime.fromisoformat('2026-10-19T23:49:00+00:00')
grades = {a['blinded_answer_id']: a for a in g['answers']}
assert len(grades) == 4 and set(grades) == set(assignment['assignment'])
mapping = {(v['pair'], v['profile']): k for k, v in assignment['assignment'].items()}
assert set(mapping) == {(pair, profile) for pair in ('c01', 'c02') for profile in ('baseline', 'zvec-grep')}

pairs = []
for pair, order in [('c01', ['baseline', 'zvec-grep']), ('c02', ['zvec-grep', 'baseline'])]:
    raw = B / 'native-claude-confirmation-v1-ci' / pair / 'raw'
    col = read(raw / 'live/collection/collection.json')
    execution_path = B / 'native-claude-confirmation-v1-audit' / (pair + '-execution-review.json')
    scope_path = B / 'native-claude-confirmation-v1-audit' / (pair + '-scope/report.json')
    execution, scope = read(execution_path), read(scope_path)
    assert col['plan_sha256'] == PLAN_SHA and col['protocol_id'] == 'native-claude-q001-confirmation-v1'
    assert execution['passed'] and execution['failed_checks'] == []
    assert scope['source_scope_valid'] and scope['all_actual_calls_reviewed']
    rows = {}
    for pr in col['profiles']:
        profile = pr['profile']
        aid = mapping[(pair, profile)]
        answer = raw / 'live/collection' / (profile + '-answer.txt')
        anonymous = B / 'native-claude-confirmation-v1-blind-packet' / (aid + '.txt')
        assert answer.read_bytes() == anonymous.read_bytes() and sha(answer) == pr['answer_sha256']
        grade = grades[aid]
        points = {x['id']: x['points'] for x in grade['criterion_scores']}
        assert set(points) == {'C' + str(i) for i in range(1, 8)}
        assert sum(points.values()) == grade['primary_total']
        core = all(points[x] == 2 for x in ('C1', 'C2', 'C3'))
        assert core == grade['core_criteria_full_credit']
        assert grade['primary_pass'] == (grade['primary_total'] >= 8 and core and grade['core_citations_valid'] and not grade['critical_contradictions'])
        usage = pr['claude_reported_model_usage']['usage']
        assert usage['input_tokens'] == sum(usage[x] for x in ('uncached_input_tokens', 'cache_read_tokens', 'cache_write_tokens'))
        for k, value in usage.items():
            assert value == pr['strict_usage'][k]
        rows[profile] = {
            'profile': profile, 'anonymous_answer_id': aid, 'answer_sha256': sha(answer),
            'trial_directory': pr['trial_directory'],
            'execution_valid': execution['passed'] and scope['source_scope_valid'] and pr['structural_valid'],
            'rubric_points': grade['primary_total'], 'criterion_points': points,
            'quality_pass': grade['primary_pass'], 'core_criteria_full_credit': core,
            'core_citations_valid': grade['core_citations_valid'],
            'critical_contradiction_count': len(grade['critical_contradictions']),
            'noncritical_unsupported_claim_count': len(grade['unsupported_claims']),
            'sensitivity': grade['sensitivity'],
            'reported_usage': usage, 'strict_persisted_usage': pr['strict_usage'],
            'metric_corroboration': pr['metric_consistency'],
            'reported_metric_eligible': pr['reported_metric_eligible'],
            'session_count': pr['session_count'], 'child_count': pr['child_count'],
            'native_run_wall_seconds': pr['native_run_wall_seconds'],
            'outer_slot_wall_seconds': pr['outer_slot_wall_seconds'],
            'harbor_timing': pr['harbor_timing'], 'native_zg_setup_timing': pr['native_zg_setup_timing'],
            'source_audit_wall_seconds': pr['source_audit_wall_seconds'],
            'log_export_wall_seconds': pr['log_export_wall_seconds'],
        }
    base, zg = rows['baseline'], rows['zvec-grep']
    checks = {
        'both_executions_valid': all(r['execution_valid'] for r in rows.values()),
        'both_original_quality_gates_pass': all(r['quality_pass'] for r in rows.values()),
        'zg_rubric_points_at_least_baseline': zg['rubric_points'] >= base['rubric_points'],
        'both_reported_usage_metrics_eligible': all(r['reported_metric_eligible'] for r in rows.values()),
        'zg_reported_input_strictly_lower': zg['reported_usage']['input_tokens'] < base['reported_usage']['input_tokens'],
        'successful_relevant_correct_workspace_zg_retrieval': scope['successful_relevant_zg_retrieval'],
    }
    changes = {
        'reported_input_including_cache_pct': 100 * (zg['reported_usage']['input_tokens'] / base['reported_usage']['input_tokens'] - 1),
        'native_run_wall_pct': 100 * (zg['native_run_wall_seconds'] / base['native_run_wall_seconds'] - 1),
        'outer_slot_wall_pct': 100 * (zg['outer_slot_wall_seconds'] / base['outer_slot_wall_seconds'] - 1),
    }
    pairs.append({
        'id': pair, 'profiles_in_execution_order': order, 'profiles': rows,
        'collection': ref(R / pair / 'raw/live/collection/collection.json'),
        'execution_review': ref(R / pair / 'review/execution' / (pair + '-execution-review.json')),
        'source_scope_review': ref(R / pair / 'review/scope/report.json'),
        'execution_checks_passed': execution['passed_checks'], 'tool_calls_reviewed': scope['total_unique_calls'],
        'collection_wall_seconds': col['collection_wall_seconds'],
        'primary_conditions': checks, 'primary_pass': all(checks.values()),
        'primary_failed_conditions': [k for k, v in checks.items() if not v],
        'quality_candidate': checks['both_executions_valid'] and zg['quality_pass'] and not base['quality_pass'] and checks['zg_rubric_points_at_least_baseline'] and scope['successful_relevant_zg_retrieval'],
        'observed_changes': changes,
        'sensitivity_primary_pass': checks['both_executions_valid'] and all(r['sensitivity']['pass_under_same_gate'] for r in rows.values()) and zg['sensitivity']['total'] >= base['sensitivity']['total'] and checks['both_reported_usage_metrics_eligible'] and checks['zg_reported_input_strictly_lower'] and scope['successful_relevant_zg_retrieval'],
    })

summary = {
    'schema_version': 1, 'status': 'completed_two_pair_confirmation_not_confirmed',
    'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
    'protocol_id': 'native-claude-q001-confirmation-v1', 'plan_sha256': PLAN_SHA,
    'repository': 'Cuiyus/zvec-grep', 'run_id': 39343873279, 'run_attempt': 1,
    'run_url': 'https://github.com/Cuiyus/zvec-grep/actions/runs/39343873279',
    'execution_head_sha': 'fd0c94c37c95e2afb4b5482530904574f5719c5f', 'run_conclusion': 'success',
    'model': 'qwen3.8-max', 'native_agent': 'Claude Code 2.1.212 via pinned upstream Harbor adapter',
    'upstream_main_commit': 'a424e0ce06868dc9ad8f142f57de59b8f9b58699',
    'rust_binary_sha256': '645ef8b62d62ff604c06f3c8aa34790c3c28bc65c946714ecdbf7175131b61dd',
    'task_id': 'q001', 'source_commit': 'ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d',
    'issue_url': 'https://github.com/alibaba/zvec/issues/594',
    'question_sha256': '94aad1d075ef44984c2e6c330bca8d3dc6c273c1c7044c20b4bdd47c6c7854ef',
    'rubric_sha256': '6da038c670a93213095bb3ac07c77a67f9863defa23f8ac4a3b6fd2af8a81cb9',
    'primary_definition': 'Both scheduled pairs separately satisfy every frozen condition. No development pooling, failed-pair dropping, cross-pair averaging, retries or replacement.',
    'pairs': pairs,
    'primary_confirmation_pass': all(p['primary_pass'] for p in pairs),
    'successful_primary_pairs': sum(p['primary_pass'] for p in pairs), 'planned_pairs': 2,
    'sensitivity_confirmation_pass': all(p['sensitivity_primary_pass'] for p in pairs),
    'repeated_observed_outer_slot_time_reduction': all(p['observed_changes']['outer_slot_wall_pct'] < 0 for p in pairs),
    'quality_candidate_pairs': [p['id'] for p in pairs if p['quality_candidate']],
    'favorable_benchmark_set_established': False,
    'grading': {
        'kind': 'Independent blinded source-grounded model review, not official SWE-QA or human grading',
        'grades': ref(R / 'grading/grades.json'), 'consistency_review': ref(R / 'grading/consistency-review.json'),
        'pre_dispatch_assignment': ref(R / 'grading/unblinding/assignment.json'),
        'packet_manifest': ref(R / 'grading/packet/manifest.json'),
        'checked_counts': c['checked_counts'], 'non_scoring_errata': c['issues'],
        'observation_notes': c['observations_and_scope_notes'],
        'scores_replaced': False, 'mapping_unblinded_only_after_grading_and_consistency_review_completed': True,
    },
    'ci_retention': ref(R / 'ci/retention-status.json'),
    'observed_tool_limits': [
        'c01 MCP output has one non-whitespace snippet difference: local_wal_file.h line34 returned } instead of };. Full original text and native locations are retained; no cause is inferred.',
        'c02 contains two environment-directory navigation calls. They returned names only, with no external source, evidence-file or gold content; all relevant factual source evidence remains in the frozen corpus. This is not a claim of filesystem or network isolation.',
        'c02 one local grep pattern caused an option-parser error; it is retained and not counted as successful retrieval.',
    ],
    'limitations': [
        'One previously observed issue, two scheduled confirmation pairs. This does not establish a favorable multi-task benchmark set or general causal benefit.',
        'All four answers retain noncritical unsupported auxiliary claims; rubric points are not a claim that every statement is correct.',
        'Reported input includes uncached input, cache reads and cache creation; complete persisted evidence corroborates these fields here. Provider billing, embedding cost and immutable provider weights remain unverified.',
        'Native execution, setup/index, source audit, log export, Harbor intervals and outer slot are different timing scopes. Outer slots exclude queue, common CI preparation and later collection.',
        'The lifecycle-only C6 sensitivity is separately recorded and does not replace primary scoring; it does not reverse the confirmation outcome.',
        'Earlier custom Claude, native OpenCode and native Claude development results remain separate and unchanged.',
    ],
    'stopping': 'The two planned pairs and independent audits are complete. Stop this q001 confirmation phase; no additional samples or prompt/rubric changes. Any next-task experiment requires a separate prospective decision.',
}
assert [p['primary_pass'] for p in pairs] == [False, True]
assert not summary['primary_confirmation_pass'] and not summary['sensitivity_confirmation_pass']
assert not summary['repeated_observed_outer_slot_time_reduction']
assert summary['quality_candidate_pairs'] == []
out = R / 'summary.json'
assert not out.exists(), 'Do not overwrite a retained final summary'
out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({'summary': str(out), 'sha256': sha(out), 'primary_confirmation_pass': summary['primary_confirmation_pass'], 'pairs': [{'id': p['id'], 'primary_pass': p['primary_pass'], 'changes': p['observed_changes']} for p in pairs]}, indent=2))
