#!/usr/bin/env python3
"""Complete-worker-only raw accounting observation; never infer joint eligibility."""
from pathlib import Path
import argparse,datetime,importlib.util,json,sys
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_prepared_q005_audit',ROOT/'q005_execution_audit_v1.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
assert m.sha(ROOT/'q005_execution_audit_v1.py')=='1330b52942923f5973864967defaab8a1d7287aeb7cfbbae3d01f2f4c2840d83'
import accounting_v2

def main(profile):
    c=m.CI/profile
    names=('artifact.zip','artifact-byte-verification.json','artifact-metadata.json','run-metadata.json','job-metadata.json')
    if not all((c/n).is_file() and not (c/n).is_symlink() for n in names):
        print(json.dumps({'status':'waiting','profile':profile,'raw_read':False}));return 3
    receipt=m.load(c/'artifact-byte-verification.json')
    if receipt.get('complete') is not True or receipt.get('exact_extracted_inventory') is not True:
        print(json.dumps({'status':'waiting','profile':profile,'raw_read':False}));return 3
    out=ROOT/'workers'/profile;destination=out/'execution-observation.json'
    if destination.exists():raise FileExistsError('worker_observation_exists')
    out.mkdir(parents=True,exist_ok=True)
    a=m.Audit();a.c=c;a.raw=c/'raw';a.live=a.raw/'live';a.artifact_key=profile
    metrics=None;timing=None;setup_measurements=None;tool_summary=None
    try:
        a.verify_one_origin()
        if not all(x['passed'] for x in a.checks):raise ValueError('origin_invalid')
        record=m.load(a.live/'execution.json');slots=record['slots'];slot=slots[0]
        trials=list((a.live/'runs'/('q001-'+profile)).glob('*/result.json'))
        a.check('single_original_worker_slot_and_trial',len(slots)==1 and slot['profile']==profile and len(trials)==1 and record['protocol_id']==m.PROTOCOL and record['plan_sha256']==m.PLAN_SHA and record['dry_run'] is False,[a.live/'execution.json'],category='binding')
        assert len(slots)==len(trials)==1
        trial=trials[0].parent;agent=trial/'agent';result=m.load(trials[0]);audit=m.load(trial/'qa-source-audit.json')
        events=[row for _,row in m.old.rows(agent/'claude-code.txt')]
        inits=[x for x in events if x.get('type')=='system' and x.get('subtype')=='init'];terminals=[x for x in events if x.get('type')=='result']
        init=inits[0] if len(inits)==1 else {};terminal=terminals[0] if len(terminals)==1 else {}
        a.check('single_native_completed_root_and_original_cleanup',len(inits)==len(terminals)==1 and events[-1]==terminal and init.get('model')==m.old.MODEL and init.get('cwd')=='/workspace' and init.get('claude_code_version')=='2.1.212' and terminal.get('session_id')==init.get('session_id') and terminal.get('subtype')=='success' and terminal.get('is_error') is False and terminal.get('terminal_reason')=='completed' and terminal.get('stop_reason')=='end_turn' and not terminal.get('permission_denials') and slot['status']=='exited' and slot['returncode']==0 and m.runtime.live.cleanup_complete(slot) and result.get('exception_info') is None,[agent/'claude-code.txt',a.live/(profile+'-slot.json'),trials[0]],root_session_id=init.get('session_id'))
        account=accounting_v2.analyze(agent,expected_model=m.old.MODEL)
        independent=m.old.independent_messages(agent,account)
        a.check('complete_recursive_terminal_usage_tool_identity_and_inventory',account['complete'] is True and account['complete_tree'] is True and not account['errors'] and account['usage']['unknown_usage_messages']==0 and independent['message_match'] and independent['tool_match'] and independent['sessions_match'] and not independent['usage_update_issues'] and not independent['tool_conflicts'] and independent['all_terminal_fields_available'] and m.runtime.live.native_log_bytes_match(agent),[agent/'claude-code.txt',*independent['persisted_files']],category='metric',accounting_errors=account['errors'],usage_update_issues=independent['usage_update_issues'],tool_conflicts=independent['tool_conflicts'])
        reported=m.runtime.live.reported_model_usage(terminal);consistency=m.runtime.live.metric_consistency(reported,account)
        a.check('raw_terminal_sum_equals_native_reported_model_usage',reported['available'] is True and independent['usage'] is not None and reported['usage']==independent['usage'] and all(account['usage'][k]==v for k,v in independent['usage'].items()) and not consistency['contradictions'],[agent/'claude-code.txt',*independent['persisted_files']],category='metric',reported=reported,consistency=consistency)
        atif=m.load(agent/'trajectory.json');finals=[s for s in atif['steps'] if s.get('source')=='agent' and not s.get('tool_calls')]
        a.check('worker_terminal_and_atif_final_byte_identity_without_quality_read',account['final']['text']==terminal['result'] and atif['session_id']==init['session_id'] and bool(finals) and finals[-1]['message'].strip()==terminal['result'].strip(),[agent/'trajectory.json',agent/'claude-code.txt'],answer_sha256=m.hashlib.sha256(terminal['result'].encode()).hexdigest(),quality_evaluated=False)
        metrics={'primary_usage':reported['usage'],'independent_terminal_usage':independent['usage'],'strict_usage':account['usage'],'root_session_id':account['root_session_id'],'session_count':account['session_count'],'child_count':account['child_count'],'message_count':len(independent['messages']),'terminal_messages':independent['messages'],'session_identities':account['sessions'],'tools':account['tools'],'consistency':consistency}
        tool_summary={'count':len(account['tools']),'errors':sum(t['is_error'] for t in account['tools']),'name_counts':{n:sum(t['name']==n for t in account['tools']) for n in sorted({t['name'] for t in account['tools']})}}
        timing={}
        for phase in ('environment_setup','agent_setup','agent_execution','verifier','trial'):
            item=result if phase=='trial' else result[phase]
            start,end=[datetime.datetime.fromisoformat(item[k].replace('Z','+00:00')) for k in ('started_at','finished_at')]
            timing[phase]=(end-start).total_seconds()
        timing.update(outer_slot=slot['finished_at_unix']-slot['started_at_unix'],native_run=audit['native_run_wall_seconds'],source_audit=audit['audit_wall_seconds'],log_export=audit['export_wall_seconds'])
        a.check('all_worker_timing_scopes_nonnegative_finite',all(m.old.number(v) for v in timing.values()),[trials[0],trial/'qa-source-audit.json',a.live/'execution.json'],category='metric',timing_seconds=timing,pair_time_invented=False)
        if profile=='zvec-grep':
            setup=m.load(agent/'zvec-grep-setup.json');stages={k:setup[k] for k in ('install_duration_seconds','authorization_duration_seconds','index_duration_seconds','mcp_setup_duration_seconds')};elapsed=(datetime.datetime.fromisoformat(setup['finished_at'])-datetime.datetime.fromisoformat(setup['started_at'])).total_seconds()
            okay=setup['status']=='ready' and setup['workdir']=='/workspace' and setup['authorization_scope']=='workspace' and all(m.old.number(v) and v>0 for v in stages.values()) and sum(stages.values())<=setup['total_duration_seconds'] and abs(elapsed-setup['total_duration_seconds'])<.002 and setup['package_sha256']==m.PACKAGE_SHA and setup['embedding_model']=='qwen/qwen3.7-text-embedding' and setup['mcp_target']=='claude-code' and not setup['index_cache_hit'] and not setup['index_seed_available'] and not setup['install_reused_cache']
            a.check('four_cold_setup_phases_and_original_fresh_state',okay,[agent/'zvec-grep-setup.json'],category='metric',four_stages_seconds=stages,total_seconds=setup['total_duration_seconds'])
            setup_measurements={'four_stages_seconds':stages,'four_stage_sum_seconds':sum(stages.values()),'total_seconds':setup['total_duration_seconds'],'embedding_request_tokens':None,'embedding_cost':None}
    except Exception as exc:a.check('required_worker_evidence_read_or_schema',False,error_type=type(exc).__name__,error=str(exc))
    report={'schema_version':1,'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Single complete worker execution/accounting observation only. Joint and other-profile checks remain pending.','run_id':m.RUN,'head_sha':m.HEAD,'plan_sha256':m.PLAN_SHA,'profile':profile,'origin':getattr(a,'origin',None),'passed':all(c['passed'] for c in a.checks),'checks':a.checks,'check_count':len(a.checks),'failed_checks':[c['check'] for c in a.checks if not c['passed']],'metrics':metrics,'tool_summary':tool_summary,'timing_seconds':timing,'cold_setup':setup_measurements,'evidence_catalog':a.catalog,'joint_validity':None,'quality_evaluated':False,'source_scope_evaluated':False,'benefit_evaluated':False,'method_sha256':m.sha(Path(__file__)),'limitations':['No joint completion or comparison inferred from one worker.','Final-answer identity is compared internally; no answer text or semantic grading is output.','ModelUsage and persisted terminal usage are not provider billing; embedding request/tokens/cost remain unknown.']}
    m.write(destination,report);print(json.dumps({'path':str(destination),'sha256':m.sha(destination),'passed':report['passed'],'checks':len(a.checks),'failed_checks':report['failed_checks']},indent=2));return 0 if report['passed'] else 1
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--profile',choices=m.PROFILES,required=True);args=p.parse_args();raise SystemExit(main(args.profile))
