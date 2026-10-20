#!/usr/bin/env python3
"""Exclusive q004 execution/accounting audit; frozen checks retained, no quality/scope judgment."""
from pathlib import Path, PurePosixPath
import datetime, hashlib, importlib.util, inspect, json, math, subprocess, sys, tarfile, textwrap
sys.dont_write_bytecode = True
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep'); H=W/'benchmarks/native-claude-qa-pilot'
M=B/'upstream-main/benchmarks/swe-qa-bench'; REFERENCE=B/'native-claude-live-preflight-v1/prepared'
GROUP=B/'native-architecture-q004-preparation-v1/prepared'; OUT=Path(__file__).resolve().parent
CI=B/'native-q004-live-v1-ci/39375703707'; HEAD='19c532cfeddcdf4c9374539acf4c2be3b9eebfee'; RUN=39375703707
PLAN_SHA='803750ff8050adf770325345b907ef662c578d6dff6e0fd480f92f78d3a1148d'
PROTOCOL='native-claude-q004-development-v1'; BUILD='a424e0ce06868dc9ad8f142f57de59b8f9b58699'
CURRENT_MAIN='49de68563a7d3da9b408dd64277a8a51e3dd0fc6'; RUST_TREE='de02030056a3224012725799d845bf2be9edae2b'
GROUP_SHA='62fec44c8858acc6dc65459abe552015b77e3e02993a3455bc61e84079f5f8de'
META_SHA='21a9c131760f615f7993de5d16b3533e8277c9a77f205fdfb2b456046cac21de'
MANIFEST_SHA='eeccdb40c6b045c4961538b9a78f5e5b17bfa16c3124f1774bbd5f9cf708860d'
PACKAGE_SHA='fbe4bd201c8a0520f2373f1039e1d0805a38781c6e2f3ce11b83310d23dc6522'
METHOD=H/'results/20261020-expansion-v1/method/review-execution.py'
METHOD_SHA='5659223ccebe9c6e5d421c3d400ff98b7a1dfd2743090cc5377bdfd4734a8085'
PRIOR=B/'native-zvec-q001-repeat-v1-audit/q001_execution_audit_v1.py'
PRIOR_SHA='cb539aeeeb582afe8ceb438df6572c33c1ec8b2550a831bdc9e75b9605ca6348'
PRIOR_COMMAND=B/'native-zvec-q001-repeat-v1-audit/command_replay_v1.py'
PRIOR_COMMAND_SHA='66d9a8afe15fc17a999744e29c70e8668bd562a4d322d135581ad36a18318e86'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p): return json.loads(p.read_text())
def write(p,x):
    with p.open('x') as f: json.dump(x,f,indent=2,sort_keys=True,ensure_ascii=False); f.write('\n')
assert sha(METHOD)==METHOD_SHA and sha(PRIOR)==PRIOR_SHA and sha(PRIOR_COMMAND)==PRIOR_COMMAND_SHA
spec=importlib.util.spec_from_file_location('_frozen_expansion_execution_audit',METHOD)
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
old.RUN,old.HEAD,old.PLAN_SHA,old.PROTOCOL=RUN,HEAD,PLAN_SHA,PROTOCOL
old.ORDERS={'q004':['baseline','zvec-grep']}
origin_source=textwrap.dedent(inspect.getsource(old.Audit.verify_origin))
origin_changes=[("j.get('name') == 'expansion (' + self.task + ')'","j.get('name') == 'pilot'"),
("f'native-claude-expansion-v1-{RUN}-{self.task}'","f'native-q004-v1-{RUN}-pilot'"),
("j.get('name') in ('expansion (q002)', 'expansion (q003)')","j.get('name') in ('preflight', 'pilot')"),
('across both final artifacts when both are available.','across the preflight and pilot jobs retained in this run metadata.')]
for before,after in origin_changes:
    assert before in origin_source,before
    origin_source=origin_source.replace(before,after)
exec(compile(origin_source,str(Path(__file__))+'[origin-adapter]','exec'),old.__dict__)
profile_source=textwrap.dedent(inspect.getsource(old.Audit.profile))
old_prefix="""    pcmd = list(next(x['command'] for x in self.pre['slots'] if x['profile'] == profile))
    pi = pcmd.index('--jobs-dir') + 1
    pcmd[pi] = str(PurePosixPath(pcmd[pi]).parent.parent / 'live' / 'runs')"""
