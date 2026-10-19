# Independent blinded source audit — part 1

Reviewer: Codex independent source review, not a human or official SWE-QA grader. These are source grades only; no model/runtime attribution or reproduction result is inferred.

| Anonymous answer | C1 | C2 | C3 | C4 | C5 | C6 | C7 | Total | Source-grounded success |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| answer-A | 2 | 2 | 2 | 1 | 1 | 0 | 1 | **9/10** | **Yes** |
| answer-C | 2 | 2 | 2 | 0 | 1 | 0 | 1 | **8/10** | **Yes** |

Both answers meet the complete original success gate: at least 8 points, full credit for C1–C3, verified supporting core citations, and zero listed critical contradictions. Only the available final answers A and C were graded.

The frozen source verifies the central chain: recovery uses a local WAL reader and populates memory/doc IDs without attaching the member WAL; `flush()` returns OK for a null member WAL; optimize switches the nonempty writer through dump/flush and persists a version after removing the old writing-block metadata. A later open consequently skips that old WAL and lacks the persisted forward block for the recovered rows.

## Deductions under the exact rubric

- **A, C6 = 0:** Reopening the existing member WAL is the right repair direction, but the answer does not require propagation of member-reopen failure. Its direct-adoption variant also needs to change the existing deferred close. Mentioning the current reader-open error swallowing as a nearby issue does not complete the member-reopen error handling required by C6; there is no partial point.
- **C, C4 = 0:** Explains the live count but omits how indexed metadata produces index completeness. Merely citing the full Stats range does not supply that missing explanation.
- **C, C6 = 0:** The attach-WAL option omits error handling; the alternative dirty-test-only option is asserted equivalent without proving WAL cleanup. With a null member, the existing WAL-removal branch is still skipped. C6 explicitly requires proof of version/flush/WAL-cleanup invariants for an alternative design.

Both regression proposals include the required recovery check, immediate optimize without writes, fresh-process reopen, content/vector checks, and a no-crash control. Neither was executed.

## Additional claims retained in the audit

- A and C overstate post-optimize Fetch/Query readability. Count can stay healthy, but fetch routing also requires writing-block metadata, which optimize has removed (`segment.cc:2483–2502`, `2743–2756`).
- A's assertion that `finish_memory_components` is the only caller of `segment_meta_->add_persisted_block` is too broad; other block registrations exist. Its compaction/WAL-deletion and next-open residue-cleanup extensions also omit conditions needed to reach those branches.
- C's “exactly one field” comparison and “only durability act is writing the manifest” are overbroad. Its RocksDB “write-through” rationale is not established: the configured RocksDB WAL is disabled, while close has a separate flush path. The cited explicit generator Flush is Android-only.

These are recorded as noncritical errors or unsupported extensions, rather than silently treating them as one of the rubric's enumerated critical contradictions.

All 16 rubric evidence spans and their source-file hashes match. Exact question, rubric, answer, contract, manifest, and all 21 inspected source-file SHA256 hashes are retained in `review-part1.json`, together with criterion rationales, answer quotes, verified citations, unsupported claims, and each gate check. No network/provider/CI calls, benchmark execution, implementation edits, neighboring result inspection, or reproduction were performed.
