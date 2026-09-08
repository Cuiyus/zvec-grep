"""Pinned, explicit agent contracts for the fixed-corpus read-only experiment.

Qoder uses its native account model, authenticated only through a PAT environment
variable. This is not an undocumented BYOK settings-file integration. CLI/tool
contracts were checked against published Qoder 1.1.45 and its official references:
https://docs.qoder.com/cli/model
https://docs.qoder.com/cli/permissions
https://docs.qoder.com/cli/mcp-reference
https://docs.qoder.com/cli/custom-models
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


QODER_VERSION = "1.1.45"
OPENCODE_VERSION = "1.18.4"
QODER_READ_TOOLS = ("Read", "Grep", "Glob")
QODER_SEARCH_TOOL = "mcp__zvec_grep__zvec_grep_search"
QODER_DENY_TOOLS = (
    "Bash", "Edit", "Write", "NotebookEdit", "Agent", "Task", "Skill",
    "WebFetch", "WebSearch", "ImageGen", "ImageSearch", "Workflow",
)
_QODER_MODEL_ALIASES = {"qmodel_38max": "qwen3.8-max"}


@dataclass(frozen=True)
class AgentSpec:
    name: str
    version: str
    model: str
    cli_model: str
    provider_model: str
    credential_env: str
    stream_filename: str
    config_filename: str
    base_url: str | None
    auth_mode: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def agent_spec(
    agent: str,
    model: str,
    *,
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> AgentSpec:
    """Describe an authorized combination without resolving any credential."""
    name = "qodercli" if agent in {"qoder", "qodercli"} else agent
    model_id = model.removeprefix("custom-openai/")
    if name == "qodercli":
        if model_id.lower() != "qwen3.8-max":
            raise ValueError("Qoder experiment requires the exact qwen3.8-max model")
        if base_url is not None or api_key_env not in {None, "QODER_PERSONAL_ACCESS_TOKEN"}:
            raise ValueError("Qoder uses native PAT authentication; BYOK is not configured here")
        return AgentSpec(name, QODER_VERSION, "qwen3.8-max", "Qwen3.8-Max",
                         "qwen3.8-max", "QODER_PERSONAL_ACCESS_TOKEN",
                         "qodercli-stream.jsonl", "qoder.json", None, "qoder-native-pat")
    if name != "opencode" or model_id.lower() not in {"glm-5.2", "qwen3.8-max"}:
        raise ValueError("Unsupported fixed-case agent/model combination")
    if not base_url:
        raise ValueError("OpenCode requires an explicit provider base URL")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Provider URL must be HTTP(S) and contain no credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Provider URL must not contain a query or fragment")
    env_name = api_key_env or "OPENAI_API_KEY"
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", env_name):
        raise ValueError("API key must be referenced by its environment-variable name")
    model_id = model_id.lower()
    return AgentSpec(name, OPENCODE_VERSION, f"custom-openai/{model_id}",
                     f"custom-openai/{model_id}", model_id, env_name, "opencode.txt",
                     "opencode.json", base_url, "openai-compatible-env")


def expected_tools(spec: AgentSpec, *, zg: bool) -> list[str]:
    if spec.name == "qodercli":
        return [*QODER_READ_TOOLS, *([QODER_SEARCH_TOOL] if zg else [])]
    return ["read", "glob", "grep", *(["zvec_grep_zvec_grep_search"] if zg else [])]


def control_manifest(spec: AgentSpec, *, max_model_turns: int) -> dict[str, Any]:
    if isinstance(max_model_turns, bool) or not isinstance(max_model_turns, int) or max_model_turns < 1:
        raise ValueError("max_model_turns must be a positive integer")
    return {
        "temperature": 0 if spec.name == "opencode" else None,
        "temperature_control": "provider.models.<model>.temperature=true + agent.build.temperature=0" if spec.name == "opencode"
        else "unsupported_by_verified_qoder_1.1.45_cli",
        "temperature_wire_verification_required": spec.name == "opencode",
        "model_turn_limit": max_model_turns,
        "native_model_turn_limit": "agent.build.steps" if spec.name == "opencode" else "--max-turns (pinned bundle verified)",
        "external_budget_enforcement_required": True,
        "qoder_model_request_retries": 0 if spec.name == "qodercli" else None,
        "seed_control": "not_exposed",
    }


def build_agent_config(
    spec: AgentSpec,
    *,
    zg: bool,
    mcp_command: list[str] | None = None,
    max_model_turns: int = 30,
) -> dict[str, Any]:
    """Generate public configuration; the caller writes it outside the corpus."""
    control_manifest(spec, max_model_turns=max_model_turns)
    if zg and (not mcp_command or not all(isinstance(v, str) and v for v in mcp_command)):
        raise ValueError("zg integration requires the explicit read-only MCP command")
    if not zg and mcp_command:
        raise ValueError("Baseline must not receive an MCP command")
    if spec.name == "opencode":
        config: dict[str, Any] = {
            "$schema": "https://opencode.ai/config.json", "model": spec.model,
            "autoupdate": False, "share": "disabled", "lsp": False,
            "provider": {"custom-openai": {
                "npm": "@ai-sdk/openai-compatible",
                "options": {"apiKey": "{env:" + spec.credential_env + "}", "baseURL": spec.base_url},
                # OpenCode 1.18.4 defaults a custom model's temperature
                # capability to false and omits the sampling parameter even
                # when agent.build.temperature is set. Both are required.
                "models": {spec.provider_model: {"name": spec.provider_model, "temperature": True}},
            }},
            "agent": {"build": {"temperature": 0, "steps": max_model_turns}},
            "permission": {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow"},
        }
        if zg:
            config["permission"]["zvec_grep_*"] = "allow"
            config["mcp"] = {"zvec_grep": {
                "type": "local", "enabled": True, "timeout": 600000,
                "command": list(mcp_command or []),
            }}
        return config
    config = {
        "model": {"name": spec.cli_model},
        "tools": {"core": list(QODER_READ_TOOLS), "useRipgrep": True},
        "general": {"defaultPermissionMode": "dont_ask", "enableAutoUpdate": False,
                    "enableAutoUpdateNotification": False},
        "disableAllHooks": True,
        "permissions": {"allow": expected_tools(spec, zg=zg), "deny": list(QODER_DENY_TOOLS)},
        "security": {"disableYoloMode": True, "environmentVariableRedaction": {"enabled": True}},
        "mcp": {"lazyLoad": False},
        "mcpServers": {},
    }
    if zg:
        assert mcp_command is not None
        config["mcpServers"] = {"zvec_grep": {
            "type": "stdio", "command": mcp_command[0], "args": mcp_command[1:],
            "timeout": 600000, "includeTools": ["zvec_grep_search"],
            "alwaysAllow": ["zvec_grep_search"],
        }}
    return config


def agent_environment(spec: AgentSpec, *, config_path: str) -> dict[str, str]:
    """Non-secret env additions; pass spec.credential_env separately by name."""
    if spec.name == "opencode":
        return {"OPENCODE_CONFIG": config_path, "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
                "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
                "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true"}
    return {"QODER_CONFIG_DIR": "/tmp/qa-qoder-config", "QODER_EXPOSE_TOKEN_USAGE": "1",
            "QODER_MCP_LAZY": "0"}


def build_agent_command(
    spec: AgentSpec, instruction: str, *, config_path: str, zg: bool = False,
    max_model_turns: int = 30,
) -> list[str]:
    """Return argv, not shell text; no prompt or credential is reinterpreted."""
    control_manifest(spec, max_model_turns=max_model_turns)
    if spec.name == "opencode":
        return ["opencode", "--model", spec.cli_model, "run", "--format", "json", "--thinking", "--", instruction]
    return [
        "qodercli", "--print", "--output-format", "stream-json", "--no-session-persistence",
        "--config-dir", "/tmp/qa-qoder-config", "--setting-sources", "",
        "--settings", config_path, "--mcp-config", config_path, "--strict-mcp-config",
        "--disable-builtin-skills",
        "--permission-mode", "dont_ask", "--tools", ",".join(QODER_READ_TOOLS),
        "--allowed-tools", ",".join(expected_tools(spec, zg=zg)),
        "--disallowed-tools", ",".join(QODER_DENY_TOOLS),
        "--max-model-request-retries", "0", "--max-turns", str(max_model_turns),
        "--model", spec.cli_model, "--", instruction,
    ]


def read_native_events(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    invalid: list[int] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid.append(line_number)
            continue
        if isinstance(value, dict):
            events.append(value)
        else:
            invalid.append(line_number)
    return events, {"invalid_json_lines": invalid, "last_line_incomplete": bool(invalid and invalid[-1] == len(lines))}


def _qoder_identity(events: list[dict[str, Any]], spec: AgentSpec) -> dict[str, Any]:
    observed: set[str] = set()
    sources: set[str] = set()
    for event in events:
        if event.get("parent_tool_use_id"):
            continue
        if event.get("type") == "assistant" and isinstance(event.get("message"), dict):
            model = event["message"].get("model")
            if isinstance(model, str) and model:
                observed.add(_QODER_MODEL_ALIASES.get(model.lower(), model.lower()))
                sources.add("assistant.message.model")
        if event.get("type") == "result" and isinstance(event.get("modelUsage"), dict):
            for model in event["modelUsage"]:
                observed.add(_QODER_MODEL_ALIASES.get(model.lower(), model.lower()))
                sources.add("result.modelUsage")
    return {"requested": spec.provider_model, "observed": sorted(observed),
            "sources": sorted(sources), "valid": observed == {spec.provider_model}}


def qoder_contract(events: list[dict[str, Any]], *, zg: bool) -> dict[str, Any]:
    allowed = {*QODER_READ_TOOLS, *([QODER_SEARCH_TOOL] if zg else [])}
    starts = [e for e in events if e.get("type") == "system" and e.get("subtype") == "init"
              and not e.get("parent_tool_use_id")]
    observed = {t for e in starts for t in e.get("tools", []) if isinstance(t, str)}
    called = {b["name"] for e in events if e.get("type") == "assistant"
              for b in (e.get("message", {}).get("content", []) or [])
              if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("name"), str)}
    modes = sorted({e.get("permissionMode") for e in starts if isinstance(e.get("permissionMode"), str)})
    connected = any(server.get("name") == "zvec_grep" and server.get("status") == "connected"
                    for e in starts for server in e.get("mcp_servers", []) if isinstance(server, dict))
    # An unavailable tool name generated by the model is an observed agent
    # error. It does not change the correctly advertised runtime contract.
    valid = bool(starts) and observed == allowed
    valid = valid and bool(modes) and set(modes) <= {"dont_ask", "dontAsk"} and (not zg or connected)
    return {"valid": valid, "expected_tools": sorted(allowed), "observed_tools": sorted(observed),
            "unexpected_tool_calls": sorted(called - allowed), "permission_modes": modes,
            "zg_mcp_connected": connected if zg else None, "init_events": len(starts)}


def _annotate_qoder_messages(data: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """Retain per-message usage and reasoning that the Harbor adapter omits."""
    from ..agents.qodercli import _number

    groups: list[dict[str, Any]] = []
    positions: dict[tuple[str, str, str], int] = {}
    for event in events:
        message = event.get("message")
        if event.get("type") != "assistant" or not isinstance(message, dict):
            continue
        message_id = message.get("id")
        key = (str(event.get("session_id") or ""), str(event.get("parent_tool_use_id") or ""), str(message_id))
        position = positions.get(key) if message_id else None
        if position is None:
            position = len(groups)
            groups.append({"message_id": message_id, "session_id": key[0], "parent_tool_use_id": key[1],
                           "usage_snapshots": [], "reasoning": []})
            if message_id:
                positions[key] = position
        group = groups[position]
        usage = message.get("usage")
        if isinstance(usage, dict) and usage not in group["usage_snapshots"]:
            group["usage_snapshots"].append(usage)
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "thinking" and isinstance(block.get("thinking"), str):
                if block["thinking"] not in group["reasoning"]:
                    group["reasoning"].append(block["thinking"])
    steps = [s for s in data.get("steps", []) if s.get("source") == "agent" and s.get("llm_call_count") == 1]
    if len(steps) != len(groups):
        raise RuntimeError("Qoder message/ATIF step mapping differs; cannot safely attach usage")
    for step, group in zip(steps, groups, strict=True):
        usages = group["usage_snapshots"]
        # Some completed-block events repeat a masked all-zero usage object.
        # A later masked block must not erase usage already exposed for this ID.
        usable = [usage for usage in usages if any(
            (_number(usage.get(k), integer=True) or 0) > 0
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"))]
        usage = usable[-1] if usable else (usages[-1] if usages else {})
        tokens = {k: _number(usage.get(k), integer=True)
                  for k in ("input_tokens", "output_tokens", "cache_read_input_tokens")}
        available = any(v is not None and v > 0 for v in tokens.values())
        step["extra"] = {**step.get("extra", {}), "qoder_message_id": group["message_id"],
                         "qoder_session_id": group["session_id"], "qoder_parent_tool_use_id": group["parent_tool_use_id"]}
        metrics: dict[str, Any] = {"extra": {"qoder_usage_snapshots": usages,
                                            "token_usage_available": available,
                                            "usage_aggregation": "last_nonzero_message_snapshot_not_sum"}}
        if available:
            for source, target in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"),
                                   ("cache_read_input_tokens", "cached_tokens")):
                if tokens[source] is not None:
                    metrics[target] = tokens[source]
        step["metrics"] = metrics
        if group["reasoning"]:
            step["reasoning_content"] = "\n".join(group["reasoning"])


def convert_agent_trace(
    agent_dir: Path, spec: AgentSpec, instruction: str, *, zg: bool = False,
) -> dict[str, Any]:
    """Always retain a recoverable trajectory; return explicit validity failures.

    The runner must fail a trial when error_event_count > 0 or has_final_answer is
    false. Tool errors alone are retained as quality/cost observations, not hidden
    or transformed into session success/failure. No retry or tool-name aliasing.
    """
    from .readonly_judge import extract_final_answer

    events, parse = read_native_events(agent_dir / spec.stream_filename)
    if spec.name == "opencode":
        from harbor.agents.installed.opencode import OpenCode
        adapter = OpenCode(logs_dir=agent_dir, model_name=spec.model, version=spec.version)
        adapter._instruction = instruction
        trajectory = adapter._convert_events_to_trajectory(events)
        errors = sum(e.get("type") == "error" for e in events)
        identity = {"requested": spec.model, "observed": [], "valid": None,
                    "limitation": "run JSON events do not expose provider response model identity"}
        contract = None
        contract_errors = 0
        successful_result = True
    else:
        from ..agents.qodercli import QoderCLI
        adapter = QoderCLI(logs_dir=agent_dir, model_name=spec.model, version=spec.version)
        trajectory = adapter._trajectory(events)
        results = [e for e in events if e.get("type") == "result" and not e.get("parent_tool_use_id")]
        successful_result = bool(results) and results[-1].get("subtype") == "success" and not results[-1].get("is_error")
        identity = _qoder_identity(events, spec)
        contract = qoder_contract(events, zg=zg)
        validation_errors = int(not identity["valid"]) + int(not contract["valid"])
        # Missing identity/init after a truncated stream is a failed observation,
        # not affirmative evidence of a configuration mismatch for later trials.
        contract_errors = int(bool(identity["observed"]) and not identity["valid"])
        contract_errors += int(bool(contract["init_events"]) and not contract["valid"])
        errors = sum(e.get("type") == "error" for e in events)
        errors += int(not successful_result) + validation_errors
    if trajectory is None:
        raise RuntimeError("Native stream contains no recoverable agent trajectory")
    data = trajectory.model_dump(mode="json", exclude_none=True)
    if spec.name == "qodercli":
        _annotate_qoder_messages(data, events)
    (agent_dir / "trajectory.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tool_errors = sum(bool(result.get("extra", {}).get("is_error"))
                      for step in data.get("steps", [])
                      for result in step.get("observation", {}).get("results", []))
    has_final = successful_result and extract_final_answer(data) is not None
    return {"event_count": len(events), "error_event_count": errors + len(parse["invalid_json_lines"]),
            "contract_error_count": contract_errors,
            "has_final_answer": has_final, "tool_error_count": tool_errors,
            "model_identity": identity, "tool_contract": contract, "parse": parse,
            "final_metrics": data.get("final_metrics", {})}
