# Native main OpenCode q001 pilot

This independent development phase runs the **original q001** once with baseline
tools and once with zg. It borrows the exact current-main Harbor/OpenCode runner
at `a424e0ce06868dc9ad8f142f57de59b8f9b58699` and the Rust package from that
commit. The question, pre-fix source and seven-criterion rubric are unchanged.

The plan in `experiment.json` is frozen for this single development pair.
No live result or favorable benchmark set is claimed at preparation time. The separate fork workflow defaults to offline validation; only
its explicit `execute_pair=true` dispatch runs the two slots. Failed attempts
remain evidence and are not automatically retried.

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
