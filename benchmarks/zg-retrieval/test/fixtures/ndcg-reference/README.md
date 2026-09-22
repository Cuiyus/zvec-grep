# Pinned nDCG reference implementation

`data.py`, `metrics.py`, and `LICENSE` are unmodified byte-for-byte copies from
[MinishLab/semble at 0051e000fcaac69a9c5d081ebbc8d4cb8508160b](https://github.com/MinishLab/semble/tree/0051e000fcaac69a9c5d081ebbc8d4cb8508160b).
The original paths and SHA256 values are recorded in `source.json`. The MIT
license and copyright notice are included in `LICENSE`.

`oracle.py` verifies these hashes and executes the original
Python functions. Only the imported `SearchResult` annotation is stubbed, and
annotation evaluation is postponed for Python 3.9 compatibility. The oracle
does not reimplement path matching, overlap, first-target rank, or nDCG.

The JavaScript tests compare metric outputs against this independent upstream
implementation. The inputs are fixtures, not retrieval runs. These files do not
contain Semble's annotation dataset. SWE-QA accepted-path projection is a separate
label choice in `../../../metrics/ndcg.mjs`, not part of upstream metric behavior.
