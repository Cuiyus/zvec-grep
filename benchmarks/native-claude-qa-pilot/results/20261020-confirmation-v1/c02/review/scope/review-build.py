"""Materialize c02's completed source-scope review, with exact native/source bindings.
All commands are untrusted evidence; this script never executes transcript commands.
No answer text or blinded quality mapping is read. No grade is assigned.
"""
from pathlib import Path, PurePosixPath
import collections, hashlib, json, re
BASE=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
OUT=Path(__file__).parent
RAW=BASE/'native-claude-confirmation-v1-ci/c02/raw/live'
PREP=BASE/'native-claude-live-preflight-v1/prepared'
SOURCE=PREP/'tasks/zvec-q001/environment/source'
EXTRACTOR=BASE/'native-claude-live-scope-audit-v1/extract.py'
def sha(value):return hashlib.sha256(value if isinstance(value,bytes) else value.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_text())
def ref(path):return {'path':str(path),'sha256':sha(path)}
def encoded(value):return (json.dumps(value,indent=2,ensure_ascii=False,sort_keys=True)+'\n').encode()
pending={}
def retain(path,value):
 data=value if isinstance(value,bytes) else encoded(value)
 assert not path.exists() and path not in pending
 pending[path]=data
 return {'path':str(path),'sha256':sha(data)}
