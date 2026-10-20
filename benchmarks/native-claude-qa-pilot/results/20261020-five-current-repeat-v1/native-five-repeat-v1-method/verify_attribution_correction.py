"""Retain and verify the separate q002 literal citation-attribution correction."""
from pathlib import Path
import copy,hashlib,json
B=Path(__file__).resolve().parent.parent;G=B/'native-five-repeat-v1-grading';CHUNK='2fc8313d-e669-430b-baf3-dc2ecda3d431'
def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
root=G/'primary/q002/chunks'/CHUNK;correction_path=root/'citation-attribution-correction.json';c=load(correction_path);rp=root/'report.json';report=load(rp)
assert c['base_report']['sha256']==sha(rp) and c['score_or_success_changes'] is False
manifest_path=G/'chunks/q002'/CHUNK/'manifest.json';manifest=load(manifest_path);items={r['blinded_answer_id']:r for r in manifest['answers']};rows={r['blinded_answer_id']:r for r in report['records']}
source=Path(manifest['source_root']);checks=[]
for key,s in c['source_spans'].items():
 data=(source/s['path']).read_bytes();lines=data.splitlines(keepends=True);span=b''.join(lines[s['start_line']-1:s['end_line']])
 checks.append({'kind':'source_span','id':key,'passed':hashlib.sha256(data).hexdigest()==s['file_sha256'] and hashlib.sha256(span).hexdigest()==s['span_sha256'] and span==s['text'].encode()})
seen=set();numeric_keys=('criterion_points','criterion_score_bounds','score_exact','score_lower','score_upper','source_grounded_success')
for replacement in c['replacements']:
 identity,unit_id=replacement['blinded_answer_id'],replacement['unit_id'];assert (identity,unit_id) not in seen;seen.add((identity,unit_id))
 assert set(replacement)=={'blinded_answer_id','unit_id','replace_actual_citation_checks'}
 original=rows[identity];modified=copy.deepcopy(original);unit=next(u for u in modified['unit_audits'] if u['unit_id']==unit_id)
 unit['actual_citation_checks']=replacement['replace_actual_citation_checks']
 checks.append({'kind':'no_numeric_or_gate_change','answer':identity,'unit':unit_id,'passed':all(modified[k]==original[k] for k in numeric_keys)})
 data=(manifest_path.parent/items[identity]['file']).read_bytes()
 for check in replacement['replace_actual_citation_checks']:
  q=check['answer_quote'];ref=check['source_ref'];span=c['source_spans'][ref]
  checks.append({'kind':'literal_quote_and_cited_filename','answer':identity,'unit':unit_id,'source_ref':ref,'passed':data[q['utf8_start_byte']:q['utf8_end_byte_exclusive']]==q['exact_quote'].encode() and check['origin']=='answer_cited' and Path(span['path']).name in q['exact_quote']})
result={'schema_version':1,'base_report_sha256':sha(rp),'correction_sha256':sha(correction_path),'manifest_sha256':sha(manifest_path),'method_sha256':sha(Path(__file__)),'replacements':len(seen),'checks':checks,'all_mechanical_checks_passed':all(c['passed'] for c in checks),'score_or_success_changes':False,'condition_mapping_read':False,'scope':'Companion retained alongside original reports. Literal bytes/filenames/source spans and unchanged numeric fields checked; this does not assert an automated semantic proof of every citation claim.'}
out=G/'verification/primary/q002'/CHUNK/'attribution-correction-verification.json'
with out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
print(json.dumps({'replacements':len(seen),'checks':len(checks),'failed_checks':[c for c in checks if not c['passed']],'verification_sha256':sha(out)}))
