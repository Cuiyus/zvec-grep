from datetime import datetime, timezone
import hashlib, json, subprocess
from pathlib import Path

root=Path('/tmp/zg-qa-candidate-724-20261020')
pre='68078e55e7ae9d705b8829b5d416e022060bebf7'
post='31d88ea3e95430b79d12e2461dabc3ab3de5bcc0'
def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
issue=json.loads((root/'provenance/issue.json').read_text())[0]
(root/'provenance/issue-body.md').write_text(issue['body'])
refs=[
 ('writes','pre','src/db/index/segment/segment.cc',868,935),
 ('live_count','pre','src/db/collection.cc',529,544),
 ('segment_count','pre','src/db/index/segment/segment.cc',616,626),
 ('optimize_entry','pre','src/db/collection.cc',910,952),
 ('task_policy','pre','src/db/collection.cc',1086,1173),
 ('threshold','pre','src/db/common/constants.h',35,45),
 ('delete_filter','pre','src/db/index/segment/segment.cc',464,475),
 ('create_vector_path','pre','src/db/index/segment/segment.cc',1466,1578),
 ('merge_bridge','pre','src/db/index/column/vector_column/vector_column_indexer.cc',103,132),
 ('pk_rewrite','pre','src/core/interface/index.cc',1016,1076),
 ('dense_renumber','pre','src/core/mixed_reducer/mixed_streamer_reducer.cc',255,325),
 ('scalar_fetch','pre','src/db/index/segment/segment.cc',1028,1127),
 ('vector_fetch','pre','src/db/index/segment/segment.cc',1268,1308),
 ('search_keys','pre','src/db/index/column/vector_column/combined_vector_column_indexer.cc',54,75),
 ('search_forward_join','pre','src/db/sqlengine/planner/vector_recall_node.cc',216,237),
 ('query_delete_filter','pre','src/db/sqlengine/planner/doc_filter.cc',68,85),
 ('compact_scalar_vector','pre','src/db/index/segment/segment_helper.cc',100,171),
 ('compact_forward_rewrite','pre','src/db/index/segment/segment_helper.cc',232,280),
 ('compact_filter_bitmap','pre','src/db/index/segment/segment_helper.cc',934,995),
 ('persist_reload','pre','src/db/collection.cc',1009,1070),
 ('fixed_filter','post','src/db/index/segment/segment.cc',1491,1517),
 ('regression_oracle','post','tests/db/collection_test.cc',4006,4076),
 ('regression_delete_upsert','post','tests/db/collection_test.cc',4080,4150),
 ('regression_create_index','post','tests/db/collection_test.cc',4152,4189),
]
refdir=root/'source-references';refdir.mkdir(exist_ok=True)
manifest=[]
for ident,version,path,first,last in refs:
    source=root/('source-pre-fix' if version=='pre' else 'source-post-fix-references')/path
    lines=source.read_text().splitlines(keepends=True)
    assert last<=len(lines)
    content=''.join(lines[first-1:last])
    excerpt=refdir/(ident+'.txt')
    excerpt.write_text(''.join(f'{n}: {line}' for n,line in enumerate(lines[first-1:last],first)))
    commit=pre if version=='pre' else post
    manifest.append({'id':ident,'version':version,'commit':commit,'path':path,'first_line':first,'last_line':last,'source_sha256':digest(source),'excerpt_sha256':hashlib.sha256(content.encode()).hexdigest(),'numbered_export':str(excerpt.relative_to(root)),'numbered_export_sha256':digest(excerpt),'url':f'https://github.com/alibaba/zvec/blob/{commit}/{path}#L{first}-L{last}'})
save_json(root/'source-references.json',manifest)

