"""Observe the single accepted supplemental run; never redispatch or retry agents."""
from pathlib import Path
import datetime,json,subprocess,time
B=Path(__file__).resolve().parent.parent;PY=B/'upstream-main/benchmarks/swe-qa-bench/.venv/bin/python';METHOD=Path(__file__).resolve().parent
RUN=39401348683;HEAD='1bcd2b80df42de344fed9ff67e9f5c326fb19c24';ROOT=B/'native-five-completion-v2-ci'/str(RUN)
while True:
 result=subprocess.run([str(PY),str(METHOD/'collect_completion.py'),'--run-id',str(RUN),'--head',HEAD,'--root',str(ROOT)],capture_output=True,text=True)
 event={'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'run_id':RUN,'returncode':result.returncode,'output':result.stdout.strip(),'stderr':result.stderr.strip(),'dispatch_or_retry':False}
 if result.returncode==0 and (ROOT/'preflight/artifact-byte-verification.json').is_file():
  audit=subprocess.run([str(PY),str(METHOD/'execution_audit.py'),'--run-id',str(RUN),'--head',HEAD],capture_output=True,text=True)
  event['audit_returncode']=audit.returncode;event['audit_stdout']=audit.stdout.strip();event['audit_stderr']=audit.stderr.strip()
 with (ROOT.parent/'host-observer-events.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
 print(json.dumps(event),flush=True)
 if result.returncode==0:
  states=sorted(ROOT.glob('status-*.json'));state=json.loads(states[-1].read_text())
  if state['run_status']=='completed' and state['retained_count']==state['planned_pairs']:break
 time.sleep(45)
