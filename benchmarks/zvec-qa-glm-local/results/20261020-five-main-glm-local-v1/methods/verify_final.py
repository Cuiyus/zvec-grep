"""Read-only checks on the original admitted GLM/local campaign and report."""
from pathlib import Path
import hashlib
import json
import sys
import datetime

WORKTREE = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
sys.path[:0] = [str(WORKTREE / 'benchmarks' / p) for p in
               ('zvec-qa-glm-local', 'opencode-qa-pilot', 'native-claude-qa-pilot')]
import cohort

ROOT = Path(__file__).resolve().parent
RAW = ROOT / 'live-ci/39414480210'
PLAN = WORKTREE / 'benchmarks/zvec-qa-glm-local/plan-v1.json'
RUN = 39414480210
HEAD = '4e72601048a2eed840b5bcb99105c4d9aa6314a8'
EXPECTED_PLAN = 'a145cb195eac4bc729fea1b642c9db480a360ddc14e5217d7f157683b03bcc99'


def verify_native_row(collection_path, row, pair):
    from zg_bench.agents.opencode_usage import summarize_session_usage
    from zg_bench.metrics.usage import validate_session_usage
    from repair_v2.qa_agents import snapshot_tree, validate_export
    trial=collection_path.parent/'runs'/(pair['task_id']+'-'+row['profile'])/row['retained_trials'][0]
    snapshot=cohort.load(trial/'agent/opencode-session-snapshot.json')
    usage=validate_session_usage(cohort.load(trial/'agent/session-usage.json'),require_identity=True)
    recomputed=summarize_session_usage(snapshot,row['root_session_id'],capture_complete=True)
    assert recomputed['complete'] and not recomputed['errors']
    for key,value in recomputed.items():assert usage[key]==value,key
    root,tree=snapshot_tree(snapshot)
    assert root==row['root_session_id']
    assert set(tree['session'])==set(row['source_audit']['exported_sessions'])
    for session_id in tree['session']:
        validate_export(cohort.load(trial/'qa-session-exports'/(session_id+'.json')),session_id,tree)
    for session in usage['sessions']:
        assert session['provider_models']==[{'provider_id':'custom-openai','model_id':'glm-5.2'}]
    final=cohort.pilot.final_answer(trial/'agent/opencode.txt',cohort.load(trial/'agent/trajectory.json'),
                                  usage,snapshot,cohort.load(trial/'qa-session-exports'/(root+'.json')))
    assert final['answer']==row['answer'] and final['answer_sha256']==row['answer_sha256']
    for metric in ('input_tokens','output_tokens','tool_calls'):
        assert row['metrics'][metric]==usage['total'][metric],metric
    timing=cohort.load(trial/'result.json')['agent_execution']
    parse=lambda value:datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
    seconds=(parse(timing['finished_at'])-parse(timing['started_at'])).total_seconds()-usage['collection_wall_seconds']
    assert abs(row['metrics']['agent_wall_seconds']-seconds)<0.000001
    return len(usage['sessions'])