assert sha(EXTRACTOR)=='c129ac63632ddfcd8a2e667d02b62b5c4d87f0137ca15d411ca07f9338dcb0d9'
verification=BASE/'native-claude-confirmation-v1-ci/c02/artifact-byte-verification.json'
assert sha(verification)=='0e58a7a15d567cba969cfd8412f8547a1fad980f64a841bd078b99549102aee5'
manifest=load(PREP/'source-manifest.json');source_map={r['path']:r for r in manifest['files']}
assert len(source_map)==1232 and manifest['inventory_sha256']=='741d06d8fc216150933c5ed87f9a100e0d2120b2dc09a9109084365e92d307ba'
assert all(sha(SOURCE/p)==r['sha256'] and (SOURCE/p).stat().st_size==r['bytes'] for p,r in source_map.items())
collection=load(RAW/'collection/collection.json');all_names={p.name for p in SOURCE.rglob('*')}
all_reviews=[];profiles=[];mcp_checks=[];navigation=[];diagnostics=[]
# These are the full manually reviewed inventories, not a safe-command whitelist.
reviewed={'baseline':set(range(1,31)),'zvec-grep':set(range(1,31))}
for profile in ('baseline','zvec-grep'):
 folder=OUT/profile;queue=load(folder/'tool-audit.json');v2path=RAW/'collection'/f'{profile}-strict-accounting.json';v2=load(v2path)
 row=next(r for r in collection['profiles'] if r['profile']==profile);trial=RAW/row['trial_directory'];agent=trial/'agent'
 assert queue['extraction_complete'] and not queue['errors'] and v2['complete'] and v2['child_count']==0 and v2['root_count']==1
 assert {t['ordinal'] for t in queue['tools']}==reviewed[profile]
 assert {(t['session'],t['id']):t['frozen_accounting_identity'] for t in queue['tools']}=={(t['session'],t['id']):t for t in v2['tools']}
 auditpath=trial/'qa-source-audit.json';audit=load(auditpath);source_checks=[]
 for phase in ('before','after'):
  files=audit[phase]['observed']['files'];actual={r['path']:r for r in files};assert len(actual)==len(files)
  unchanged=all(actual.get(p,{}).get('kind')=='file' and all(actual[p].get(k)==r[k] for k in ('bytes','sha256')) for p,r in source_map.items())
  additions=[r for p,r in actual.items() if p not in source_map]
  allowed=all(profile=='zvec-grep' and r['kind']=='file' and (r['path'].startswith('.zvec-grep/') or r['path']=='.zg-bench-index.ignore') for r in additions)
  assert unchanged and allowed
  source_checks.append({'phase':phase,'all_1232_source_files_unchanged':unchanged,'additional_files':len(additions),'all_additions_within_original_generated_rules':allowed})
 counts=collections.Counter();read_lines=0;bash_classes=collections.Counter();items=[]
 for tool in queue['tools']:
  n=tool['ordinal'];call=tool['call_variants'][0];result=tool['result_variants'][0];args=call['input'];name=tool['name'];counts[name]+=1
  assert len(tool['call_variants'])==len(tool['result_variants'])==1 and not result['is_error'] and tool['identity_matches_frozen_accounting']
  for variant in (call,result):
   for loc in variant['locations']:
    data=(agent/loc['file']).read_bytes();rawline=data.splitlines(keepends=True)[loc['line']-1]
    assert sha(data)==loc['file_sha256'] and sha(rawline)==loc['raw_line_sha256']
    assert data[loc['byte_offset']:loc['byte_offset']+loc['byte_length']]==rawline
    assert json.loads(rawline)['message']['content'][loc['block_index']]==variant['block']
  review={'ordinal':n,'session':tool['session'],'tool_id':tool['id'],'name':name,'input':args,
   'evidence':ref(folder/tool['evidence_path']),'input_sha256':call['input_sha256'],'result_block_sha256':result['block_sha256'],
   'agent_directory':str(agent),
   'call_locations':[{k:l[k] for k in ('file','line','byte_offset','byte_length','raw_line_sha256','file_sha256','block_index')} for l in call['locations']],
   'result_locations':[{k:l[k] for k in ('file','line','byte_offset','byte_length','raw_line_sha256','file_sha256','block_index')} for l in result['locations']],
   'source_only_factual_evidence':True,'outside_workspace_directory_metadata_observed':False,'outside_workspace_file_content_observed':False,
   'solver_web_or_network_request':False,'solver_source_mutation':False,'project_code_or_tests_executed':False,
   'external_factual_evidence':False,'gold_or_prior_answer_content_access':False,'scope_valid':True,'quality_graded':False}
  if name=='Read':
   path=args['file_path'];assert path.startswith('/workspace/') and '..' not in PurePosixPath(path).parts
   rel=path.removeprefix('/workspace/');assert rel in source_map;f=result['metadata']['file'];lines=(SOURCE/rel).read_text().split('\n')
   assert f['filePath']==path and f['totalLines']==len(lines) and f['startLine']==args.get('offset',1)
   assert f['content']=='\n'.join(lines[f['startLine']-1:f['startLine']-1+f['numLines']])
   returned=[re.fullmatch(r'(\d+)\t(.*)',x) for x in result['content'].splitlines()]
   assert all(returned) and len(returned)==f['numLines']
   assert all(m[2]==lines[int(m[1])-1] for m in returned)
   read_lines+=len(returned)
   review.update(operation='Read frozen source slice',review_reason='Exact /workspace source file; every returned numbered line and native file.content match the frozen source slice. Repository test source is read, not executed.',
    source_files=[dict(source_map[rel])],returned_lines=len(returned),returned_numbered_text_exact=True,metadata_content_exact=True)
  elif name=='Bash':
   cmd=args['command'];meta=result['metadata'];assert result['content']==meta['stdout'] and meta['stderr']=='' and meta['interrupted'] is False
   targets=[]
   for p in re.findall(r'/workspace(?:/[A-Za-z0-9_.?*+/-]+)?',cmd):
    assert '..' not in PurePosixPath(p).parts
    matches=list(SOURCE.glob(p[len('/workspace/'):])) if any(x in p for x in '*?') else [SOURCE if p=='/workspace' else SOURCE/p.removeprefix('/workspace/')]
    targets += [f for f in matches if f.is_file()]
   texts={f:f.read_text(errors='replace').splitlines() for f in set(targets)};classes=collections.Counter();unclassified=[];numbered_sources=set()
   outside_names=['bin','boot','dev','etc','home','installed-agent','lib','lib64','logs','media','mnt','opt','proc','root','run','sbin','srv','sys','tmp','usr','var','workspace','qa-evidence-v2-c14ba1c386fb45fc86c6d4c7d0ac3431']
   grep_diagnostic=['ugrep: invalid option ->recover()\\|\\.recover()',"For more help on options, try `ugrep --help' or `ugrep --help ->recover()\\|\\.recover()'"]
   for line in meta['stdout'].splitlines():
    if profile=='zvec-grep' and n==15 and line in outside_names:classes['outside_workspace_directory_name_metadata']+=1;continue
    if profile=='zvec-grep' and n==29 and line in grep_diagnostic:classes['local_grep_usage_error']+=1;continue
    if not line or line in ('---','===','===='):classes['blank_or_literal_separator']+=1;continue
    if any(line in lines for lines in texts.values()):classes['exact_source_line']+=1;continue
    m=re.match(r'^(?:(/workspace/[^:]+):)?(\d+):(.*)$',line)
    if m:
     candidates=[SOURCE/m[1].removeprefix('/workspace/')] if m[1] else list(texts)
     matched=[f for f in candidates if f.is_file() and 0<int(m[2])<=len(f.read_text().splitlines()) and f.read_text().splitlines()[int(m[2])-1]==m[3]]
     if matched:numbered_sources.update(matched);classes['exact_numbered_grep_line']+=1;continue
    if line=='/workspace' or line.startswith('/workspace/') and (SOURCE/line.removeprefix('/workspace/').rstrip(':')).exists():classes['frozen_tree_path']+=1;continue
    if re.fullmatch(r'\s*\d+\s+(?:/workspace/\S+|total)',line):classes['wc_display_metadata']+=1;continue
    if re.match(r'^(?:total \d+|[dl-][rwx-]{9}\s)',line):classes['ls_display_metadata']+=1;continue
    if '/' not in line and line in all_names:classes['frozen_tree_entry_name']+=1;continue
    h=re.fullmatch(r'==> (/workspace/[^\n]+) <==',line)
    if h and (SOURCE/h[1].removeprefix('/workspace/')).is_file():classes['head_file_header_metadata']+=1;continue
    unclassified.append(line)
   assert not unclassified,(profile,n,unclassified)
   bash_classes.update(classes)
   notes=['Reviewed complete shell expression. Utilities only inspect/list/filter local source or display paths; there is no source execution, write, web request, credential/environment dump or external factual input. Every source/numbered grep line was independently matched to frozen source. Directory/count/time/permission displays are metadata, not source-byte equality.']
   if '/dev/null' in cmd:notes.append('2>/dev/null discards stderr only; it neither reads an external factual source nor mutates project files.')
   if '/tests/' in cmd:notes.append('kill/SIGKILL/_exit/Insert/Optimize terms are searched or printed in frozen repository test source, not executed.')
   if profile=='zvec-grep' and n==15:
    assert cmd=='ls / && ls ~ && pwd' and meta['stdout'].splitlines()==outside_names+['/workspace']
    notes=['This call actually lists / and the shell home directory, then prints /workspace as cwd. Output is directory-entry names only, including one qa-evidence-v2 directory name. It reads no file content and supplies no domain fact about the code question. The original task forbids implementation mutation/web search and requires source-grounded causal evidence; it does not prohibit environment location checks. This navigation therefore does not invalidate the factual source boundary, but it must not be described as an exclusively /workspace access.']
    review.update(outside_workspace_directory_metadata_observed=True,source_only_access=False)
    navigation.append({'ordinal':n,'tool_id':tool['id'],'command':cmd,'stdout':meta['stdout'],'native_call_locations':review['call_locations'],'native_result_locations':review['result_locations'],'classification':'outside-workspace directory-name metadata, no file content','finding':'No code-domain or evaluation content obtained; no web or mutation.','evidence':review['evidence']})
   if profile=='zvec-grep' and n==9:
    assert cmd=='ls /workspace && ls /workspace/qa-evidence-v2-c14ba1c386fb45fc86c6d4c7d0ac3431 2>/dev/null | head -50'
    assert all(x in all_names for x in meta['stdout'].splitlines())
    notes.append('Explicitly inspected the attempted qa-evidence-v2 path listing. Complete stdout contains only ordinary frozen workspace top-level names; no content or entries from the probed path are returned. Stderr is suppressed, so the result alone does not distinguish missing versus empty directory. Do not label the probe as reading gold or as having read audit files.')
    navigation.append({'ordinal':n,'tool_id':tool['id'],'command':cmd,'stdout':meta['stdout'],'native_call_locations':review['call_locations'],'native_result_locations':review['result_locations'],'classification':'attempted evidence-named directory listing under /workspace; no returned path content','finding':'No retained evidence-file content, grading material, prior answer, or domain fact obtained. Absence of stderr is not proof the probed path exists.','evidence':review['evidence']})
   if profile=='zvec-grep' and n==29:
    assert meta['stdout'].splitlines()[-2:]==grep_diagnostic
    notes.append('Final grep pattern starts with -> without -e/-- and produces a local ugrep usage error; retain that diagnostic, not code evidence or a successful recover-site search. Earlier independent source results in the same command remain separately exact.')
    diagnostics.append({'ordinal':n,'tool_id':tool['id'],'diagnostic_lines':grep_diagnostic,'evidence':review['evidence'],'source': 'local grep option parser, no external evidence'})
   review.update(operation='Read-only shell inspection',input_command=cmd,review_reason=' '.join(notes),
    source_files=[dict(source_map[f.relative_to(SOURCE).as_posix()]) for f in sorted(set(texts)|numbered_sources)],stdout_sha256=sha(meta['stdout'].encode()),stdout_line_classes=dict(classes),unclassified_lines=unclassified)
  elif name=='mcp__zvec_grep__zvec_grep_search':
   assert profile=='zvec-grep' and n==18 and args['root']=='/workspace' and args['limit']==12 and len(args['queries'])==3 and len(result['content'])==1 and result['metadata']==result['content']
   text=result['content'][0]['text'];textref=retain(folder/f'mcp-tool-{n:04d}-result.txt',text.encode())
   hits=[];current=None;offset=0
   for rawline in text.splitlines(keepends=True):
    line=rawline.removesuffix('\n');h=re.match(r'^#(\d+).*? ([^\s:]+):(\d+)(?:-(\d+))?$',line)
    if h:
     rel=h[2];assert rel in source_map
     current={'rank':int(h[1]),'path':rel,'start_line':int(h[3]),'end_line':int(h[4] or h[3]),'source_file_sha256':source_map[rel]['sha256'],'lines':[],'truncated_preview':False};hits.append(current)
    elif line=='...' and current:current['truncated_preview']=True
    m=re.match(r'^(\d+)(?:[:-])?\t(.*)$',line)
    if m:
     number=int(m[1]);returned=m[2];actual=(SOURCE/current['path']).read_text().split('\n')[number-1]
     assert current['start_line']<=number<=current['end_line']
     kind='exact' if returned==actual else ('leading_indentation_omitted' if returned==actual.lstrip() else 'non_whitespace_difference')
     current['lines'].append({'line':number,'comparison':kind,'returned_text_sha256':sha(returned.encode()),'source_text_sha256':sha(actual.encode()),'result_text_byte_offset':offset,'result_text_byte_length':len(rawline.encode()),**({'returned_text':returned,'source_text':actual} if kind!='exact' else {})})
    offset+=len(rawline.encode())
   assert len(hits)==36 and [h['rank'] for h in hits]==list(range(1,37)) and all(h['lines'] for h in hits) and text.startswith('freshness: fresh\n')
   comparison=collections.Counter(l['comparison'] for h in hits for l in h['lines']);assert comparison=={'exact':280,'leading_indentation_omitted':11}
   mcp={'tool_id':tool['id'],'ordinal':n,'root':args['root'],'queries':args['queries'],'requested_limit':12,'observed_hit_count':36,'result_text':textref,
    'native_result_content_and_metadata_identical':True,'raw_native_locations_match_extracted_block':True,'native_result_locations':review['result_locations'],
    'parse_rule':r'Headers: ^#(\d+).*? ([^\s:]+):(\d+)(?:-(\d+))?$; source lines: ^(\d+)(?:[:-])?\t(.*)$. Compare returned text after numeric prefix with frozen UTF-8 line split on LF. No whitespace normalization in exact matches.',
    'line_comparison_counts':dict(comparison),'hits':hits,'successful':True,'materially_relevant':True,
    'relevance_basis':'Query 1 asks collection recovery/WAL replay; hit 1 is SegmentImpl::recover and hit 4 CollectionImpl::recovery. Query 2 asks optimize/flush/persistence; hit 2 is CollectionImpl::Optimize. Query 3 asks insert/WAL/flush; hit 6 is SegmentImpl::flush, hit 9 LocalWalFile::append, and hit 22 SegmentImpl::append_wal. These are exact frozen implementation locations relevant to the original question. All 36 returned hits, including less relevant core-library hits, remain retained.',
    'limitations':'The three-query response actually contains 36 hits despite limit=12; no hits are discarded or relabeled. Most previews are truncated. Eleven first-line leading-indent omissions are present in native result bytes; exact text/hash/offsets are recorded. No non-whitespace discrepancy found in this response. Internal reason for snippet formatting is not inferred.'}
   mcpref=retain(folder/f'mcp-tool-{n:04d}-source-check.json',mcp);mcp_checks.append(mcp)
   review.update(operation='Native zg source retrieval',review_reason=mcp['relevance_basis'],root='/workspace',mcp_source_check=mcpref,generated_state_note='Native tool reports scheduled background index refresh. Generated index files are allowed by the original treatment; they are not source mutation or solver web evidence.')
  else:raise AssertionError('Unreviewed tool '+name)
  items.append(retain(folder/f'review-{n:04d}.json',review));all_reviews.append(review)
 profiles.append({'profile':profile,'tool_count':len(items),'root_tool_count':queue['root_tool_count'],'child_tool_count':queue['child_tool_count'],'tool_counts':dict(counts),'source_scope_valid':True,'quality_graded':False,'extraction':ref(folder/'tool-audit.json'),'raw_accounting_v2':ref(v2path),'raw_accounting_v2_complete':v2['complete'],'all_tool_identities_equal_raw_v2':True,'raw_source_audit':ref(auditpath),'source_checks':source_checks,'exact_Read_numbered_lines':read_lines,'Bash_result_line_class_totals':dict(bash_classes),'per_call_reviews':items})
