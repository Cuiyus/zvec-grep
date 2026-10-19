# Zvec closed-issue QA

This is one development QA task, selected before running either retrieval arm. It tests code-grounded causal explanation across recovery, flushing, segment switching, and statistics. It is not yet an independently validated benchmark or a representative estimate of zg's overall benefit.

The source repository is `alibaba/zvec`. The pilot uses the parent of the fixing commit, `ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d` (2026-07-16). The fix and its newly added regression test are absent. The source has been inspected, but the native bug reproduction has not been executed as part of constructing this task.

A later [auxiliary native check](../claude-qa-pilot/results/20261019/zvec-q001-native-validation/README.md) exercised an already installed post-fix 0.7.0 package with crash/recovery/optimize/fresh-process reopen and a no-crash control. Both retained all 150 IDs, scalar values, and vectors within a fixed tolerance. It does not validate the frozen pre-fix commit or replace a failure/pass regression at that exact source; package publishing metadata is not binary build attestation. The initial FP32 fixture failure is retained separately. This check did not change the task, rubric, or solver corpus.

Files:

- `dataset.json`: runner metadata and frozen selection policy; do not expose it to the solver.
- `tasks/q001/instruction.md`: the only task-specific solver input.
- `evaluator/q001.json`: reference answer, scored claims, verified source spans and hashes; evaluator only.
- `evaluator/provenance/`: issue and PR snapshots; evaluator only.
- `evaluator/candidates.json`: the considered candidates and pre-run rationale.
- `prepare_corpus.py`: exports the exact commit without Git history, validates evidence, writes an inventory outside the corpus, and checks known leakage markers.

Prepare from an existing clone with the frozen Git object:

```bash
python3 benchmarks/zvec-issue-qa/prepare_corpus.py \
  --repo /path/to/zvec-clone \
  --output /path/to/isolated/source \
  --manifest /path/to/controller/corpus-manifest.json
```

Run the solver in an isolated environment that contains only the exported source, its prompt, and the allowed tools. The benchmark directory, evaluator files, issue/PR snapshots, `.git`, host home directories, prior transcripts, and run results must not be accessible. Disable web/repository-history tools. Giving the solver an ordinary host shell with parent-directory access does not satisfy this isolation rule. Keep the model service reachable while restricting solver tool access to external content.

Use the same exported source and exclusions in both arms. For the main e2e comparison, keep the same model, model revision/endpoint, agent/version, exploration instructions, turn/token/wall-time limits and independent initial context. The baseline keeps its normal exact-search/read tools; the treatment adds zg with neutral usage documentation, with normal tools still available. Do not force a minimum zg call count into the task. Report actual zg adoption and failures. If testing a mandatory zg-first policy, report it as a separate strategy experiment.

Prebuild the index outside the timed solver phase, and report indexing/model-load costs separately. Record zg version and exact model configuration. The inventory must cover all eligible files; if either search tool silently omits evidence files, classify it as an infrastructure failure instead of an answer-quality failure. Record raw tool responses and all model usage, including cache-read/write tokens where available. Report total input tokens, uncached input tokens, output tokens, tool calls, time, per-claim QA score, citation validity, and evidence coverage independently.

Score arms blind using `evaluator/q001.json`. A path hit or a one-line overlap is not sufficient evidence. Confirm the cited text actually supports the relevant claim; allow equivalent valid source evidence. For this pilot, all failure outcomes and all attempted runs stay in the result table. Pair repeats by task and trial; repeated runs of one task are not independent tasks. Freeze prompt and rubric before collecting results. Any later prompt/rubric change creates a new dataset revision and invalidates comparisons to earlier trials.

The primary pilot endpoint is successful, source-grounded explanation (at least 8/10 points, all three causal criteria complete, and no critical contradiction). Cost comparisons are meaningful only alongside answer quality; if quality differs, show the tradeoff instead of claiming unconditional efficiency improvement. One pair is a smoke test. Three or more paired repeats can show instability but cannot establish generalization. Next expand with a predeclared issue eligibility/stratified sampling policy and freeze a held-out set before tuning.

Repository recency may reduce some memorization risk, but it does not prove absence from any model's training data. These are public issues and commits. This pilot relies on frozen pre-fix source, withheld issue text, citations and isolated execution; it makes no contamination-free claim.

## Separately prepared issue expansion

The original `dataset.json` and q001 question/rubric remain unchanged. A separate
`dataset-expansion-v1.json` contains two reserve candidates from the original
selection ledger. Their questions and scoring rules were prepared without seeing
solver answers on these tasks. The preceding q001 development results were known;
this is purposive task expansion, not random sampling or a contamination-free test.

| Task | Source scenario | Exact pre-fix commit | Pre-fix evidence spans |
| --- | --- | --- | ---: |
| q002 | Full-text queries lose a zero-match indexed scalar restriction | `e906168cc19378358d8fa1c1559f48e4b920aa37` | 17 |
| q003 | First requested HNSW build misaligns vector keys and retained forward rows after deletion | `68078e55e7ae9d705b8829b5d416e022060bebf7` | 29 |

Both packages have English scenario-only questions, 10-point rubrics, explicit
critical-claim/citation gates, and independent static source reviews in their
`evaluator/provenance/q002/` and `q003/` directories. Reporter explanations were
checked against source; q003 preserves the later discussion's narrower trigger
and controls. Fix-only evidence remains evaluator-only and is excluded from the
pre-fix spans validated by the exporter. No engine reproduction or solver result
is claimed for these two tasks.

The [expansion plan](../claude-qa-pilot/experiment-issue-expansion-v1.json) is frozen
for both tasks after the [q001 confirmation decision](../claude-qa-pilot/issue-expansion-decision-v1.json).
The earlier efficiency signal did not repeat; this separate batch explores task
dependence under unchanged completion-v2. It includes both tasks and keeps every
outcome, without pooling q001 development or confirmation into a new-task claim.

Export one task explicitly from a clone containing its exact Git object:

```bash
python3 benchmarks/zvec-issue-qa/prepare_corpus.py \
  --dataset benchmarks/zvec-issue-qa/dataset-expansion-v1.json --task q002 \
  --repo /path/to/zvec-clone \
  --output /path/to/isolated/q002-source \
  --manifest /path/to/controller/q002-corpus-manifest.json
```

Both output and manifest must be fresh paths outside the benchmark and Git
directories, and the manifest must be outside solver source. Multi-task datasets
require an explicit ID. The exporter checks task/rubric identity, file and span
hashes, line ranges, the complete source inventory, and known leakage markers.
It rejects path escapes, unsupported archive links and existing manifest files.
The unchanged default q001 export remains byte-identical to its earlier manifest.

Run the offline exporter checks with
`python3 -m unittest discover -s benchmarks/zvec-issue-qa -p 'test_*.py'`.
