"""First agent execution for one proven never-started original slot only."""
from __future__ import annotations
import argparse
import copy
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
sys.path.insert(0,str(REPO/'benchmarks/zvec-qa-glm-local'))
import cohort


def eligible(result, trial):
    for key in ('agent_setup','agent_execution','agent_result'):
        if key not in result or result[key] is not None:
            raise ValueError('an_agent_that_started_must_never_be_repeated')
    error=result.get('exception_info') or {}
    message=error.get('exception_message','')
    if (error.get('exception_type')!='RuntimeError' or 'Docker compose command failed' not in message
        or ' build.' not in message or "Could not resolve 'archive.ubuntu.com'" not in message):
        raise ValueError('only_proven_pre_agent_container_build_DNS_failure_is_eligible')
    for name in ('opencode.txt','session-usage.json','opencode-session-snapshot.json','trajectory.json'):
        if (trial/'agent'/name).exists():
            raise ValueError('prior_agent_evidence_forbids_completion')
    if list((trial/'qa-session-exports').glob('*.json')):
        raise ValueError('prior_agent_session_export_forbids_completion')


def run(args):
    recovery_plan=cohort.load(HERE/'plan-v1.json')
    for rel,digest in cohort.load(HERE/'source-seal-v1.json')['controls'].items():
        if cohort.sha(REPO/rel)!=digest:raise ValueError('recovery_control_changed:'+rel)
    plan=cohort.validate(args)
    if cohort.sha(args.plan)!=recovery_plan['native_plan_sha256']:
        raise ValueError('wrong_original_native_plan')
    if args.output.exists():raise FileExistsError('never_repeat_the_completion_run')
    old=cohort.load(args.original/'collection.json')
    if old['pair']['key']!='q004-r09' or old['plan_sha256']!=cohort.sha(args.plan):
        raise ValueError('wrong_original_failed_slot')
    target=next(r for r in old['profiles'] if r['profile']=='zvec-grep')
    if target['execution_valid'] is not False or target['answer'] is not None:
        raise ValueError('never_replace_an_available_original_execution')
    results=list((args.original/'runs/q004-zvec-grep').glob('*/result.json'))
    if len(results)!=1 or cohort.sha(results[0])!=recovery_plan['original_result_sha256']:
        raise ValueError('original_failure_identity_changed')
    eligible(cohort.load(results[0]),results[0].parent)
    old_execution=cohort.load(args.original/'execution.json')
    old_slot=next(s for s in old_execution['slots'] if s['profile']=='zvec-grep')
    failure_seconds=old_slot['finished_at_unix']-old_slot['started_at_unix']
    args.output.mkdir(parents=True)
    cohort.write(args.output/'admission-proof.json',{
        'protocol_id':recovery_plan['protocol_id'],'recovery_plan_sha256':cohort.sha(HERE/'plan-v1.json'),
        'original_failed_result_sha256':cohort.sha(results[0]),
        'original_collection_sha256':cohort.sha(args.original/'collection.json'),
        'prior_agent_setup_started':False,'prior_agent_execution_started':False,
        'prior_agent_context_available':False,'prior_stream_or_session_evidence':False,
        'original_failure_outer_seconds':failure_seconds,'only_profile_executed':'zvec-grep',
        'successful_original_profiles_repeated':0,'original_failed_slot_overwritten':False,
        'new_host_for_logical_pair':True,'source_run_id':os.environ.get('GITHUB_RUN_ID'),
        'source_head_sha':os.environ.get('GITHUB_SHA')})
    selected=copy.copy(args);selected.output=args.output/'native'
    pair=next(p for p in plan['schedule'] if p['key']=='q004-r09')
    single={**pair,'profiles':['zvec-grep']}
    cohort.run_pair(selected,plan,single)
    collected=cohort.collect(selected,plan,single)
    row=collected['profiles'][0]
    native_results=list((selected.output/'runs/q004-zvec-grep').glob('*/result.json'))
    result=cohort.load(native_results[0]) if len(native_results)==1 else {}
    summary={'protocol_id':recovery_plan['protocol_id'],'source_run_id':os.environ.get('GITHUB_RUN_ID'),
        'source_head_sha':os.environ.get('GITHUB_SHA'),'pair_key':'q004-r09','profile':'zvec-grep',
        'actual_agent_execution_started':result.get('agent_execution') is not None,
        'original_failed_agent_execution_started':False,'native_valid':row['execution_valid'],
        'answer_sha256':row.get('answer_sha256'),'native_collection_sha256':cohort.sha(selected.output/'collection.json'),
        'original_failure_outer_seconds':failure_seconds,
        'recovery_outer_seconds':(row.get('metrics') or {}).get('outer_slot_seconds'),
        'completed_slot_outer_including_first_environment_failure_seconds':None if row.get('metrics') is None else failure_seconds+row['metrics']['outer_slot_seconds'],
        'new_host_for_logical_pair':True,'original_primary_data_unchanged':True,
        'completed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    cohort.write(args.output/'recovery-summary.json',summary)
    print(json.dumps(summary))


def judge(args):
    import judging
    selected=copy.copy(args);selected.output=args.output/'native'
    data=cohort.load(selected.output/'collection.json')
    if data['pair']['key']!='q004-r09' or len(data['profiles'])!=1 or data['profiles'][0]['profile']!='zvec-grep':
        raise ValueError('singleton_quality_input_must_match_admitted_recovery')
    # Reuse the unchanged logging and main judge call. Its legacy human-only
    # stdout assumes two profiles; this adapter prints the actual singleton count.
    with contextlib.redirect_stdout(io.StringIO()):judging.judge(selected)
    result=cohort.load(selected.output/'judgements.json')
    assert len(result['profiles'])==1
    print(json.dumps({'planned':1,'scored':sum(r['status']=='scored' for r in result['profiles']),
                      'actual_judge_requests':result['actual_judge_requests'],'new_native_agent_executions':0}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['run','judge'])
    for name in ('main-root','prepared','plan','candidate','original','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    for key,value in vars(a).items():
        if isinstance(value,Path):setattr(a,key,value.resolve())
    run(a) if a.command=='run' else judge(a)
