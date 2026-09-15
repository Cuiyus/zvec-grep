#!/usr/bin/env python3
"""Prepare the released zg install integration, then run an observed native agent.

Each invocation belongs to a new container. Corpus source is mounted read-only;
its .zvec-grep directory is writable and freshly built, not frozen across trials.
The MCP tap changes descriptions only when explicitly testing that prompt factor.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

EMBEDDING = "local/potion-code-16m-v2"
# In the released 0.2.2 package, registerTool(zvec_grep_rg) is guarded by
# toolset === "full". Installer guidance/pre-approval mentions rg, but the
# default agent catalog is search-only; exact lookup remains native Grep.
NATIVE_TOOLS = ("zvec_grep_search",)
VERSIONS = {"zg": "0.2.2", "opencode": "1.18.4", "qodercli": "1.1.45"}
DENIED = ["Bash", "Edit", "Write", "NotebookEdit", "Agent", "Task", "Skill",
          "WebFetch", "WebSearch", "ImageGen", "ImageSearch", "Workflow"]


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def scrub(value, env):
    for name in ("OPENAI_API_KEY", "GLM_API_KEY", "QWEN_API_KEY", "QODER_PERSONAL_ACCESS_TOKEN"):
        if env.get(name):
            value = value.replace(env[name], "[REDACTED]")
    return value


def installed_paths(agent, config_root):
    agent_root = config_root / agent
    return agent_root / ("opencode.json" if agent == "opencode" else "settings.json"), agent_root / "AGENTS.md"


def native_names(agent):
    prefix = "zvec_grep_" if agent == "opencode" else "mcp__zvec_grep__"
    return [prefix + tool for tool in NATIVE_TOOLS]


def validate_spec(spec):
    if spec.get("agent") not in {"opencode", "qodercli"}:
        raise ValueError("agent must be opencode or qodercli")
    if spec.get("arm") not in {"baseline", "zg"}:
        raise ValueError("arm must be baseline or zg")
    variant = spec.get("prompt_variant", "P00")
    if variant not in {"P00", "P10", "P01", "P11"}:
        raise ValueError("Unknown prompt variant")
    if spec["arm"] == "baseline" and (variant != "P00" or spec.get("guidance_override") or spec.get("description_overrides")):
        raise ValueError("Baseline cannot expose zg prompt or tool variants")
    if spec.get("replay_plan") and spec["arm"] != "zg":
        raise ValueError("Native retrieval replay requires the installed zg arm")
    if not isinstance(spec.get("base_config"), dict):
        raise ValueError("base_config must contain the common native agent/model settings")
    if spec["agent"] == "qodercli" and spec.get("model") != "qwen3.8-max":
        raise ValueError("Pinned Qoder combination requires qwen3.8-max")
    if spec["agent"] == "opencode" and spec.get("model") not in {"glm-5.2", "qwen3.8-max"}:
        raise ValueError("Unsupported OpenCode model")
    for key in ("guidance_override", "description_overrides"):
        needed = variant in ({"P10", "P11"} if key == "guidance_override" else {"P01", "P11"})
        if needed != bool(spec.get(key)):
            raise ValueError(f"{key} must match declared prompt factor {variant}")
    descriptions = spec.get("description_overrides", {})
    if not isinstance(descriptions, dict) or any(key not in NATIVE_TOOLS or not isinstance(value, str) or not value.strip()
                                                for key, value in descriptions.items()):
        raise ValueError("Only native search/rg tool description text may be changed")


def merge_config(agent, base, installed, *, zg, guidance_path, wrapped_command):
    """Merge common knobs without replacing installed MCP tools or schemas."""
    config = copy.deepcopy(base)
    if agent == "opencode":
        config.pop("mcp", None)
        if zg:
            if set(installed.get("mcp", {})) != {"zvec_grep"}:
                raise ValueError("Unexpected installed OpenCode MCP server set")
            config["mcp"] = copy.deepcopy(installed["mcp"])
            config["mcp"]["zvec_grep"]["command"] = wrapped_command
            config.setdefault("permission", {})["zvec_grep_*"] = "allow"
            # The custom OPENCODE_CONFIG directory is not the global config
            # directory. Explicit instructions preserve the installed file.
            config["instructions"] = [*config.get("instructions", []), str(guidance_path)]
        return config
    config["mcpServers"] = {}
    if zg:
        if set(installed.get("mcpServers", {})) != {"zvec_grep"}:
            raise ValueError("Unexpected installed Qoder MCP server set")
        config["mcpServers"] = copy.deepcopy(installed["mcpServers"])
        server = config["mcpServers"]["zvec_grep"]
        server["command"], server["args"] = wrapped_command[0], wrapped_command[1:]
        allow = config.setdefault("permissions", {}).setdefault("allow", [])
        for rule in installed.get("permissions", {}).get("allow", []):
            if rule not in allow:
                allow.append(rule)
        for name in native_names(agent):
            if name not in allow:
                allow.append(name)
    return config


def agent_command(spec, config_path, config_root, guidance_text):
    agent, instruction = spec["agent"], spec["instruction"]
    if agent == "opencode":
        return ["opencode", "--model", "custom-openai/" + spec["model"], "run", "--format", "json", "--thinking", "--", instruction]
    allowed = ["Read", "Grep", "Glob", *(native_names(agent) if spec["arm"] == "zg" else [])]
    command = ["qodercli", "--print", "--output-format", "stream-json", "--no-session-persistence",
               "--config-dir", str(config_root / agent), "--setting-sources", "",
               "--settings", str(config_path), "--mcp-config", str(config_path), "--strict-mcp-config",
               "--disable-builtin-skills", "--permission-mode", "dont_ask", "--tools", "Read,Grep,Glob",
               "--allowed-tools", ",".join(allowed), "--disallowed-tools", ",".join(DENIED),
               "--max-model-request-retries", "0", "--max-turns", str(spec["limits"]["model_requests"]),
               "--model", "Qwen3.8-Max"]
    # Pinned Qoder 1.1.45: --setting-sources '' -> [''] -> allowedAgentSources=[];
    # discoverMemoryPaths therefore omits global/project AGENTS.md. Append the
    # exact installed file once, while preserving the isolated setting sources.
    if guidance_text:
        command += ["--append-system-prompt", guidance_text]
    return [*command, "--", instruction]


def checked_command(argv, *, name, root, env, cwd, timeout=1200):
    started = time.monotonic()
    result = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=timeout)
    (root / f"{name}.stdout.txt").write_text(scrub(result.stdout, env))
    (root / f"{name}.stderr.txt").write_text(scrub(result.stderr, env))
    record = {"command": argv, "returncode": result.returncode, "wall_seconds": time.monotonic() - started}
    save(root / f"{name}.json", record)
    if result.returncode:
        raise RuntimeError(f"{name} failed (exit {result.returncode}); see archived stdout/stderr")
    return result.stdout, record


def probe_catalog(command, *, root, env, cwd, timeout=90):
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, bufsize=1)
    responses = queue.Queue()
    stderr_chunks = []
    def read():
        for line in process.stdout:
            try:
                responses.put(json.loads(line))
            except ValueError:
                continue
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    def read_stderr():
        for line in process.stderr:
            stderr_chunks.append(line)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()
    def request(value):
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()
        if "id" not in value:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = responses.get(timeout=max(0.1, deadline - time.monotonic()))
            if response.get("id") == value["id"] and "method" not in response:
                if "error" in response:
                    raise RuntimeError(f"Native MCP probe failed: {response['error']}")
                return response.get("result")
        raise TimeoutError("Native MCP catalog probe timed out")
    try:
        initialized = request({"jsonrpc": "2.0", "id": "probe-init", "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "zg-benchmark-catalog-probe", "version": "1"}}})
        request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        catalog = request({"jsonrpc": "2.0", "id": "probe-list", "method": "tools/list", "params": {}})
        names = sorted(tool["name"] for tool in catalog.get("tools", []))
        if names != sorted(NATIVE_TOOLS):
            raise ValueError(f"Released agent toolset mismatch: {names}")
        save(root / "native-mcp-probe.json", {"initialize": initialized, "tool_names": names, "status": "passed"})
        return catalog
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        stderr_thread.join(timeout=1)
        (root / "native-mcp-probe.stderr.txt").write_text(scrub("".join(stderr_chunks), env))
        process.stdout.close()
        process.stderr.close()
        thread.join(timeout=1)


def instruction_texts(body):
    """Read actual textual instruction parts without JSON-escaping newlines."""
    texts = []
    for message in body.get("messages", []):
        if message.get("role") not in {"system", "developer"}:
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
    return texts


def first_task_request(root):
    """Title/background model requests are not the first QA decision."""
    wire = root / "wire.jsonl"
    if not wire.exists():
        return {}
    for line in wire.read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event") != "request" or not event.get("tool_names"):
            continue
        request_id = event.get("request_id")
        if type(request_id) is not int:
            continue
        path = root / "wire-requests" / f"request-{request_id:03d}.json"
        return json.loads(path.read_text()) if path.exists() else {}
    return {}


def prepare(spec):
    validate_spec(spec)
    root = Path(spec.get("log_dir", "/logs"))
    root.mkdir(parents=True, exist_ok=True)
    config_root = Path(spec.get("config_root", "/tmp/qa-native"))
    config_root.mkdir(parents=True, exist_ok=True)
    workspace = Path(spec.get("workspace", "/app"))
    agent, zg = spec["agent"], spec["arm"] == "zg"
    config_path, guidance_path = installed_paths(agent, config_root)
    if config_path.exists() or guidance_path.exists():
        raise ValueError("Native trial requires a fresh config directory/session container")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(spec.get("env", {}))
    env.update({"OPENCODE_CONFIG": str(config_path), "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
                "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
                "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true", "QODER_CONFIG_DIR": str(config_root / "qodercli"),
                "QODER_IDE_MCP_PATH": str(config_root / "qoder-ide-mcp.json"),
                "QODER_EXPOSE_TOKEN_USAGE": "1", "QODER_MCP_LAZY": "0",
                "ZVEC_GREP_HOME": str(config_root / "zg"), "ZVEC_GREP_DEVICE": "cpu",
                "ZVEC_GREP_EMBEDDING": EMBEDDING,
                "ZVEC_GREP_MODEL_CACHE": spec.get("model_cache", "/models")})
    versions = {}
    for binary in (["zg", agent] if zg else [agent]):
        output, _ = checked_command([binary, "--version"], name=f"version-{binary}", root=root, env=env, cwd=workspace, timeout=60)
        versions[binary] = output.strip()
        if versions[binary] != VERSIONS[binary]:
            raise ValueError(f"Expected {binary} {VERSIONS[binary]}, got {versions[binary]}")
    installed, installed_guidance, guidance, native_command, wrapped, index_record = {}, "", "", None, None, None
    if zg:
        checked_command(["zg", "config", "model", "set", EMBEDDING, "--default", "--device", "cpu"],
                        name="zg-config-model", root=root, env=env, cwd=workspace)
        checked_command(["zg", "install", "--target", "opencode" if agent == "opencode" else "qoder",
                         "--mcp-transport", "stdio", "--mcp-toolset", "agent", "--yes"],
                        name="zg-install", root=root, env=env, cwd=workspace)
        installed = json.loads(config_path.read_text())
        installed_guidance = guidance_path.read_text()
        (root / "installed-guidance.md").write_text(installed_guidance)
        save(root / "installed-agent-config.json", installed)
        guidance = spec.get("guidance_override", installed_guidance)
        if spec.get("guidance_override"):
            names = native_names(agent)
            guidance = guidance.format(search_tool=names[0], rg_tool="native Grep")
        guidance_path.write_text(guidance)
        (root / "effective-guidance.md").write_text(guidance)
        server = installed["mcp" if agent == "opencode" else "mcpServers"]["zvec_grep"]
        native_command = server["command"] if agent == "opencode" else [server["command"], *server.get("args", [])]
        if not native_command or native_command[0] != "zg":
            raise ValueError("Installer did not produce the expected released zg command")
        wrapped = ["node", str(Path(__file__).with_name("native-mcp-tap.mjs")), "--log-dir", str(root)]
        if spec.get("description_overrides"):
            description_path = root / "description-overrides.json"
            save(description_path, spec["description_overrides"])
            wrapped += ["--descriptions", str(description_path)]
        wrapped += ["--", *native_command]
        if spec.get("prepare_index", True):
            _, index_record = checked_command(["zg", "index", str(workspace), "--embedding", EMBEDDING,
                                              "--device", "cpu", "--model-cache", env["ZVEC_GREP_MODEL_CACHE"]],
                                             name="zg-index", root=root, env=env, cwd=workspace)
            checked_command(["zg", "status", str(workspace), "--check-ready"], name="zg-status-before",
                            root=root, env=env, cwd=workspace)
        # An independent no-model probe catches native transport/schema errors
        # before paid QA, and archives original + effective descriptions.
        probe_catalog(wrapped, root=root, env=env, cwd=workspace)
    config = merge_config(agent, spec["base_config"], installed, zg=zg,
                          guidance_path=guidance_path, wrapped_command=wrapped)
    save(config_path, config)
    save(root / "native-agent-config.json", config)
    command = agent_command(spec, config_path, config_root, guidance)
    manifest = {"schema_version": 1, "arm": spec["arm"], "prompt_variant": spec.get("prompt_variant", "P00"),
                "versions": versions, "integration": "released zg install" if zg else "baseline: zg absent",
                "embedding": EMBEDDING if zg else None, "index_build": index_record,
                "index_policy": "fresh per trial; no cross-trial index identity requirement" if zg else None,
                "expected_native_tools": native_names(agent) if zg else [],
                "guidance_path": str(guidance_path) if zg else None,
                "guidance_text": guidance, "installed_guidance_text": installed_guidance,
                "guidance_sha256": digest(guidance), "installed_guidance_sha256": digest(installed_guidance),
                "config_path": str(config_path), "effective_config_sha256": digest(config_path.read_bytes()),
                "native_mcp_command": native_command, "mcp_command": wrapped,
                "guidance_delivery": "explicit OpenCode instructions file" if agent == "opencode" else "exact installed text via --append-system-prompt; pinned 1.1.45 --setting-sources empty excludes automatic AGENTS loading",
                "guidance_wire_verified": False, "qoder_sampling_control": "effective values unverified" if agent == "qodercli" else None,
                "tool_behavior": "released agent toolset: native zg search only; exact lookup uses agent Grep; no tool-call rewriting or repair"}
    save(root / "install-manifest.json", manifest)
    save(root / "native-install.json", manifest)
    session = {"command": command, "config_path": str(config_path), "log_dir": str(root),
               "native_name": "opencode.txt" if agent == "opencode" else "qodercli-stream.jsonl",
               "limits": spec["limits"], "env": {key: value for key, value in env.items() if os.environ.get(key) != value}}
    if spec.get("tap_upstream"):
        session["tap_upstream"] = spec["tap_upstream"]
    # Saved env is names/config only. Credentials are inherited, never archived.
    save(root / "native-runtime-spec.json", {**session, "env": {key: value for key, value in session["env"].items()
                                                                  if key not in {"OPENAI_API_KEY", "GLM_API_KEY", "QWEN_API_KEY", "QODER_PERSONAL_ACCESS_TOKEN"}}})
    return session, manifest


def run(spec):
    root = Path(spec.get("log_dir", "/logs"))
    root.mkdir(parents=True, exist_ok=True)
    try:
        session, manifest = prepare(spec)
        if spec.get("replay_plan"):
            replay_env = dict(os.environ)
            replay_env.update(session["env"])
            checked_command(["node", str(Path(__file__).with_name("native-replay.mjs")),
                             "--plan", str(spec["replay_plan"]), "--log-dir", str(root)],
                            name="native-replay-launch", root=root, env=replay_env,
                            cwd=spec.get("workspace", "/app"), timeout=spec.get("replay_wall_seconds", 7200))
            save(root / "preparation.json", {"status": "replay_completed", "mode": "retrieval-only", "not_an_e2e_sample": True, "paid_model_requests": 0})
            return 0
        if spec.get("prepare_only"):
            save(root / "preparation.json", {"status": "prepared", "paid_model_requests": 0})
            return 0
    except Exception as error:
        save(root / "preparation.json", {"status": "failed", "error_type": type(error).__name__, "error": str(error)})
        return 2
    module_spec = importlib.util.spec_from_file_location("qa_session_native_delegate", Path(__file__).with_name("qa-session.py"))
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    previous = Path.cwd()
    try:
        os.chdir(spec.get("workspace", "/app"))
        result = module.run(session)
    finally:
        os.chdir(previous)
    if spec["agent"] == "opencode" and spec["arm"] == "zg":
        first = first_task_request(root)
        count = sum(text.count(manifest["guidance_text"].strip()) for text in instruction_texts(first))
        manifest["guidance_wire_occurrences"] = count
        manifest["guidance_wire_verified"] = count == 1
        effective_path = root / "opencode.effective.json"
        if effective_path.exists():
            manifest["pre_wire_config_sha256"] = manifest["effective_config_sha256"]
            manifest["effective_config_sha256"] = digest(effective_path.read_bytes())
            manifest["effective_config_path"] = str(effective_path)
        save(root / "install-manifest.json", manifest)
        save(root / "native-install.json", manifest)
        if not manifest["guidance_wire_verified"]:
            save(root / "guidance-contract.json", {"status": "failed", "reason": "Expected exactly one installed guidance fragment in initial model request", "occurrences": count})
            return 4
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(json.loads(args.spec.read_text())))
