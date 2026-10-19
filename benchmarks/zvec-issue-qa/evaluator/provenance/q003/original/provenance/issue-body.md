## Summary

After `Collection.optimize()`, the **last K live documents are missing from the vector
index**, where **K is the number of superseded rows** in the segment — rows left behind by
an `upsert()` on an existing id, or by `delete()`.

The loss is silent. `stats.doc_count` still counts those documents and a scalar scan still
lists them, so the collection looks complete; they simply never come back from a vector
search, and `fetch()` returns nothing for them when vectors are included.

The engine logs the underlying failure itself:

```
[ERROR] hnsw_streamer_entity.cc:234] Read vector failed, offset=13440, read size=68, ret=0
[ERROR] index.cc:633] Failed to fetch vector, doc_id: 28
[ERROR] segment.cc:1290] vector indexer fetch failed, block_doc_id: 28, block_idx: 0, segment_doc_id: 28
```

In that run the segment held 31 physical rows (28 inserts + 3 overwrites) but only 28 live
documents. The vector storage rebuilt by `optimize()` is sized for the 28 live documents,
while lookups still address vectors by the raw segment row id, so row ids 28, 29, 30 read
past the end of the file. The K highest row ids — i.e. the K most recently written
documents — are lost.

## Environment

| | |
|---|---|
| zvec | 0.7.0 (also reproduced on 0.6.0, identical result) |
| Python | 3.12.13, CPython, MSC v.1944 64 bit (AMD64) |
| numpy | 2.5.2 |
| OS | Windows 11 Pro, build 26200 (`Windows-11-10.0.26200-SP0`) |
| CPU | 11th Gen Intel Core i9-11900KF |
| Install | `pip install zvec==0.7.0 numpy` into a clean venv |

## Steps to reproduce

Save as `repro.py` and run. It needs only `zvec` and `numpy`, uses no external data, and
uses a fixed random seed. It exits with code 1 when the defect reproduces.

