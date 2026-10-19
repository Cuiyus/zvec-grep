"""Independent, local-only review of retained R3; never imports runner/collector."""
from pathlib import Path
import collections
import hashlib
import json
import math
import tarfile
import zipfile

BASE = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
CI = BASE / 'native-claude-offline-v1-r3-ci'
RAW = CI / 'raw'
ROOT = RAW / 'integration'
OUT = Path(__file__).parent
WT = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
SOURCE = BASE / 'live-v2-local-prepared/tasks/zvec-q001/environment/source'
FIELDS = ['input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens']
MODEL_FIELDS = ['inputTokens', 'cacheCreationInputTokens', 'cacheReadInputTokens', 'outputTokens']
checks = []
def sha(data): return hashlib.sha256(data).hexdigest()
def read(p): return json.loads(p.read_text())
def rows(p): return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def check(name, value, **detail): checks.append(dict(name=name, passed=bool(value), **detail))
def inventory(p): return [dict(path=x.relative_to(p).as_posix(), bytes=x.stat().st_size, sha256=sha(x.read_bytes())) for x in sorted(p.rglob('*')) if x.is_file()]
def text(value):
    if isinstance(value, str): return value
    if isinstance(value, list): return '\n'.join(text(v) for v in value)
    if isinstance(value, dict): return '\n'.join(text(v) for v in value.values())
    return ''

proof = read(ROOT / 'proof.json')
proof_sha = sha((ROOT / 'proof.json').read_bytes())
run = read(CI / 'run-metadata.json')
artifact = read(CI / 'artifact-metadata.json')
inputs = read(RAW / 'inputs.json')
check('run_identity', run.get('id') == 39335177244 and run.get('run_attempt') == 1 and run.get('head_sha') == '26d857e82f6967b44f76e82fc8b0d5c2d7262e90' and run.get('conclusion') == 'success')
zip_sha = sha((CI / 'artifact.zip').read_bytes())
check('artifact_zip_digest', artifact.get('digest') == 'sha256:' + zip_sha)
with zipfile.ZipFile(CI / 'artifact.zip') as archive:
    names = [x.filename for x in archive.infolist() if not x.is_dir()]
    safe = all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in names)
    actual = [r['path'] for r in inventory(RAW)]
    check('zip_exact_raw_file_set', safe and len(names) == len(set(names)) and sorted(names) == actual, files=len(names))
    check('zip_all_raw_bytes', safe and all((RAW / n).read_bytes() == archive.read(n) for n in names))
check('revision3_inputs', inputs.get('revision') == 3 and sha((RAW / 'inputs.json').read_bytes()) == sha((WT / 'benchmarks/native-claude-qa-pilot/inputs.json').read_bytes()))
check('frozen_repository_inputs', all(sha((WT / p).read_bytes()) == h for p,h in inputs['repository_files'].items()))
check('candidate_archive_hash', sha((RAW / 'zg-install.tgz').read_bytes()) == inputs['candidate_sha256'])
expected_inventory = [x for x in proof['evidence_files'] if x['path'] != 'proof.json']
actual_inventory = [x for x in inventory(ROOT) if x['path'] != 'proof.json']
check('all_proof_inventory_except_known_self_reference', expected_inventory == actual_inventory, files=len(actual_inventory))
self_inventory = [x for x in proof['evidence_files'] if x['path'] == 'proof.json']
check('only_proof_self_reference_exception', len(self_inventory) == 1)
check('proof_successful_pair', proof.get('valid') is True and proof.get('errors') == [] and proof.get('quality_evaluated') is False and [s['profile'] for s in proof['slots']] == ['baseline','zvec-grep'])
check('all_declared_checks_true', all(v.get('valid') is True and v.get('checks') and all(c.get('passed') is True for c in v['checks']) for v in proof['verification']))

source = {p.relative_to(SOURCE).as_posix(): dict(bytes=p.stat().st_size, sha256=sha(p.read_bytes())) for p in SOURCE.rglob('*') if p.is_file()}
check('frozen_source_count', len(source) == 1232)
question_path = WT / 'benchmarks/zvec-issue-qa/tasks/q001/instruction.md'
question = question_path.read_text()
check('unchanged_question', sha(question_path.read_bytes()) == '94aad1d075ef44984c2e6c330bca8d3dc6c273c1c7044c20b4bdd47c6c7854ef')
profiles = []
binary_ids, toolsets, rootids, volume_sets = [], [], [], []
with tarfile.open(RAW / 'zg-install.tgz', 'r:gz') as package:
    payloads = {m.name.removeprefix('package/'): dict(bytes=m.size, sha256=sha(package.extractfile(m).read())) for m in package.getmembers() if m.isfile() and m.name.removeprefix('package/').split('/')[0] in ('bin', 'lib', 'data')}
