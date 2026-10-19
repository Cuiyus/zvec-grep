#!/usr/bin/env python3
"""Small paired QA pilot. No third-party Python dependencies; run on Docker host."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


BUILTIN_TOOLS = {"Read", "Grep", "Glob"}
AUTH_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ZVEC_GREP_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY")
PASS_ENV_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")
EMBEDDING_ENV_NAMES = ("ZVEC_GREP_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "ZVEC_GREP_EMBEDDING_ENDPOINT", "ZVEC_GREP_ENDPOINT")
COMMON_INSTRUCTIONS = (
    "Answer the user's repository question using the frozen source in /workspace. "
    "Use source evidence and cite file paths with line numbers for material claims. "
    "If the available source cannot establish a claim, state the uncertainty. "
    "This is a read-only QA task: do not edit files or execute programs. "
    "Only repository files are relevant; do not read credentials or runtime state."
)
INFRASTRUCTURE_ERRORS = {
    "missing_resolved_model", "model_mismatch", "invalid_usage",
    "unexpected_tools", "missing_init_tool_inventory", "missing_required_tools",
    "unexpected_exposed_tools", "zg_mcp_not_connected",
}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def source_manifest(source: Path) -> dict:
    files = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in {".git", ".zvec-grep"} for part in relative.parts):
            continue
        if path.is_symlink():
            if not path.resolve().is_relative_to(source):
                raise ValueError(f"source contains external symlink: {relative}")
            files[str(relative)] = "symlink:" + os.readlink(path)
        elif path.is_file():
            files[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "file_count": len(files), "files": files}


def parse_stream(text: str, model: str, profile: str, zg_tool: str) -> dict:
    """Use authoritative session usage, deduplicate assistant message/tool events."""
    events, invalid_lines = [], 0
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            invalid_lines += bool(line.strip())
            continue
        if isinstance(value, dict):
            events.append(value)
    result_events = [event for event in events if event.get("type") == "result"]
    result = result_events[-1] if result_events else {}
    messages, calls, models, tool_errors, init = {}, {}, set(), set(), {}
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            init = event
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        if event.get("type") == "assistant":
            actual_model = message.get("model")
            if actual_model and actual_model != "<synthetic>":
                models.add(actual_model)
            message_id = message.get("id", f"event-{len(messages)}")
            old = messages.get(message_id, {})
            new_usage = message.get("usage")
            if isinstance(new_usage, dict):
                messages[message_id] = {
                    key: max(old.get(key, 0), new_usage.get(key, 0) or 0)
                    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                }
        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls[block.get("id", f"anonymous-{len(calls)}")] = block.get("name")
            elif block.get("type") == "tool_result" and block.get("is_error"):
                tool_errors.add(block.get("tool_use_id", f"anonymous-{len(tool_errors)}"))
    model_usage = result.get("modelUsage")
    if isinstance(model_usage, dict):
        models.update(model_usage)
    usage = result.get("usage")
    usage_source = "result.usage"
    if not isinstance(usage, dict) or "input_tokens" not in usage:
        usage_source = "deduplicated_assistant_messages"
        usage = {
            key: sum(value.get(key, 0) for value in messages.values())
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        } if messages else {}
    errors = []
    if not result_events:
        errors.append("missing_final_result")
    if result.get("is_error") or result.get("subtype") not in (None, "success"):
        errors.append("agent_result_error:" + str(result.get("subtype")))
    if not models:
        errors.append("missing_resolved_model")
    elif models != {model}:
        errors.append("model_mismatch")
    if not usage or not isinstance(usage.get("input_tokens"), int):
        errors.append("missing_input_usage")
    elif any(not isinstance(value, (int, float)) or value < 0 for key, value in usage.items() if key in {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"}):
        errors.append("invalid_usage")
    unexpected = sorted({name for name in calls.values() if name not in BUILTIN_TOOLS and not (profile == "zg" and name == zg_tool)})
    if unexpected:
        errors.append("unexpected_tools")
    expected_tools = BUILTIN_TOOLS | ({zg_tool} if profile == "zg" else set())
    raw_tools = init.get("tools")
    exposed_tools = set()
    if not isinstance(raw_tools, list) or not all(isinstance(tool, str) for tool in raw_tools):
        errors.append("missing_init_tool_inventory")
    else:
        exposed_tools = set(raw_tools)
    missing_tools = sorted(expected_tools - exposed_tools)
    unexpected_exposed_tools = sorted(exposed_tools - expected_tools)
    if missing_tools:
        errors.append("missing_required_tools")
    if unexpected_exposed_tools:
        errors.append("unexpected_exposed_tools")
    mcp_servers = init.get("mcp_servers", [])
    if profile == "zg" and not any(server.get("name") == "zvec_grep" and server.get("status") == "connected" for server in mcp_servers if isinstance(server, dict)):
        errors.append("zg_mcp_not_connected")
    token_keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    input_total = sum(usage.get(key, 0) or 0 for key in token_keys) if usage and "invalid_usage" not in errors else None
    if input_total is not None and input_total <= 0:
        errors.append("empty_input_usage")
    answer = result.get("result", "")
    if not isinstance(answer, str) or not answer.strip():
        errors.append("missing_final_answer")
    infrastructure_errors = [error for error in errors if error in INFRASTRUCTURE_ERRORS]
    if not models:
        infrastructure_errors.extend(error for error in errors if error in {"missing_input_usage", "empty_input_usage"})
    return {
        "errors": errors, "requested_model": model, "resolved_models": sorted(models),
        "infrastructure_errors": infrastructure_errors,
        "protocol_valid": not infrastructure_errors,
        "usage_source": usage_source, "usage": usage, "input_tokens_including_cache": input_total,
        "output_tokens": usage.get("output_tokens"), "tool_calls": len(calls),
        "tool_counts": {name: list(calls.values()).count(name) for name in sorted(set(calls.values()))},
        "tool_error_count": len(tool_errors), "zg_used": zg_tool in calls.values(),
        "unexpected_tools": unexpected, "mcp_servers": mcp_servers,
        "expected_tools": sorted(expected_tools), "exposed_tools": sorted(exposed_tools),
        "missing_required_tools": missing_tools, "unexpected_exposed_tools": unexpected_exposed_tools,
        "reported_cost_usd": result.get("total_cost_usd"),
        "cost_is_verified": False, "answer": answer,
        "result_subtype": result.get("subtype"), "non_json_stdout_lines": invalid_lines,
    }


def redact(text: str) -> str:
    for name in AUTH_NAMES:
        value = os.environ.get(name)
        if value and len(value) >= 8:
            text = text.replace(value, "[REDACTED]")
    return text


def copy_seed(source: Path, dest: Path) -> None:
    # A private writable runtime copy prevents previous trials sharing cache/locks.
    shutil.copytree(source, dest, dirs_exist_ok=True, symlinks=False,
                    ignore=shutil.ignore_patterns("*.sock", "*.pid", "*.lock"))


def cleanup_runtime(runtime_dir: Path, image: str) -> None:
    try:
        shutil.rmtree(runtime_dir)
    except PermissionError:
        # Docker may have made root-owned private session files on a non-root CI host.
        subprocess.run(["docker", "run", "--rm", "--mount",
                        f"type=bind,src={runtime_dir},dst=/cleanup", image,
                        "sh", "-c", "find /cleanup -mindepth 1 -delete"],
                       capture_output=True, check=True, timeout=60)
        runtime_dir.rmdir()


def docker_base(args: argparse.Namespace, name: str, trial_dir: Path, runtime_dir: Path) -> list[str]:
    return [
        "docker", "run", "--rm", "--name", name, "--init", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp:rw,exec,size=2g", "--tmpfs", "/run:rw,size=64m",
        "--mount", f"type=bind,src={args.source},dst=/workspace,readonly",
        "--mount", f"type=bind,src={runtime_dir / 'home'},dst=/root",
        "--mount", f"type=bind,src={trial_dir / 'config'},dst=/pilot-config,readonly",
        "--workdir", "/workspace", "--env", "HOME=/root",
        "--env", "CLAUDE_CONFIG_DIR=/root/.claude",
        "--env", "ZVEC_GREP_HOME=/root/.zvec-grep",
    ]


def run_trial(args: argparse.Namespace, profile: str, repeat: int, prompt: str) -> dict:
    trial_dir = args.output / f"r{repeat:02d}-{profile}"
    trial_dir.mkdir()
    # Never put credential-bearing session/config/index runtime in uploadable output.
    runtime_dir = Path(tempfile.mkdtemp(prefix="zg-qa-runtime-"))
    (runtime_dir / "home").mkdir()
    (trial_dir / "config").mkdir()
    if profile == "zg" and args.home_seed:
        copy_seed(args.home_seed, runtime_dir / "home" / ".zvec-grep")
    mcp = {"mcpServers": {}}
    if profile == "zg":
        copy_seed(args.index_seed, runtime_dir / "index")
        mcp["mcpServers"]["zvec_grep"] = args.mcp_server
    write_json(trial_dir / "config" / "mcp.json", mcp)
    # Explicit settings avoid implicit user, repository, plugin and hook state.
    write_json(trial_dir / "config" / "settings.json", {"permissions": {"deny": [
        "Read(//proc/**)", "Read(//root/**)", "Read(//pilot-config/**)",
        "Read(//workspace/.zvec-grep/**)", "Grep(//root/**)",
    ]}})
    name = "zg-qa-" + uuid.uuid4().hex[:12]
    command = docker_base(args, name, trial_dir, runtime_dir)
    if profile == "zg":
        command += ["--mount", f"type=bind,src={runtime_dir / 'index'},dst=/workspace/.zvec-grep"]
    else:
        command += ["--tmpfs", "/workspace/.zvec-grep:ro,size=64k"]
    for name_env in (*PASS_ENV_NAMES, *(EMBEDDING_ENV_NAMES if profile == "zg" else ())):
        if os.environ.get(name_env):
            command += ["--env", name_env]
    for variable, value in {
        "ANTHROPIC_MODEL": args.model, "ANTHROPIC_DEFAULT_SONNET_MODEL": args.model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": args.model, "ANTHROPIC_DEFAULT_HAIKU_MODEL": args.model,
        "CLAUDE_CODE_SUBAGENT_MODEL": args.model, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(args.max_output_tokens),
        "IS_SANDBOX": "1",
    }.items():
        command += ["--env", f"{variable}={value}"]
    allowed = ",".join(sorted(BUILTIN_TOOLS))
    if profile == "zg":
        allowed += "," + args.zg_tool
    command += [args.image, args.claude, "--print", "--verbose",
        "--output-format", "stream-json", "--model", args.model,
        "--max-turns", str(args.max_turns), "--effort", args.effort,
        "--permission-mode", "dontAsk", "--tools", ",".join(sorted(BUILTIN_TOOLS)),
        "--allowedTools", allowed, "--disable-slash-commands", "--no-session-persistence",
        "--setting-sources", "", "--settings", "/pilot-config/settings.json",
        "--strict-mcp-config", "--mcp-config", "/pilot-config/mcp.json",
        "--append-system-prompt", COMMON_INSTRUCTIONS, "--", prompt]
    write_json(trial_dir / "command.json", command)
    start = time.monotonic()
    timed_out = False
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = proc.communicate(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
        stdout, stderr = proc.communicate(timeout=30)
    wall = time.monotonic() - start
    cleanup_runtime(runtime_dir, args.image)
    stdout, stderr = redact(stdout), redact(stderr)
    (trial_dir / "stream.jsonl").write_text(stdout)
    (trial_dir / "stderr.txt").write_text(stderr)
    metrics = parse_stream(stdout, args.model, profile, args.zg_tool)
    if timed_out:
        metrics["errors"].append("wall_timeout")
    if proc.returncode:
        metrics["errors"].append(f"process_exit:{proc.returncode}")
    metrics.update({"profile": profile, "repeat": repeat, "wall_seconds": wall,
                    "wall_time_scope": "docker_start_through_agent_exit", "exit_code": proc.returncode})
    metrics["valid"] = not metrics["errors"]
    (trial_dir / "answer.txt").write_text(metrics.pop("answer"))
    write_json(trial_dir / "metrics.json", metrics)
    return metrics


def scan_output_for_secrets(output: Path) -> dict:
    matches = []
    secrets = [value.encode() for name in AUTH_NAMES if (value := os.environ.get(name)) and len(value) >= 8]
    for path in output.rglob("*"):
        if path.is_file() and any(secret in path.read_bytes() for secret in secrets):
            matches.append(str(path.relative_to(output)))
    return {"passed": not matches, "matched_paths": matches, "credential_values_checked": len(secrets)}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, type=Path, help="Frozen git-archive tree without .git")
    p.add_argument("--prompt", required=True, type=Path, help="Question-only text; never reference answer")
    p.add_argument("--output", required=True, type=Path, help="New output directory outside source")
    p.add_argument("--image", default="localhost/zg-claude-qa-pilot:20261019")
    p.add_argument("--model", required=True, help="Exact requested and expected response model; no aliases/fallback")
    p.add_argument("--claude", default="claude")
    p.add_argument("--zg", default="zg")
    p.add_argument("--index-seed", required=True, type=Path, help="Prepared .zvec-grep directory for /workspace")
    p.add_argument("--home-seed", type=Path, help="Prepared ~/.zvec-grep runtime/config/model-cache directory")
    p.add_argument("--mcp-args-json", default='["server","--stdio","--mcp-toolset","agent"]')
    p.add_argument("--mcp-config", type=Path, help="Optional config generated by zg install; exactly one stdio zvec_grep entry")
    p.add_argument("--zg-tool", default="mcp__zvec_grep__zvec_grep_search")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--max-turns", type=int, default=30)
    p.add_argument("--max-output-tokens", type=int, default=4096)
    p.add_argument("--effort", choices=("low", "medium", "high"), default="high")
    p.add_argument("--dry-run", action="store_true", help="Validate inputs and print manifest; no model calls")
    return p


def main() -> int:
    args = parser().parse_args()
    for name in ("source", "prompt", "output", "index_seed", "home_seed", "mcp_config"):
        value = getattr(args, name)
        if value:
            setattr(args, name, value.expanduser().resolve())
    if not args.source.is_dir() or (args.source / ".git").exists():
        raise ValueError("--source must be an exported tree without .git; use git archive")
    if args.output.exists() or args.output.is_relative_to(args.source):
        raise ValueError("--output must be new and outside the frozen source")
    if not args.index_seed.is_dir() or (args.home_seed and not args.home_seed.is_dir()):
        raise ValueError("index and home seeds must exist")
    if min(args.repeats, args.timeout, args.max_turns, args.max_output_tokens) < 1:
        raise ValueError("limits and repeats must be positive")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ValueError("export ANTHROPIC_API_KEY for the isolated provider session")
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise ValueError("unset ANTHROPIC_AUTH_TOKEN; use only ANTHROPIC_API_KEY to avoid mixed auth")
    if not os.environ.get("ANTHROPIC_BASE_URL"):
        raise ValueError("export the verified ANTHROPIC_BASE_URL for the selected model")
    args.mcp_args = json.loads(args.mcp_args_json)
    if not isinstance(args.mcp_args, list) or not all(isinstance(v, str) for v in args.mcp_args):
        raise ValueError("--mcp-args-json must be a JSON array of strings")
    args.mcp_server = {"type": "stdio", "command": args.zg, "args": args.mcp_args}
    if args.mcp_config:
        servers = json.loads(args.mcp_config.read_text()).get("mcpServers", {})
        if set(servers) != {"zvec_grep"}:
            raise ValueError("--mcp-config must contain exactly one zvec_grep server")
        args.mcp_server = servers["zvec_grep"]
        if args.mcp_server.get("type", "stdio") != "stdio" or "env" in args.mcp_server:
            raise ValueError("MCP template must use stdio and inherit environment, not embed credentials")
        if not isinstance(args.mcp_server.get("command"), str) or not isinstance(args.mcp_server.get("args"), list):
            raise ValueError("MCP template needs command and args")
    prompt = args.prompt.read_text()
    frozen = source_manifest(args.source)
    image_result = subprocess.run(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True, capture_output=True, check=True)
    versions = {}
    for executable in (args.claude, args.zg):
        checked = subprocess.run(["docker", "run", "--rm", args.image, executable, "--version"], text=True, capture_output=True, check=True, timeout=60)
        versions[executable] = checked.stdout.strip()
    manifest = {
        "schema_version": 1, "purpose": "exploratory paired read-only QA pilot",
        "image": args.image, "image_id": image_result.stdout.strip(),
        "versions": versions,
        "requested_model": args.model, "source": str(args.source),
        "source_sha256": frozen["sha256"], "source_file_count": frozen["file_count"],
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "common_instructions": COMMON_INSTRUCTIONS, "zg_tool": args.zg_tool,
        "mcp_server": args.mcp_server, "repeats": args.repeats,
        "limits": {"wall_seconds": args.timeout, "max_turns": args.max_turns, "max_output_tokens": args.max_output_tokens, "effort": args.effort, "disable_adaptive_thinking": False},
        "isolation": {"bare": False, "disable_claude_mds": True, "disable_auto_memory": True, "setting_sources": [], "disable_slash_commands": True, "strict_mcp_config": True},
        "order": [profile for repeat in range(1, args.repeats + 1) for profile in (("baseline", "zg") if repeat % 2 else ("zg", "baseline"))],
        "index_setup_excluded": True, "quality_judged": False,
        "deviations": ["standalone Docker runner, no Harbor", "Read/Grep/Glob only plus zg search in treatment", "fresh HOME, empty setting sources, disabled repository CLAUDE.md and auto-memory; bare mode is not used because it removes native Grep and Glob", "Claude --effort high is not assumed equivalent to OpenCode provider-specific high/xhigh or temperature settings", "wall includes container launch and MCP startup", "turn/wall/output limits instead of unverifiable third-party USD budget", "all planned trials retained; no trimming or task filtering"],
    }
    if args.dry_run:
        print(json.dumps(manifest, indent=2))
        return 0
    args.output.mkdir(parents=True)
    write_json(args.output / "manifest.json", manifest)
    write_json(args.output / "source-manifest.json", frozen)
    shutil.copyfile(args.prompt, args.output / "prompt.txt")
    trials = []
    for repeat in range(1, args.repeats + 1):
        order = ("baseline", "zg") if repeat % 2 else ("zg", "baseline")
        for profile in order:
            print(f"running r{repeat:02d}-{profile}", flush=True)
            metrics = run_trial(args, profile, repeat, prompt)
            trials.append(metrics)
            unchanged = source_manifest(args.source)["sha256"] == frozen["sha256"]
            scan = scan_output_for_secrets(args.output)
            write_json(args.output / "secret-scan.json", scan)
            write_json(args.output / "summary.json", {
                "trials": trials, "source_unchanged": unchanged, "quality_judged": False,
                "secret_scan_passed": scan["passed"], "planned_trial_count": args.repeats * 2,
                "all_planned_trials_completed": len(trials) == args.repeats * 2,
                "invalid_trial_count": sum(not trial["valid"] for trial in trials),
            })
            print(json.dumps(metrics), flush=True)
            # Contract failures stop spending. A model that exhausts its task budget
            # is still a planned outcome; run the other arm rather than suppress it.
            if metrics["infrastructure_errors"] or not unchanged or not scan["passed"]:
                return 1
    return 0 if all(trial["valid"] for trial in trials) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"error: {redact(str(error))}", file=sys.stderr)
        raise SystemExit(2)
