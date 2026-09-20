# Retrieval-only: ZG with an optional Semble baseline

The suite uses the **20 unchanged SWE-QA questions and 11 pinned repositories** selected in [Actions run 35206585943](https://github.com/Cuiyus/zvec-grep/actions/runs/35206585943). It calls public MCP search directly; no answering agent, query rewriting, subquery or LLM judge runs during retrieval. See the [test design](../../docs/zg-retrieval-only-sweqa20-design.md) for protocol and scoring details.

## Run CI and read the result

There is one [Retrieval-only workflow](../../.github/workflows/retrieval-only.yml), triggered **only by `workflow_dispatch`**. Push and pull-request events do not run this benchmark. The dispatch actor and the current re-run actor must have the repository **maintain or admin** role. Every job repeats this check, including partial re-runs; GitHub's ordinary write permission alone is insufficient.

This in-workflow check applies to the checked-in workflow. Contributors who can modify the workflow or its local authorization action on another branch can bypass that check; a permission boundary against those contributors requires repository- or organization-level Actions execution policies.

In GitHub Actions, select **Retrieval-only → Run workflow**:

| Input | Default | Effect |
| --- | --- | --- |
| `run_semble` | `false` | ZG always runs; enable this to add Semble in the same run |
| `modes` | `hybrid` | ZG hybrid, or `hybrid,fts,vector`; each mode includes short and full previews |

The **Retrieval results** job publishes the single overview table. Repository shards and the optional Semble job upload evidence without publishing competing summary tables. Each row is a mode/preview arm; optional Semble appears as **Disabled** with dashes when disabled. A missing or invalid report is a failure, never a zero score.

The table contains exactly five quality metrics plus two operational measurements:

| Column | Meaning / aggregation |
| --- | --- |
| File Hit@1 | Fraction of the 20 original questions with a labeled file at native rank 1 |
| File Hit@5 | Fraction with a labeled file within native Top 5 |
| File Hit@10 | Fraction with a labeled file within native Top 10 |
| File MRR@10 | Mean of `1 / first matching native rank`; Top-10 misses are zero, all 20 questions have equal weight |
| nDCG@10 | Pinned upstream binary-gain algorithm; repository macro average across 11 repositories |
| Mean output (KiB) | Mean public MCP text UTF-8 bytes / 1024, using successful fifth calls only; not model tokens |
| Latency P50 (ms) | Median of all successful MCP search calls, including the five repetitions; excludes indexing and SDK parity calls |

Quality values use the fifth call per question. Successful measurement sample counts are stated below the table (normally 20 output samples and 100 latency samples per arm). Product failures remain zero quality observations and fail operational integrity, while their calls are excluded from output/latency measurements. Invalid experiments withhold their aggregate. There is no arbitrary quality-score pass threshold.

**Removed:** strict-anchor Hit/RR/MRR, grouped-anchor nDCG, nDCG@5, and separate file-presence/category score tables. Original frozen Gold annotations remain as provenance; they no longer drive source-anchor scoring.

`retrieval-results` contains the concise `summary.md`, machine-readable `summary.json`, and `comparison.json` when both engines validate. Download `retrieval-zg-report` for ZG's per-question JSON/JSONL, `retrieval-data-<owner>__<repo>` for ZG raw evidence, and optional `retrieval-semble-evidence` for Semble raw evidence and SDK parity. Evidence retention is 14 days. Missing artifacts and failed upstream jobs are explicit in the overview.

GitHub normally registers a manually triggered workflow from the default branch. Before first merge, use the registered workflow's CLI dispatch if GitHub accepts the feature branch; otherwise validate locally and enable the UI entry after merge. Do not temporarily add automatic triggers to test it.

## Fixed protocol and interpretation

- ZG uses its packed product and MCP configuration produced by `zg install --target opencode --yes --mcp-transport stdio`. Hybrid uses original `query`, `limit: 10`, `autoUpdate: false`, and `freshness: eventual`.
- Each repository has a fresh index and one MCP session. Per question, short runs five times, then full runs five times. Hybrid makes 200 calls; all three ZG modes make 600. Each arm still has only 20 quality samples. Full returns all stored retrieved-item content/outline, not the entire source file.
- Both engines index code with the fixed extension/size policy, retaining their native ignores, chunking and ranking. Indexes are not cached. Only model downloads may be cached. Corpus/model/index inventories are checked before and after retrieval.
- Semble is pinned to `0051e000fcaac69a9c5d081ebbc8d4cb8508160b` (0.6.0), with model revision `e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b`. Optional Semble runs 100 native MCP calls using original questions, `top_k=10`, `content=code`, and `max_snippet_lines=null`. Default hybrid and rule-based reranking remain enabled.
- Each fifth-round Semble result must match an independent `index.search()` call on the same persisted index, including rank, path, range, score and full content. These 20 SDK parity calls do not supply quality or latency observations.
- Fixed short-before-full order, different runtimes and CI runners mean observed timings are not a controlled speed comparison. Five repeats do not support tail-latency claims.

Frozen inputs are `data/source.lock.json`, `data/queries.jsonl`, `configs/protocol.json`, and `gold/semble-file-v1.json`. The 39 file targets are unique accepted paths projected from AI-reviewed source annotations. They remain partial positives, not independent human ground truth or complete answer evidence. The [metric review](../../docs/zg-retrieval-metric-review.md) explains why declaration anchors were retired.

## Scoring and report contracts

File Hit/RR/MRR use contract `sweqa-file-hit-rr-v1`. They share exactly the same frozen file targets and upstream normalized-path matching as nDCG@10. Native ranks are preserved; repeated file chunks are never collapsed or renumbered. Source declarations, outlines and preview length cannot alter these quality scores.

`metrics/ndcg.mjs` preserves upstream first-target rank, binary gain and target-count IDCG. The byte-identical Python oracle and MIT attribution remain in `test/fixtures/semble-upstream/`; tests compare the JavaScript implementation against those original functions. The dataset is SWE-QA, not Semble's own benchmark dataset.

ZG reports use schema **3**; Semble reports use schema **2**. New reports contain `file_retrieval`, `semble_official` (nDCG@10 only) and `measurements`. Each quality row preserves five `measurement_observations` to make measurement aggregation inspectable. Old top-level anchor scores and `summary` fields are removed. The same-engine comparator can read historical ZG schemas 1/2 by recomputing supported metrics from public items; old reports without complete measurement evidence show unavailable measurements. Raw response and index audits remain the aggregator's responsibility.

The combined CI `summary.json` uses schema **2** and names the quality metric `ndcg_at_10`. The report label is **nDCG@10** throughout; this naming change does not alter scoring or aggregation.

## CI structure

Maintainer checks → contract tests → packed ZG candidate → 11 ZG repository shards (maximum four concurrent) → complete ZG report. Optional Semble runs alongside ZG after contract tests. The final result job waits for all required jobs and publishes one table even when an authorized upstream execution fails. Each job checks the current actor again, so partial re-runs cannot reuse an earlier actor's successful authorization.

## Code structure

```text
zg-retrieval/
  *.mjs              Stable command-line entrypoints
  core/              Frozen suite, corpus, I/O and shared response scoring
  metrics/           File ranking, nDCG and operational measurements
  engines/
    zg/              ZG runner, parser, index snapshots and raw-evidence report
    semble/          Baseline runner, parser, preparation and evidence audits
  reports/           Report validation, comparisons, rendering and CI overview
  configs/           Frozen protocols and dependency constraints
  data/              Frozen questions and repository/source lock
  gold/              Frozen relevance labels and annotation provenance
  test/              Contract tests and upstream scoring oracle
```

The command-line entrypoints keep existing CI and replay commands stable. Each engine owns its public response parser, runner and raw-evidence checks. `core/response.mjs` applies the shared failure and scoring rules to normalized public results; metric modules do not depend on either engine. The report layer validates frozen identities and recomputes scores and measurements before rendering or comparing reports. Validation is explicit, not implemented by comparing a report with itself.

To add an engine, implement its public-response parser and evidence capture/audit, then supply its normalized observations to the existing metric functions. Add its report contract to `reports/validation.mjs` and its CI integration separately. Keep engine-specific request and index checks with that engine; do not duplicate the relevance formulas or introduce dependencies between engine implementations.

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

## Offline replay and comparisons

Download all `retrieval-data-*` artifacts into separate artifact-named subdirectories under one directory, then recompute from captured requests/responses and inventories:

```sh
node benchmarks/zg-retrieval/report.mjs /absolute/path/to/downloaded-shards
node benchmarks/zg-retrieval/compare.mjs baseline/report.json candidate/report.json NEW_OUTPUT_DIR
```

Generate the exact CI overview from validated reports (the optional Semble path need not exist when disabled):

```sh
RETRIEVAL_SEMBLE_REQUESTED=false node benchmarks/zg-retrieval/ci-report.mjs \
  --zg /absolute/path/to/zg/report.json \
  --semble /absolute/path/to/semble/report.json \
  --output /absolute/path/to/overview
```

Set `RETRIEVAL_SEMBLE_REQUESTED=true` to require and compare Semble, and `RETRIEVAL_MODES=hybrid,fts,vector` for a three-mode ZG run. A requested missing engine fails the overview. Offline recomputation is not a new retrieval run.

## Prepare the optional Semble baseline locally

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
  benchmarks/zg-retrieval/engines/semble/prepare.py model \
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

## Limits

These public development questions support regression diagnosis, not broad generalization claims. A file hit does not establish that the returned snippet answers the question. Reports do not claim complete candidate/fusion histories or embedding inputs; a miss cannot by itself identify the responsible stage. Historical answer scores and agent token use are separate evaluations.
