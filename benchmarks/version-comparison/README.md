# Node 0.2.2 versus Rust main

This experiment compares the unmodified npm release `@zvec/zvec-grep@0.2.2`
against upstream Rust `a09cd1236eee003feb699a17c5bf63d76f83f06c`.
`experiment.json` freezes the identities, registry integrity, models and five
E2e repetitions. All Actions run in `Cuiyus/zvec-grep`.

Retrieval uses the existing SWE-QA20, BEIR20, DuRetrieval10 and Quarry20 suites,
unchanged gold, hybrid/FTS/vector queries and five repetitions. The adapter only
translates CLI spelling and parses each version's public readiness output. Both
versions' official installers generate the MCP command consumed by the harness.
Public status aggregates do not prove byte-identical persisted indexes.

E2e uses the same 20 SWE-QA questions, OpenCode 1.18.4, GLM-5.2 and Qwen3.8-Max,
local Potion code and remote Qwen3.7 embeddings, five repetitions per version.
Both arms receive zg, with balanced alternating version order. The judge is
blind to versions and uses the unchanged reference/rubric. The legacy baseline
report slot means Node, and the zvec-grep slot means Rust. Index setup is reported
separately from agent time. A full matrix contains 800 planned agent trials;
failed-trial retries are retained separately. These are existing development
questions, not an independent held-out sample.

Run `.github/workflows/retrieval-only.yml` for each runtime/embedding pair,
and `.github/workflows/swe-qa-bench.yml` with embedding_scope local and remote.
Both workflows retain package hashes, raw evidence, per-task results and aggregate
reports. E2e gates the other 19 tasks on the first task in all selected model and
embedding configurations. Incomplete reports retain the planned denominator and
list missing cases. Numerical improvements do not decide CI success.

The primary version summary includes every completed task, without filtering on
observed score or token changes. The historical aggregate excludes large changes
and is retained only as `legacy_filtered_aggregate` for sensitivity analysis.
Runs dispatched before this reporting fix retain their immutable CI artifacts;
`zg_bench.version_summary.include_all_cases` recomputes the full-case summary
offline from those original cases without any new agent or judge calls.

The registered Actions paths are reused on this isolated experiment branch.
Historical workflow contract tests read the legacy fixtures; the active workflow
guards and complete runtime matrix are checked by test_version_e2e.py.
