"""Build each supplemental anonymous question packet once when all its byte receipts exist."""
from pathlib import Path
import datetime,json,subprocess,time
B=Path(__file__).resolve().parent.parent;METHOD=Path(__file__).resolve().parent;PY=B/'upstream-main/benchmarks/swe-qa-bench/.venv/bin/python'
H=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep/benchmarks/native-claude-qa-pilot')
plan=json.loads((H/'five-completion-plan-v2.json').read_text());tasks=sorted({p['task_id'] for p in plan['pairs']})
RUN=39401348683;HEAD='1bcd2b80df42de344fed9ff67e9f5c326fb19c24';CI=B/'native-five-completion-v2-ci'/str(RUN);G=B/'native-five-completion-v2-grading'
while True:
 for task in tasks:
  if (G/task/'manifest.json').is_file():continue
  keys=[p['key'] for p in plan['pairs'] if p['task_id']==task]
  if not all((CI/k/'artifact-byte-verification.json').is_file() for k in keys):continue
  r=subprocess.run([str(PY),str(METHOD/'build_blind_packets.py'),'--run-id',str(RUN),'--head',HEAD,'--task',task],capture_output=True,text=True)
  event={'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'returncode':r.returncode,'output':r.stdout.strip(),'stderr':r.stderr.strip(),'parent_mapping_read':False,'dispatch_or_retry':False}
  with (METHOD/'packet-observer-events.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
  print(json.dumps(event),flush=True)
  if r.returncode:raise SystemExit(r.returncode)
 if all((G/t/'manifest.json').is_file() for t in tasks):break
 time.sleep(45)
