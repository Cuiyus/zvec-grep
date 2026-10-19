Using the supplied source snapshot, explain the following query behavior and support your explanation with file and line citations. You do not need to modify code or run tests. If you perform any validation, distinguish executed results from source-based inference.

A collection contains the three non-null documents below. `body` has a full-text index with the standard tokenizer and lowercase filter. `status` (STRING) and `views` (INT64) have inverted indexes. Query configuration is otherwise at its defaults, and no records have been deleted.

| id | status | views | body |
| --- | --- | --- | --- |
| 1 | draft | 10 | hello one |
| 2 | published | 20 | hello two |
| 3 | published | 30 | hello three |

All queries use `topk=10`. The following behavior is reported; compare ID sets, not result ordering:

- A full-text query for `hello` with either `status = 'archived'` or `views > 1000` returns IDs 1, 2 and 3. A scalar-only query with the same condition returns an empty set.
- A full-text query for `hello` with `status = 'published'` returns IDs 2 and 3.
- A full-text query for `one` with `status = 'published'` returns an empty set.
- In a separate collection with identical data, remove only the inverted indexes on `status` and `views`, retaining the full-text index on `body`. A full-text query for `hello` with either of the zero-match scalar conditions above returns an empty set.

Explain the anomaly and why the three controls behave differently, tracing how the conditions are passed and executed in the relevant source. Propose a minimal repair principle that preserves ordinary queries both with and without scalar conditions, and describe regression checks covering this problem and its boundary cases.
