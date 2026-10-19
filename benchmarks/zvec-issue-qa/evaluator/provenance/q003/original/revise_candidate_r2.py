from datetime import datetime, timezone
import hashlib, json, shutil
from pathlib import Path
root=Path('/tmp/zg-qa-candidate-724-20261020')
now=datetime.now(timezone.utc).isoformat()
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def load(name): return json.loads((root/name).read_text())
def save(name,value): (root/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
review=load('independent-review.json')
for name in ['candidate/prompt.txt','candidate/rubric-draft.json','source-references.json','decision.json']:
    assert sha(root/name)==review['input_sha256'][name], 'Original draft differs from reviewed version: '+name
snapshot=root/'revisions/v1-before-independent-review'
snapshot.mkdir(parents=True,exist_ok=False)
originals=['candidate/prompt.txt','candidate/rubric-draft.json','source-references.json','decision.json','review.txt','source-checks.json','evidence-manifest.json','independent-review.json','build_candidate_evidence.py']
for name in originals:
    target=snapshot/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/name,target)
(snapshot/'snapshot.json').write_text(json.dumps({'snapshot_at_utc':now,'reason':'Preserve the exact draft assessed in independent-review.json before R1/R2/R3 revisions.','files':{name:sha(snapshot/name) for name in originals}},indent=2)+'\n')

refs=load('source-references.json');pre='68078e55e7ae9d705b8829b5d416e022060bebf7'
new_refs=[
 ('initial_indexed_flag','src/db/index/segment/segment.cc',536,556),
 ('indexed_flag_definition','src/db/index/common/meta.h',341,355),
 ('initial_flat_storage','src/db/index/segment/segment.cc',4117,4138),
 ('default_flat_params','src/include/zvec/db/index_params.h',570,578),
 ('flush_vector_transfer','src/db/index/segment/segment.cc',4382,4396),
 ('record_indexed_flag','src/db/index/segment/segment.cc',1470,1486),
 ('same_index_skip','src/db/index/segment/segment.cc',1525,1540),
 ('scalar_forward_scan','src/db/sqlengine/planner/query_planner.cc',467,493),
 ('scalar_delete_predicate','src/db/sqlengine/planner/ops/check_not_filtered_op.cc',21,41),
]
for ident,path,first,last in new_refs:
    source=root/'source-pre-fix'/path
    lines=source.read_text().splitlines(keepends=True)
    content=''.join(lines[first-1:last])
    target=root/'source-references'/(ident+'.txt')
    target.write_text(''.join(f'{n}: {line}' for n,line in enumerate(lines[first-1:last],first)))
    refs.append({'id':ident,'version':'pre','commit':pre,'path':path,'first_line':first,'last_line':last,'source_sha256':sha(source),'excerpt_sha256':hashlib.sha256(content.encode()).hexdigest(),'numbered_export':str(target.relative_to(root)),'numbered_export_sha256':sha(target),'url':f'https://github.com/alibaba/zvec/blob/{pre}/{path}#L{first}-L{last}'})
save('source-references.json',refs)

prompt=(root/'candidate/prompt.txt').read_text()
prompt=prompt.replace('A small collection uses dense HNSW/COSINE vectors and scalar labels. Each inserted ID has a distinct vector whose original value is also saved outside the collection. Consider this sequence in the supplied checkout:', 'A small collection is configured for non-quantized FP32 HNSW/COSINE vectors and scalar labels. Each of 28 inserted IDs has a different unit basis vector in 32 dimensions, saved outside the collection. Consider this reported failure pattern in the supplied checkout:')
prompt=prompt.replace('Insert 28 documents into a new collection; do not build another index or optimize non-empty content beforehand.', 'Insert IDs doc-0000 through doc-0027, in that order, into a new collection; do not call explicit index creation or optimize non-empty content beforehand.')
prompt=prompt.replace('Delete three early IDs, flush, and call optimize().', 'Delete doc-0000, doc-0001 and doc-0002, flush, and call optimize().')
(root/'candidate/prompt.txt').write_text(prompt)

