"""Incrementally audit retained records of the sole original run, no new trials."""
import datetime,importlib.util,json,time,traceback
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_fixed_five_execution_audit',HERE/'execution_audit.py')
method=importlib.util.module_from_spec(spec);spec.loader.exec_module(method)
method.OUT.mkdir(parents=True,exist_ok=True)
with (method.OUT/'observer-events.jsonl').open('x') as log:
 inputs=method.prepare()
 while True:
  pending=[p for p in method.five.schedule() if (method.CI/p['key']/'artifact-byte-verification.json').is_file() and not (method.OUT/p['key']/'report.json').is_file()]
  for pair in pending:
   record={'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pair':pair['key'],'solver_calls':0}
   try:record.update(method.audit(pair,inputs))
   except Exception as error:record.update(error=type(error).__name__,detail=str(error));traceback.print_exc()
   log.write(json.dumps(record)+'\n');log.flush();print(json.dumps(record),flush=True)
  completed=sum((method.OUT/p['key']/'report.json').is_file() for p in method.five.schedule())
  if completed==50:break
  time.sleep(45)
