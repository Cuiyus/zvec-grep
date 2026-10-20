"""Independent count/reference check of completed supplemental scope reports only; no answer access."""
from pathlib import Path
import json,hashlib,re,datetime
D=Path(__file__).parent;M=D.parent
F=json.loads((D/'frozen-inputs.json').read_text())
def ref(p):
 b=p.read_bytes();return {'path':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
checks=[]
for pair in F['schedule']:
 O=M/'pairs'/pair['key'];rp=O/'report.json'
 if not rp.is_file():continue
 r=json.loads(rp.read_text());assert r['run_id']==39401348683 and r['head_sha']==F['head_sha'] and r['original_primary_rows_modified']is False and r['completion_plan']==F['completion_plan']
 hits=0;tools=0;rows=[]
 for pr in pair['profiles']:
  a=json.loads((O/pr/'extracted/tool-audit.json').read_text());p=next(p for p in r['profiles']if p['profile']==pr);assert p['tool_count']==len(a['tools']);phits=0;calls=[]
  for t in a['tools']:
   if t['name']!='mcp__zvec_grep__zvec_grep_search':continue
   assert len(t['call_variants'])==len(t['result_variants'])==1
   content=t['result_variants'][0]['content'];texts=[content]if isinstance(content,str)else[b['text']for b in content if b.get('type')=='text'];raw_hits=sum(len(re.findall(r'^#\d+\s',s,re.M))for s in texts)
   q=json.loads((O/f"mcp-retrieval-review-{t['ordinal']:04d}.json").read_text());assert raw_hits==q['hit_count']==len(q['hits'])
   phits+=raw_hits;calls.append({'ordinal':t['ordinal'],'tool_evidence':ref(O/pr/'extracted'/t['evidence_path']),'direct_raw_header_hit_count':raw_hits,'review':ref(O/f"mcp-retrieval-review-{t['ordinal']:04d}.json")})
  assert phits==p['mcp_hit_count'];hits+=phits;tools+=len(a['tools']);rows.append({'profile':pr,'tool_count':len(a['tools']),'mcp_hit_count':phits,'calls':calls})
 assert r['tool_count']==tools and r['mcp_hit_count']==hits
 checks.append({'pair_key':pair['key'],'report':ref(rp),'profiles':rows,'tool_count':tools,'mcp_hit_count':hits,'all_match':True})
p=M/'snapshots'/('mcp-count-crosscheck-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
x={'run_id':39401348683,'original_primary_rows_modified':False,'completed_pairs_checked':len(checks),'planned_pairs':5,'not_yet_reviewed_keys':[p['key']for p in F['schedule']if p['key']not in {c['pair_key']for c in checks}],'observed_tool_calls':sum(c['tool_count']for c in checks),'observed_mcp_hits':sum(c['mcp_hit_count']for c in checks),'checks':checks,'all_match':True,'checker':ref(Path(__file__)),'original_and_completion_cohorts_pooled':False,'quality_or_resource_assessment':False}
with p.open('x')as f:json.dump(x,f,indent=2)
print(json.dumps({'path':str(p),'sha256':ref(p)['sha256'],'pairs':len(checks),'tools':x['observed_tool_calls'],'hits':x['observed_mcp_hits']}))
