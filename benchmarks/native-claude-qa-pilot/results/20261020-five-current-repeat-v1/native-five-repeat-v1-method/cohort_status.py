"""Compact authoritative original-cohort state, no answer/quality reads."""
import collections,datetime,json,subprocess
endpoint='repos/Cuiyus/zvec-grep/actions/runs/39392884840/jobs?per_page=100'
jobs=json.loads(subprocess.run(['gh','api',endpoint],capture_output=True,text=True,check=True).stdout)['jobs']
rows=[j for j in jobs if j['name'].startswith('repeat (')]
pending=[]
for j in rows:
 setup=[s for s in j.get('steps',[]) if s['name']=='Run ./.github/actions/native-five-setup']
 if len(setup)!=1 or setup[0].get('conclusion') not in ('success','failure'):pending.append({'name':j['name'],'status':j['status'],'setup_status':setup[0].get('status') if setup else None})
print(json.dumps({'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'counts':dict(collections.Counter(j['status']+':'+str(j['conclusion']) for j in rows)),'pending_setups':pending,'all_planned_jobs':len(rows),'solver_or_dispatch_calls':0}))
