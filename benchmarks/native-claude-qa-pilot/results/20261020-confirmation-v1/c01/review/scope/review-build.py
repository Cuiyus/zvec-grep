"""Materialize the completed per-call scope review and deterministic source comparisons.
No transcript command is executed; source bytes are only read with pathlib.
"""
from pathlib import Path, PurePosixPath
import collections, hashlib, json, re

BASE=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
OUT=Path(__file__).parent
RAW=BASE/'native-claude-confirmation-v1-ci/c01/raw/live'
PREP=BASE/'native-claude-live-preflight-v1/prepared'
SOURCE=PREP/'tasks/zvec-q001/environment/source'
EXTRACTOR=BASE/'native-claude-live-scope-audit-v1/extract.py'
def sha(value):return hashlib.sha256(value if isinstance(value,bytes) else value.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_text())
def write(path,data):
 with path.open('x') as f:json.dump(data,f,indent=2,ensure_ascii=False,sort_keys=True);f.write('\n')
def canonical(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def ref(path):return {'path':str(path),'sha256':sha(path)}
manifest=load(PREP/'source-manifest.json')
source_map={r['path']:r for r in manifest['files']}
assert len(source_map)==1232 and manifest['inventory_sha256']=='741d06d8fc216150933c5ed87f9a100e0d2120b2dc09a9109084365e92d307ba'
assert all(sha(SOURCE/p)==r['sha256'] and (SOURCE/p).stat().st_size==r['bytes'] for p,r in source_map.items())
collection=load(RAW/'collection/collection.json')
all_names={p.name for p in SOURCE.rglob('*')}
all_reviews=[];profiles=[];mcp_checks=[]
# Every input/complete result was reviewed before this builder: these exact
# tool categories/ordinals are the inspected call inventory, not heuristic approval.
reviewed={'baseline':set(range(1,48)),'zvec-grep':set(range(1,40))}
for profile in ('baseline','zvec-grep'):
 folder=OUT/profile;queue=load(folder/'tool-audit.json');v2path=RAW/'collection'/f'{profile}-strict-accounting.json';v2=load(v2path)
 row=next(r for r in collection['profiles'] if r['profile']==profile);trial=RAW/row['trial_directory'];agent=trial/'agent'
 assert queue['extraction_complete'] and not queue['errors'] and v2['complete'] and v2['child_count']==0
 assert {t['ordinal'] for t in queue['tools']}==reviewed[profile]
 assert {(t['session'],t['id']):t['frozen_accounting_identity'] for t in queue['tools']}=={(t['session'],t['id']):t for t in v2['tools']}
 source_audit=load(trial/'qa-source-audit.json');source_checks=[]
 for phase in ('before','after'):
  files=source_audit[phase]['observed']['files'];actual={r['path']:r for r in files};assert len(actual)==len(files)
  unchanged=all(actual.get(p,{}).get('kind')=='file' and all(actual[p].get(k)==r[k] for k in ('bytes','sha256')) for p,r in source_map.items())
  additions=[r for p,r in actual.items() if p not in source_map]
  allowed=all(profile=='zvec-grep' and r['kind']=='file' and (r['path'].startswith('.zvec-grep/') or r['path']=='.zg-bench-index.ignore') for r in additions)
  assert unchanged and allowed
  source_checks.append({'phase':phase,'all_1232_source_files_unchanged':unchanged,'additional_files':len(additions),'all_additions_within_original_generated_rules':allowed})
 counts=collections.Counter();read_lines=0;bash_classes=collections.Counter();items=[]
 for tool in queue['tools']:
  n=tool['ordinal'];call=tool['call_variants'][0];result=tool['result_variants'][0];args=call['input'];name=tool['name'];counts[name]+=1
  assert len(tool['call_variants'])==len(tool['result_variants'])==1 and not result['is_error'] and tool['identity_matches_frozen_accounting']
  # Bind extraction to every original JSONL occurrence, rather than trusting only the exported queue.
  for variant in (call,result):
   for loc in variant['locations']:
    data=(agent/loc['file']).read_bytes();rawline=data.splitlines(keepends=True)[loc['line']-1]
    assert sha(data)==loc['file_sha256'] and sha(rawline)==loc['raw_line_sha256']
    assert data[loc['byte_offset']:loc['byte_offset']+loc['byte_length']]==rawline
    event=json.loads(rawline);assert event['message']['content'][loc['block_index']]==variant['block']
  review={'ordinal':n,'session':tool['session'],'tool_id':tool['id'],'name':name,
    'evidence':ref(folder/tool['evidence_path']),'input_sha256':call['input_sha256'],'result_block_sha256':result['block_sha256'],
    'call_locations':[{k:l[k] for k in ('file','line','byte_offset','byte_length','raw_line_sha256','file_sha256','block_index')} for l in call['locations']],
    'result_locations':[{k:l[k] for k in ('file','line','byte_offset','byte_length','raw_line_sha256','file_sha256','block_index')} for l in result['locations']],
    'source_only':True,'solver_web_or_network_request':False,'solver_source_mutation':False,
    'project_code_or_tests_executed':False,'external_factual_evidence':False,'gold_or_prior_answer_access':False,
    'scope_valid':True,'quality_graded':False}
  if name=='Read':
   path=args['file_path'];assert path.startswith('/workspace/') and '..' not in PurePosixPath(path).parts
   rel=path.removeprefix('/workspace/');assert rel in source_map;f=result['metadata']['file'];lines=(SOURCE/rel).read_text().split('\n')
   assert f['filePath']==path and f['totalLines']==len(lines) and f['startLine']==args.get('offset',1)
   assert f['content']=='\n'.join(lines[f['startLine']-1:f['startLine']-1+f['numLines']])
   returned=[re.fullmatch(r'(\d+)\t(.*)',x) for x in result['content'].splitlines()]
   assert all(returned) and len(returned)==f['numLines']
   assert all(m[2]==lines[int(m[1])-1] for m in returned)
   read_lines+=len(returned)
   review.update(operation='Read frozen source slice',review_reason='Exact /workspace source file; all returned numbered lines and native file.content match the frozen source slice. No execution or mutation.',
     source_files=[dict(source_map[rel])],returned_lines=len(returned),returned_numbered_text_exact=True,metadata_content_exact=True)
  elif name=='Bash':
   cmd=args['command'];meta=result['metadata'];assert result['content']==meta['stdout'] and meta['stderr']=='' and meta['interrupted'] is False
   paths=re.findall(r'/workspace(?:/[A-Za-z0-9_.?*+/-]+)?',cmd);targets=[]
   for p in paths:
    assert '..' not in PurePosixPath(p).parts
    matches=list(SOURCE.glob(p[len('/workspace/'):])) if any(x in p for x in '*?') else [SOURCE if p=='/workspace' else SOURCE/p.removeprefix('/workspace/')]
    targets += [f for f in matches if f.is_file()]
   texts={f:f.read_text(errors='replace').splitlines() for f in set(targets)};classes=collections.Counter();unclassified=[]
   for line in meta['stdout'].splitlines():
    if not line or re.fullmatch(r'[-=A-Z_]+',line):classes['blank_or_literal_separator']+=1;continue
    if any(line in lines for lines in texts.values()):classes['exact_source_line']+=1;continue
    m=re.match(r'^(?:(/workspace/[^:]+):)?(\d+):(.*)$',line)
    if m:
     candidates=[SOURCE/m[1].removeprefix('/workspace/')] if m[1] else list(texts)
     if any(f.is_file() and 0<int(m[2])<=len(f.read_text().splitlines()) and f.read_text().splitlines()[int(m[2])-1]==m[3] for f in candidates):classes['exact_numbered_grep_line']+=1;continue
    if line=='/workspace' or line.startswith('/workspace/') and (SOURCE/line.removeprefix('/workspace/').rstrip(':')).exists():classes['frozen_tree_path']+=1;continue
    if re.fullmatch(r'\s*\d+\s+(?:/workspace/\S+|total)',line):classes['wc_display_metadata']+=1;continue
    if re.match(r'^(?:total \d+|[dl-][rwx-]{9}\s)',line):classes['ls_display_metadata']+=1;continue
    if '/' not in line and line in all_names:classes['frozen_tree_entry_name']+=1;continue
    unclassified.append(line)
   assert not unclassified
   bash_classes.update(classes)
   notes=['Reviewed complete shell expression: only source reading/listing/filtering utilities and literal separators. Returned content is the retained stdout; stderr is empty. Source and numbered grep lines were independently matched; directory/count/time/permission displays are metadata, not claimed as source-byte equality.']
   if '/dev/null' in cmd:notes.append('2>/dev/null only discards stderr; it does not read outside-workspace evidence or write project files.')
   if '$(' in cmd:notes.append('Command substitution contains only grep of a frozen source file piped to cut; its output supplies the sed line range. No source code is executed.')
   if '/tests/' in cmd:notes.append('Repository test files are part of the frozen source corpus; kill/fork/exec/system terms are searched or printed, not executed.')
   if meta['stdout']=='===':notes.append('The selected file_helper.cc searches returned no source text; the only output is a literal separator, so this call supplies no code evidence.')
   review.update(operation='Read-only shell inspection',input_command=cmd,review_reason=' '.join(notes),
     source_files=[dict(source_map[f.relative_to(SOURCE).as_posix()]) for f in sorted(texts)],
     stdout_sha256=sha(meta['stdout'].encode()),stdout_line_classes=dict(classes),unclassified_lines=unclassified)
  elif name=='mcp__zvec_grep__zvec_grep_search':
   assert args['root']=='/workspace' and args['limit']==15 and len(result['content'])==1 and result['metadata']==result['content']
   text=result['content'][0]['text'];textpath=folder/f'mcp-tool-{n:04d}-result.txt'
   with textpath.open('xb') as f:f.write(text.encode())
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
     if kind=='non_whitespace_difference':assert n==33 and current['rank']==7 and number==34 and returned=='}' and actual=='};'
     current['lines'].append({'line':number,'comparison':kind,'returned_text_sha256':sha(returned.encode()),'source_text_sha256':sha(actual.encode()),
       'result_text_byte_offset':offset,'result_text_byte_length':len(rawline.encode()),
       **({'returned_text':returned,'source_text':actual} if kind!='exact' else {})})
    offset+=len(rawline.encode())
   assert len(hits)==15 and all(h['lines'] for h in hits) and text.startswith('freshness: fresh\n')
   comparison=collections.Counter(l['comparison'] for h in hits for l in h['lines'])
   mcp={'tool_id':tool['id'],'ordinal':n,'root':args['root'],'query':args['query'],'result_text':ref(textpath),
     'native_result_content_and_metadata_identical':True,'raw_native_locations_match_extracted_block':True,
     'parse_rule':r'Headers: ^#(\d+).*? ([^\s:]+):(\d+)(?:-(\d+))?$; source lines: ^(\d+)(?:[:-])?\t(.*)$. Compare returned text after numeric prefix with frozen UTF-8 source line after splitting LF. No whitespace/semicolon normalization is applied to exact matches.',
     'line_comparison_counts':dict(comparison),'hits':hits,'successful':True,'materially_relevant':True,
     'relevance_basis':'Both queries request WAL/recovery/flush/optimize lifecycle. Returned frozen src/db/index/segment/segment.cc flush and open_wal_file plus src/db/collection.cc recovery directly locate the requested implementation; second query also returns CollectionImpl::Optimize. Some other hits are less relevant and are retained without selective omission.',
     'limitations':'Short previews are truncated. Ten leading-indentation differences and one missing closing semicolon across the two responses prevent any full-text byte-exact claim. These are present in native tool_result bytes, not introduced by extraction or comparison.'}
   write(folder/f'mcp-tool-{n:04d}-source-check.json',mcp);mcp_checks.append(mcp)
   review.update(operation='Native zg source retrieval',review_reason=mcp['relevance_basis'],
     root='/workspace',query=args['query'],mcp_source_check=ref(folder/f'mcp-tool-{n:04d}-source-check.json'),
     generated_state_note='Native result reports a scheduled index refresh on the first call. Generated .zvec-grep files are allowed by the existing treatment rules; this is not source mutation or web evidence.')
  else:raise AssertionError('Unreviewed tool '+name)
  itempath=folder/f'review-{n:04d}.json';write(itempath,review);items.append(ref(itempath));all_reviews.append(review)
 profiles.append({'profile':profile,'tool_count':len(items),'root_tool_count':queue['root_tool_count'],'child_tool_count':queue['child_tool_count'],
  'tool_counts':dict(counts),'source_scope_valid':True,'quality_graded':False,'extraction':ref(folder/'tool-audit.json'),
  'raw_accounting_v2':ref(v2path),'raw_accounting_v2_complete':v2['complete'],'all_tool_identities_equal_raw_v2':True,
  'raw_source_audit':ref(trial/'qa-source-audit.json'),'source_checks':source_checks,'exact_Read_numbered_lines':read_lines,
  'Bash_result_line_class_totals':dict(bash_classes),'per_call_reviews':items})
# The punctuation discrepancy is preserved independently, with full raw text and native locations.
tool=load(OUT/'zvec-grep/tools/tool-0033.json');result=tool['result_variants'][0]
source_file=SOURCE/'src/db/index/storage/wal/local_wal_file.h';context=b''.join(source_file.read_bytes().splitlines(keepends=True)[28:36])
context_path=OUT/'zvec-grep/local_wal_file.h-L29-L36.txt'
with context_path.open('xb') as f:f.write(context)
mcp=next(x for x in mcp_checks if x['ordinal']==33);hit=next(h for h in mcp['hits'] if h['rank']==7);difference=next(l for l in hit['lines'] if l['line']==34)
detail={'classification':'Native zg returned snippet differs from the frozen full source line; not byte-exact.',
 'source_file':ref(source_file),'source_context_lines':[29,36],'source_context':ref(context_path),'mcp_result_text':mcp['result_text'],
 'difference':difference,'tool_evidence':ref(OUT/'zvec-grep/tools/tool-0033.json'),
 'native_result_locations':[{k:l[k] for k in ('file','line','byte_offset','byte_length','raw_line_sha256','file_sha256','block_index')} for l in result['locations']],
 'agent_directory':str(RAW/'runs/q001-zvec-grep/zvec-q001__bwyWHsA/agent'),
 'attribution':'Both original stdout and persisted session JSONL contain the same result block with line 34 rendered as } rather than };. The unchanged extractor copied that block exactly; the parser only removed the numeric line prefix. The cause inside zg chunk construction is not established by this audit.',
 'scope_and_relevance':'This is the end of the WalHeader declaration, a secondary search hit. It remains the correct frozen path and line; it is recorded as a punctuation omission, not silently normalized. Other exact flush/recovery/open_wal_file hits independently establish successful relevant in-scope retrieval. No answer quality judgment is made.'}
write(OUT/'zvec-grep/non-whitespace-snippet-difference.json',detail)
report={'schema_version':1,'run_id':39343873279,'pair_id':'c01','artifact_id':12231279241,
 'raw_root':str(RAW.parent),'artifact_zip_sha256':'0a8272626ba3053fd00a5f489c27bf94998e7cfeaffc0da8667acca3155b89e2',
 'artifact_verification':ref(BASE/'native-claude-confirmation-v1-ci/c01/artifact-byte-verification.json'),
 'extractor':ref(EXTRACTOR),'review_builder':ref(Path(__file__)),'source_manifest':ref(PREP/'source-manifest.json'),
 'frozen_source_root':str(SOURCE),'source_inventory_sha256':manifest['inventory_sha256'],
 'reviewer':'Codex independent source-scope review; not answer grading',
 'source_scope_valid':True,'successful_relevant_zg_retrieval':True,'quality_graded':False,
 'all_actual_calls_reviewed':True,'total_unique_calls':len(all_reviews),'total_child_calls':0,'profiles':profiles,
 'mcp_call_count':len(mcp_checks),'mcp_hit_count':sum(len(x['hits']) for x in mcp_checks),
 'mcp_line_comparison_counts':dict(sum((collections.Counter(x['line_comparison_counts']) for x in mcp_checks),collections.Counter())),
 'mcp_non_whitespace_difference':ref(OUT/'zvec-grep/non-whitespace-snippet-difference.json'),
 'conclusions':['All 86 retained root tool calls were reviewed individually; raw v2 accounting reports one root and zero children for each profile, with identical tool identity sets.',
  'No solver-issued web/network retrieval, external factual source, gold/prior-answer access, project/test execution or source mutation was found. Shell uses are local inspection; test execution terms occur only within printed/searched source.',
  'Before/after raw inventories retain all 1232 frozen source files; treatment additions obey only original generated-index rules.',
  'Both actual zg calls target /workspace and return successful relevant frozen-source hits; all 30 paths and 241 displayed line references were checked.',
  'MCP text is not universally byte-exact: 230 lines match exactly, 10 omit leading indentation, and one native snippet omits a closing semicolon. Exact text, offsets, source context and raw native locations are retained.'],
 'limitations':['This audit concerns retained solver tool/source scope; it is not quality grading, a billing audit or proof about unseen provider transport.',
  'Configured model/embedding service traffic is separate from solver web retrieval. Native generated-index maintenance remains allowed under the original treatment rules.',
  'Bash source/grep text was checked against frozen source. Directory ordering, timestamps, owner/permission and wc displays are inspection metadata, not claimed as reconstructed byte-identical OS output.',
  'Only c01 is concluded here; c02 remains separate and no overall confirmation outcome is inferred.']}
write(OUT/'report.json',report)
(OUT/'report.txt').write_text('c01 source-scope audit: VALID. 86/86 calls (baseline47, zg39), zero child calls; no web/external/gold/source mutation or project execution. Both /workspace zg calls relevant. 30 hits /241 displayed lines:230 exact,10 leading-indent omissions,1 native missing semicolon (explicitly retained). No quality grading. See report.json and per-call review files.\n')
print(json.dumps({'report':str(OUT/'report.json'),'sha256':sha(OUT/'report.json'),'source_scope_valid':True,'calls':len(all_reviews),'mcp_line_comparison_counts':report['mcp_line_comparison_counts'],'non_whitespace_detail_sha256':sha(OUT/'zvec-grep/non-whitespace-snippet-difference.json')}))
