"""Seal all separately assigned supplemental comparisons before any unblinding."""
from pathlib import Path
import datetime,json
import verify_anonymous_grades as v
G=v.G;B=v.B
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');P=W/'benchmarks/native-claude-qa-pilot/five-completion-plan-v2.json'
plan=v.load(P);tasks=sorted({r['task_id'] for r in plan['pairs']});bindings=[];rows=[];pending=[]
for task in tasks:
 p=G/'comparison'/task/'comparison.json'
 if not p.is_file():pending.append(task);continue
 c=v.load(p);mp=G/task/'manifest.json';m=v.load(mp)
 expected=2*sum(r['task_id']==task for r in plan['pairs'])
 assert c['manifest_sha256']==v.sha(mp) and c['condition_mapping_read'] is False
 assert c['total_records']==m['planned_answers']==expected
 assert {r['blinded_answer_id'] for r in c['records']}=={r['blinded_answer_id'] for r in m['answers']}
 for role,ref in c['input_reports'].items():
  assert v.sha(Path(ref['path']))==ref['sha256']
  proof=G/'verification'/role/task/'report.json';assert v.load(proof)['evidence_bytes_and_score_arithmetic_valid'] and v.sha(proof)==ref['verification_sha256']
 bindings.append({'task_id':task,'path':str(p),'sha256':v.sha(p),'records':expected})
 rows.extend({'task_id':task,**r} for r in c['records'])
if pending:print(json.dumps({'ready':False,'pending_questions':pending,'condition_mapping_read':False}));raise SystemExit(0)
assert len(rows)==len({r['blinded_answer_id'] for r in rows})==plan['scheduled_supplemental_solver_trials']
result={'schema_version':1,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'supplement_run_id':39401348683,'supplement_head':'1bcd2b80df42de344fed9ff67e9f5c326fb19c24','supplement_plan_sha256':v.sha(P),'questions':bindings,'records':rows,'all_supplemental_planned_records':len(rows),'condition_mapping_read':False,'independent_comparisons_sealed_before_unblinding':True,'method_sha256':v.sha(Path(__file__))}
out=G/'comparison/final-supplement-comparison.json'
if out.exists():assert v.load(out)['questions']==bindings
else:v.write(out,result)
print(json.dumps({'ready':True,'records':len(rows),'master_comparison_sha256':v.sha(out),'condition_mapping_read':False}))
