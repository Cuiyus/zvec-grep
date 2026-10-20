"""One pair in the fixed five-question, 100-execution native cohort."""
from __future__ import annotations
import argparse
import copy
import importlib.util
import json
from pathlib import Path, PurePosixPath
import random

import live
import five_inputs_v1 as preparation
import rust_candidate_v1 as candidate
import workspace_context_v1 as workspace
from native_claude.integration import validate_inputs

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
PREFIX='benchmarks/native-claude-qa-pilot/'
PROTOCOL='native-claude-fixed-five-repeat-v1'
WORKFLOW='.github/workflows/native-five-repeat-v1.yml'
SETUP='.github/actions/native-five-setup/action.yml'
STATISTICS=PREFIX+'five-statistics-plan-v1.json'
BASE_PLAN=PREFIX+'live-plan-v2.json'
BASE_SHA='b4e8fb15b6cae82a9083add53ad7c04e67673720f7a50825fde587b7538ad026'
IDENTITY=PREFIX+'repeat_identity_v1.py'
IDENTITY_SHA='3235e1e6fe788dcc68f17415351c62434f5b8269b33a08026c32931bf8fe2539'
SEED=20261020
FIXED={'schema_version':1,'protocol_id':PROTOCOL,'frozen':True,'task_ids':list(preparation.TASK_IDS),
    'repetitions_per_task_profile':10,'scheduled_pairs':50,'scheduled_solver_trials':100,
    'n_attempts_per_pair':1,'max_retries':0,'max_parallel_pairs':20,'schedule_seed':SEED,
    'execution_workflow':WORKFLOW,'statistics_plan':STATISTICS,'ci_scope':'Cuiyus/zvec-grep'}
EXTRA={'created_at_utc','schedule','prepared','candidate_manifest','offline_proof','grading',
    'frozen_harness_sha256','historical_evidence','stopping','authorization','selection_disclosure'}
REQUIRED_CONTROLS={WORKFLOW,SETUP,STATISTICS,BASE_PLAN,IDENTITY}|{PREFIX+n for n in (
    'five_repeat_v1.py','five_inputs_v1.py','rust_candidate_v1.py','workspace_context_v1.py',
    'test_five_repeat_v1.py','quality_scores_v1.py','test_quality_scores_v1.py','five-quality-contract-v1.json',
    'repeat-scope-policy-v1.json','grader-contract-v1.json',
    'expansion-grader-contract-v1.json','architecture-grader-contract-v1.json','runtime-grader-contract-v1.json')}

def schedule():
    rng=random.Random(SEED);rows=[]
    for index in range(1,11):
        tasks=list(preparation.TASK_IDS);rng.shuffle(tasks)
        for task in tasks:
            position=preparation.TASK_IDS.index(task)
            rows.append({'key':f'{task}-r{index:02d}','task_id':task,'trial_index':index,
                'profiles':['baseline','zvec-grep'] if (position+index)%2 else ['zvec-grep','baseline']})
    return rows

def selected_pair(task,index):
    if type(index) is not int:raise ValueError('integer_repeat_required')
    rows=[r for r in schedule() if r['task_id']==task and r['trial_index']==index]
    if len(rows)!=1:raise ValueError('undeclared_task_or_repetition')
    return rows[0]

def reference(repo,record):
    path=live.pilot.safe_file(repo,record['path'])
    if candidate.digest(path)!=record['sha256']:raise ValueError('frozen_reference_changed:'+record['path'])
    return path

def proof_inputs(plan,repo=REPO):
    record=plan['offline_proof'];directory=repo/record['directory']
    proof=live.pilot.load(reference(repo,{'path':record['directory']+'/proof.json','sha256':record['proof_sha256']}))
    binding=live.pilot.load(reference(repo,{'path':record['directory']+'/current-candidate-binding.json','sha256':record['binding_sha256']}))
    if (proof.get('valid') is not True or proof.get('quality_evaluated') is not False or proof.get('errors')
        or len(proof.get('verification',[]))!=2 or any(not r['valid'] or any(not c['passed'] for c in r['checks']) for r in proof['verification'])
        or set(proof.get('pair_checks',{}))!={'same_actual_claude_binary','same_native_init_tools_except_zg'}
        or not all(proof['pair_checks'].values()) or binding.get('valid') is not True
        or binding.get('manifest_sha256')!=plan['candidate_manifest']['sha256']
        or binding.get('workspace_context_sha256')!=workspace.CONTEXT_SHA256
        or binding.get('original_native_proof_sha256')!=record['proof_sha256']
        or len(binding.get('actual_provider_context_checks',[]))!=2
        or not all(r['passed'] and r['first_actual_root_system_context_occurrences']==1 for r in binding['actual_provider_context_checks'])):
        raise ValueError('new_current_native_offline_proof_not_valid')
    expected=[r for r in proof['evidence_files'] if r['path']!='proof.json']
    actual=[r for r in live.pilot.file_inventory(directory) if r['path'] not in ('proof.json','current-candidate-binding.json')]
    if actual!=expected:raise ValueError('offline_native_raw_bytes_changed')
    return proof

