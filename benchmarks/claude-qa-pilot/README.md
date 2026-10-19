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

Outputs include the manifest, source hashes, prompts, per-trial stream JSON,
answer, metrics, and a known-credential scan. The runtime home and writable index
copies live outside the artifact directory and are deleted after each trial.
Only upload the explicit output directory after `secret-scan.json` reports
`passed: true`; never upload preparation seeds. Input tokens include ordinary
input, cache reads, and cache writes. Final session usage takes precedence over
deduplicated assistant message usage. Tool calls are counted by unique IDs.
`zg_used` means attempted use; the separate `zg_tool_attempts`,
`zg_tool_successes`, `zg_tool_errors`, and `zg_tool_unresolved` fields distinguish
actual successful retrieval from an error or a call with no observed result.
Fewer tokens after a failed zg call do not establish retrieval benefit.

Quality is deliberately not inferred from completion. Judge the saved answers
separately using a source-verified rubric outside the agent workspace, with
profile labels hidden. Report correctness and evidence before efficiency.

Run parser and source-isolation checks with:

```sh
python3 -m unittest discover -s benchmarks/claude-qa-pilot -p 'test_*.py'
```
