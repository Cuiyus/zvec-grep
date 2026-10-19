# Independent blinded source audit — merged review

Reviewer: Codex independent source review, not a human or official SWE-QA grader. Source grades only; no experimental labels, model/runtime outcomes, or executed reproductions are inferred.

| Anonymous answer | Original review | Total | Complete source-grounded success gate |
|---|---|---:|---|
| answer-A | Part 1, preserved | 9/10 | Pass |
| answer-B | Part 2 | 9/10 | Pass |
| answer-C | Part 1, preserved | 8/10 | Pass |
| answer-D | Part 2 | 7/10 | Fail: fewer than 8 points and C2 below full credit |

The detailed review records in `review.json` are copied unchanged from their original parts, including decisions, rationales, quotes, verified citations, unsupported claims, and gate checks. A and C were not rescored after B and D became available. The original part1 report files remain byte-identical. The two part summaries below are preserved verbatim.

## Manifest and report provenance

- **part1**: saved manifest `answers-manifest-part1.json`, SHA256 `6afb3d6d8021b046c2b8a543201c08bb431a587a924c3a8c5c76b342e0547c80`. JSON report `review-part1.json`, SHA256 `df76de31b0999e51cecee564f9211b3777982b0375b0a6a59d592f1e673cdf6b`; Markdown report `review-part1.md`, SHA256 `45394156c943dc2f3180d30fdd03bea4c968307b86c92048c63904d7ef32fd9e`.
- **part2**: saved manifest `answers-manifest-part2.json`, SHA256 `ba66fdd948e9f67d90b5a7960b6d76eb2dd104a9727f97db1174d00ae9d92eed`. JSON report `review-part2.json`, SHA256 `1dd7e73c805fd008bcfc9b1e373c64149055b7abffa0c0ca01cbf98107ac8a2d`; Markdown report `review-part2.md`, SHA256 `b946c5d4b17b349431dd39610836fef894e08e75d6ebe25056d7b22bf6529fd8`.

The part1 report's original `answers-manifest.json` hash resolves to the saved `answers-manifest-part1.json`, not the later all-four manifest. Part2 uses `answers-manifest-part2.json`, byte-identical to the current manifest. Every question/rubric/answer hash and all 28 inspected source hashes are retained in the merged JSON. All 16 rubric evidence file/span hashes match. No source implementation or original part1 file was changed.

---

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

---

# Independent blinded source audit — part 2

Reviewer: Codex independent source review, not a human or official SWE-QA grader. Only the additional anonymous final answers B and D were graded. Part 1 decisions and files remain unchanged.

| Anonymous answer | C1 | C2 | C3 | C4 | C5 | C6 | C7 | Total | Source-grounded success |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| answer-B | 2 | 2 | 2 | 1 | 1 | 0 | 1 | **9/10** | **Yes** |
| answer-D | 2 | 1 | 2 | 1 | 1 | 0 | 0 | **7/10** | **No** |

B meets the complete original success gate: at least 8 points, full C1–C3, verified core citations, and no listed critical contradictions. D fails both the minimum-point threshold and required full credit on C2. D's cited core lines resolve: the C2 deduction concerns an omitted logical arm, not an invented or false citation. Neither answer contains one of the rubric's five enumerated critical contradictions.

## Deductions under the exact rubric

- **B, C6 = 0:** Correctly proposes adoption/reopening of the member WAL, but does not require checking and propagating a failed reopen. C6 explicitly includes error handling and has no partial point.
- **D, C2 = 1:** Correctly identifies the null-member early return and skipped persistence/finalization, but never states the `!wal_file_->has_record()` arm as part of the exact no-op guard. The cited span includes it; an implicit source reference does not replace the required explanation.
- **D, C6 = 0:** The attach-WAL direction lacks error handling. The alternative dirty flag is asserted equivalent without proving WAL cleanup when the member remains null; literally OR-ing it into the existing early-return guard also has the wrong polarity.
- **D, C7 = 0:** Gives the main crash/recover/optimize-without-writes/reopen checks, but supplies no no-crash or already-flushed regression control. The general control explanation earlier in the answer is not a proposed test arm.

D explains both live counts and metadata-derived completeness, so C4 receives its factual point. Its `collection.cc:403–431` citation stops before `vector_indexed` and the completeness calculation at 433–438; the correct neighboring code was independently checked and the citation shortfall is retained.

## Additional claims retained in the audit

- B overstates identical recovered/ordinary state, calls the WAL unreferenced before writing metadata is removed, and generalizes zero forward blocks into zero blocks. Its assertion that any two persisted segments require compaction omits grouping thresholds. The quoted `Segment not found` status comes from an internal fetch helper; public Fetch returns a null map entry for a missing segment.
- D overstates the blast radius to every invocation of several APIs, despite early returns before the switch. Its claim that idmap entries reach disk only through the segment's `id_map_->flush()` omits the close-time RocksDB flush. The cited `has_record` span also stops one line before its size comparison.
- Both correctly identify the local-reader/member-WAL mismatch, the optimize→switch→dump→flush call chain, and writing-block metadata removal as the loss of automatic recovery reachability. Neither was credited with having executed a reproduction.

All 16 rubric evidence spans and hashes match. `review-part2.json` retains exact input hashes, 28 inspected source-file hashes across this review session, answer quotes, full rationales, verified citations, unsupported claims, and gate checks. No network/provider/CI calls, reproduction, implementation change, or excluded-result inspection occurred.
