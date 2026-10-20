"""Separate completion view; never modify the original planned100 view.

Only the proven zero-start q004-r09 zg position can be completed. Use its first
actual native execution and first postrun main judgement regardless of score.
"""
from pathlib import Path
import argparse
import copy
import json
import shutil
import sys

ROOT=Path(__file__).resolve().parent
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
sys.path.insert(0,str(W/'benchmarks/zvec-qa-glm-local'))
import cohort


def build(args):
    if args.output.exists():raise FileExistsError('never_overwrite_a_derived_completion_view')
    quality_run=cohort.load(args.quality_root/'run-metadata.json')
    recovery_run=cohort.load(args.recovery_root/'run-metadata.json')
    assert quality_run['id']==39424282413 and quality_run['head_sha']=='f2952116d1055e8270e967aca9c7026cb87c62c9'
    assert recovery_run['id']==39422897058 and recovery_run['head_sha']=='3390a27bbb2234abb5590754ff04f65bbfed4541'
    for run in (quality_run,recovery_run):
        assert run['status']=='completed' and run['run_attempt']==1 and run['event']=='workflow_dispatch'
        assert run['repository']['full_name']=='Cuiyus/zvec-grep'
    for root in (args.quality_root,args.recovery_root):
        receipt=cohort.load(root/'artifact-byte-verification.json')
        assert receipt['complete'] and receipt['zip_sha256']==cohort.sha(root/'artifact.zip')
    primary=args.quality_root/'raw/quality/pairs'
    recovery=args.recovery_root/'raw/execution/native'
    admission=cohort.load(args.recovery_root/'raw/execution/admission-proof.json')
    native=cohort.load(recovery/'collection.json');recovered=native['profiles'][0]
    grade=cohort.load(recovery/'judgements.json');recovered_grade=grade['profiles'][0]
    assert len(native['profiles'])==len(grade['profiles'])==1
    assert native['pair']['key']=='q004-r09' and recovered['profile']=='zvec-grep'
    assert recovered['execution_valid'] and recovered['answer'] is not None
    assert admission['prior_agent_execution_started'] is False and admission['successful_original_profiles_repeated']==0
    assert grade['collection_sha256']==cohort.sha(recovery/'collection.json')
    assert recovered_grade['answer_sha256']==recovered['answer_sha256']
    original_path=primary/'q004-r09/collection.json'
    original=cohort.load(original_path);original_grade=cohort.load(original_path.parent/'judgements.json')
    failed=next(r for r in original['profiles'] if r['profile']=='zvec-grep')
    assert failed['execution_valid'] is False and failed['answer'] is None
    assert admission['original_collection_sha256']==cohort.sha(original_path)
    assert grade['reference']==original_grade['reference']
    original_execution=cohort.load(ROOT/'live-ci/39414480210/q004-r09/raw/output/execution.json')
    failed_slot=next(r for r in original_execution['slots'] if r['profile']=='zvec-grep')
    baseline_slot=next(r for r in original_execution['slots'] if r['profile']=='baseline')
    recovered_slot=cohort.load(recovery/'execution.json')['slots'][0]
    failure_seconds=failed_slot['finished_at_unix']-failed_slot['started_at_unix']
    assert failure_seconds==admission['original_failure_outer_seconds']
    assert baseline_slot['started_at_unix']<recovered_slot['started_at_unix']
    raw_recovery_outer=recovered['metrics']['outer_slot_seconds']
    args.output.mkdir(parents=True)
    originals=[]
    for pair in cohort.schedule():
        source=primary/pair['key'];target=args.output/pair['key'];target.mkdir()
        assert cohort.load(source/'collection.json')['pair']==pair
        for name in ('collection.json','judgements.json'):
            shutil.copyfile(source/name,target/name)
        originals.append({'pair_key':pair['key'],'original_collection_sha256':cohort.sha(source/'collection.json'),
                          'original_uniform_judgements_sha256':cohort.sha(source/'judgements.json')})
    target=args.output/'q004-r09'
    completed=copy.deepcopy(original)
    replacement=copy.deepcopy(recovered)
    replacement['metrics']['outer_slot_seconds']=raw_recovery_outer+failure_seconds
    completed['profiles']=[replacement if r['profile']=='zvec-grep' else r for r in completed['profiles']]
    completed['all_valid']=all(r['execution_valid'] for r in completed['profiles'])
    completed['derived_view']='posthoc_infrastructure_completion_not_original_raw'
    completed['completion_provenance']={
        'original_native_run_id':39414480210,'first_execution_completion_run_id':39422897058,
        'original_collection_sha256':cohort.sha(original_path),
        'completion_native_collection_sha256':cohort.sha(recovery/'collection.json'),
        'original_failed_profile_preserved':failed,'actual_agent_repetitions_added_beyond_requested100':0,
        'original_failure_outer_seconds':failure_seconds,'raw_recovery_outer_seconds':raw_recovery_outer,
        'completed_outer_formula':'original failed environment outer + first actual recovery outer',
        'calendar_elapsed_from_initial_attempt_to_recovery_finish_seconds':recovered_slot['finished_at_unix']-failed_slot['started_at_unix'],
        'between_attempt_wait_seconds_excluded_from_cumulative_execution_metric':recovered_slot['started_at_unix']-failed_slot['finished_at_unix'],
        'same_host_as_original_baseline':False,'actual_agent_first_profile':'baseline',
        'scheduled_first_profile':original['pair']['profiles'][0]}
    cohort.write(target/'collection.json',completed)
    combined=copy.deepcopy(original_grade)
    combined['collection_sha256']=cohort.sha(target/'collection.json')
    combined['derived_view']='posthoc_infrastructure_completion_not_original_judge_record'
    combined['original_primary_judgements_sha256']=cohort.sha(original_path.parent/'judgements.json')
    combined['completion_singleton_judgements_sha256']=cohort.sha(recovery/'judgements.json')
    combined['method']='Unchanged main GLM postrun scores:99 original answers plus1 first-execution completion answer; no score selection'
    combined['profiles']=[copy.deepcopy(recovered_grade) if r['profile']=='zvec-grep' else r for r in combined['profiles']]
    cohort.write(target/'judgements.json',combined)
    manifest={'schema_version':1,'view':'posthoc_infrastructure_completion',
        'original_primary_unchanged':True,'original_native_run_id':39414480210,
        'completion_native_run_id':39422897058,'uniform_original_quality_run_id':39424282413,
        'actual_native_agent_executions':100,'extra_original_setup_failure_records_preserved':1,
        'first_attempt_original_native_completion':{'valid':99,'planned':100},
        'replacement_positions':[{'pair_key':'q004-r09','profile':'zvec-grep','original_agent_never_started':True,
            'original_failed_profile_retained':True,'source_answer_sha256':recovered['answer_sha256'],
            'source_judgement_sha256':cohort.sha(recovery/'judgements.json'),
            'original_failure_outer_seconds':failure_seconds,'raw_recovery_outer_seconds':raw_recovery_outer,
            'completed_outer_seconds':replacement['metrics']['outer_slot_seconds'],'new_host_and_later_wave':True}],
        'metric_scope':'All native token/agent metrics are unchanged; completed outer is cumulative measured execution across the failed environment and successful first execution, excluding between-attempt waiting.',
        'pairing_limit':'49 original shared-host pairs plus1 logical pair spanning hosts/waves; planned and effective agent order differ for q004-r09.',
        'original_pair_bindings':originals}
    cohort.write(args.output/'completion-manifest.json',manifest)
    # The primary artifacts remain byte-for-byte unchanged after derivation.
    for pair in originals:
        source=primary/pair['pair_key']
        assert cohort.sha(source/'collection.json')==pair['original_collection_sha256']
        assert cohort.sha(source/'judgements.json')==pair['original_uniform_judgements_sha256']
    print(json.dumps({'actual_native_executions':100,'missing_position_completions':1,'original_primary_unchanged':True}))


