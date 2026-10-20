# Current-task repeated comparison, v1

The [frozen plan](../../repeat-plan-v1.json) schedules exactly 30 fresh baseline/zg pairs across the existing q001/q002/q003 tasks, ten pairs per task. Original questions, rubrics and the verified native runtime are unchanged. All historical observations remain separate.

Preparation passed 86 local checks, all 30 actual prepared-input dry runs, and 14 YAML/shell blocks. The two trailing-blank-line formatting changes after local validation preserve identical ASTs; their before/after files and updated control hashes are retained. The exact final commit must additionally pass fork CI preflight before the single paid dispatch. No paid execution result is claimed by these preparation files.

PR #261 score trimming removes one lowest and one highest score per task/profile and applies the same eight retained tuples to all resource means. Raw ten-repeat means and individual failures remain visible. Three tasks are below the five-task MAD guard, so no task-level exclusion is enabled. Paired bootstrap intervals and raw/trimmed dispersion will measure whether filtering actually improves repeatability.
