#!/usr/bin/env python3
"""q005 independent-host execution/accounting audit; no provider or quality work."""
from pathlib import Path
from types import SimpleNamespace
import argparse,datetime,hashlib,importlib.util,inspect,json,math,subprocess,sys,tarfile,textwrap
sys.dont_write_bytecode=True
OUT=Path(__file__).resolve().parent
B=OUT.parents[1]
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');H=W/'benchmarks/native-claude-qa-pilot'
M=B/'upstream-main/benchmarks/swe-qa-bench';REFERENCE=B/'native-claude-live-preflight-v1/prepared'
GROUP=B/'native-runtime-q005-preparation-v1/prepared';CI=B/'native-q005-live-v1-ci/39383511290'
RUN=39383511290;HEAD='30fe7fc4b80027cf62006bbe9d3de59445f10a16'
PLAN_SHA='d29aa89c2c541fcb7088833310b87ed712d910c43084678fa5d6f1880ded4027'
PROTOCOL='native-claude-q005-development-v1';PROFILES=('baseline','zvec-grep');KEYS=('preflight',*PROFILES,'joint')
GROUP_SHA='fe9d03f202b7d86ecf06c16e875e6ee4c61dc4328a49a355f0ab7deb6cc20ce0'
META_SHA='33e43e5e0b8052429fd063eecbd8176c1859fb64db3570f42464cffbbceccde2'
MANIFEST_SHA='7edb7862509ab574cdf64767ce51f78283f6c399b0a33dc156082c185149682b'
PACKAGE_SHA='fbe4bd201c8a0520f2373f1039e1d0805a38781c6e2f3ce11b83310d23dc6522'
BUILD='a424e0ce06868dc9ad8f142f57de59b8f9b58699';RUST_TREE='de02030056a3224012725799d845bf2be9edae2b'
DEV=B/'native-q004-live-v1-audit/execution/q004_execution_audit_v1.py';DEV_SHA='70355eb254ccfbdd4a2ec24d03921839d0052f6eeae26af784baa03c8cd42abb'
HELPER_SHA='2d179fc3c1e621f0c0bdf4832fdc138fe0125f8a18af3587a0d94c1e2e2bb984'
REPLAY=B/'native-q005-live-v1-method/replay_retained_pair.py';REPLAY_SHA='4e7ac7e99b510bea848fc9ac788520d14f6d4391f972f6db15d6df0de80590d4'
VERIFIER=REPLAY.with_name('build_blind_packet.py');VERIFIER_SHA='e5bf373d80a34d6b73c37d422169116af66c903b09a441e048606aece1a0cd31'
COLLECTOR=REPLAY.with_name('collect_live.py');COLLECTOR_SHA='d632d0fcbda69e7ba84313d29e9cf5339bcee28af6941c75957a206dd3a576bd'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def write(p,value):
    with p.open('x') as f:json.dump(value,f,indent=2,sort_keys=True,ensure_ascii=False);f.write('\n')
assert sha(DEV)==DEV_SHA and sha(OUT/'command_replay_v1.py')==HELPER_SHA
assert sha(REPLAY)==REPLAY_SHA and sha(VERIFIER)==VERIFIER_SHA and sha(COLLECTOR)==COLLECTOR_SHA
spec=importlib.util.spec_from_file_location('_original_q004_audit',DEV);dev=importlib.util.module_from_spec(spec);spec.loader.exec_module(dev)
old=dev.old
old.RUN,old.HEAD,old.PLAN_SHA,old.PROTOCOL=RUN,HEAD,PLAN_SHA,PROTOCOL
sys.path[:0]=[str(H),str(W/'benchmarks/opencode-qa-pilot'),str(M),str(OUT)]
import runtime_live_v1 as runtime
from command_replay_v1 import expected_command

