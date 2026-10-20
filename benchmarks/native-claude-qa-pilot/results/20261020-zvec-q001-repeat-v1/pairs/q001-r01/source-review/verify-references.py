from pathlib import Path
import json,hashlib
b=Path(__file__).parent;pending=[b/'report.json'];seen=set();refs={};errors=[]
while pending:
 p=pending.pop()
 if p in seen:continue
 seen.add(p)
 try:d=json.loads(p.read_text())
 except (ValueError,UnicodeError):continue
 def walk(x):
  if isinstance(x,dict):
   if isinstance(x.get('path'),str) and x['path'].startswith('/') and 'sha256' in x and 'bytes' in x:
    p=Path(x['path']);s=hashlib.sha256(p.read_bytes()).hexdigest();ok=s==x['sha256'] and p.stat().st_size==x['bytes'];refs[str(p)]={'sha256':s,'bytes':p.stat().st_size,'matches':ok}
    if not ok:errors.append(str(p))
    if p.suffix=='.json' and b in p.parents:pending.append(p)
   for v in x.values():walk(v)
  elif isinstance(x,list):
   for v in x:walk(v)
 walk(d)
p=b/'reference-verification.json';d={'report_sha256':hashlib.sha256((b/'report.json').read_bytes()).hexdigest(),'checked_distinct_file_references':len(refs),'all_match':not errors,'errors':errors,'references':refs}
with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
print(json.dumps({'refs':len(refs),'all_match':not errors,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))
