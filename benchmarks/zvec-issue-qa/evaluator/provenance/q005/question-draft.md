# External identifier migration and mixed insert batches

Use only the supplied immutable zvec source. This is a read-only behavior QA task: do not modify files, execute project code/tests, or consult the web, Git history, issues, prior answers or evaluator files. Support the important conclusions with source paths and line ranges.

A Python client is migrating external identifiers. Analyze these independent probes against the supplied version:

1. At identifier validation, would a collection name `商品 2026/😀` and a scalar field name `标题` both be accepted? All unrelated schema requirements are satisfied. This probe asks about identifier validation, not filesystem-path behavior or the time an error is raised during construction.
2. For a document ID, is `客户 42/😀` permitted? Would `"汉" * 341` and `"汉" * 342` be permitted? Explain the relevant size unit and limit. These are ordinary valid Python strings, not escaped text or surrogate code points.
3. In an otherwise valid, open, writable collection with ASCII field/vector names, call `collection.insert([d1, d2, d3])`. Their IDs, in order, are `客户 42/😀`, `bad\tid`, and `fresh-after`. Here `bad\tid` contains one actual U+0009 TAB between `bad` and `id`. All three IDs are new. Does Python return per-document statuses or raise an exception? Which, if any, of these documents are inserted?
4. In a separate otherwise identical collection, a live document `seed-id` already exists with payload `old`. Insert three valid documents whose IDs are `fresh-before`, `seed-id`, and `fresh-after`; the middle document has a different payload `new`. Determine the Python call's return/exception behavior, the outcomes for the first and last documents, and whether the existing document is overwritten.

For probes 3 and 4, every document has schema-compatible fields/vectors; there are no concurrent operations, read-only/closed states, segment switches, I/O failures or other errors. The only exceptional conditions are the stated identifier or duplicate. Explain why the two batches do or do not have the same failure scope by tracing the Python/native boundary and native write path. Distinguish validation guarantees from assumptions about transaction atomicity.