```python
"""Minimal reproduction for zvec: Collection.optimize() silently drops the last K
live documents from the vector index, where K is the number of superseded rows
(documents that were overwritten by a second upsert, or deleted) in the segment.

The documents are still counted by stats.doc_count and still returned by a scalar
scan, but they are absent from every vector search result, and fetch() with
vectors returns nothing for them.

Only zvec and numpy are needed; vectors are deterministic (fixed seed).
Exit code 1 means the defect reproduced, 0 means it did not.

    pip install zvec==0.7.0 numpy
    python repro.py
"""

import gc
import platform
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import zvec

SEED = 20260904
DIM = 16
N = 28  # documents inserted
K = 3  # documents later overwritten / deleted


def build_schema() -> zvec.CollectionSchema:
    return zvec.CollectionSchema(
        name="repro",
        fields=[zvec.FieldSchema("doc_id", zvec.DataType.STRING, nullable=False)],
        vectors=[
            zvec.VectorSchema(
                "dense",
                zvec.DataType.VECTOR_FP32,
                dimension=DIM,
                index_param=zvec.HnswIndexParam(metric_type=zvec.MetricType.COSINE),
            )
        ],
    )


def close(coll):
    # Collection.close() only exists from 0.7.0. On 0.6.0 the lock is released
    # when the object is collected, so the caller must drop its reference too:
    #     coll = close(coll)
    if hasattr(coll, "close"):
        coll.close()
    del coll
    gc.collect()
    return None


def doc_id(i: int) -> str:
    return f"doc-{i:04d}"


def make_doc(i: int) -> zvec.Doc:
    rng = np.random.default_rng(SEED + i)
    vector = [float(x) for x in rng.random(DIM, dtype=np.float32)]
    return zvec.Doc(id=doc_id(i), vectors={"dense": vector}, fields={"doc_id": doc_id(i)})


def upsert(coll, indices) -> None:
    for start in range(0, len(indices), 4):
        statuses = coll.upsert([make_doc(i) for i in indices[start : start + 4]])
        statuses = statuses if isinstance(statuses, list) else [statuses]
        assert all(s.ok() for s in statuses), statuses
        coll.flush()


def inspect(coll, live: list) -> dict:
    """Compare a scalar scan against point lookup and vector search."""
    listed = [str(d.id) for d in coll.query(topk=100_000, filter="doc_id != ''", output_fields=[])]
    fetched = coll.fetch(live)
    unfetchable = [i for i in live if fetched.get(i) is None]
    hits = coll.query(
        zvec.Query(
            field_name="dense",
            vector=make_doc(0).vectors["dense"],
            param=zvec.HnswQueryParam(ef=500),
        ),
        topk=100_000,
        output_fields=[],
    )
    found = {str(h.id) for h in hits}
    unsearchable = [i for i in live if i not in found]
    return {
        "doc_count": int(coll.stats.doc_count),
        "listed": len(listed),
        "unfetchable": unfetchable,
        "unsearchable": unsearchable,
    }


def report(label: str, live: list, r: dict) -> None:
    print(
        f"  {label:<22} doc_count={r['doc_count']:<4} scan lists={r['listed']:<4} "
        f"fetch() misses={len(r['unfetchable']):<3} vector search misses={len(r['unsearchable'])}"
    )
    if r["unsearchable"]:
        print(f"  {'':<22} missing ids: {r['unsearchable']}")
        print(f"  {'':<22} (these are the {len(r['unsearchable'])} rows written last)")


def scenario(root: Path, name: str, description: str, mutate) -> int:
    path = root / name
    if path.exists():
        shutil.rmtree(path)

    coll = zvec.create_and_open(str(path), build_schema())
    upsert(coll, list(range(N)))
    live = mutate(coll)
    coll.flush()
    coll.optimize()

    print(f"\n{name}: {description}")
    same = inspect(coll, live)
    report("same handle:", live, same)
    coll = close(coll)

    coll = zvec.open(str(path), zvec.CollectionOption(read_only=True))
    try:
        reopened = inspect(coll, live)
        report("reopened read-only:", live, reopened)
    finally:
        coll = close(coll)
    return len(reopened["unsearchable"])


def no_change(coll) -> list:
    return [doc_id(i) for i in range(N)]


def overwrite(coll) -> list:
    upsert(coll, list(range(K)))  # re-upsert K existing ids -> K superseded rows
    return [doc_id(i) for i in range(N)]


def remove(coll) -> list:
    coll.delete([doc_id(i) for i in range(K)])
    coll.flush()
    return [doc_id(i) for i in range(K, N)]


def main() -> int:
    print(f"zvec {zvec.__version__} | numpy {np.__version__}")
    print(f"Python {sys.version.split()[0]} | {platform.platform()}")
    print(f"CPU: {platform.processor()}")

    root = Path(tempfile.mkdtemp(prefix="zvec-repro-"))
    try:
        a = scenario(root, "A-control", f"{N} inserts, nothing superseded, optimize()", no_change)
        b = scenario(root, "B-overwrite", f"{N} inserts, {K} re-upserted, optimize()", overwrite)
        c = scenario(root, "C-delete", f"{N} inserts, {K} deleted, optimize()", remove)

        print("\n" + "=" * 78)
        ok = a == 0 and b == K and c == K
        print(
            f"REPRODUCED: control loses {a}, overwrite loses {b}, delete loses {c} "
            f"(expected 0 / {K} / {K})."
            if ok
            else f"NOT reproduced as expected: control={a} overwrite={b} delete={c}"
        )
        if ok:
            print("optimize() drops the LAST K live documents from the vector index,")
            print("where K = number of superseded rows. They remain in doc_count and in")
            print("scalar scans, so the collection looks complete until a lookup is tried.")
        return 1 if ok else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
```

## Expected behavior

After `optimize()` every live document is in the vector index: `fetch()` returns it with
its vector, and a vector search over the whole collection can return it. Scenarios A, B
and C should all report `vector search misses=0`.

## Actual behavior

Output (engine `[ERROR]` lines removed for readability; 10 consecutive runs produced
byte-identical output, and the same output on 0.6.0):

