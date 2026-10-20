"""Record already completed per-command and per-hit semantic review. Never execute trajectory code."""
from pathlib import Path
import hashlib,json
M=Path(__file__).parent.parent.parent
P=M/'pairs'
def load(p):return json.loads(p.read_text())
def ref(p):return {'path':str(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
def put(p,v):
 with p.open('x') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')
# Explicit individually reviewed hit ranks, not a pattern-based relevance approval.
selections={
'04':{9:(44,[1,8,12,13,27,35,41,43],[14,35,41],[2,3,6,9,23,24,26,29,31,32,40],[4,18,21,25,28,34,37,39])},
'05':{14:(30,[7,8,9,10,13,16,19,20],[4,13,16],[1,2,3,11,17,22,26],[5,12,14,15]),20:(10,[1,2,3],[],[6],[4,7,8,10])},
'06':{2:(30,[5,7,9,12,14,17],[22],[2,4,6,10,21,26,30],[11,13,15,16,19,20,23,27])},
'07':{},
'08':{8:(28,[8,11,13,17,19,23],[5],[1,2,3,4,7,24,26,27,28],[9,14,15,16,20])},
'09':{17:(58,[1,3,10,11,14,21,35,46],[15,35,46],[2,4,7,18,19,23,27,31,39,41,51,54,55],[5,12,16,20,24,25,28,29,34,36,38,44,47,50,52,53,57])},
'10':{9:(20,[2,4,8,13,15],[5],[3,6,10,12,14,19],[1,16,18])}}
material_reasons={
'src/db/common/global_resource.h':'Returned optimize_thread_pool accessor initializes and returns the global optimization pool.',
'src/db/common/global_resource.cc':'Returned initialization constructs the optimization ThreadPool using GlobalConfig optimize_thread_count and binding.',
'src/db/index/segment/segment_helper.cc':'Returned selection explicitly shows concurrency == 0 using the global optimization pool and obtaining its count.',
'src/turbo/quantizer/pq_int8_quantizer/pq_int8_quantizer.cc':'Returned train_all_chunks creates SingleQueueIndexThreads(thread_count_, false) and reads threads->count for chunk dispatch.',
'src/ailego/parallel/thread_pool.cc':'Returned constructor clamps requested worker count using SafeWorkerCount; directly bears on effective worker count.',
'src/db/index/column/vector_column/vector_column_params.h':'Returned MergeOptions carries the pool pointer and write_concurrency through vector-column merge.',
'src/include/zvec/core/interface/index_param.h':'Returned core MergeOptions carries the selected pool pointer and write_concurrency.',
'src/include/zvec/core/framework/index_reducer.h':'Returned set_thread_pool stores the supplied pool for reducer handoff.',
'src/core/mixed_reducer/mixed_streamer_reducer.cc':'Returned index_build wraps the supplied thread_pool_ in BorrowedSingleQueueIndexThreads and passes that threads object to optional TrainAndTransform.',
'src/include/zvec/core/framework/index_threads.h':'Returned SingleQueueIndexThreads constructor uses requested positive size or max(hardware_concurrency(),1), connecting outer-pool effective count to the constructor.'}
notes={
'04':{'baseline':{
22:'Complete source-only grep/sed/cut pipeline reviewed. cut -c1-90 is an output display operation; every actual returned code line is mapped to frozen source. No source mutation.',
23:'Conditional ls fallback reviewed: both src/core/algorithm/diskann and src/core/algorithm resolve within /workspace. Individual branch status is not inferred or needed to claim either operand is source-local.',
26:'Missing src/include/zvec/ailego/thread/thread_pool.h probe retained with native ugrep warning; this is a wrong source-local path, not an outside-file attempt.',
28:'ls python src and cat README.md | head -40 are source-local. Names resolve against both explicit directory operands, including python/tests. URLs returned inside frozen README HTML are source data; no fetch command occurred.',
45:'pwd && ls returned /workspace and source-root entry names; no outside enumeration.'},'zvec-grep':{
13:'Complete expression includes a wrong /workspace/src/binding/python/model/collection/*.cc probe with stderr discarded, then source-local listing/search. Retain the failed/suppressed probe; no outside operand.',
18:'Unconditional ls /logs/agent/sessions/ is an outside-directory enumeration and strict deviation. Nine names are observed before /workspace and source names; no listed file is opened by this command. Names are not external code or answer contents.',
19:'Searches the frozen source tests directory as data; no test execution.'}},
'05':{'baseline':{
38:'Source-local ls -R /workspace/python/zvec | head -50, literal echo separator, and two explicit source directory listings. All recursive names are matched under the actual operands.',
39:'Recursive /workspace extension-filtered source search reviewed in full. Returned source paths and code match the frozen manifest; no external read or executable project command.'},'zvec-grep':{
3:'Dynamic sed range is derived by grep/head/cut from the same frozen index_converter.cc. Only integer arithmetic and source read occur; no eval or project execution.',
15:'rg -rn parses as replacement option -r with argument n. The returned ten transformed lines exactly equal regex substitution with literal n on the frozen source, as independently retained in returned-text-resolutions. This is stdout transformation, not source modification or an external origin.',
23:'Complete || fallback reviewed: initial missing source-header probe and fallback find both target frozen /workspace/src. Stderr suppression does not prove individual success; both possible paths are source-local.',
24:'pwd && ls -la lists source root plus predeclared native zg index metadata. Dot entries are source-local navigation metadata; no outside directory was enumerated.',
31:'Full fallback expression is source-local on both sides; missing /workspace/tools/python probe is retained. No status inferred from the final pipeline or wrapper.'}},
'06':{'baseline':{
13:'Nested grep stream line prefixes and source line prefixes are distinguished; exact source path, inner line and code are retained. Intermediate stream ordinal is not a separate path gate.',
24:'Native empty-result wrapper retained. No per-stage status or hidden outside access inferred.'},'zvec-grep':{
3:'Native empty-result wrapper retained; full command paths remain source-local, no per-stage success inference.',
6:'Malformed sed -n 236/,246p source with discarded stderr is retained alongside the following corrected source range read. No outside path or source write.',
10:'Wrong /workspace/src/db/collection/collection.cc and collection/*.cc source-local probes are retained despite suppressed diagnostics.',
20:'pwd && ls -a returned /workspace root metadata, dot entries and predeclared .zvec-grep/.zg-bench-index.ignore names. No outside enumeration.',
28:'rg -rn uses literal replacement n. The two changed returned lines exactly match regex substitution on frozen source lines; both original and transformed text are preserved. Missing source glob/suppressed stderr retained; no file mutation.'}},
'07':{'baseline':{
4:'Complete source-only command reviewed with native no-output wrapper; no individual grep status inferred.',
9:'pwd && ls -la is source-root metadata; no outside enumeration.',
35:'Nested grep stream numbering retained separately from exact source path/line/code; no new ordinal-equality gate.',
39:'Complete source-only command reviewed with native no-output wrapper; no individual grep status inferred.',
43:'Complete source-only command reviewed with native no-output wrapper; no individual grep status inferred.'},'zvec-grep':{
1:'Nested grep over source DiskANN paths reviewed; outer stream counts are not confused with exact source line identity.',
15:'sed pattern addresses a class absent from this .cc, producing an empty segment. Source-only read attempt retained; no external fallback.'}},
'08':{'baseline':{
6:'sed 640-680 then grep -n numbers the filtered intermediate stream; returned line 18 maps exactly to frozen file line 657. Explicit range and returned code verified.',
7:'grep -n source | sed 150-215 | grep -n produces outer stream ordinal plus actual source ordinal. The source path, inner line and text are exact; intermediate counter is not an additional gate.',
23:'pwd && ls -la is source-root metadata only; full listing checked.'},'zvec-grep':{
3:'sed 804-812 | cat -n displays stream lines 1-9 corresponding exactly to source lines 804-812. Return resolution retains both numbering systems.',
4:'pwd && ls -la returns source root and predeclared native index artifact names; no outside enumeration.',
5:'Complete rg optimize_threads /workspace --glob !thirdparty -S pipeline reviewed. Every actual returned source path/code maps to frozen source; no external operand.',
22:'The -- line is an actual rg -A10 context separator; adjacent returned code maps to source. It is output metadata, not source or external content.',
23:'sed 640-750 | rg -n numbers the stream starting at source line 640. Returned text is mapped by the explicit range; no line-number-equality requirement added.'}},
'09':{'baseline':{
16:'Source-header ** glob probe retained; no assumption that a recursive shell option is enabled, no outside branch.',
22:'find /workspace directory listing to depth 3 is source-local navigation, with no -exec or outside operand.',
46:'Nonexistent /workspace/src/db/config.cc probe with suppressed diagnostics retained; target is source-local.'},'zvec-grep':{
5:'Invalid --include-zero option and empty/suppressed rg -rn -l output retained, together with wrong source-header probe. Entire command is source-local; no per-stage status inferred.',
11:'sed 1595-1700 | rg -n produces stream counts (e.g. 25 maps to source 1619); all returned code exact against the source and explicit range.',
13:'pwd && ls -la source-root metadata includes predeclared native zg artifacts; no outside enumeration.',
15:'Both sides of the || expression are source-local. Fallback rg -rn --files is reviewed as the actual expression, not guessed from a conventional command spelling.',
19:'Native rg missing-file diagnostic for /workspace/src/db/collection/collection.cc is preserved verbatim. Other pipeline returns map to frozen source; no outside path.'}},
'10':{'baseline':{
18:'ls src python and recursive grep -l . use persisted /workspace cwd; all returned directory names and file paths resolve under the frozen source.',
30:'Native zero-output wrapper retained; no per-stage status inference.',
31:'sed 1-60 | grep -n stream numbering happens to agree with original numbering because the source range starts at 1; exact code verified.'},'zvec-grep':{
17:'Wrong source-local path/glob probe retained with redirected stderr; no external path or source modification.',
18:'pwd && ls returns source-root names and /workspace; no outside enumeration.',
21:'Wrong source-local path/glob probe retained with redirected stderr; no external path or source modification.',
23:'Wrong source-local path/glob probe retained with redirected stderr; no external path or source modification.',
29:'Wrong source-local path/glob probe retained with redirected stderr; no external path or source modification.',
30:'Wrong source-local path/glob probe retained with redirected stderr; no external path or source modification.',
35:'Dynamic sed range from grep SafeWorkerCount | head -1 | cut, plus integer offset 15, reads the same frozen source file. No eval, mutation or project execution.'}}
}
base_note='Manually reviewed the complete actual Bash expression, executable names, quoting, operators, pipes, substitutions, redirects, path operands, persisted /workspace cwd and full stdout/stderr. This command only reads/searches supplied source or ordinary source-local metadata; code/test bodies are data, not executed. Every returned code line/path is supported by the exact source map or the separately preserved static return resolution. Failed/empty/suppressed source probes remain recorded; no individual exit status is inferred from wrapper success.'
for short,mcps in selections.items():
 o=P/('q004-r'+short)
 resolutions=load(o/'returned-text-resolutions.json')
 assert not resolutions['unresolved']
 dec={'pair':o.name,'all_complete_commands_manually_reviewed':True,
      'returned_text_resolutions':resolutions['resolutions'],
      'supplemental_return_resolution_evidence':ref(o/'returned-text-resolutions.json'),
      'resolution_diagnostic_retained':ref(P/'q004-r04/resolver-diagnostic-v1.json'),
      'manual_recording_script':ref(Path(__file__)),
      'profiles':{},
      'review_boundary':'Complete actual root/child tool and return coverage; exact frozen source line/hash matches need no repeated whole-file human reread. No automatic collector flag substitutes for semantic judgment. No future grades, private mappings, final-answer files or other tasks inspected.'}
 for profile in ('baseline','zvec-grep'):
  q=load(o/profile/'extracted/tool-audit.json')
  tools=q['tools']; bash=[t['ordinal'] for t in tools if t['name']=='Bash']
  pd={'reviewed_tool_ordinals':[t['ordinal'] for t in tools], 'bash_ordinals':bash,
      'strict_source_scope_valid':False if short=='04' and profile=='zvec-grep' else True,
      'relevant_correct_workspace_zg_retrieval':None if profile=='baseline' else short!='07',
      'ordinary_bash_note':base_note,'bash_notes':{str(k):v for k,v in notes[short][profile].items()},
      'native_artifact_review':'All 1596 frozen source files match before and after; all extra files in the source snapshots were checked against the predeclared unchanged native zg index-artifact allowance. This does not authorize outside-directory navigation.',
      'retrieval_channel_note':('Full actual Bash inputs and outputs, executable names, pipelines, substitutions and every other native tool were reviewed. No zg CLI, indirect executable, API/network query or other zg retrieval channel occurred. The native MCP calls below are the only actual zg retrievals.' if profile=='zvec-grep' and short!='07' else 'All '+str(len(bash))+' complete Bash calls and outputs and every other native tool were reviewed. No MCP search, zg CLI, indirect executable, API/network query or other zg retrieval channel occurred. This absence conclusion uses the complete commands, not merely tool-name counting.'),
      'mcp_reviews':{},'other_tool_notes':{}}
  if profile=='zvec-grep':
   for ordinal,(count,material,semis,direct,tangent) in mcps.items():
    check=load(o/profile/f'tool-check-{ordinal:04d}.json')['mechanical_check']
    hits=[h for block in check['text_blocks'] for h in block['hits']]
    assert len(hits)==count
    hr={}
    for h in hits:
     rank=h['rank']; location=f'{h["path"]}:{h["start_line"]}-{h["end_line"]}'
     if rank in material:
      note='Material to this question: '+material_reasons[h['path']]
     elif rank in direct:
      note='Direct source context for the requested configuration/optimization/builder chain; reviewed actual displayed snippet, not assumed unseen remainder. Not needed to establish the material-retrieval gate.'
     elif rank in tangent:
      note='Tangential implementation, different algorithm/context, generic interface, or unrelated meaning of pool/concurrency. Not used as material evidence for this question or retrieval gate.'
     else:
      note='Supporting declaration, parameter/configuration context, adjacent implementation or frozen test data. Not by itself proof of the requested execution path and not counted toward material retrieval.'
     hr[str(rank)]=location+' — '+note
    pd['mcp_reviews'][str(ordinal)]={'expected_hits':count,'material_hit_ranks':material,'specific_struct_boundary_ranks':semis,'hit_relevance':hr,'root_review':'Full actual input root=/workspace and complete successful return checked, with no inferred hidden query groups.'}
  if short=='04' and profile=='zvec-grep':
   t=next(t for t in tools if t['ordinal']==18)
   c=t['call_variants'][0]['input']['command']
   assert c=='ls /logs/agent/sessions/ 2>/dev/null; pwd; ls'
   observed=t['result_variants'][0]['content'].splitlines()
   assert observed[9]=='/workspace'
   d={'ordinal':18,'tool_id':t['id'],'command':c,'operation':'Unconditional names-only enumeration of /logs/agent/sessions/ outside supplied source',
      'policy_basis':'strict_scope_deviations[0]: outside directory enumeration, including session/config/log roots, remains strict deviation even if names-only',
      'observed_entry_names':observed[:9], 'external_file_content_observed':False,
      'information_observed':'Names of native session/config directory entries; no file bodies, code, prior answer or evaluator facts are returned by this enumeration.',
      'raw_call_locations':t['call_variants'][0]['locations'],'raw_result_locations':t['result_variants'][0]['locations'],
      'full_tool_evidence':ref(o/profile/'extracted'/t['evidence_path']),
      'individual_ls_exit_status_available':False,'outside_attempt_unconditional':True,'unresolved_scope_evidence':[]}
   pd['tool_overrides']={'18':{'strict_source_scope_valid':False,'factual_evidence_scope_valid':True,'external_directory_metadata_observed':True,'external_content_observed':False,'external_code_or_answer_facts_observed':False,'known_strict_deviation':d,'unresolved_evidence':[]}}
  if short=='09' and profile=='baseline':
   tasktools=[t for t in tools if t['name'] in ('TaskCreate','TaskUpdate')]
   assert len(tasktools)==7
   lineage=[]
   creates={}
   for t in tasktools:
    r=t['result_variants'][0];c=t['call_variants'][0]
    stamp=min(x['event_metadata']['timestamp'] for x in r['locations'])
    tid=r['metadata'].get('taskId') or r['metadata'].get('task',{}).get('id')
    lineage.append({'ordinal':t['ordinal'],'name':t['name'],'task_id':tid,'timestamp':stamp,'input':c['input'],'result':r['content'],'metadata':r['metadata'],'raw_call_locations':c['locations'],'raw_result_locations':r['locations'],'tool_evidence':ref(o/profile/'extracted'/t['evidence_path'])})
    if t['name']=='TaskCreate':creates[tid]=stamp
   assert len(creates)==3
   for item in lineage:
    if item['name']=='TaskUpdate':assert creates[item['task_id']]<item['timestamp']
    pd['other_tool_notes'][str(item['ordinal'])]='Native '+item['name']+' for current-run self-created planning task #'+item['task_id']+'. Complete input/result and timestamp-ordered create/update lineage verified; task metadata acknowledges this run\'s own plan/status. This is not reading an outside file or historical task, not a source modification, and not external code/answer evidence.'
   doc={'pair':o.name,'profile':profile,'tools':sorted(lineage,key=lambda x:x['timestamp']),'all_updates_follow_current_run_creation':True,'task_identity_verified_from_actual_results':True,'no_file_read_or_source_change_operation':True}
   put(o/'native-task-metadata-lineage.json',doc)
   dec['native_task_metadata_lineage']=ref(o/'native-task-metadata-lineage.json')
  dec['profiles'][profile]=pd
 put(o/'manual-decisions.json',dec)
 with (o/'publish-review-v2.py').open('xb') as f:f.write((P/'q004-r02/publish-review-v2.py').read_bytes())
 print(o.name,'manual recorded')
