# Native Claude Code + Qwen pilot

This separate pilot tests the main benchmark's native Claude agent with the existing Qwen Anthropic endpoint. The main registry supports Claude/Opus only, so a small explicit routing adapter selects Qwen without editing the registry or the native agent loop.

The prior 22 custom Claude rows disabled `CLAUDE.md`, persistent sessions and task delegation. This stack enables native behavior and uses current upstream Rust zg at `a424e0ce06868dc9ad8f142f57de59b8f9b58699`. It is a whole-stack development comparison, not an isolated estimate of one setting.

Current status: provider-offline Docker integration in preparation. No live trial or benefit result exists for this protocol. The original q001 source, question and rubric are reused unchanged. Fake responses exercise retrieval, child tasks and accounting; they are never quality scores.

The [decision](decision.json) defines the bounded gate. The [parity audit](provenance/native-claude-parity-v1/feasibility-report.json) records the implementation differences. The [six-answer diagnosis](provenance/native-q001-coverage-diagnosis-v1/report.txt) shows why adding headings to the old prompt is not a new hypothesis. C6 grading sensitivity changes none of the original candidate decisions.

All development CI runs on `Cuiyus/zvec-grep`. Existing upstream binaries are read only; only package metadata is repaired for installation, preserving all runtime payload bytes.