rubric=load('candidate/rubric-draft.json')
rubric['revision']='r2-after-independent-static-review'
rubric['revised_at_utc']=now
rubric['status']='revised candidate draft; R1/R2/R3 addressed by author, pending independent re-review/admission; no solver or runtime execution'
rubric['grading_policy']='Award only the integer point units explicitly defined below (no half-points). Each unit requires a correct causal statement, not merely a restatement of the prompt. Equivalent terminology and equivalent invariant-preserving repairs are accepted. Grade only the submitted final answer. Post-fix evidence is evaluator-only corroboration; solver receives only the frozen pre-fix source and source-neutral prompt.'
rubric['success']={
 'minimum_score':8,'maximum_score':10,'final_answer_required':True,
 'required_full_credit':{'maintenance_branch':2,'broken_mapping':2},
 'core_citation_gate':{
  'criteria':['maintenance_branch','broken_mapping'],
  'rule':'For each core criterion, the answer must give existing pre-fix file paths and line numbers whose cited content jointly supports both awarded point units. Judge the actual frozen source, not literal equality with curated ranges. A post-fix-only, fabricated, wrong-revision, or materially unrelated citation cannot satisfy this gate. Missing core citations fail the answer even if its prose is correct.',
  'required_record':'For each core unit record the answer citation, verified pre-fix location, and a short explanation of how it supports the unit.'},
 'critical_contradictions_allowed':0,
 'rule':'pass = nonempty final answer AND total_points >= 8 AND maintenance_branch == 2 AND broken_mapping == 2 AND all core citation gates pass AND no substantiated disqualifying overclaim/critical contradiction. A disqualification forces pass=false independently of total score.',
 'authority':'Scratch author adopted the independent review proposal in this revision before any solver run; this is a draft scoring contract, not dataset admission or execution authorization.'}
rubric['score_output_required']=['final_answer_present','per-criterion integer score and satisfied point-unit IDs','total_points','core citation gate results with verified source locations','disqualifications with answer quotations and evidence, or an empty list','pass boolean and failed gates']
rubric['disqualification_policy']={
 'application':'Apply only to an explicit material assertion or recommendation in the final answer that contradicts the frozen evidence; an omission is scored by anchors and mandatory gates, not automatically treated as a contradiction. A claim clearly quoted and rejected by the answer is not an endorsement.',
 'evidence_required':['short exact quotation from the answer','the violated disqualifier/contradiction ID','pre-fix source path and line range demonstrating the conflict, or the named evidence record for an unsupported execution claim','a short rationale'],
 'uncertainty':'Do not manufacture an answer quotation or infer a disqualification solely from ambiguous wording; record the ambiguity and use ordinary point/citation rules. An unresolved factual dispute must be adjudicated before declaring pass.'}
rubric['critical_contradictions']=[
 {'id':'C1','claim':'The single-segment vector-only path compacts forward rows in coordination with the filtered vectors, while identifying that same path as the cause.','references':['task_policy','create_vector_path','dense_renumber']},
 {'id':'C2','claim':'There is no vector/forward association error; the scenario is solely ordinary approximate-search recall loss.','references':['create_vector_path','dense_renumber','search_forward_join']},
 {'id':'C3','claim':'Read-only reopening repairs the persisted vector-key mapping.','references':['create_vector_path','persist_reload']},
 {'id':'C4','claim':'Disabling tombstone filtering during queries is the repair.','references':['query_delete_filter','scalar_forward_scan','scalar_delete_predicate']},
 {'id':'C5','claim':'No vector storage or vector indexer exists before the first optimize, or an existing default FLAT indexer means the requested HNSW index is already marked built.','references':['initial_indexed_flag','indexed_flag_definition','initial_flat_storage','default_flat_params','flush_vector_transfer','same_index_skip']},
]
rubric['disqualifying_overclaims']=[
 {'id':'D1','claim':'Treat the original tail-only description as complete and explicitly deny wrong-vector associations.','references':['dense_renumber','search_forward_join']},
 {'id':'D2','claim':'Every overwrite/delete followed by optimize is affected regardless of the maintenance path.','references':['task_policy','same_index_skip','compact_scalar_vector']},
 {'id':'D3','claim':'Repairing only absent tail rows with vectors fetched from the affected index fully repairs all wrong associations.','references':['dense_renumber','vector_fetch','search_forward_join']},
 {'id':'D4','claim':'Assert a local runtime reproduction or passing upstream test execution without supporting execution evidence.','evidence_records':['source-checks.json','independent-review.json:independence_and_limits']},
]
criteria={c['id']:c for c in rubric['criteria']}
def units(ident, values):
 c=criteria[ident];c['allowed_scores']=list(range(c['points']+1));c['point_units']=[{'id':f'{ident}.{i+1}','points':1,'required':value} for i,value in enumerate(values)]
 assert len(values)==c['points']
 if c['points']==2:
  c['anchors']={'0':'Neither point unit is fully supported.','1':'Exactly one of the two stated point units is fully supported; identify which one.','2':'Both point units are fully supported without contradicting each other.'}
 else:
  c['anchors']={'0':'The stated point unit is absent, materially wrong, or incomplete.','1':'All essential elements of the stated point unit are supported; optional extensions below are not required.'}

