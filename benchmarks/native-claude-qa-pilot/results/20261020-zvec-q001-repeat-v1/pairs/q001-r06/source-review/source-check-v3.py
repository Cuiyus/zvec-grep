"""Read-only provenance checks for q001-r06. Never execute transcript commands or grade answers."""
from pathlib import Path, PurePosixPath
import collections, hashlib, json, re
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
O=B/'native-zvec-q001-repeat-v1-audit/source-reviews/q001-r06'; Q=B/'native-zvec-q001-repeat-v1-audit/scope/q001-r06'; R=B/'native-zvec-q001-repeat-v1-ci/q001-r06/raw'; P=B/'native-claude-live-preflight-v1/prepared'; S=P/'tasks/zvec-q001/environment/source'
def sha(x): return hashlib.sha256(x.read_bytes() if isinstance(x,Path) else x).hexdigest()
def load(p):return json.loads(p.read_text())
def ref(p):return {'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
def dump(p,x):
 with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
manifest=load(P/'source-manifest.json');sm={x['path']:x for x in manifest['files']};assert len(sm)==1232
assert all((S/p).is_file() and not (S/p).is_symlink() and sha(S/p)==x['sha256'] and (S/p).stat().st_size==x['bytes'] for p,x in sm.items())
source_lines={p:(S/p).read_text(errors='replace').split('\n') for p in sm}
allnames={p.name for p in S.rglob('*')}
profiles=[]; mcps=[]; checked=[]; issues=[]
for profile in ('baseline','zvec-grep'):
 folder=Q/profile; q=load(folder/'tool-audit.json'); v2p=R/'live/collection'/f'{profile}-strict-accounting.json';v2=load(v2p)
 agent=Path(q['agent_directory']);auditpath=agent.parent/'qa-source-audit.json';audit=load(auditpath)
 assert q['extraction_complete'] and not q['errors']
 assert {(t['session'],t['id']):t['frozen_accounting_identity'] for t in q['tools']}=={(t['session'],t['id']):t for t in v2['tools']}
 source_checks=[]
 for phase in ('before','after'):
  obs=audit[phase]['observed']['files'];am={x['path']:x for x in obs};assert len(obs)==len(am)
  exact=all(am.get(p,{}).get('kind')=='file' and all(am[p].get(k)==x[k] for k in ('sha256','bytes')) for p,x in sm.items())
  extra=[x for p,x in am.items() if p not in sm]
  allowed=all(profile=='zvec-grep' and x['kind']=='file' and (x['path'].startswith('.zvec-grep/') or x['path']=='.zg-bench-index.ignore') for x in extra)
  assert exact and allowed
  source_checks.append({'phase':phase,'source_files':len(sm),'all_source_files_exact':exact,'additional_files':len(extra),'all_additions_match_original_generated_rules':allowed,'additions':extra})
 reviews=[]
 for t in q['tools']:
  n=t['ordinal'];assert len(t['call_variants'])==len(t['result_variants'])==1
  c=t['call_variants'][0];r=t['result_variants'][0];args=c['input'];name=t['name']
  assert t['identity_matches_frozen_accounting']
  for var in (c,r):
   for loc in var['locations']:
    data=(agent/loc['file']).read_bytes(); line=data.splitlines(keepends=True)[loc['line']-1]
    assert sha(data)==loc['file_sha256'] and sha(line)==loc['raw_line_sha256'] and data[loc['byte_offset']:loc['byte_offset']+loc['byte_length']]==line
    assert json.loads(line)['message']['content'][loc['block_index']]==var['block']
  review={'ordinal':n,'session':t['session'],'tool_id':t['id'],'name':name,'input':args,'tool_evidence':ref(folder/t['evidence_path']),'call_locations':c['locations'],'result_locations':r['locations'],'input_sha256':c['input_sha256'],'result_block_sha256':r['block_sha256'],'is_error':r['is_error'],'quality_graded':False}
  if name=='Read':
   path=args['file_path'];assert path.startswith('/workspace/') and '..' not in PurePosixPath(path).parts
   rel=path[len('/workspace/'):];assert rel in sm;lines=source_lines[rel];f=r['metadata']['file']
   assert f['filePath']==path and f['totalLines']==len(lines) and f['startLine']==args.get('offset',1)
   assert f['content']=='\n'.join(lines[f['startLine']-1:f['startLine']-1+f['numLines']])
   returned=[re.fullmatch(r'(\d+)\t(.*)',x) for x in r['content'].splitlines()]
   assert all(returned) and len(returned)==f['numLines'] and all(m[2]==lines[int(m[1])-1] for m in returned)
   review.update(source_files=[sm[rel]],returned_numbered_lines=len(returned),all_numbered_lines_and_metadata_exact=True,operation='Read frozen source slice',observation='Source inspection only; no project execution.')
  elif name=='Bash':
   cmd=args['command'];meta=r['metadata'];assert (r['content']==meta['stdout'] or (profile=='zvec-grep' and n==7 and r['content']=='(Bash completed with no output)' and meta['stdout']=='')) and meta['stderr']=='' and meta['interrupted'] is False
   targets=[];requested=[]
   path_exprs=re.findall(r'/workspace(?:/[A-Za-z0-9_.?*+/-]+)?',cmd)
   relative_exprs=re.findall(r'(?<![A-Za-z0-9_./])(?:src|tests)(?:/[A-Za-z0-9_.?*+/-]+)?',cmd)
   if relative_exprs:assert all(l['event_metadata'].get('cwd')=='/workspace' for l in c['locations'] if l['source_kind']=='persisted-session-jsonl')
   path_exprs += ['/workspace/'+x for x in relative_exprs]
   for path in path_exprs:
    assert '..' not in PurePosixPath(path).parts
    matches=list(S.glob(path[len('/workspace/'):])) if any(x in path for x in '*?') else [S if path=='/workspace' else S/path.removeprefix('/workspace/')]
    requested.append({'path_expression':path,'matched_frozen_paths':[p.relative_to(S).as_posix() for p in matches if p.exists()]})
    for p in matches:
     if p.is_file():targets.append(p)
     if p.is_dir():targets.extend(x for x in p.rglob('*') if x.is_file())
   eligible={p.relative_to(S).as_posix() for p in targets};texts={p:source_lines[p] for p in eligible if p in source_lines};classes=collections.Counter();lnchecks=[];missing=[]
   for line_no,line in enumerate(meta['stdout'].splitlines(),1):
    item={'output_line':line_no,'text_sha256':sha(line.encode())}
    if not line or line in ('---','----','===','===='):kind='blank_or_literal_separator'
    elif line=='/workspace' or (line.startswith('/workspace/') and (S/line.removeprefix('/workspace/').rstrip(':')).exists()):kind='frozen_tree_path'
    elif re.fullmatch(r'\s*\d+\s+(?:(?:/workspace/)?(?:src|tests)/\S+|total)',line):kind='wc_line_count_metadata'
    elif line=='=====WRITE=====' and 'echo =====WRITE=====;' in cmd:kind='literal_echo_separator'
    elif profile=='zvec-grep' and n==12 and line=='ugrep: warning: /workspace/src/db/index/common/segment_meta.h: No such file or directory':
     assert not (S/'src/db/index/common/segment_meta.h').exists();kind='explicit_missing_source_local_path_diagnostic'
    elif profile=='zvec-grep' and n==18 and line_no>11 and re.fullmatch(r'\d+:.*',line):
     displayed,txt=line.split(':',1);num=int(displayed)+143;assert source_lines['tests/db/crash_recovery/optimize_recovery_test.cc'][num-1]==txt
     kind='exact_grep_line_with_sed_slice_offset';item.update(displayed_line_number=int(displayed),source_line_offset=143,source_matches=[{'path':'tests/db/crash_recovery/optimize_recovery_test.cc','line':num}])
    elif (line.startswith('src') or line.startswith('tests')) and (S/line.rstrip(':')).exists():kind='relative_frozen_tree_path'
    elif re.match(r'^(?:total \d+|[dl-][rwx-]{9}\s)',line):kind='ls_directory_entry_metadata'
    elif '/' not in line and line in allnames:kind='frozen_tree_entry_name'
    elif profile=='baseline' and n==15 and line_no==122:
     rel='src/include/zvec/db/collection.h';assert not (S/rel).read_bytes().endswith(b'\n') and line==source_lines[rel][-1]+'----' and 'echo ----;' in cmd
     kind='exact_source_eof_plus_literal_separator';item.update(source_matches=[{'path':rel,'line':len(source_lines[rel])}],no_trailing_source_newline=True,following_text='----')
    elif any(line in lines for lines in texts.values()):
     kind='exact_source_line';item['source_matches']=[{'path':p,'line':i+1} for p,lines in texts.items() for i,v in enumerate(lines) if v==line]
    else:
     m=re.fullmatch(r'(?:(/workspace/.*?|(?:src|tests)/.*?)[:-])?(\d+)[:-](.*)',line)
     matched=[]
     if m:
      candidates=[m[1].removeprefix('/workspace/')] if m[1] else list(texts)
      matched=[{'path':p,'line':int(m[2])} for p in candidates if p in texts and 0<int(m[2])<=len(texts[p]) and texts[p][int(m[2])-1]==m[3]]
     if matched:kind='exact_numbered_grep_line';item['source_matches']=matched
     else:kind='unclassified';missing.append({'line':line_no,'text':line})
    item['classification']=kind;classes[kind]+=1;lnchecks.append(item)
   if missing:issues.append({'profile':profile,'ordinal':n,'unclassified':missing})
   review.update(operation='Read-only shell source inspection',requested_source_paths=requested,stdout_sha256=sha(meta['stdout'].encode()),stdout_line_classes=dict(classes),output_line_checks=lnchecks,unclassified_lines=missing,stderr=meta['stderr'],interrupted=meta['interrupted'],observation='Complete shell expression manually reviewed as local ls/pwd/find/grep/sed/head/sort/echo inspection. No transcript command executed by auditor. Source text is matched to files reachable through its declared workspace targets; listing metadata is classified separately.')
  elif name=='mcp__zvec_grep__zvec_grep_search':
   assert args['root']=='/workspace';assert len(r['content'])==1 and r['metadata']==r['content'];text=r['content'][0]['text'];hits=[];current=None;offset=0;unparsed=[]
   for rawline in text.splitlines(keepends=True):
    line=rawline.removesuffix('\n');h=re.match(r'^#(\d+).*? ([^\s:]+):(\d+)(?:-(\d+))?$',line)
    if h:
     rel=h[2];assert rel in sm;current={'rank':int(h[1]),'path':rel,'start_line':int(h[3]),'end_line':int(h[4] or h[3]),'source_file_sha256':sm[rel]['sha256'],'lines':[],'truncated_preview':False};hits.append(current)
    elif line=='...' and current:current['truncated_preview']=True
    m=re.match(r'^(\d+)(?:[:-])?\t(.*)$',line)
    if m:
     assert current;num=int(m[1]);returned=m[2];actual=source_lines[current['path']][num-1];assert current['start_line']<=num<=current['end_line']
     kind='exact' if returned==actual else 'leading_indentation_omitted' if returned==actual.lstrip() else 'non_whitespace_difference'
     current['lines'].append({'line':num,'comparison':kind,'returned_text_sha256':sha(returned.encode()),'source_text_sha256':sha(actual.encode()),'result_text_byte_offset':offset,'result_text_byte_length':len(rawline.encode()),**({'returned_text':returned,'source_text':actual} if kind!='exact' else {})})
    elif not h and line and line not in ('source:','...') and not line.startswith(('freshness:','results:','background_refresh:','query groups (','  Q','selection:','groups:','heading:','heading_level:')):unparsed.append(line)
    offset+=len(rawline.encode())
   assert hits and all(h['lines'] for h in hits) and not unparsed and [h['rank'] for h in hits]==list(range(1,len(hits)+1))
   counts=collections.Counter(x['comparison'] for h in hits for x in h['lines'])
   check={'ordinal':n,'tool_id':t['id'],'root':args['root'],'input':args,'requested_limit':args['limit'],'query_groups_reported':3,'hit_count':len(hits),'line_comparison_counts':dict(counts),'hits':hits,'native_result_content_and_metadata_identical':True,'all_raw_result_locations_verified':True,'result_text_sha256':sha(text.encode()),'result_text_bytes':len(text.encode()),'text':text,'unparsed_lines':unparsed};mcps.append(check)
   review.update(operation='Native zg retrieval',mcp_check=check)
  elif name in ('TaskCreate','TaskUpdate'):
   assert profile=='baseline' and not r['is_error']
   meta=r['metadata']
   if name=='TaskCreate':
    tid=meta['task']['id'];assert set(meta)=={'task'} and meta['task']=={'id':tid,'subject':args['subject']} and r['content']==f"Task #{tid} created successfully: {args['subject']}"
   else:
    tid=args['taskId'];assert r['content']==f'Updated task #{tid} status' and meta=={'success':True,'taskId':tid,'updatedFields':['status'],'statusChange':{'from':meta['statusChange']['from'],'to':args['status']}}
   review.update(operation='Native self-authored planning task metadata',task_id=tid,raw_result=r['content'],raw_metadata=meta,observation='Native common task tool result contains only self-authored task subject/id and status transitions. No filesystem contents or code facts returned; not child agent delegation.')
  else:raise AssertionError('Unreviewed tool name '+name)
  reviews.append(review);checked.append(review)
 taskrows=[r for r in reviews if r['name'] in ('TaskCreate','TaskUpdate')]
 taskrows.sort(key=lambda r:min(l['line'] for l in r['call_locations'] if l['source_kind']=='persisted-session-jsonl'))
 states={};taskchain=[]
 for row in taskrows:
  tid=row['task_id']
  if row['name']=='TaskCreate':assert tid not in states;states[tid]='pending'
  else:
   assert tid in states and states[tid]==row['raw_metadata']['statusChange']['from'];states[tid]=row['input']['status']
  taskchain.append({'ordinal':row['ordinal'],'task_id':tid,'operation':row['name'],'state_after':states[tid]})
 assert not states or states=={'1':'completed','2':'completed'}
 profiles.append({'profile':profile,'native_self_task_chain':taskchain,'tool_count':len(reviews),'root_tool_count':q['root_tool_count'],'child_tool_count':q['child_tool_count'],'tool_counts':dict(collections.Counter(t['name'] for t in q['tools'])),'accounting':ref(v2p),'accounting_complete':v2['complete'],'all_tool_identities_equal_frozen_v2':True,'sessions':q['sessions'],'source_audit':ref(auditpath),'source_checks':source_checks,'extraction':ref(folder/'tool-audit.json'),'reviews':reviews})
result={'source_manifest':ref(P/'source-manifest.json'),'task_instruction':ref(P/'tasks/zvec-q001/instruction.md'),'source_root':str(S),'source_file_count':len(sm),'source_inventory_sha256':manifest['inventory_sha256'],'profiles':profiles,'issues':issues,'tool_count':len(checked),'mcp_count':len(mcps),'mcp_line_counts':[m['line_comparison_counts'] for m in mcps],'quality_graded':False,'manual_scope_verdict':'pending'}
dump(O/'source-checks-v3.json',result)
print(json.dumps({'tools':len(checked),'profiles':[{k:p[k] for k in ('profile','tool_count','root_tool_count','child_tool_count','tool_counts')} for p in profiles],'issues':issues,'mcp_hits':[m['hit_count'] for m in mcps],'mcp_line_counts':result['mcp_line_counts'],'nonwhite_diffs':[{'rank':h['rank'],'path':h['path'],'line':x} for m in mcps for h in m['hits'] for x in h['lines'] if x['comparison']=='non_whitespace_difference'],'source_checks':[{ 'profile':p['profile'],'phases':[{k:v for k,v in x.items() if k!='additions'} for x in p['source_checks']]} for p in profiles]},ensure_ascii=False))
