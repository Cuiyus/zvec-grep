# Native Claude q001 confirmation: not confirmed

The two scheduled pairs did **not** reproduce quality-preserving reported-input reduction in both pairs. Both used Claude Code 2.1.212 with Qwen3.8-max, the unchanged q001 question and rubric, the pinned upstream native harness and Rust zg, and fresh runner/container/index state. The sole execution was [fork run 39343873279](https://github.com/Cuiyus/zvec-grep/actions/runs/39343873279), attempt 1, execution commit `fd0c94c37c95e2afb4b5482530904574f5719c5f`.

| Pair / scheduled order | Baseline rubric score | zg rubric score | Reported input, baseline → zg | Input change | Native execution change | Outer-slot change | Original primary gate |
|---|---:|---:|---:|---:|---:|---:|---|
| c01 / baseline → zg | 9/10, pass | 7/10, fail | 3,190,126 → 1,561,493 | −51.1% | +0.7% | +13.7% | Fail |
| c02 / zg → baseline | 8/10, pass | 8/10, pass | 1,908,200 → 974,232 | −48.9% | −22.3% | −4.7% | Pass |

The primary rule required both answers to pass the original quality gate, zg's rubric score to be at least baseline's, valid execution/source evidence, eligible lower reported input, and a relevant successful zg retrieval **separately in both pairs**. c01 fails the quality and non-inferiority conditions. There is no secondary quality-candidate pair. Development results are not pooled into confirmation.

The c01 zg answer explains the core recovery/flush/metadata causal chain but misses the full index-statistics explanation, handling failure when reopening the member WAL, and a control in its proposed regression test. These omissions receive the original C4/C6/C7 scores of zero. The predeclared C6 lifecycle-only sensitivity adds one point to every answer; c01 remains worse than baseline, so the confirmation outcome is unchanged. All four answers also retain noncritical unsupported auxiliary claims, detailed in the grading report.

Execution audits passed 68/68 checks for c01 and 71/71 for c02. Source-scope audits reviewed all 146 actual tool calls. Four anonymous exact-byte answers were graded together using IDs assigned before dispatch, followed by a separate blinded quote/citation consistency review. That review records one non-scoring citation annotation erratum: the cited optimizer utility's explicit `Flush()` is Android-conditional. Original grades remain unchanged.

- [Machine-readable conclusion and separate timing scopes](summary.json)
- [Original blind grades](grading/grades.json), [consistency review and erratum](grading/consistency-review.json), and [pre-dispatch assignment, disclosed after grading](grading/unblinding/assignment.json)
- [c01 original evidence and audits](c01/README.md), [c02 original evidence and audits](c02/README.md), and [complete CI retention](ci/retention-status.json)
- [Frozen confirmation decision](../../confirmation-decision-v1.json) and [plan](../../confirmation-plan-v1.json)

Reported input includes uncached input, cache reads and cache creation; retained terminal messages corroborate all four usage fields. These totals are not verified provider billing. Outer slots include fresh setup/index work but exclude CI queue, shared preparation and later collection. The opposing outer-time changes do not establish repeated time savings.

Scope limitations are retained: c01 has one MCP snippet punctuation omission; c02 observed environment directory names without reading extra file content, and one local grep command returned an option error. Full evidence is archived. The c01 interim snapshot remains unchanged; its then-pending grading status is superseded for the completed phase by `summary.json`.

Stop this q001 confirmation phase after the two planned pairs. No retries, replacement cases, prompt/rubric changes, or additional q001 sampling are part of this result. A later experiment on other issues needs its own prospective decision. A favorable multi-task benchmark set has not yet been established.
