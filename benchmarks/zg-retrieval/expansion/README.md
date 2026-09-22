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

Each suite uses its own specified embedding model. The MCP requests use the
same fixed mode order (hybrid, fts, vector), `limit: 10`, no agent, and five
calls per query; the fifth result supplies quality and output size. Results
include File Hit@1/5/10, File MRR@10, binary file nDCG@10, mean public output
and median call latency. Native result ranks are retained and repeat chunks
consume ranks. The ten-query sample is exploratory; do not compare absolute
scores across suites as if they shared a corpus or relevance definition.
The pilot response parser tolerates one empty trailing source line immediately
after a Markdown result's public range; the frozen SWE-QA20 parser remains
strict, and nonempty out-of-range source is still rejected.

The CI jobs publish their own GitHub job summaries and artifacts. The final
`Retrieval results` job publishes one page with all three suite sections.