candidate=root/'candidate';candidate.mkdir(exist_ok=True)
prompt='''A small collection uses dense HNSW/COSINE vectors and scalar labels. Each inserted ID has a distinct vector whose original value is also saved outside the collection. Consider this sequence in the supplied checkout:

1. Insert 28 documents into a new collection; do not build another index or optimize non-empty content beforehand.
2. Delete three early IDs, flush, and call optimize(). The collection is small enough to remain within one segment-size limit.
3. The live document count and a scalar-only enumeration agree on 25 documents, but searches using saved reference vectors can return the wrong live IDs or omit live tail documents. Fetching vector payloads may also fail even though scalar data remains. Reopening read-only does not remove the search discrepancy.

Two controls matter: optimizing the empty collection before the first insertion does not prevent the issue, whereas optimizing the initial non-empty data before deleting those IDs avoids it in this sequence. There is no crash or concurrent writer.

Explain the source-level cause from deletion through optimize() and subsequent reads. Why can the scalar/count checks agree while vector results are wrong? Explain the two controls and distinguish the maintenance path used when the deleted-row fraction is much larger. State the mapping invariant a fix must preserve, and describe a regression check that would detect wrong ID/vector associations rather than merely checking counts. Cite relevant source paths and line numbers, and keep conclusions limited to the paths established by this checkout. Do not edit or execute the repository.
'''
(candidate/'prompt.txt').write_text(prompt)
rubric={
 'status':'candidate source-grounded draft, not admitted to dataset and not solver-tested',
 'issue':'https://github.com/alibaba/zvec/issues/724','pre_fix_commit':pre,'total_points':10,
 'grading_policy':'Score the causal explanation and valid pre-fix source citations, not exact function-name phrasing. Partial credit is allowed. Do not award points merely for repeating the scenario. Post-fix evidence is evaluator-only corroboration; solver gets only the pre-fix export and prompt.',
 'criteria':[
  {'id':'physical_vs_live','points':1,'claim':'Deletes mark tombstones without removing physical forward rows; an upsert likewise tombstones the old ID and appends a new row. Thus physical and live counts differ, and stats count live rows using the delete filter. The prompt only requires the delete route.','references':['writes','live_count','segment_count']},
  {'id':'maintenance_branch','points':2,'claim':'optimize seals nonempty writing data and snapshots persisted segments. With a small deletion fraction the planner does not physically purge rows; a single group/segment uses CreateVectorIndexTask and create_all_vector_index/create_vector_index. The actual criterion is live < physical * (1 - 0.3); three of 28 is below that deletion threshold. It is not generic deletion loss in every optimize call.','references':['optimize_entry','task_policy','threshold','create_vector_path']},
  {'id':'broken_mapping','points':2,'claim':'The in-place index-build helper passes the segment deletion filter into Merge. The filter resolves original row positions to deleted global IDs. Merge enables PK rewrite and the reducer skips filtered vectors while assigning surviving vectors consecutive IDs. Forward rows/doc_ids and new block metadata retain physical positions/counts, so compact vector keys no longer denote the same forward rows.','references':['delete_filter','create_vector_path','merge_bridge','pk_rewrite','dense_renumber']},
  {'id':'symptoms_and_persistence','points':1,'claim':'Search keys are interpreted as segment row indices when fetching forward IDs/fields; point vector fetch uses the original physical row/block offset. This explains wrong associations and high-row vector-fetch failure while scalar-only access and live counts can agree. Flushed replacement index/metadata and reload explain why reopening is not a repair. Do not reduce the fault to harmless ANN recall or missing scalar rows.','references':['search_keys','search_forward_join','scalar_fetch','vector_fetch','live_count','persist_reload']},
  {'id':'bounded_controls','points':1,'claim':'Empty optimize returns without persisted content. A clean initial index build has no deleted rows to skip; later same-parameter index creation can be skipped once indexed, and non-purging multi-segment compaction keeps scalar/vector row treatment aligned. This explains the stated clean-first control, not a universal lifetime immunity of every directory.','references':['optimize_entry','create_vector_path','task_policy','compact_scalar_vector','compact_filter_bitmap']},
  {'id':'high_delete_path','points':1,'claim':'Above the deletion threshold, true CompactTask passes the delete filter through scalar reduction, constructs a bitmap of omitted original row positions, and uses the matching row filter for vector reduction. Both spaces are compacted together. Therefore skipping tombstoned vectors is appropriate there, unlike a vector-only rebuild over unchanged forward rows.','references':['task_policy','compact_scalar_vector','compact_forward_rewrite','compact_filter_bitmap']},
  {'id':'repair_invariant','points':1,'claim':'A vector-only rebuild must retain tombstoned vector slots when forward rows are unchanged; or both sides would need coordinated remapping. Passing no deletion filter on the former path is the targeted correction, while deletion filtering at query time and coordinated full compaction remain necessary.','references':['create_vector_path','query_delete_filter','compact_scalar_vector'],'evaluator_only_corroboration':['fixed_filter']},
  {'id':'regression_design','points':1,'claim':'Use independent distinct vectors (for example basis vectors) and compare expected ID plus payload for each live document after deletion/overwrite and optimize, including read-only reopen and clean/empty controls. Count-only tests are inadequate. Tests should target below-threshold single-segment rebuild and compare real compaction; a same-document-ID query is not an independent vector oracle.','references':['search_forward_join','vector_fetch'],'evaluator_only_corroboration':['regression_oracle','regression_delete_upsert','regression_create_index']},
 ],
 'not_required_or_not_proven':['Windows-specific cause','all index types or all versions have identical behavior','segfault mechanism or dimension/volume thresholds','exactly K missing IDs under every segment topology','global durable directory immunity after one optimize','ANN top-k or recall guarantees across arbitrary data'],
 'disqualifying_overclaims':['Treat original tail-only account as complete and deny wrong-vector associations','Claim every overwrite/delete followed by optimize is affected regardless of path','Recommend repairing only absent tail rows using vectors fetched from the affected index as a complete repair','Claim local runtime reproduction or passing upstream tests without evidence'],
}
save_json(candidate/'rubric-draft.json',rubric)

