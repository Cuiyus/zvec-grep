# Claude Code paired QA pilot

This is an exploratory end-to-end QA runner, not a reproduction of the published
SWE-QA numbers. It runs the same frozen question and source with Claude Code in
fresh Docker containers. Baseline exposes Read/Grep/Glob; treatment adds the
prepared zg search MCP tool. No answer, rubric, issue resolution, or judge files
are mounted. The source is read-only and its content manifest is checked after
every trial. Existing indexes are copied before each treatment; index preparation
is outside the agent wall-time measurement.

The original [q004 architecture pair](results/20261019/README.md#新架构题-q004)
scored 7/10 for baseline and 9/10 for zg, but neither passed its frozen
source-grounded quality endpoint. zg used 29.91% more input while its agent wall
fell 30.32%; including grant, indexing and preflight removes that time advantage.
This pair does not meet the frozen efficiency signal rule.

The subsequent [shared answer-verification pair](results/20261019/README.md#共同答案核验提示的单次验证)
scored 9/10 for both arms, with both still failing the unchanged C4 endpoint.
zg input increased 55.57% and agent wall increased 21.88%; three actual QA
searches succeeded at the correct root. Neither frozen signal appeared, so this
q004 instruction branch has stopped. All 22 trials remain in the report; a
stable favorable benchmark set has not been established.

The image pins Claude Code 2.1.212 and the Rust zg binary built from upstream
main `a424e0ce06868dc9ad8f142f57de59b8f9b58699` (package version 0.0.1).
The native package, binary, and local potion-code model files are hash-verified.
The runner records the image ID and actual versions. This pilot uses a fresh HOME, empty setting sources,
disabled CLAUDE.md/auto-memory, restricted tools, turn/output/wall limits, and wall time that includes container
launch and MCP startup. `--effort high` is recorded; equivalence to another
agent's provider-specific reasoning and temperature settings is not assumed.
Third-party `total_cost_usd` is retained as unverified, not used as a budget or
efficiency metric. There is no model fallback. A different resolved model or
missing native tools invalidates the protocol and stops further spending.
Task failures such as timeout or exhausted turns remain in the plan and do not
prevent the other arm from running. `--bare` is intentionally absent: in this
Claude version it suppresses Grep and Glob. The offline `probe_cli.py` verifies
the actual native tool exposure, connected zg MCP and provider request settings
before any paid task call. See `experiment.json` for the frozen two-route plan.

Preparation and trials run as the host UID/GID so private index/config modes
remain usable under dropped container capabilities. After corpus indexing, an
additional MCP preflight checks initialize, tools/list and a fixed search using
the same read-only container restrictions and separate copies of the real
index/home seeds. Its work is setup validation, excluded from QA metrics.

Prepare an exported source tree with `git archive`, without `.git`. Build its
index at **`/workspace` inside this same image**, preserving that absolute path
when running. Keep an empty `.zvec-grep` directory in the source export for the
overlay mount. The `--index-seed` argument points directly at the prepared
workspace's `.zvec-grep` directory. Optional `--home-seed` points directly at
prepared `~/.zvec-grep`, including matching authorization state and model config.
Do not persist provider credentials in either seed; pass them through environment.
The default embedding is main's `local/potion-code-16m-v2`; no embedding API key
is required. The pinned model cache is baked into the image and verified with
network disabled and a read-only filesystem before paid trials. The CI wrapper
fetches main's exact index ignore rules and records source hashes and setup time.

```sh
gh run download 39221698769 --repo zvec-ai/zvec-grep \
  --name rust-native-linux-x64-gnu --dir /absolute/rust-native
python3 benchmarks/claude-qa-pilot/prepare_rust_runtime.py \
  --artifact-dir /absolute/rust-native \
  --runtime-dir benchmarks/claude-qa-pilot/runtime
docker build -t zg-claude-qa-pilot:20261019 benchmarks/claude-qa-pilot
export ANTHROPIC_API_KEY="$EXISTING_PROVIDER_SECRET"
export ANTHROPIC_BASE_URL="$VERIFIED_ANTHROPIC_ENDPOINT"
unset ANTHROPIC_AUTH_TOKEN

python3 benchmarks/claude-qa-pilot/run.py \
  --source /absolute/frozen-source \
  --prompt /absolute/question-only.txt \
  --index-seed /absolute/prepared-workspace/.zvec-grep \
  --home-seed /absolute/prepared-home/.zvec-grep \
  --model qwen3.8-max \
  --output /absolute/new-pilot-output \
  --repeats 1 --max-turns 30 --timeout 900 --max-output-tokens 8192
```

Use `--dry-run` to validate and print the frozen manifest without a model call.
It still inspects the image and executes `claude --version` and `zg --version`.
`--mcp-config` can supply the exact JSON produced by `zg install`, containing
only the stdio `mcpServers.zvec_grep` entry, without embedded credentials. The
default command is `zg --server --stdio --mcp-toolset agent` for current-main Rust.
The agent toolset exposes only search. The same native tools remain available;
native zg MCP instructions are preserved, without extra benchmark-specific
tool forcing.

The default development plan is exactly one baseline/zg pair for each route.
There is no automatic repeat or suite expansion. The separate, explicitly
selected completion confirmation phase below permits two further pairs on the
same zvec task; all other CI modes enforce one pair.
All planned trials remain in the manifest. Infrastructure failures and incomplete
answers stay visible; they are never treated as efficient answers. A single pair
is descriptive and cannot establish a general improvement.

The separate `root-v2` development plan in `experiment-root-v2.json` tests whether
explicit workspace guidance prevents repository-root argument mistakes. Its only
protocol change appends the same instruction to both arms: "The repository root
is exactly /workspace. Use /workspace for every repository-scoped root argument."
It runs only one `reflex-6` pair with the original question, source and rubric;
V1 evidence remains separate and will not be replaced. The default remains V1
(`workspace_root_guidance=false`, both tasks). To dispatch only the V2 case:

```sh
gh workflow run claude-qa-pilot.yml --repo Cuiyus/zvec-grep \
  --ref dev/claude-zvec-qa-pilot \
  -f task=reflex-6 -f workspace_root_guidance=true
```

Outputs include the manifest, source hashes, prompts, per-trial stream JSON,
answer, metrics, and a known-credential scan. The runtime home and writable index
copies live outside the artifact directory and are deleted after each trial.
Only upload the explicit output directory after `secret-scan.json` reports
`passed: true`; never upload preparation seeds. Input tokens include ordinary
input, cache reads, and cache writes. Final session usage takes precedence over
deduplicated assistant message usage. `usage_complete` requires final session
usage with all four input/output/cache token fields present and finite nonnegative
numbers; fallback usage is incomplete. `efficiency_comparable` additionally
requires a successful final answer and a valid trial after timeout/exit checks;
it does not establish answer quality. Tool calls are counted by unique IDs.
`zg_used` means attempted use; the separate `zg_tool_attempts`,
`zg_tool_successes`, `zg_tool_errors`, and `zg_tool_unresolved` fields distinguish
actual successful retrieval from an error or a call with no observed result.
Fewer tokens after a failed zg call do not establish retrieval benefit.

Quality is deliberately not inferred from completion. Judge the saved answers
separately using a source-verified rubric outside the agent workspace, with
profile labels hidden. Report correctness and evidence before efficiency.

The independent `experiment-architecture-v1.json` plan is frozen for exactly one
`zvec-q004` baseline/zg pair from a closed zvec issue about optimization-thread
configuration. It uses the same executable Rust, Claude/Qwen, remote Qwen
embedding, authorization, preflight and completion-budget settings as the remote
q001 phase. The task and its source corpus change. The new plan has its own
source-grounded quality endpoint; factual points, citation support and resource
costs stay separate. It cannot establish a stable suite benefit from one pair.
The workflow rejects this phase until the plan is frozen and its question,
rubric, dataset, protocol and retrieval identities match. The explicit inputs
are `task=zvec-q004`, `architecture_pilot=true`, `completion_budget=true`, with
all other phase flags false. There is no automatic repeat or outcome replacement.
The q001-only remote phase and every older frozen phase retain their identities.

This pair completed in [run 39297180874](https://github.com/Cuiyus/zvec-grep/actions/runs/39297180874)
at `b986001e7bf658eaa0598b053d94f525e367a0cb`, with complete terminal usage and a
successful [execution audit](results/20261019/architecture-v1/independent-audit.json).
The [blind review](results/20261019/architecture-v1/blinded-review.json) scored
baseline 7/10: C3 denies the target-builder pool handoff (critical contradiction
D3), and C3/C4 citation gates fail. zg scored 9/10 but fails C4: hardware >=4 does
not rule out an explicitly configured builder count of 1. The correct max/min
formula does not support the added builder>=2 assertion. Neither answer passes;
the rubric and older phase rules are unchanged.

Baseline made 25 tool attempts: 24 admitted, two returning file-not-found errors,
and a final budget-denied attempt. This is valid retained agent behavior, not an
infrastructure failure. zg made 21 admitted attempts with two successful MCP
calls using `/workspace` (four query strings). Agent wall was 410.16/285.79 s,
but zg grant/index/preflight add 201.741 s, bringing setup plus agent to 487.53 s.
This is not a cold setup-inclusive speedup and does not include installation or
image-build time. See the [pair verdict](results/20261019/architecture-v1/pair-verdict.json),
[trajectory audit](results/20261019/architecture-v1/trajectory-audit.json), and
[review input verification](results/20261019/architecture-v1/review-input-verification.json).

```sh
gh workflow run claude-qa-pilot.yml --repo Cuiyus/zvec-grep \
  --ref dev/claude-zvec-qa-pilot -f task=zvec-q004 \
  -f workspace_root_guidance=false -f completion_budget=true \
  -f completion_confirmation=false -f closed_issue_expansion=false \
  -f remote_embedding_pilot=false -f architecture_pilot=true
```

The separate `experiment-completion-v2.json` plan calibrates completion on the
same zvec-q001 task after both V1 arms obtained core evidence but failed to
deliver an answer. Both arms receive identical generic budget instructions and
a native PreToolUse hook: admit at most 24 unique repository tool calls, and
deny new retrieval after 600 elapsed seconds. All tools share the cap; atomic
locking covers parallel calls. The hook never grants a permission exception.
The original 30-turn/900-second total limits remain, and hook overhead counts in
wall time. Denials request a final answer from existing evidence, but cannot
guarantee that the model stops asking for tools. In-flight work is not cancelled
at 600 seconds. This is a development protocol with disclosed post-observation
budget selection, not a reconstructed second session or a blind confirmation.
V1 failures remain in the report. A zero-inference fixture verifies actual
Claude hook admission/denial and a subsequent final answer before paid trials;
per-trial `tool-budget.json` audits hook IDs against the actual stream.

```sh
gh workflow run claude-qa-pilot.yml --repo Cuiyus/zvec-grep \
  --ref dev/claude-zvec-qa-pilot -f task=zvec-q001 \
  -f workspace_root_guidance=false -f completion_budget=true
```

`experiment-completion-confirmation.json` freezes two further same-case pairs
after the completion-v2 development signal. It keeps all agent, source, tool,
hook, and per-trial settings unchanged, and runs pair 2 in zg/baseline order,
then pair 3 in baseline/zg order. The selected development pair is excluded from
confirmation estimates. Compare each pair's frozen source-based quality before
complete token usage; retain failures and quality/resource tradeoffs. These are
replications of an already selected task, not evidence of held-out generalization.

```sh
gh workflow run claude-qa-pilot.yml --repo Cuiyus/zvec-grep \
  --ref dev/claude-zvec-qa-pilot -f task=zvec-q001 \
  -f workspace_root_guidance=false -f completion_budget=true \
  -f completion_confirmation=true
```

This mode alone permits `--repeats 2` in `ci_run.py`, passing `--start-repeat 2`
to the runner. Its 90-minute job and 4500-second outer runner timeout accommodate
four unchanged 900-second trials. Setup records the confirmation plan hash;
manifest `planned_trials` and the actual loop use the same schedule function.
The artifact audit verifies the summary's execution order and strictly increasing
per-trial hook start times, rather than inferring order from directory names.

The separately prepared `experiment-issue-expansion-v1.json` references q002 and
q003 in `benchmarks/zvec-issue-qa/dataset-expansion-v1.json`. It is now frozen after
the [q001 confirmation decision](issue-expansion-decision-v1.json): the earlier
efficiency signal did not repeat, so this batch explores task dependence rather
than assuming established benefit. Its `task=zvec-expansion` dispatch must set
`closed_issue_expansion=true` and `completion_budget=true`, with both other phase
flags false. The selector emits both tasks, each with one unchanged completion-v2
pair. The wrapper rejects an unfrozen plan or a dataset hash mismatch before
setup, and saves both plan and dataset in the task artifact. This phase neither
changes nor pools with q001 confirmation or the selected development results.

`experiment-remote-embedding-v1.json` freezes a separate q001
development pair to test the Qwen embedding configuration used in main's
historical Claude + Opus protocol. Its sole solver factor is the embedding
stack: `qwen/qwen3.7-text-embedding` replaces local Potion; Claude/Qwen3.8-max,
the question/source/rubric, native Rust binary, common instructions, MCP schema,
completion hook and limits remain unchanged. This is one fresh baseline/remote-zg
pair, not a contemporaneous local/remote ablation. It cannot establish an
embedding-specific causal gain or generalization, and unfavorable results remain.

The phase requires `task=zvec-q001`, `completion_budget=true`,
`remote_embedding_pilot=true`, and all other phase flags false. Before the
provider probe or setup, its guard requires an explicitly frozen plan, unchanged
question/rubric/dataset/hook/base-plan hashes, the exact endpoint, and the
configured embedding credential. The evidence-based freeze decision is retained
in `remote-embedding-decision-v1.json`, together with the independently reviewed
preparation snapshot. An unfrozen or changed plan is rejected before paid calls.
Offline preparation does not establish working remote quota or model access.

The fork's `QWEN_EMBEDDING_API_KEY` secret is mapped to `ZVEC_GREP_API_KEY`, and
its `QWEN_EMBEDDING_ENDPOINT` variable must equal the endpoint frozen in the plan;
there is no silent catalog-endpoint fallback. Docker receives credential names,
never values in command arguments or saved configuration. Baseline receives
neither embedding environment nor index/authorization seed. The remote setup
grants workspace authorization bound to `/workspace`, model and endpoint, then
indexes using that persistent grant. Index model/endpoint/dimensions are checked,
and private seeds are scanned before use; seeds and signing keys are never
uploaded. The real MCP preflight runs the existing fixed hybrid query plus one
vector-only query with the same generic text, requiring a vector-matched source
result. It uses private seed clones that are discarded before solver trials.

Authorization, index setup, whole preflight and both preflight searches have
separate timing records. Preflight may incur embedding charges; its outer timeout
is 600 seconds to cover the extra native request. All agent-time query network
waits remain inside zg wall time and the investigation budget. Search wall
includes native computation and network time; it is not a measurement of HTTP
latency alone. Remote billing and immutable provider weight identity are not
attested. The requested model, stored index identity, exact endpoint and pinned
main configuration/catalog/backend hashes are recorded instead. Existing local
phases keep their original commands and behavior.

The frozen remote pair has completed real indexing, hybrid/vector preflight and
QA. Its [audited result](results/20261019/README.md#远端-qwen-embedding-单题验证)
shows a passing core source gate for zg, but lower factual points and greater
input/time. The frozen joint signal rule failed. The plan remains an immutable
pre-run record; `remote-embedding-v1/live-validation.json` in the result directory
records the subsequent live validation.

Run parser and source-isolation checks with:

```sh
python3 -m unittest discover -s benchmarks/claude-qa-pilot -p 'test_*.py'
```

After downloading the task artifact, independently audit its recorded stream,
final answer, complete usage, source inventory, and hook decisions without
changing the evidence or invoking a model:

```sh
python3 benchmarks/claude-qa-pilot/audit_artifacts.py \
  --artifact-dir /absolute/download/qa-zvec-q001 \
  --expected-limit 24 --expected-research-seconds 600 \
  --expected-hook-sha256 8a3a39a1a45e766bf15ba258fa4a0d2f36c63c70482258c6df10e841b99af761 \
  > /absolute/download/completion-audit.json
```

Budget denials are error tool results in the raw stream and therefore appear
in the runner's aggregate error counts. The independent audit joins unique
tool IDs to the hook decisions and separates budget denials, admitted execution
errors, successful returns, and unresolved calls. An admitted successful return
does not establish useful evidence or answer quality. Omit the three expected
budget arguments when inspecting a protocol without a hook; admission-based
counts then remain unavailable. This script was added after completion-v2 was
dispatched and does not change that run's agent protocol.

## Bounded final-answer verification development

The separately named [answer-verification plan](experiment-answer-verification-v1.json)
is frozen for exactly one additional q004 baseline/zg pair after 101 offline
checks and an independent execution review. No new solver outcomes existed at
freeze. It adds the same [generic final consistency instruction](answer-verification-instructions.txt)
to both arms; the question, source, rubric, current-main Rust, native MCP schema,
Claude/Qwen settings and completion budget remain unchanged. No additional model
invocation or retrieval phase is introduced by the harness. Actual solver calls
may still change in response to the instruction.

The retrospective [diagnosis](provenance/answer-verification-diagnostic-20261019.json)
found exact counterevidence in successful tool results before unsupported final
claims in q004 and q002. Existing instructions already require citations and
uncertainty. The [independent design review](provenance/answer-verification-design-review-20261019.json)
permits testing only the small explicit pre-submit consistency step, with no
expectation of improvement. The experiment does not establish whether internal
self-checking occurred or isolate prompt effects from run-to-run variation.

The unchanged source-grounded quality endpoint stays primary. A candidate quality
signal requires zg to pass while baseline fails, with non-lower factual points,
a valid complete pair and successful correct-root retrieval. If both pass,
quality is tied; efficiency still requires lower complete input including cache
and the existing joint conditions. Wall and setup remain separate. If neither
signal appears, this q004 instruction branch stops: no further wording revisions,
repeated runs to obtain a pass, or outcome-based replacement with an easier task.
All earlier answers, failures, rules and resource comparisons remain available.

This one pair completed in [run 39302184475](https://github.com/Cuiyus/zvec-grep/actions/runs/39302184475)
at commit `fe54360c`. The [independent execution audit](results/20261019/answer-verification-v1/independent-audit.json)
verified both actual command prompts, complete terminal usage and successful QA
retrieval. The [fresh blind review](results/20261019/answer-verification-v1/blinded-review.json)
scored both answers 9/10: both gave the correct PQ count formula but then asserted
exact four/two workers using an unstated default-builder assumption. C4 factual
and citation gates therefore failed. The [frozen verdict](results/20261019/answer-verification-v1/pair-verdict.json)
sets both signal flags to false and `stop_this_q004_shared_instruction_branch=true`.
No further runs or wording changes are scheduled for this branch. The command
below records the historical invocation; it is not an instruction to repeat it.

```sh
gh workflow run claude-qa-pilot.yml --repo Cuiyus/zvec-grep \
  --ref dev/claude-zvec-qa-pilot -f task=zvec-q004 \
  -f workspace_root_guidance=false -f completion_budget=true \
  -f completion_confirmation=false -f closed_issue_expansion=false \
  -f remote_embedding_pilot=false -f architecture_pilot=false \
  -f answer_verification_pilot=true
```
