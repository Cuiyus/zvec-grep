"""Read-only current-cohort execution, command and accounting fidelity audit.

Only original native collection and task-identity functions run on exclusive
copies of retained records. There are no Docker, provider or dispatch calls.
Source/tool semantics are independently reviewed by the scope auditor.
"""
import argparse, copy, datetime, hashlib, importlib.util, json, shutil, sys
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode=True
METHOD=Path(__file__).resolve().parent
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
B=METHOD.parent
H=W/'benchmarks/native-claude-qa-pilot'
M=B/'upstream-main/benchmarks/swe-qa-bench'
CI=B/'native-five-repeat-v1-ci/39392884840'
OUT=B/'native-five-repeat-v1-audit/execution'
GROUP=B/'native-five-repeat-v1-preparation/prepared'
REF=B/'native-claude-live-preflight-v1/prepared'
sys.path[:0]=[str(H),str(W/'benchmarks/opencode-qa-pilot'),str(M)]
import five_repeat_v1 as five
spec=importlib.util.spec_from_file_location('_five_artifact_bytes',METHOD/'build_blind_packets.py')
bytes_method=importlib.util.module_from_spec(spec);spec.loader.exec_module(bytes_method)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def write(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def prepare():
    args=SimpleNamespace(plan=H/'five-repeat-plan-v1.json',task_id=None,repetition=None,main_root=M,
        reference_prepared=REF,prepared_group=GROUP,candidate=CI/'preflight/raw/zg-install.tgz')
    plan,base,proof,manifest=five.validate_plan(args)
    return args,plan,base,proof,manifest
def expected_command(module,profile,selected,remote_selected,remote_live,logical_output,base):
    with module.live_environment(base,profile,M,require_credentials=False):
        runner=module.pilot.native(M)
        suite=runner.load_suite(selected/'suite.yaml')
        cache=SimpleNamespace(zvec_grep_package='/tmp/zg-bench-zvec-grep.tgz' if profile=='zvec-grep' else None,
            zvec_grep_package_sha256=module.PACKAGE_SHA if profile=='zvec-grep' else None)
        command=module.build_command(runner,suite,profile,selected,logical_output,cache,base['embedding']['endpoint'])
    mapping={str(selected):remote_selected,str(logical_output):remote_live,
             str(M):'/home/runner/work/_temp/qa-main/benchmarks/swe-qa-bench'}
    return [replace_paths(token,mapping) for token in command]
def replace_paths(token,mapping):
    for a,b in sorted(mapping.items(),key=lambda row:-len(row[0])):token=token.replace(a,b)
    return token
def normalized_collection(value):
    result=copy.deepcopy(value);result.pop('collection_wall_seconds',None)
    return result
def audit(pair,inputs):
    key=pair['key'];folder=CI/key;destination=OUT/key
    if not (folder/'artifact-byte-verification.json').is_file():return {'pair':key,'status':'awaiting_complete_original_retention'}
    if (destination/'report.json').is_file():return {'pair':key,'status':'already_audited','report_sha256':sha(destination/'report.json')}
    assert not destination.exists();destination.mkdir(parents=True)
    checks=[]
    def check(name,value,**detail):
        checks.append({'check':name,'passed':bool(value),**detail});return bool(value)
    args,plan,base,proof,manifest=inputs
    raw=bytes_method.verify_artifact(key)
    check('complete_original_zip_digest_and_exact_raw_inventory',True,receipt_sha256=sha(folder/'artifact-byte-verification.json'))
    jobs=load(folder/'job-metadata.json')['jobs'];job=next(j for j in jobs if j['name']=='repeat ('+key+')')
    context_path=raw/'live-five-context.json';profiles=[]
    if not context_path.is_file():
        steps=[s for s in job['steps'] if s['name']=='Execute exactly the scheduled original native pair']
        not_started=len(steps)==1 and steps[0]['conclusion']=='skipped' and not (raw/'live').exists()
        check('absence_supported_by_skipped_native_execution_step',not_started)
        for p in pair['profiles']:profiles.append({'profile':p,'execution_status':'not_started' if not_started else 'unknown',
            'execution_valid':None,'reported_metric_eligible':None,'metrics':None,'absence_reason':'original_pre_agent_setup_failed'})
        report={'schema_version':1,'pair':pair,'original_job_conclusion':job['conclusion'],'native_execution_started':False if not_started else None,
            'checks':checks,'audit_evidence_valid':all(c['passed'] for c in checks),'profiles':profiles,
            'scope':'No final or resource value imputed; original preparation failure remains in all scheduled counts.'}
    else:
        context=load(context_path);local_args=copy.copy(args)
        local_args.task_id=pair['task_id'];local_args.repetition=pair['trial_index']
        remote_live='/home/runner/work/_temp/native-five-v1-'+key+'/live'
        local_args.output=Path(remote_live)
        wanted=five.context_record(local_args,plan,pair,context['recorded_prepared_root'],dry_run=False)
        check('exact_frozen_pair_context',context==wanted)
        check('retained_original_plan_bytes',(raw/'five-repeat-plan-v1.json').read_bytes()==args.plan.read_bytes())
        check('retained_schedule_identity',load(raw/'scheduled-pair.json')==pair)
        record=load(raw/'live/execution.json')
        check('original_native_execution_header',record['protocol_id']==five.PROTOCOL and record['plan_sha256']==sha(args.plan)
            and record['candidate_sha256']==manifest['install_archive']['sha256'] and record['dry_run'] is False
            and record['offline_proof_sha256']==plan['offline_proof']['proof_sha256']
            and [s['profile'] for s in record['slots']]==pair['profiles'])
        check('pinned_harbor_python_and_architecture',record['system']['harbor_version']=='0.18.0'
            and record['system']['docker_architecture']=='x86_64' and record['system']['python'].startswith('3.12.'))
        selected=five.preparation.task_root(GROUP,REF,pair['task_id']);module=five.native(pair,manifest)
        commands={}
        for slot in record['slots']:
            p=slot['profile'];commands[p]=expected_command(module,p,selected,context['recorded_prepared_root'],remote_live,destination/'logical',base)
            check(p+':exact_native_command_with_shared_context',slot.get('command')==commands[p])
            check(p+':original_slot_and_execution_record_agree',load(raw/'live'/(p+'-slot.json'))==slot)
            check(p+':slot_finished_and_cleaned',slot.get('status')=='exited' and slot.get('returncode')==0 and module.cleanup_complete(slot))
        write(destination/'expected-commands.json',commands)
        replay=destination/'native-collection-replay'
        shutil.copytree(raw/'live',replay,ignore=shutil.ignore_patterns('collection'))
        local_args.output=replay;local_args.prepared=selected
        original=load(raw/'live/collection/collection.json')
        module.collect(local_args,base,proof)
        recomputed=load(replay/'collection/collection.json')
        check('complete_native_collector_replay_fidelity',normalized_collection(recomputed)==normalized_collection(original))
        for p in pair['profiles']:
            expected=original['profiles'][pair['profiles'].index(p)]
            if expected.get('answer_file'):
                name=expected['answer_file'];check(p+':exact_original_final_bytes',
                    (replay/'collection'/name).read_bytes()==(raw/'live/collection'/name).read_bytes()
                    and sha(replay/'collection'/name)==expected['answer_sha256'])
        identity=five.identity_module().verify(GROUP,pair['task_id'],replay,reference_prepared=REF,
            recorded_prepared_root=context['recorded_prepared_root'],expected_profiles=pair['profiles'],repo_root=W)
        write(destination/'identity-replay.json',identity)
        saved=load(raw/'live/task-identity.json')
        check('original_task_identity_recomputed',all(saved.get(k)==v for k,v in identity.items()))
        check('task_identity_valid',identity['task_identity_valid'])
        check('native_pair_structural_valid',recomputed['structural_valid'])
        check('same_original_native_tools_except_zg',recomputed['same_native_tools_except_zg'])
        roots=[]
        for row in recomputed['profiles']:
            p=row['profile'];agent=replay/row['trial_directory']/'agent' if row.get('trial_directory') else replay/'unavailable-agent'
            stream=module.root_stream(agent/'claude-code.txt')
            roots.append(stream['result'].get('session_id') if stream['result'] else None)
            check(p+':original_root_session_identity',row.get('init_identity_valid') and bool(roots[-1]))
            check(p+':native_and_exported_logs_exact',row.get('native_log_copy_matches'))
            check(p+':current_payloads_bound_before_and_after',row.get('pinned_runtime_valid'))
            check(p+':source_snapshot_and_mounts_valid',row.get('source_audit_valid') and row.get('container_mounts_valid'))
            check(p+':original_terminal_usage_reconciled',row['reported_metric_eligible'])
            usage=row['claude_reported_model_usage']['usage']
            tools=row.get('tool_calls');calls=len(tools) if isinstance(tools,list) else None
            profiles.append({'profile':p,'execution_status':row['slot_status'],'execution_valid':row['structural_valid'],
                'reported_metric_eligible':row['reported_metric_eligible'],'strict_usage_available':row['strict_usage_available'],
                'strict_accounting_errors':row.get('strict_accounting_errors'),'session_count':row.get('session_count'),
                'child_count':row.get('child_count'),'answer_sha256':row.get('answer_sha256'),
                'metrics':{'input_tokens':usage['input_tokens'] if usage else None,'output_tokens':usage['output_tokens'] if usage else None,
                    'tool_calls':calls,'agent_wall_seconds':row.get('harbor_timing',{}).get('agent_execution',{}).get('seconds'),
                    'outer_slot_seconds':row.get('outer_slot_wall_seconds')},
                'native_adapter_run_wall_seconds':row.get('native_run_wall_seconds'),
                'harbor_timing':row.get('harbor_timing'),'native_zg_setup_timing':row.get('native_zg_setup_timing'),
                'metric_consistency':row.get('metric_consistency')})
        check('two_distinct_original_roots',len(roots)==len(set(roots))==2 and all(roots))
        volumes=[v for s in record['slots'] for v in s.get('fresh_volumes',[])]
        check('four_distinct_fresh_profile_volumes',len(volumes)==len(set(volumes))==4)
        report={'schema_version':1,'pair':pair,'original_job_conclusion':job['conclusion'],'native_execution_started':True,
            'checks':checks,'audit_evidence_valid':all(c['passed'] for c in checks),'profiles':profiles,
            'source_scope_semantic_audit':'separate_independent_review','collection_fidelity_valid':normalized_collection(recomputed)==normalized_collection(original)}
    report.update(run_id=bytes_method.RUN,head_sha=bytes_method.HEAD,plan_sha256=bytes_method.PLAN_SHA,
        reviewed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),method_sha256=sha(Path(__file__)),
        solver_calls=0,docker_invoked=False,private_assignment_read=False,quality_evaluated=False,
        limitation='Reported usage is not billing. Cleanup/source/runtime records are retained observations, not a fresh inspection of an expired runner.')
    write(destination/'report.json',report)
    return {'pair':key,'status':'audited','evidence_valid':report['audit_evidence_valid'],'failed_checks':[c['check'] for c in checks if not c['passed']],
        'report_sha256':sha(destination/'report.json')}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--pair');a=p.parse_args();OUT.mkdir(parents=True,exist_ok=True)
    selected=[row for row in five.schedule() if a.pair is None or row['key']==a.pair]
    if not selected:p.error('undeclared pair')
    inputs=prepare()
    for pair in selected:print(json.dumps(audit(pair,inputs)),flush=True)
