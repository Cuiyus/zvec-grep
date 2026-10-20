# zvec q004: ten baseline and ten zg executions

All 20 executions and both independent anonymous reviews are complete. Complete
reported input decreased 50.66%, but quality preservation and whole-cohort strict
validity are not established. This fixed architecture case therefore does not
establish a stable favorable benchmark. Four material grading judgments remain
unknown, and filtering does not repair source or retrieval failures.

[Fork run 39380248003](https://github.com/Cuiyus/zvec-grep/actions/runs/39380248003)
completed attempt 1 at `3978d4bbbbb1e37961c277cdf76696b84c69e3bc`. Ten fresh
pair runners executed five baseline→zg and five zg→baseline pairs. No retry,
replacement, favorable early stop, cancelled-run pooling or historical pooling
occurred. The earlier one-pair q004 development result is excluded.

## Complete raw resource comparison

| Metric | Baseline mean | zg mean | zg reduction and paired 95% interval |
|---|---:|---:|---:|
| Reported model input tokens | 1,490,122.2 | 735,291.0 | 50.66% [39.92%, 60.17%] |
| Agent execution seconds | 717.32 | 542.17 | 24.42% [13.25%, 33.93%] |
| Full cold outer-slot seconds | 755.69 | 774.37 | -2.47% [-15.88%, 8.61%] |

Positive reduction means zg used less. Input includes uncached input, cache
reads and cache writes; output is separate. All complete persisted root-session
totals match native terminal modelUsage. These counts are not provider bills;
embedding usage and monetary cost remain unknown. Agent time excludes setup;
the outer slot includes fresh installation, indexing and capture overhead.
Across ten zg executions, native setup totalled 1,941.317 seconds, including
1,859.848 seconds of indexing. No warm-index speed estimate is inferred.

Intervals use the prospectively fixed 10,000 whole-pair bootstrap draws, seed
20261020. They describe this question and stack, not a model population or
cross-task generalization. All 20 original executions contribute to this table.

## Quality retains four unknowns

| Profile | Known pass | Known fail | Unknown | Possible pass count, not an observed rate |
|---|---:|---:|---:|---:|
| baseline | 9 | 0 | 1 | 9–10 / 10 |
| zg | 7 | 0 | 3 | 7–10 / 10 |

The reviewers agree on 196/200 atomic factual points and 16/20 complete answer
decisions. Four C4.2 judgments remain unresolved. The original rule distinguishes
the separate PQ outer pool from its capacity: `max(1,min(builder count,caller
count))`. Hardware≥4 alone does not establish exact capacities four/two when a
smaller independent builder setting is possible. The reports differ over the
scope of answers that state the formula but also assert exact values using the
ordinary wrapper/default path. Both original interpretations and their source
evidence remain unchanged; the comparison chooses no favorable replacement.

The original primary reviewer assigned nine points and a failed mandatory C4
gate to these four answers; the independent reviewer retained null. The main
view therefore preserves four unknown scores and four unknown quality decisions,
without dropping observations or zero-filling. Complete primary quality and
score-tail selection statistics are unavailable. General mechanism citation
agreement does not replace the four broader unresolved original gates.

## Filtering is a separate diagnostic

The original primary review supplies a complete numeric interpretation, retained
as a diagnostic. Its raw quality counts are baseline 9/10 and zg 7/10; score-tail
selection gives baseline 8/8 and zg 6/8. These are not the main consensus results.
Exactly one lowest and one highest score are removed per profile, with original
trial index then position as tie breakers. Every resource metric uses the same
retained tuples; the selection is recomputed within every paired bootstrap draw.

| Resource reduction | Raw 10 | Original-primary diagnostic trimmed 8 | Interval-width change |
|---|---:|---:|---:|
| Input | 50.66% [39.92%, 60.17%] | 52.81% [39.45%, 61.79%] | +10.32% |
| Agent time | 24.42% [13.25%, 33.93%] | 27.27% [13.23%, 36.76%] | +13.76% |
| Cold outer slot | -2.47% [-15.88%, 8.61%] | 0.80% [-16.09%, 11.56%] | +12.91% |

All three intervals widen in this diagnostic. Filtering has not demonstrated
reduced randomness here. A single fixed task cannot activate task-level MAD;
ten repetitions are not ten distinct tasks. No favorable statistical or validity
conclusion follows from selecting the larger input-saving point estimate.

## Execution, scope and retained evidence

All 730 original execution checks and 57 cohort checks pass: ten distinct hosted
runners, 20 root sessions, no child sessions, and 40 runner-qualified fresh volume
identities. Independent source review covers all 780 tools and 328 MCP hits.
All 1,596 frozen source files stayed unchanged. Factual-evidence scope is true
for every execution; no outside file content, code or answer facts were observed.

| Observation | Original gate result |
|---|---|
| r02 baseline | strict=false: unconditional outside home-directory probes, names-only metadata |
| r03 zg | strict=null: outside home fallback lacks separate branch/status evidence |
| r04 zg | strict=false: enumerated `/logs/agent/sessions/`, returning nine names |
| r07 zg | strict=true, required retrieval=false: no MCP, CLI or other actual zg query |

Strict scope is true for 17 executions, false for two and unknown for one.
Only 16/20 pass all source and required-retrieval gates. These results remain
visible regardless of score-tail exclusions and block a favorable valid-cohort
claim independently of grading uncertainty.

Source is pinned to `1a573058b24e11cdb7e91f27d6638c5f1c0cdcaa`, inventory
`129579aae937735ed79c444bd6f83a05e42058a679e0077ff39599c9ce5d4eef`.
Claude Code 2.1.212/Qwen3.8-max and the original native harness are retained.
Preflight required actual upstream main's complete Rust tree to equal the
pinned binary tree `de02030056a3224012725799d845bf2be9edae2b`; this does not
claim the entire older harness equals latest upstream main.

Both independent reports and the later anonymous comparison were saved before
explicit condition unblinding. Reviewer reuse and exposure are disclosed, with
blindness self-reported rather than independently certified from complete tool
histories. The parent had labelled resource/source evidence and is not claimed
blind. Narrow host-replay/schema and floating-arithmetic reconciliation records
are preserved; no original trial, grading report or statistical rule was changed.

See [summary.json](summary.json), the [execution manifest](execution/retained-evidence-manifest.json),
[source-review manifest](source-scope/retained-evidence-manifest.json) and
[grading/analysis manifest](grading-analysis/retained-evidence-manifest.json).
Every archived original byte is verified, including exact gzip restoration for
large files and raw text streams. Original API-bound ZIPs and duplicate static
install packages remain locally at their manifest paths and hashes. Four quality
unknowns, failed probes, native diagnostics and all original anonymous answers
remain available. No new question is added by this result.