for profile in ['baseline','zvec-grep']:
    slot = next(s for s in proof['slots'] if s['profile'] == profile)
    trials = list((ROOT / 'runs' / ('q001-' + profile)).glob('*/agent'))
    check(profile + ':one_trial', len(trials) == 1)
    agent = trials[0]
    trial = read(agent.parent / 'result.json')
    audit = read(agent.parent / 'qa-source-audit.json')
    account = read(ROOT / (profile + '-accounting.json'))
    trace = read(ROOT / (profile + '-provider-trace.json'))
    chats = [r for r in trace['rows'] if r['kind'] == 'chat']
    check(profile + ':native_completed_cleanup', slot['status'] == 'exited' and slot['returncode'] == 0 and slot['process_cleanup']['complete'] is True and slot['container_cleanup']['complete'] is True and not trial.get('exception_info') and audit['native_run_completed'] is True)
    check(profile + ':all_fixture_requests', not trace.get('errors') and not trace.get('rejected_requests') and all(r.get('authorization_is_exact_fixture') is True and r['kind'] in ('chat','embedding') for r in trace['rows']))
    stages = ['root_read' if profile=='baseline' else 'root_mcp','root_child','child_read','child_final','root_final']
    check(profile + ':actual_chat_sequence', [r['stage'] for r in chats] == stages and [r['classification'] for r in chats] == ['root','root','child','child','root'])
    check(profile + ':actual_models_effort', all(r['body']['model'] == 'qwen3.8-max' for r in chats) and all(r['body'].get('output_config',{}).get('effort') == 'high' for r in chats if r['classification']=='root'))
    stream = rows(agent / 'claude-code.txt')
    result = [e for e in stream if e.get('type') == 'result']
    init = [e for e in stream if e.get('type')=='system' and e.get('subtype')=='init']
    check(profile + ':exact_root_terminal', len(result)==1 and result[0].get('subtype')=='success' and result[0].get('is_error') is False and result[0].get('terminal_reason')=='completed' and result[0].get('result')=='NATIVE_CLAUDE_V1_ROOT_COMPLETE')
    result=result[0];init=init[0];rootids.append(result['session_id'])
    check(profile + ':native_init_identity', init['session_id']==result['session_id'] and init['model']=='qwen3.8-max' and init['claude_code_version']=='2.1.212')
    toolsets.append(sorted(t for t in init['tools'] if not t.startswith('mcp__zvec_grep__')))
    native = list(agent.glob('sessions/projects/**/*.jsonl'))
    rootfiles = [p for p in native if 'subagents' not in p.parts]
    childfiles = [p for p in native if 'subagents' in p.parts]
    check(profile + ':actual_root_child_files', len(rootfiles)==1 and len(childfiles)==1)
    nr, nc = rows(rootfiles[0]), rows(childfiles[0])
    all_ass = [e for e in nr+nc if e.get('type')=='assistant']
    byid={e['message']['id']:e['message'] for e in all_ass}
    expected={r['message_id']:r['usage'] for r in chats}
    actual={mid:{f:m['usage'].get(f) for f in FIELDS} for mid,m in byid.items()}
    check(profile + ':five_unique_native_provider_messages', len(all_ass)==len(byid)==len(chats)==5 and actual==expected and all(m['model']=='qwen3.8-max' for m in byid.values()))
    totals={f:sum(m['usage'][f] for m in byid.values()) for f in FIELDS}
    check(profile + ':required_usage_numeric', all(type(m['usage'].get(f)) in (int,float) and math.isfinite(m['usage'][f]) and m['usage'][f]>=0 for m in byid.values() for f in FIELDS))
    check(profile + ':modelUsage_exact_full_tree', set(result['modelUsage'])=={'qwen3.8-max'} and all(result['modelUsage']['qwen3.8-max'][mf]==totals[f] for f,mf in zip(FIELDS,MODEL_FIELDS)))
    check(profile + ':accounting_exact_full_tree', account['complete'] is True and account['errors']==[] and account['child_count']==1 and account['usage']['llm_calls']==5 and account['usage']['input_tokens']==sum(totals[f] for f in FIELDS[:3]) and account['usage']['output_tokens']==totals['output_tokens'])
    parent = next(e for e in nr if isinstance(e.get('toolUseResult'),dict) and e['toolUseResult'].get('agentId'))
    childid=parent['toolUseResult']['agentId']
    check(profile + ':native_child_link', childfiles[0].name=='agent-'+childid+'.jsonl' and parent['toolUseResult']['status']=='completed' and all(e.get('agentId')==childid and e.get('sessionId')==result['session_id'] and e.get('isSidechain') is True for e in nc if e.get('type') in ('assistant','user')))
    child_final=next(e['message']['content'][0]['text'] for e in reversed(nc) if e.get('type')=='assistant')
    check(profile + ':child_final_returned', child_final=='NATIVE_CLAUDE_V1_CHILD_COMPLETE' and child_final in text(parent['toolUseResult']['content']))
    check(profile + ':exact_question_in_native_root', next(e['message']['content'] for e in nr if e.get('type')=='user') == question)
    uses=[b for e in all_ass for b in e['message']['content'] if b['type']=='tool_use']
    answers={b['tool_use_id']:b for e in nr+nc if e.get('type')=='user' and isinstance(e['message']['content'],list) for b in e['message']['content'] if b['type']=='tool_result'}
    check(profile + ':all_tools_terminal_success', len(uses)==3 and set(answers)=={t['id'] for t in uses} and all(not r.get('is_error') for r in answers.values()))
    readcalls=[t for t in uses if t['name']=='Read']
    check(profile + ':all_Read_paths_source_only', len(readcalls)==(2 if profile=='baseline' else 1) and all(t['input']=={'file_path':'/workspace/README.md','limit':12} for t in readcalls))
    check(profile + ':no_unexpected_tool', sorted(t['name'] for t in uses)==(['Agent','Read','Read'] if profile=='baseline' else ['Agent','Read','mcp__zvec_grep__zvec_grep_search']))
    trajectory=read(agent/'trajectory.json');final=[s for s in trajectory['steps'] if s.get('source')=='agent' and not s.get('tool_calls')][-1]['message']
    check(profile + ':atif_final_exact', final==result['result'])
    relevant=lambda p: p.name=='claude-code.txt' or p.suffix=='.jsonl' and 'projects' in p.parts
    original={str(p.relative_to(agent)):sha(p.read_bytes()) for p in agent.rglob('*') if p.is_file() and relevant(p)}
    snap=agent.parent/'qa-native-claude-logs';copied={str(p.relative_to(snap)):sha(p.read_bytes()) for p in snap.rglob('*') if p.is_file() and relevant(p)}
    check(profile + ':all_native_snapshot_bytes',original==copied)
    for phase in ['before','after']:
        observed=audit[phase]['observed'];files={r['path']:r for r in observed['files']}
        check(profile+':'+phase+':all1232source_bytes',all(p in files and all(files[p][k]==v[k] for k in ['bytes','sha256']) for p,v in source.items()))
        additions=set(files)-set(source)
        check(profile+':'+phase+':only_declared_additions',not additions if profile=='baseline' else all(p=='.zg-bench-index.ignore' or p.startswith('.zvec-grep/') for p in additions))
        binary_ids.append(observed['runtime']['claude']['binary_sha256'])
        check(profile+':'+phase+':actual_claude_version',observed['runtime']['claude']['version']=='2.1.212 (Claude Code)')
        if profile=='zvec-grep':
            installed=observed['runtime']['zg']['payloads']
            check(profile+':'+phase+':package_runtime_payloads',bool(payloads) and all(installed.get(p)==v for p,v in payloads.items()))
    volume_sets.append(set(slot['fresh_volumes']))
    if profile=='zvec-grep':
        mcp=next(t for t in uses if t['name'].startswith('mcp__'))
        check('treatment_correct_workspace_mcp',mcp['input'].get('root')=='/workspace')
        returned=text(answers[mcp['id']]['content'])
        match=next(v['mcp_source_match'] for v in proof['verification'] if v['profile']==profile)
        source_line=(SOURCE/match['path']).read_text().splitlines()[match['line']-1]
        check('treatment_real_frozen_source_snippet',bool(source_line.strip()) and source_line.strip() in returned and match['path'] in returned)
        guidance=audit['before']['observed']['configurations']['/logs/agent/sessions/CLAUDE.md']['content']
        start,end='<!-- ZVEC_GREP_START -->\n','<!-- ZVEC_GREP_END -->\n'
        check('installed_exact_boundary_rendering',guidance.startswith(start) and guidance.endswith(end) and text(chats[0]['body']).count(guidance[len(start):-len(end)])==1)
    profiles.append(dict(profile=profile,root_session_id=result['session_id'],child_agent_id=childid,message_ids=list(byid),usage=totals,input_tokens=sum(totals[f] for f in FIELDS[:3]),actual_tools=[dict(id=t['id'],name=t['name'],input=t['input']) for t in uses],provider_kind_counts=dict(collections.Counter(r['kind'] for r in trace['rows']))))
