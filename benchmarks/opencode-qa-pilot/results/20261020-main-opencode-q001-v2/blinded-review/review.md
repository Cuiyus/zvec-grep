Independent blinded Codex source audit. This is not a human review or an official SWE-QA score. Only the supplied question, rubric, two answers, anonymous manifest, and frozen source were examined. No reproduction or runtime validation was performed. All five input hashes match the recorded files; all 16 rubric file/span hashes match the source.

| Answer | C1 | C2 | C3 | C4 | C5 | C6 | C7 | Points | Source-grounded success |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| A | 2 | 2 | 2 | 0 | 1 | 1 | 1 | 9/10 | Yes |
| B | 2 | 2 | 2 | 1 | 1 | 1 | 0 | 9/10 | Yes |

Both answers support the critical chain: local recovery reader and internal replay (`segment.cc:4193-4301`, `837-881`), null-member early return (`2197-2222`), and Optimize/switch removal of writing metadata followed by version persistence (`collection.cc:779-816`, `1517-1552`). Later Open only replays a writing block (`segment.cc:461-517`); absent persisted SCALAR blocks, it cannot reconstruct the lost doc ids (`3913-3957`). Both meet the supplied success rule and contain none of its listed critical contradictions.

A loses C4 because it explains live document counting but does not explain Stats index completeness via `vector_indexed` metadata (`collection.cc:425-438`). It also overstates that Fetch/Query continue to observe retained RAM after optimization: memory fetch routing requires the removed writing metadata, and Fetch checks the metadata maximum (`segment.cc:1102-1105`, `2483-2498`, `2744-2756`). Its primary process-crash regression and no-crash control satisfy C7; the optional unit-test idea must bypass normal close because `SegmentImpl::close()` itself flushes (`551-552`).

B loses C7: its detailed sequence only specifies a new handle in the same process, and its control adds a write after recovery rather than testing a no-crash or already-flushed collection. It correctly explains Stats counts and index-completeness metadata.

C6 is credited to both for an explicitly stated valid member-WAL retention/reopen lifecycle repair. For B, this credit is specifically for alternative (a), not its final recommendation (b). A guard-only repair leaves WAL cleanup skipped while the member is null (`segment.cc:2305-2315`), and memory-row predicates miss delete-only dirty state (`897-900`, `2266-2285`). B's proposed equality guard is also insufficient: `SegmentMeta::doc_count()` includes the writing block (`meta.h:307-317`), so it can equal the live count without persisted coverage. Both should make temporary-reader ownership and error propagation explicit when implementing the valid lifecycle repair.

The JSON report records per-criterion quotes, verified citations, regression conditions, noncritical unsupported claims, and hashes. The scores reflect the original seven criteria only; peripheral factual errors were not converted into invented criteria. No end-to-end test outcome has been verified.
