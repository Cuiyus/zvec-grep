"""Audit collected immutable artifacts only; never dispatch or run transcript commands."""
import datetime,json,subprocess,time
from pathlib import Path,PurePosixPath
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot');ROOT=Path(__file__).resolve().parent;CI=B/'native-zvec-q001-repeat-v1-ci';PY=B/'upstream-main/benchmarks/swe-qa-bench/.venv/bin/python'
KEYS=[f'q001-r{i:02d}' for i in range(1,11)]

def load(p):return json.loads(p.read_text())
def write(p,x):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')

def command(argv,destination):
 result=subprocess.run([str(PY),'-B',*map(str,argv)],capture_output=True,text=True)
 write(destination,{'command':list(map(str,argv)),'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
 return result.returncode

start=time.monotonic()
while time.monotonic()-start<21600:
 for key in KEYS:
  raw=CI/key/'raw';receipt=CI/key/'artifact-byte-verification.json';record=ROOT/'automatic-attempts'/(key+'.json')
  if not receipt.is_file() or record.exists():continue
  attempt={'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pair_key':key,'execution_audit_returncode':None,'extractions':[],'source_scope_judgment':'pending_manual_review','quality_graded':False}
  try:
   proof=load(receipt);assert proof['complete'] and proof['pair_key']==key and proof['run_id']==39364155852
   attempt['execution_audit_returncode']=command([ROOT/'q001_execution_audit_v1.py','--pair',key,'--output-root',ROOT/'reviews'],ROOT/'automatic-attempts'/(key+'-execution-command.json'))
   collection=raw/'live/collection/collection.json'
   if collection.is_file():
    for profile in load(collection)['profiles']:
     relative=PurePosixPath(profile['trial_directory']);assert not relative.is_absolute() and '..' not in relative.parts
     agent=raw/'live'/str(relative)/'agent'
     if agent.is_dir():
      name=profile['profile'];assert name in ('baseline','zvec-grep')
      code=command([ROOT/'scope_extract_v2.py','--agent-dir',agent,'--output',ROOT/'scope'/key/name],ROOT/'automatic-attempts'/(key+'-'+name+'-extract-command.json'))
      attempt['extractions'].append({'profile':name,'returncode':code})
  except Exception as error:attempt['evidence_processing_error_type']=type(error).__name__
  write(record,attempt);print(json.dumps(attempt),flush=True)
 if (CI/'final-run-metadata.json').is_file():
  present=[k for k in KEYS if (CI/k/'artifact-byte-verification.json').is_file()]
  if all((ROOT/'automatic-attempts'/(k+'.json')).is_file() for k in present):
   print(json.dumps({'run_terminal':True,'artifacts_processed':len(present),'planned_pairs':10,'manual_source_review_and_original_grading_still_required':True}),flush=True);break
 time.sleep(30)
