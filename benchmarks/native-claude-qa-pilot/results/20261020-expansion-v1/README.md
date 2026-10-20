# Completed native reserve-task expansion

[Run 39353782766](https://github.com/Cuiyus/zvec-grep/actions/runs/39353782766) completed both original task pairs. The frozen primary condition **failed**; this phase establishes no stable favorable benchmark set. See [summary.json](summary.json) for exact metrics and preserved original gates. Historical q001 results are excluded.

| Task | Baseline / zg score | Original quality pass | Reported input change | Outer-slot change | Primary |
|---|---|---|---|---|---|
| q002 | 9 / 9 | False / True | -43.96% | +23.39% | failed |
| q003 | 9 / 8 | False / False | +23.26% | +57.06% | failed |

q002 is a secondary quality candidate: zg passes the unchanged gate while baseline misses the complete C4 preservation explanation, despite equal total points. q003 passes neither quality gate. Its zg run also listed a session directory outside supplied source; the strict source-scope failure remains, while no external file content was observed.

Both execution/accounting audits passed and all reported aggregates match complete persisted session trees. Reported tokens are not billing; fresh setup/index, native execution and outer-slot wall are distinct measurements.

Original [grades and consistency reports](grading/), [anonymous answer packets](blind-packets/), [unblinding receipt](unblinding/receipt.json), [final CI metadata/logs](final-ci/) and per-task execution/scope evidence are retained. Original scorer blindness is self-reported. Root is not claimed blind, and the consistency review's condition-metadata exposure is recorded. q003's separate erratum changes quote locators only; original grades are unchanged. Task archive manifests remain immutable.

The PR #261 supplementary statistics cannot trim a one-pair sample or screen two tasks. All observations remain. The subsequent current-task repetition study is separately frozen and excludes this phase from its sample.
