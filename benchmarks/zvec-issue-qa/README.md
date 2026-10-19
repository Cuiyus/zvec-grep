# Zvec closed-issue QA

The completed three-task development set was built in two separately frozen stages. The first task was selected before running either retrieval arm and tests code-grounded causal explanation across recovery, flushing, segment switching, and statistics. The later two-task stage covers scalar/full-text filtering and vector/forward row alignment. A separate q004 architecture/configuration task is now frozen below. This is not a representative estimate of zg's overall benefit.

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

## Design references and outcome interpretation

- [zg main's benchmark design](https://github.com/zvec-ai/zvec-grep/blob/a424e0ce06868dc9ad8f142f57de59b8f9b58699/benchmarks/README.md) distinguishes retrieval evaluation from agent e2e evaluation. This set follows the latter: fixed source and questions, paired tool exposure, separate setup accounting, and final-answer assessment.
- [Semble's benchmark](https://github.com/MinishLab/semble/blob/8ee6463efbb1ff755c339e965c7298b7afd7db5b/benchmarks/README.md) supplies useful retrieval diagnostics and semantic/architecture/symbol categories. Its simulated search/read token results are not agent-QA outcomes. q001–q003 cover causal debugging; q004 adds one architecture/configuration question, not full category coverage.
- [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph/blob/437b5c329fbdb8fe53a4440005168d463b1073e3/README.md), linked from Semble's methods discussion, is a relevant CodeGraph reference candidate; the user's intended same-name project is not confirmed. Its same-question MCP comparison and independent repeats inform the pairing design. The inspected README does not establish a blinded answer-quality rubric, so completion or lower tool usage alone cannot establish equivalent quality here.

The dataset's primary endpoint remains source-grounded success. Report quality
points, citation gates, input/output tokens and time separately. A higher score
at higher cost is a quality/resource tradeoff; a failed core gate remains a
failure even with a high score. Existing phases also freeze specific joint
quality/efficiency signal rules: evaluate those rules unchanged, without making
simultaneous improvement an unstated requirement for every future experiment.
The [reference audit](evaluator/provenance/design-reference-audit-20261019.json)
records source identities and limits; it neither changes frozen questions nor
selects tasks based on favorable solver outcomes.

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
pre-fix spans validated by the exporter. Construction used static source review;
it did not execute a native regression at either exact pre-fix commit.

The [expansion plan](../claude-qa-pilot/experiment-issue-expansion-v1.json) is frozen
for both tasks after the [q001 confirmation decision](../claude-qa-pilot/issue-expansion-decision-v1.json).
The earlier efficiency signal did not repeat; this separate batch explores task
dependence under unchanged completion-v2. It includes both tasks and keeps every
outcome, without pooling q001 development or confirmation into a new-task claim.

Both expansion pairs have now completed with full execution evidence and fresh
blinded source reviews. q002 scored 10/10 in both arms and passed both quality
gates; q003 improved from 6/10 to 9/10 with zg, but neither answer passed its core
source-evidence gates. zg used more input tokens and time in both pairs. The
[complete results](../claude-qa-pilot/results/20261019/README.md) retain every
answer and failed gate. These observations establish that the harness and tasks
can run end to end; they do not establish a stable quality or efficiency benefit.

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

## Separate architecture development case

`dataset-architecture-v1.json` freezes q004 from closed issue
[#758](https://github.com/alibaba/zvec/issues/758), using immutable source
`1a573058b24e11cdb7e91f27d6638c5f1c0cdcaa`. This is current implementation
behavior, including fixes, rather than a hidden-patch reconstruction. The issue
has no body or comments: its title supplies the thread-configuration topic,
while every question premise and scored claim must be verified from source.
No historical workload, observed thread count, or native runtime reproduction
is asserted.

The saved selection policy precedes candidate inspection. It orders a bounded
100-issue search pool by closing time and selects the first source-verified
eligible architecture question. #794 was rejected as a single test-helper
compiler regression; #758 was next. The policy, raw pool, ordering and source
checks are retained in `evaluator/provenance/q004/`. Prior task results were
known during design, but no q004 solver outcome existed. This is purposive
workload coverage, not random sampling or proof of no training contamination.

The question compares global optimization threads with explicit operation
concurrency and traces their effect on index-building/training components.
The rubric separates factual points from citation support and accepts equivalent
source evidence. It does not require every curated evidence span to be cited.
The independent [architecture plan](../claude-qa-pilot/experiment-architecture-v1.json)
is frozen for one baseline/zg pair; it keeps the previously
validated remote embedding stack and completion budget. Quality, retrieval
adoption and resources are reported separately, and every outcome is retained.
Older datasets, rubrics and phase-specific success rules remain unchanged.
Independent source and execution reviews found no blocking issues. The exporter
verified all 1,596 source files and 21 evidence spans; 93 offline checks passed.
These checks validate preparation, not a solver-quality or performance result.
