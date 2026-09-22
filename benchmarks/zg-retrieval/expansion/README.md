# Retrieval-only pilot suites

These pilots reuse the Rust public MCP search route and the existing five
file-level metrics. They run independently of the frozen SWE-QA20 suite.

- **BEIR / SciFact test:** ten original test claims sampled at equal positions
  in the sorted test-query IDs. The complete 5,183-document SciFact corpus is
  indexed, one unchanged title/text document per Markdown file. The official
  test qrels determine relevant document IDs. The original archive is pinned
  by SHA-256. CI downloads its corpus and queries from a pinned Hugging Face
  mirror, checks the full corpus content digest against the original archive,
  and verifies the selected queries and the checked-in original test qrels.
  This mirror is used because the original download host is not reachable from
  GitHub runners.
- **Quarry / quic-go preimage:** the first original query from each of the first
  ten distinct quic-go tasks in the pinned release, using each task's exact
  preimage revision. The full repository checkout is indexed separately for
  each revision. The original `positive_units` remain in the lock; file metrics
  project their unique paths. This is a file-level pilot, not Quarry's official
  function-level recall score.
- **DuRetrieval / Chinese web search dev:** ten original Chinese queries sampled
  at equal positions in the sorted dev-query IDs. All 100,001 passages in the
  pinned [C-MTEB DuRetrieval](https://huggingface.co/datasets/C-MTEB/DuRetrieval)
  corpus subset are indexed, one unchanged passage per Markdown file. The
  published [dev qrels](https://huggingface.co/datasets/C-MTEB/DuRetrieval-qrels)
  determine relevant passage IDs. Source revisions and Parquet SHA-256 hashes
  are frozen in `data/duretrieval10.json`; preparation verifies the original
  queries, qrels and target IDs. This is a ten-query pilot on the C-MTEB corpus
  subset, not a score on DuReader's original full 8.09-million-passage corpus.

Each suite uses its own specified embedding model. The MCP requests use the
same fixed mode order (hybrid, fts, vector), `limit: 10`, no agent, and five
calls per query; the fifth result supplies quality and output size. Results
include File Hit@1/5/10, File MRR@10, binary file nDCG@10, mean public output
and median call latency. Native result ranks are retained and repeat chunks
consume ranks. The ten-query sample is exploratory; do not compare absolute
scores across suites as if they shared a corpus or relevance definition.
All four suites use the same public response parser. It tolerates one empty
trailing source line immediately after a result's public range, matching the
Rust MCP presentation of files ending in a newline. Nonempty or further
out-of-range source lines are still rejected.

After one shared Rust package build, SWE-QA20, BEIR, DuRetrieval and Quarry run
in four parallel suite jobs. BEIR and DuRetrieval use
`local/potion-multilingual-128m`; the two code suites use
`local/potion-code-16m-v2`. Each job publishes its own aggregate metrics and
per-question results, including failed questions. The final `Retrieval results`
job publishes one page with all four suite sections, even if a suite fails.

DuRetrieval's Parquet reader is pinned in `requirements-duretrieval.txt` and
installed only in its CI job. A local run needs that dependency before invoking
`expansion/run.mjs --suite duretrieval`. The downloaded Parquet files and
materialized passage files are run-local inputs; only their locked hashes and
the ten original query/qrel records are checked in.
