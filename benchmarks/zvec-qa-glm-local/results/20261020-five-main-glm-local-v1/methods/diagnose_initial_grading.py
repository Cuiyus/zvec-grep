"""Replay the recorded initial judge attempts without any provider calls."""
from pathlib import Path
from collections import Counter
import json
import sys

ROOT=Path(__file__).resolve().parent
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
sys.path.insert(0,str(W/'benchmarks/zvec-qa-glm-local'))
import cohort
import judging
cohort.native(ROOT/'main/benchmarks/swe-qa-bench')
from zg_bench.engines.judge import _judge_prompt,_parse_scores,_response_content

rows=[];transport=Counter()
for pair in cohort.schedule():
    root=ROOT/'live-ci/39414480210'/pair['key']/'raw/output'
    native=cohort.load(root/'collection.json');judged=cohort.load(root/'judgements.json')
    log=root/'judge-requests.json';calls=cohort.load(log) if log.exists() else []
    ref=judging.reference(pair['task_id']);index=0
    for original in native['profiles']:
        grade=next(r for r in judged['profiles'] if r['profile']==original['profile'])
        failures=[];attempts=0;success=None
        if original['answer'] is None:
            assert grade['status']=='unavailable_no_complete_answer'
        else:
            prompt=_judge_prompt(question=ref['question'],reference=ref['reference_answer'],candidate=original['answer'])
            while attempts<3:
                call=calls[index];index+=1;attempts+=1
                assert call['request']['messages']==[{'role':'user','content':prompt}]
                if call.get('error_type'):
                    failures.append(call['error_type']);transport[call['error_type']]+=1
                else:
                    try:success=_parse_scores(_response_content(call['response']))
                    except Exception as error:failures.append('response_parse:'+type(error).__name__)
                    else:break
            if success is not None:
                assert grade['status']=='scored' and grade['judge']['scores']==success
            else:assert grade['status']=='judge_failed'
        rows.append({'pair_key':pair['key'],'profile':original['profile'],'status':grade['status'],
                     'logged_completion_invocations':attempts,'attempt_failures':failures,
                     'all_three_attempts_rate_limited':attempts==3 and failures==['RateLimitError']*3})
    assert index==len(calls)
result={'native_run_id':39414480210,'positions':100,'initial_quality_statuses':dict(Counter(r['status'] for r in rows)),
        'transport_attempt_failures':dict(transport),'failed_judgements': [r for r in rows if r['status']=='judge_failed'],
        'failed_judgements_all_rate_limited':all(r['all_three_attempts_rate_limited'] for r in rows if r['status']=='judge_failed'),
        'all_recorded_attempts_replayed':True,'new_provider_calls':0,'profiles':rows}
cohort.write(ROOT/'initial-grading-diagnosis.json',result)
print(json.dumps({k:v for k,v in result.items() if k not in ('profiles','failed_judgements')}))
