"""One uniform quality wave after all original native jobs have terminated.

No Harbor agent is launched. All original planned positions are retained. The
initial competing-workload judgements are never consumed for score selection.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / 'benchmarks/zvec-qa-glm-local'))
import cohort
import judging

NATIVE_RUN = 39414480210
NATIVE_HEAD = '4e72601048a2eed840b5bcb99105c4d9aa6314a8'
NATIVE_PLAN_SHA = 'a145cb195eac4bc729fea1b642c9db480a360ddc14e5217d7f157683b03bcc99'
PROTOCOL = 'zvec-five-main-glm52-postrun-quality-v1'


def load_records(raw_root, plan_path):
    plan = cohort.load(plan_path); expected = {p['key']: p for p in plan['schedule']}
    found = {}
    for path in sorted(raw_root.rglob('collection.json')):
        item = cohort.load(path)
        if item.get('protocol_id') != cohort.PROTOCOL:
            continue
        if item.get('plan_sha256') != cohort.sha(plan_path):
            raise ValueError('wrong_original_native_plan')
        pair = item['pair']; key = pair['key']
        if key not in expected or pair != expected[key]:
            raise ValueError('wrong_original_pair_identity')
        if key in found:
            raise ValueError('duplicate_original_pair')
        if len(item['profiles']) != 2 or {r['profile'] for r in item['profiles']} != set(cohort.PROFILES):
            raise ValueError('original_pair_must_retain_both_profiles')
        found[key] = (path, item)
    records = []
    for pair in plan['schedule']:
        path, collection = found.get(pair['key'], (None, {'profiles': []}))
        for profile in pair['profiles']:
            row = next((r for r in collection['profiles'] if r['profile'] == profile), {})
            answer = row.get('answer'); answer_hash = row.get('answer_sha256')
            if answer is not None:
                if not isinstance(answer, str) or not answer or hashlib.sha256(answer.encode()).hexdigest() != answer_hash:
                    raise ValueError('original_answer_identity_mismatch')
            if row and (row['task_id'] != pair['task_id'] or row['trial_index'] != pair['trial_index']):
                raise ValueError('original_profile_identity_mismatch')
            records.append({'pair_key': pair['key'], 'task_id': pair['task_id'],
                            'trial_index': pair['trial_index'], 'profile': profile,
                            'answer': answer, 'answer_sha256': answer_hash,
                            'collection_path': path,
                            'collection_sha256': None if path is None else cohort.sha(path)})
    assert len(records) == 100
    return records, found


def evaluate(records, grade, *, workers=3):
    def one(record):
        result = {k: record[k] for k in ('pair_key','task_id','trial_index','profile','answer_sha256')}
        result.update(judge=None, status='unavailable_no_complete_answer', error_type=None)
        if record['answer'] is not None:
            try:
                result['judge'] = grade(record)
                result['status'] = 'scored'
            except Exception as error:
                result.update(status='judge_failed', error_type=type(error).__name__)
        return result
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(one, records))


def main(args):
    if args.output.exists():
        raise FileExistsError('never_restart_or_overwrite_uniform_quality_wave')
    for rel, digest in cohort.load(HERE / 'source-seal-v1.json')['controls'].items():
        if cohort.sha(REPO / rel) != digest:
            raise ValueError('postrun_quality_control_changed:' + rel)
    if cohort.sha(args.plan) != NATIVE_PLAN_SHA:
        raise ValueError('original_plan_changed')
    run = cohort.load(args.native_run_metadata)
    if (run['id'] != NATIVE_RUN or run['head_sha'] != NATIVE_HEAD or run['run_attempt'] != 1
        or run['status'] != 'completed' or run['event'] != 'workflow_dispatch'
        or run['repository']['full_name'] != 'Cuiyus/zvec-grep'):
        raise ValueError('original_workload_must_be_terminal_before_quality_wave')
    cohort.validate(args); cohort.native(args.main_root)
    from zg_bench.engines.judge import default_completion, judge_candidate, judge_concurrency
    from zg_bench.engines.registry import resolve_opencode_model
    if judge_concurrency() != 3:
        raise ValueError('unchanged_main_default_judge_concurrency_required')
    model = resolve_opencode_model(cohort.MODEL, require_credentials=True)
    records, found = load_records(args.raw_root, args.plan)
    args.output.mkdir(parents=True)
    amendment = HERE / 'amendment-v1.json'
    manifest = {'schema_version': 1, 'protocol_id': PROTOCOL,
                'native_run_id': NATIVE_RUN, 'native_head_sha': NATIVE_HEAD,
                'native_plan_sha256': NATIVE_PLAN_SHA,
                'native_terminal_metadata_sha256': cohort.sha(args.native_run_metadata),
                'amendment_sha256': cohort.sha(amendment), 'planned_positions': 100,
                'available_native_pairs': len(found), 'available_complete_answers': sum(r['answer'] is not None for r in records),
                'original_grade_used_for_selection': False, 'native_agent_executions_added': 0,
                'positions': [{k: str(v) if isinstance(v, Path) else v for k, v in row.items() if k != 'answer'} for row in records]}
    cohort.write(args.output / 'input-manifest.json', manifest)
    references = {task: judging.reference(task) for task in cohort.five.TASK_IDS}
    completion = default_completion(); calls = []; lock = threading.Lock()
    def grade(record):
        def recorded(**kwargs):
            started = time.time()
            item = {'pair_key': record['pair_key'], 'profile': record['profile'],
                    'answer_sha256': record['answer_sha256'], 'started_at_unix': started,
                    'request': {k: v for k, v in kwargs.items() if k in ('model','temperature','seed','reasoning_effort','max_tokens','messages','extra_body','allowed_openai_params')}}
            try:
                response = completion(**kwargs)
                data = response if isinstance(response, dict) else response.model_dump()
                item['response'] = {k: data.get(k) for k in ('id','model','object','created','choices','usage')}
                return response
            except Exception as error:
                item['error_type'] = type(error).__name__
                raise
            finally:
                item['finished_at_unix'] = time.time()
                with lock:
                    calls.append(item)
                    cohort.write(args.output / 'judge-requests.json', calls)
        ref = references[record['task_id']]
        return judge_candidate(completion_fn=recorded, api_key=model.api_key,
                               api_base=model.configuration.base_url, model='glm-5.2', attempts=3,
                               question=ref['question'], reference=ref['reference_answer'], candidate=record['answer'])
    results = evaluate(records, grade, workers=3)
    cohort.write(args.output / 'all100-judgements.json', {'schema_version': 1, 'protocol_id': PROTOCOL,
                 'input_manifest_sha256': cohort.sha(args.output / 'input-manifest.json'),
                 'amendment_sha256': cohort.sha(amendment), 'profiles': results,
                 'actual_judge_requests': len(calls), 'native_agent_executions_added': 0})
    for key, (path, collection) in found.items():
        target = args.output / 'pairs' / key; target.mkdir(parents=True)
        (target / 'collection.json').write_bytes(path.read_bytes())
        ref = references[collection['pair']['task_id']]
        cohort.write(target / 'judgements.json', {'schema_version': 1, 'protocol_id': PROTOCOL,
            'plan_sha256': NATIVE_PLAN_SHA, 'collection_sha256': cohort.sha(path),
            'amendment_sha256': cohort.sha(amendment),
            'method': 'Uniform postrun wave: unchanged main GLM-5.2 SWE-QA judge; concurrency3/attempts3',
            'reference': {k: v for k, v in ref.items() if k not in ('question','reference_answer')},
            'judge_prompt_has_condition_or_resources': False, 'agent_executions_retried': False,
            'profiles': [r for r in results if r['pair_key'] == key]})
    summary = {'protocol_id': PROTOCOL, 'native_run_id': NATIVE_RUN, 'planned': 100,
               'native_pairs_retained': len(found), 'scored': sum(r['status'] == 'scored' for r in results),
               'unavailable_no_answer': sum(r['status'] == 'unavailable_no_complete_answer' for r in results),
               'judge_failed': sum(r['status'] == 'judge_failed' for r in results),
               'actual_judge_requests': len(calls), 'native_agent_executions_added': 0,
               'completed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    cohort.write(args.output / 'summary.json', summary)
    print(json.dumps(summary))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('main-root','prepared','plan','candidate','raw-root','output','native-run-metadata'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    for key, value in vars(a).items():
        setattr(a, key, value.resolve())
    main(a)
