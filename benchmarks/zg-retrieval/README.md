# zg Retrieval-only

This regression suite uses the **20 unchanged SWE-QA questions across 11 pinned repositories** selected in [Actions run 35206585943](https://github.com/Cuiyus/zvec-grep/actions/runs/35206585943). Each of what/where/how/why has five questions. It tests retrieval through the packed product's public MCP tool, using the configuration produced by `zg install --target opencode --yes --mcp-transport stdio`. It does not run an answering agent.

## Protocol

- Hybrid search is primary, with `limit: 10`, `autoUpdate: false`, and `freshness: eventual` on a prepared index. Optional FTS/vector ablations use the **same original text**; there are no subqueries or query rewrites, answering agents or LLM judges.
- Each question runs **five consecutive times per source-preview arm**, matching the fixed Semble SDK quality runner. **Repetition 5** supplies quality scores; all five measure consistency. There are 20 quality samples **per arm**, not 200 independent questions. Hybrid alone makes 200 calls; all three modes make 600. Protocol v3 cannot be mixed with earlier one-arm protocols.
- Both `preview: "short"` (the product default) and `preview: "full"` run on the **same prepared index and MCP session**. Fixed order is mode → question → preview → repetition: short five times, then full five times. The only request difference is `preview`. Full displays all available retrieved-item source content and outline; it does not expand each hit into its entire file or fetch hidden source for scoring. Native extraction/chunk limits remain.
- The new primary metric is **Semble official-algorithm nDCG@5/@10** on all 20 questions. The displayed primary aggregate is the repository macro mean, matching upstream saved JSON. Query mean and language macro mean (upstream terminal convention) are separately labeled. See the exact contract below.
- Existing visible source-entry Hit@1/5/10 and RR@10/MRR@10 remain **diagnostics**. Accepted entries are OR alternatives. Duplicate results retain their original ranks; hidden candidate metadata cannot earn a visible anchor hit.
- The legacy grouped nDCG diagnostic covers **12 questions** with complementary evidence groups. It is distinct from the new Semble metric and keeps its own denominator.
- Both runs use code-only indexing and a 1,000,000-byte file limit. zg uses the frozen Semble CODE extension list through native index glob filters; Semble uses `content=code`. Native ignores, supported extraction, chunk boundaries and ranking remain product behavior. This does not promise identical candidate-file inventories; both inventories are retained.
- Gold is a **partial set of source-verified positives**. Two independent AI agents proposed and cross-reviewed the labels against fixed source commits; this is **not human-reviewed ground truth**. Source hashes, exact anchors, review records, and reference-answer corrections are preserved.
- Every run builds a fresh index for each repository. Only model artifacts may be cached. Corpus, index content, and model identity are checked before/after queries. Gold, questions, and output files stay outside the search corpus.

The frozen inputs are in `data/source.lock.json` and `data/queries.jsonl`, settings in `configs/protocol.json`, and labels in `gold/v1/`. The historical run used a source-built package reporting version 0.2.1; current candidate identity is recorded by commit and tarball hash, not inferred from that version number.

The shared report table contains both official Semble nDCG and original anchor metrics for short/full. `paired_preview_comparison` checks ordered rank/path/range/matched-range/match-type and official nDCG for every paired repetition. It excludes displayed text and outline from retrieval identity. A mismatch is reported as an uncontrolled retrieval difference, not silently attributed to preview length. Visible UTF-8 output bytes measure response size only, not tokens, latency or answer quality. Fixed short-before-full ordering means latency differences may include warming and are not a controlled speed comparison.

In report schema v2, `tasks` contains both arms, `previews.short` and `previews.full` hold separate aggregates, and `modes` aliases the primary short arm. Never aggregate all 40 quality rows as independent questions. Same-version comparisons pair matching arms; Semble comparisons show zg short, zg full and Semble full chunk in one metric table.

## Semble official metric contract

`semble-metrics.mjs` ports `benchmarks/data.py` and `benchmarks/metrics.py` at upstream commit `0051e000fcaac69a9c5d081ebbc8d4cb8508160b` without changing their relevance or gain rules:

- Path matching uses the upstream normalized-separator/exact-or-path-segment-suffix rule.
- A file-only target matches any returned item in that file. A target with both line bounds additionally requires any inclusive intersection with the returned range. No visible text, signature, outline or full-span coverage is required.
- Each target contributes its **first matching native rank**. Returned results are never deduplicated or rank-compacted. Multiple targets at one rank still yield one binary gain, and IDCG uses the original number of targets, exactly as upstream does.
- Primary and secondary upstream targets have the same binary relevance. The old accepted-OR/grouped coverage matching is not substituted into this metric.

Our input remains **SWE-QA**, not Semble's official dataset. `gold/semble-file-v1.json` freezes 39 file targets for all 20 questions, projected from unique accepted paths in the existing source-reviewed Gold. Bridge targets are excluded. Declaration/body anchors are not silently reinterpreted as relevant spans. Its hash and the original Gold hash bind every experiment. These labels remain partial positives; locating a labeled file does not establish answer correctness or snippet relevance.

The byte-identical upstream Python sources and MIT license are retained in `test/fixtures/semble-upstream/`, with provenance and SHA256. Tests execute the original Python functions as an independent oracle and compare path/span matching, first ranks, duplicate targets and nDCG against the JavaScript implementation. The differential oracle needs Python 3 standard library only.

## Run locally

Use Node.js 24, Python 3, npm, Git, and a platform supported by the packed product. The first run needs network access for repository checkouts, package dependencies, and the local embedding model. Run these commands from the repository root:

```sh
# Contract tests use Node built-ins plus a Python stdlib oracle; no npm install.
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

CI uses Ubuntu 24.04 and Node 24: contract tests plus the Python upstream oracle, one candidate tarball, then 11 repository shards with at most four in parallel. Each shard installs that candidate independently and rebuilds its index. The sole model cache key includes OS, architecture, the model catalog, and package lock; no corpus or index cache is restored.

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

`semble-run.mjs` runs Semble **0.6.0 at commit `0051e000fcaac69a9c5d081ebbc8d4cb8508160b`** against these same 20 questions, 11 source commits and unchanged source Gold. It calls the native stdio MCP `search` tool with the original question, `top_k=10`, `max_snippet_lines=null` (full chunk) and `content=code`, matching SDK defaults and the official quality runner. Native hybrid ranking stays enabled. Each query runs five consecutive times in one session per repository; repetition 5 supplies quality scores. The public interface does not expose separate FTS/vector modes or an auto-update disable switch.

Public JSON results feed two separately named scorers. The new Semble metric reads native file paths/ranges and ranks; the legacy visible-source diagnostics read actual displayed content and outline. Native chunks can start inside a function body and miss a frozen declaration anchor even when a file target matches. A separate **Gold-file-presence diagnostic** remains, but is not substituted for nDCG. zg runs both bounded `short` and complete retrieved-content `full` arms. Each visibility diagnostic scores only the public response; no source text is filled in. Identical rankings should give identical file-target nDCG even when full content reveals additional anchors.

Both tools search code within the frozen repository roots. Semble applies its native supported extensions, ignores, symlink policy and 1 MB file limit. zg's code selection follows the pinned Semble extension set and size cap, while retaining native ignores/extraction. The named potion-code model family is shared, but model formats, preprocessing, chunking, ranking and rendering differ. This is an end-to-end retrieval comparison, not an isolated embedding comparison.

After the 100 MCP calls, a separate Python audit loads each exact persisted Semble index and calls unmodified `index.search()` for the same 20 queries. Every fifth-round MCP path, full range, score, full content and rank must equal the SDK result. SDK calls do not replace MCP observations or enter MCP latency measurements. `sdk-replay.json` and `sdk-parity.json` retain evidence; the offline report independently repeats the comparison and refuses valid headline scores when parity fails.

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

The separate [Semble CI workflow](../../.github/workflows/retrieval-semble.yml) runs on benchmark/shared-protocol changes and manual dispatch. It pins source, model revision and Python dependency constraints, caches only model downloads, and executes 100 MCP calls plus 20 SDK parity queries. Its `retrieval-semble-evidence` artifact contains the reports, requests, raw responses, runtime identity, source audits, SDK parity evidence, inventories, chunks and metadata. Large BM25/vector payloads and external model weights are excluded; their captured file hashes remain in the inventories. Local runs retain the complete persisted index. Evidence is retained for 14 days, including failed runs.

## Interpretation limits

These are public development/regression questions, not an unseen generalization test. Entry hits do not establish answer correctness, exhaustive relevance, or complete multi-file evidence. Output bytes are a response-size diagnostic only. No byte-window hit, first-hit-byte, or agent token-saving metric is used.

Stage evidence covers file inventories, scanner output, persisted chunks, source mappings, and stored vector hashes. Actual embedding inputs and full candidate/fusion histories are currently unobserved; a miss is not proof of an embedding defect. Five repeats support consistency checks, not reliable tail-latency estimates.

Historical E2E answer scores, token use, and tool calls may be joined by task ID for investigation. Fixed original-query results cannot explain an agent's rewritten queries or prove a causal E2E improvement.
