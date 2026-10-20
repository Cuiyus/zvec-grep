"""Observe only the existing run; never dispatch/restart solver executions."""
import datetime,json,subprocess,sys,time
from pathlib import Path
root=Path(__file__).resolve().parent
previous=None
started=time.monotonic()
while time.monotonic()-started<21600:
 result=subprocess.run([sys.executable,str(root/'collect.py'),'--run-id','39364155852','--head','aa230d91eb426eb295cf95fe0fa2d80d52f588e2'],capture_output=True,text=True)
 now=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 with (root/('observer-'+now+'.json')).open('x') as f:
  json.dump({'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr},f,indent=2);f.write('\n')
 if result.returncode==0:
  state=json.loads(result.stdout)
  signature=(state['run_status'],state['run_conclusion'],state['retained_pairs'])
  if signature!=previous or state['new_artifacts']:
   print(json.dumps({'observed_at_utc':now,**state}),flush=True);previous=signature
  if state['run_status']=='completed':
   raise SystemExit(0 if state['run_conclusion']=='success' and state['retained_pairs']==10 else 1)
 else:
  print(json.dumps({'observed_at_utc':now,'observation_error':True,'same_run_id':39364155852,'process_not_restarted':True}),flush=True)
 time.sleep(60)
print(json.dumps({'observation_window_elapsed':True,'run_not_cancelled_or_restarted':True}),flush=True)
