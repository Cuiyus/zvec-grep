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
