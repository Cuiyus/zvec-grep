# zvec q001: ten baseline and ten zg executions

The fixed study of 20 executions is complete. **It does not establish a stable favorable benchmark.** Reported input saving is repeatable on this case, but full end-to-end speed and quality preservation are not established. Score-tail filtering did not make the resource comparisons more precise.

[Fork run 39364155852](https://github.com/Cuiyus/zvec-grep/actions/runs/39364155852) completed all 10 pairs, attempt 1, at commit `aa230d91eb426eb295cf95fe0fa2d80d52f588e2`. Baseline 10 and zg 10 are all retained; no retry, replacement, early favorable stop, historical pooling or cancelled-run pooling. This replaces the cancelled campaign of 60 executions with the user-requested q001-only cohort.

## Raw 10 resources per profile

The following resource values and paired 95% bootstrap intervals are identical under all three rating interpretations, because all 20 original executions are used.

| Metric | Baseline mean | zg mean | zg reduction and 95% interval |
|---|---:|---:|---:|
| Reported input tokens | 2,191,899.5 | 1,401,041.0 | 36.08% [24.18%, 45.09%] |
| Agent execution seconds | 1,085.8 | 918.8 | 15.38% [4.34%, 25.74%] |
| Full outer-slot seconds | 1,123.2 | 1,118.0 | 0.47% [-11.39%, 11.42%] |

Input includes Claude-reported uncached input, cache reads and cache writes; output is separate. These are not provider bills. Agent execution excludes setup. Full outer slot includes fresh setup/indexing, whose extra time largely offsets agent-stage savings here. All 20 reported usage aggregates matched complete persisted session trees.

## Quality interpretation remains partly unknown

The original rubric's C7 requires comparing known documents and vectors after a fresh reopen. Initial grading sometimes accepted successful known-pk fetches or related checks without full payload comparison. The original scorer and an independent reviewer rechecked all 20 anonymous answers before mapping read. They agreed on 17 C7 points and disagreed on 3. The primary view preserves 3 unknown exact scores and 2 unknown pass decisions; it does not zero-fill or drop them. A complete primary score/score-trimmed statistic is therefore unavailable.

Under the agreed portion and remaining original-rule ambiguity, baseline has **5/10** quality passes; zg has **2–4/10**. The initial ratings, scorer's C7 clarification and consistency review are retained separately. Their complete numeric statistics below are diagnostics, not interchangeable primary results. C1–C6 and core-citation/critical-contradiction gates were preserved; no relaxed C6 sensitivity replaced the original rule.

## Raw 10 versus score-trimmed 8 diagnostics

Each profile loses exactly one minimum and one maximum score. All metrics use the same retained tuples; ties use trial index then position. Selection is independent between profiles. Intervals resample whole pairs 10000 times (seed 20261020) and recompute trimming each draw. Positive reduction means zg used less; negative means zg used more.

| Rating interpretation | Raw quality B / zg | Trimmed quality B / zg | Trimmed input reduction | Input CI width change | Agent-time CI width change | Trimmed full-slot reduction |
|---|---|---|---:|---:|---:|---:|
| initial-grades | 5/10 / 6/10 | 4/8 / 5/8 | 36.58% | +17.69% | +12.50% | 3.66% |
| scorer-c7 | 5/10 / 2/10 | 4/8 / 1/8 | 35.59% | +19.44% | +24.37% | -0.58% |
| consistency-c7 | 5/10 / 4/10 | 5/8 / 3/8 | 32.30% | +34.28% | +11.61% | -1.76% |

The input-saving interval widens by 18%–34% after filtering across these interpretations. Agent-time and full-slot intervals also widen in every interpretation. No interpretation meets even the statistical-only unchanged favorable criterion; scope validity separately fails. Trimming is not evidence that underlying solver randomness changed.

| Interpretation | Profile | Score sample SD raw → trimmed | Input CV raw → trimmed | Agent-time CV raw → trimmed |
|---|---|---:|---:|---:|
| initial-grades | baseline | 1.619 → 0.991 | 24.69% → 24.15% | 14.11% → 12.98% |
| initial-grades | zvec-grep | 0.483 → 0.463 | 23.77% → 25.24% | 16.69% → 18.90% |
| scorer-c7 | baseline | 1.434 → 0.707 | 24.69% → 24.15% | 14.11% → 14.79% |
| scorer-c7 | zvec-grep | 0.422 → 0.354 | 23.77% → 23.04% | 16.69% → 18.65% |
| consistency-c7 | baseline | 1.506 → 0.835 | 24.69% → 26.83% | 14.11% → 15.33% |
| consistency-c7 | zvec-grep | 0.516 → 0.518 | 23.77% → 25.57% | 16.69% → 12.14% |

| Interpretation | Profile | Score-tail exclusions |
|---|---|---|
| initial-grades | baseline | r06 (low, score 4), r10 (high, score 9) |
| initial-grades | zvec-grep | r04 (low, score 7), r10 (high, score 8) |
| scorer-c7 | baseline | r02 (high, score 9), r06 (low, score 4) |
| scorer-c7 | zvec-grep | r02 (low, score 7), r07 (high, score 8) |
| consistency-c7 | baseline | r05 (high, score 9), r06 (low, score 4) |
| consistency-c7 | zvec-grep | r03 (low, score 7), r10 (high, score 8) |

One fixed task cannot enable the task-level MAD filter. Ten repetitions are not ten different tasks. The analyzer's `all_tasks` field duplicates q001's single-task estimate and is not an additional sample.

## Validity and provenance

All 10 pairs passed 63 execution/accounting checks each. Ten hosted runners, 20 distinct root sessions and 40 runner-qualified fresh volume identities were verified; no child sessions were present. All 1232 frozen source files stayed unchanged. Independent source review covered 739 actual tool calls; parent verification rehashed 1667 per-pair evidence references.

Strict source scope is true for 15 executions, false for 3 and unknown for 2. The known violations are r08 in both profiles and r09 baseline. r02/r04 baseline have outside fallback branches whose execution could not be determined. r08 zg read an outside status file containing `0`; no outside code/reference-answer facts were observed, and no project-test execution or answer pollution is inferred. r07 zg used no zg retrieval, including through Bash. Every failure/unknown remains visible even if a score trim removes the corresponding row. Only 14/20 pass all source and required-retrieval gates, so whole-cohort strict validity fails.

This is custom zvec QA from [closed issue 594](https://github.com/alibaba/zvec/issues/594), frozen source `ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d`, Claude Code 2.1.212 plus Qwen3.8-max. Native harness and Rust build are pinned to `a424e0ce06868dc9ad8f142f57de59b8f9b58699`. Preflight observed upstream main `49de68563a7d3da9b408dd64277a8a51e3dd0fc6`, whose Rust subtree `de02030056a3224012725799d845bf2be9edae2b` was identical. No cross-task, provider-billing or general model-population claim follows.

Original source-grounded model grades, clarification and consistency artifacts were saved before explicit analysis mapping read. Reviewer reuse and exposure are self-reported, not independently certified from complete tool histories. Root had labelled execution/source/resource evidence and is not claimed blind. Exact anonymous answers, unblinding receipts and all disagreements remain retained.

See [summary.json](summary.json), [analysis](analysis/), [grading](grading/), [anonymous packet](anonymous-grading-packet/), [per-pair evidence](pairs/), [source cohort checks](source-cohort-evidence/) and [final execution/CI evidence](execution-cohort-evidence/). Large original artifact ZIPs and duplicate install packages are retained locally with exact hashes and API/byte-verification receipts; they are omitted from Git. Every other archived byte is covered by the phase manifest. The preserved first-five status is an earlier immutable partial snapshot, not this phase's final status.
