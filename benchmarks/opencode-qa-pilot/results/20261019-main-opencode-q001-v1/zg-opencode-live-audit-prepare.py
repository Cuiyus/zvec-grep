#!/usr/bin/env python3
"""Offline evidence checks only. Run with exact-main .venv/bin/python after artifact receipt.
No network, subprocess model execution, grading, or outcome inference. Output must be new.
GitHub inputs must be original REST API run/artifact metadata, not a success summary.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys, zipfile
from pathlib import Path

sys.dont_write_bytecode = True
RUN = 39308807224
HEAD = '5ccf538037a23c2fa17c97fc8d00fe657b65ba85'
PLAN_SHA = 'ce13c13785117c49c515a79cfbba13ad70eaf7113dbcb3d6e91b29ec3a8dcc43'
REPO = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
MAIN = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot/upstream-main/benchmarks/swe-qa-bench')
PROFILES = ['baseline', 'zvec-grep']


def sha_bytes(data): return hashlib.sha256(data).hexdigest()
def sha(path): return sha_bytes(path.read_bytes())
def load(path): return json.loads(path.read_text())
def canon(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
def require(value, message):
    if not value: raise ValueError(message)
def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'symlink in artifact')
        if path.is_file(): result[path.relative_to(root).as_posix()] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    return result

def rows(snapshot, kind):
    return [json.loads(r['data']) if isinstance(r['data'], str) else r['data'] for r in snapshot if r['kind'] == kind]

def zip_check(path, artifact, root):
    require(artifact['workflow_run']['id'] == RUN, 'artifact belongs to another run')
    require(artifact['workflow_run']['head_sha'] == HEAD, 'artifact head differs')
    require(artifact['name'] == f'main-opencode-q001-{RUN}', 'artifact name differs')
    require(artifact.get('digest') == 'sha256:' + sha(path), 'GitHub ZIP digest differs/missing')
    packed = {}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            if member.is_dir(): continue
            p = Path(member.filename)
            require(not p.is_absolute() and '..' not in p.parts, 'unsafe ZIP entry')
            require(member.filename not in packed, 'duplicate ZIP entry')
            data = archive.read(member)
            packed[member.filename] = {'bytes': len(data), 'sha256': sha_bytes(data)}
    require(packed == inventory(root), 'ZIP decoded bytes/inventory differ from raw extraction')
    return {'zip_sha256': sha(path), 'artifact_id': artifact['id'], 'decoded_files_verified': len(packed)}

def tool_index(exports):
    tools, flags = [], []
    for sid, export in exports.items():
        for mi, message in enumerate(export['messages']):
            for pi, part in enumerate(message.get('parts', [])):
                if part.get('type') != 'tool': continue
                state = part.get('state', {})
                arguments, output = state.get('input', {}), state.get('output')
                name = part.get('tool', '')
                text = json.dumps({'input': arguments, 'output': output}, ensure_ascii=False).lower()
                indicators = [x for x in ('https://', 'http://', 'curl ', 'wget ', 'git clone', 'evaluator/',
                    'reference-answer', 'pair-verdict', 'experiment.json', 'source-manifest.json') if x in text]
                ref = f'qa-session-exports/{sid}.json#/messages/{mi}/parts/{pi}'
                if indicators: flags.append({'reference': ref, 'indicators': indicators})
                tools.append({'session_id': sid, 'message_id': message.get('info', {}).get('id'),
                    'part_id': part.get('id'), 'call_id': part.get('callID'), 'tool': name,
                    'status': state.get('status'), 'reference': ref,
                    'input_sha256': sha_bytes(canon(arguments)), 'output_sha256': sha_bytes(canon(output)),
                    'zg_name_candidate': any(s in name.lower() for s in ('zvec', 'zgrep', 'zg_'))})
    return tools, flags


def audit_arm(profile, run_root, reports_root, plan, pilot):
    from zg_bench.agents.opencode_usage import summarize_session_usage
    from zg_bench.metrics.usage import validate_session_usage
    from zg_bench.swe_qa.collect import _profile_result, _completed_job
    job = run_root / 'runs' / ('q001-' + profile)
    trial_dirs = sorted(p for p in job.iterdir() if p.is_dir())
    trials = [p for p in trial_dirs if (p / 'result.json').is_file()]
    require(len(trials) == 1 and len(trial_dirs) == 1, 'expected exactly one retained trial directory, including partials')
    trial = trials[0]
    _completed_job(job, expected_trials=1)
    metrics = _profile_result(profile=profile, job_dir=job, trial_dir=trial, result=load(trial/'result.json'), trial_index=1)
    metrics.pop('answer', None)  # never emit semantic answer text
    audit = load(trial / 'qa-source-audit.json')
    require(audit['profile'] == profile and audit['valid'] is True, 'source audit missing/invalid')
    source_hashes = []
    for phase in ('before', 'after'):
        observed = audit[phase]['observed']
        require(audit[phase]['valid'] is True and not audit[phase]['errors'], 'source snapshot failed')
        source = []
        for row in observed['files']:
            added = row['path'].startswith('.zvec-grep/') or row['path'] == '.zg-bench-index.ignore'
            if added:
                require(profile == 'zvec-grep' and row['kind'] == 'file', 'unapproved addition/link')
            else:
                require(row['kind'] == 'file', 'source symlink/non-file')
                source.append({k: row[k] for k in ('path','bytes','sha256')})
        source.sort(key=lambda r:r['path'])
        require(len(source) == plan['source_file_count'], 'source file count differs')
        source_hashes.append(sha_bytes(canon(source)))
        require(source_hashes[-1] == plan['source_inventory_sha256'], 'source inventory differs')
        if profile == 'baseline':
            require(all(observed[k] is False for k in ('zg_executable_present','zg_home_present','mcp_config_present')), 'baseline contaminated')
            require(observed.get('embedding_environment_names') == [], 'baseline embedding environment evidence missing/present')
    before, runtime = audit['before']['observed']['runtime'], audit['after']['observed']['runtime']
    require(before['opencode'] == runtime['opencode'] and runtime['opencode']['version'] == plan['agent']['version'], 'OpenCode identity drift')
    host = load(reports_root / 'opencode-runtime.json')
    require(runtime['opencode']['binary_sha256'] == host['binary_sha256'] and host['version'] == plan['agent']['version'], 'host mock and live OpenCode executable differ')
    if profile == 'zvec-grep':
        require(before['zg'] == runtime['zg'], 'zg identity drift')
        require(runtime['zg']['binary_sha256'] == plan['candidate']['binary_sha256'], 'wrong Rust binary')
    snapshot = load(trial/'agent/opencode-session-snapshot.json')
    usage = validate_session_usage(load(trial/'agent/session-usage.json'), require_identity=True)
    recomputed = summarize_session_usage(snapshot, usage['root_session_id'], capture_complete=True)
    validate_session_usage(recomputed, require_identity=True)
    for key in ('root_session_id','sessions','root','descendants','total'):
        require(recomputed[key] == usage[key], 'stored usage differs from DB rows: ' + key)
    session_ids = {s['session_id'] for s in usage['sessions']}
    require(session_ids == {s['id'] for s in rows(snapshot,'session')}, 'fresh DB has outside/unaccounted session')
    for m in rows(snapshot,'message'):
        require(m['session_id'] in session_ids, 'uncounted assistant message')
        require((m['provider_id'],m['model_id']) == ('custom-openai','qwen3.8-max'), 'wrong provider/model')
    require(audit['session_exports_complete'] is True and set(audit['exported_sessions']) == session_ids, 'missing recursive export')
    exports = {sid:load(trial/'qa-session-exports'/(sid+'.json')) for sid in session_ids}
    require({p.stem for p in (trial/'qa-session-exports').glob('*.json')} == session_ids, 'export file set differs')
    for entry in usage['sessions']:
        e=exports[entry['session_id']]
        require(e['info']['id']==entry['session_id'] and e['info'].get('parentID')==entry['parent_session_id'], 'session parent/identity mismatch')
        mids={m['info']['id'] for m in e['messages'] if m['info']['role']=='assistant'}
        tids={p['id'] for m in e['messages'] for p in m.get('parts',[]) if p.get('type')=='tool'}
        require(mids==set(entry['message_ids']) and tids==set(entry['tool_ids']), 'export message/tool IDs incomplete')
    final = pilot.final_answer(trial/'agent/opencode.txt', load(trial/'agent/trajectory.json'), usage, snapshot, exports[usage['root_session_id']])
    expected_prompt=(REPO/'benchmarks/zvec-issue-qa/tasks/q001/instruction.md').read_text().strip()
    users=[m for m in exports[usage['root_session_id']]['messages'] if m['info']['role']=='user']
    require(users, 'missing root user prompt')
    prompt='\n'.join(p.get('text','') for p in users[0].get('parts',[]) if p.get('type')=='text').strip()
    require(prompt==expected_prompt, 'root user instruction differs')
    containers=load(run_root/(profile+'-containers.json'))
    require(containers, 'container/mount evidence absent')
    for c in containers.values():
        original_trial=Path(c['trial_dir'])
        for m in c['mounts']:
            destination=m.get('Destination',''); source=Path(m.get('Source','/'))
            if destination=='/root/.nvm':
                require(m.get('Type')=='volume' and ('-'+profile+'-') in m.get('Name',''), 'wrong install-cache mount')
            elif destination=='/tmp/zg-bench-zvec-grep.tgz':
                require(profile=='zvec-grep' and m.get('RW') is False, 'wrong candidate mount')
                require(source.name=='zvec-grep-'+plan['candidate_sha256'][:16]+'.tgz', 'candidate mount cache identity differs')
            elif destination.startswith('/logs/'):
                require(source==original_trial or original_trial in source.parents, 'logs mount outside recorded CI trial')
            else: raise ValueError('unapproved mount destination: '+destination)
    # Never dereference /home/runner bind sources on the audit workstation.
    tools,flags=tool_index(exports)
    require(len(tools)==usage['total']['tool_calls'], 'tool count differs from exports')
    require(all(t['status'] in ('completed','error') for t in tools), 'unfinished tools')
    if profile=='baseline': require(not any(t['zg_name_candidate'] for t in tools), 'baseline used zg-named tool')
    setup=load(trial/'agent/zvec-grep-setup.json') if profile=='zvec-grep' else None
    if setup:
        require(setup['status']=='ready' and setup['workdir']=='/workspace', 'zg setup not ready/correct root')
        require(setup['embedding_model']==plan['embedding']['model'] and setup['embedding_endpoint']==plan['embedding']['endpoint'], 'embedding identity differs')
        require(setup['package_sha256']==plan['candidate_sha256'] and not setup.get('index_seed_available'), 'wrong package or seed reuse')
    return {'profile':profile,'automated_checks_passed':True,'trial':str(trial),'source_inventory_sha256':source_hashes,
        'runtime':runtime,'usage':usage,'metrics':metrics,'root_final_sha256':final['answer_sha256'],
        'root_session_id':usage['root_session_id'],'tools':tools,'tool_content_review_flags':flags,
        'setup':setup,'source_audit_wall_seconds':audit['audit_wall_seconds'],'session_export_wall_seconds':audit['export_wall_seconds'],
        'protocol_valid':None,'quality_evaluated':False,'correct_root_relevant_successful_zg_retrieval':None}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('artifact-dir','run-metadata','artifact-metadata','zip','job-metadata','output'):
        ap.add_argument('--'+name,type=Path,required=True)
    a=ap.parse_args()
    require(not a.output.exists(),'output exists; refusing overwrite')
    root=a.artifact_dir.resolve(); reports=root/'qa-reports'; run=root/'qa-run'
    require(reports.is_dir() and run.is_dir(),'expected qa-reports/ and qa-run/ extracted roots')
    plan_path=reports/'experiment.json'
    require(sha(plan_path)==PLAN_SHA,'artifact frozen plan hash differs')
    plan=load(plan_path)
    require(plan['frozen'] is True,'plan not frozen')
    for rel,digest in plan['frozen_harness_sha256'].items(): require(sha(REPO/rel)==digest,'local audited code drift: '+rel)
    sys.path[:0]=[str(MAIN),str(REPO/'benchmarks/opencode-qa-pilot')]
    import pilot
    pilot.validate_plan(plan_path,REPO,MAIN)
    rm=load(a.run_metadata)
    require(rm['id']==RUN and rm['head_sha']==HEAD and rm['repository']['full_name']=='Cuiyus/zvec-grep','run provenance differs')
    require(rm['event']=='workflow_dispatch' and rm['run_attempt']==1 and rm['status']=='completed','wrong event/attempt or run incomplete')
    artifacts=load(a.artifact_metadata)
    matches=[x for x in artifacts['artifacts'] if x['name']==f'main-opencode-q001-{RUN}']
    require(len(matches)==1,'missing/ambiguous artifact')
    zip_evidence=zip_check(a.zip,matches[0],root)
    jobs=load(a.job_metadata)['jobs']
    require(len(jobs)==1 and jobs[0]['run_id']==RUN and jobs[0]['head_sha']==HEAD,'job provenance differs')
    scans=[s for s in jobs[0]['steps'] if s['name']=='Reject secret leakage before artifact publication']
    require(len(scans)==1 and scans[0]['conclusion']=='success','missing successful configured-secret scan receipt')
    execution=load(run/'execution.json')
    require(execution['plan_sha256']==PLAN_SHA and execution['dry_run'] is False,'execution identity differs')
    require([s['profile'] for s in execution['slots']]==PROFILES,'missing/extra/reordered slots')
    results=[]
    for profile,slot in zip(PROFILES,execution['slots']):
        try:
            require(slot['status']=='exited' and slot['returncode']==0 and slot['container_cleanup']['complete'] is True,'slot failed/incomplete or cleanup uncertain')
            results.append(audit_arm(profile,run,reports,plan,pilot))
        except Exception as e:
            results.append({'profile':profile,'automated_checks_passed':False,'error':type(e).__name__+': '+str(e),'slot':slot,'protocol_valid':None,'quality_evaluated':False})
    if all(r['automated_checks_passed'] for r in results):
        require(results[0]['root_session_id']!=results[1]['root_session_id'],'same root session reused')
        require(execution['slots'][0]['finished_at_unix']<=execution['slots'][1]['started_at_unix'],'slots overlap/order differs')
    report={'schema_version':1,'run_id':RUN,'head':HEAD,'plan_sha256':PLAN_SHA,'zip':zip_evidence,
        'input_files':{str(p):sha(p) for p in (a.run_metadata,a.artifact_metadata,a.job_metadata,a.zip,plan_path)},
        'raw_inventory':inventory(root),'execution':execution,'profiles':results,'protocol_valid':None,'quality_evaluated':False,
        'status':'automated_checks_finished_manual_audit_required',
        'required_manual_checks':['Review every root/child tool input AND output for source-only/no-web compliance; heuristic flags are neither exhaustive nor verdicts.',
            'Check root user/source/tool records for evaluator/reference/plan/old-answer exposure; host trial config/manifest paths are expected host-only metadata.',
            'For each zg-named call inspect raw response isError/error status, actual roots and returned source locations; require at least one /workspace successful relevant retrieval, never setup/preflight alone.',
            'Inspect treatment setup grant/index/MCP readiness, fresh remote index, container mounts and private config evidence; no grant secret values are read.',
            'Confirm frozen native command/config and all job gate receipts; provider request contract is local-mock evidence, live HTTP body is not retained.',
            'Reconstruct raw Harbor wall and measured usage/source/export overhead; report setup/cold separately. No quality, efficiency or whole-provider savings conclusion here.'],
        'limitations':['Source before/after inventories are controller observations, not independently retained post-run source bytes or OS read-only enforcement.',
            'CI bind-source filesystem paths cannot be rehashed locally; their recorded mount identity is checked and candidate bytes are tied through frozen package and actual runtime binary identities.',
            'Persisted root/recursive child usage excludes absent background/provider requests and embedding usage; zero custom-provider cost means unknown.',
            'A successful configured-value secret scan is not proof against unknown/encoded secrets. Final answer is compared only as opaque text/hash, never graded.']}
    with a.output.open('x') as f: json.dump(report,f,indent=2);f.write('\n')
    print(json.dumps({'report':str(a.output),'sha256':sha(a.output),'profiles_checked':len(results),'protocol_valid':None}))

if __name__=='__main__': main()
