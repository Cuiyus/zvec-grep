"""Offline c01 C4 evidence-availability diagnostic; never regrades or runs tools."""
from pathlib import Path
import collections
import hashlib
import json
import re
BASE=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
OUT=Path(__file__).resolve().parent
RAW=BASE/'confirmation-v1-interim/c01/raw/qa-confirmation-run/runs'
SOURCE=BASE/'confirmation-v1-local/c01/prepared/tasks/zvec-q001/environment/source'
AUDIT=BASE/'confirmation-v1-audit/c01/interim-machine-pass/audit.json'
REVIEW=BASE/'confirmation-v1-blind/review-part1.json'
def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def digest(b):return hashlib.sha256(b).hexdigest()
def write(name,data):
 p=OUT/name
 with p.open('x') as f:f.write(json.dumps(data,indent=2)+'\n')
audit=load(AUDIT);review=load(REVIEW);queue=audit['tools'];partrows={};finals={};exports={};volumes={};lexical_mentions={}
for f in sorted(RAW.glob('*/*/qa-session-exports/*.json')):
 profile=f.parent.parent.parent.name.removeprefix('q001-');d=load(f);exports[profile]={'path':str(f),'sha256':sha(f)}
 alltools=[];reads=[];covered=collections.Counter();mentions=[]
 for m in d['messages']:
  for t in m['parts']:
   if t.get('type')=='tool':
    alltools.append(t);partrows[t['id']]=t
    if t['tool']=='read':
     disp=t['state']['metadata']['display'];path=disp['path'].removeprefix('/workspace/');lo=disp['lineStart'];hi=disp['lineEnd'];lines=(SOURCE/path).read_text().splitlines()[lo-1:hi]
     assert '\n'.join(lines).strip()==disp['text'].strip()
     for line in range(lo,hi+1):covered[(path,line)]+=1
     reads.append({'part_id':t['id'],'path':path,'start_line':lo,'end_line':hi,'line_presentations':hi-lo+1,'display_utf8_bytes':len(disp['text'].encode()),'tool_output_utf8_bytes':len(t['state']['output'].encode())})
   elif t.get('type')=='reasoning':
    terms=sorted(set(re.findall(r'index_completeness|vector_indexed',t.get('text',''),re.I)))
    if terms:mentions.append({'message_id':m['info']['id'],'part_id':t['id'],'matched_terms':terms,'text_sha256':digest(t['text'].encode())})
 assistants=[m for m in d['messages'] if m['info'].get('role')=='assistant'];final='\n'.join(p.get('text','') for p in assistants[-1]['parts'] if p['type']=='text')
 expected=next(x for x in audit['profiles'] if x['profile']==profile)['terminal_answer']['answer_sha256'];assert digest(final.encode())==expected
 finals[profile]={'message_id':assistants[-1]['info']['id'],'answer_sha256':expected,'stats_paragraphs':[p for p in re.split(r'\n\s*\n',final) if re.search(r'stats|statistic|completeness|metadata.*index|index.*metadata',p,re.I)],'exact_index_completeness_term_present':bool(re.search(r'index_completeness',final)),'exact_vector_indexed_term_present':bool(re.search(r'vector_indexed',final))}
 volumes[profile]={'native_tool_count':len(alltools),'native_read_count':len(reads),'native_read_line_presentations':sum(covered.values()),'native_read_unique_file_line_positions':len(covered),'native_read_repeated_line_presentations':sum(covered.values())-len(covered),'native_read_positions_seen_more_than_once':sum(v>1 for v in covered.values()),'native_read_distinct_files':len({p for p,l in covered}),'native_read_display_utf8_bytes_sum':sum(x['display_utf8_bytes'] for x in reads),'all_tool_output_utf8_bytes_sum':sum(len(t['state'].get('output','').encode()) for t in alltools),'by_tool_output_utf8_bytes':{name:sum(len(t['state'].get('output','').encode()) for t in alltools if t['tool']==name) for name in sorted({t['tool'] for t in alltools})},'read_ranges':reads}
 lexical_mentions[profile]=mentions
mapping={'answer-A':'baseline','answer-C':'zvec-grep'};grade=[]
for g in review['reviews']:
 aid=g['blinded_answer_id'];profile=mapping[aid];assert review['inputs_hashes'][aid+'.md']==finals[profile]['answer_sha256']
 grade.append({'profile':profile,'blinded_answer_id':aid,'points_as_previously_assessed':g['total_points'],'C4_as_previously_assessed':g['criterion_scores']['C4'],'C4_rationale_as_previously_assessed':g['criterion_rationales']['C4'],'source_grounded_success_as_previously_assessed':g['source_grounded_success']})
mcp=[]
for q in queue:
 if 'zvec_grep_search' not in q['tool']:continue
 p=partrows[q['part_id']];output=p['state']['output'];headers=[]
 for rank,path,start,end in re.findall(r'^#(\d+)[^\n]*? ([^\s]+):(\d+)(?:-(\d+))?$',output,re.M):headers.append({'rank':int(rank),'path':path,'start':int(start),'end':int(end or start)})
 assert len(headers)==15
 mcp.append({'tool_id':q['id'],'part_id':q['part_id'],'request':q['input'],'input_sha256':q['input_sha256'],'output_sha256':q['output_sha256'],'results':headers,'direct_C4_stats_or_doc_count_result':any((h['path']=='src/db/collection.cc' and h['start']<=439 and h['end']>=403) or (h['path']=='src/db/index/segment/segment.cc' and h['start']<=600 and h['end']>=589) for h in headers)})