criteria['physical_vs_live']['claim']='Deleting a document marks its global ID deleted and removes the external-ID mapping, but does not erase the physical forward row/doc_ids entry. Physical=28 and live=25; stats applies the deletion filter. Upsert behavior is optional for this delete-only task.'
units('physical_vs_live',['Explain tombstones/retained physical rows and how filtered live counting yields 25 from 28 in this delete sequence.'])
criteria['physical_vs_live']['optional_extensions']=['The upsert tombstone-and-append route; omission cannot reduce credit.']

criteria['maintenance_branch']['claim']='Vector storage already exists before optimize: non-quantized inserts create default FLAT memory vector indexers; flush transfers them to persisted vector_indexers. A new HNSW-schema segment lacks the requested-index built flag (only an initially FLAT schema is pre-marked). The first nonempty optimize materializes the requested HNSW index. With physical=28/live=25 the strict live < physical * (1 - 0.3f) purge test is false, so the sealed single-segment group gets CreateVectorIndexTask and no coordinated forward-row purge.'
criteria['maintenance_branch']['references'] += ['initial_indexed_flag','indexed_flag_definition','initial_flat_storage','default_flat_params','flush_vector_transfer']
units('maintenance_branch',[
 'Explain the physical-versus-live denominator and strict purge condition; 3/28 deletion fraction selects the non-purging branch, while a sufficiently larger fraction selects coordinated compaction. Do not require ideal-decimal equality behavior at exactly 30%.',
 'Trace sealing the nonempty segment to the single-segment CreateVectorIndexTask/create_vector_index path without forward-row purge. Distinguish existing default FLAT storage (memory then persisted) from first materialization of the requested HNSW index with its initially unset indexed flag.'
])

units('broken_mapping',[
 'Identify deletion filtering in vector Merge and the reducer skipping tombstones while assigning consecutive keys only to surviving vectors.',
 'Explain that forward/doc_ids positions and vector block metadata still describe the physical row space, and explicitly connect the dense survivor keys to the resulting coordinate mismatch.'
])

criteria['symptoms_and_persistence']['references'] += ['scalar_forward_scan','scalar_delete_predicate']
units('symptoms_and_persistence',['Explain why vector keys/physical offsets fetch the wrong association or run past the vector tail while live counts and scalar scans use retained forward rows with deletion filtering, and why flushing/committing the rebuilt index makes read-only reopen no repair. It is sufficient to name the access paths; detailed conversion internals are unnecessary.'])

criteria['bounded_controls']['claim']='Empty optimize has no persisted content to process. Clean nonempty first materialization has no tombstones, so it preserves key alignment and records the target index as built. The later delete-only same-segment/same-parameter create_vector_index returns early on the indexed flag. This explains the exact controls, not universal directory immunity.'
criteria['bounded_controls']['references']=['optimize_entry','initial_flat_storage','default_flat_params','initial_indexed_flag','record_indexed_flag','same_index_skip','writes']
units('bounded_controls',['Explain both controls: empty optimize does no useful build, whereas a clean nonempty requested-index build stays aligned and sets the indexed flag, allowing the later same-parameter delete-only control to skip rebuilding.'])
criteria['bounded_controls']['optional_extensions']=['Non-purging multi-segment compaction; not needed for full control credit because the prompt adds no documents after the clean first build.']

units('high_delete_path',['Explain that the above-threshold path filters forward rows and records omitted original positions in a bitmap used to filter vectors, preserving correspondence by compacting both spaces together. Merely naming CompactTask is insufficient; it can also be called with no purge filter.'])
units('repair_invariant',['State and preserve the same row-key correspondence on both sides: retain tombstoned vector slots for unchanged forward rows, or provide an equivalent coordinated remapping. Keep logical deletion filtering in reads. Literal nullptr spelling is not required.'])

