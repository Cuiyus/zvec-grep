"""Observe the single admitted run; never dispatch or restart a worker."""
import datetime, importlib.util, json, time, traceback
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_fixed_five_retention',HERE/'collect_cohort.py')
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)
root=HERE.parent/'native-five-repeat-v1-ci/39392884840'
log=root/'host-observer-events.jsonl'
with log.open('x') as f:
    while True:
        record={'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'run_id':39392884840,'dispatch_or_restart':False}
        try:
            collector.collect(39392884840,'df0d37796435a596b4f6d62215a8fe28f401774a',root)
            latest=max(root.glob('status-*.json'))
            state=json.loads(latest.read_text());record.update(run_status=state['run_status'],retained_pairs=state['retained_count'],new_artifacts=state['new_artifacts'])
        except Exception as error:
            record.update(observation_error=type(error).__name__,detail=str(error));traceback.print_exc()
        f.write(json.dumps(record)+'\n');f.flush()
        print(json.dumps(record),flush=True)
        if record.get('run_status')=='completed':break
        time.sleep(45)