```
zvec 0.7.0 | numpy 2.5.2
Python 3.12.13 | Windows-11-10.0.26200-SP0
CPU: Intel64 Family 6 Model 167 Stepping 1, GenuineIntel

A-control: 28 inserts, nothing superseded, optimize()
  same handle:           doc_count=28   scan lists=28   fetch() misses=0   vector search misses=0
  reopened read-only:    doc_count=28   scan lists=28   fetch() misses=0   vector search misses=0

B-overwrite: 28 inserts, 3 re-upserted, optimize()
  same handle:           doc_count=28   scan lists=28   fetch() misses=3   vector search misses=3
                         missing ids: ['doc-0000', 'doc-0001', 'doc-0002']
                         (these are the 3 rows written last)
  reopened read-only:    doc_count=28   scan lists=28   fetch() misses=0   vector search misses=3
                         missing ids: ['doc-0000', 'doc-0001', 'doc-0002']
                         (these are the 3 rows written last)

C-delete: 28 inserts, 3 deleted, optimize()
  same handle:           doc_count=25   scan lists=25   fetch() misses=3   vector search misses=3
                         missing ids: ['doc-0025', 'doc-0026', 'doc-0027']
                         (these are the 3 rows written last)
  reopened read-only:    doc_count=25   scan lists=25   fetch() misses=0   vector search misses=3
                         missing ids: ['doc-0025', 'doc-0026', 'doc-0027']
                         (these are the 3 rows written last)

==============================================================================
REPRODUCED: control loses 0, overwrite loses 3, delete loses 3 (expected 0 / 3 / 3).
```

Scenario C is the sharpest form: three documents are deleted, and three *different*,
never-touched documents — the three written last — stop being searchable.

### Additional observations

These come from a parameter sweep around the same script.

- **The loss size equals the number of superseded rows exactly.** With 28 documents and
  D of them re-upserted, the number of lost documents is D for D = 1…12.
- **It disappears above a garbage ratio of 30%.** With 28 documents, D = 13 and above lose
  nothing. With 100 documents, D = 42 loses 42 and D = 43 loses 0 — the boundary sits at
  `superseded / total_rows > 0.30`, which suggests `optimize()` has a full-rebuild path
  above that ratio and a faulty incremental path below it.
- **Independent of everything else we varied**: collection size (3 to 5000 documents),
  vector dimension (16 and 1024), upsert batch size (1, 4, 28), whether `flush()` is called
  between batches, and whether the schema has a second (sparse) vector.
- **`optimize()` is required.** Without it, every document is fetchable and searchable.
- **The damage is persisted, not a cache artifact.** It is still there after closing the
  handle and reopening the directory read-only in a fresh process.
- **`fetch()` masks it in some configurations.** On a reopened read-only handle with a
  dense-only schema, `fetch()` returns the document (apparently falling back to the raw
  column) while vector search still cannot find it. Vector search is the reliable symptom;
  `fetch()` alone is not a sufficient health check.
- Not observed for collections with only 2 documents, which is consistent with the 30%
  ratio rule (1 superseded row out of 3 is 33%).

## Impact

An indexing pipeline that re-processes changed records — i.e. any incremental update —
produces superseded rows as a matter of course, so this is hit on ordinary operation
rather than in a corner case. The result is a collection that reports the correct document
count and enumerates correctly, but silently omits its most recently written records from
every semantic search. Because the counts agree, the shortfall is invisible to the obvious
integrity checks and only surfaces later as unexplained gaps in search results.

## Current workaround

After `optimize()`, re-check the documents written last (a sliding window of the most
recent writes), re-`upsert()` the ones that no longer come back, and `flush()` again
without calling `optimize()` a second time. This repairs the collection in place, but it
requires keeping the recently written vectors in memory, and it only covers what fits in
that window.

Two smaller notes for anyone reproducing this:

- **A second `optimize()` must not be used as the repair.** Measured on the scenario B
  collection: 3 documents unsearchable after the first `optimize()`; 0 after re-`upsert()`
  plus `flush()`; 3 again after calling `optimize()` a second time — the repair writes K
  new rows, which supersede K old ones, and the same rule loses the new tail.
- `stats.doc_count` and a scalar scan cannot be used to detect the condition — both report
  the full, correct count while the documents are unsearchable.

## Related

#594 (closed) is a different failure with a similar shape: there the loss needs a crash-recovered WAL, and it is `doc_count` that regresses. Here no crash is involved, `doc_count` stays correct, and what disappears is the tail of the vector index. They may or may not share a root cause in the `optimize()` rebuild path.