criteria['regression_design']['claim']='The essential oracle supplies independent known normalized directions, such as unit basis vectors, and checks the expected live ID and vector payload after the below-threshold delete/optimize sequence. It must detect wrong association rather than only counts; an affected-index fetch or same-ID query is not an independent vector oracle. Wider variant coverage is useful but optional for this single point.'
units('regression_design',['Propose an independently retained known-direction vector oracle and assert correct live ID plus payload after the triggering delete/optimize sequence, explicitly catching associations that count-only checks miss.'])
criteria['regression_design']['optional_extensions']=['Read-only reopen, empty/clean controls, high-delete comparison, upsert, quantization or explicit create_index matrix; none is required for this point once the essential independent oracle is sound.','Distinct raw vectors alone are not necessarily distinct COSINE directions; normalized basis directions avoid that ambiguity. No universal ANN recall guarantee is inferred.']
save('candidate/rubric-draft.json',rubric)

checks=load('source-checks.json')
checks['r2_static_evidence']={
 'hnsw_schema_not_initially_marked_indexed':{'reference':'initial_indexed_flag','assertion':'Create only calls add_indexed_vector_field when schema index type is FLAT.'},
 'initial_vectors_use_flat_storage':{'references':['writes','initial_flat_storage','default_flat_params'],'assertion':'Insertion initializes memory components and default parameters return FlatIndexParams.'},
 'flush_preserves_available_vector_storage':{'reference':'flush_vector_transfer','assertion':'FLAT memory vector indexers are moved into persisted vector_indexers with physical block counts.'},
 'target_index_build_and_skip':{'references':['record_indexed_flag','same_index_skip'],'assertion':'Building records indexed fields; later same-parameter creation returns early.'},
 'scalar_enumeration_direct_evidence':{'references':['scalar_forward_scan','scalar_delete_predicate'],'assertion':'forward_scan filters LOCAL_ROW_ID with the segment delete predicate.'},
 'method':'Inspected exact frozen source; no engine or solver execution.'}
save('source-checks.json',checks)

decision=load('decision.json');decision['draft_revision']=rubric['revision'];decision['revised_at_utc']=now
decision['review_response']={'independent_review_sha256':sha(root/'independent-review.json'),'required_changes_addressed':['R1','R2','R3'],'additional_recommendation_addressed':'O3 scalar-only enumeration references','status':'author-addressed; independent re-review/admission still separate','original_snapshot':'revisions/v1-before-independent-review/','prompt_scope':'Same 28-document delete-only case and controls. Clarified non-quantized FP32/default target and non-collinear basis directions; no FLAT implementation details or issue/PR IDs added to solver prompt.'}
decision['next_gate']='Independent re-review of revised scoring and references before formal admission or solver authorization; no automatic run.'
save('decision.json',decision)
(root/'review.txt').write_text((root/'review.txt').read_text()+f'''\nRevision R2 ({now}): independent-review.json R1/R2/R3 have been addressed in scratch. Success now requires a nonempty final answer, >=8/10, both two-point core criteria at full credit, verified core citations, and zero evidenced critical contradictions/disqualifications. Integer scoring units and 0/1/2 anchors specify partial credit. Delete-only control credit requires no multi-segment extension; regression credit requires an independent association oracle, not a broad optional test matrix. Added direct initial FLAT storage, indexed-flag and flush-transfer evidence: the first optimize builds the requested HNSW index rather than creating vector storage for the first time. Added direct scalar-scan delete filtering. The original reviewed draft is preserved under revisions/v1-before-independent-review/. No source or raw provenance changed, and no execution was performed. The old build_candidate_evidence.py is a retained V1 generator; do not rerun it over the revised scratch draft.\n''')
changes={
 'revision':rubric['revision'],'revised_at_utc':now,'input_independent_review_sha256':sha(root/'independent-review.json'),
 'reason':'Resolve required R1 success/citation/disqualification policy, R2 integer partial-credit anchors, and R3 FLAT storage versus built HNSW distinction before any outcomes; add O3 scalar enumeration evidence.',
 'original_snapshot':'revisions/v1-before-independent-review/','before_sha256':review['input_sha256'],
 'after_sha256':{name:sha(root/name) for name in ['candidate/prompt.txt','candidate/rubric-draft.json','source-references.json','decision.json','source-checks.json','review.txt']},
 'requirements':{
  'R1':{'status':'addressed','locations':['candidate/rubric-draft.json:success','candidate/rubric-draft.json:disqualification_policy','candidate/rubric-draft.json:critical_contradictions'],'policy':'Author adopted >=8 plus both core criteria full, core citations valid, and no critical contradiction. Missing core cause cannot pass at score8.'},
  'R2':{'status':'addressed','locations':['candidate/rubric-draft.json:criteria[*].point_units','candidate/rubric-draft.json:criteria[*].anchors'],'policy':'Integer units; explicit 1-point anchors for both 2-point criteria. Upsert, multi-segment controls and broad regression variants are optional.'},
  'R3':{'status':'addressed','new_reference_ids':[x[0] for x in new_refs[:7]],'policy':'Existing FLAT storage distinguished from first target HNSW build and indexed-flag skip; implementation details remain evaluator-only.'},
  'O3':{'status':'addressed','new_reference_ids':['scalar_forward_scan','scalar_delete_predicate']}
 },
 'unchanged':['pre-fix source commit and all exported source files','raw issue including both complete comments','merged fix provenance','original observed controls and scoped causal question','total possible score 10','no solver output used'],
 'execution':{'solver':False,'ci':False,'engine':False,'dependencies_installed':False},
 'admission':'Not added to dataset; no runtime reproduction or solver evaluation claimed.'}
