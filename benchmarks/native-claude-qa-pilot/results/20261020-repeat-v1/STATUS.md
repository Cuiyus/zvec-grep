# Current-task repeat study status

Preparation is complete. Paid execution has **not started**.

[Preflight revision 1](preflight-r1/diagnosis.json) failed before setup because upstream merged a CI-only commit after the local freeze; no solver ran. Its always-run scan also exposed an uninitialized-path handling error. Full run/job metadata and logs are retained.

Revision 2 checks the complete current Rust tree and retains actual main/tree metadata, allowing unrelated non-Rust changes while blocking a changed Rust implementation. It initializes retention early and uses a byte-identical standalone copy of the upstream stdlib secret scanner even when setup is incomplete. The original native solver/runtime, tasks, scoring, schedule and statistical policies are unchanged. Nine targeted driver tests, 14 shell blocks, two embedded Python blocks, a cross-chunk scan canary and final real control validation passed. The revised exact commit must pass fork provider-free preflight before the single paid dispatch.

The original [preparation manifest](preparation-manifest.json) and [README](README.md) describe the immutable preparation snapshot at commit `c3c626587f465ee938646f3275b3acfa585aed13`. Subsequent status and preflight records are additional evidence.
