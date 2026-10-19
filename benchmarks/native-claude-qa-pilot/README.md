# Native Claude Code + Qwen pilot

This separate pilot tests the main benchmark's native Claude agent with the existing Qwen Anthropic endpoint. The main registry supports Claude/Opus only, so a small explicit routing adapter selects Qwen without editing the registry or the native agent loop.

The prior 22 custom Claude rows disabled `CLAUDE.md`, persistent sessions and task delegation. This stack enables native behavior and uses current upstream Rust zg at `a424e0ce06868dc9ad8f142f57de59b8f9b58699`. It is a whole-stack development comparison, not an isolated estimate of one setting.

Current status: offline revision 3 passed the real Linux Docker gate ([run 39335177244](https://github.com/Cuiyus/zvec-grep/actions/runs/39335177244)). Revision 1 exposed a fake-service nullable-schema incompatibility; revision 2 completed both native sequences but failed a guidance assertion because Claude removes the installer's two outer HTML comment markers. Revision 3 checks the entire unchanged guidance body and retains the earlier failed attempts. These are infrastructure checks with fake responses, never quality scores.

The live v1 precheck passed ([run 39336325047](https://github.com/Cuiyus/zvec-grep/actions/runs/39336325047)); its paid execution steps were skipped. Execution is currently on hold: revision 3 only exercised a synchronous child, while native Claude defaults to background delegation and the collector does not yet reconcile later completion. The [separate background Explore probe](async-probe-decision-v1.json) captures one baseline execution against a fake provider before the live pair. The frozen v1 plan remains retained as an unexecuted historical plan.

The separate live plan reuses the original q001 source, question and rubric unchanged. It schedules one fresh baseline then zg pair through the existing Qwen Anthropic endpoint, with no whole-trial retries. Quality is graded from blinded final answers against frozen source. A source-only/tool audit is required before any candidate conclusion.

Primary resource metric for this new phase is explicitly **Claude-reported per-model input tokens**, including uncached, cache-read and cache-write components of terminal `modelUsage`. Strict persisted root/child accounting remains separate corroboration. Missing usage is never filled with zero, reported totals are not billed cost, and this scope is not pooled with previous custom Claude or OpenCode totals. Setup/index, native execution, collection and total wall times are retained separately. One favorable development pair would justify confirmation, not establish a stable favorable benchmark set.

The [decision](decision.json) defines the bounded gate. The [parity audit](provenance/native-claude-parity-v1/feasibility-report.json) records the implementation differences. The [six-answer diagnosis](provenance/native-q001-coverage-diagnosis-v1/report.txt) shows why adding headings to the old prompt is not a new hypothesis. C6 grading sensitivity changes none of the original candidate decisions.

All development CI runs on `Cuiyus/zvec-grep`. Existing upstream binaries are read only; only package metadata is repaired for installation, preserving all runtime payload bytes.

[Revision 1 evidence](results/20261020-offline-r1/README.md) preserves the failed run, original artifact ZIP, source-bound diagnosis and the limitation that its rejected chat body was not retained.

[Revision 2 evidence](results/20261020-offline-r2/summary.json) retains the successful native sequences and original failed guidance assertion. The [live runner](live.py), [prospective plan](live-plan-v1.json), [decision](live-decision-v1.json) and [host-only grading contract](grader-contract-v1.json) define the next bounded experiment.