check('independent_same_claude_binary',len(set(binary_ids))==1)
check('independent_same_native_tools_except_zg',toolsets[0]==toolsets[1])
check('distinct_root_sessions_and_install_volumes',rootids[0]!=rootids[1] and not(volume_sets[0]&volume_sets[1]))
report=dict(schema_version=1,scope='Independent retained offline infrastructure review; not QA grading or live result',valid=all(c['passed'] for c in checks),run_id=39335177244,head='26d857e82f6967b44f76e82fc8b0d5c2d7262e90',proof_sha256=proof_sha,inputs_sha256=sha((RAW/'inputs.json').read_bytes()),artifact_sha256=zip_sha,artifact_id=artifact.get('id'),checks=checks,profiles=profiles,
    proof_self_inventory_exception=dict(path='proof.json',recorded_intermediate=self_inventory,final_sha256_bound_independently=proof_sha,exclusion='Only proof.json self-reference excluded; every other integration file matches the writer inventory and every raw file independently matches artifact ZIP bytes.'),
    limitations=['Scripted fake-provider transport/wiring proof, not QA quality, provider billing, or guaranteed actual-provider persisted usage shape.','QA source/read-only tool scope in this finite fixture verified; future live calls still require independent source/no-web audit.'],review_script_sha256=sha(Path(__file__).read_bytes()))
(OUT/'review.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(dict(valid=report['valid'],proof_sha256=proof_sha,checks=len(checks),failed=[c for c in checks if not c['passed']]),indent=2))
