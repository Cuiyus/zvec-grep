from pathlib import Path
import json,hashlib,re
O=Path(__file__).parent; B=O.parents[1];S=B/'native-architecture-q004-preparation-v1/prepared/q004/tasks/zvec-q004/environment/source'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ref(p):return {'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
def load(p):return json.loads(p.read_text())
def dump(p,x):
 with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
m=load(O/'zvec-grep/mcp-0025-verification.json');data=(O/'zvec-grep/mcp-0025-response.txt').read_bytes();text=data.decode();m['query_groups_reported']=int(re.search(r'query groups \((\d+)\):',text)[1]);assert m['query_groups_reported']==m['actual_query_groups']==4
nonwhite=[]
for h in m['hits']:
 returned=[]
 for x in h['lines']:
  raw=data[x['result_text_byte_offset']:x['result_text_byte_offset']+x['result_text_byte_length']].decode().removesuffix('\n');match=re.fullmatch(r'(\d+)\t(.*)',raw);assert match and int(match[1])==x['line'];value=match[2];assert hashlib.sha256(value.encode()).hexdigest()==x['returned_text_sha256'];returned.append(value)
 snippet='\n'.join(returned);actual='\n'.join((S/h['path']).read_text().split('\n')[h['start_line']-1:h['end_line']]);exactprefix=actual.startswith(snippet)
 for x in h['lines']:
  if x['comparison']=='non_whitespace_difference':
   assert exactprefix and x['returned_is_exact_source_prefix'] and x['at_declared_hit_end'] and x['last_displayed_source_line'];x['whole_snippet_is_contiguous_source_prefix']=exactprefix
   nonwhite.append({'rank':h['rank'],'path':h['path'],'declared_span':[h['start_line'],h['end_line']],'raw_header':re.search(r'^#'+str(h['rank'])+r' .*$',text,re.M)[0],'returned_full_entity_snippet':snippet,'frozen_full_declared_line_span':actual,**x})
assert len(nonwhite)==3
dump(O/'zvec-grep/mcp-0025-verification-v2.json',m)
obs=load(O/'observations.json');obs['non_whitespace_mcp_differences']=nonwhite;obs['all_three_at_hit_end_and_last_displayed_line']=True;obs['boundary_verification_method']='Reconstruct each hit only from its recorded response UTF-8 byte offsets, verify text hashes and numbered rows; compare complete entity snippet to frozen source declared span. Never globally match just a line number shared by other files.'
dump(O/'observations-v2.json',obs)
review=load(O/'zvec-grep/review-0025.json');review['mcp_verification']=ref(O/'zvec-grep/mcp-0025-verification-v2.json');dump(O/'zvec-grep/review-0025-v2.json',review)
diagnostic={'kind':'auditor_metadata_correction','superseded_initial_report':ref(O/'report.json'),'initial_report_preserved':True,'raw_solver_evidence_changed':False,'scope_or_fact_verdict_changed':False,'corrections':[{'field':'query_groups_reported','initial':3,'corrected':4,'cause':'Inherited literal dict field survived earlier incorrect keyword-style replacement. Parse response header in source-check-v4.py.'},{'field':'whole_snippet_is_contiguous_source_prefix','initial_hit39':False,'corrected_all_three':True,'cause':'Initial global line-number regex matched another hit with same row number. Correct by response byte offsets already retained per hit. Per-line source equality/three punctuation differences were unaffected.'}],'authoritative_report_filename':'report-v2.json','authoritative_checks':ref(O/'source-checks-v4.json'),'authoritative_mcp':ref(O/'zvec-grep/mcp-0025-verification-v2.json'),'preserved_initial_reference_check':ref(O/'reference-verification.json')}
dump(O/'report-correction-v2.json',diagnostic)
r=load(O/'report.json');r['schema_version']=2;r['supersedes_preserved_report']=ref(O/'report.json');r['correction_record']=ref(O/'report-correction-v2.json');r['machine_source_checks']=ref(O/'source-checks-v4.json');r['observations']=ref(O/'observations-v2.json');r['mcp_verifications']=[ref(O/'zvec-grep/mcp-0025-verification-v2.json')];r['auditor_scripts'] += [ref(O/'source-check-v4.py'),ref(O/'correct-report-v2.py')]
for p in r['profiles']:
 if p['profile']=='zvec-grep':p['per_tool_reviews']=[ref(O/'zvec-grep/review-0025-v2.json') if Path(x['path']).name=='review-0025.json' else x for x in p['per_tool_reviews']]
dump(O/'report-v2.json',r)
s=(O/'verify-references.py').read_text().replace("b/'report.json'","b/'report-v2.json'").replace("b/'reference-verification.json'","b/'reference-verification-v2.json'")
with (O/'verify-references-v2.py').open('x') as f:f.write(s)
print(json.dumps({'report_sha256':sha(O/'report-v2.json'),'prefix_equality':[x['whole_snippet_is_contiguous_source_prefix'] for x in nonwhite],'query_groups':m['query_groups_reported'],'same_scope_verdicts':True}))
