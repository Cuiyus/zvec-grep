from pathlib import Path
import json,hashlib,datetime
M=Path(__file__).resolve().parent.parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
rows=[]
for O in sorted((M/'pairs').iterdir()):
 rp=O/('report-v2.json'if O.name=='q003-r04'else'report.json')
 if not rp.is_file():continue
 r=json.loads(rp.read_text())
 if r['status']!='review_complete':continue
 calls=[];profile_counts={}
 for pr in ['baseline','zvec-grep']:
  a=json.loads((O/pr/'extracted/tool-audit.json').read_text());actual=0
  for t in a['tools']:
   if t['name']!='mcp__zvec_grep__zvec_grep_search':continue
   n=t['ordinal'];raw=O/pr/f'tool-check-{n:04d}.json';tb=json.loads(raw.read_text())['mechanical_check']['text_blocks'];cnt=sum(len(b['hits'])for b in tb);review=O/f'mcp-retrieval-review-{n:04d}.json'
   if O.name=='q003-r04'and n==18:review=O/'mcp-retrieval-review-0018-v2.json'
   if not review.exists():review=Path(r['mcp_retrieval_review']['path'])
   rr=json.loads(review.read_text());assert rr['hit_count']==len(rr['hits'])==cnt
   calls.append({'profile':pr,'ordinal':n,'actual_hits':cnt,'raw_check':str(raw),'raw_check_sha256':sha(raw),'review':str(review),'review_sha256':sha(review),'matches':True});actual+=cnt
  profile_counts[pr]=actual;assert next(p['mcp_hit_count']for p in r['profiles']if p['profile']==pr)==actual
 assert r['mcp_hit_count']==sum(profile_counts.values())
 rows.append({'pair':O.name,'report':str(rp),'report_sha256':sha(rp),'actual_total':sum(profile_counts.values()),'call_count':len(calls),'profiles':profile_counts,'calls':calls,'matches':True})
out=M/'snapshots'/('mcp-count-crosscheck-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
with out.open('x')as f:json.dump({'method':str(Path(__file__)),'method_sha256':sha(Path(__file__)),'active_report_resolution':str(M/'method/report-resolution-index-v1.json'),'all_match':True,'pairs':rows},f,indent=2)
print(json.dumps({'path':str(out),'sha256':sha(out),'pairs':len(rows),'hits':sum(r['actual_total']for r in rows),'multi_call_pairs':[r['pair']for r in rows if r['call_count']>1]}))
