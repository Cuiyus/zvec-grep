"""One supplemental campaign for all proven pre-agent setup failures.

Selection uses only the settled original setup/execution-step states. Original
primary absences remain unchanged. A later completion view is explicitly a
post-dispatch infrastructure amendment, never a retry of a started agent.
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
PROTOCOL='native-five-pre-agent-completion-v2'
PLAN=HERE/'five-completion-plan-v2.json'
CONTROLS={'.github/workflows/native-five-completion-v2.yml','.github/actions/native-five-completion-setup-v2/action.yml',
    'benchmarks/native-claude-qa-pilot/five_completion_v2.py','benchmarks/native-claude-qa-pilot/test_five_completion_v2.py'}

def missing_pairs(run,jobs):
    """All fifty setups must settle; no answer, score or resource inputs."""
    if (run.get('id')!=ORIGINAL_RUN or run.get('head_sha')!=ORIGINAL_HEAD or run.get('run_attempt')!=1
        or run.get('path')!='.github/workflows/native-five-repeat-v1.yml' or run.get('event')!='workflow_dispatch'
        or run.get('repository',{}).get('full_name')!='Cuiyus/zvec-grep'
        or run.get('status') not in ('queued','in_progress','completed')
        or run.get('conclusion') not in (None,'success','failure')):raise ValueError('original_cohort_must_remain_bound_and_not_cancelled')
    rows=jobs['jobs'];expected={'repeat ('+p['key']+')':p for p in five.schedule()}
    indexed={}
    for job in rows:
        if job.get('name') in expected:
            if (job['name'] in indexed or job.get('run_id')!=ORIGINAL_RUN or job.get('head_sha')!=ORIGINAL_HEAD
                or job.get('run_attempt')!=1 or job.get('status') not in ('in_progress','completed')
                or job.get('conclusion')=='cancelled'):raise ValueError('all_original_jobs_must_have_started_and_remain_bound')
            indexed[job['name']]=job
    if set(indexed)!=set(expected):raise ValueError('all_fifty_original_jobs_required')
    selected=[]
    for name,pair in expected.items():
        job=indexed[name];steps=job.get('steps',[])
        setup=[s for s in steps if s.get('name')=='Run ./.github/actions/native-five-setup']
        execute=[s for s in steps if s.get('name')=='Execute exactly the scheduled original native pair']
        if len(setup)!=1 or len(execute)!=1:raise ValueError('original_execution_step_state_missing')
        if setup[0].get('conclusion') not in ('success','failure'):raise ValueError('all_fifty_original_setups_must_settle')
        if setup[0].get('conclusion')=='failure' and (job.get('status')!='completed' or job.get('conclusion')!='failure'
            or execute[0].get('conclusion')!='skipped'):raise ValueError('failed_setup_must_finish_without_native_execution')
        if (job.get('conclusion')=='failure' and setup[0].get('conclusion')=='failure'
            and execute[0].get('conclusion')=='skipped'):
            selected.append({**pair,'original_job_id':job['id']})
    return selected

def parallel_capacity(jobs,supplement_pairs):
    # Once every original setup has settled, original active-job count can only
    # fall. Keep combined original/supplemental native pair concurrency <=20.
    active=sum(j.get('name','').startswith('repeat (') and j.get('status')=='in_progress' for j in jobs['jobs'])
    return active+min(20,supplement_pairs)<=20

def validate_completion(plan,repo=REPO):
    pairs=plan.get('pairs',[])
    if (plan.get('schema_version')!=1 or plan.get('protocol_id')!=PROTOCOL or plan.get('frozen') is not True
        or plan.get('original_run_id')!=ORIGINAL_RUN or plan.get('original_head')!=ORIGINAL_HEAD
        or plan.get('original_plan_sha256')!=ORIGINAL_PLAN_SHA or not 1<=len(pairs)<=50
        or plan.get('scheduled_supplemental_solver_trials')!=len(pairs)*2
        or plan.get('n_attempts_per_pair')!=1 or plan.get('max_retries')!=0
        or plan.get('max_parallel_pairs')!=20 or plan.get('replace_original_missing_primary_rows') is not False):
        raise ValueError('supplement_controls_changed')
    if len({p['key'] for p in pairs})!=len(pairs):raise ValueError('duplicate_supplement_pair')
    for p in pairs:
        if {k:v for k,v in p.items() if k!='original_job_id'}!=five.selected_pair(p['task_id'],p['trial_index']):
            raise ValueError('original_pair_source_or_order_changed')
        if type(p.get('original_job_id')) is not int:raise ValueError('original_skipped_job_required')
    if set(plan['frozen_harness_sha256'])!=CONTROLS:raise ValueError('completion_pins_incomplete')
    for path,digest in plan['frozen_harness_sha256'].items():
        if five.candidate.digest(repo/path)!=digest:raise ValueError('completion_control_changed:'+path)
    if five.candidate.digest(HERE/'five-repeat-plan-v1.json')!=ORIGINAL_PLAN_SHA:raise ValueError('original_plan_changed')
    evidence=five.live.pilot.load(five.reference(repo,plan['final_selection_evidence']))
    if (evidence.get('pairs')!=pairs or evidence.get('original_run_id')!=ORIGINAL_RUN
        or evidence.get('original_head')!=ORIGINAL_HEAD or evidence.get('original_primary_missing_rows_preserved') is not True
        or evidence.get('quality_or_mapping_read') is not False):raise ValueError('final_technical_selection_not_bound')
    if plan['grading']['planned_answers']!=len(pairs)*2 or not five.candidate.is_sha(plan['grading']['assignment_sha256']):
        raise ValueError('supplement_anonymous_assignment_not_frozen')
    return plan

def api(endpoint):
    return json.loads(subprocess.run(['gh','api',endpoint],capture_output=True,text=True,check=True).stdout)

def admit(plan,root):
    run=api(f'repos/Cuiyus/zvec-grep/actions/runs/{ORIGINAL_RUN}')
    jobs=api(f'repos/Cuiyus/zvec-grep/actions/runs/{ORIGINAL_RUN}/jobs?per_page=100')
    if missing_pairs(run,jobs)!=plan['pairs']:raise ValueError('supplement_must_cover_exact_final_pre_agent_failure_set')
    if not parallel_capacity(jobs,len(plan['pairs'])):raise ValueError('waiting_for_original_pair_capacity')
    root.mkdir(parents=True,exist_ok=True)
    for name,data in [('original-admission-run.json',run),('original-admission-jobs.json',jobs)]:
        p=root/name
        if not p.exists():five.candidate.write(p,data)
    (root/'five-completion-plan-v2.json').write_bytes(PLAN.read_bytes())
    return plan

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('preview-all','run','collect'));parser.add_argument('--pair')
    for name in ('main-root','reference-prepared','prepared-group','candidate','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();completion=validate_completion(five.live.pilot.load(PLAN))
    if args.command!='preview-all' and args.pair not in {p['key'] for p in completion['pairs']}:parser.error('only_final_pre_agent_failures_allowed')
    admit(completion,args.output.parent)
    native_args=copy.copy(args);native_args.plan=HERE/'five-repeat-plan-v1.json'
    native_args.task_id=None;native_args.repetition=None
    plan,base,proof,manifest=five.validate_plan(native_args)
    selected=completion['pairs'] if args.command=='preview-all' else [p for p in completion['pairs'] if p['key']==args.pair]
    for pair in selected:
        local=copy.copy(native_args);local.task_id=pair['task_id'];local.repetition=pair['trial_index']
        local.command='dry-run' if args.command=='preview-all' else args.command
        local.output=args.output.with_name('dry-'+pair['key']) if args.command=='preview-all' else args.output
        sidecar=local.output.with_name(local.output.name+'-supplement-context.json')
        if args.command!='collect':
            five.candidate.write(sidecar,{'schema_version':1,'protocol_id':PROTOCOL,
                'supplement_plan_sha256':five.candidate.digest(PLAN),'original_run_id':ORIGINAL_RUN,
                'original_job_id':pair['original_job_id'],'pair':pair,'original_execution_step_skipped':True,
                'replace_original_missing_primary_rows':False,'original_missing_rows_preserved':True,
                'dry_run':args.command=='preview-all'})
        else:
            context=five.live.pilot.load(sidecar)
            if context['supplement_plan_sha256']!=five.candidate.digest(PLAN) or context['pair']!=pair or context['dry_run'] is not False:
                raise ValueError('supplement_context_changed')
        result=five.execute(local,plan,base,proof,manifest)
        if result:return result
    if args.command=='preview-all':
        print(json.dumps({'include':completion['pairs']}))
    return 0

if __name__=='__main__':raise SystemExit(main())
