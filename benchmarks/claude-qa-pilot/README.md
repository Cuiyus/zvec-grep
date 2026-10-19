# Claude Code paired QA pilot

This is an exploratory end-to-end QA runner, not a reproduction of the published
SWE-QA numbers. It runs the same frozen question and source with Claude Code in
fresh Docker containers. Baseline exposes Read/Grep/Glob; treatment adds the
prepared zg search MCP tool. No answer, rubric, issue resolution, or judge files
are mounted. The source is read-only and its content manifest is checked after
every trial. Existing indexes are copied before each treatment; index preparation
is outside the agent wall-time measurement.

The image pins Claude Code 2.1.212 and zg 0.2.2. The runner records the image ID
and actual versions. Unlike the Harbor protocol, this pilot uses bare mode,
restricted tools, turn/output/wall limits, and wall time that includes container
launch and MCP startup. `--effort high` is recorded; equivalence to another
agent's provider-specific reasoning and temperature settings is not assumed.
Third-party `total_cost_usd` is retained as unverified, not used as a budget or
efficiency metric. There is no model fallback. A different resolved model or
missing usage invalidates a trial and stops further spending.

Prepare an exported source tree with `git archive`, without `.git`. Build its
index at **`/workspace` inside this same image**, preserving that absolute path
when running. Keep an empty `.zvec-grep` directory in the source export for the
overlay mount. The `--index-seed` argument points directly at the prepared
workspace's `.zvec-grep` directory. Optional `--home-seed` points directly at
prepared `~/.zvec-grep`, including matching authorization state and model config.
Do not persist provider credentials in either seed; pass them through environment.
For Node zg 0.2.2, the embedding endpoint variable is `ZVEC_GREP_ENDPOINT`.

```sh
docker build -t localhost/zg-claude-qa-pilot:20261019 benchmarks/claude-qa-pilot
export ANTHROPIC_API_KEY="$EXISTING_PROVIDER_SECRET"
export ANTHROPIC_BASE_URL="$VERIFIED_ANTHROPIC_ENDPOINT"
unset ANTHROPIC_AUTH_TOKEN
export ZVEC_GREP_API_KEY="$EXISTING_EMBEDDING_SECRET"
export ZVEC_GREP_ENDPOINT="$VERIFIED_EMBEDDING_ENDPOINT"

python3 benchmarks/claude-qa-pilot/run.py \
  --source /absolute/frozen-source \
  --prompt /absolute/question-only.txt \
  --index-seed /absolute/prepared-workspace/.zvec-grep \
  --home-seed /absolute/prepared-home/.zvec-grep \
  --model qwen3.8-max \
  --output /absolute/new-pilot-output \
  --repeats 1 --max-turns 30 --timeout 900 --max-output-tokens 4096
```

Use `--dry-run` to validate and print the frozen manifest without a model call.
It still inspects the image and executes `claude --version` and `zg --version`.
`--mcp-config` can supply the exact JSON produced by `zg install`, containing
only the stdio `mcpServers.zvec_grep` entry, without embedded credentials. The
default command is `zg server --stdio --mcp-toolset agent` for zg 0.2.2; other
versions can override `--mcp-args-json`. The agent toolset exposes only search.
The same native tools remain available and zg use is optional.

For multiple repeats, execution order alternates baseline/zg and zg/baseline.
All planned trials remain in the manifest. Infrastructure failures and incomplete
answers stay visible; they are never treated as efficient answers. Three repeats
are a pilot, not statistical evidence of a general improvement.

Outputs include the manifest, source hashes, prompts, per-trial stream JSON,
answer, metrics, and a known-credential scan. The runtime home and writable index
copies live outside the artifact directory and are deleted after each trial.
Only upload the explicit output directory after `secret-scan.json` reports
`passed: true`; never upload preparation seeds. Input tokens include ordinary
input, cache reads, and cache writes. Final session usage takes precedence over
deduplicated assistant message usage. Tool calls are counted by unique IDs.

Quality is deliberately not inferred from completion. Judge the saved answers
separately using a source-verified rubric outside the agent workspace, with
profile labels hidden. Report correctness and evidence before efficiency.

Run parser and source-isolation checks with:

```sh
python3 -m unittest discover -s benchmarks/claude-qa-pilot -p 'test_*.py'
```
