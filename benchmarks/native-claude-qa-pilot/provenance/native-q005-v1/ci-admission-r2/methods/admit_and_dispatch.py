"""Verify the current frozen q005 pair before one explicit fork dispatch."""
import argparse, datetime, hashlib, json, re, subprocess, sys, zipfile
from pathlib import Path
from types import SimpleNamespace

B = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
H = W / 'benchmarks/native-claude-qa-pilot'
OUT = B / 'native-q005-live-v1-ci'
HEAD = '30fe7fc4b80027cf62006bbe9d3de59445f10a16'
PREFLIGHT = 39383111550
REPO = 'Cuiyus/zvec-grep'
PLAN_SHA = 'd29aa89c2c541fcb7088833310b87ed712d910c43084678fa5d6f1880ded4027'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p): return json.loads(p.read_text())
def write(p, value):
    with p.open('x') as f: json.dump(value, f, indent=2); f.write('\n')
def api(endpoint):
    return json.loads(subprocess.run(['gh', 'api', endpoint], check=True, capture_output=True, text=True).stdout)

def admit():
    assert subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=W, check=True, capture_output=True, text=True).stdout.strip() == HEAD
    assert not subprocess.run(['git', 'status', '--porcelain'], cwd=W, check=True, capture_output=True, text=True).stdout
    assert sha(H / 'runtime-live-plan-v1.json') == PLAN_SHA
    sys.path.insert(0, str(H))
    import runtime_live_v1 as runtime
    plan, base, proof = runtime.validate_plan(SimpleNamespace(
        plan=H / 'runtime-live-plan-v1.json', main_root=B / 'upstream-main/benchmarks/swe-qa-bench',
        reference_prepared=B / 'native-claude-live-preflight-v1/prepared',
        candidate=B / 'repair-v2/revision-1-ci/raw/zg-install.tgz',
        prepared_group=B / 'native-runtime-q005-preparation-v1/prepared'))
    assert sha(B / 'native-q005-live-v1-grading-private/assignment.json') == plan['grading']['assignment_sha256']
    root=B / 'native-q005-live-v1-preflight-ci' / str(PREFLIGHT) / 'preflight'
    receipt=load(root / 'artifact-byte-verification.json'); meta=load(root / 'artifact-metadata.json')
    assert receipt['complete'] and receipt['exact_extracted_inventory'] and receipt['matches_api_digest']
    assert receipt['run_id'] == PREFLIGHT and receipt['head_sha'] == HEAD and receipt['run_attempt'] == 1 and receipt['job_conclusion'] == 'success'
    zp=root / 'artifact.zip'; assert sha(zp) == receipt['artifact_sha256'] and meta['digest'] == 'sha256:' + sha(zp)
    assert zp.stat().st_size == meta['size_in_bytes'] == receipt['artifact_bytes']
    raw=root / 'raw'
    with zipfile.ZipFile(zp) as z:
        assert sorted(n for n in z.namelist() if not n.endswith('/')) == sorted(r['path'] for r in receipt['files'])
        assert sorted(p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file()) == sorted(r['path'] for r in receipt['files'])
        for record in receipt['files']:
            p=raw / record['path']; assert not p.is_symlink() and raw.resolve() in p.resolve().parents
            assert sha(p) == record['sha256'] and p.stat().st_size == record['bytes'] and z.read(record['path']) == p.read_bytes()
    assert sha(raw / 'runtime-live-plan-v1.json') == PLAN_SHA
    assert sha(raw / 'zg-install.tgz') == 'fbe4bd201c8a0520f2373f1039e1d0805a38781c6e2f3ce11b83310d23dc6522'
    tests={'test_runtime_preparation_v1.py':4,'test_profile_assembly_v1.py':6,'test_profile_artifacts_v1.py':5,'test_runtime_live_v1.py':7}
    for name, count in tests.items():
        text=(raw / (name+'.log')).read_text()
        assert re.search(r'Ran '+str(count)+r' tests? in ', text) and text.rstrip().endswith('OK') and '\nFAILED' not in text
    replay=load(raw / 'collector-replay/verification.json')
    assert replay['production_assembly_used'] and replay['unchanged_collector_body'] and replay['all_checks_passed'] and all(replay['checks'].values())
    assert len(replay['checks']) == 18 and replay['solver_calls'] == 0 and replay['original_files_changed'] is False
    for profile in plan['profiles']:
        execution=load(raw / ('dry-'+profile) / 'live/execution.json')
        context=load(raw / ('dry-'+profile) / 'live-q005-worker-context.json')
        assert execution['dry_run'] and execution['plan_sha256'] == PLAN_SHA and len(execution['slots']) == 1
        slot=execution['slots'][0]; cmd=slot['command']; assert slot['profile'] == profile
        assert cmd[cmd.index('--n-attempts')+1] == '1' and cmd[cmd.index('--max-retries')+1] == '0'
        assert cmd[cmd.index('--model')+1] == 'qwen3.8-max' and cmd[cmd.index('--include-task-name')+1] == 'zvec-q005'
        assert context['dry_run'] and context['task_id'] == 'q005' and context['profile'] == profile and context['plan_sha256'] == PLAN_SHA
    prior=api(f'repos/{REPO}/actions/runs/39380248003')
    assert prior['status'] == 'completed' and prior['run_attempt'] == 1 and prior['head_sha'] == '3978d4bbbbb1e37961c277cdf76696b84c69e3bc'
    run=api(f'repos/{REPO}/actions/runs/{PREFLIGHT}')
    assert run['status']=='completed' and run['conclusion']=='success' and run['head_sha']==HEAD and run['run_attempt']==1 and run['event']=='push'
    dispatches=api(f'repos/{REPO}/actions/workflows/native-claude-q005-v1.yml/runs?event=workflow_dispatch&per_page=100')
    assert dispatches['total_count'] == 0
    ref=api(f'repos/{REPO}/git/ref/heads/dev/opencode-qa-repair-v2'); assert ref['object']['sha']==HEAD
    upstream=api('repos/zvec-ai/zvec-grep/commits/main')
    tree=api('repos/zvec-ai/zvec-grep/git/trees/'+upstream['sha'])
    rust=[r for r in tree['tree'] if r['path']=='rust']; assert len(rust)==1 and rust[0]['sha']=='de02030056a3224012725799d845bf2be9edae2b'
    OUT.mkdir(exist_ok=True)
    evidence={'q004-confirmation-terminal.json':prior,'preflight-run-current.json':run,'prior-q005-dispatches.json':dispatches,'remote-ref-before-dispatch.json':ref,'current-upstream-before-dispatch.json':upstream,'current-upstream-tree-before-dispatch.json':tree}
    for name, data in evidence.items(): write(OUT/name,data)
    admission={'schema_version':1,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'protocol_id':plan['protocol_id'],'fork_only':True,'head_sha':HEAD,'plan_sha256':PLAN_SHA,
        'preflight_run_id':PREFLIGHT,'preflight_receipt_sha256':sha(root/'artifact-byte-verification.json'),
        'preflight_artifact_zip_sha256':sha(zp),'preflight_files_reverified':len(receipt['files']),
        'linux_tests_passed':22,'actual_native_profile_previews':2,'production_replay_checks':18,
        'full_plan_source_and_contract_validation_passed':True,'current_upstream_sha':upstream['sha'],
        'current_upstream_rust_tree':rust[0]['sha'],'prior_q004_execution_terminal':True,
        'scheduled_solver_trials':2,'profiles':plan['profiles'],'mode':plan['mode'],
        'n_attempts_per_profile':1,'max_retries':0,'assignment_sha256':plan['grading']['assignment_sha256'],
        'private_mapping_displayed_to_parent':False,'solver_dispatches_before_this_intent':0,
        'evidence_sha256':{name:sha(OUT/name) for name in evidence}}
    write(OUT/'admission.json',admission)
    return admission

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute', action='store_true');args=parser.parse_args()
    if (OUT/'dispatch-intent.json').exists(): raise SystemExit('existing_dispatch_intent_do_not_redispatch')
    result=admit()
    if args.execute:
        argv=['gh','workflow','run','native-claude-q005-v1.yml','--repo',REPO,'--ref','dev/opencode-qa-repair-v2','-f','execute_pair=true']
        write(OUT/'dispatch-intent.json',{'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'argv':argv,'head_sha':HEAD,'admission_sha256':sha(OUT/'admission.json'),'dispatch_count':1})
        p=subprocess.run(argv, capture_output=True, text=True)
        write(OUT/'dispatch-response.json',{'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
        if p.returncode: raise SystemExit('dispatch_response_not_success_do_not_retry_inspect_remote_runs')
    print(json.dumps({'admitted':True,'dispatch_requested':args.execute,'scheduled_solver_trials':2,'head_sha':HEAD,'plan_sha256':PLAN_SHA}))