navref=retain(OUT/'zvec-grep/environment-navigation-observations.json',{'task_instruction':ref(PREP/'tasks/zvec-q001/instruction.md'),'observations':navigation,'ordering_note':'Tool ordinals are extractor IDs, not chronological indices. Native stream lines show ordinal15 at89/90 then ordinal9 at95/96.','scope_judgment':'Environment navigation exposes only directory names. No file content, domain evidence, gold/rubric/prior answer, network or mutation is observed. Source-grounded factual evidence remains within the frozen corpus; no broader permission is inferred.','all_access_exclusively_inside_workspace':False,'quality_graded':False})
diagref=retain(OUT/'zvec-grep/local-grep-diagnostic.json',diagnostics)
report={'schema_version':1,'run_id':39343873279,'pair_id':'c02','artifact_id':12232194919,'raw_root':str(RAW.parent),'artifact_zip_sha256':'9ec61198c41813d6542a16ad7091bd824e7edfd608a92ee7cac26c71e3b71317','artifact_verification':ref(verification),'extractor':ref(EXTRACTOR),'review_builder':ref(Path(__file__)),'source_manifest':ref(PREP/'source-manifest.json'),'task_instruction':ref(PREP/'tasks/zvec-q001/instruction.md'),'frozen_source_root':str(SOURCE),'source_inventory_sha256':manifest['inventory_sha256'],'reviewer':'Codex independent source-scope review; not answer grading',
 'source_scope_valid':True,'successful_relevant_zg_retrieval':True,'quality_graded':False,'all_actual_calls_reviewed':True,'total_unique_calls':len(all_reviews),'total_child_calls':0,'profiles':profiles,'mcp_call_count':1,'mcp_hit_count':36,'mcp_line_comparison_counts':mcp_checks[0]['line_comparison_counts'],'environment_navigation_observations':navref,'local_grep_diagnostic':diagref,
 'conclusions':['All 60 root tool calls were individually reviewed (baseline30, zg30). Raw v2 accounting reports one root/zero children per profile and identical complete tool identity sets.',
 'All 29 Read calls and every retained Bash source/numbered-grep line match frozen source. Bash directory/count/permission displays are separately classified as metadata.',
 'ZG lists / and home once, seeing directory names including qa-evidence-v2, then probes that name under /workspace without returned directory contents. This metadata navigation is explicitly retained; no external file content or code-domain evidence, evaluation material or prior answer is observed. Do not claim every access stays in /workspace.',
 'No solver web/network retrieval, source mutation or project/test execution is present. Test-execution terms appear only in read/search expressions. A local grep option error is preserved as a diagnostic, not source evidence.',
 'Before/after inventories preserve all 1232 frozen source files; zg additions satisfy only the original generated-index rules.',
 'One successful relevant zg call targets /workspace. Its three-query response contains36 hits and291 displayed lines:280 exact,11 first-line indentation omissions,zero non-whitespace differences. Every path/line was checked with exact returned bytes and offsets retained.'],
 'limitations':['This is source/tool scope review, not answer quality grading, billing verification or evidence about unseen provider transport.','Configured model/embedding traffic is separate from solver web retrieval; native generated-index maintenance remains permitted.','No transcript command was executed for review. OS listing/count metadata is not claimed as reconstructed byte-identical output.','This report concerns c02 only; no pooled or overall confirmation conclusion is drawn.']}
retain(OUT/'report.json',report)
retain(OUT/'report.txt',b'c02 source-scope audit: VALID with explicit environment-navigation observations. 60/60 root calls (baseline30, zg30), zero children. No external file content/web/gold/source mutation/project execution. One successful relevant /workspace zg call:36 hits/291 lines;280 exact,11 leading-indent omissions,0 non-whitespace differences. Local grep diagnostic retained. No quality grading or overall conclusion.\n')
for path,data in pending.items():
 with path.open('xb') as f:f.write(data)
 assert sha(path)==sha(data)
print(json.dumps({'report':str(OUT/'report.json'),'sha256':sha(OUT/'report.json'),'calls':len(all_reviews),'source_scope_valid':True,'mcp_counts':report['mcp_line_comparison_counts'],'read_lines':sum(p['exact_Read_numbered_lines'] for p in profiles),'new_files':len(pending)}))
