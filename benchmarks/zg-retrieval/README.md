# zg Retrieval-only

This regression suite uses the **20 unchanged SWE-QA questions across 11 pinned repositories** selected in [Actions run 35206585943](https://github.com/Cuiyus/zvec-grep/actions/runs/35206585943). Each of what/where/how/why has five questions. It tests retrieval through the packed product's public MCP tool, using the configuration produced by `zg install --target opencode --yes --mcp-transport stdio`. It does not run an answering agent.

## Protocol

- Hybrid search is primary, with `limit: 10`, `autoUpdate: false`, and `freshness: eventual` on a prepared index. Optional FTS/vector ablations use the **same original text**; there are no subqueries or query rewrites.
- Each question runs five times. Repetition 1 supplies quality scores; all five measure consistency. There are 20 quality samples, not 100. Hybrid alone makes 100 calls; all three modes make 300.
- Main metrics are visible source-entry Hit@1/5/10, first-hit rank, and RR@10/MRR@10. Accepted entries are OR alternatives. Duplicate results retain their original ranks; hidden candidate metadata cannot earn a visible hit.
- **12 questions** additionally define complementary evidence groups for nDCG@5/@10. A group and a result rank each receive at most one credit. OR alternatives within a group do not add coverage; the nDCG subset denominator is reported separately.
- Gold is a **partial set of source-verified positives**. Two independent AI agents proposed and cross-reviewed the labels against fixed source commits; this is **not human-reviewed ground truth**. Source hashes, exact anchors, review records, and reference-answer corrections are preserved.
- Every run builds a fresh index for each repository. Only model artifacts may be cached. Corpus, index content, and model identity are checked before/after queries. Gold, questions, and output files stay outside the search corpus.

The frozen inputs are in `data/source.lock.json` and `data/queries.jsonl`, settings in `configs/protocol.json`, and labels in `gold/v1/`. The historical run used a source-built package reporting version 0.2.1; current candidate identity is recorded by commit and tarball hash, not inferred from that version number.

## Run locally

Use Node.js 24, npm, Git, and a platform supported by the packed product. The first run needs network access for repository checkouts, package dependencies, and the local embedding model. Run these commands from the repository root:

```sh
# The scorer/contract tests use Node built-ins and need no npm install.
node --test benchmarks/zg-retrieval/test/*.test.mjs

npm ci
retrieval_work="$(mktemp -d)"
mkdir -p "$retrieval_work/package"
npm pack --json --pack-destination "$retrieval_work/package"

node benchmarks/zg-retrieval/run.mjs \
  --package "$retrieval_work/package" \
  --output "$retrieval_work/results" \
  --corpus "$retrieval_work/corpus" \
  --model-cache "$retrieval_work/model-cache" \
  --candidate-commit "$(git rev-parse HEAD)" \
  --modes hybrid

# Recompute the complete 20-question report from saved evidence.
node benchmarks/zg-retrieval/report.mjs "$retrieval_work/results"
```

`--package` accepts a tarball or a directory containing exactly one `.tgz`. The runner installs it in an isolated consumer; retrieval jobs do not use workspace `node_modules`. Use a new `--output` directory and a fresh corpus checkout for each run: output is never overwritten and an existing product index is rejected. A dedicated external model-cache directory can be reused.

Use `--modes hybrid,fts,vector` for the three-mode diagnostic. Add `--repository reflex-dev/reflex` for one repository, or `--tasks reflex:6` for a smoke run. The runner labels subset reports explicitly. The standalone `report.mjs` command always requires the complete 20-question suite; a smoke result is not a full benchmark.

## CI and artifacts

[Retrieval-only](../../.github/workflows/retrieval-only.yml) runs on relevant code/package/protocol changes to main and pull requests. Markdown-only changes do not trigger it. Manual dispatch selects hybrid or all three modes.

CI uses Ubuntu 24.04 and Node 24: built-in contract tests, one candidate tarball, then 11 repository shards with at most four in parallel. Each shard installs that candidate independently and rebuilds its index. The sole model cache key includes OS, architecture, the model catalog, and package lock; no corpus or index cache is restored.

- `package-retrieval-candidate`: the shared candidate tarball.
- `retrieval-data-<owner>__<repo>`: per-repository directories containing raw responses, requests, manifests, installation/index records, stage evidence, and the shard report. Consumer dependencies and runtime-home directories are excluded from uploads.
- `retrieval-summary`: combined `report.md`, `report.json`, and `scores.jsonl`. Repository data artifacts are downloaded into separate subdirectories before aggregation, preserving every shard. Raw-response links refer to that downloaded data layout.

Reports are appended to GitHub job summaries even on failure. Runtime/product errors, invalid output parsing, identity changes, or an incomplete call matrix fail CI. Failed product delivery remains a zero for reviewed questions; harness-invalid observations are N/A and block a valid aggregate. Retrieval quality thresholds are **report-only** until a reviewed baseline and regression policy are established.

For offline recomputation, download all `retrieval-data-*` artifacts under one directory, retaining their artifact subdirectories, then run:

```sh
node benchmarks/zg-retrieval/report.mjs /absolute/path/to/downloaded-shards
```

Compare two reports from the same source suite, Gold and protocol:

```sh
node benchmarks/zg-retrieval/compare.mjs baseline/report.json candidate/report.json NEW_OUTPUT_DIR
```

The comparison writes `comparison.json` and `comparison.md` with per-task rank changes, Hit flips, MRR/nDCG deltas and failure/recovery transitions. It rejects invalid experiments, missing/duplicate tasks and incompatible label or protocol identities. Product-error zeros remain in the denominator with an explicit operational warning. Comparisons are report-only; the tool does not impose a quality threshold.

## Semble baseline on the same questions

`semble-run.mjs` runs Semble **0.6.0 at commit `0051e000fcaac69a9c5d081ebbc8d4cb8508160b`** against these same 20 questions, 11 source commits and unchanged Gold. It calls the native stdio MCP `search` tool with the original question, `top_k=10`, `max_snippet_lines=10` and `content=all`. Native hybrid ranking stays enabled. Five repetitions run in one session per repository; repetition 1 supplies quality scores. The public interface does not expose separate FTS/vector modes or an auto-update disable switch.

The public JSON snippet is parsed into the same visible-source scorer. No chunk-range-only credit, source completion, query rewrite or Gold change is applied. Native chunks often start inside a function body; returning such a chunk without a frozen declaration/body anchor receives no entry credit. Reports therefore include a separate **Gold-file-presence diagnostic**, which checks only whether an accepted target's exact file path occurs in the top ten. This diagnostic does not contribute to Hit/MRR/nDCG or establish that the required code was visible.

`content=all` is an explicit whole-repository choice; Semble's default is code only. Semble still applies its own supported extensions, ignores, symlink policy and 1 MB file limit. Its code/docs/config scope excludes DATA formats such as JSON and CSV. The named potion-code model family is shared with zg, but model formats, preprocessing, chunking, ranking and rendering differ. This measures delivered source-entry retrieval under each product's public interface, not an isolated embedding comparison.

Use Python 3.12 and Node 24. Run from this repository root:

```sh
npm ci
semble_work="$(mktemp -d)"
git clone https://github.com/MinishLab/semble.git "$semble_work/source"
git -C "$semble_work/source" checkout --detach 0051e000fcaac69a9c5d081ebbc8d4cb8508160b
python3 -m venv "$semble_work/venv"
"$semble_work/venv/bin/python" -m pip install \
  -c benchmarks/zg-retrieval/configs/semble-requirements.txt \
  "$semble_work/source[mcp]"
mkdir -p "$semble_work/model"
HF_HOME="$semble_work/hf-cache" "$semble_work/venv/bin/python" \
  benchmarks/zg-retrieval/semble-prepare.py model \
  --revision e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b \
  --model-directory "$semble_work/model/weights" \
  --output "$semble_work/model/model.json"
node benchmarks/zg-retrieval/semble-run.mjs \
  --python "$semble_work/venv/bin/python" \
  --source "$semble_work/source" \
  --model "$semble_work/model/weights" \
  --model-info "$semble_work/model/model.json" \
  --output "$semble_work/results" \
  --corpus "$semble_work/corpus"

# Offline re-score; the optional second argument adds a compatible zg comparison.
node benchmarks/zg-retrieval/semble-report.mjs \
  "$semble_work/results" /absolute/path/to/zg/report.json
```

`--repository reflex-dev/reflex` selects an explicitly labeled smoke run. Full comparison requires all 20 questions. The Semble report keeps its own protocol identity and compares only common Gold quality; it does not bypass the same-engine protocol checks in `compare.mjs`, and it does not compute speed ratios across environments.

Preparation uses the unmodified public `SembleIndex.from_path` API and persists a fresh external index. Installed Python source files are checked against the pinned checkout. Model files are frozen across the entire experiment, and corpus/model/index inventories are checked before and after each session. Returned snippets are bound to persisted chunks and their source positions without adding unseen content to scoring. Semble's UTF-8 replacement decoding is retained when auditing non-UTF-8 source fixtures.

The separate [Semble CI workflow](../../.github/workflows/retrieval-semble.yml) runs on relevant baseline/shared-protocol changes and manual dispatch. It pins source, model revision and Python dependency constraints, caches only model downloads, and executes all 100 calls. Its `retrieval-semble-evidence` artifact contains the reports, requests, raw responses, runtime identity, source audits, inventories, chunks and metadata. Large BM25/vector payloads and external model weights are excluded; their captured file hashes remain in the inventories. Local runs retain the complete persisted index. Evidence is retained for 14 days, including failed runs.

## Interpretation limits

These are public development/regression questions, not an unseen generalization test. Entry hits do not establish answer correctness, exhaustive relevance, or complete multi-file evidence. No byte-window hit, output-byte, first-hit-byte, or agent token-saving metric is used.

Stage evidence covers file inventories, scanner output, persisted chunks, source mappings, and stored vector hashes. Actual embedding inputs and full candidate/fusion histories are currently unobserved; a miss is not proof of an embedding defect. Five repeats support consistency checks, not reliable tail-latency estimates.

Historical E2E answer scores, token use, and tool calls may be joined by task ID for investigation. Fixed original-query results cannot explain an agent's rewritten queries or prove a causal E2E improvement.
