from pathlib import Path
import json,hashlib
O=Path(__file__).parent;B=O.parents[2];Q=B/'native-zvec-q001-repeat-v1-audit/scope'/O.name
# B is the main-agent-pilot root.
assert B.name=='main-agent-pilot'
details=[]
for pr in ('baseline','zvec-grep'):
 q=json.loads((Q/pr/'tool-audit.json').read_text());a=Path(q['agent_directory']);actual_paths=[a/'claude-code.txt',*sorted((a/'sessions/projects').rglob('*.jsonl'))];assert {p.relative_to(a).as_posix() for p in actual_paths}=={f['path'] for f in q['files']}
 occurrences=set();expected=set()
 for p in actual_paths:
  data=p.read_bytes();fp=p.relative_to(a).as_posix();row=next(x for x in q['files'] if x['path']==fp);assert hashlib.sha256(data).hexdigest()==row['sha256'] and len(data)==row['bytes']
  for n,raw in enumerate(data.splitlines(keepends=True),1):
   event=json.loads(raw)
   if event.get('type') not in ('assistant','user'):continue
   content=event.get('message',{}).get('content',[])
   if not isinstance(content,list):continue
   for i,block in enumerate(content):
    if isinstance(block,dict) and block.get('type') in ('tool_use','tool_result'):occurrences.add((fp,n,i,hashlib.sha256(raw).hexdigest()))
 for t in q['tools']:
  assert json.loads((Q/pr/t['evidence_path']).read_text())==t
  for kind in ('call_variants','result_variants'):
   for v in t[kind]:
    for l in v['locations']:expected.add((l['file'],l['line'],l['block_index'],l['raw_line_sha256']))
 assert expected==occurrences
 details.append({'profile':pr,'raw_files':len(actual_paths),'native_tool_occurrences':len(occurrences),'extracted_occurrences':len(expected),'full_file_inventory_exact':True,'all_per_tool_files_equal_queue':True,'exhaustive_raw_tool_occurrences_exact':True})
with (O/'extraction-crosscheck.json').open('x') as f:json.dump({'pair_id':O.name,'all_pass':True,'profiles':details,'does_not_assess_answer_quality':True},f,indent=2);f.write('\n')
print(json.dumps(details))