origin_source=textwrap.dedent(inspect.getsource(old.Audit.verify_origin))
origin_changes=[
 ("j.get('name') == 'expansion (' + self.task + ')'","j.get('name') == ('worker ('+self.artifact_key+')' if self.artifact_key in ('baseline','zvec-grep') else self.artifact_key)"),
 ("f'native-claude-expansion-v1-{RUN}-{self.task}'","f'native-q005-v1-{RUN}-{self.artifact_key}'"),
 ("receipt.get('task') == self.task", "receipt.get('task') == (None if self.artifact_key=='preflight' else self.task) and receipt.get('artifact_key') == self.artifact_key and receipt.get('profile') == (self.artifact_key if self.artifact_key in ('baseline','zvec-grep') else None)"),
 ("run.get('event') == 'workflow_dispatch' and run['repository']","run.get('event') == 'workflow_dispatch' and run.get('path') == '.github/workflows/native-claude-q005-v1.yml' and run['repository']"),
 ("j.get('name') in ('expansion (q002)', 'expansion (q003)')","j.get('name') in ('worker (baseline)', 'worker (zvec-grep)')"),
 ('across both final artifacts when both are available.','across both original independent worker artifacts.')]
for before,after in origin_changes:
    assert origin_source.count(before)==1,before
    origin_source=origin_source.replace(before,after)
exec(compile(origin_source,str(Path(__file__))+'[origin]','exec'),old.__dict__)