save('revision-r2.json',changes)

# Validate frozen inputs, snapshots, scores, references and the mandatory success gate.
source=load('source-manifest.json')
for name,value in source['files'].items(): assert sha(root/'source-pre-fix'/name)==value,name
for name,value in json.loads((snapshot/'snapshot.json').read_text())['files'].items(): assert sha(snapshot/name)==value,name
assert sha(root/'independent-review.json')==sha(snapshot/'independent-review.json')
ids={r['id'] for r in refs}
for r in refs:
 src=root/('source-pre-fix' if r['version']=='pre' else 'source-post-fix-references')/r['path']
 assert sha(src)==r['source_sha256']
 assert sha(root/r['numbered_export'])==r['numbered_export_sha256']
for c in rubric['criteria']:
 assert sum(u['points'] for u in c['point_units'])==c['points']
 assert set(c['allowed_scores'])==set(range(c['points']+1))
 assert all(r in ids for r in c['references']+c.get('evaluator_only_corroboration',[]))
assert sum(c['points'] for c in rubric['criteria'])==10
assert rubric['success']['required_full_credit']=={'maintenance_branch':2,'broken_mapping':2}
assert 'FLAT' not in prompt and '#724' not in prompt and '#731' not in prompt
assert all(sha(root/name)==review['input_sha256'][name] for name in ['source-manifest.json','provenance/issue-body.md','provenance/comment-5550659107.md','provenance/comment-5578420111.md','provenance/merged-fix.patch'])
# Exhaustively validate intended pass logic for all possible integer score vectors.
import itertools
passing=0
for scores in itertools.product(*[c['allowed_scores'] for c in rubric['criteria']]):
 total=sum(scores);by_id=dict(zip([c['id'] for c in rubric['criteria']],scores))
 allowed=total>=8 and all(by_id[k]==v for k,v in rubric['success']['required_full_credit'].items())
 if allowed:
  passing+=1
  assert by_id['maintenance_branch']==2 and by_id['broken_mapping']==2
validation={'validated_at_utc':now,'frozen_source_files_unchanged':len(source['files']),'snapshot_hashes_valid':True,'raw_issue_comments_unchanged':True,'source_reference_count':len(refs),'core_missing_cannot_pass':True,'score_combinations_checked':576,'passing_score_combinations_before_other_gates':passing,'no_implementation_leak_in_prompt':True,'solver_or_engine_run':False}
save('revision-r2-validation.json',validation)
files={str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*')) if p.is_file() and 'repository' not in p.relative_to(root).parts and p!=root/'evidence-manifest.json'}
save('evidence-manifest.json',{'file_count':len(files),'files':files,'revision':rubric['revision'],'candidate_prompt_sha256':sha(root/'candidate/prompt.txt'),'candidate_rubric_sha256':sha(root/'candidate/rubric-draft.json')})
for name,value in files.items(): assert sha(root/name)==value,name
print(json.dumps({'prompt_sha256':sha(root/'candidate/prompt.txt'),'rubric_sha256':sha(root/'candidate/rubric-draft.json'),'references':len(refs),'evidence_files':len(files),'validation':validation},indent=2))
