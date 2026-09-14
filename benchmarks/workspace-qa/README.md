# Qoder + Qwen3.8-Max Workspace QA

This experiment measures adding zg 0.2.2 to Qoder CLI 1.1.45 on an original
Workspace-Bench Lite CN QA subset. It uses **remote
`qwen/qwen3.7-text-embedding`**, as requested; there is no local model fallback.
It does not reproduce the official full Lite leaderboard.

The [selection protocol](../../docs/benchmark-protocols/qoder-workspace-lite-cn-selection.zh-CN.md)
compares the five candidate benchmarks and explains exclusions. The
[lock](data/lock.json) freezes 10 tasks, dataset/workspace revisions and source
hashes. Tasks 3, 127 and 128 are code QA; 139, 143, 158, 160, 161, 191 and 373
are other read-only workspace QA. Original Chinese tasks may contain English
code or documents; this run does not add the separate English task split.

## CI execution

[Qoder Qwen3.8-Max Workspace QA](../../.github/workflows/workspace-qa-qoder.yml)
runs on this branch. A push affecting this experiment first validates the harness,
then runs task 128 once per arm. Only a successful smoke, including complete
measurements and rubric judgments, unlocks the 10-task matrix. Every task runs
10 independent repetitions per arm: **200 formal runs**, plus two smoke runs.
Smoke observations are excluded from the formal report. Manual dispatch supports
`smoke` and `full` scopes once GitHub exposes the workflow for dispatch.

Both arms use the same original question, read-only tools, 4 CPU/8 GiB container
limits and 900-second agent limit. Balanced AB/BA ordering is frozen per task.
With zg additionally receives the read-only MCP search tool. The harness writes
the final response verbatim to the requested Markdown/text filename, outside
the immutable source workspace. File-delivery rubric successes therefore do
not establish that the agent itself wrote files.

Each job extracts the entire original persona's working files, including
distractors, from the pinned ZIP using verified byte ranges and per-file CRC/SHA.
Only nested `.git` metadata is uniformly omitted; no corpus selection uses the
hidden dependency list. Original inputs must match the full workspace by SHA.
Each task builds a fresh remote-model index before its paired trials; each
with-zg run receives an isolated copy. Index preparation is reported separately
from agent time, tokens and tool calls. The runner never loads a Potion index.

Required Actions secrets:

- `QODER_PERSONAL_ACCESS_TOKEN`: Qoder native model access.
- `GLM_API_KEY`: existing Model Studio workspace key for GLM-5.2 rubric judging.
- `QWEN_API_KEY`, if a separate embedding key is used. Otherwise the workflow
  maps the existing workspace `GLM_API_KEY` to `QWEN_API_KEY` for the **same
  already-configured Model Studio workspace endpoint**. A bilingual embedding
  probe must return the requested 1024-dimensional model before corpus download.
  A denied/unavailable model stops the job; it does not trigger model fallback.

The endpoint is the existing `ZVEC_GREP_EMBEDDING_ENDPOINT` in
`benchmarks/swe-qa-bench/zg_bench/settings.py`. Keys travel to Docker by environment
variable name, never as command-line values. Baseline receives no embedding key.
Artifacts are scanned for all configured secret values before upload.

## Measurements and judgment

Each planned run retains judge score, inclusive native Qoder input tokens,
attempted tool calls (plus zg calls), agent wall seconds, outcome and raw trace.
Missing measurements remain null and failed/planned runs stay in the denominator.
Cached input is already included in Qoder's inclusive counter and is not added
again. Remote embedding and judge usage are not added to Qoder input tokens.
Wall time includes Qoder startup and, for with-zg, the MCP bridge's in-process
integrity work. Host-side index preparation and post-trial verification are
outside the agent interval; this harness overhead limits latency attribution.

GLM-5.2 scores all unchanged source rubrics against original source files and one
candidate report, blind to arm and cost metrics. This is a **custom rubric judge
adapter**, not the official ClaudeCode filesystem judge. Source-rubric grounding
defects are retained and disclosed; scores are descriptive, not a statistical
non-inferiority claim. No automatic best-of selection or retry of low scores.

Per-task artifacts contain `runs/trial-results.json`, `runs/judgements.json`,
native JSONL/trajectory, candidate files and setup provenance. The final artifact
contains `summary.json`, `summary.md`, `rows.json`, `rows.csv` and `rows.md`.
Results are paired by task/repetition, averaged within task and then equally
across tasks, with code QA and other QA reported separately. Incomplete runs
produce a partial report and a failing completeness check rather than a claimed
efficacy result.

## Offline validation

```bash
python3.12 -m unittest discover -s benchmarks/workspace-qa/tests -v
```

Actual dataset preparation and model evaluations run through GitHub Actions.
No measured results have been committed at initial implementation time.
