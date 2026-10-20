"""Publish independent q004 source/tool-scope audit; no solver commands or grading."""
from pathlib import Path
import json,hashlib,collections,re,subprocess
O=Path(__file__).parent; B=O.parents[1]
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
P=B/'native-architecture-q004-preparation-v1/prepared/q004';S=P/'tasks/zvec-q004/environment/source'
A=B/'native-q004-live-v1-ci/39375703707/pilot';R=A/'raw';Q=O/'extracted'
def load(p):return json.loads(p.read_text())
def sha(x):return hashlib.sha256(x.read_bytes() if isinstance(x,Path) else x).hexdigest()
def ref(p):return {'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
def dump(p,x):
 with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
policy=W/'benchmarks/native-claude-qa-pilot/repeat-scope-policy-v1.json'; assert sha(policy)=='2233de3a9413bbad6c8af4a977c4da406213ac25674bb7b38e90b08fb4ac8d06'
decision=W/'benchmarks/native-claude-qa-pilot/architecture-execution-decision-v1.json';assert decision.read_bytes()==(R/'architecture-execution-decision-v1.json').read_bytes()
d=load(O/'source-checks-v3.json');assert d['tool_count']==86 and not d['issues']
m=load(P/'source-manifest.json');assert m['source_commit']=='1a573058b24e11cdb7e91f27d6638c5f1c0cdcaa' and len(m['files'])==1596
assert {p.relative_to(S).as_posix() for p in S.rglob('*') if p.is_file()}=={x['path'] for x in m['files']}
assert not any(p.is_symlink() for p in S.rglob('*'))
file_refs=[]
for x in m['files']:
 p=S/x['path'];r=ref(p);assert r['sha256']==x['sha256'] and r['bytes']==x['bytes'];file_refs.append(r)
dump(O/'source-file-verification.json',{'source_commit':m['source_commit'],'source_inventory_sha256':m['inventory_sha256'],'file_count':1596,'complete_inventory_equal':True,'all_bytes_and_hashes_equal':True,'symlinks':[],'source_files':file_refs})
notes={
'baseline':{
1:'Both find operations are confined to the two explicit ailego source directories. Command substitution selects thread_pool.h for sed; only the frozen src/include/zvec/ailego/parallel/thread_pool.h matches. No find -exec, shell evaluation of source text, or write. Returned first 120 source lines match.',
2:'Source-local recursive grep for IndexBuilder and ls of the framework directory. head bounds each output; literal === separates them.',
3:'Source-local recursive grep for optimization pool fields, followed by text filtering and head. No source execution.',
7:'All three find roots are inside /workspace. The third find uses -o between type/name predicates; no action escapes those supplied roots. && affects only further source listings and echo. Python test filenames are metadata, not test execution.',
8:'grep and fixed sed range inspect builder/converter headers; train/build words are patterns or returned source text.',
11:'grep searches runner source; sed reads builder header. Returned declarations and comments remain source content.',
12:'Two source-local declaration searches. Matches in other builders are ordinary frozen source, not executed implementations.',
15:'Source-only filenames and numbered matches identify reducer setters. No dynamically invoked methods.',
17:'Single frozen DiskANN source grep with head; no training/build invocation.',
18:'Read-only quantizer header grep plus source-wide converter symbol search.',
19:'Two fixed sed slices, core MergeOptions and quantizer training source. Thread creation calls shown as source text only.',
20:'Two fixed sed slices inspect task declaration and handler source. No task execution.',
23:'All grep operands are frozen source paths. 2>/dev/null suppresses diagnostics for the final search; no file contents read from /dev/null. Python stub match verified.',
27:'Recursive source and Python grep emits frozen file names only, including tests. No tests run.',
29:'Source-local binding grep and frozen Python glob. All paths and returned lines match.',
30:'Two header searches return only the echo separator. Preserve lack of matches/output; is_error=false does not establish each grep exit status. Both possible searches remain source-only.',
31:'Source-local filename search plus ls of DiskANN source directory; names verified against frozen tree.',
34:'Source-local grep, fixed configuration sed slice, then configuration count grep. No initialize invocation despite searched symbols.',
35:'Recursive source-only write_concurrency symbol search; source writing is not requested.',
36:'pwd returns /workspace and persisted call cwd is /workspace; && ls -la lists that directory only. Dot and parent-dot entries are incidental source-directory metadata explicitly allowed by policy. No enumeration of parent directory.',
39:'Two explicit frozen DiskANN files searched for builder count; all numbered lines verified.',
41:'Single frozen PQ implementation searched for pool/chunk symbols. Source functions are not invoked.',
42:'Recursive db-source grep with textual exclusion of test matches; entirely source-only.',
44:'Header grep and fixed converter/Python binding sed ranges; full expression is read-only.',
45:'Source and Python recursive grep includes test source content. Returned zvec.init examples/tests are not executed.',
46:'Source headers and Python binding grep; numbered lines match.',
47:'Fixed configuration sed slice; suppressed grep against collection/*.cc and *.h whose source-local directory does not exist in frozen tree; then source model-directory ls. Empty suppressed segment retained without asserting its command status or successful match. No outside fallback.',
48:'Source-only ThreadPool header/implementation grep; constructor and worker code appears only as returned text.',
50:'Source-only options header grep.',
52:'Source-only recursive setter search.',
55:'Source-only recursive quantizer filename search.'},
'zvec-grep':{
1:'All seven grep searches use explicit frozen src paths; pipes/head and separators process text. Suppressed diagnostics for a source header are retained. No executable training or pool creation.',
3:'Source-only grep, fixed collection sed range, and compact-task declaration grep. Empty first segment retained; no outside fallback.',
8:'Source-only reducer implementation/header grep and reducer directory ls. No thread_pool assignment execution.',
9:'Source-only setter search and fixed builder/options sed ranges; signatures/text match.',
11:'Two independent source-only grep pipelines separated by literal echo. First operands are DiskANN *.cc/*.h under /workspace; 2>/dev/null discards stderr. Second grep -in numbers its incoming text stream, then head filters. Returned inner source location diskann_builder.cc:379 and entire text match. Outer 71 is stream metadata; ordinary sorted-glob static reconstruction gives 22, and intermediate stream/order/locale is not retained. This unresolved ordering diagnostic does not identify any unknown path or read operation: every operand is frozen source, no input redirection, eval, substitutions, outside fallback or external reader exists. No additional exact-stream-ordinal policy imposed.',
13:'Fixed ThreadPool/config slices and source-local symbol searches, including a Python glob. 2>/dev/null suppresses diagnostics only; final grep reports a frozen filename.',
14:'Three fixed source slices inspect merge forwarding and converter signatures. No source execution.',
15:'Source-only ReduceVectorIndex/compact searches and fixed task/collection slices.',
16:'pwd and persisted cwd both /workspace; ls -la is source-local. Dot-parent entry is allowed incidental metadata; .zvec-grep and .zg-bench-index.ignore are predeclared native generated artifact names. No external listing or content read.',
17:'Source/tests symbol search and runner/segment grep. Test source is searched, never executed.',
18:'Source-only builder/converter/Python binding searches. Empty declaration search segments preserved without invented successful matches.',
19:'Fixed builder/reducer/binding slices and source-only searches; absence of non-helper matches preserved. Setters/builders displayed as source text only.',
24:'Source-only converter symbol search, DiskANN filenames, and DiskANN training/pool grep. No executable methods or external query channel.',
27:'Fixed helper source slice, helper-header grep, then db grep with textual filter; empty later segments preserved, no fallback.'}}
relevance={
1:('direct','Python OptimizeOption declaration/default concurrency context; preview does not contain its whole definition.'),
2:('direct','Segment merge entry accepts concurrency; preview stops before pool selection.'),
3:('direct','ThreadPool clamp ties requested count to hardware capacity; actually displayed source94-100.'),
4:('direct','Builder-side reducer constructs BorrowedSingleQueueIndexThreads and passes threads to optional conversion-training; displayed742-747 directly relevant.'),
5:('direct','Core Index::merge entry identified; preview does not include later pool selection.'),
6:('tangential','IndexRunner optimize interface is not collection optimization pool selection.'),
7:('direct','Global optimization pool accessor, initialization and returned pointer.'),
8:('direct','Displayed805-808 selects global pool and its count when concurrency is zero.'),
9:('adjacent','CreateIndexOptions rather than OptimizeOptions; same source API vicinity, not evidence of optimization branch by itself.'),
10:('direct','Python collection.optimize forwards OptimizeOption to binding.'),
11:('direct','GlobalResource once-initialization constructs optimization pool from GlobalConfig.'),
12:('direct','VectorColumnIndexer merge takes MergeOptions; preview lacks final forwarding call.'),
13:('tangential','IVF-specific merge implementation outside question focus; frozen source only.'),
14:('adjacent','C optimization API declaration, not Python invocation evidence.'),
15:('direct','Helper merge declaration accepts concurrency.'),
16:('direct','Vector-column MergeOptions contains concurrency and pool pointer.'),
17:('adjacent','Generated Python Optimize binding stub; no pool selection shown.'),
18:('direct','OptimizeOption constructor concurrency/default context, not authoritative worker count alone.'),
19:('adjacent','C optimization API wrapper outside Python-specific question.'),
20:('direct','Python configuration test source illustrates optimize_threads input; not an executed test.'),
21:('direct','Segment merge declaration accepts concurrency.'),
22:('adjacent','Generic TrainAndBuild convenience helper; lacks supplied pool argument and cannot establish target-stage handoff alone.'),
23:('adjacent','C configuration getter declaration.'),
24:('adjacent','DiskANN alternative trainer overload, not queried pool-taking overload.'),
25:('direct','ThreadPool class identified, but displayed first ten lines do not establish count semantics.'),
26:('tangential','IVF trainer overload outside requested builder case.'),
27:('tangential','Different ThreadQueue primitive, no queried optimization flow shown.'),
28:('adjacent','DiskANN wrapper training entry; does not display merge target-stage pool handoff.'),
29:('tangential','Standalone local-builder tool source, not application optimize path.'),
30:('direct','Source regression test for converter use of reducer pool; source evidence only, not test execution.'),
31:('tangential','Recall utility pool example, not application optimize path.'),
32:('tangential','FlatSparse trainer overload outside requested focus.'),
33:('tangential','Standalone benchmark utility pool setup; not measured or executed.'),
34:('tangential','Standalone sparse-builder tool source.'),
35:('tangential','Original recall utility source, not historical solution material.'),
36:('tangential','Streamer trained-metadata hook does not show pool handoff.'),
37:('tangential','Original benchmark tool source, not benchmark measurement.'),
38:('tangential','HNSW sparse trainer overload outside requested focus.'),
39:('direct','Core MergeOptions carries concurrency and pool pointer.'),
40:('direct','Python init signature exposes optimize_threads parameter.'),
41:('direct','GlobalResource class/accessor identity; preview ends on optimize accessor declaration.'),
42:('adjacent','PQ/OPQ training callback declaration; not DiskANN outer pool.'),
43:('direct','Collection optimize declaration accepts OptimizeOptions.'),
44:('direct','Collection optimize implementation entry; preview shows maintenance serialization only.'),
45:('adjacent','IndexThreads stop wrapper, not count/pool identity.'),
46:('tangential','PQ int4 outer-pool analogue; does not establish DiskANN int8 implementation.'),
47:('adjacent','Lloyd centroid initialization generic pool callback, not outer dispatch pool.'),
48:('direct','Actual PQ int8 train_all_chunks creates SingleQueueIndexThreads(thread_count_, false) and queries count.'),
49:('direct','DiskANN merge wrapper forwards MergeOptions to core Index::merge.'),
50:('adjacent','C configuration setter declaration.'),
51:('tangential','IVF RaBitQ builder outside requested focus.'),
52:('direct','SingleQueueIndexThreads constructor maps zero to hardware fallback and otherwise uses passed size.'),
53:('adjacent','C configuration setter implementation; not Python binding path.'),
54:('tangential','IVF RaBitQ class declaration outside question focus.'),
55:('direct','Vector-column merge declaration takes MergeOptions.'),
56:('direct','Global-resource effective count test source; source only, not executed verification.'),
57:('tangential','FlatSparse train overload outside requested focus.')}
assert len(relevance)==57
impl=O/'tool-implementation';tree='de02030056a3224012725799d845bf2be9edae2b'
assert subprocess.check_output(['git','cat-file','-t',tree],cwd=W).strip()==b'tree'
implfiles=[('code.rs','crates/zg-engine/src/extraction/code.rs'),('adapter.rs','crates/zg-engine/src/extraction/code/adapter.rs'),('search_format.rs','crates/zg-transport-mcp/src/search_format.rs')]
for dest,repo_path in implfiles:assert (impl/dest).read_bytes()==subprocess.check_output(['git','show',tree+':'+repo_path],cwd=W)
transform={'fixed_rust_tree':tree,'tree_object_verified_locally':True,'source_files':[dict(ref(impl/f),repository_path=p) for f,p in implfiles],'source_ranges':[{'file':'adapter.rs','lines':[26,45],'meaning':'C++ class_specifier/struct_specifier are entity types.'},{'file':'adapter.rs','lines':[172,185],'meaning':'Normal C++ entity is the parser node itself.'},{'file':'code.rs','lines':[209,241],'meaning':'Entity content is an exact source slice between parser start/end bytes; source range retains columns.'},{'file':'search_format.rs','lines':[193,250],'meaning':'Short previews display at most ten content lines and explicit ellipsis for omitted lines.'},{'file':'search_format.rs','lines':[263,279],'meaning':'Long-line truncation applies only above160 UTF16 units; cannot itself remove semicolon from a two-character line.'}],'inference':'All three observed closing-brace-only lines are exact frozen-source prefixes at the declared end of struct entities. C++ node byte slicing supports this boundary mechanism; renderer does not independently strip punctuation. No foreign replacement text is present. The raw search reply lacks parser byte-column metadata; exact per-hit parser boundary is inferred from the matching snippet and fixed extraction mechanism, not a newly executed parser or historical-case exception.','auditor_only':True,'solver_allowed_sources_unchanged':True,'runtime_package_binding':'Fixed Rust tree supplied by parent execution provenance; this source-scope audit verifies that tree object and copied files, not a new binary build.'}
dump(O/'tool-transformation-evidence.json',transform)
profiles=[];allreviews=[];mcp_refs=[];nonwhite=[]
for pr in d['profiles']:
 name=pr['profile'];dest=O/name;dest.mkdir(exist_ok=False);queue=load(Q/name/'tool-audit.json')
 assert set(notes[name])=={r['ordinal'] for r in pr['reviews'] if r['name']=='Bash'}
 reviews=[]
 for r in pr['reviews']:
  raw=next(t for t in queue['tools'] if t['ordinal']==r['ordinal']);result=raw['result_variants'][0]
  r.update(manual_review_complete=True,strict_source_scope_valid=True,factual_evidence_scope_valid=True,external_content_observed=False,external_code_or_answer_facts_observed=False,network_or_web_attempt_observed=False,source_write_attempt_observed=False,project_execution_attempt_observed=False,external_path_attempt_observed=False,unresolved_evidence=[],full_input_result_metadata_preserved=ref(Q/name/raw['evidence_path']),result_content_sha256=sha(json.dumps(result['content'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()),metadata_sha256=result.get('metadata_sha256'),transcript_commands_executed_by_auditor=False)
  if r['name']=='Bash':r['manual_observation']=notes[name][r['ordinal']];r['all_shell_operators_and_paths_reviewed']=True;r['zg_query_via_bash_observed']=False
  elif r['name']=='Read':r['manual_observation']='Explicit absolute frozen source path. Entire returned content and metadata start/count/total/line numbering independently match frozen bytes; no source execution or outside fallback.'
  else:
   check=r.pop('mcp_check');text=check.pop('text')
   for h in check['hits']:
    h['relevance_class'],h['relevance_observation']=relevance[h['rank']]
    h['source_file']=ref(S/h['path']);h['displayed_line_count']=len(h['lines'])
    for line in h['lines']:
     if line['comparison']=='non_whitespace_difference':
      line.update(returned_is_exact_source_prefix=line['source_text'].startswith(line['returned_text']),at_declared_hit_end=line['line']==h['end_line'],last_displayed_source_line=line is h['lines'][-1],source_line_remaining_suffix=line['source_text'][len(line['returned_text']):],whole_snippet_is_contiguous_source_prefix='\n'.join((S/h['path']).read_text().split('\n')[h['start_line']-1:h['end_line']]).startswith('\n'.join(re.search(r'^'+str(x['line'])+r'\t(.*)$',text,re.M)[1] for x in h['lines'])))
      nonwhite.append({'rank':h['rank'],'path':h['path'],'declared_span':[h['start_line'],h['end_line']],**line})
   check.update(all_hit_paths_in_frozen_manifest=True,all_numbered_source_lines_compared=True,successful=True,materially_relevant=True,root_correct=True,transformation_evidence=ref(O/'tool-transformation-evidence.json'),actual_query_groups=4,limit_semantics_observation='Input limit20; response contains four query groups and57 candidate details, all retained. No assumption that total detailed candidates must equal20.',relevant_success_examples=[{'rank':4,'path':'src/core/mixed_reducer/mixed_streamer_reducer.cc','displayed_lines':[742,747]},{'rank':8,'path':'src/db/index/segment/segment_helper.cc','displayed_lines':[799,808]},{'rank':48,'path':'src/turbo/quantizer/pq_int8_quantizer/pq_int8_quantizer.cc','displayed_lines':[319,328]}])
   (dest/'mcp-0025-response.txt').write_text(text)
   check['raw_text']=ref(dest/'mcp-0025-response.txt');dump(dest/'mcp-0025-verification.json',check);mcp_refs.append(ref(dest/'mcp-0025-verification.json'))
   r['mcp_verification']=mcp_refs[-1];r['manual_observation']='Correct-root native zg query succeeded with substantively relevant source snippets. All57 hits/431 numbered lines individually verified.20 leading-indent differences and3 closing-semicolon boundary differences retained; source-only irrelevant hits also explicitly categorized. No final-answer assessment.'
  dump(dest/f"review-{r['ordinal']:04d}.json",r);reviews.append(ref(dest/f"review-{r['ordinal']:04d}.json"));allreviews.append(r)
 audit=load(Path(pr['source_audit']['path']));assert not audit['errors'] and all(not audit[x]['errors'] for x in ['before','after'])
 profiles.append({k:pr[k] for k in ['profile','tool_count','root_tool_count','child_tool_count','tool_counts','accounting','accounting_complete','all_tool_identities_equal_frozen_v2','source_audit','source_checks','extraction']} | {'strict_source_scope_valid':True,'strict_scope_gate_pass':True,'factual_evidence_scope_valid':True,'external_content_observed':False,'external_code_or_answer_facts_observed':False,'external_metadata_enumeration_observed':False,'source_unchanged':True,'relevant_correct_workspace_zg_retrieval':True if name=='zvec-grep' else None,'retrieval_required':name=='zvec-grep','unresolved_evidence':[],'read_numbered_lines_verified':sum(r.get('returned_numbered_lines',0) for r in pr['reviews']),'bash_stdout_lines_verified':sum(sum(r.get('stdout_line_classes',{}).values()) for r in pr['reviews']),'per_tool_reviews':reviews,'all_bash_inputs_outputs_reviewed_for_cli_or_other_zg_query_channels':True,'zg_bash_query_count':0,'raw_tool_files':[dict(ref(Path(queue['agent_directory'])/f['path']),relative_native_path=f['path']) for f in queue['files']]})
assert len(allreviews)==86
streamdiag={'profile':'zvec-grep','ordinal':11,'tool_evidence':ref(Q/'zvec-grep/tools/tool-0011.json'),'source_line':{'path':'src/core/algorithm/diskann/diskann_builder.cc','line':379,'text':(S/'src/core/algorithm/diskann/diskann_builder.cc').read_text().split('\n')[378]},'observed_pipeline_line_number':71,'sorted_glob_static_reconstruction_line_number':22,'intermediate_stream_order_verified':False,'scope_blocking':False,'reason':'Second grep -in prefixes pipeline stream ordinal; this is not the source line379. Every first/second-search file operand is under frozen /workspace/src/core/algorithm/diskann; the source path, source line and content are verified. No unknown input path/read operation or external reader remains. Missing intermediate stream/locale/order only prevents reproducing metadata ordinal, which is not an added frozen-policy requirement. A possible scope-unknown interpretation was considered before identifying this distinction; no published report or gate was changed.','diagnostic_sources':[ref(O/'parser-diagnostics.json'),ref(O/'parser-diagnostics-v2.json')],'forbidden_source_access_inferred':False}
dump(O/'nonblocking-output-order-observation.json',streamdiag)
observations={'non_whitespace_mcp_differences':nonwhite,'mcp_difference_count':len(nonwhite),'all_three_at_hit_end_and_last_displayed_line':all(x['at_declared_hit_end'] and x['last_displayed_source_line'] and x['returned_is_exact_source_prefix'] and x['whole_snippet_is_contiguous_source_prefix'] for x in nonwhite),'mcp_transformation_evidence':ref(O/'tool-transformation-evidence.json'),'output_order_diagnostic':ref(O/'nonblocking-output-order-observation.json'),'native_tool_reported_errors':0,'empty_and_suppressed_source_probes':[{'profile':'baseline','ordinal':30,'detail':'Only separator output from two source header searches.'},{'profile':'baseline','ordinal':47,'detail':'Suppressed grep uses absent source-local collection/*.cc and *.h; no per-subcommand status inferred.'},{'profile':'zvec-grep','ordinal':11,'detail':'Second source-only query pipeline has no matches displayed; no hidden success inferred.'}],'project_source_not_executed':True,'no_solver_external_file_content_observed':True,'source_metadata_dot_parent_policy_basis':load(policy)['solver_allowed'][2],'generated_artifact_observation':'zg before174 and after187 additional files are exclusively .zvec-grep/* and .zg-bench-index.ignore; no supplied source file changes. Names listed by solver at workspace root are predeclared generated artifacts. Setup/index embedding calls are harness activity, distinct from solver web access.'}
dump(O/'observations.json',observations)
report={'schema_version':1,'audit':'q004-native-source-tool-scope-v1','task_id':'q004','native_task_name':'zvec-q004','run_id':39375703707,'head_sha':'19c532cfeddcdf4c9374539acf4c2be3b9eebfee','run_attempt':1,'audit_complete':True,'strict_source_scope_valid':True,'strict_scope_gate_pass':True,'factual_evidence_scope_valid':True,'external_content_observed':False,'external_code_or_answer_facts_observed':False,'external_metadata_enumeration_observed':False,'source_unchanged':True,'relevant_correct_workspace_zg_retrieval':True,'required_zg_retrieval_gate_pass':True,'source_and_retrieval_gate_pass':True,'unresolved_evidence':[],'quality_graded':False,'resource_comparison_performed':False,'policy':ref(policy),'execution_decision':ref(decision),'artifact_byte_verification':ref(A/'artifact-byte-verification.json'),'source_manifest':ref(P/'source-manifest.json'),'task_instruction':ref(P/'tasks/zvec-q004/instruction.md'),'source_commit':m['source_commit'],'source_file_count':1596,'source_inventory_sha256':m['inventory_sha256'],'source_file_verification':ref(O/'source-file-verification.json'),'coverage':{'tool_count':86,'root_tool_count':86,'child_tool_count':0,'native_tool_occurrences':344,'tool_counts':dict(collections.Counter(r['name'] for r in allreviews)),'read_numbered_lines_verified':4506,'bash_stdout_lines_verified':1807,'mcp_calls':1,'mcp_query_groups':4,'mcp_hits':57,'mcp_numbered_lines':431,'mcp_line_comparison_counts':{'exact':408,'leading_indentation_omitted':20,'non_whitespace_difference':3},'all_actual_root_child_calls_and_results_checked':True,'all_raw_locations_offsets_hashes_verified':True,'all_bash_full_expressions_and_results_reviewed':True,'all_read_returned_lines_match':True,'all_mcp_hits_checked':True},'profiles':profiles,'mcp_verifications':mcp_refs,'observations':ref(O/'observations.json'),'exhaustive_extraction_crosscheck':ref(O/'extraction-crosscheck.json'),'machine_source_checks':ref(O/'source-checks-v3.json'),'preserved_initial_checks':ref(O/'source-checks.json'),'auditor_scripts':[ref(O/f) for f in ['source-check.py','source-check-v2.py','source-check-v3.py','extraction-crosscheck.py','review-build.py','verify-references.py']],'extractor':ref(W/'benchmarks/native-claude-qa-pilot/results/20261020-zvec-q001-repeat-v1/method/scope_extract_v2.py'),'accounting_identity_basis':ref(W/'benchmarks/native-claude-qa-pilot/accounting_v2.py'),'interpretation':['Verdicts are independent semantic source/tool-scope judgments, not collector flag or answer-score substitutions.','Every actual Bash command uses frozen source paths or pwd/ls at persisted /workspace. No external directory enumeration/read attempt, network request, source write, test/build execution, history/gold/answer read is observed.','MCP returned prefixes and leading indentation are reported separately from actual external content. A non-identical line is not automatically outside-source content.','Bash11 outer stream ordinal cannot be independently reconstructed from retained intermediate output, which does not exist; the actual read paths and returned source text are determined. This diagnostic does not create a new exact-sort gate or unknown path.','No answers or private grading files were inspected; parsed native JSONL only supplied tool blocks/metadata to the reviewer. Raw logs were hashed and machine-enumerated for exhaustive tool occurrence conservation.'],'limits':['Post-run retained evidence audit; not OS-level network isolation.','No new solver/provider run or source execution performed.','Source-scope success does not establish answer correctness, resource benefit, execution gate success, or experimental efficacy.','Fixed Rust extraction mechanism checked statically; per-hit parser byte-column traces were not retained and no parser was executed. Actual nonwhite differences and exact source prefixes remain visible.']}
dump(O/'report.json',report)
print(json.dumps({'report':str(O/'report.json'),'sha256':sha(O/'report.json'),'tool_count':86,'strict_source_scope_valid':report['strict_source_scope_valid'],'retrieval':True,'nonwhite':len(nonwhite)}))
