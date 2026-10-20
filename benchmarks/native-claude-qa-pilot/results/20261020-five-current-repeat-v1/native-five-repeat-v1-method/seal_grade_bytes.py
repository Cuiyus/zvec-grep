"""Seal anonymous review and correction bytes without displaying judgments."""
from pathlib import Path
import datetime,hashlib,json
B=Path(__file__).resolve().parent.parent
G=B/'native-five-repeat-v1-grading'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 out=G/'seals';out.mkdir(exist_ok=True);added=[]
 for manifest in sorted((G/'chunks').glob('*/*/manifest.json')):
  task,chunk=manifest.parent.parent.name,manifest.parent.name
  reports={r:G/r/task/'chunks'/chunk/'report.json' for r in ('primary','secondary')}
  if not all(p.is_file() for p in reports.values()):continue
  try:
   for p in reports.values():json.loads(p.read_text())
  except json.JSONDecodeError:continue
  files=[p for report in reports.values() for p in sorted(report.parent.glob('*.json'))]
  records=[{'path':str(p),'sha256':sha(p),'bytes':p.stat().st_size} for p in files]
  payload={'schema_version':2,'task_id':task,'chunk':chunk,'manifest_sha256':sha(manifest),'reports_and_ancillary_corrections':records,'sealed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'read_for_hashes_only':True,'parent_read_report_contents':False,'parent_read_condition_mapping':False}
  target=out/(task+'-'+chunk+'-byte-seal-v2.json')
  if target.exists():
   old=json.loads(target.read_text());assert old['manifest_sha256']==payload['manifest_sha256']
   for record in old['reports_and_ancillary_corrections']:assert sha(Path(record['path']))==record['sha256']
   continue
  with target.open('x') as f:json.dump(payload,f,indent=2);f.write('\n')
  added.append({'task':task,'chunk':chunk,'seal_sha256':sha(target)})
 print(json.dumps({'new_report_byte_seals':added,'report_contents_printed':False,'condition_mapping_read':False}))
if __name__=='__main__':main()
