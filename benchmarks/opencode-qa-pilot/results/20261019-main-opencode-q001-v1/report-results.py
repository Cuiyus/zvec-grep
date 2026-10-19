"""Rebuild the failed development phase summary from immutable raw evidence."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent


def load(name):
    return json.loads((ROOT / name).read_text())


def elapsed(value):
    parse = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00'))
    return (parse(value['finished_at']) - parse(value['started_at'])).total_seconds()


def main():
    run = load('run-metadata.json')
    artifact = load('artifacts-metadata.json')['artifacts'][0]
    plan = load('experiment-plan.json')
    execution = load('execution.json')
    review = load('diagnostic-blinded-review.json')['answers'][0]
    assert run['id'] == 39308807224 and run['run_attempt'] == 1
    assert run['head_sha'] == '5ccf538037a23c2fa17c97fc8d00fe657b65ba85'
    assert run['repository']['full_name'] == 'Cuiyus/zvec-grep'
    assert run['status'] == 'completed' and run['conclusion'] == 'failure'
    assert hashlib.sha256((ROOT / 'experiment-plan.json').read_bytes()).hexdigest() == execution['plan_sha256']
    assert artifact['digest'] == 'sha256:' + hashlib.sha256((ROOT / 'artifact.zip').read_bytes()).hexdigest()
    assert [s['profile'] for s in execution['slots']] == plan['profiles']
    assert all(s['container_cleanup']['complete'] for s in execution['slots'])
    answer = (ROOT / 'diagnostic-answer-K.txt').read_bytes()
    assert review['blinded_answer_id'] == 'K'
    assert review['answer_sha256'] == hashlib.sha256(answer).hexdigest()
    assert load('blinding-assignment-before-results.json')['mapping']['K'] == 'baseline'
    with ZipFile(ROOT / 'artifact.zip') as z:
        read = lambda p: json.loads(z.read(p))
        b = 'qa-run/runs/q001-baseline/zvec-q001__AWjMY7j/'
        t = 'qa-run/runs/q001-zvec-grep/zvec-q001__XWvyzhA/'
        result, audit, usage = [read(b + p) for p in ('result.json', 'qa-source-audit.json', 'agent/session-usage.json')]
        treatment, setup = [read(t + p) for p in ('result.json', 'agent/zvec-grep-setup.json')]
        assert result['exception_info'] is None and usage['complete']
        assert audit['before']['valid'] and audit['after']['valid']
        assert audit['errors'] == ['session_export:JSONDecodeError']
        assert not audit['session_exports_complete']
        assert treatment['agent_execution'] is None and treatment['agent_result'] is None
        assert setup['status'] == 'failed'
        assert treatment['exception_info']['exception_message'] == 'zvec-grep installed but ZG_BIN_PATH did not contain an absolute path'
        raw_wall = elapsed(result['agent_execution'])
        overhead = usage['collection_wall_seconds'] + audit['audit_wall_seconds'] + audit['export_wall_seconds']
        baseline = {
            'profile': 'baseline', 'answer_completed': True, 'protocol_valid': False,
            'status': 'completed_answer_required_native_export_missing',
            'source_before_after_valid': True, 'required_native_export_available': False,
            'diagnostic_text_quality': {
                'review_type': 'Independent blinded Codex model source audit, not human or official SWE-QA scoring',
                'points': review['total_points'],
                'criterion_points': {c['id']: c['points'] for c in review['criterion_scores']},
                'text_source_grounded_success': review['source_grounded_success'],
                'does_not_establish_protocol_validity': True,
                'answer_sha256': review['answer_sha256'],
            },
            'usage_scope': usage['scope'], 'persisted_usage_complete': usage['complete'],
            'persisted_usage': usage['total'],
            'raw_agent_execution_seconds': raw_wall,
            'usage_collection_seconds': usage['collection_wall_seconds'],
            'integrity_audit_seconds': audit['audit_wall_seconds'],
            'failed_native_export_seconds': audit['export_wall_seconds'],
            'solver_wall_seconds': raw_wall - overhead,
            'agent_setup_seconds': elapsed(result['agent_setup']),
            'native_trial_wall_seconds': elapsed(result),
            'outer_slot_seconds': execution['slots'][0]['finished_at_unix'] - execution['slots'][0]['started_at_unix'],
        }
        treatment_row = {
            'profile': 'zvec-grep', 'answer_completed': False, 'protocol_valid': False,
            'status': 'installation_failed_before_QA', 'source_before_after_valid': None,
            'diagnostic_text_quality': None, 'persisted_usage': None,
            'solver_wall_seconds': None, 'agent_setup_seconds': elapsed(treatment['agent_setup']),
            'zg_setup_attempt_seconds': setup['total_duration_seconds'],
            'native_trial_wall_seconds': elapsed(treatment),
            'outer_slot_seconds': execution['slots'][1]['finished_at_unix'] - execution['slots'][1]['started_at_unix'],
            'embedding_authorization_index_MCP_started': False,
            'QA_started': False, 'failure': setup['error'],
        }
    summary = {
        'schema_version': 1, 'protocol_id': plan['protocol_id'], 'phase': plan['phase'],
        'run_id': run['id'], 'run_url': run['html_url'], 'head_sha': run['head_sha'],
        'plan_sha256': execution['plan_sha256'], 'github_conclusion': run['conclusion'],
        'artifact_id': artifact['id'], 'artifact_digest': artifact['digest'],
        'slots_scheduled': 2, 'slots_attempted_once': 2, 'valid_completed_pairs': 0,
        'comparison_assessable': False, 'candidate_evaluation_status': 'not_assessable_invalid_pair',
        'quality_candidate': False, 'scoped_efficiency_candidate': False,
        'quality_or_efficiency_failure_of_zg_established': False,
        'stop_this_phase': True, 'redispatch_or_repair_in_this_phase': False,
        'goal_completed': False, 'rows': [baseline, treatment_row],
        'interpretation': 'Infrastructure failures prevent comparison. Keep the completed baseline answer only as diagnostic evidence; do not reuse it in a later pair or count missing treatment as zero quality/usage.',
        'future_work_rule': 'Any repair requires a separately documented infrastructure revision and real Harbor/Docker mock-provider integration before a separately frozen new pair. No live rerun is part of this archived phase.',
        'limits': [
            'Persisted-session input includes caches; output includes reasoning. Excludes unpersisted background/provider requests and embedding usage. Custom-provider dollar cost is unknown.',
            'Baseline native export output was not retained, so its exact failing payload cannot be recovered. stdout/ATIF answer bytes match; DB confirms the final message completed but the snapshot contains no answer text.',
            'Source hashes are controller before/after attestations, not an enforced read-only filesystem. Treatment never reached this audit.',
            'Native installation command output was not retained. Package metadata and the failing parser establish an integration defect, not a verified successful live installation.',
            'q001 is previously observed development material; no held-out, contamination-free, stable-set or cross-stack causal claim.',
        ],
    }
    (ROOT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({'summary': str(ROOT / 'summary.json'), 'comparison_assessable': False, 'valid_pairs': 0}))


if __name__ == '__main__':
    main()
