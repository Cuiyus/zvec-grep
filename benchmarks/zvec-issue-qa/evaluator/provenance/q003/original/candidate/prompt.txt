A small collection is configured for non-quantized FP32 HNSW/COSINE vectors and scalar labels. Each of 28 inserted IDs has a different unit basis vector in 32 dimensions, saved outside the collection. Consider this reported failure pattern in the supplied checkout:

1. Insert IDs doc-0000 through doc-0027, in that order, into a new collection; do not call explicit index creation or optimize non-empty content beforehand.
2. Delete doc-0000, doc-0001 and doc-0002, flush, and call optimize(). The collection is small enough to remain within one segment-size limit.
3. The live document count and a scalar-only enumeration agree on 25 documents, but searches using saved reference vectors can return the wrong live IDs or omit live tail documents. Fetching vector payloads may also fail even though scalar data remains. Reopening read-only does not remove the search discrepancy.

Two controls matter: optimizing the empty collection before the first insertion does not prevent the issue, whereas optimizing the initial non-empty data before deleting those IDs avoids it in this sequence. There is no crash or concurrent writer.

Explain the source-level cause from deletion through optimize() and subsequent reads. Why can the scalar/count checks agree while vector results are wrong? Explain the two controls and distinguish the maintenance path used when the deleted-row fraction is much larger. State the mapping invariant a fix must preserve, and describe a regression check that would detect wrong ID/vector associations rather than merely checking counts. Cite relevant source paths and line numbers, and keep conclusions limited to the paths established by this checkout. Do not edit or execute the repository.
