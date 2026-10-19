# Confirmation execution audit scaffold

Status: ready; not run against confirmation evidence. Do not interpret scaffold readiness as an execution, quality, usage, or benefit verdict.

The audit checks both frozen c01/c02 repeat artifacts, all retained ZIP bytes, exact plan/head/harness, 1232 source files plus four generated task files, independently recomputed Harbor Packager and legacy dirhash values, original Rust payloads, native settings, root/container isolation, fresh installation/index state, cleanup, and separately labelled raw/setup/capture/export/solver timings.

Both repeats intentionally use baseline then zg. Profile order is not randomized or counterbalanced. Docker image/layer cache may be shared within a repeat. These remain interpretation limitations, not post hoc failure gates. Quality, full tool trees, and token arithmetic belong to separate reviews. The confirmation blind key is never read.

Expected saved layout: global `run-metadata.json`, `job-metadata.json`, `artifacts-metadata.json`; each `c01`/`c02` contains `artifact-metadata.json`, `artifact.zip`, and `raw/qa-confirmation-{run,reports}/`. Missing evidence fails closed while retaining diagnostics and continuing the other repeat. The fixed local source tree is used only as a verified byte/hash reference; no development metrics are imported.

After the parent confirms final evidence and supplies artifact identities:

```sh
python3 audit_confirmation_execution.py \
  --expected-artifact c01:ARTIFACT_ID:ARTIFACT_SHA256 \
  --expected-artifact c02:ARTIFACT_ID:ARTIFACT_SHA256
```

Only `execution-review.json` (or `--report`) is written. All other inputs are read-only; no Docker, native agent, provider, CI, or network calls occur. The only subprocess operation is local `git show` of the fixed execution commit.

Timestamp revision: actual executed-step and native/outer-slot intervals establish serial execution. The raw job-level started_at is retained but may be a queue placeholder. Missing executed-step timestamps are unconfirmed. Six synthetic cases pass; no confirmation artifacts have been read. Prior scaffold bytes and readiness are retained alongside scaffold-revision-note.json. At final execution explicitly pass --source-root for the newly prepared confirmation c01 task source.
