# q004 native task preparation (no solver execution)

The unchanged architecture/configuration QA from closed issue #758 is prepared for the native Claude Code + Qwen stack. This phase makes no new quality or performance claim. The earlier custom-agent q004 outcomes remain separate and are known to the parent; this is not a new holdout.

The exact source export contains 1,596 files at `1a573058b24e11cdb7e91f27d6638c5f1c0cdcaa`. Harbor 0.18.0 identifies it as `zvec-q004`, with 1,600 packaged task files. Both task hashes and the complete source inventory are retained in [verification](raw/verification.json) and [prepared metadata](prepared/q004/prepared.json). The adapter reuses the original preparation format (whose legacy artifact label still says reserve), while dataset and task identities explicitly name q004. Original q001-q003 inputs and preparation modules were not edited.

Four input-integrity/isolation tests passed. Replaying retained real q001 evidence as q004 failed 16 identity checks, including actual task selection. Two real native command previews selected `zvec-q004`, its own source manifest and the correct baseline/zg classes, with one attempt and zero retries. These were command-generation checks only: no Docker trial, provider request or paid CI execution occurred.

The original five-criterion rubric has ten independent one-point units. Its host-only native grading contract retains the original factual, core-citation and material-contradiction gates, accepts equivalent evidence, and preserves unresolved judgments. No repair or regression-test criterion was added to this architecture question. Neither question nor rubric changed after prior outcomes.

A fresh upstream check observed main `49de68563a7d3da9b408dd64277a8a51e3dd0fc6` and Rust tree `de02030056a3224012725799d845bf2be9edae2b`, identical to the existing pinned a424 binary build. This does not make the older pinned harness itself identical to the current main harness.

Actual execution still needs a separate frozen one-pair plan/decision and a provider-free Linux preflight on the user's fork. This preparation authorizes no paid run. A single future pair would be development evidence; any ten-per-profile confirmation would need its own prior cohort freeze and exclude development answers. No historical or q001 repeat sample can be pooled.

The full exported source and dependency Git cache remain locally at the exact paths in [retention.json](retention.json). The public immutable commit and full inventory permit reconstruction. All retained controller and support-file bytes are listed in [manifest.json](manifest.json).
