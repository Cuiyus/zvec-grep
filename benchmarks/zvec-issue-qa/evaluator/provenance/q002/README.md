# q002 normalization provenance

This is a pending-freeze package for the independently reviewed issue #583 candidate. The English instruction and normalized rubric preserve the reviewed observations, causal scope, five 2-point criteria and success gate. It does not activate a benchmark task.

- `task-entry.json`: complete proposed manifest task entry; not an expansion manifest.
- `normalization.json`: translation/schema mapping, preserved-file hashes, source inventory algorithm and limitations.
- `research/`: unchanged original draft, independent review, primary provenance, source inventory and post-fix corroborating files. The full source checkout is not duplicated.
- `fix-only-evidence.json`: post-fix corroboration, excluded from pre-fix evidence spans and solver requirements.
- `SHA256SUMS`: hashes for this provenance package plus the normalized instruction and rubric, relative to this directory.

No native reproduction or solver/CI execution was performed. Static evidence and known-marker checks do not establish training-contamination isolation or zg effectiveness. Keep all evaluator/provenance content outside any future solver source corpus.
