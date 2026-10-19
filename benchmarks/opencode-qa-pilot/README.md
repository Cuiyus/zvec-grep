# Native main OpenCode q001 pilot

This independent development phase runs the **original q001** once with baseline
tools and once with zg. It borrows the exact current-main Harbor/OpenCode runner
at `a424e0ce06868dc9ad8f142f57de59b8f9b58699` and the Rust package from that
commit. The question, pre-fix source and seven-criterion rubric are unchanged.

The plan in `experiment.json` remains the unchanged frozen record of this single
development pair. Its only live dispatch, [run 39308807224](https://github.com/Cuiyus/zvec-grep/actions/runs/39308807224),
finished with **zero valid completed pairs** at commit `5ccf5380`. Both scheduled
slots were attempted once and cleaned up. Collection rejected the evidence;
the artifact was still scanned and retained. This phase is closed without a
quality or efficiency conclusion, and has not been rerun.

## Observed infrastructure failures

- Baseline delivered a final answer and complete persisted-session usage, with
  unchanged source hashes before and after execution. The required native
  session export failed JSON parsing, leaving the protocol invalid. Native
  OpenCode writes an export diagnostic to stderr; Harbor merges stderr into
  stdout, which this wrapper attempted to parse as JSON. Existing native mock
  stdout/stderr captures reproduce this incompatibility. The exact failing
  live export output was not retained, so additional live diagnostics remain
  unknown.
- The zg slot failed installation before embedding authorization, indexing,
  MCP setup or QA. The frozen artifact is a native platform package containing
  `bin/zg` but no npm `bin` declaration. Main's global npm installer therefore
  cannot establish the `zg` command it expects. The earlier Rust smoke test
  executed the extracted binary directly and did not test that installation
  boundary. Actual live npm output was not retained.

The completed baseline text received **9/10** in a fresh blinded Codex source
review: C1–C6 full credit, C7 missing explicit final validation in a new process.
Its core citations pass, with noncritical overclaims retained in the report.
This is a diagnostic text score, not a valid trial or a paired quality result.
There is no treatment answer or comparable treatment usage; missing values are
not zero scores or zero cost. Baseline persisted-session input was 2,590,012
(2,461,568 cached), output 23,825 including 15,125 reasoning tokens, with 77 tool
calls. None of these values establishes a relative benefit.

[Machine-readable summary](results/20261019-main-opencode-q001-v1/summary.json),
[original artifact ZIP](results/20261019-main-opencode-q001-v1/artifact.zip),
[diagnostic blind review](results/20261019-main-opencode-q001-v1/diagnostic-blinded-review.json)
and [independent execution audit](results/20261019-main-opencode-q001-v1/independent-execution-audit.json)
retain the failed attempt. The ZIP SHA-256 matches GitHub's artifact digest and
all 43 extracted files. The prior 22 Claude rows remain unchanged and separate.
The [export diagnosis](results/20261019-main-opencode-q001-v1/baseline-export-diagnosis.json)
and [installation diagnosis](results/20261019-main-opencode-q001-v1/treatment-install-diagnosis.json)
describe the two integration defects and the remaining evidence limits.
The archive's `report-results.py` rebuilds its summary, and
`reproduce-export-merge.py` replays the saved mock output without model calls.
The original execution-audit scripts also depend on the recorded absolute paths,
pinned upstream modules and Harbor environment; the full execution audit is not
a standalone rerunnable bundle. The relevant Harbor Docker source and license
are retained with the diagnostic fixtures.

The 61 offline tests and Linux loader smoke passed before dispatch, but did not
cover these two real adapter/container boundaries. Any future repair must be a
separate infrastructure revision with actual Harbor/Docker fake-provider
integration before a newly frozen live pair. It cannot reuse this baseline,
retroactively validate this phase, or change the question/rubric to obtain a
favorable outcome. The original favorable-benchmark-set goal remains unmet.
The separate [infrastructure repair design](provenance/infrastructure-repair-design-20261019.json)
proposes metadata-only package compatibility and file-based evidence capture,
with a real Docker fake-provider gate. The versioned implementation is now in
`repair_v2/`, under a separate [offline-only decision](repair_v2/decision.json).
Its [fork workflow](../../.github/workflows/opencode-qa-repair-v2.yml) accepts no
live-run input and receives no live provider secrets. Linux validation passed in
[run 39314027006](https://github.com/Cuiyus/zvec-grep/actions/runs/39314027006)
at `cd0f4dbc`: 50 unit tests and the actual Harbor/Docker integration passed.
The [retained archive](results/20261020-infrastructure-v2-r1/archive.json) includes
the original 130-file ZIP and an independent audit with 775 checks, zero failures.

The repaired package adds only `bin: {"zg": "bin/zg"}` to the original npm
metadata. Rust, its native library and data payloads remain byte-for-byte
identical. Canonical uncompressed gzip blocks make the derived archive identity
independent of the host compression library. The v2 wrapper downloads native
exports, stderr and exit status separately, retains partial failures, and checks
all counted root/child message and tool identities. The Docker gate exercises
the unchanged native installer, real indexing and MCP against local scripted
providers, plus malformed-export and timeout/cleanup cases. These synthetic
answers are infrastructure evidence only and are never assigned QA scores.

`repair_v2/inputs.json` binds the gate's code, workflow, decision, closed v1
inputs and derived package identity. Any change requires a newly recorded input
revision. This successful gate supports the separately recorded
[live v2 decision](provenance/live-v2-decision-20261020.json); it cannot
retroactively validate v1 or establish a zg quality/efficiency result.

The new frozen [v2 plan](experiment-v2.json) binds the canonical install package,
all original evaluation/stopping rules, the retained Docker proof and its exact
GitHub provenance. `pilot_v2.py` adds whole-process-group cleanup checks and
requires both process and container cleanup before the next slot.
The separate [v2 fork workflow](../../.github/workflows/opencode-qa-pilot-v2.yml)
runs controller preflight on push, including the Linux descendant cleanup test.
Only an explicit `execute_pair=true` dispatch runs one fresh baseline then one
fresh zg slot. No old answer is reused. Live execution remains pending at this
freeze; failed attempts remain evidence and are not automatically retried.

## Why this phase exists

The earlier Claude experiments have 22 retained trials. They did not establish
a stable favorable set. q001 is the first original zvec issue task and has
already been observed; it is development material, not a held-out selection.
The stopped q004 final-verification prompt branch remains stopped.

Current main uses OpenCode 1.18.4, Harbor 0.18.0 and Qwen3.8-max through the
OpenAI-compatible endpoint. Native prompts, tools, child agents, zg installation
guidance, API, budgets and usage accounting differ from the Claude stack. This
is a whole-stack experiment. Its results must not be pooled with the Claude
rows or interpreted as an isolated effect of changing the CLI.

## Fixed comparison

- One fresh baseline container, then one fresh zg container; no trial retries.
- Same q001 source export, question and model request configuration.
- Current-main Rust, fresh remote `qwen/qwen3.7-text-embedding` index for zg.
- Native output allowance 32,000 per request; agent timeout 1,800 seconds.
  Each slot has a 3,600-second outer limit including setup. No additional native
  tool, turn, request or dollar cap is imposed.
- Main's web tools are denied; shell access remains native. Full root and child
  tool records must pass a source-only/no-web audit. This is not network isolation.
- Full source hashes and added files are checked before and after execution.
  Gold, issue resolution, rubric and old answers remain outside solver containers.

`pilot.py` validates the plan, prepares a source-only Harbor task, builds the
native commands and collects complete or partial evidence. `qa_agents.py`
extends the native adapters only to capture source/runtime/session evidence.
Harbor's verifier reward describes structural validity, **not answer quality**.
Only the unchanged q001 rubric determines answer success.

## Evidence and decision

Final answers require a completed root assistant turn that agrees with the ATIF
trajectory. Child or intermediate text cannot substitute for a final answer.
The source audit, session tree, native tool records, command/config identities,
raw logs and failed attempts are retained. Artifacts are scanned for configured
secret values before upload.

Usage has the explicit scope `opencode-session-tree-v1`: persisted root and
recursive child messages, including cached input and reasoning output. It
excludes unpersisted title/background requests, other requests absent from the
session database and embedding setup. Missing/zero provider dollar costs are
unknown. Solver wall, evidence-export overhead and setup/cold wall are separate.

The prospective candidate rules are in the plan. A quality candidate requires
zg to pass the original gate while baseline fails, with no lower factual score.
A scoped input candidate requires both to pass, no lower zg factual score and
strictly lower zg session-tree input. Both require valid complete trials and
successful relevant retrieval from the actual correct workspace. Other resource
metrics are descriptive and cannot replace these rules.

If neither candidate occurs, this attempt stops. A positive pair permits only a
separately frozen confirmation decision; it cannot establish stable benefit or
complete the original favorable-set goal.

## Execution

Use `.github/workflows/opencode-qa-pilot.yml` on **Cuiyus/zvec-grep**. The workflow
checks the current upstream main identity, exports its exact harness, installs
locked dependencies, tests native requests against a local mock provider,
validates the wrapper and exports q001 before exposing model credentials. It
reads an existing upstream Rust artifact without triggering upstream CI.

The runtime main checkout is separate from the older `swe-qa-bench` files on
this development branch. Never silently import that older checkout as the
current-main runner.