def validate_plan(args,repo=REPO):
    plan=live.pilot.load(args.plan)
    if (not isinstance(plan,dict) or set(plan)!=set(FIXED)|EXTRA
        or any(type(plan.get(k)) is not type(v) or plan[k]!=v for k,v in FIXED.items())
        or plan['schedule']!=schedule()):raise ValueError('fixed_five_cohort_changed')
    if args.task_id is not None:selected_pair(args.task_id,args.repetition)
    controls=plan['frozen_harness_sha256']
    if not REQUIRED_CONTROLS<=set(controls):raise ValueError('required_controls_missing')
    for path,digest in controls.items():reference(repo,{'path':path,'sha256':digest})
    if candidate.digest(repo/BASE_PLAN)!=BASE_SHA:raise ValueError('original_runtime_parameters_changed')
    validate_inputs(repo,args.main_root)
    if plan['prepared']!=preparation.references(args.prepared_group,args.reference_prepared,repo):
        raise ValueError('five_frozen_task_sources_or_questions_changed')
    manifest_path=reference(repo,plan['candidate_manifest'])
    manifest=candidate.read_candidate(manifest_path,plan['candidate_manifest']['sha256'])
    if (candidate.digest(args.candidate)!=manifest['install_archive']['sha256']
        or args.candidate.stat().st_size!=manifest['install_archive']['bytes']):raise ValueError('new_candidate_package_changed')
    proof=proof_inputs(plan,repo)
    grading=plan['grading']
    if grading.get('planned_answers')!=100 or not candidate.is_sha(grading.get('assignment_sha256')):
        raise ValueError('hundred_anonymous_answers_not_preassigned')
    base=copy.deepcopy(live.pilot.load(repo/BASE_PLAN))
    base['candidate_sha256']=manifest['install_archive']['sha256']
    base['offline_proof']={'proof_sha256':plan['offline_proof']['proof_sha256'],
        'inputs_sha256':candidate.digest(HERE/'inputs.json')}
    return plan,base,proof,manifest

def native(pair,manifest):
    module=candidate.native_module(manifest,'live',protocol=PROTOCOL,profiles=('baseline','zvec-grep'))
    module.PROFILES=list(pair['profiles'])
    return module

def context_path(output):return output.with_name(output.name+'-five-context.json')

def context_record(args,plan,pair,prepared_root,*,dry_run):
    p=PurePosixPath(prepared_root)
    if not p.is_absolute() or '..' in p.parts or str(p)!=prepared_root:raise ValueError('recorded_prepared_root_required')
    return {'schema_version':1,'protocol_id':PROTOCOL,**pair,'plan_sha256':candidate.digest(args.plan),
        'prepared':plan['prepared'],'candidate_manifest_sha256':plan['candidate_manifest']['sha256'],
        'workspace_context_sha256':workspace.CONTEXT_SHA256,'recorded_prepared_root':prepared_root,'dry_run':dry_run}

def identity_module():
    path=HERE/'repeat_identity_v1.py'
    if candidate.digest(path)!=IDENTITY_SHA:raise ValueError('original_identity_body_changed')
    spec=importlib.util.spec_from_file_location('_fixed_five_identity',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.preparation=preparation
    return module

def execute(args,plan,base,proof,manifest):
    pair=selected_pair(args.task_id,args.repetition);module=native(pair,manifest)
    # validate_plan already verified every source byte and native task identity
    # in this command. Select its root without hashing all five exports again.
    prepared=preparation.task_root(args.prepared_group,args.reference_prepared,args.task_id)
    native_args=copy.copy(args);native_args.prepared=prepared
    sidecar=context_path(args.output)
    if args.command=='collect':
        saved=live.pilot.load(sidecar)
        if saved!=context_record(args,plan,pair,saved.get('recorded_prepared_root',''),dry_run=False):
            raise ValueError('five_pair_context_changed')
        collected=None;error=None
        try:collected=module.collect(native_args,base,proof)
        except (OSError,ValueError,KeyError,TypeError) as exc:error=type(exc).__name__
        result=identity_module().verify(args.prepared_group,args.task_id,args.output,
            reference_prepared=args.reference_prepared,recorded_prepared_root=saved['recorded_prepared_root'],expected_profiles=pair['profiles'])
        result.update(native_collection_completed=collected is not None,native_collection_error_type=error,
            native_structural_valid=bool(collected and collected['structural_valid']),pair_key=pair['key'])
        result['joint_gate_valid']=result['native_structural_valid'] and result['task_identity_valid']
        candidate.write(args.output/'task-identity.json',result)
        return 0 if result['joint_gate_valid'] else 1
    if args.output.exists() or sidecar.exists():raise FileExistsError('fresh_pair_output_required')
    sidecar.parent.mkdir(parents=True,exist_ok=True)
    candidate.write(sidecar,context_record(args,plan,pair,prepared.resolve().as_posix(),dry_run=args.command=='dry-run'))
    result=module.run_pair(native_args,base,dry_run=args.command=='dry-run')
    return 0 if args.command=='dry-run' or all(s.get('status')=='exited' and s.get('returncode')==0 and module.cleanup_complete(s) for s in result['slots']) else 1

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','main-root','reference-prepared','prepared-group','candidate','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--task',dest='task_id',choices=preparation.TASK_IDS);p.add_argument('--repetition',type=int)
    p.add_argument('command',choices=('dry-run','run','collect','schedule'));args=p.parse_args(argv)
    for key in ('plan','main_root','reference_prepared','prepared_group','candidate','output'):setattr(args,key,getattr(args,key).resolve())
    plan,base,proof,manifest=validate_plan(args)
    if args.command=='schedule':print(json.dumps({'include':schedule()},separators=(',',':')));return 0
    if args.task_id is None or args.repetition is None:p.error('--task and --repetition required')
    return execute(args,plan,base,proof,manifest)

if __name__=='__main__':raise SystemExit(main())