selected_ids=['prt_155fa5f68001xZ9zPSOVy0PHFF','prt_155f70303001VW6W7yJltJantd','prt_155feaeac001VFhw6bPq8sw1XG','prt_155e8dfb9001S6ZK7hrZZAo0Ru','prt_155e885f3001ujjDMUOXWsMFQr']
excerpts=[]
for tid in selected_ids:
 q=next(x for x in queue if x['part_id']==tid);p=partrows[tid];profile_tools=[x for x in queue if x['profile']==q['profile']]
 text=p['state']['output']
 if tid in ('prt_155f70303001VW6W7yJltJantd','prt_155e885f3001ujjDMUOXWsMFQr'):
  text='\n'.join(l for l in text.splitlines() if re.match(r'^(58[9]|59\d|600):',l))
 excerpts.append({'tool_id':q['id'],'profile':q['profile'],'profile_tool_ordinal_1based':profile_tools.index(q)+1,'message_id':q['message_id'],'part_id':tid,'input':q['input'],'output_sha256':q['output_sha256'],'native_tool_time':p['state']['time'],'excerpt':text})
write('read-volume.json',{'scope':'Native read windows only for source-line overlap. All-tool output bytes count unique retained tool output strings, not provider billed tokens. Bash/MCP source overlap is excluded from line counts; repetition is not automatically waste.','profiles':volumes})
write('trace-excerpts.json',{'input_exports':exports,'MCP_calls':mcp,'selected_C4_source_results':excerpts,'final_answer_stats_excerpts':finals,'reasoning_lexical_mentions_metadata_only':lexical_mentions,'reasoning_note':'Exact term presence only; no private reasoning text reproduced and no inference of comprehension or causal use.'})
record={'schema_version':1,'kind':'bounded_c01_C4_evidence_availability_diagnostic','protocol_id':audit['protocol_id'],'repeat':'c01','run_id':audit['run_id'],'head':audit['head'],'artifact_zip_sha256':audit['artifact_integrity']['sha256'],'inputs':{'native_audit_sha256':sha(AUDIT),'review_part1_sha256':sha(REVIEW),'native_exports':exports,'source_files_sha256':{p:sha(SOURCE/p) for p in ['src/db/collection.cc','src/db/index/segment/segment.cc','src/db/index/common/meta.h']}},'existing_grade_context_only':grade,'findings':[{'id':'F1','fact':'The single treatment MCP query targets WAL recovery/replay and optimization loss. All15 result definitions concern recovery/flush/WAL/optimize/tests; none is Stats403-439 or doc_count589-600. It directly returned the C2 flush guard, but not the C4 stats derivation.','evidence':'trace-excerpts.json:MCP_calls'},{'id':'F2','fact':'Treatment nevertheless received the complete Stats403-442 implementation in native read tool ordinal32 (offset403,limit50), including meta()->vector_indexed at433 and weighted completeness calculation at437-438. Native read ordinal14 supplied doc_count589-600 and doc_ids_.size().','evidence_parts':['prt_155fa5f68001xZ9zPSOVy0PHFF','prt_155f70303001VW6W7yJltJantd']},{'id':'F3','fact':'Treatment later received meta.h340-341 in bash tool ordinal63: vector_indexed returns indexed_vector_fields_.count(field)>0. This result is untruncated; relevant code was available before final generation.','evidence_part':'prt_155feaeac001VFhw6bPq8sw1XG'},{'id':'F4','fact':'The treatment final answer cites Stats403-442 and explains live doc_count, but does not contain index_completeness or vector_indexed and omits the metadata-derived completeness explanation, as the existing blind review records. Baseline final explicitly names both.','interpretation':'The recorded C4 miss cannot be explained solely by unavailable source. It localizes the observed gap to carrying available evidence into final answer coverage; it does not establish why the model omitted it.'},{'id':'F5','fact':'Both arms make repeated native-read presentations; exact comparable counts are retained separately. Line overlap excludes bash/MCP, and output-byte counts are not provider input tokens. No efficiency or causal claim follows from these counts.'}],'investigation_focus':['Inspect answer coverage for each requested observable (document counts and index completeness) during final synthesis; this trace already supplied both source mechanisms.','Inspect how recovery-focused MCP queries and partial function previews support secondary subquestions; direct C4 retrieval was absent, but later native reads closed that evidence gap.'],'no_changes_or_experiments':True,'quality_regraded':False,'causality_established':False,'final_confirmation_decision':None,'limits':['One c01 pair, task already observed; cannot infer causal model/tool effects or stable benefit.','Availability means text occurs in retained completed tool output, not a claim about internal comprehension.','Existing scores are copied only to bind the requested diagnostic; all original criteria remain unchanged.','Final confirmation validity remains pending overall run provenance and c02 audits.']}
write('c4-diagnostic.json',record)
print(json.dumps({'output':str(OUT),'volumes':{k:{x:v for x,v in d.items() if x!='read_ranges'} for k,d in volumes.items()},'quality_regraded':False}))
