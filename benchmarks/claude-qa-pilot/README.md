# Claude Code paired QA pilot

This is an exploratory end-to-end QA runner, not a reproduction of the published
SWE-QA numbers. It runs the same frozen question and source with Claude Code in
fresh Docker containers. Baseline exposes Read/Grep/Glob; treatment adds the
prepared zg search MCP tool. No answer, rubric, issue resolution, or judge files
are mounted. The source is read-only and its content manifest is checked after
every trial. Existing indexes are copied before each treatment; index preparation
is outside the agent wall-time measurement.

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

The development plan is exactly one baseline/zg pair for each route. There is
no automatic repeat or suite expansion. The generic runner supports alternating
orders for later explicitly planned repetitions, but ci_run.py enforces one pair.
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
