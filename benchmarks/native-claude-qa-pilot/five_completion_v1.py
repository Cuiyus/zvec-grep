"""Complete only an independently verified pre-agent preparation failure.

This supplemental pair is never substituted into the original 100-row primary
study. A distinct completion view may show the user's ten actual executions,
with both the original absence and this later technical amendment disclosed.
"""
from __future__ import annotations
import argparse, copy, json, subprocess
from pathlib import Path
import five_repeat_v1 as five

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
ORIGINAL_RUN=39392884840
ORIGINAL_HEAD='df0d37796435a596b4f6d62215a8fe28f401774a'
ORIGINAL_PLAN_SHA='cbd1e29c7badfe005c5e36392b9eeb7e26b016b890a92e44d5251096f6af9d54'
PLAN=HERE/'five-completion-plan-v1.json'
PAIR=five.selected_pair('q004',4)

def eligible_job(job):
    if (job.get('name')!='repeat (q004-r04)' or job.get('head_sha')!=ORIGINAL_HEAD
        or job.get('run_id')!=ORIGINAL_RUN or job.get('run_attempt')!=1
        or job.get('status')!='completed' or job.get('conclusion')!='failure'):return False
    steps=job.get('steps',[])
    setup=[s for s in steps if s.get('name')=='Run ./.github/actions/native-five-setup']
    execute=[s for s in steps if s.get('name')=='Execute exactly the scheduled original native pair']
    return (len(setup)==len(execute)==1 and setup[0].get('conclusion')=='failure'
        and execute[0].get('conclusion')=='skipped')

def original_binding(run,job,*,require_terminal):
    if (run.get('id')!=ORIGINAL_RUN or run.get('head_sha')!=ORIGINAL_HEAD or run.get('run_attempt')!=1
        or run.get('path')!='.github/workflows/native-five-repeat-v1.yml' or run.get('event')!='workflow_dispatch'
        or run.get('repository',{}).get('full_name')!='Cuiyus/zvec-grep' or not eligible_job(job)):
        raise ValueError('only_the_bound_pre_agent_failure_may_be_completed')
    if require_terminal and run.get('status')!='completed':raise ValueError('original_cohort_must_finish_before_supplement')
    return True

def validate_completion(plan,repo=REPO):
    if (plan.get('schema_version')!=1 or plan.get('protocol_id')!='native-five-pre-agent-completion-v1'
        or plan.get('frozen') is not True or plan.get('original_run_id')!=ORIGINAL_RUN
        or plan.get('original_head')!=ORIGINAL_HEAD or plan.get('original_plan_sha256')!=ORIGINAL_PLAN_SHA
        or plan.get('pair')!=PAIR or plan.get('scheduled_supplemental_solver_trials')!=2
        or plan.get('n_attempts_per_pair')!=1 or plan.get('max_retries')!=0
        or plan.get('replace_original_missing_primary_rows') is not False):raise ValueError('supplement_controls_changed')
    expected={'.github/workflows/native-five-completion-v1.yml','.github/actions/native-five-completion-setup/action.yml',
        'benchmarks/native-claude-qa-pilot/five_completion_v1.py','benchmarks/native-claude-qa-pilot/test_five_completion_v1.py'}
    if set(plan['frozen_harness_sha256'])!=expected:raise ValueError('completion_pins_incomplete')
    for path,digest in plan['frozen_harness_sha256'].items():
        if five.candidate.digest(repo/path)!=digest:raise ValueError('completion_control_changed:'+path)
    if five.candidate.digest(HERE/'five-repeat-plan-v1.json')!=ORIGINAL_PLAN_SHA:raise ValueError('original_plan_changed')
    if plan['grading']['planned_answers']!=2 or not five.candidate.is_sha(plan['grading']['assignment_sha256']):
        raise ValueError('supplement_anonymous_assignment_not_frozen')
    return plan

def api(endpoint):
    return json.loads(subprocess.run(['gh','api',endpoint],capture_output=True,text=True,check=True).stdout)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('dry-run','run','collect'))
    for name in ('main-root','reference-prepared','prepared-group','candidate','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();completion=validate_completion(five.live.pilot.load(PLAN))
    run=api(f'repos/Cuiyus/zvec-grep/actions/runs/{ORIGINAL_RUN}')
    job=api('repos/Cuiyus/zvec-grep/actions/jobs/118529165394')
    original_binding(run,job,require_terminal=args.command!='dry-run')
    parent=args.output.parent;parent.mkdir(parents=True,exist_ok=True)
    for name,data in [('original-run-at-completion-admission.json',run),('original-unstarted-job.json',job)]:
        target=parent/name
        if target.exists():
            previous=five.live.pilot.load(target)
            # Original conclusion may become terminal between preflight and run;
            # each hosted job has its own exclusive admission records.
            if previous!=data and args.command!='collect':raise ValueError('admission_already_written')
        else:five.candidate.write(target,data)
    native_args=copy.copy(args);native_args.plan=HERE/'five-repeat-plan-v1.json'
    native_args.task_id='q004';native_args.repetition=4
    plan,base,proof,manifest=five.validate_plan(native_args)
    if args.command!='collect':
        context={'schema_version':1,'protocol_id':completion['protocol_id'],'supplement_plan_sha256':five.candidate.digest(PLAN),
            'original_run_id':ORIGINAL_RUN,'original_job_id':job['id'],'original_execution_step_skipped':True,
            'pair':PAIR,'original_missing_rows_preserved':True,'replace_original_missing_primary_rows':False,
            'reason':'Environment preparation failed before either native agent execution; no executed outcome selected or repeated.',
            'dry_run':args.command=='dry-run'}
        five.candidate.write(parent/'supplement-context.json',context)
        (parent/'five-completion-plan-v1.json').write_bytes(PLAN.read_bytes())
    return five.execute(native_args,plan,base,proof,manifest)

if __name__=='__main__':raise SystemExit(main())