checks={
 'merged_fix_and_parent_verified':json.loads((root/'provenance/closing-reference.json').read_text())['data']['repository']['pullRequest'],
 'old_filter_present':'Merge(to_merge_indexers, filter_, merge_options)' in (root/'source-pre-fix/src/db/index/segment/segment.cc').read_text(),
 'new_filter_absent':'Merge(to_merge_indexers, nullptr, merge_options)' in (root/'source-post-fix-references/src/db/index/segment/segment.cc').read_text(),
 'regression_names_present':[name for name in ['Feature_Optimize_Delete_RowMapping','Feature_Optimize_Upsert_RowMapping','Feature_CreateIndex_Delete_RowMapping'] if name in (root/'source-post-fix-references/tests/db/collection_test.cc').read_text()],
 'runtime_reproduction_performed':False,'build_or_dependencies_installed':False,'solver_or_ci_run':False,
 'interpretation':'Static source and merged regression-test inspection only; test source is not a passing test execution.'}
save_json(root/'source-checks.json',checks)
decision={
 'candidate':'alibaba/zvec#724','reviewed_at':datetime.now(timezone.utc).isoformat(),
 'decision':'retain_as_narrow_source_grounded_QA_candidate; not yet admitted or evaluated',
 'primary_issue_url':'https://github.com/alibaba/zvec/issues/724',
 'complete_issue_comments_read':[5550659107,5578420111],
 'closing_pr':731,'merged_commit':post,'precise_pre_fix':pre,
 'retention_reasons':['Maintainer explicitly confirmed reproduction and cause in the second comment.','PR #731 is merged and GraphQL closingIssuesReferences explicitly names #724.','The immediate pre-fix parent is frozen and exported with all source file hashes.','There is a traceable multi-file chain from tombstones through task selection, filtered vector merge, dense key reassignment, unchanged forward mapping, and fetch/search result assembly.','Merged regression tests independently specify ID/vector equality after delete/upsert and reopening, rather than merely asserting count.','The restricted HNSW single-segment below-threshold scenario is code-grounded and avoids the unqualified original report.'],
 'original_account_corrections':['Tail loss alone understates the report: the follow-up also demonstrates foreign-vector/ID associations.','The report narrows its trigger to the first optimize that actually processes nonempty data with superseded rows already present; empty optimize does not count.','Clean-first protection is kept as a bounded observed sequence, not universal directory immunity.','Fetching corrupted vectors or re-upserting only missing tail IDs is not established as a complete repair.','The >30% path is coordinated scalar-and-vector compaction, not simply a universally better vector rebuild.'],
 'scope_exclusions':['Do not require a crash/WAL scenario; that belongs to another issue.','Do not score the reported segmentation fault or its dimension/volume threshold; its precise mechanism is not established by this fix.','Do not generalize all index/metric/platform/version claims into gold.','Do not use the unrelated external-project cross-reference as evidence of this fix.'],
 'verification_limits':['No zvec binary was built or installed and no reproduction script was executed in this task.','Author observations and maintainer confirmation are primary reported evidence; local static source checks support the narrow causal mechanism.','Regression test source has been inspected; its execution results were not verified.','Repository recency does not establish absence from LLM training.','No solver output, zg result, or projected tool advantage was used for selection.'],
 'artifacts':{'prompt':'candidate/prompt.txt','rubric':'candidate/rubric-draft.json','source_manifest':'source-manifest.json','references':'source-references.json','raw_provenance':'provenance/'},
 'next_gate':'Separate independent review of the draft/rubric and optional engine reproduction if required before formal admission; no automatic dispatch or alternate candidate selection.'}
