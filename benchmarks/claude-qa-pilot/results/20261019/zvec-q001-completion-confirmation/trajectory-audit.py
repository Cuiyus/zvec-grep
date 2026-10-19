from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime
import json,hashlib,re
BASE=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/confirmation-39282211793/qa-zvec-q001')
SOURCE=Path('/tmp/zg-zvec-issue-qa-20261019/final-source')
WT=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
RUBRIC=WT/'benchmarks/zvec-issue-qa/evaluator/q001.json'
HOOK=WT/'benchmarks/claude-qa-pilot/tool_budget.py'
OUT=Path('/tmp/zg-confirmation-39282211793-trajectory-audit.json')
def sha(b):return hashlib.sha256(b).hexdigest()
def file_record(p):return {'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())}
def text_blocks(c):
 if isinstance(c,str):return c
 return ''.join(x.get('text','') for x in c if isinstance(x,dict) and x.get('type')=='text') if isinstance(c,list) else ''
def norm(p):return p.removeprefix('/workspace/').removeprefix('./')
def ranges(nums):
 ns=sorted(set(nums));out=[]
 for n in ns:
  if out and n==out[-1][1]+1:out[-1][1]=n
  else:out.append([n,n])
 return out
manifest=json.loads((BASE/'manifest.json').read_text())
source_manifest=json.loads((BASE/'source-manifest.json').read_text())
rubric=json.loads(RUBRIC.read_text())
source_cache={}
def source_lines(p):
 if p not in source_cache:source_cache[p]=(SOURCE/p).read_text().splitlines()
 return source_cache[p]
source_checks=[]
for e in rubric['evidence_spans']:
 b=(SOURCE/e['path']).read_bytes();span='\n'.join(b.decode().splitlines()[e['start_line']-1:e['end_line']])+'\n'
 source_checks.append({'evidence_id':e['id'],'path':e['path'],'file_sha256':sha(b),'file_matches_rubric':sha(b)==e['file_sha256'],'file_matches_run_manifest':sha(b)==source_manifest['files'][e['path']],'span_sha256':sha(span.encode()),'span_matches_rubric':sha(span.encode())==e['span_sha256']})
assert all(x['file_matches_rubric'] and x['file_matches_run_manifest'] and x['span_matches_rubric'] for x in source_checks)
assert sha(HOOK.read_bytes())==manifest['budget_hook_sha256']

def audit_arm(arm):
 d=BASE/arm;rows=[json.loads(x) for x in (d/'stream.jsonl').read_text().splitlines()];metrics=json.loads((d/'metrics.json').read_text());budget=json.loads((d/'tool-budget.json').read_text());state=budget['state'];calls=[];byid={};rounds={};duplicate_ids=[];result=None;warning_hits=[]
 for ln,r in enumerate(rows,1):
  if r.get('type')=='result':result=(ln,r)
  msg=r.get('message',{});cs=msg.get('content',[])
  if not isinstance(cs,list):continue
  # Deliberately never inspect or export thinking block contents, answer text, or judge outputs.
  for c in cs:
   if c.get('type')=='tool_use':
    if c['id'] in byid:duplicate_ids.append(c['id']);continue
    rid=msg['id'];rounds.setdefault(rid,len(rounds)+1)
    x={'call':len(calls)+1,'tool_round':rounds[rid],'tool_use_id':c['id'],'assistant_message_id':rid,'name':c['name'],'input':c.get('input',{}),'stream_call_line':ln,'stream_result_lines':[],'_result_text':'','_pairs':[],'_read_pairs':[]}
    calls.append(x);byid[c['id']]=x
   elif c.get('type')=='tool_result':
    x=byid[c['tool_use_id']];s=text_blocks(c.get('content',''));x['stream_result_lines'].append(ln);x['_result_text']+=s;x['is_error']=bool(c.get('is_error',False));x['result_timestamp']=r.get('timestamp');x['_raw_metadata']=r.get('tool_use_result',{})
    if 'Investigation budget:' in s or 'Prioritize only material evidence gaps' in s:warning_hits.append(ln)
 assert result is not None
 for x in calls:
  assert len(x['stream_result_lines'])==1
  s=x['_result_text'];x['result_text_characters']=len(s);x['result_text_utf8_bytes']=len(s.encode());x['result_text_sha256']=sha(s.encode());x['hook_state']=state['calls'][x['tool_use_id']];x['hook_elapsed_seconds']=x['hook_state']['elapsed_seconds']
  t=x.get('result_timestamp');x['result_elapsed_seconds']=datetime.fromisoformat(t.replace('Z','+00:00')).timestamp()-state['started_at_epoch'] if t else None
  pairs=[];read_pairs=[];bad=[];sections=[];active=None
  if x['name']=='Read':
   p=norm(x['input']['file_path']);meta=x['_raw_metadata'].get('file',{});returned=[]
   for l in s.splitlines():
    m=re.match(r'^(\d+)\t(.*)$',l)
    if m:returned.append((int(m[1]),m[2]));pairs.append((p,int(m[1]),m[2]));read_pairs.append((p,int(m[1]),m[2]))
   x['read']={'path':p,'requested_offset':x['input'].get('offset',1),'requested_limit':x['input'].get('limit'),'returned_ranges':ranges(n for n,_ in returned),'returned_line_count':len(returned),'file_total_lines':meta.get('totalLines'),'read_entire_file':len(returned)==meta.get('totalLines') and bool(returned) and returned[0][0]==1}
  elif x['name']=='Grep' and x['input'].get('output_mode')=='content':
   p=norm(x['input'].get('path',''));single=(SOURCE/p).is_file()
   for l in s.splitlines():
    m=re.match(r'^(\d+)[:-](.*)$',l) if single else None
    if m:pairs.append((p,int(m[1]),m[2]));continue
    m=re.match(r'^(.+?)[-:](\d+)[-:](.*)$',l)
    if m and (SOURCE/norm(m[1])).is_file():pairs.append((norm(m[1]),int(m[2]),m[3]))
  elif x['name']=='mcp__zvec_grep__zvec_grep_search':
   for l in s.splitlines():
    m=re.match(r'^#(\d+).*?\s((?:src|python|tests)/[^ :]+):(\d+)(?:-(\d+))?',l)
    if m:
     active={'rank':int(m[1]),'path':m[2],'advertised_range':[int(m[3]),int(m[4] or m[3])],'returned_lines':[],'truncated_preview':False};sections.append(active);continue
    if active:
     if l=='...':active['truncated_preview']=True
     m=re.match(r'^(\d+)\t(.*)$',l)
     if m:pairs.append((active['path'],int(m[1]),m[2]));active['returned_lines'].append(int(m[1]))
   for sec in sections:sec['returned_ranges']=ranges(sec.pop('returned_lines'))
   x['zg_results']={'freshness':s.splitlines()[:3],'sections':sections,'interpretation':'Only explicitly returned source lines are counted; advertised ranges alone do not establish evidence arrival.'}
  for p,n,line in pairs:
   src=source_lines(p)
   if 0<n<=len(src) and src[n-1]==line:x['_pairs'].append((p,n))
   else:bad.append({'path':p,'line':n,'reason':'synthetic trailing empty row rendered beyond final source line' if n==len(src)+1 and line=='' else 'returned line text differs from whole source line (may be snippet boundary truncation)'})
  for p,n,line in read_pairs:
   if 0<n<=len(source_lines(p)) and source_lines(p)[n-1]==line:x['_read_pairs'].append((p,n))
  x['verified_source_line_count']=len(x['_pairs']);x['nonmatching_numbered_source_lines']=bad
  if x['name']=='Read': assert all(v['reason']=='synthetic trailing empty row rendered beyond final source line' for v in bad)
  x['fully_contains_evidence_spans']=[]
  pairset=set(x['_pairs'])
  for e in rubric['evidence_spans']:
   if all((e['path'],n) in pairset for n in range(e['start_line'],e['end_line']+1)):x['fully_contains_evidence_spans'].append(e['id'])
 all_counts=Counter();read_counts=Counter();priorread=set();exact=defaultdict(list);bytool=defaultdict(lambda:{'calls':0,'result_characters':0,'result_utf8_bytes':0});ev=[]
 for x in calls:
  bytool[x['name']]['calls']+=1;bytool[x['name']]['result_characters']+=x['result_text_characters'];bytool[x['name']]['result_utf8_bytes']+=x['result_text_utf8_bytes'];all_counts.update(x['_pairs']);read_counts.update(x['_read_pairs'])
  if x['name']=='Read':x['read']['previously_read_source_lines']=len(set(x['_read_pairs'])&priorread);priorread.update(x['_read_pairs'])
  normalized={k:norm(v) if k in ['path','file_path','root'] and isinstance(v,str) else v for k,v in x['input'].items()};exact[(x['name'],json.dumps(normalized,sort_keys=True))].append(x['call'])
 def milestone(x):return {'call':x['call'],'tool_round':x['tool_round'],'stream_call_line':x['stream_call_line'],'stream_result_line':x['stream_result_lines'][0],'hook_elapsed_seconds':x['hook_elapsed_seconds'],'result_elapsed_seconds':x['result_elapsed_seconds']}
 for e in rubric['evidence_spans']:
  desired={(e['path'],n) for n in range(e['start_line'],e['end_line']+1)};covered=set();arrivals=[];complete=None
  for x in sorted(calls,key=lambda x:x['stream_result_lines'][0]):
   new=set(x['_pairs'])&desired-covered
   if new:arrivals.append({**milestone(x),'new_verified_ranges':ranges(n for _,n in new),'new_verified_line_count':len(new)});covered|=new
   if covered==desired and complete is None:complete=milestone(x)
  ev.append({'evidence_id':e['id'],'path':e['path'],'source_range':[e['start_line'],e['end_line']],'verified_lines_returned':len(covered),'total_span_lines':len(desired),'verified_returned_ranges':ranges(n for _,n in covered),'complete_by':complete,'arrivals':arrivals})
 evmap={x['evidence_id']:x for x in ev};bundles=[]
 for criterion in rubric['criteria']:
  ids=criterion['evidence_sets'][0];missing=[eid for eid in ids if evmap[eid]['complete_by'] is None];latest=max((evmap[eid]['complete_by'] for eid in ids if evmap[eid]['complete_by']),key=lambda t:t['stream_result_line'],default=None)
  bundles.append({'criterion_id':criterion['id'],'evidence_ids':ids,'missing_full_spans':missing,'full_source_bundle_complete_by':latest if not missing else None,'not_a_quality_score':True})
 core=[x for x in bundles if x['criterion_id'] in ['C1','C2','C3']];corepoint=max((x['full_source_bundle_complete_by'] for x in core),key=lambda t:t['stream_result_line']) if all(x['full_source_bundle_complete_by'] for x in core) else None
 warnings=[]
 for order,(tid,c) in enumerate(state['calls'].items(),1):
  admitted_so_far=sum(v['admitted'] for _,v in list(state['calls'].items())[:order])
  if c['admitted'] and (admitted_so_far>=18 or c['elapsed_seconds']>=480):warnings.append({**milestone(byid[tid]),'admitted_so_far':admitted_so_far,'condition':'call_count_75_percent' if admitted_so_far>=18 else 'elapsed_80_percent','expected_additionalContext':f"Investigation budget: {admitted_so_far}/24 repository tool calls admitted; {c['elapsed_seconds']:.0f}/600 seconds elapsed. Prioritize only material evidence gaps and finish your answer soon. Do not repeat searches merely to reconfirm established evidence."})
 last=calls[-1];rl,rr=result
 out={'artifacts':{n:file_record(d/n) for n in ['stream.jsonl','tool-budget.json','metrics.json','command.json','answer.txt']},'stream_line_count':len(rows),'tool_calls':len(calls),'tool_bearing_assistant_message_rounds':len(rounds),'duplicate_stream_tool_ids':duplicate_ids,'successful_tool_results':sum(not x['is_error'] for x in calls),'error_results':sum(x['is_error'] for x in calls),'all_results_resolved':all(len(x['stream_result_lines'])==1 for x in calls),'tool_return_volume':{'definition':'Sum Unicode code points / UTF-8 bytes of user message tool_result content text only; excludes duplicate root tool_use_result payload, JSON framing, assistant text, hidden thinking and usage tokens.','characters':sum(x['result_text_characters'] for x in calls),'utf8_bytes':sum(x['result_text_utf8_bytes'] for x in calls),'by_tool':dict(bytool)},'read_overlap':{'definition':'Exact frozen-source (path,line) pairs in Read results; repeat means same source line returned by a subsequent Read, not repeated reasoning or intention.','returned_line_occurrences':sum(read_counts.values()),'displayed_numbered_rows_including_synthetic_eof_rows':sum(x.get('read',{}).get('returned_line_count',0) for x in calls),'unique_source_lines':len(read_counts),'repeated_line_occurrences':sum(v-1 for v in read_counts.values()),'lines_repeated_ranges_by_path':{p:ranges(n for (pp,n),v in read_counts.items() if pp==p and v>1) for p in sorted({p for p,n in read_counts if read_counts[(p,n)]>1})},'entire_file_read_calls':[x['call'] for x in calls if x.get('read',{}).get('read_entire_file')],'read_calls_with_overlap':[x['call'] for x in calls if x.get('read',{}).get('previously_read_source_lines',0)>0]},'all_tools_source_overlap':{'verified_line_occurrences':sum(all_counts.values()),'unique_verified_source_lines':len(all_counts),'duplicate_verified_line_occurrences':sum(v-1 for v in all_counts.values()),'note':'Includes Grep-to-Read and search-preview-to-Read overlap, often useful navigation or contextual follow-up; not automatically waste.'},'exact_duplicate_tool_requests':[{'name':k[0],'normalized_input':json.loads(k[1]),'calls':v} for k,v in exact.items() if len(v)>1],'evidence_spans':ev,'source_bundles':bundles,'full_C1_C2_C3_source_bundle_complete_by':corepoint,'post_core_additional_tool_calls':len(calls)-corepoint['call'] if corepoint else None,'budget':{'limit':24,'research_seconds':600,'admitted':budget['admitted'],'denied':budget['denied'],'all_hook_states_match_observed_tool_ids':set(state['calls'])==set(byid),'hook_sha_matches_run_manifest':True,'warning_decisions_reconstructed_from_pinned_hook_and_state':warnings,'raw_tool_result_warning_text_occurrences':warning_hits,'warning_delivery_limit':'The captured stream has no hook-response events or warning text in tool_result. Matching hook code plus admitted order/timing establishes that its warning branch should emit on these calls; actual model receipt is not independently logged by this run. Offline budget probe is separate plumbing evidence, not proof of model response causality.'},'termination':{'result_stream_line':rl,'result_subtype':rr.get('subtype'),'result_is_error':rr.get('is_error'),'stop_reason':rr.get('stop_reason'),'result_num_turns':rr.get('num_turns'),'wall_seconds':metrics['wall_seconds'],'exit_code':metrics['exit_code'],'last_tool_call':milestone(last),'wall_minus_last_tool_return_seconds':metrics['wall_seconds']-last['result_elapsed_seconds'],'delivered_final_before_call_or_time_boundary':rr.get('subtype')=='success' and rr.get('stop_reason')=='end_turn' and metrics['wall_seconds']<600 and len(calls)<24,'interpretation':'Normal end_turn final delivery with no denial, timeout or max-turn error. This establishes observed self-termination, not that the budget prompt/reminder caused it.'},'metrics_context':{'input_tokens_including_cache':metrics['input_tokens_including_cache'],'output_tokens':metrics['output_tokens'],'usage_complete':metrics['usage_complete'],'quality_not_audited':True},'calls':calls}
 for x in calls:
  for k in list(x):
   if k.startswith('_'):del x[k]
 return out

trial_names=['r02-zg','r02-baseline','r03-baseline','r03-zg']
trials={name:audit_arm(name) for name in trial_names}
comparisons=[]
for repeat in [2,3]:
 b=trials[f'r{repeat:02d}-baseline'];z=trials[f'r{repeat:02d}-zg']
 comparisons.append({'repeat':repeat,'baseline_to_zg':{
  'tool_calls':[b['tool_calls'],z['tool_calls']],
  'tool_rounds':[b['tool_bearing_assistant_message_rounds'],z['tool_bearing_assistant_message_rounds']],
  'Read_calls':[b['tool_return_volume']['by_tool']['Read']['calls'],z['tool_return_volume']['by_tool']['Read']['calls']],
  'tool_return_characters':[b['tool_return_volume']['characters'],z['tool_return_volume']['characters']],
  'repeated_Read_line_occurrences':[b['read_overlap']['repeated_line_occurrences'],z['read_overlap']['repeated_line_occurrences']],
  'full_C1_C2_C3_source_bundle_complete_by':[b['full_C1_C2_C3_source_bundle_complete_by'],z['full_C1_C2_C3_source_bundle_complete_by']],
  'input_tokens_including_cache':[b['metrics_context']['input_tokens_including_cache'],z['metrics_context']['input_tokens_including_cache']]}})
summary={'schema_version':1,'run_id':39282211793,'phase':'confirmation','audit_scope':'Tool trajectory and pinned hook only. No answer grading, prior answers/reviews, or hidden thinking analysis. Four planned trials retained.','inputs':{'manifest':file_record(BASE/'manifest.json'),'source_manifest':file_record(BASE/'source-manifest.json'),'rubric':file_record(RUBRIC),'hook':file_record(HOOK),'audit_script':file_record(Path(__file__))},'source_hash_verification':source_checks,'definitions':{'tool_round':'Unique assistant message.id containing at least one tool_use, not SDK turns.','evidence_arrival':'Exact complete source lines actually returned; a location or advertised range is insufficient. Absence of a full span does not prove answer error, because alternative evidence may suffice.','volume':'Unicode code points in tool_result text; excludes JSON metadata, assistant text and hidden thinking. Not tokens or verified billable dollars.'},'trials':trials,'comparisons':comparisons,'limitations':['Two confirmation pairs on a task selected after a positive development pair; not generalization.','Hook warning branches are reconstructed from pinned code and state; model receipt is not independently captured.','Source evidence arrival is not understanding or answer quality.','No outcome replacement, causal attribution or pooling with development.']}
OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'output':str(OUT),'sha256':sha(OUT.read_bytes()),'comparisons':comparisons},indent=2))
