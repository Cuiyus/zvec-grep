"""Pinned-main self-judging; labels/resources never enter the judge prompt."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import threading
import time
import cohort


def reference(task):
    rubric = cohort.REPO / f'benchmarks/zvec-issue-qa/evaluator/{task}.json'
    data = cohort.load(rubric)
    answer = data.get('reference_answer')
    if answer is None:
        answer = '\n'.join(unit['id'] + ': ' + unit['claim'] for unit in data['units'])
    return {'question': (cohort.REPO / f'benchmarks/zvec-issue-qa/tasks/{task}/instruction.md').read_text(),
        'reference_answer': answer, 'frozen_rubric_sha256': cohort.sha(rubric),
        'reference_answer_sha256': hashlib.sha256(answer.encode()).hexdigest()}


def judge(args):
    cohort.validate(args); cohort.native(args.main_root)
    from zg_bench.engines.judge import judge_candidate, default_completion
    from zg_bench.engines.registry import resolve_opencode_model
    import os
    execution = cohort.load(args.output / 'collection.json')
    if (args.output / 'judgements.json').exists(): raise FileExistsError('judgements_already_exist')
    model = resolve_opencode_model(cohort.MODEL, require_credentials=True)
    completion = default_completion(); calls = []; lock = threading.Lock()
    def recorded_completion(**kwargs):
        start = time.time()
        item = {'started_at_unix': start,
            'request': {k: v for k, v in kwargs.items() if k in ('model', 'temperature', 'seed',
                'reasoning_effort', 'max_tokens', 'messages', 'extra_body', 'allowed_openai_params')}}
        try:
            response = completion(**kwargs)
            root = response if isinstance(response, dict) else response.model_dump()
            item['response'] = {k: root.get(k) for k in ('id', 'model', 'object', 'created', 'choices', 'usage')}
            return response
        except Exception as exc:
            item['error_type'] = type(exc).__name__; raise
        finally:
            item['finished_at_unix'] = time.time()
            with lock:
                calls.append(item); cohort.write(args.output / 'judge-requests.json', calls)
    ref = reference(execution['pair']['task_id']); result = []
    for row in execution['profiles']:
        item = {k: row[k] for k in ('task_id', 'trial_index', 'profile', 'answer_sha256')}
        item.update(judge=None, status='unavailable_no_complete_answer', error_type=None)
        if row['answer'] is not None:
            try:
                item['judge'] = judge_candidate(completion_fn=recorded_completion,
                    api_key=model.api_key, api_base=model.configuration.base_url,
                    question=ref['question'], reference=ref['reference_answer'], candidate=row['answer'],
                    attempts=3, model='glm-5.2')
                item['status'] = 'scored'
            except Exception as exc:
                item.update(status='judge_failed', error_type=type(exc).__name__)
        result.append(item)
    out = {'schema_version': 1, 'protocol_id': cohort.PROTOCOL,
        'recorded_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'plan_sha256': cohort.sha(args.plan), 'collection_sha256': cohort.sha(args.output / 'collection.json'),
        'method': 'unchanged main judge_candidate GLM-5.2 five-dimension 100-point self-judge',
        'reference': {k: v for k, v in ref.items() if k not in ('question', 'reference_answer')},
        'judge_prompt_has_condition_or_resources': False, 'profiles': result,
        'actual_judge_requests': len(calls), 'agent_executions_retried': False}
    cohort.write(args.output / 'judgements.json', out)
    print(json.dumps({'scored': sum(r['status'] == 'scored' for r in result), 'planned': 2,
        'actual_judge_requests': len(calls)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('main-root', 'prepared', 'plan', 'candidate', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    for k, v in vars(args).items(): setattr(args, k, v.resolve())
    judge(args)
