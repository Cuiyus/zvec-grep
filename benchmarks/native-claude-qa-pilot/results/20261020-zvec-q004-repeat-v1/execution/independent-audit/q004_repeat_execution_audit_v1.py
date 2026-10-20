#!/usr/bin/env python3
"""Read-only q004 repetition audit. Never executes models, providers, CI or collectors.

One invocation audits exactly one ready pair and writes exclusive new evidence under
pairs/<key>. Missing complete collector receipt means waiting, with no outcome report.
All original q004 profile and cold-setup checks are inherited without modification.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse, ast, datetime, hashlib, importlib.util, inspect, json, sys, textwrap
sys.dont_write_bytecode = True
METHOD_ROOT = Path(__file__).resolve().parent
B = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
H = W / 'benchmarks/native-claude-qa-pilot'
RUN = 39380248003
HEAD = '3978d4bbbbb1e37961c277cdf76696b84c69e3bc'
PLAN_SHA = 'c2d102654a6f11ab330f843c501b1820c0c7385595e0fdb1d69635880f218c76'
RUNTIME_SHA = '803750ff8050adf770325345b907ef662c578d6dff6e0fd480f92f78d3a1148d'
PROTOCOL = 'native-claude-zvec-q004-repeat-v1'
CI = B / 'native-q004-repeat-v1-ci' / str(RUN)
DEVELOPMENT_METHOD = B / 'native-q004-live-v1-audit/execution/q004_execution_audit_v1.py'
DEVELOPMENT_SHA = '70355eb254ccfbdd4a2ec24d03921839d0052f6eeae26af784baa03c8cd42abb'
COMMAND_SHA = '2d179fc3c1e621f0c0bdf4832fdc138fe0125f8a18af3587a0d94c1e2e2bb984'
COLLECTOR = B / 'native-q004-repeat-v1-method/collect.py'
COLLECTOR_SHA = 'da2127b430bbf05dace9c022b2826d098f5e3e276e736c23ea1191ca3f12a5ec'
KEYS = [f'q004-r{i:02d}' for i in range(1, 11)]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p): return json.loads(p.read_text())
def write(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True, ensure_ascii=False); f.write('\n')
assert sha(DEVELOPMENT_METHOD) == DEVELOPMENT_SHA
assert sha(METHOD_ROOT / 'command_replay_v1.py') == COMMAND_SHA
assert sha(COLLECTOR) == COLLECTOR_SHA
spec = importlib.util.spec_from_file_location('_preserved_q004_development_audit', DEVELOPMENT_METHOD)
dev = importlib.util.module_from_spec(spec); spec.loader.exec_module(dev)
# Pin local imports before importing the reusable cohort adapter.
plan = load(H / 'architecture-repeat-plan-v1.json')
assert sha(H / 'architecture-repeat-plan-v1.json') == PLAN_SHA
assert all(sha(W / path) == digest for path, digest in plan['frozen_harness_sha256'].items())
sys.path[:0] = [str(H), str(W / 'benchmarks/opencode-qa-pilot'), str(dev.M), str(METHOD_ROOT)]
import architecture_repeat_v1 as cohort
assert cohort.PROTOCOL == PROTOCOL and cohort.REFERENCE_SHA == RUNTIME_SHA
old = dev.old
for module in (dev, old):
    module.RUN, module.HEAD, module.PLAN_SHA, module.PROTOCOL = RUN, HEAD, PLAN_SHA, PROTOCOL
old.PAIR_KEYS = KEYS
old.ORDERS = {'q004': ['baseline', 'zvec-grep']}
dev.METHOD_ROOT = METHOD_ROOT
dev.RUNTIME_SHA = RUNTIME_SHA
dev.cohort = cohort
dev.SimpleNamespace = SimpleNamespace
adaptations = []
def replace_once(source, before, after, scope):
    if source.count(before) != 1:
        raise ValueError('adaptation_boundary_changed:' + scope + ':' + before[:80])
    adaptations.append({'scope': scope, 'before': before, 'after': after})
    return source.replace(before, after, 1)

# Preserve the expansion method's original ZIP/metadata/per-file verification body,
# with only exact new run, artifact, job, receipt and ten-runner cohort identities.
origin_source = textwrap.dedent(inspect.getsource(old.Audit.verify_origin))
for before, after in [
    ("j.get('name') == 'expansion (' + self.task + ')'", "j.get('name') == 'repeat (' + self.pair['key'] + ')'"),
    ("f'native-claude-expansion-v1-{RUN}-{self.task}'", "f'native-q004-repeat-v1-{RUN}-{self.pair[\"key\"]}'"),
    ("receipt.get('task') == self.task and receipt.get('job_id')", "receipt.get('task') == self.task and receipt.get('pair_key') == self.pair['key'] and receipt.get('artifact_key') == self.pair['key'] and receipt.get('trial_index') == self.pair['trial_index'] and receipt.get('job_id')"),
    ("run.get('event') == 'workflow_dispatch' and run['repository']", "run.get('event') == 'workflow_dispatch' and run.get('path') == '.github/workflows/native-claude-q004-repeat-v1.yml' and run['repository']"),
    ("j.get('name') in ('expansion (q002)', 'expansion (q003)')", "j.get('name') in tuple('repeat (' + key + ')' for key in PAIR_KEYS)"),
    ('len(peers) < 2', 'len(peers) < 10'),
    ('across both final artifacts when both are available.', 'across all ten final pair artifacts when available.'),
]: origin_source = replace_once(origin_source, before, after, 'origin')
exec(compile(origin_source, str(Path(__file__)) + '[origin]', 'exec'), old.__dict__)

# Reuse the entire q004 preparation audit, updating only the outer cohort bindings.
prepare_source = textwrap.dedent(inspect.getsource(dev.Audit.prepare))
prepare_source = replace_once(prepare_source,
    "self.plan=load(r/'architecture-live-plan-v1.json');self.base=load(r/'live-plan-v2.json');inputs=load(r/'inputs.json')",
    "self.plan=load(r/'architecture-repeat-plan-v1.json');self.runtime=load(r/'architecture-live-plan-v1.json');self.base=load(r/'live-plan-v2.json');inputs=load(r/'inputs.json')", 'prepare')
prepare_source = prepare_source.replace("r/'architecture-live-plan-v1.json'", "r/'architecture-repeat-plan-v1.json'")
prepare_source = prepare_source.replace("self.runtime=load(r/'architecture-repeat-plan-v1.json')", "self.runtime=load(r/'architecture-live-plan-v1.json')")
prepare_source = replace_once(prepare_source,
    "old.gitbytes('benchmarks/native-claude-qa-pilot/architecture-live-plan-v1.json')",
    "old.gitbytes('benchmarks/native-claude-qa-pilot/architecture-repeat-plan-v1.json')", 'prepare')
prepare_source = replace_once(prepare_source,
    "allpins={**inputs['repository_files'],**self.base['frozen_harness_sha256'],**self.plan['frozen_harness_sha256']}",
    """runtime_ok=sha(r/'architecture-live-plan-v1.json')==RUNTIME_SHA and (r/'architecture-live-plan-v1.json').read_bytes()==old.gitbytes(cohort.REFERENCE)
    decision_path=r/'architecture-repeat-decision-v1.json'
    decision_ok=decision_path.read_bytes()==old.gitbytes(cohort.DECISION) and sha(decision_path)==self.plan['frozen_harness_sha256'][cohort.DECISION]
    self.check('exact_original_q004_runtime_reference_and_repeat_decision',runtime_ok and decision_ok,[r/'architecture-live-plan-v1.json',decision_path],category='binding')
    assert runtime_ok and decision_ok
    allpins={}
    for pinset in (inputs['repository_files'],self.base['frozen_harness_sha256'],self.runtime['frozen_harness_sha256'],self.plan['frozen_harness_sha256']):
        for path,digest in pinset.items():
            if path in allpins and allpins[path]!=digest:raise ValueError('conflicting_transitive_pin:'+path)
            allpins[path]=digest""", 'prepare')
prepare_source = replace_once(prepare_source,
    "sys.path[:0]=[str(H),str(W/'benchmarks/opencode-qa-pilot'),str(M),str(OUT)]",
    "sys.path[:0]=[str(H),str(W/'benchmarks/opencode-qa-pilot'),str(M),str(METHOD_ROOT)]", 'prepare')
prepare_source = replace_once(prepare_source,
    'self.identity=architecture_identity_v1.implementation()',
    "self.implementation=cohort.execution_implementation(self.pair['trial_index']);self.identity=self.implementation.identity", 'prepare')
prepare_source = replace_once(prepare_source, 'assert base==self.base', """assert base==self.base
    validation_args=SimpleNamespace(plan=r/'architecture-repeat-plan-v1.json',main_root=M,reference_prepared=REFERENCE,prepared_group=GROUP,candidate=r/'zg-install.tgz',repetition=self.pair['trial_index'])
    checked,checked_base,checked_proof=cohort.validate_plan(validation_args,W)
    self.check('full_outer_repeat_and_original_runtime_validation',checked==self.plan and checked_base==self.base and checked_proof==self.proof,[r/'architecture-repeat-plan-v1.json',r/'architecture-live-plan-v1.json'],category='binding')
    assert checked==self.plan and checked_base==self.base and checked_proof==self.proof""", 'prepare')
prepare_source = prepare_source.replace("r/'live-q004-context.json'", "r/'live-q004-repeat-context.json'")
expected_start = "expected={'schema_version':1,'protocol_id':PROTOCOL,'task_id':'q004','profiles':['baseline','zvec-grep'],'plan_sha256':PLAN_SHA,"
expected_end = "'candidate_sha256':PACKAGE_SHA,'recorded_prepared_root':remote,'dry_run':False}"
start = prepare_source.index(expected_start); end = prepare_source.index(expected_end, start) + len(expected_end)
old_expected = prepare_source[start:end]
new_expected = """context_args=SimpleNamespace(task_id='q004',plan=r/'architecture-repeat-plan-v1.json')
    expected=self.implementation.context_record(context_args,self.plan,self.base,dry_run=False,recorded_prepared_root=remote)
    literal_expected={'schema_version':1,'protocol_id':PROTOCOL,'task_id':'q004','profiles':self.pair['profiles'],'plan_sha256':PLAN_SHA,
        'base_plan_sha256':self.plan['base_plan']['sha256'],'dataset_sha256':self.plan['dataset']['sha256'],'prepared_group_sha256':GROUP_SHA,
        'prepared_metadata_sha256':META_SHA,'offline_proof_sha256':self.base['offline_proof']['proof_sha256'],'candidate_sha256':PACKAGE_SHA,'recorded_prepared_root':remote,'dry_run':False,
        'pair_key':self.pair['key'],'trial_index':self.pair['trial_index'],'scheduled_pairs':10,'scheduled_solver_trials':20}
    self.check('independent_literal_and_frozen_cohort_context_agree',expected==literal_expected,[r/'architecture-repeat-plan-v1.json'],category='binding')
    assert expected==literal_expected"""
prepare_source = replace_once(prepare_source, old_expected, new_expected, 'prepare')
old_order = """self.check('one_q004_pair_baseline_then_zg_one_attempt_no_retry',self.plan['tasks']==[{'id':'q004','profiles':['baseline','zvec-grep']}]
        and self.plan['n_attempts']==1 and self.plan['max_retries']==0
        and [s['profile'] for s in self.execution['slots']]==['baseline','zvec-grep']
        and [s['profile'] for s in self.collection['profiles']]==['baseline','zvec-grep'],[r/'architecture-repeat-plan-v1.json',self.live/'execution.json',self.live/'collection/collection.json'])"""
new_order = """self.check('exact_scheduled_q004_pair_order_one_attempt_no_retry',self.plan['schedule']==cohort.schedule()
        and self.plan['scheduled_pairs']==10 and self.plan['scheduled_solver_trials']==20 and self.plan['task_ids']==['q004']
        and self.plan['n_attempts_per_pair']==1 and self.plan['max_retries']==0
        and load(r/'scheduled-pair.json')==self.pair
        and [s['profile'] for s in self.execution['slots']]==self.pair['profiles']
        and [s['profile'] for s in self.collection['profiles']]==self.pair['profiles'],[r/'architecture-repeat-plan-v1.json',r/'scheduled-pair.json',self.live/'execution.json',self.live/'collection/collection.json'])"""
prepare_source = replace_once(prepare_source, old_order, new_order, 'prepare')
prepare_source = replace_once(prepare_source,
    "architecture_identity_v1.verify(GROUP,'q004',self.live,reference_prepared=REFERENCE,recorded_prepared_root=remote,repo_root=W)",
    "self.identity.verify(GROUP,'q004',self.live,reference_prepared=REFERENCE,recorded_prepared_root=remote,expected_profiles=self.pair['profiles'],repo_root=W)", 'prepare')
prepare_source = replace_once(prepare_source,
    "for p in ('baseline','zvec-grep')}", "for p in self.pair['profiles']}", 'prepare')
prepare_source = prepare_source.replace("sha(OUT/'command_replay_v1.py')", "sha(METHOD_ROOT/'command_replay_v1.py')")
prepare_source = replace_once(prepare_source,
    "pilot_job=next(j for j in jobs if j['name']=='pilot')",
    "pilot_job=next(j for j in jobs if j['name']=='repeat ('+self.pair['key']+')')", 'prepare')
prepare_source = replace_once(prepare_source,
    "s['name']=='Collect exact answers and task identity'",
    "s['name']=='Collect exact answers and identity even after failure'", 'prepare')
exec(compile(prepare_source, str(Path(__file__)) + '[prepare]', 'exec'), dev.__dict__)

run_source = textwrap.dedent(inspect.getsource(dev.Audit.run))
run_source = replace_once(run_source, "for profile in ('baseline','zvec-grep'):", "for profile in self.pair['profiles']:", 'run')
run_source = replace_once(run_source, "    self.verify_origin()\n    try:\n        self.prepare()", """    try:
        self.verify_origin()
        if not all(c['passed'] for c in self.checks):raise ValueError('origin_checks_failed')
        self.prepare()""", 'run')
run_source = replace_once(run_source,
    "'protocol_id':PROTOCOL,'plan_sha256':PLAN_SHA,**self.origin,",
    "'protocol_id':PROTOCOL,'plan_sha256':PLAN_SHA,'runtime_reference_sha256':RUNTIME_SHA,'pair_key':self.pair['key'],'trial_index':self.pair['trial_index'],'scheduled_profile_order':self.pair['profiles'],**self.origin,", 'run')
run_source = replace_once(run_source,
    "'method':{'original_path':str(METHOD)",
    "'method':{'q004_development_method_path':str(DEVELOPMENT_METHOD),'q004_development_method_sha256':DEVELOPMENT_SHA,'cohort_binding_adaptations':adaptations,'original_path':str(METHOD)", 'run')
run_source = run_source.replace("sha(OUT/'command_replay_v1.py')", "sha(METHOD_ROOT/'command_replay_v1.py')")
run_source = replace_once(run_source,
    "self.check('all_frozen_review_method_sources_unchanged',sha(METHOD)==METHOD_SHA",
    "self.check('all_frozen_review_method_sources_unchanged',sha(DEVELOPMENT_METHOD)==DEVELOPMENT_SHA and sha(METHOD_ROOT/'command_replay_v1.py')==COMMAND_SHA and sha(METHOD)==METHOD_SHA", 'run')
# Missing/incomplete evidence is explicit, with no vacuous metric pass or zero fill.
run_source = replace_once(run_source,
    "mv=all(c['passed'] for c in self.checks if c['category']=='metric')",
    "mv=all(c['passed'] for c in self.checks if c['category']=='metric') if len(self.profiles)==2 and all(p.get('complete_review') for p in self.profiles) else None", 'run')
for name in ('DEVELOPMENT_METHOD','DEVELOPMENT_SHA','COMMAND_SHA','adaptations'):
    setattr(dev, name, globals()[name])
dev.__file__ = str(Path(__file__))
exec(compile(run_source, str(Path(__file__)) + '[run]', 'exec'), dev.__dict__)

class Audit(dev.Audit):
    verify_origin = old.__dict__['verify_origin']
    prepare = dev.__dict__['prepare']
    run = dev.__dict__['run']
    # profile and additional_metrics are inherited verbatim from q004 development.
    def __init__(self, key):
        if key not in KEYS: raise ValueError('undeclared_pair')
        self.pair=cohort.selected_pair(int(key.rsplit('r',1)[1])); self.task='q004'
        assert self.pair['key']==key
        self.c=CI/key; self.raw=self.c/'raw'; self.live=self.raw/'live'
        self.out=METHOD_ROOT/'pairs'/key
        self.checks,self.catalog,self.profiles,self.inits=[],{},[],{}
        self.origin={'run_id':RUN,'head_sha':HEAD,'run_attempt':1,'task_id':'q004','artifact_id':None,'artifact_sha256':None,'job_id':None,'job_conclusion':None}
        dev.OUT=self.out
    def receipt_ready(self):
        required=('artifact.zip','artifact-byte-verification.json','artifact-metadata.json','run-metadata.json','job-metadata.json')
        missing=[name for name in required if not (self.c/name).is_file() or (self.c/name).is_symlink()]
        if missing:return False,{'status':'waiting_for_verified_artifact','missing_or_unsafe':missing}
        receipt=load(self.c/'artifact-byte-verification.json')
        if receipt.get('complete') is not True or receipt.get('exact_extracted_inventory') is not True:
            return False,{'status':'waiting_for_complete_receipt'}
        return True,{'status':'ready_for_independent_verification'}

def check_method():
    """No artifact inspection. Prove preserved methods and both context bindings."""
    checks=[]
    def record(name,ok,**details):
        checks.append({'check':name,'passed':bool(ok),**details})
        if not ok:raise ValueError('method_check_failed:'+name)
    record('development_method_exact_sha',sha(DEVELOPMENT_METHOD)==DEVELOPMENT_SHA)
    record('original_profile_function_inherited_verbatim',Audit.profile is dev.Audit.profile)
    record('original_four_phase_and_terminal_usage_additions_inherited_verbatim',Audit.additional_metrics is dev.Audit.additional_metrics)
    record('original_command_helper_bytes_unchanged',sha(METHOD_ROOT/'command_replay_v1.py')==COMMAND_SHA)
    record('collector_schema_source_pinned',sha(COLLECTOR)==COLLECTOR_SHA)
    record('only_declared_cohort_keys',KEYS==sorted(p['key'] for p in cohort.schedule()))
    record('all_prepare_original_check_ids_preserved_except_pair_order',
        set(check_ids(inspect.getsource(dev.Audit.prepare)))-{'one_q004_pair_baseline_then_zg_one_attempt_no_retry'} <= set(check_ids(prepare_source)))
    base=load(H/'live-plan-v2.json')
    for i in (1,2):
        impl=cohort.execution_implementation(i)
        context=impl.context_record(SimpleNamespace(task_id='q004',plan=H/'architecture-repeat-plan-v1.json'),plan,base,dry_run=False,recorded_prepared_root='/home/runner/work/_temp/qa-architecture/q004')
        pair=cohort.selected_pair(i)
        record('context_binding_order_'+str(i),context['protocol_id']==PROTOCOL and context['plan_sha256']==PLAN_SHA and context['profiles']==pair['profiles'] and context['trial_index']==i and context['pair_key']==pair['key'] and context['scheduled_pairs']==10 and context['scheduled_solver_trials']==20)
    record('final_syntax_valid',bool(compile(Path(__file__).read_text(),str(Path(__file__)),'exec')))
    return checks

def check_ids(source):
    tree=ast.parse(textwrap.dedent(source))
    return [node.args[0].value for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='check' and node.args and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,str)]

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-method',action='store_true');mode.add_argument('--pair',choices=KEYS)
    args=parser.parse_args()
    if args.check_method:
        checks=check_method()
        print(json.dumps({'status':'method_prepared_artifacts_not_audited','checks':checks,'passed':all(x['passed'] for x in checks),'source_sha256':sha(Path(__file__))},indent=2))
    else:
        audit=Audit(args.pair)
        ready,status=audit.receipt_ready()
        if not ready:
            print(json.dumps({'pair_key':args.pair,**status,'raw_read':False,'audit_report_written':False}));raise SystemExit(3)
        audit.out.mkdir(parents=True,exist_ok=True)
        raise SystemExit(audit.run())