class Audit(dev.Audit):
    # Exact inherited functions retain every original per-profile execution,
    # session/tool identity, terminal usage, timing and four-stage setup check.
    verify_one_origin=old.__dict__['verify_origin']
    def __init__(self):
        self.task='q005';self.out=OUT;self.raw=CI/'joint/raw';self.live=self.raw/'live'
        self.checks,self.catalog,self.profiles,self.inits=[],{},[],{}
        self.origins={};self.jobs={};self.contexts={};self.worker_records={}
    def ready(self):
        pending=[]
        for key in KEYS:
            folder=CI/key
            required=('artifact.zip','artifact-byte-verification.json','artifact-metadata.json','run-metadata.json','job-metadata.json')
            if not all((folder/n).is_file() and not (folder/n).is_symlink() for n in required):pending.append(key);continue
            receipt=load(folder/'artifact-byte-verification.json')
            if receipt.get('complete') is not True or receipt.get('exact_extracted_inventory') is not True:pending.append(key)
        return pending
    def origins_verify(self):
        for key in KEYS:
            self.artifact_key=key;self.c=CI/key;self.raw=self.c/'raw'
            count=len(self.checks);self.verify_one_origin()
            self.origins[key]=dict(self.origin)
            rows=load(self.c/'job-metadata.json')['jobs'];name='worker ('+key+')' if key in PROFILES else key
            self.jobs[key]=next(j for j in rows if j['name']==name)
            for c in self.checks[count:]:c['check']=key+':'+c['check']
            if not all(c['passed'] for c in self.checks[count:]):raise ValueError('origin_verification_failed:'+key)
        self.c=CI/'joint';self.raw=self.c/'raw';self.live=self.raw/'live'
        self.check('two_distinct_original_worker_jobs_and_hosted_runners',len({self.jobs[p]['id'] for p in PROFILES})==2 and len({self.jobs[p]['runner_id'] for p in PROFILES})==2,[CI/p/'job-metadata.json' for p in PROFILES],category='binding',workers={p:{'job_id':self.jobs[p]['id'],'runner_id':self.jobs[p]['runner_id']} for p in PROFILES})
    def prepare(self):
        r=self.raw;pre=CI/'preflight/raw';candidate=pre/'zg-install.tgz'
        self.plan=load(r/'runtime-live-plan-v1.json');self.base=load(r/'live-plan-v2.json');inputs=load(r/'inputs.json')
        bound=(sha(r/'runtime-live-plan-v1.json')==PLAN_SHA and (r/'runtime-live-plan-v1.json').read_bytes()==old.gitbytes('benchmarks/native-claude-qa-pilot/runtime-live-plan-v1.json') and self.plan['frozen'] is True and self.plan['protocol_id']==PROTOCOL)
        self.check('exact_frozen_q005_plan',bound,[r/'runtime-live-plan-v1.json'],category='binding');assert bound
        input_ok=sha(r/'inputs.json')==self.base['offline_proof']['inputs_sha256'] and (r/'inputs.json').read_bytes()==old.gitbytes('benchmarks/native-claude-qa-pilot/inputs.json') and inputs['revision']==self.base['offline_proof']['inputs_revision']
        base_ok=sha(r/'live-plan-v2.json')==self.plan['base_plan']['sha256'] and (r/'live-plan-v2.json').read_bytes()==old.gitbytes(self.plan['base_plan']['path'])
        self.check('exact_base_plan_and_original_native_inputs',input_ok and base_ok,[r/'inputs.json',r/'live-plan-v2.json'],category='binding');assert input_ok and base_ok
        allpins={}
        for pins in (inputs['repository_files'],self.base['frozen_harness_sha256'],self.plan['frozen_harness_sha256'],runtime.preparation.PINS):
            for path,digest in pins.items():
                if path in allpins and allpins[path]!=digest:raise ValueError('transitive_pin_conflict:'+path)
                allpins[path]=digest
        bad=[path for path,digest in allpins.items() if hashlib.sha256(old.gitbytes(path)).hexdigest()!=digest]
        self.check('all_repository_pins_at_exact_execution_commit',not bad,[r/'runtime-live-plan-v1.json',r/'live-plan-v2.json',r/'inputs.json'],category='binding',checked_files=len(allpins),mismatches=bad,restricted_control_bytes='Asset readiness/rubric controls only hashed for binding; no private assignment or answer grading parsed.')
        local_bad=[path for path,digest in allpins.items() if path.endswith('.py') and (not (W/path).is_file() or sha(W/path)!=digest)]
        self.check('local_readonly_replay_code_matches_execution_pins',not local_bad,[W/path for path in allpins if path.endswith('.py')],category='binding',mismatches=local_bad);assert not bad and not local_bad
        import accounting_v2
        self.accounting=accounting_v2;self.collector=runtime.native_implementation(PROFILES);self.identity=runtime.identity.implementation()
        args=SimpleNamespace(plan=r/'runtime-live-plan-v1.json',main_root=M,reference_prepared=REFERENCE,prepared_group=GROUP,candidate=candidate)
        checked,base,self.proof=runtime.validate_plan(args,W)
        self.check('accepted_original_native_runtime_proof_and_plan_revalidated',checked==self.plan and base==self.base,[r/'runtime-live-plan-v1.json',r/'live-plan-v2.json',r/'inputs.json',candidate,W/self.base['offline_proof']['directory']/'proof.json',W/self.base['offline_proof']['review_path']],category='binding');assert checked==self.plan and base==self.base
        validated=runtime.preparation.validate(GROUP,REFERENCE,W)
        self.selected_root=GROUP/'q005';self.metadata=load(self.selected_root/'prepared.json');self.manifest=load(self.selected_root/'source-manifest.json')
        self.source=self.selected_root/'tasks/zvec-q005/environment/source';self.canonical={f['path']:f for f in self.manifest['files']}
        self.question_path=W/'benchmarks/zvec-issue-qa'/self.metadata['question']['path'];self.question=self.question_path.read_text()
        source_files=sorted(p.relative_to(self.source).as_posix() for p in self.source.rglob('*') if p.is_file())
        ok=(sha(GROUP/'group.json')==sha(r/'q005-prepared-group.json')==self.plan['prepared_group']['group_sha256']==GROUP_SHA and sha(self.selected_root/'prepared.json')==sha(r/'q005-prepared.json')==self.plan['prepared_group']['metadata_sha256']['q005']==META_SHA and sha(self.selected_root/'source-manifest.json')==sha(r/'q005-source-manifest.json')==self.metadata['source_manifest_sha256']==MANIFEST_SHA and validated==load(r/'q005-prepared-group.json') and validated['task_ids']==['q005'] and self.metadata['task_id']=='q005' and self.metadata['scheduled_task_ids']==['q005'] and self.metadata['dataset']==self.plan['dataset'] and sha(W/self.plan['dataset']['path'])==self.plan['dataset']['sha256'] and sha(self.question_path)==self.metadata['question']['sha256'] and self.manifest['source_commit']==self.metadata['source_commit'] and self.manifest['inventory_sha256']==self.metadata['source_inventory_sha256'] and len(self.canonical)==len(self.manifest['files'])==self.metadata['source_file_count']==1596 and sorted(self.canonical)==source_files and not any(p.is_symlink() for p in self.source.rglob('*')) and all((self.source/n).stat().st_size==f['bytes'] and sha(self.source/n)==f['sha256'] for n,f in self.canonical.items()) and (r/'q001-reference-prepared.json').read_bytes()==(REFERENCE/'prepared.json').read_bytes())
        self.check('selected_q005_group_metadata_manifest_all_source_bytes_and_question',ok,[GROUP/'group.json',self.selected_root/'prepared.json',self.selected_root/'source-manifest.json',r/'q005-prepared-group.json',r/'q005-prepared.json',r/'q005-source-manifest.json',self.question_path,r/'q001-reference-prepared.json'],category='binding',source_commit=self.metadata['source_commit'],source_file_count=len(self.canonical),source_inventory_sha256=self.metadata['source_inventory_sha256']);assert ok
        self.execution=load(self.live/'execution.json');self.collection=load(self.live/'collection/collection.json')
        remote='/home/runner/work/_temp/qa-runtime/q005';remote_live='/home/runner/work/_temp/native-claude-q005-v1-pilot/live'
        self.ctx={'recorded_prepared_root':remote}
        for profile in PROFILES:
            wr=CI/profile/'raw';context=load(wr/'live-q005-worker-context.json');record=load(wr/'live/execution.json');self.contexts[profile]=context;self.worker_records[profile]=record
            expected=runtime.worker_context(SimpleNamespace(plan=r/'runtime-live-plan-v1.json',prepared_group=Path(remote).parent,output=Path(remote_live)),self.plan,profile,dry_run=False)
            common=['runtime-live-plan-v1.json','runtime-execution-decision-v1.json','runtime-grader-contract-v1.json','live-plan-v2.json','inputs.json','q005-prepared-group.json','q005-prepared.json','q005-source-manifest.json','q001-reference-prepared.json','package-derivation.json']
            equal=all((wr/n).read_bytes()==(r/n).read_bytes()==(pre/n).read_bytes() for n in common)
            self.check(profile+':frozen_context_controls_and_single_native_slot',context==expected and equal and len(record['slots'])==1 and record['slots'][0]['profile']==profile and record['protocol_id']==PROTOCOL and record['plan_sha256']==PLAN_SHA and record['offline_proof_sha256']==self.base['offline_proof']['proof_sha256'] and record['offline_inputs_sha256']==self.base['offline_proof']['inputs_sha256'] and record['dry_run'] is False,[wr/'live-q005-worker-context.json',wr/'live/execution.json',*[wr/n for n in common]],category='binding')
            prior=load(wr/'q004-confirmation-execution.json');prior_ok=prior['id']==39380248003 and prior['head_sha']=='3978d4bbbbb1e37961c277cdf76696b84c69e3bc' and prior['run_attempt']==1 and prior['status']=='completed'
            self.check(profile+':q004_execution_terminal_before_q005_worker',prior_ok,[wr/'q004-confirmation-execution.json'],category='binding')
            sysinfo=record['system'];self.check(profile+':pinned_native_system_versions',sysinfo['harbor_version']=='0.18.0' and sysinfo['docker_architecture']=='x86_64' and sysinfo['python'].startswith('3.12.'),[wr/'live/execution.json'],system=sysinfo)
            nested=r/'workers'/profile;receipt=runtime.artifacts.verify_retained(nested,run_id=RUN,head_sha=HEAD,profile=profile)
            same=(nested/'artifact.zip').read_bytes()==(CI/profile/'artifact.zip').read_bytes() and runtime.assembly.inventory(nested/'raw')==runtime.assembly.inventory(wr)
            self.check(profile+':joint_nested_worker_zip_and_every_raw_byte_match_external_original',same and receipt['artifact_sha256']==self.origins[profile]['artifact_sha256'] and receipt['job_id']==self.jobs[profile]['id'] and receipt['runner_id']==self.jobs[profile]['runner_id'],[nested/'artifact.zip',nested/'artifact-byte-verification.json',CI/profile/'artifact.zip'],category='binding',raw_file_count=len(runtime.assembly.inventory(wr)))
        self.check('two_original_worker_slots_exact_in_joint_no_shared_host_or_pair_time',self.execution['slots']==[self.worker_records[p]['slots'][0] for p in PROFILES] and self.execution['system']['per_profile']=={p:self.worker_records[p]['system'] for p in PROFILES} and set(self.execution)==set(runtime.assembly.HEADER_KEYS)|{'slots','system'} and [p['profile'] for p in self.collection['profiles']]==list(PROFILES) and self.plan['mode']=='independent_profile_jobs' and self.plan['scheduled_solver_trials']==2 and self.plan['n_attempts_per_profile']==1 and self.plan['max_retries']==0,[self.live/'execution.json',self.live/'profile-assembly.json',self.live/'collection/collection.json'],category='binding')
        qualified=[(self.jobs[p]['runner_id'],v) for p in PROFILES for v in self.worker_records[p]['slots'][0]['fresh_volumes']]
        self.check('four_fresh_runner_qualified_volume_identities',len(qualified)==len(set(qualified))==4,[CI/p/'raw/live/execution.json' for p in PROFILES],qualified_identities=qualified)
        for record in [self.execution,self.collection]:assert record['protocol_id']==PROTOCOL and record['plan_sha256']==PLAN_SHA and record['offline_proof_sha256']==self.base['offline_proof']['proof_sha256']
        replay=runtime.identity.verify(GROUP,'q005',self.live,reference_prepared=REFERENCE,recorded_prepared_root=remote,repo_root=W)
        replay_path=OUT/'task-identity-replay.json';write(replay_path,replay)
        expected_saved=dict(replay,native_structural_valid=self.collection['structural_valid'],assembly_receipt_sha256=sha(self.live/'profile-assembly.json'),worker_receipt_sha256={p:sha(r/'workers'/p/'artifact-byte-verification.json') for p in PROFILES},shared_host_claimed=False,aggregate_pair_time_invented=False)
        expected_saved['joint_gate_valid']=replay['task_identity_valid'] and self.collection['structural_valid']
        self.check('actual_q005_task_identity_replay_and_joint_gate',replay['task_identity_valid'] is True and load(self.live/'task-identity.json')==expected_saved and expected_saved['joint_gate_valid'],[replay_path,self.live/'task-identity.json'],category='binding',errors=replay['errors'],replayed_checks=len(replay['checks']))
        self.expected_commands={p:expected_command(p,self.selected_root,remote,remote_live,M,self.base,OUT/'logical-command-output') for p in PROFILES}
        self.command_replay_path=OUT/'frozen-command-replay.json';write(self.command_replay_path,{'method':'Original frozen native build_command only, exact recorded q005 path mappings; no solver.','helper_sha256':HELPER_SHA,'commands':self.expected_commands})
        self.pkg=load(r/'package-derivation.json');self.package_sha=sha(candidate);self.payloads={x['path'].removeprefix('package/'):{'bytes':x['derived_bytes'],'sha256':x['derived_sha256'],'mode':x['mode']} for x in self.pkg['runtime_payloads']}
        with tarfile.open(candidate) as z:
            payload_ok=all(hashlib.sha256(z.extractfile('package/'+k).read()).hexdigest()==v['sha256'] and z.getmember('package/'+k).size==v['bytes'] and format(z.getmember('package/'+k).mode,'04o')==v['mode'] for k,v in self.payloads.items())
        self.check('canonical_preflight_package_four_unchanged_runtime_payloads',payload_ok and len(self.payloads)==4 and self.package_sha==PACKAGE_SHA==self.pkg['install_package_sha256']==self.base['candidate_sha256']==self.execution['candidate_sha256'] and self.pkg['upstream_commit']==inputs['upstream_commit']==self.base['upstream_main']==BUILD and all(x['unchanged'] is True for x in self.pkg['runtime_payloads']),[candidate,r/'package-derivation.json',r/'inputs.json',self.live/'execution.json'],category='binding',static_archive_retained_once_in_preflight=True)
        build_tree=subprocess.check_output(['git','rev-parse',BUILD+':rust'],cwd=W,text=True,timeout=30).strip()
        for key in KEYS:
            rr=CI/key/'raw';current=load(rr/'current-upstream.json');tree=load(rr/'current-upstream-tree.json');rust=[x for x in tree['tree'] if x['path']=='rust' and x['type']=='tree']
            self.check(key+':recorded_current_main_rust_tree_equals_build_rust_tree',current['sha']==tree['sha'] and tree['truncated'] is False and len(rust)==1 and rust[0]['sha']==build_tree==RUST_TREE,[rr/'current-upstream.json',rr/'current-upstream-tree.json'],category='binding',actual_main_observed=current['sha'],rust_build_commit=BUILD,whole_harness_same_as_current_main='not_claimed')
        for key,scan_name in [('preflight','Scan provider-free retention'),('baseline','Scan original worker secrets before retention'),('zvec-grep','Scan original worker secrets before retention'),('joint','Scan joint retention')]:
            steps=self.jobs[key]['steps'];scan=[s for s in steps if s['name']==scan_name];upload=[s for s in steps if 'upload-artifact' in s['name']]
            okay=self.jobs[key]['conclusion']=='success' and len(scan)==len(upload)==1 and scan[0]['conclusion']==upload[0]['conclusion']=='success' and scan[0]['number']<upload[0]['number']
            if key=='joint':
                assembled=[s for s in steps if s['name']=='Assemble unchanged slots and jointly collect original answers and usage'];okay &= len(assembled)==1 and assembled[0]['conclusion']=='success' and assembled[0]['number']<scan[0]['number']
            self.check(key+':actual_secret_scan_and_upload_receipts',okay,[CI/key/'job-metadata.json'],category='binding',secret_values_read=False)
    def production_replay(self):
        spec=importlib.util.spec_from_file_location('_q005_retained_production_replay',REPLAY);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mod.OUT=OUT/'production-replay'
        mod.audit()
        path=mod.OUT/'verification.json';result=load(path)
        self.check('production_assembly_collection_replay_all_original_worker_bytes_and_metrics',result['all_fidelity_checks_passed'] is True and all(result['checks'].values()) and result['native_collection_returncode']==0 and result['native_structural_valid'] is True and result['native_task_identity_valid'] is True and result['solver_calls']==0 and result['docker_invoked'] is False and result['shared_host_claimed'] is False and result['aggregate_pair_time_invented'] is False,[path,REPLAY,VERIFIER],category='binding',replay_check_count=len(result['checks']),rehashed_original_file_counts=result['original_files_rehashed_before_and_after'])
    def run(self):
        dst=OUT/'execution-review.json'
        if dst.exists() or (OUT/'task-identity-replay.json').exists() or (OUT/'production-replay').exists():raise FileExistsError('exclusive_q005_audit_exists')
        try:
            self.origins_verify();self.prepare()
            for profile in PROFILES:
                try:self.profile(profile)
                except Exception as exc:self.check(profile+':required_evidence_read_or_schema',False,error_type=type(exc).__name__,error=str(exc))
            roots=[p.get('root_session_id') for p in self.profiles];a=self.inits.get('baseline',{});z=self.inits.get('zvec-grep',{})
            tools_ok=isinstance(a.get('tools'),list) and isinstance(z.get('tools'),list) and set(z['tools'])-set(a['tools'])=={'mcp__zvec_grep__zvec_grep_search'} and not set(a['tools'])-set(z['tools'])
            self.check('two_distinct_native_roots_and_same_tools_except_zg',len(roots)==len(set(roots))==2 and all(roots) and tools_ok and self.collection['structural_valid'] is True,[self.live/'execution.json',self.live/'collection/collection.json'])
            self.additional_metrics();self.production_replay()
        except Exception as exc:self.check('required_global_evidence_read_or_schema',False,error_type=type(exc).__name__,error=str(exc))
        self.check('all_frozen_review_methods_unchanged',sha(DEV)==DEV_SHA and sha(dev.METHOD)==dev.METHOD_SHA and sha(dev.PRIOR)==dev.PRIOR_SHA and sha(dev.PRIOR_COMMAND)==dev.PRIOR_COMMAND_SHA and sha(old.PREVIOUS)==old.PREVIOUS_SHA and sha(OUT/'command_replay_v1.py')==HELPER_SHA and sha(REPLAY)==REPLAY_SHA and sha(VERIFIER)==VERIFIER_SHA,[DEV,dev.METHOD,dev.PRIOR,dev.PRIOR_COMMAND,old.PREVIOUS,OUT/'command_replay_v1.py',REPLAY,VERIFIER],category='binding')
        ev=all(c['passed'] for c in self.checks if c['category']!='metric');mv=all(c['passed'] for c in self.checks if c['category']=='metric') if len(self.profiles)==2 and all(p.get('complete_review') for p in self.profiles) else None
        result={'schema_version':1,'reviewed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'protocol_id':PROTOCOL,'plan_sha256':PLAN_SHA,'run_id':RUN,'head_sha':HEAD,'task_id':'q005','run_attempt':1,'mode':'independent_profile_jobs','artifact_origins':self.origins,'execution_valid':ev,'metric_reconciliation_valid':mv,'reported_metric_pair_eligible':bool(ev and mv and len(self.profiles)==2 and all(p.get('reported_metric_eligible') is True for p in self.profiles)),'strict_usage_pair_complete':len(self.profiles)==2 and all(p.get('strict_complete') is True for p in self.profiles),'passed':all(c['passed'] for c in self.checks),'check_count':len(self.checks),'passed_checks':sum(c['passed'] for c in self.checks),'failed_checks':[c['check'] for c in self.checks if not c['passed']],'checks':self.checks,'profiles':self.profiles,'evidence_catalog':self.catalog,'quality_evaluated':False,'benefit_evaluated':False,'source_scope_audit_status':'separate_review_required','shared_host_claimed':False,'aggregate_pair_time_invented':False,'method':{'path':str(Path(__file__)),'sha256':sha(Path(__file__)),'original_q004_method_sha256':DEV_SHA,'original_expansion_method_sha256':dev.METHOD_SHA,'profile_logic':'Original q004 adapter profile and additional_metrics methods inherited verbatim. Q005 setup replaces only task/protocol/artifact/host bindings.','origin_adaptations':origin_changes,'production_replay_sha256':REPLAY_SHA},'limitations':['Execution/accounting only. Source-tool semantic scope and source-grounded answer quality require separate reviews.','No private assignment, anonymous packet or model grading read. Final answer bytes are compared only for immutable execution identity.','The original per-profile timing scopes are retained for two independent workers. No serial same-host requirement, combined pair duration or q004 timing pool is constructed.','Original archive retained once in verified preflight; worker and joint copies omit only that duplicate static input per frozen plan.','Complete terminal/message sums and reported modelUsage are not provider billing or proof of unlogged requests. Embedding request/token/billing values remain null.','Current-main Rust subtree equality is checked separately from the pinned build-era native harness; whole-harness equality is not claimed.','Source snapshots, freshness and cleanup are retained controller observations; expired runners are not reinspected.','No new solver, model, provider, Docker, CI dispatch, raw mutation or frozen-code edit. Production collector replay writes only exclusive audit outputs.']}
        write(dst,result);print(json.dumps({'path':str(dst),'sha256':sha(dst),'passed':result['passed'],'checks':len(self.checks),'failed_checks':result['failed_checks']},indent=2));return 0 if result['passed'] else 1

def selfcheck():
    checks={'original_profile_inherited_verbatim':Audit.profile is dev.Audit.profile,'original_terminal_and_cold_setup_inherited_verbatim':Audit.additional_metrics is dev.Audit.additional_metrics,'helper_unchanged':sha(OUT/'command_replay_v1.py')==HELPER_SHA,'plan_exact':sha(H/'runtime-live-plan-v1.json')==PLAN_SHA,'syntax':bool(compile(Path(__file__).read_text(),str(Path(__file__)),'exec'))}
    for profiles in (('baseline',),('zvec-grep',),PROFILES):
        module=runtime.native_implementation(profiles)
        checks['native_bodies_unchanged:'+','.join(profiles)]=module.run_pair.__code__.co_code==runtime.live.run_pair.__code__.co_code and module.collect.__code__.co_code==runtime.live.collect.__code__.co_code
    assert all(checks.values());return checks
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--audit',action='store_true');args=parser.parse_args()
    if not args.audit:print(json.dumps({'status':'prepared','checks':selfcheck(),'artifact_raw_read':False}));raise SystemExit(0)
    audit=Audit();pending=audit.ready()
    if pending:print(json.dumps({'status':'waiting_for_complete_receipts','pending_artifacts':pending,'raw_read':False,'outcome_report_written':False}));raise SystemExit(3)
    raise SystemExit(audit.run())