def main():
    assert cohort.sha(PLAN) == EXPECTED_PLAN
    cohort.native(ROOT/'main/benchmarks/swe-qa-bench')
    run = cohort.load(RAW / 'final-run-metadata.json')
    assert run['id'] == RUN and run['head_sha'] == HEAD and run['run_attempt'] == 1
    assert run['event'] == 'workflow_dispatch' and run['status'] == 'completed'
    assert run['repository']['full_name'] == 'Cuiyus/zvec-grep'
    receipts = []
    for receipt in sorted(RAW.glob('*/artifact-byte-verification.json')):
        item = cohort.load(receipt)
        assert item['run_id'] == RUN and item['head_sha'] == HEAD and item['complete']
        assert cohort.sha(receipt.parent / 'artifact.zip') == item['artifact_sha256']
        for entry in item['files']:
            p = receipt.parent / 'raw' / entry['path']
            assert not p.is_symlink() and p.stat().st_size == entry['bytes']
            assert cohort.sha(p) == entry['sha256']
        receipts.append({'artifact_key': item['artifact_key'], 'zip_sha256': item['artifact_sha256']})
    proof = cohort.load(RAW / 'preflight/raw/native/proof.json')
    assert proof['valid'] and proof['paid_provider_calls'] == 0
    assert proof['remote_embedding_calls'] == 0 and proof['live_trial_count'] == 0
    assert proof['embedding'] == cohort.EMBEDDING and proof['plan_sha256'] == EXPECTED_PLAN
    fixture=cohort.load(RAW/'preflight/raw/native/native-pair/collection.json')
    expected_payloads=next(r['runtime']['zg']['payloads'] for r in fixture['profiles'] if r['profile']=='zvec-grep')
    expected = {p['key']: p for p in cohort.schedule()}
    seen = set(); sessions = set(); rows = []; missing = []; failures = []; revalidated_sessions=0
    for key, pair in expected.items():
        path = RAW / key / 'raw/output/collection.json'
        if not path.exists():
            missing.append(key); continue
        assert (RAW / key / 'artifact-byte-verification.json').exists()
        data = cohort.load(path)
        assert data['pair'] == pair and data['plan_sha256'] == EXPECTED_PLAN
        assert {r['profile'] for r in data['profiles']} == set(cohort.PROFILES)
        execution = cohort.load(path.parent / 'execution.json')
        assert execution['pair'] == pair and execution['dry_run'] is False
        assert execution['plan_sha256'] == EXPECTED_PLAN
        assert [r['profile'] for r in execution['slots']] == pair['profiles']
        judge_path = path.parent / 'judgements.json'
        judged = cohort.load(judge_path) if judge_path.exists() else {'profiles': []}
        if judge_path.exists():
            assert judged['plan_sha256'] == EXPECTED_PLAN
            assert judged['collection_sha256'] == cohort.sha(path)
            assert judged['agent_executions_retried'] is False
            assert judged['judge_prompt_has_condition_or_resources'] is False
        for row in data['profiles']:
            identity = (key, row['profile']); assert identity not in seen; seen.add(identity)
            assert row['task_id'] == pair['task_id'] and row['trial_index'] == pair['trial_index']
            if row['execution_valid']:
                assert row['answer'] is not None and not row['errors']
                assert hashlib.sha256(row['answer'].encode()).hexdigest() == row['answer_sha256']
                assert row['root_session_id'] not in sessions; sessions.add(row['root_session_id'])
                assert row['runtime']['opencode']['version'] == '1.18.4'
                assert row['runtime']['opencode']['binary_sha256'] == '6ce6570e7db9a40e7bd3304ebdfff607920bde8cafd2eb5587bd7a26f89ba0b5'
                assert row['source_audit']['valid'] and row['source_audit']['session_exports_complete']
                revalidated_sessions+=verify_native_row(path,row,pair)
                if row['profile'] == 'zvec-grep':
                    setup = row['zg_setup']
                    assert row['runtime']['zg']['binary_sha256'] == cohort.BINARY_SHA
                    assert row['runtime']['zg']['payloads'] == expected_payloads
                    assert setup['embedding_model'] == cohort.EMBEDDING and setup['status'] == 'ready'
                    assert setup['api_key_source'] is None and setup['index_cache_hit'] is False
                    assert setup['index_seed_available'] is False
            else:
                failures.append({'pair': key, 'profile': row['profile'], 'errors': row['errors']})
            matching = [j for j in judged['profiles'] if j['profile'] == row['profile']]
            assert len(matching) <= 1
            if matching and matching[0]['status'] == 'scored':
                j = matching[0]; value = j['judge']
                assert j['answer_sha256'] == row['answer_sha256']
                assert value['model'] == 'glm-5.2'
                assert set(value['scores']) == {'correctness','completeness','relevance','clarity','coherence'}
                assert all(type(v) is int and 1 <= v <= 20 for v in value['scores'].values())
                assert value['total'] == sum(value['scores'].values())
            rows.append({'pair': key, 'profile': row['profile'], 'native_valid': row['execution_valid'],
                         'scored': bool(matching and matching[0]['status'] == 'scored')})
    result = {'run_id': RUN, 'head_sha': HEAD, 'plan_sha256': EXPECTED_PLAN,
              'planned': 100, 'retained_native_rows': len(rows), 'missing_original_pairs': missing,
              'native_valid': sum(r['native_valid'] for r in rows), 'scored': sum(r['scored'] for r in rows),
              'native_usage_recomputed_from_snapshots': sum(r['native_valid'] for r in rows),
              'source_session_exports_revalidated': revalidated_sessions,
              'original_failures': failures, 'receipts': receipts,
              'new_provider_calls': 0, 'semantic_directory_scope_not_asserted': True}
    cohort.write(ROOT / 'final-evidence-verification.json', result)
    print(json.dumps({k: v for k, v in result.items() if k not in ('receipts', 'original_failures')}))


if __name__ == '__main__':
    main()
