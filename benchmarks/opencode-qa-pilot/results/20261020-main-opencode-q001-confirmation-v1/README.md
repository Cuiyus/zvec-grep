# Original q001: two prospective native confirmation pairs

The two-pair scoped-input replication rule was not met. This is a result for one previously observed task, not a favorable benchmark set.

| Repeat | Profile | Protocol valid | Quality gate | Session input incl. cache | Solver seconds | Outer slot seconds |
|---|---|---|---|---:|---:|---:|
| c01 | baseline | True | 9/10, pass | 2708800 | 772.76 | 826.11 |
| c01 | zvec-grep | True | 8/10, pass | 2295032 | 805.89 | 991.73 |
| c02 | baseline | True | 7/10, fail | 3949879 | 1031.83 | 1080.15 |
| c02 | zvec-grep | True | 9/10, pass | 2155014 | 947.59 | 1139.68 |

Each pair must independently have valid complete trials, both answers pass the original gate, no lower zg factual score, strictly lower zg input, and relevant successful correct-workspace MCP retrieval. Missing values are not zero.

The phase is closed after its four scheduled slots. The development pair is not pooled. Fixed profile order and shared within-pair Docker image cache limit causal and timing interpretation; whole-provider dollar savings are unknown.

[Structured results](summary.json) · [blind source review](blinded-review/review.json) · [execution audit](execution-review/execution-review.json). Run `python3 report-results.py` to rebuild this summary from the retained evidence.
