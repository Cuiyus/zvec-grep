"""Read-only provenance checks for q003. Never execute transcript commands or grade answers."""
from pathlib import Path, PurePosixPath
import collections, hashlib, json, re
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
O=B/'native-expansion-v1-audit/q003-scope'; R=B/'native-expansion-v1-ci/q003/raw'; P=B/'native-expansion-prepared-v1/q003'; S=P/'tasks/zvec-q003/environment/source'
def sha(x): return hashlib.sha256(x.read_bytes() if isinstance(x,Path) else x).hexdigest()
def load(p):return json.loads(p.read_text())
def ref(p):return {'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
def dump(p,x):
 with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
manifest=load(P/'source-manifest.json');sm={x['path']:x for x in manifest['files']};assert len(sm)==1519
assert all((S/p).is_file() and not (S/p).is_symlink() and sha(S/p)==x['sha256'] and (S/p).stat().st_size==x['bytes'] for p,x in sm.items())
source_lines={p:(S/p).read_text(errors='replace').split('\n') for p in sm}
allnames={p.name for p in S.rglob('*')}
profiles=[]; mcps=[]; checked=[]; issues=[]
for profile in ('baseline','zvec-grep'):
 folder=O/profile; q=load(folder/'tool-audit.json'); v2p=R/'live/collection'/f'{profile}-strict-accounting.json';v2=load(v2p)
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
   cmd=args['command'];meta=r['metadata'];assert r['content']==meta['stdout'] and meta['stderr']=='' and meta['interrupted'] is False
   targets=[];requested=[]
   for path in re.findall(r'/workspace(?:/[A-Za-z0-9_.?*+/-]+)?',cmd):
    assert '..' not in PurePosixPath(path).parts
    matches=list(S.glob(path[len('/workspace/'):])) if any(x in path for x in '*?') else [S if path=='/workspace' else S/path.removeprefix('/workspace/')]
    requested.append({'path_expression':path,'matched_frozen_paths':[p.relative_to(S).as_posix() for p in matches if p.exists()]})
    for p in matches:
     if p.is_file():targets.append(p)
     if p.is_dir():targets.extend(x for x in p.rglob('*') if x.is_file())
   eligible={p.relative_to(S).as_posix() for p in targets};texts={p:source_lines[p] for p in eligible if p in source_lines};classes=collections.Counter();lnchecks=[];missing=[]
   for line_no,line in enumerate(meta['stdout'].splitlines(),1):
    item={'output_line':line_no,'text_sha256':sha(line.encode())}
    if profile=='zvec-grep' and n==25 and line_no<=9:
     assert args['command']=='ls /logs/agent/sessions/ 2>/dev/null; echo \"---\"; pwd; echo \"---\"; ls'
     assert line==['CLAUDE.md','backups','debug','projects','session-env','sessions','settings.json','shell-snapshots','skills'][line_no-1]
     kind='outside_workspace_directory_entry_name_metadata'
    elif not line or line in ('---','----','===','===='):kind='blank_or_literal_separator'
    elif line=='/workspace' or (line.startswith('/workspace/') and (S/line.removeprefix('/workspace/').rstrip(':')).exists()):kind='frozen_tree_path'
    elif re.match(r'^(?:total \d+|[dl-][rwx-]{9}\s)',line):kind='ls_directory_entry_metadata'
    elif '/' not in line and line in allnames:kind='frozen_tree_entry_name'
    elif any(line in lines for lines in texts.values()):
     kind='exact_source_line';item['source_matches']=[{'path':p,'line':i+1} for p,lines in texts.items() for i,v in enumerate(lines) if v==line]
    else:
     nested=re.fullmatch(r'(\d+):(\d+):(.*)',line) if profile=='baseline' and n==35 else None
     if nested:
      assert 'grep -in' in cmd
      item['pipeline_result_line_number']=int(nested[1])
     matchline=(nested[2]+':'+nested[3]) if nested else line
     m=re.fullmatch(r'(?:(/workspace/[^:]+):)?(\d+):(.*)',matchline)
     matched=[]
     if m:
      candidates=[m[1].removeprefix('/workspace/')] if m[1] else list(texts)
      matched=[{'path':p,'line':int(m[2])} for p in candidates if p in texts and 0<int(m[2])<=len(texts[p]) and texts[p][int(m[2])-1]==m[3]]
     if matched:kind='exact_source_grep_line_with_pipeline_number' if nested else 'exact_numbered_grep_line';item['source_matches']=matched
     else:kind='unclassified';missing.append({'line':line_no,'text':line})
    item['classification']=kind;classes[kind]+=1;lnchecks.append(item)
   if missing:issues.append({'profile':profile,'ordinal':n,'unclassified':missing})
   review.update(operation='Read-only shell source inspection',requested_source_paths=requested,stdout_sha256=sha(meta['stdout'].encode()),stdout_line_classes=dict(classes),output_line_checks=lnchecks,unclassified_lines=missing,stderr=meta['stderr'],interrupted=meta['interrupted'],observation='Complete shell expression manually reviewed as local source inspection or separately recorded directory/git metadata probes. No transcript command executed by auditor. Source text is matched to files reachable through its declared workspace targets; listing metadata is classified separately.')
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
    elif not h and line and line not in ('source:','...') and not line.startswith(('freshness:','results:','background_refresh:','query groups (','  Q','selection:','groups:')):unparsed.append(line)
    offset+=len(rawline.encode())
   assert hits and all(h['lines'] for h in hits) and not unparsed and [h['rank'] for h in hits]==list(range(1,len(hits)+1))
   counts=collections.Counter(x['comparison'] for h in hits for x in h['lines'])
   check={'ordinal':n,'tool_id':t['id'],'root':args['root'],'input':args,'requested_limit':args['limit'],'query_groups_reported':4,'hit_count':len(hits),'line_comparison_counts':dict(counts),'hits':hits,'native_result_content_and_metadata_identical':True,'all_raw_result_locations_verified':True,'result_text_sha256':sha(text.encode()),'result_text_bytes':len(text.encode()),'text':text,'unparsed_lines':unparsed};mcps.append(check)
   review.update(operation='Native zg retrieval',mcp_check=check)
  else:raise AssertionError('Unreviewed tool name '+name)
  reviews.append(review);checked.append(review)
 profiles.append({'profile':profile,'tool_count':len(reviews),'root_tool_count':q['root_tool_count'],'child_tool_count':q['child_tool_count'],'tool_counts':dict(collections.Counter(t['name'] for t in q['tools'])),'accounting':ref(v2p),'accounting_complete':v2['complete'],'all_tool_identities_equal_frozen_v2':True,'sessions':q['sessions'],'source_audit':ref(auditpath),'source_checks':source_checks,'extraction':ref(folder/'tool-audit.json'),'reviews':reviews})
result={'source_manifest':ref(P/'source-manifest.json'),'task_instruction':ref(P/'tasks/zvec-q003/instruction.md'),'source_root':str(S),'source_file_count':len(sm),'source_inventory_sha256':manifest['inventory_sha256'],'profiles':profiles,'issues':issues,'tool_count':len(checked),'mcp_count':len(mcps),'mcp_line_counts':[m['line_comparison_counts'] for m in mcps],'quality_graded':False,'manual_scope_verdict':'pending'}
dump(O/'source-checks.json',result)
print(json.dumps({'tools':len(checked),'profiles':[{k:p[k] for k in ('profile','tool_count','root_tool_count','child_tool_count','tool_counts')} for p in profiles],'issues':issues,'mcp_hits':[m['hit_count'] for m in mcps],'mcp_line_counts':result['mcp_line_counts'],'nonwhite_diffs':[{'rank':h['rank'],'path':h['path'],'line':x} for m in mcps for h in m['hits'] for x in h['lines'] if x['comparison']=='non_whitespace_difference'],'source_checks':[{ 'profile':p['profile'],'phases':[{k:v for k,v in x.items() if k!='additions'} for x in p['source_checks']]} for p in profiles]},ensure_ascii=False))
