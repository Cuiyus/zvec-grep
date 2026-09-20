# Retrieval-only metric validity review and revision

This document records the September 18, 2026 validity review. On September 20, 2026, legacy anchor scoring was removed from the current implementation, leaving five file-retrieval quality metrics, output size, and latency. See the [test design](./zg-retrieval-only-sweqa20-design.md) for the current protocol.

The review used the [ZG short/full CI run](https://github.com/Cuiyus/zvec-grep/actions/runs/35314877482) and [Semble CI run](https://github.com/Cuiyus/zvec-grep/actions/runs/35314877473) from commit `d8cc881`. Both used the same 20 original SWE-QA questions and 11 fixed repository commits. This revision changes scoring and reporting; it does not change retrieval, questions, Gold labels, source code, or historical outputs.

## Findings

The former strict-anchor metrics had limited value for detecting presentation regressions, but were unsuitable as the primary optimization target for general retrieval quality or answer-evidence quality. The standard Hit, RR, and MRR formulas were not the problem. Their relevance definition favored declaration entry points and particular source boundaries.

A low score alone does not invalidate a metric. More direct evidence is that identical retrieval rankings could receive different scores after changing only the output presentation. A declaration alone could earn credit, while a directly relevant implementation body could receive zero. Forcing declarations into the output to improve scores would not necessarily improve the evidence users receive.

## Independent inspection

| Check | Result | Implication |
| --- | --- | --- |
| Accepted targets | 57: 52 symbols and 5 code spans | Labels were organized mainly around symbol entry points |
| A declaration alone was sufficient for credit | All 52 symbols, covering 19 of 20 questions | Even when body anchors existed, the declaration could independently satisfy an OR alternative |
| Every anchor was declaration-only | 23 targets across 9 questions | Those targets' bodies had no independent way to earn a hit |
| Evidence that earned ZG short credit | 12 accepted matches; the first credited match in each of the 10 hit questions was a declaration | This does not mean the outputs lacked bodies; it means scoring did not require them |
| Actual effect of the outline exception | Improved the first anchor rank for django:21 without changing ZG's set of Hit@10 questions | Outlines cannot explain every difference; declaration labels and complete-text requirements were the main issue |
| Paired short/full retrieval identities | Identical in all 100 pairs, but anchor RR changed for 3 questions | The former metrics mixed retrieval and presentation behavior |

[The strict scorer at the time of the review](https://github.com/Cuiyus/zvec-grep/blob/c31fd9c82c46273b6b29fad2ca1ccf1d34554085/benchmarks/zg-retrieval/scoring.mjs) accepted an exact source anchor or a definition outline at the start of a result. Body anchors required the complete text of every specified line. The [Django label review record](../benchmarks/zg-retrieval/gold/v1/django-32.json) explicitly records the choice to use declaration-only first anchors. That choice can support entry-point navigation diagnostics, but does not establish that implementation evidence was retrieved.

The Gold records state that targets were initially selected from the questions and fixed source code, without using ZG retrieval outputs. This review found insufficient evidence to conclude that result leakage occurred. However, source cross-checks by two AI agents do not replace blind human review or validation against real task outcomes. A label design can favor a product's presentation even without observing its rankings.

For example, the body of `_deps` in `reflex:6` explains how `_fget` is passed to the dependency tracker, but the former label accepted only that method's declaration. A relevant error branch in `django:32` could be returned on its own yet receive no credit because another branch in the long anchor was absent. Conversely, returning only a file header for `streamlink:14` could earn full file-level nDCG credit. File metrics therefore do not establish evidence sufficiency either.

## Revised main-table definitions

The main table now treats Hit/RR/MRR as **localization metrics for annotated relevant files**. They use exactly the same `gold/semble-file-v1.json` as the existing upstream nDCG algorithm: 39 frozen file targets projected from the original accepted targets. Bridge-only files receive no credit.

For question q, let r be the first native rank among the top ten results that matches any annotated file:

- `Hit@K(q) = 1` if r exists and r ≤ K; otherwise 0. K is 1, 5, or 10.
- `RR@10(q) = 1/r` if r ≤ 10; otherwise 0.
- `MRR@10` is the arithmetic mean of RR@10 across all scoreable questions. A complete run of this suite includes all 20 questions, retaining zeros.

RR is a per-question value; MRR is its mean across questions. Five repetitions of one question must not be treated as five independent quality samples. The main-table MRR gives each question equal weight; nDCG uses a repository macro-average. These different weightings are labeled explicitly.

Matching follows Semble's path rules and checks only publicly returned paths. It does not require a function signature, outline, specific text, complete anchor, or snippet boundary. Native ranks are preserved without collapsing and renumbering repeated files. Changing source presentation alone cannot change the revised metrics.

The scoring contract is `sweqa-file-hit-rr-v1`. Public results are the sole scoring input; cached derived scores are not authoritative. Product-call failures still receive zero quality credit and fail operational integrity. Invalid experiments cannot publish valid aggregates, and unreviewed labels are not silently treated as zeros.

## Rescoring the same historical outputs

This table compares scoring definitions. It does not claim that the retrieval systems improved as a result of this change:

| Metric | ZG short | ZG full | Semble MCP |
| --- | --- | --- | --- |
| New: File Hit@1 | 7/20 | 7/20 | 10/20 |
| New: File Hit@5 | 10/20 | 10/20 | 15/20 |
| New: File Hit@10 | 11/20 | 11/20 | 18/20 |
| New: File MRR@10, question mean | 0.4056 | 0.4056 | 0.6267 |
| Unchanged: nDCG@10, repository macro-average | 0.2925 | 0.2925 | 0.5599 |

Comparing file RR per question, Semble leads on 11 questions, ZG leads on 2, and 7 are tied. The revised localization metrics make ZG's gaps in target-file retrieval and early ranking easier to see. They were not designed to raise ZG's scores or produce a preferred ordering between the products.

Strict-anchor and complementary-group nDCG metrics were initially retained as diagnostics. Their computation and output were removed on September 20, 2026. Current reports contain no legacy score tables.

## Remaining label limitations

File-level metrics reduce presentation bias; they do not turn the labels into complete, independently validated relevance ground truth. The dependency-tracking implementation file for `reflex:6` and the cookie-preparation helper for `requests:16` contain relevant information, but the original accepted/bridge distinction affects whether they earn credit.

The definition therefore remains “annotated relevant files.” It does not claim complete recall, sufficient evidence, or answer correctness. Hit changes in five-percentage-point increments on this 20-question suite. The suite is useful for small development regressions, but cannot by itself support general superiority claims.

Evaluating code evidence would require a separate, independently reviewed label set. Candidate evidence should combine results from multiple retrieval systems, keyword search, and manual inspection. Reviewers should assess relevant logical spans and acceptable alternatives for each question without seeing the candidates' origins, and annotation agreement should be measured. Deterministic scripts can still score subsequent runs. The current revision does not inflate scores by adding ad hoc agent-selected entry points, accepting arbitrary one-line overlap, or introducing loose text-similarity thresholds.

## Implementation and compatibility

Original queries, retrieval protocols, raw responses, and frozen labels remain unchanged. Current reports use ZG schema 3 and Semble schema 2. The five quality metrics are stored under `file_retrieval` and `semble_official`; the latter retains only nDCG@10 and its supporting target evidence. Legacy top-level anchor scores and mode summaries have been removed. Per-question first-file ranks and RR support the MRR calculation; they are not additional headline metrics.

The new `measurements` field records mean public output bytes from successful fifth calls and P50 latency across all successful search calls, with sample counts and metadata for all five repetitions. The unified manual CI publishes one results table, with ZG enabled by default and Semble optional. Historical comparisons recompute only current metrics; missing operational measurement evidence is shown as N/A.
