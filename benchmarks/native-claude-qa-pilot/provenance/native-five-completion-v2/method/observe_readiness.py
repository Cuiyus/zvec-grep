"""Observe only setup/concurrency readiness; freeze once, never dispatch."""
from pathlib import Path
import datetime,json,subprocess,time
B=Path(__file__).resolve().parent.parent
PY=B/'upstream-main/benchmarks/swe-qa-bench/.venv/bin/python'
while True:
 result=subprocess.run([str(PY),str(Path(__file__).with_name('freeze_final_selection.py'))],capture_output=True,text=True)
 event={'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit_code':result.returncode,'output':result.stdout.strip(),'stderr':result.stderr.strip(),'paid_dispatch_performed':False}
 with (Path(__file__).parent/'readiness-observations.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
 print(json.dumps(event),flush=True)
 if result.returncode!=3:raise SystemExit(result.returncode)
 time.sleep(45)