assert old_prefix in profile_source
profile_source=profile_source.replace(old_prefix,"    pcmd = self.expected_commands[profile]")
profile_changes=[('actual_command_exact_preflight_and_native_task','actual_command_exact_frozen_builder_and_native_task'),
("r / 'preflight.json'","self.command_replay_path"),('GROUP / self.task','self.selected_root')]
for before,after in profile_changes:
    assert before in profile_source,before
    profile_source=profile_source.replace(before,after)
exec(compile(profile_source,str(Path(__file__))+'[preserved-profile-checks]','exec'),old.__dict__)

class Audit(old.Audit):
    verify_origin=old.__dict__['verify_origin']
    profile=old.__dict__['profile']
    def __init__(self):
        self.task='q004';self.c=CI/'pilot';self.raw=self.c/'raw';self.live=self.raw/'live';self.out=OUT
        self.checks,self.catalog,self.profiles,self.inits=[],{},[],{}
    def prepare(self):
        r=self.raw
        self.plan=load(r/'architecture-live-plan-v1.json');self.base=load(r/'live-plan-v2.json');inputs=load(r/'inputs.json')
        self.check('exact_frozen_q004_plan',sha(r/'architecture-live-plan-v1.json')==PLAN_SHA
            and (r/'architecture-live-plan-v1.json').read_bytes()==old.gitbytes('benchmarks/native-claude-qa-pilot/architecture-live-plan-v1.json')
            and self.plan['frozen'] is True and self.plan['protocol_id']==PROTOCOL,
            [r/'architecture-live-plan-v1.json'],category='binding')
        input_ok=sha(r/'inputs.json')==self.base['offline_proof']['inputs_sha256'] and (r/'inputs.json').read_bytes()==old.gitbytes('benchmarks/native-claude-qa-pilot/inputs.json') and inputs['revision']==self.base['offline_proof']['inputs_revision']
        base_ok=sha(r/'live-plan-v2.json')==self.plan['base_plan']['sha256'] and (r/'live-plan-v2.json').read_bytes()==old.gitbytes(self.plan['base_plan']['path'])
        self.check('exact_base_plan_and_original_native_inputs',input_ok and base_ok,[r/'inputs.json',r/'live-plan-v2.json'],category='binding')
        assert sha(r/'architecture-live-plan-v1.json')==PLAN_SHA and input_ok and base_ok
        allpins={**inputs['repository_files'],**self.base['frozen_harness_sha256'],**self.plan['frozen_harness_sha256']}
        bad=[p for p,d in allpins.items() if hashlib.sha256(old.gitbytes(p)).hexdigest()!=d]
        self.check('all_repository_pins_at_exact_execution_commit',not bad,[r/'architecture-live-plan-v1.json',r/'live-plan-v2.json',r/'inputs.json'],category='binding',checked_files=len(allpins),mismatches=bad,
            restricted_files='Grading readiness/control and prior summaries are hashed as opaque bytes only; no private assignment or quality result is parsed.')
        local_bad=[p for p,d in allpins.items() if p.endswith('.py') and (not (W/p).is_file() or sha(W/p)!=d)]
        self.check('local_readonly_replay_code_matches_execution_pins',not local_bad,[W/p for p in allpins if p.endswith('.py')],category='binding',mismatches=local_bad)
        assert not bad and not local_bad
        sys.path[:0]=[str(H),str(W/'benchmarks/opencode-qa-pilot'),str(M),str(OUT)]
        import accounting_v2, architecture_preparation_v1, architecture_identity_v1, architecture_live_v1, live
        from command_replay_v1 import expected_command
        self.accounting,self.collector=accounting_v2,live
        self.identity=architecture_identity_v1.implementation()
        # Base runtime validation is read-only and checks the frozen native harness,
        # accepted offline proof, retained bytes and candidate, without execution.
        base, self.proof=live.validate_plan(r/'live-plan-v2.json',M,REFERENCE,r/'zg-install.tgz',W)
        assert base==self.base
        self.check('accepted_original_native_runtime_proof_and_plan_revalidated',True,[r/'live-plan-v2.json',r/'inputs.json',W/self.base['offline_proof']['directory']/'proof.json',W/self.base['offline_proof']['review_path']],category='binding')
        preparation=architecture_preparation_v1
        validated=preparation.validate(GROUP,REFERENCE,W)
        self.selected_root=GROUP/'q004';self.metadata=load(self.selected_root/'prepared.json');self.manifest=load(self.selected_root/'source-manifest.json')
        self.source=self.selected_root/'tasks/zvec-q004/environment/source';self.canonical={x['path']:x for x in self.manifest['files']}
        self.question_path=W/'benchmarks/zvec-issue-qa'/self.metadata['question']['path'];self.question=self.question_path.read_text()
        source_files=sorted(p.relative_to(self.source).as_posix() for p in self.source.rglob('*') if p.is_file())
        ok=(sha(GROUP/'group.json')==sha(r/'q004-prepared-group.json')==self.plan['prepared_group']['group_sha256']==GROUP_SHA
            and sha(self.selected_root/'prepared.json')==sha(r/'q004-prepared.json')==self.plan['prepared_group']['metadata_sha256']['q004']==META_SHA
            and sha(self.selected_root/'source-manifest.json')==sha(r/'q004-source-manifest.json')==self.metadata['source_manifest_sha256']==MANIFEST_SHA
            and validated==load(r/'q004-prepared-group.json') and validated['task_ids']==['q004']
            and self.metadata['task_id']=='q004' and self.metadata['scheduled_task_ids']==['q004']
            and self.metadata['dataset']==self.plan['dataset'] and sha(W/self.plan['dataset']['path'])==self.plan['dataset']['sha256']
            and sha(self.question_path)==self.metadata['question']['sha256']
            and self.manifest['source_commit']==self.metadata['source_commit']
            and self.manifest['inventory_sha256']==self.metadata['source_inventory_sha256']
            and len(self.canonical)==len(self.manifest['files'])==self.metadata['source_file_count']==1596
            and sorted(self.canonical)==source_files and not any(p.is_symlink() for p in self.source.rglob('*'))
            and all((self.source/n).stat().st_size==x['bytes'] and sha(self.source/n)==x['sha256'] for n,x in self.canonical.items())
            and (r/'q001-reference-prepared.json').read_bytes()==(REFERENCE/'prepared.json').read_bytes())
        self.check('selected_q004_group_metadata_manifest_all_source_bytes_and_question',ok,[GROUP/'group.json',self.selected_root/'prepared.json',self.selected_root/'source-manifest.json',r/'q004-prepared-group.json',r/'q004-prepared.json',r/'q004-source-manifest.json',self.question_path,r/'q001-reference-prepared.json'],category='binding',source_commit=self.metadata['source_commit'],source_file_count=len(self.canonical),source_inventory_sha256=self.metadata['source_inventory_sha256'],source_root=str(self.source))
        assert ok
        self.ctx=load(r/'live-q004-context.json');self.execution=load(self.live/'execution.json');self.collection=load(self.live/'collection/collection.json')
        remote='/home/runner/work/_temp/qa-architecture/q004'
        expected={'schema_version':1,'protocol_id':PROTOCOL,'task_id':'q004','profiles':['baseline','zvec-grep'],'plan_sha256':PLAN_SHA,
            'base_plan_sha256':self.plan['base_plan']['sha256'],'dataset_sha256':self.plan['dataset']['sha256'],'prepared_group_sha256':GROUP_SHA,
            'prepared_metadata_sha256':META_SHA,'offline_proof_sha256':self.base['offline_proof']['proof_sha256'],'candidate_sha256':PACKAGE_SHA,'recorded_prepared_root':remote,'dry_run':False}
        self.check('exclusive_q004_context_and_execution_collection_provenance',self.ctx==expected
            and all(d['protocol_id']==PROTOCOL and d['plan_sha256']==PLAN_SHA and d['offline_proof_sha256']==self.base['offline_proof']['proof_sha256'] for d in [self.execution,self.collection])
            and self.execution['offline_inputs_sha256']==self.base['offline_proof']['inputs_sha256'] and self.execution['dry_run'] is False,
            [r/'live-q004-context.json',self.live/'execution.json',self.live/'collection/collection.json'],category='binding')
        self.check('one_q004_pair_baseline_then_zg_one_attempt_no_retry',self.plan['tasks']==[{'id':'q004','profiles':['baseline','zvec-grep']}]
            and self.plan['n_attempts']==1 and self.plan['max_retries']==0
            and [s['profile'] for s in self.execution['slots']]==['baseline','zvec-grep']
            and [s['profile'] for s in self.collection['profiles']]==['baseline','zvec-grep'],[r/'architecture-live-plan-v1.json',self.live/'execution.json',self.live/'collection/collection.json'])
        replay=architecture_identity_v1.verify(GROUP,'q004',self.live,reference_prepared=REFERENCE,recorded_prepared_root=remote,repo_root=W)
        replay_path=OUT/'task-identity-replay.json';write(replay_path,replay)
        expected_saved=dict(replay,native_collection_completed=True,native_collection_error_type=None,native_structural_valid=self.collection.get('structural_valid') is True,context_sha256=sha(r/'live-q004-context.json'))
        expected_saved['joint_gate_valid']=expected_saved['native_structural_valid'] and replay['task_identity_valid']
        self.check('actual_q004_task_identity_replay_and_joint_gate',replay['task_identity_valid'] is True and load(self.live/'task-identity.json')==expected_saved and expected_saved['joint_gate_valid'],[replay_path,self.live/'task-identity.json'],category='binding',errors=replay['errors'],replayed_checks=len(replay['checks']))
        self.expected_commands={p:expected_command(p,self.selected_root,remote,'/home/runner/work/_temp/native-claude-q004-v1-pilot/live',M,self.base,OUT/'logical-command-output') for p in ('baseline','zvec-grep')}
        self.command_replay_path=OUT/'frozen-command-replay.json';write(self.command_replay_path,{'method':'Frozen native build_command only; explicit local-to-recorded prepared/main/output prefix mappings. No setup, execution or credentials.','helper_sha256':sha(OUT/'command_replay_v1.py'),'commands':self.expected_commands})
        slots=self.execution['slots'];volumes=[v for s in slots for v in s.get('fresh_volumes',[])]
        self.check('serial_slots_and_four_fresh_distinct_volumes',len(slots)==2 and slots[0].get('finished_at_unix',math.inf)<=slots[1].get('started_at_unix',-math.inf) and len(volumes)==len(set(volumes))==4,[self.live/'execution.json'])
        system=self.execution['system'];self.check('pinned_native_system_versions',system['harbor_version']=='0.18.0' and system['docker_architecture']=='x86_64' and system['python'].startswith('3.12.'),[self.live/'execution.json'],system=system)
        self.pkg=load(r/'package-derivation.json');self.package_sha=sha(r/'zg-install.tgz');self.payloads={x['path'].removeprefix('package/'):{'bytes':x['derived_bytes'],'sha256':x['derived_sha256'],'mode':x['mode']} for x in self.pkg['runtime_payloads']}
        with tarfile.open(r/'zg-install.tgz') as z:
            payload_ok=all(hashlib.sha256(z.extractfile('package/'+k).read()).hexdigest()==v['sha256'] and z.getmember('package/'+k).size==v['bytes'] and format(z.getmember('package/'+k).mode,'04o')==v['mode'] for k,v in self.payloads.items())
        self.check('canonical_package_four_unchanged_runtime_payloads',payload_ok and len(self.payloads)==4
            and self.package_sha==PACKAGE_SHA==self.pkg['install_package_sha256']==self.base['candidate_sha256']==self.execution['candidate_sha256']
            and self.pkg['upstream_commit']==inputs['upstream_commit']==self.base['upstream_main']==BUILD
            and all(x['unchanged'] is True for x in self.pkg['runtime_payloads']),[r/'zg-install.tgz',r/'package-derivation.json',r/'inputs.json',self.live/'execution.json'])
        current=load(r/'current-upstream.json');tree=load(r/'current-upstream-tree.json')
        rust=[x for x in tree['tree'] if x['path']=='rust' and x['type']=='tree']
        build_tree=subprocess.check_output(['git','rev-parse',BUILD+':rust'],cwd=W,text=True,timeout=30).strip()
        self.check('recorded_current_main_rust_tree_equals_build_rust_tree',current['sha']==tree['sha']==CURRENT_MAIN and tree['truncated'] is False and len(rust)==1 and rust[0]['sha']==build_tree==RUST_TREE,[r/'current-upstream.json',r/'current-upstream-tree.json',r/'package-derivation.json'],category='binding',actual_main_observed=CURRENT_MAIN,rust_build_commit=BUILD,rust_tree=RUST_TREE,whole_harness_same_as_current_main='not_claimed; imported harness is pinned to the a424 build-era source')
        jobs=load(self.c/'job-metadata.json')['jobs'];pilot_job=next(j for j in jobs if j['name']=='pilot');preflight_job=next(j for j in jobs if j['name']=='preflight')
        steps=pilot_job['steps'];scans=[s for s in steps if s['name']=='Scan actual secrets before retention'];collects=[s for s in steps if s['name']=='Collect exact answers and task identity'];uploads=[s for s in steps if 'upload-artifact' in s['name']]
        self.check('preflight_and_execution_collection_secret_scan_upload_receipts',preflight_job['conclusion']=='success' and pilot_job['conclusion']=='success'
            and len(scans)==len(collects)==len(uploads)==1 and all(s['conclusion']=='success' for s in scans+collects+uploads)
            and collects[0]['number']<scans[0]['number']<uploads[0]['number'],[self.c/'job-metadata.json'],category='binding',secret_values_read=False)
    def additional_metrics(self):
        for detail in self.profiles:
            if not detail.get('complete_review'):continue
            profile=detail['profile'];trial=self.live/detail['trial_directory'];account=load(self.live/'collection'/f'{profile}-strict-accounting.json')
            totals={k:sum(m['usage'][k] for m in account['messages']) for k in old.FIELDS} if all(m['usage_valid'] for m in account['messages']) else None
            by_session=[]
            for session in account['sessions']:
                messages=[m for m in account['messages'] if m['session']==session['id']];tools=[t for t in account['tools'] if t['session']==session['id']]
                component={k:sum(m['usage'][k] for m in messages) for k in old.FIELDS} if messages and all(m['usage_valid'] for m in messages) else None
                by_session.append({'session_id':session['id'],'message_count':len(messages),'tool_count':len(tools),'terminal_usage_components':component})
            detail['per_session_terminal_usage']=by_session
            detail['complete_strict_usage_record']=account['usage']
            detail['tool_name_counts']={n:sum(t['name']==n for t in account['tools']) for n in sorted({t['name'] for t in account['tools']})}
            detail['strict_tool_errors']=sum(t['is_error'] for t in account['tools'])
            self.check(profile+':independent_terminal_usage_has_no_regression_or_conflict',totals is not None and account['complete'] is True and not account['errors'] and account['usage']['unknown_usage_messages']==0,[self.live/'collection'/f'{profile}-strict-accounting.json'],category='metric',components=totals,per_session=by_session)
            if profile=='zvec-grep':
                setup=load(trial/'agent/zvec-grep-setup.json');stages={k:setup[k] for k in ('install_duration_seconds','authorization_duration_seconds','index_duration_seconds','mcp_setup_duration_seconds')}
                sum4=sum(stages.values());start=datetime.datetime.fromisoformat(setup['started_at']);end=datetime.datetime.fromisoformat(setup['finished_at']);elapsed=(end-start).total_seconds()
                self.check('zvec-grep:four_separate_cold_setup_phases_and_total',all(old.number(v) and v>0 for v in stages.values()) and sum4<=setup['total_duration_seconds'] and abs(elapsed-setup['total_duration_seconds'])<0.002
                    and setup['authorization_scope']=='workspace' and setup['embedding_endpoint']==self.base['embedding']['endpoint']
                    and not setup['index_cache_hit'] and not setup['index_seed_available'] and not setup['install_reused_cache'],[trial/'agent/zvec-grep-setup.json'],category='metric',stages=stages,sum_four_stages_seconds=sum4,setup_total_seconds=setup['total_duration_seconds'],setup_unallocated_overhead_seconds=setup['total_duration_seconds']-sum4,setup_timestamp_seconds=elapsed,
                    embedding_request_token_count=None,embedding_billable_cost=None,limitation='Per-phase wall values are native setup measurements, not separate provider request logs. Missing embedding billing/usage remains unknown.')
                detail['cold_setup']={'four_stages_seconds':stages,'four_stage_sum_seconds':sum4,'total_seconds':setup['total_duration_seconds'],'unallocated_overhead_seconds':setup['total_duration_seconds']-sum4,'embedding_request_tokens':None,'embedding_cost':None,'cold_state':{'index_cache_hit':False,'index_seed_available':False,'install_reused_cache':False}}
            detail['timing_measurement_limits']='Native run, source audit, log export, Harbor intervals, four zg setup phases and outer slot are distinct measured scopes. No pure-model-time, efficiency or benefit judgment.'
    def run(self):
        destination=OUT/'execution-review.json'
        if destination.exists() or (OUT/'task-identity-replay.json').exists():raise FileExistsError('exclusive_audit_output_exists')
        self.verify_origin()
        try:
            self.prepare()
            for profile in ('baseline','zvec-grep'):
                try:self.profile(profile)
                except Exception as e:self.check(profile+':required_evidence_read_or_schema',False,error_type=type(e).__name__,error=str(e))
            roots=[p.get('root_session_id') for p in self.profiles];a,b=self.inits.get('baseline',{}),self.inits.get('zvec-grep',{})
            tools_ok=isinstance(a.get('tools'),list) and isinstance(b.get('tools'),list) and set(b['tools'])-set(a['tools'])=={'mcp__zvec_grep__zvec_grep_search'} and not set(a['tools'])-set(b['tools'])
            self.check('two_distinct_native_roots_and_same_toolsets_except_zg',len(roots)==2 and all(roots) and len(set(roots))==2 and tools_ok and self.collection.get('structural_valid') is True,[self.live/'execution.json',self.live/'collection/collection.json'])
            self.additional_metrics()
        except Exception as e:self.check('required_global_evidence_read_or_schema',False,error_type=type(e).__name__,error=str(e))
        self.check('all_frozen_review_method_sources_unchanged',sha(METHOD)==METHOD_SHA and sha(PRIOR)==PRIOR_SHA and sha(PRIOR_COMMAND)==PRIOR_COMMAND_SHA and sha(old.PREVIOUS)==old.PREVIOUS_SHA,[METHOD,PRIOR,PRIOR_COMMAND,old.PREVIOUS],category='binding')
        ev=all(c['passed'] for c in self.checks if c['category']!='metric');mv=all(c['passed'] for c in self.checks if c['category']=='metric')
        result={'schema_version':1,'reviewed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Independent execution/runtime/task identity/terminal usage/timing only; no source-tool semantic judgment or grading.',
            'protocol_id':PROTOCOL,'plan_sha256':PLAN_SHA,**self.origin,'raw_directory':str(self.raw),'execution_valid':ev,'metric_reconciliation_valid':mv,
            'reported_metric_pair_eligible':ev and mv and len(self.profiles)==2 and all(p.get('reported_metric_eligible') is True for p in self.profiles),
            'strict_usage_pair_complete':len(self.profiles)==2 and all(p.get('strict_complete') is True for p in self.profiles),
            'passed':all(c['passed'] for c in self.checks),'check_count':len(self.checks),'passed_checks':sum(c['passed'] for c in self.checks),'failed_checks':[c['check'] for c in self.checks if not c['passed']],
            'checks':self.checks,'profiles':self.profiles,'evidence_catalog':self.catalog,'quality_evaluated':False,'benefit_evaluated':False,'source_scope_audit_status':'separate_review_required',
            'method':{'original_path':str(METHOD),'original_sha256':METHOD_SHA,'repeat_adapter_reference':str(PRIOR),'repeat_adapter_reference_sha256':PRIOR_SHA,
              'original_command_replay_reference':str(PRIOR_COMMAND),'original_command_replay_reference_sha256':PRIOR_COMMAND_SHA,
              'origin_adaptations':origin_changes,'profile_adaptations':profile_changes,
              'preserved_profile_logic':'All original checks retained; only fixed q004 selected-root bindings and frozen-command replay replace expansion-preflight command source.',
              'profile_source_sha256':hashlib.sha256(profile_source.encode()).hexdigest(),'audit_sha256':sha(Path(__file__)),'command_helper_sha256':sha(OUT/'command_replay_v1.py')},
            'limitations':['Separate source-tool semantic review is still required; this is not a quality or favorable-endpoint decision.',
              'No grading result, private assignment, anonymous IDs, q005 answer material or other quality scoring was read. Grading-bound control bytes are only hashed for immutable binding.',
              'Persisted full terminal message sums and native modelUsage are not provider billing or proof of unlogged background requests.',
              'Embedding request/token/billing metrics are unavailable and remain null. Unknowns are never imputed as zero.',
              'Build-era native harness is pinned to a424e0c; recorded current upstream main is 49de685. Only the Rust subtree equality is established, not whole-harness equality.',
              'Freshness, source snapshots and cleanup are retained controller observations, not fresh inspection of the expired CI runner.',
              'Successful configured-secret scan does not prove absence of unknown or transformed secrets; secret values were not accessed.',
              'No provider, Docker, model, collector execution, CI dispatch, raw mutation, worktree edit or new agent.']}
        write(destination,result)
        print(json.dumps({'path':str(destination),'sha256':sha(destination),'passed':result['passed'],'execution_valid':ev,'metric_reconciliation_valid':mv,'checks':len(self.checks),'failed_checks':result['failed_checks']},indent=2))
        return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(Audit().run())
