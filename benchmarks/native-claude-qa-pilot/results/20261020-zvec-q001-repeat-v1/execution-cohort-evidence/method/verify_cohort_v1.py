"""Verify the completed fixed q001 execution cohort without grading or rerunning."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path

B = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
CI = B / 'native-zvec-q001-repeat-v1-ci'
A = B / 'native-zvec-q001-repeat-v1-audit'
HEAD = 'aa230d91eb426eb295cf95fe0fa2d80d52f588e2'
RUN = 39364155852
PLAN = 'ee43d475b53e6290a3ddad8e670106ffbbab65536baefa6324ea9194aa3f3b79'
KEYS = [f'q001-r{i:02d}' for i in range(1, 11)]
references = {}


def load(path):
    data = path.read_bytes()
    references[str(path)] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    return json.loads(data)


def verify():
    run = load(CI / 'final-run-metadata.json')
    assert run['id'] == RUN and run['head_sha'] == HEAD and run['run_attempt'] == 1
    assert run['status'] == 'completed' and run['conclusion'] == 'success'
    assert run['event'] == 'workflow_dispatch'
    assert run['path'] == '.github/workflows/native-claude-q001-repeat-v1.yml'
    assert run['repository']['full_name'] == 'Cuiyus/zvec-grep'
    job_document = load(CI / 'final-job-metadata.json')
    jobs = job_document['jobs']
    assert job_document['total_count'] == len(jobs) == 11
    assert all(j['run_id'] == RUN and j['head_sha'] == HEAD and j['run_attempt'] == 1
               and j['status'] == 'completed' and j['conclusion'] == 'success' for j in jobs)
    assert len([j for j in jobs if j['name'] == 'preflight']) == 1
    roots = []
    runner_ids = []
    volume_identities = []
    trial_directories = []
    pairs = []
    for index, key in enumerate(KEYS, 1):
        matches = [j for j in jobs if j['name'].startswith(f'repeat ({key},')]
        assert len(matches) == 1
        job = matches[0]
        runner_ids.append(job['runner_id'])
        pair = CI / key
        receipt = load(pair / 'artifact-byte-verification.json')
        assert receipt['complete'] and receipt['exact_extracted_inventory']
        assert (receipt['run_id'], receipt['head_sha'], receipt['run_attempt'], receipt['pair_key']) == (RUN, HEAD, 1, key)
        audit = load(A / 'reviews' / key / 'execution-review.json')
        assert audit['passed'] and audit['execution_valid'] and audit['metric_reconciliation_valid']
        assert not audit['failed_checks'] and audit['check_count'] == 63
        assert audit['run_id'] == RUN and audit['head_sha'] == HEAD and audit['trial_index'] == index
        assert audit['job_id'] == job['id'] and audit['cohort_plan_sha256'] == PLAN
        assert len(audit['profiles']) == 2
        raw = pair / 'raw'
        context = load(raw / 'live-q001-repeat-context.json')
        assert context['cohort_plan_sha256'] == PLAN and context['task_id'] == 'q001'
        assert context['trial_index'] == index and context['dry_run'] is False
        execution = load(raw / 'live/execution.json')
        collection = load(raw / 'live/collection/collection.json')
        assert len(execution['slots']) == len(collection['profiles']) == 2
        assert {p['profile'] for p in collection['profiles']} == {'baseline', 'zvec-grep'}
        assert {s['profile'] for s in execution['slots']} == {'baseline', 'zvec-grep'}
        for p in audit['profiles']:
            assert p['complete_review'] and p['strict_complete'] and p['reported_metric_eligible']
            assert p['primary_usage'] == p['strict_usage']
            roots.append(p['root_session_id'])
            trial_directories.append((job['runner_id'], p['trial_directory']))
            native = next(x for x in collection['profiles'] if x['profile'] == p['profile'])
            assert native['answer_present'] and native['answer_sha256'] == p['answer_sha256']
        for slot in execution['slots']:
            assert slot['status'] == 'exited' and slot['returncode'] == 0
            assert slot['process_cleanup']['complete'] and slot['container_cleanup']['complete']
            assert len(slot['fresh_volumes']) == 2
            volume_identities.extend((job['runner_id'], name) for name in slot['fresh_volumes'])
        pairs.append({'pair_key': key, 'trial_index': index, 'job_id': job['id'],
                      'runner_id': job['runner_id'], 'audit_checks_passed': 63})
    assert len(set(runner_ids)) == 10 and all(isinstance(i, int) and i > 0 for i in runner_ids)
    assert len(roots) == len(set(roots)) == 20
    assert len(trial_directories) == len(set(trial_directories)) == 20
    assert len(volume_identities) == len(set(volume_identities)) == 40
    return {'schema_version': 1, 'verified_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'run_id': RUN, 'head_sha': HEAD, 'run_attempt': 1, 'cohort_plan_sha256': PLAN,
            'passed': True, 'pairs': pairs, 'pair_count': 10, 'solver_execution_count': 20,
            'distinct_hosted_runner_ids': 10, 'distinct_root_session_ids': 20,
            'distinct_runner_qualified_volume_names': 40,
            'volume_identity_scope': 'runner ID plus native fresh volume name; names can repeat on separate hosted runners',
            'quality_or_source_scope_or_benefit_evaluated': False,
            'source_scope_note': 'Independent per-tool reviews remain separate; successful execution does not establish source scope compliance.',
            'references': references}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = verify()
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({k: result[k] for k in ['passed', 'pair_count', 'solver_execution_count', 'distinct_hosted_runner_ids', 'distinct_root_session_ids']}))