def annotate(args):
    report_path=args.output/'report.json';input_path=args.output/'normalized-input.json'
    report=cohort.load(report_path);payload=cohort.load(input_path)
    manifest=cohort.load(args.completion_inputs/'completion-manifest.json')
    payload.update(analysis_view='posthoc_infrastructure_completion',original_failures_removed_from_this_derived_view=1,
        original_failure_records_retained_in_unchanged_primary=True,completion_manifest_sha256=cohort.sha(args.completion_inputs/'completion-manifest.json'))
    # Correct the generic primary-only description for this explicitly derived view.
    payload['original_failures_removed']=True
    payload['time_scope']+=' Completion q004-r09 zg outer is failed-setup elapsed plus recovery execution elapsed; between-attempt waiting and CI queue are excluded and separately retained.'
    cohort.write(input_path,payload)
    report['normalized_input_sha256']=cohort.sha(input_path)
    report['analysis_view']='posthoc_infrastructure_completion'
    report['original_primary_unchanged']=True
    report['completion_provenance']=manifest
    report['time_scope']=payload['time_scope']
    report['quality_evaluation_wave']={'method':'same-main postrun quality on all100 first actual agent executions',
        'original99_quality_run_id':39424282413,'first_execution_singleton_quality_run_id':39422897058,
        'initial_competing_workload_judgements_used':False,'best_score_selection':False,
        'native_executions_beyond_requested100':0}
    report['statistics']['bootstrap']['unit']='whole planned logical pair within task; one pair spans hosts and waves'
    report['completion_actual_order_diagnostic']={}
    items=report['filter_variability_diagnostics']['per_task_selection_and_order']['q004']['profiles']
    for profile in cohort.PROFILES:
        item=items[profile];indexes=item['original_score_filter'].get('retained_trial_indexes')
        adjustment=1 if profile=='baseline' else -1
        report['completion_actual_order_diagnostic'][profile]={
            'scheduled_raw_first_count':item['raw_first_profile_count'],
            'actual_agent_first_count':item['raw_first_profile_count']+adjustment,
            'scheduled_filtered_first_count':item['filtered_first_profile_count'],
            'actual_filtered_first_count':None if indexes is None else item['filtered_first_profile_count']+(adjustment if 9 in indexes else 0)}
    report['filter_variability_diagnostics']['order_count_scope']='Existing first/second counts describe the frozen scheduled order; see completion_actual_order_diagnostic for q004 agent order after zero-start completion.'
    cohort.write(report_path,report)
    html_path=args.output/'report.html'
    if html_path.exists():
        body=html_path.read_text();body=body.replace('<h1>','<p><strong>独立基础设施补全视图：原始计划的1次启动前DNS失败保持不变。本视图纳入该位置的首次实际agent执行；累计冷启动耗时加回33.73秒失败准备时间，排除批次间等待。该逻辑测试对跨主机、跨批次，区间按计划索引重采样。</strong></p><h1>',1)
        html_path.write_text(body)
    print(json.dumps({'completion_view_annotated':True,'original_failed_plan_preserved_separately':True}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['build','annotate'])
    p.add_argument('--quality-root',type=Path);p.add_argument('--recovery-root',type=Path)
    p.add_argument('--completion-inputs',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();build(a) if a.command=='build' else annotate(a)