save_json(root/'decision.json',decision)
report='''Issue #724 candidate review (2026-10-20)

Decision: retain a narrowly scoped source-grounded QA candidate, not a published dataset item or a verified runtime benchmark.

Provenance: the issue has exactly two comments in the captured API response. Both were read in full, including the long follow-up reproduction and its controls. The maintainer confirms reproduction. PR #731 was merged at 2026-09-09T07:06:04Z; GraphQL explicitly links it as closing #724. Actual squash commit 31d88ea3e95430b79d12e2461dabc3ab3de5bcc0 has sole parent 68078e55e7ae9d705b8829b5d416e022060bebf7. The REST pull base.sha is different and was not used as the pre-fix revision.

Why the original issue wording cannot be copied as gold: the later comment expands the symptom from tail omissions to shifted ID/vector associations, but narrows the trigger. Its empty-optimize control has no effect; the clean nonempty initial-optimize control avoids the failure in the reported sequence. The final prompt preserves those observable controls and does not turn the author's directory-immunity statement into a universal invariant. The reported crash and broad claims about index families/metrics/dimensions are out of scope.

Verified static causal chain: delete/upsert create tombstones while physical rows remain. optimize computes live versus physical counts. Below the deletion threshold, one segment takes the vector-index creation path. SegmentImpl::merge_vector_indexer wrongly passes filter_ even though forward rows remain. Index::merge enables key rewrite; MixedStreamerReducer skips filtered vectors and increments a dense output ID only for retained items. New vector block metadata and forward row mapping still describe the original physical row space. Search maps vector keys back to forward rows; Fetch addresses vector blocks by physical offsets. Those mismatches explain foreign-vector associations and tail fetch failures without a corresponding live-count loss. Flushed index/metadata make the error persistent across a reopen.

The different compaction path is material: when the deleted proportion exceeds the 0.3 threshold, scalar rows are filtered too, and a bitmap passes the same removal decisions into vector reduction. That preserves alignment. Empty optimize exits before rebuilding. A clean prior index build has no tombstones to remove; later same-parameter rebuilds can be skipped and non-purging multi-segment merges retain both sides. These explain the bounded controls without proving all future operations safe.

Merged fix: the vector-only merge now receives nullptr instead of filter_. Tests add independent known vectors and verify full expected documents after deletion/upsert/optimize, after reopen, and after explicit create_index. These tests corroborate the invariant; their source was inspected, not executed here.

Evidence directory separates evaluator material from the solver source. source-pre-fix/ and source-pre-fix.tar contain only the frozen upstream tree (1519 files); candidate/ contains neutral prompt and evaluator-only draft rubric; provenance/ contains raw API snapshots and the merged diff. source-references.json maps each cited line range to a commit-pinned URL and full-file/excerpt hashes. No git dataset/harness/gold files were modified, no solver or CI ran, and no dependencies were installed.
'''
(root/'review.txt').write_text(report)
# Inventory all retained evidence except Git object store and this inventory itself.
files={str(p.relative_to(root)):digest(p) for p in sorted(root.rglob('*')) if p.is_file() and 'repository' not in p.relative_to(root).parts and p.name!='evidence-manifest.json'}
save_json(root/'evidence-manifest.json',{'file_count':len(files),'files':files,'candidate_prompt_sha256':digest(candidate/'prompt.txt'),'candidate_rubric_sha256':digest(candidate/'rubric-draft.json')})
print(json.dumps({'decision':decision['decision'],'evidence_files':len(files),'references':len(manifest),'prompt_sha256':digest(candidate/'prompt.txt'),'rubric_sha256':digest(candidate/'rubric-draft.json')},indent=2))
