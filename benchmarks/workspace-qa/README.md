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
runs on this branch with five explicit scopes:

- An ordinary push affecting this experiment runs offline validation only.
- `[workspace-qa-probe]` validates and checks the actual remote embedding,
  released SDK and Qoder MCP chain on one synthetic file. It prepares no full
  workspace and runs no benchmark trials or judge; use it for integration fixes.
  The [first verified probe](data/probe-validation.json) completed its model and
  retrieval checks in 26.117 seconds (about 95 seconds for the cold probe job).
- `[workspace-qa-rejudge]` resumes only invalid or missing judgments on the
  pinned smoke artifact, preserving its original QA observations and valid scores.
- A push whose head commit message contains `[workspace-qa-smoke]` validates,
  then runs task 3 once per arm: **two smoke trials**.
- `[workspace-qa-full]` validates, runs smoke, then unlocks the 10-task matrix
  only after complete smoke measurements and rubric judgments. Every formal
  task runs 10 independent repetitions per arm: **200 formal trials**.

Smoke observations are excluded from the formal report. Manual dispatch supports
`validate`, `probe`, `rejudge`, `smoke` (default) and `full` once GitHub exposes the workflow for
dispatch. Concurrency groups separate these scopes so a long formal run does
not queue quick validation behind it. A push must still affect the workflow's
listed experiment paths; an empty commit alone does not trigger it.
Smoke verifies a successful real Qoder vector search using the synthetic setup
probe's native and MCP traces, plus tool registration and integrity in the QA
trial. Natural non-use of zg on the original question is retained as an
observation; it does not cause another rollout. If the QA agent does attempt zg
but every search fails, the smoke still fails. A terminal answer alone cannot
establish that integration works. All rubric judgments and required measurements
must also be complete. Formal trials retain natural non-use without filtering.

For a failed judge after completed QA, `[workspace-qa-rejudge]` (or dispatch
`rejudge`) restores the exact artifact frozen in [judge-recovery.json](data/judge-recovery.json).
Every artifact file is checked against its recorded hash. Only the small original
judge evidence files are downloaded; source extraction, indexing and Qoder
rollouts are not repeated. Explicit judge resume preserves valid scores and all
prior attempts, checks candidate/prompt/model/evidence identity, and uses only the
remaining retry budget. The original validation and judgments are retained along
with new recovery provenance. This recovery artifact is excluded from formal
batch aggregation.

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
Each task validates or builds an immutable remote-model index before its paired
trials; each with-zg run receives an isolated copy. Index preparation is reported
separately from agent time, tokens and tool calls. The runner never loads a
Potion index.

Read-only QA does not require reinstalling dependencies, redownloading source,
or rebuilding an index for each repetition. All repetitions within a task share
one prepared source and seed. The current matrix uses separate jobs per task;
same-persona tasks share compatible preparation caches across jobs, not a live
runner. Fresh sessions and homes isolate answers and execution state. The four
personas have different corpora, so substituting one persona's files for another
would change the frozen tasks.

CI reuses two preparation caches. Verified 16 MiB HTTP blocks are stored under
the frozen archive identity and persona, with a **4 GiB cap per persona**. CRC
and source SHA checks still run on extraction. The large Research workspace only
fits partially in this cache; a hit does not imply zero remaining downloads.
Completed index seeds are keyed by corpus/configuration, released runtime and
build helpers, then revalidated against source and index content hashes and a
new SDK preflight. A changed embedding endpoint/model, index limit or relevant
build code invalidates reuse. No candidate answers, judgments, session state or
trial-modified index copies are cached.

