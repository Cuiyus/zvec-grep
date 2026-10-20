import argparse,hashlib,json,subprocess,sys
from pathlib import Path
b=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot');w=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');h=w/'benchmarks/native-claude-qa-pilot'
sys.path[:0]=[str(h),str(w/'benchmarks/opencode-qa-pilot'),str(b/'upstream-main/benchmarks/swe-qa-bench'),str(b/'native-current-tasks-repeat-v1-audit')]
import q001_repeat_v1 as q
from command_replay_v1 import expected_command
p=argparse.ArgumentParser();p.add_argument('root',type=Path);a=p.parse_args();r=a.root/'preflight/raw'
def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
outer=load(h/'q001-repeat-plan-v1.json');inner=load(h/'repeat-plan-v1.json')
assert load(a.root/'preflight/artifact-byte-verification.json')['complete'] is True
for file in ['q001-repeat-plan-v1.json','q001-repeat-decision-v1.json','repeat-plan-v1.json','repeat-decision-v1.json','live-plan-v2.json','inputs.json']:
 assert (r/file).read_bytes()==(h/file).read_bytes(),file
assert sha(r/'zg-install.tgz')=='fbe4bd201c8a0520f2373f1039e1d0805a38781c6e2f3ce11b83310d23dc6522'
assert sha(r/'q001-prepared.json')==inner['prepared']['metadata_sha256']['q001']
assert sha(r/'q001-source-manifest.json')==inner['prepared']['source_manifest_sha256']['q001']
assert next(x['sha'] for x in load(r/'current-upstream-tree.json')['tree'] if x['path']=='rust')==q.inner.RUST_TREE
actualmain=load(r/'current-upstream.json')['sha']
for index in (1,2):
 pair=q.inner.selected_pair('q001',index);d=r/('dry-'+pair['key']);execution=load(d/'execution.json');context=load(d.with_name(d.name+'-q001-repeat-context.json'))
 assert context=={'schema_version':1,'protocol_id':q.PROTOCOL,**pair,'cohort_plan_sha256':sha(h/'q001-repeat-plan-v1.json'),'inner_runtime_plan_sha256':q.INNER_SHA,'scheduled_pairs':10,'scheduled_solver_trials':20,'dry_run':True}
 assert execution['dry_run'] is True and execution['plan_sha256']==q.INNER_SHA
 assert [s['profile'] for s in execution['slots']]==pair['profiles']
 for slot in execution['slots']:
  cmd=slot['command'];assert slot['status']=='dry_run'
  output='/home/runner/work/_temp/native-claude-repeat-v1-preflight/dry-'+pair['key']
  assert cmd==expected_command(slot['profile'],b/'native-claude-live-preflight-v1/prepared','/home/runner/work/_temp/qa-prepared',output)
for pattern,count in [('test_repeat_live_v1.py',9),('test_q001_repeat_v1.py',8)]:
 text=(r/(pattern+'.log')).read_text();assert f'Ran {count} tests' in text and text.strip().endswith('OK')
result={'status':'pass','verified_head':'aa230d91eb426eb295cf95fe0fa2d80d52f588e2','plan_sha256':sha(h/'q001-repeat-plan-v1.json'),'upstream_main':actualmain,'rust_tree_sha':q.inner.RUST_TREE,'tests':17,'actual_dry_runs':2,'exact_command_replays':4,'scheduled_pairs':10,'solver_trials':20,'max_parallel_pairs':5,'raw_archive_verified':True,'no_paid_trials_in_this_preflight':True}
with (a.root/'verification.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
print(json.dumps(result))
