# Qoder + Qwen3.8-Max Workspace QA

**Current status: the documented `zg install` integration passed its fresh
[native probe](https://github.com/Cuiyus/zvec-grep/actions/runs/34919506961) and
[task-3 smoke](https://github.com/Cuiyus/zvec-grep/actions/runs/34919707888).
The separate 200-trial formal matrix is running in the latter workflow; no
completed standard-installation efficacy result is available.**

The earlier implementation used released zg 0.2.2 through a benchmark-managed
MCP bridge and manually assembled Qoder configuration. That did not satisfy the
requested standard installation. All earlier probe, smoke, judge-recovery and
full-run observations are now **legacy bridge diagnostics only**. They cannot
validate, populate, or be combined with the new standard-installation experiment.
The original artifacts remain intact; the classification is recorded in
[legacy-bridge-runs.json](data/legacy-bridge-runs.json).

The corrected experiment restarts **all 200 formal trials**: the same 10 tasks,
10 repetitions per arm, baseline and with-zg. Its task selection, model versions,
remote embedding, index size cap, QA budgets, source data and judge remain fixed.
Old baseline observations are excluded as well as old with-zg observations.

## Standard installation contract

Follow the repository [README quickstart](../../README.md) and
[agent installation guide](../../docs/01-agents.md#install-an-integration), using
the pinned release rather than the branch's development package:

```bash
npm install -g @zvec/zvec-grep@0.2.2
zg install --target qoder --yes
```

`qoder` is the canonical installer target even when the executable is
`qodercli`. The installer manages the `zvec_grep` MCP entry, search guidance,
Qoder trust and exact tool permissions. For Qoder CLI the configuration is
`settings.json` and `AGENTS.md` under `${QODER_CONFIG_DIR:-~/.qoder}`; the IDE
entry is normally `~/.qoder/mcp.json`, with `QODER_IDE_MCP_PATH` available for
isolation. Documented transport options may be recorded explicitly. Start a new
Qoder session after installation so it reads the installed configuration and
guidance. A hand-written replacement MCP entry or benchmark-supplied substitute
for the installed guidance is not evidence of standard installation.

Each with-zg session must receive the actual installation output in its isolated
configuration/home. Record the release identity, installation command and exit
status, generated configuration/guidance hashes, and the Qoder session's loaded
MCP/tool evidence. Baseline keeps its independent clean configuration. Package
and corpus preparation can be shared without sharing answers or session state.
The documented installer may start the local server when possible; verify the
selected transport and readiness as described in the
[setup verification guide](../../docs/01-agents.md#verify-the-setup).

Retain installation files both before and after each session. Qoder 1.1.45
natively adds its three enabled `securityScan` defaults at startup; this verified
addition and JSON formatting changes are allowed, while installed MCP settings,
permissions and `AGENTS.md` must remain unchanged. Both arms retain the native
Security `SessionStart` outcome; an observed startup failure invalidates an
otherwise completed trial without discarding its model usage or answer.

MCP tool permission and remote embedding authorization are separate. The user
has selected remote `qwen/qwen3.7-text-embedding` for this experiment; its
workspace authorization and provider credential must use the released product's
supported path and be recorded separately. An API key alone does not establish
that the installed MCP server can perform the authorized embedding operation.
New validation must exercise that installed path. No local embedding or model
fallback is allowed.

## Frozen benchmark and controls

The [selection protocol](../../docs/benchmark-protocols/qoder-workspace-lite-cn-selection.zh-CN.md)
compares the five requested candidates. [lock.json](data/lock.json) freezes the
Workspace-Bench Lite CN subset, dataset/workspace revisions and input hashes:

- Code QA: 3, 127, 128.
- Other read-only workspace QA: 139, 143, 158, 160, 161, 191, 373.
- Qoder CLI 1.1.45; requested and observed model `qwen3.8-max`; zg 0.2.2.
- Remote embedding `qwen/qwen3.7-text-embedding`; GLM-5.2 rubric judge.
- 10 repetitions per arm per task; balanced AB/BA order fixed per task.
- 4 CPU / 8 GiB per task container; 900 seconds, 60 model requests,
  120 tool calls and 600,000 inclusive input tokens per QA trial.
- Uniform 1 MiB index file-size cap; larger files remain fully readable in both
  arms and are skipped as whole files by the index. Other selection follows
  production defaults, without gold-dependent filtering.

Both arms receive the original question and immutable original persona working
files, including distractors. Only nested `.git` metadata is uniformly omitted.
Original inputs must match the full workspace by SHA. The harness materializes
the terminal answer verbatim into the original requested Markdown/text filename
outside the corpus; file-delivery rubrics do not demonstrate agent file writes.
This is a 10-task QA subset, not the official full Lite leaderboard or a pure
code-QA benchmark. Chinese tasks may contain English documents or code.

The source is prepared once per task. Each repetition uses a fresh container,
session and home; with-zg receives an isolated index copy. Installation, source
and index preparation are recorded separately from QA time and token usage.
Installing the integration correctly does not require redownloading the complete
workspace or rebuilding its index on every repetition.

## CI validation and execution

All actual installation checks, dataset preparation and model calls run through
[GitHub Actions](../../.github/workflows/workspace-qa-qoder.yml). The corrected
sequence is offline validation, a tiny probe of the **standard-installed** path,
a fresh task-3 smoke pair, and then the 10-task / 200-trial formal matrix.
The native probe passed with one successful vector retrieval; its original
artifact hashes and installation evidence are recorded in
[native-probe-validation.json](data/native-probe-validation.json). The fresh
smoke pair also passed; [native-smoke-validation.json](data/native-smoke-validation.json)
records its two completed and judged answers, including one successful QA zg
call. Both rubric scores were 18/21. Input tokens were 169,184 baseline and
466,308 with-zg; these single-pair observations are retained unchanged and
excluded from the formal matrix. Smoke validation establishes pipeline readiness,
not a token-saving or quality-improvement claim.
An old bridge probe or smoke cannot unlock the corrected formal run, even when
its old code hashes match or its bridge retrieval succeeded.

Scope names distinguish offline `validate`, tiny `probe`, task-3 `smoke`, and
formal `full`; `rejudge` is only for a separately pinned scoring recovery. The
existing [judge-recovery record](data/judge-recovery.json),
[probe record](data/probe-validation.json) and
[smoke record](data/smoke-validation.json) describe the legacy bridge protocol.
Their success fields retain their historical meaning and do not certify the
new installation. Any future validation reuse requires evidence from the
standard-installation protocol itself, with compatible installation and
execution configuration.

Natural non-use of zg on an original question remains an observation. A
separate real installed-MCP vector probe establishes connectivity; QA logs must
show the installed tool registration and integrity. Failed tool calls cannot be
represented as successful retrieval. Neither a low judge score nor a model
budget failure triggers a replacement QA sample. Smoke and probe observations
remain outside the 200 formal trials.

Verified 16 MiB archive blocks may be cached under frozen archive identity and
persona, with a 4 GiB per-persona cap; Research only fits partially. Full CRC/SHA
checks still apply. Only completed compatible index seeds may be reused, with
content/runtime/model/endpoint/index-configuration identity and fresh validation.
The standard installation must establish compatibility before using a seed from
an earlier preparation; legacy bridge validation does not establish it.
Candidate answers, judgments, sessions and modified trial copies are never
preparation caches. Cache hits and build/download time are reported separately.

Required Actions credentials remain Qoder access, a remote embedding key, and
GLM judge access for smoke/full. The configured Model Studio workspace key may
serve embedding as already configured. Credentials are passed by environment
name and scanned out of artifacts; baseline receives no embedding key. Remote
embedding usage is not added to Qoder's input tokens.

## Legacy bridge evidence

The [legacy registry](data/legacy-bridge-runs.json) is the classification record
for all earlier observations, including any partial formal results:

| Run | Historical observation; diagnostic use only |
|---|---|
| [34818892317](https://github.com/Cuiyus/zvec-grep/actions/runs/34818892317) | Task 128 setup failed because the SDK remote-operation permit was missing; no QA answers. |
| [34821814894](https://github.com/Cuiyus/zvec-grep/actions/runs/34821814894) | Task 128 reached the 30-minute index budget at 483/1226 files; no QA answers. |
| [34828583811](https://github.com/Cuiyus/zvec-grep/actions/runs/34828583811) | Both task-3 answers and scores existed, but the only bridge query failed due to missing embedding credentials. |
| [34831451904](https://github.com/Cuiyus/zvec-grep/actions/runs/34831451904) | Bridge setup probe succeeded; both answers completed with natural zero zg usage; one judge response was invalid. |
| [34832632725](https://github.com/Cuiyus/zvec-grep/actions/runs/34832632725) | The synthetic bridge probe completed in 26.117 seconds; this is not a standard-installation check. |
| [34833581759](https://github.com/Cuiyus/zvec-grep/actions/runs/34833581759) | One additional judge attempt completed the preceding bridge smoke's scoring, preserving the original QA. |
| [34833796405](https://github.com/Cuiyus/zvec-grep/actions/runs/34833796405) | A new bridge smoke had a with-zg budget-exhausted outcome; the formal matrix did not start. |
| [34837171877](https://github.com/Cuiyus/zvec-grep/actions/runs/34837171877) | Bridge full run at `d1962c2107d80c8685acc5096d2e927972317a7c`; cancellation was requested during the installation correction. Any partial results remain legacy diagnostics. |

The 1 MiB cap and task-3 smoke selection were frozen before the first QA answer
or judge score, following the two task-128 setup failures. They remain unchanged
in the corrected experiment. Later bridge repairs, judge recovery, cache timings
and smoke reuse explain those historical runs; none establishes standard
installation or transfers any QA sample into the restarted formal experiment.

## Measurements and recovery

Retain judge score, inclusive native Qoder input tokens, attempted tool calls
(including zg calls), agent wall seconds, outcome and original traces for every
planned trial. Cached input is included in Qoder's counter and is not added
twice. Missing measurements stay null; failures and unstarted trials remain in
the planned denominator. Record installation/setup, embedding, host verification
and judge time separately. The corrected runtime's timer boundaries require new
validation; old bridge latency cannot establish standard-installation latency.

The custom source-grounded GLM-5.2 adapter retains all original rubric texts and
types, including known grounding defects. It is not the official ClaudeCode
filesystem judge. Valid assessments are final regardless of score. Transient
transport errors and invalid/truncated responses share the fixed three-attempt
budget with the same model, candidate and prompt; recovery preserves previous
attempts and does not reset that budget.

Results pair task/repetition, average within each task, then weight tasks equally;
report code QA and other workspace QA separately. Incomplete observations produce
a partial report, not a completed efficacy claim. Failure audits retain actual
termination reasons, budgets and explicitly labelled usage lower bounds without
substituting them for missing final usage or rerunning failed model outcomes.
No automatic recovery of the legacy bridge run may fill the new experiment.

### Native continuation without replacing attempted samples

The original native formal run is
[34919707888](https://github.com/Cuiyus/zvec-grep/actions/runs/34919707888),
at `f33b0dac3b99bb6bba74d0f50c1596b5fddf3112`. Its task-3 second
with-zg trial hit Qoder's 60-second response-header timeout. Qoder 1.1.45
emits a local zero-usage `<synthetic>` notice for that error; the original
parser treated the notice as a different model and stopped the remaining 16
slots. The failed trial, three earlier answers, and their judge results are
retained exactly as originally recorded.

The parser now distinguishes this released local notice from real model
identity changes. A failed request remains failed, its request count stays in
the budget, and any observed usage is a labelled lower bound. Missing final
input tokens remain null. This correction does not rewrite earlier records.

The [continuation workflow](../../.github/workflows/workspace-qa-continue.yml)
is staged on this branch. It can start only after the original full run has
finished and a `data/continuation-dispatch.json` record matching
[continuation-source.json](data/continuation-source.json) is committed. The
dispatch record is deliberately absent while the original run remains active.
It selects only original `planned` slots with no execution evidence. Completed,
failed, budget-exhausted, or ambiguous running slots cannot be resampled.
The full 10-task × 2-arm × 10-repetition denominator and original order stay fixed.

Before any new QA trial, CI verifies the exact source run and artifact identities,
records hashes of the permitted code changes, and rechecks the original source
files, prompt, Node/Qoder/zg versions, model, embedding, limits and install
configuration. Each new with-zg trial still uses standard `zg install`.
Original trial files and the original ledger, manifest and judgements are
retained under `runs/continuation-evidence` and checked again during aggregation.
Only new answers are judged. The aggregate chooses exactly one merged ledger
per task, so old and new artifacts cannot double-count observations.

`--require-executed` means all 200 planned slots have terminal execution records
and every completed answer has been judged. It can pass with honestly recorded
failed samples. The separate `--require-complete` efficacy gate remains strict;
the report continues to show missing pairs and failures and cannot claim a
complete quality or token comparison from partial outcomes. Workflow reruns and
duplicate continuation runs are rejected to prevent accidental replacements.

## Offline validation

```bash
python3.12 -m unittest discover -s benchmarks/workspace-qa/tests -v
```

The standard-installation probe, fresh smoke and 164 offline checks passed.
The formal matrix is still in progress; no completed 200-trial result is claimed.