Separate Actions restore/save steps retain downloaded blocks and completed
seeds even if a later evaluation fails. First use still pays setup costs, and
an evicted cache is rebuilt. We do not increase the repository's cache quota;
GitHub's default is 10 GB shared across its caches, so retention depends on other
workflows too. [GitHub cache limits](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#usage-limits-and-eviction-policy)
`dataset/range-cache-metrics.json` records hit/download bytes even on preparation
failure; `runs/manifest.json` and `runs/preparation/runtime/preparation.json`
record index reuse/build provenance separately from QA metrics. Ephemeral Git
snapshots use compression 0 and disable background GC to reduce setup CPU time.

Protocol v2 applies one uniform SDK `maxFileSizeBytes=1048576` (1 MiB) index
limit to every task. Larger files remain available in full to both arms through
Read/Grep; the index skips the whole file and does not truncate it. Other index
selection behavior follows production defaults. Results describe this explicit
index configuration, not unrestricted default indexing. Preparation stderr is
redacted, streamed to Actions live and retained in the runtime artifact.

The first two task-128 setup attempts produced no QA answers: run
[34818892317](https://github.com/Cuiyus/zvec-grep/actions/runs/34818892317)
exposed a missing SDK remote-operation permit (fixed), and run
[34821814894](https://github.com/Cuiyus/zvec-grep/actions/runs/34821814894)
hit the 30-minute index budget at 483/1226 files, with progress slowing at large
LongDA data files. The uniform cap and smaller task-3 smoke were chosen before
any QA output or score was observed. All 10 formal tasks, including 127/128,
remain selected. Failed setup attempts are excluded from formal QA statistics.

Run [34828583811](https://github.com/Cuiyus/zvec-grep/actions/runs/34828583811)
completed both task-3 answers and all judgments, but its only MCP query failed
because Qoder removed the inherited embedding key. The old workflow incorrectly
accepted terminal completion as successful integration. Those two observations
are retained as diagnostics, **not a valid zg efficacy comparison**. Qoder's
supported `${NAME}` MCP environment references now explicitly forward
the three remote embedding variables without serializing their values. The
smoke gate catches zero-success integration even when the model still answers.

Run [34831451904](https://github.com/Cuiyus/zvec-grep/actions/runs/34831451904)
then passed the real setup search and completed both QA answers. The QA agent
naturally made zero zg calls; the first gate incorrectly treated this as an
integration failure. Gate v2 separates the successful integration probe from
natural tool uptake. Independently, one judge response repeated text until its
8192-token limit and was correctly rejected, leaving its score null. Recovery
retains both original answers and the already-valid judgment, then resumes only
the invalid assessment. No QA reroll is used to obtain a zg call.

That run restored 1,950,506,687 bytes of workspace archive blocks with zero new
archive block downloads. Extraction took about 17 seconds, versus 194 seconds
on the prior cold task-3 run; cache archive restore itself took about 18 seconds.
Index rebuilding still took 71 seconds because the bridge fix changed its build
identity. That completed seed was retained even though judging failed.

Required Actions secrets:

- `QODER_PERSONAL_ACCESS_TOKEN`: Qoder native model access.
- `GLM_API_KEY`: existing Model Studio workspace key for GLM-5.2 rubric judging
  in smoke/full. Probe does not require it when `QWEN_API_KEY` is configured.
- `QWEN_API_KEY`, if a separate embedding key is used. Otherwise the workflow
  maps the existing workspace `GLM_API_KEY` to `QWEN_API_KEY` for the **same
  already-configured Model Studio workspace endpoint**. A bilingual embedding
  probe must return the requested 1024-dimensional model before corpus download.
  A second probe uses released zg SDK document indexing and vector retrieval on
  a small synthetic bilingual file. A third probe reuses that fixture through
  the actual Qoder MCP subprocess and requires successful vector retrieval plus
  observable native model usage before any large workspace download. These are
  setup diagnostics, excluded from all benchmark trial counts and metrics.
  A denied/unavailable model stops the job; it does not trigger model fallback.

The endpoint is the existing `ZVEC_GREP_EMBEDDING_ENDPOINT` in
`benchmarks/swe-qa-bench/zg_bench/settings.py`. Keys travel to Docker by environment
variable name, never as command-line values. Baseline receives no embedding key.
The remote-only benchmark path explicitly grants each SDK operation a temporary
permit for the exact Qwen model and endpoint, corresponding to CLI `--allow-remote`.
It does not persist authorization files or enable remote access in the old local protocol.
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
Transient transport failures and invalid/truncated judge responses may use a
fixed total of three attempts with the same model, candidate and prompt. Every
attempt is retained; the first valid assessment is final regardless of score.

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
