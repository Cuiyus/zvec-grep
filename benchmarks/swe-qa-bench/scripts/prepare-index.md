# Index preparation before QA evaluation

```sh
node /opt/qa/prepare-index.mjs \
  --root /app \
  --package-dir /opt/qa/node_modules/@zvec/zvec-grep \
  --embedding-model local/potion-code-16m-v2 \
  --model-cache-dir /models \
  --log /logs/index-build.json
```

This setup command uses published zg 0.2.2 and calls
`createZvecGrep({root, embedding, modelCacheDir})`, then awaits exactly one normal
`index({root, onProgress})`. It supplies no questions, gold, target paths, chunking
overrides or custom filters. Standard production index defaults apply.

The source can be mounted read-only. An initially empty `.zvec-grep` directory
must be mounted writable beneath it, with writable index-lock storage. Model
weights and the preparation report live outside source. A missing index is
created in this phase; existing-index requirements do not apply to preparation.
The normal production API controls compatibility if a nonempty index is supplied.

Completion requires `info({includeStatus:true})` to report a populated, complete,
fresh index with the requested embedding, and an unchanged Git-tracked source
digest before/after indexing and service cleanup. An existing report is never
overwritten. Errors produce a nonzero exit status and, once preparation has
started, a diagnostic report.

The external JSON report includes the build result, full info/status, package and
script identities, source digests, `build_duration_ms`, total `duration_ms`,
progress counters and errors. Stderr carries progress transitions and at most one
ordinary progress update per second, so model-download chunks cannot flood CI.
These preparation costs are separate from the subsequent QA/retrieval metrics.

The helper imports version/source validation from `readonly-search.mjs`; copy
both scripts into the runtime image. Query runs should subsequently use the
query-only bridge and its frozen snapshot protocol.

```sh
node --test scripts/prepare-index.test.mjs
```

The implementation was also exercised against the actual zg 0.2.2 package and
real `local/potion-code-16m-v2` on a one-file repository: the source directory was
mode `0555`, its tracked file `0444`, and only the index child directory remained
writable. Preparation completed with `fresh:true`, `source_unchanged:true`, one
indexed file/entity, and zero added/modified/deleted/pending/failed status counts.
No LLM was used. This fixture validates setup behavior, not QA quality or speed.
