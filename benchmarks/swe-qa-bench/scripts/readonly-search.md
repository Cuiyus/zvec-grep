# Query-only benchmark bridge

`readonly-search.mjs` requires an installed **`@zvec/zvec-grep@0.2.2`** and an
already-existing, complete, compatible index. It never calls an indexing API,
starts a daemon, or accepts reference answers/evidence labels. The outer runner
must enforce a read-only corpus and keep logs, configuration, snapshots and gold
outside the Agent-readable source tree.

```sh
node readonly-search.mjs preflight \
  --root /app --package-dir /opt/qa/node_modules/@zvec/zvec-grep \
  --embedding-model local/potion-code-16m-v2 --model-cache-dir /models \
  --snapshot /logs/snapshot.json --log /logs/preflight.jsonl --working-copy

node readonly-search.mjs retrieve \
  --root /app --package-dir /opt/qa/node_modules/@zvec/zvec-grep \
  --embedding-model local/potion-code-16m-v2 --model-cache-dir /models \
  --snapshot /run/qa/snapshot.json --log /logs/events.jsonl --working-copy \
  --query 'How are pending requests cancelled?' --mode hybrid --repetitions 5
```

- `preflight` creates a new snapshot using exclusive creation; it never replaces
  an existing snapshot. `verify` checks a previously frozen snapshot.
- `serve` exposes only `zvec_grep_search` over stdio. It reuses the published
  product's agent tool schema, description, instructions and compact text
  renderer. Its backend calls public `context({autoUpdate:false, trace:true})`.
  The production input schema still contains `autoUpdate`; this bridge always
  overrides it to `false`. This is a benchmark integration, not the native zg
  daemon.
- `retrieve` supports `hybrid`, `fts` and `vector`. Pure FTS/vector requests use
  only the corresponding route, without an accidental additional hybrid query.
  Five repetitions is the default. Each CLI invocation is one process, holding
  one service instance; each public `context()` call opens/closes a read index.
  The first embedding query can include process/model startup. No LLM rewrites
  queries, and no debug target is injected into candidates.
- `duration_ms` covers the bridge's manifest check, public query and rendering;
  `context_duration_ms` covers only the awaited public `context()` call. Full
  preflight/final integrity audits and JSONL append time are outside these two
  measurements. These are not model input-token measurements.

## Integrity policies

The default requires unchanged source, manifest, complete native document state,
vectors **and physical storage bytes**. Source identity covers all Git-tracked
paths, independently of zg's indexing filters. Index freshness uses production
`info({includeStatus:true})`; incomplete/stale indexes fail without repair.

Published zg 0.2.2 with native zvec 0.7.1 was observed to change `.proxima` storage
bytes on read-only opens. In the synthetic integration fixture, modified bytes
included wall-clock timestamps and a checksum. A physically non-writable file
also caused `ZVecOpen({readOnly:true})` to fail. These observations must not be
generalized into a claim that all possible physical changes are harmless.

`--working-copy` therefore supports an explicit alternative protocol:

1. The outer runner preserves the original seed bytes and gives each trial a
   fresh, writable working copy of that existing index. No index is rebuilt.
2. The bridge still opens collections in read-only API mode and disables all
   automatic document updates.
3. Before and after use, native `iterDocsSync({includeVector:true})` enumerates
   **every raw document/fragment, every scalar field, and every vector** in both
   collections. Every embedding must have the expected dimension and finite
   values. Canonical hashes include all of these data, with a separate vector
   hash. Source, manifest, package/configuration and logical data changes fail.
4. Physical changes are reported explicitly with paths, before/after SHA256 and
   sizes. The integrity result is `semantic_unchanged`, never byte immutability.
   This check does not prove that native ANN graph bytes remained unchanged.

The same policy and model-cache path must be supplied to all commands using a
snapshot. The native read locks under `.zvec-grep/locks` need writable scratch
storage; this is separate from source-file modification.

## Trace and verification

Each `event: "search"` includes `origin` (`agent-mcp` or `retrieval-only`),
sequence/repetition, normalized request, actual rendered `text`, text bytes/hash,
raw result with diagnostics/trace, timing and errors. Raw item content is an
offline diagnostic; only `text` was actually returned by the MCP tool. Snapshot,
start/end and integrity events identify the index, source, package code and
dependency versions. The bridge does not assign relevance or answer correctness.

```sh
node --test scripts/readonly-search.test.mjs
ZG_READONLY_PACKAGE_DIR=/path/to/installed/@zvec/zvec-grep \
  node --test scripts/readonly-search.integration.test.mjs
```

The integration tests exercise the actual 0.2.2 MCP registration and five FTS
queries over a synthetic native index, using a deterministic fixture embedding.
They verify complete document/vector stability and identical visible results;
they are not QA or Agent/Model benchmark scores.
