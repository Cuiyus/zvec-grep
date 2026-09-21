# ZG Retrieval-only benchmark

Measure **zg-hybrid, zg-fts and zg-vector** on the same 20 unchanged SWE-QA questions and 11 pinned repositories from [Actions run 35206585943](https://github.com/Cuiyus/zvec-grep/actions/runs/35206585943). Each mode calls the Rust public MCP search endpoint directly with its default response presentation. No answering agent, query rewriting, subquery generation or LLM judge participates. See the [test design](../../docs/zg-retrieval-only-sweqa20-design.md) for protocol and scoring details.

## Run CI and read the result

The [Retrieval-only workflow](../../.github/workflows/retrieval-only.yml) runs **only through `workflow_dispatch`**. Select **Retrieval-only → Run workflow**. Use GitHub's workflow-ref selector to choose the benchmark harness (`main` for the latest merged harness), then set `candidate_ref` to the branch, tag or commit containing the `rust/` workspace to compile. It defaults to `main`. Every run includes all three modes; push and pull-request events do not trigger this benchmark.

The workflow freezes the selected harness ref to a full commit, checks out the candidate separately, resolves it to another full commit SHA, and builds `candidate/rust/`. The packed native package cache is keyed by operating system, architecture and that exact candidate commit. A hit skips Rust compilation and packaging. On a miss, a second Cargo cache can reuse registry data and `rust/target` objects before `npm run pack:local` creates the candidate tarball. Node.js version is not part of candidate selection, package-cache identity or report identity.

The original dispatch actor and current re-run actor must have the repository **maintain or admin** role. Every job checks both, including partial re-runs. This in-workflow check applies to the checked-in workflow. Contributors who can modify the workflow or its local authorization action on another branch can bypass that check; a permission boundary against those contributors requires repository- or organization-level Actions execution policies.

The **Retrieval results** job publishes one overview table with exactly three rows:

| Arm | Public MCP search input | Presentation |
| --- | --- | --- |
| zg-hybrid | `query: originalQuestion` | Rust MCP default |
| zg-fts | `fts: [originalQuestion]` | Rust MCP default |
| zg-vector | `vector: [originalQuestion]` | Rust MCP default |

Every request also uses `limit: 10`, `autoUpdate: false`, `freshness: eventual` and `preferSymbol: false`. FTS and vector requests omit `query` and the other route. The benchmark does not send a `preview` field because the Rust MCP schema does not expose it. The product retains its native chunking, aggregation, ranking and bounded response presentation.

| Column | Meaning / aggregation |
| --- | --- |
| File Hit@1 | Fraction of the 20 original questions with a labeled file at native rank 1 |
| File Hit@5 | Fraction with a labeled file within native Top 5 |
| File Hit@10 | Fraction with a labeled file within native Top 10 |
| File MRR@10 | Mean of `1 / first matching native rank`; Top-10 misses are zero, all 20 questions have equal weight |
| nDCG@10 | Binary discounted gain with target-count ideal gain; repository macro average across 11 repositories |
| Mean output (KiB) | Mean public MCP text UTF-8 bytes / 1024, using successful fifth calls only; not model tokens |
| Latency P50 (ms) | Median of all successful MCP search calls, including five repetitions; excludes indexing |

Each repository uses one fresh index and one MCP session. Modes run in the fixed order **hybrid → fts → vector**; each original question is called five consecutive times within each mode. Only the fifth result supplies quality and output-size observations. A complete run contains **300 calls and 60 quality observations**, with **20 questions, 20 output samples and 100 latency samples per mode** when all calls succeed. Repetitions are not independent questions.

Missing or invalid evidence withholds aggregate results and fails CI. Product failures retain zero quality observations and fail operational integrity; failed calls are excluded from output and latency measurements. Quality scores have no arbitrary pass threshold. Fixed mode order and shared runtime/model caches mean latency is an observation under this protocol, not a controlled comparison of cold-start or mode execution speed.

## Dataset and scoring

Frozen inputs are `data/source.lock.json`, `data/queries.jsonl`, `configs/protocol.json`, and `gold/files-v1.json`. The questions and 39 relevant-file targets are unchanged. Targets are unique accepted paths projected from AI-reviewed source annotations; bridge-only paths do not earn credit. They are partial positives, not independent human ground truth or complete answer evidence. The [metric rationale](../../docs/zg-retrieval-metric-review.md) explains how to interpret the scores and their limits.

All five quality metrics use the same file labels and normalized-path matching. Native ranks are preserved: repeated file chunks consume positions and are never collapsed or renumbered. Source declarations, outlines and response length cannot alter these quality scores. File Hit/RR/MRR use contract `sweqa-file-hit-rr-v1`.

`metrics/ndcg.mjs` uses first-target rank, binary gain and target-count IDCG. It is checked against a byte-identical pinned Python scoring reference, with its MIT attribution preserved in [`test/fixtures/ndcg-reference/`](test/fixtures/ndcg-reference/). The reference is used only by unit tests. The benchmark runs only ZG.

Questions, labels and reports remain outside indexed source checkouts. Indexing applies the frozen code-extension and size policy while retaining ZG's native ignore rules. Repository indexes are never cached; model downloads and compiled candidate packages may be cached. Corpus and model inventories plus the Rust CLI's public aggregate index status are checked before and after retrieval. Model identity includes artifact contents but excludes only the runtime-generated `.zvec-grep-artifacts-<24hex>.complete` cache marker, whose machine-specific timestamps do not identify model weights.

## Reports and artifacts

- `retrieval-results`: the single `summary.md` and machine-readable `summary.json`.
- `retrieval-zg-report`: `report.json`, `report.md` and per-call `scores.jsonl`.
- `retrieval-data-<owner>__<repo>`: raw public requests/responses, installation evidence, corpus/model inventories and public index status for each repository.

Evidence retention is 14 days. Only the final results job publishes the main CI table; shards upload evidence. Missing artifacts and failed upstream jobs remain explicit in the overview.

ZG reports use **schema 6**, with `preview: "mcp-default"`, three `modes`, and one quality row per question/mode. The overview uses **schema 4** and fixed `zg-hybrid`, `zg-fts`, `zg-vector` rows. It records both the frozen harness commit and selected candidate ref/commit. Quality rows retain five `measurement_observations` so validators can recompute measurements. The `file_retrieval` field holds Hit/MRR, `ndcg` holds nDCG and its target evidence, and `measurements` holds output size, latency and sample counts.

The protocol ID is `sweqa20-zg-rust-three-modes-mcp-default-v6`. The comparator accepts schema 6 reports with matching protocol and frozen inputs, Rust MCP default presentation and all three modes. Reports from other protocol versions require their matching scorer checkout. Replaying saved evidence is not a new retrieval run.

## Code structure

```text
zg-retrieval/
  *.mjs              Stable command-line entrypoints
  core/              Frozen suite, corpus, I/O and shared response scoring
  metrics/           File ranking, nDCG and operational measurements
  engines/zg/        Runner, public response parser, snapshots and evidence audit
  reports/           Validation, same-protocol comparisons and CI rendering
  configs/           Frozen protocol
  data/              Frozen questions and repository/source lock
  gold/              Frozen relevance labels and annotation provenance
  test/              Contract tests and pinned scoring reference
```

The runner captures public evidence; the aggregator reparses and audits it; metric functions score normalized results; report validators recompute quality and measurements before comparison or CI rendering. Metrics do not depend on engine implementations. Response presentation behavior remains covered by the product's MCP contract tests, rather than duplicate retrieval benchmark arms.

## Run locally

Use Node.js, Python 3, npm, Git, Rust and a platform supported by the packed product. Python is used only for the stdlib scoring oracle. The first retrieval run needs network access for repository checkouts, Rust dependencies and the embedding model. Run from the repository root:

```sh
node --test benchmarks/zg-retrieval/test/*.test.mjs
retrieval_work="$(mktemp -d)"
mkdir -p "$retrieval_work/package"
(cd rust && npm ci && npm run pack:local)
cp rust/dist/npm/*.tgz "$retrieval_work/package/"

node benchmarks/zg-retrieval/run.mjs \
  --package "$retrieval_work/package" \
  --output "$retrieval_work/results" \
  --corpus "$retrieval_work/corpus" \
  --model-cache "$retrieval_work/model-cache" \
  --candidate-commit "$(git rev-parse HEAD)"

node benchmarks/zg-retrieval/report.mjs "$retrieval_work/results"
```

`--package` accepts a tarball or a directory containing exactly one `.tgz`. The runner installs it in an isolated consumer. Use a new `--output` directory and fresh corpus checkout: output is never overwritten and an existing index is rejected. An external model-download cache may be reused.

Add `--repository reflex-dev/reflex` or `--tasks reflex:6` for an explicitly labeled subset smoke run; all three modes still execute. Standalone `report.mjs` requires all 20 questions, and CI rejects subset reports. Modes and MCP presentation are fixed by the protocol, without selection flags.

## Offline replay and comparisons

Download all `retrieval-data-*` artifacts into separate artifact-named subdirectories beneath one directory, then recompute:

```sh
node benchmarks/zg-retrieval/report.mjs /absolute/path/to/downloaded-shards
node benchmarks/zg-retrieval/compare.mjs baseline/report.json candidate/report.json NEW_OUTPUT_DIR
node benchmarks/zg-retrieval/ci-report.mjs \
  --zg /absolute/path/to/report.json \
  --output /absolute/path/to/overview
```

A partial or incompatible experiment cannot produce a valid overview. Candidate source selection happens through `candidate_ref`; the harness comes from the workflow ref selected for that manual run. Do not add automatic triggers to test it.

## Limits

These public development questions support regression diagnosis, not broad generalization claims. A file hit does not establish that its returned snippet answers the question. Reports do not claim complete candidate/fusion histories or embedding inputs; a miss alone cannot identify the responsible stage. Answer correctness and agent token use require separate evaluations.
