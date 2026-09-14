"""Verify real zg installation and Qoder native retrieval on a tiny fixture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

import runner

PROTOCOL = "workspace-qa-qoder-native-install-v3"
ZG_SEARCH_TOOL = "mcp__zvec_grep__zvec_grep_search"
PROBE_MARKER = "WORKSPACE_QA_NATIVE_PROBE_7f3a"
PROBE_TEXT = ("代码仓库问答：检索源代码并引用文件。 Repository code search finds source files. "
              + PROBE_MARKER + "\n")


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Evidence must contain a JSON object")
    return value


def installation_evidence(agent: Path, *, profile: str = "with-zg") -> dict:
    from native_session import validate_installation
    return validate_installation(agent, profile=profile)


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block["text"] for block in content if isinstance(block, dict)
                         and block.get("type") == "text" and isinstance(block.get("text"), str))
    return ""


def native_mcp_evidence(agent: Path) -> dict:
    """Read actual native calls/results; no private bridge trace is required."""
    path = agent / "qodercli-stream.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    calls, observations, initialized = {}, {}, []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Native trace must contain objects")
        if event.get("type") == "system" and event.get("subtype") == "init":
            initialized.append(event.get("qodercli_version") == "1.1.45"
                and event.get("model") == "Qwen3.8-Max"
                and ZG_SEARCH_TOOL in event.get("tools", [])
                and any(server.get("name") == "zvec_grep" and server.get("status") == "connected"
                        for server in event.get("mcp_servers", []) if isinstance(server, dict)))
        message = event.get("message")
        blocks = message.get("content", []) if isinstance(message, dict) else []
        if not isinstance(blocks, list):
            continue
        scope = (event.get("session_id"), event.get("parent_tool_use_id"))
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use" and block.get("name") == ZG_SEARCH_TOOL:
                if not isinstance(block.get("id"), str) or not block["id"]:
                    raise ValueError("Native zg call has no ID")
                key, value = (*scope, block["id"]), block.get("input", {})
                if key in calls and calls[key] != value:
                    raise ValueError("Conflicting native call IDs")
                calls[key] = value
            elif event.get("type") == "user" and block.get("type") == "tool_result":
                key = (*scope, block.get("tool_use_id"))
                value = {"is_error": block.get("is_error") is True, "text": _content_text(block.get("content"))}
                if key in observations and observations[key] != value:
                    raise ValueError("Conflicting native tool results")
                observations[key] = value
    succeeded = [key for key in calls if key in observations and not observations[key]["is_error"]]
    vectors = [key for key in succeeded if isinstance(calls[key], dict)
               and isinstance(calls[key].get("vector"), str) and calls[key]["vector"].strip()]
    fixtures = [key for key in vectors if "probe.md" in observations[key]["text"]
                and PROBE_MARKER in observations[key]["text"]]
    return {"mcp_registered_and_connected": bool(initialized) and all(initialized),
            "native_attempts": len(calls), "native_successes": len(succeeded),
            "native_errors": sum(observations[key]["is_error"] for key in calls if key in observations),
            "native_missing_results": sum(key not in observations for key in calls),
            "native_empty_successes": sum(not observations[key]["text"].strip() for key in succeeded),
            "native_vector_successes": len(vectors), "native_fixture_vector_successes": len(fixtures),
            "native_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def validate_probe(output: Path) -> dict:
    report = read_object(output / "result.json")
    evidence = native_mcp_evidence(output / "agent")
    installation = installation_evidence(output / "agent")
    measured, attempts = report.get("zg_tool_calls_successful"), report.get("zg_tool_calls")
    valid = (report.get("protocol") == PROTOCOL and report.get("status") == "completed"
        and report.get("phase") == "setup_qoder_mcp_probe" and report.get("included_in_benchmark") is False
        and report.get("embedding_model") == runner.EMBEDDING and report.get("model") == runner.MODEL
        and isinstance(report.get("model_identity"), dict) and report["model_identity"].get("valid") is True
        and report.get("source_unchanged") is True and evidence["mcp_registered_and_connected"]
        and isinstance(report.get("installation"), dict)
        and report["installation"].get("manifest_sha256") == installation["manifest_sha256"]
        and type(report.get("input_tokens")) is int and report["input_tokens"] > 0
        and type(report.get("tool_calls")) is int and report["tool_calls"] >= 1
        and type(attempts) is int and attempts == evidence["native_attempts"]
        and type(measured) is int and measured == evidence["native_successes"] and measured > 0
        and evidence["native_missing_results"] == 0 and evidence["native_empty_successes"] == 0
        and evidence["native_fixture_vector_successes"] > 0)
    if not valid:
        raise ValueError("Native probe requires standard installation, unchanged source, actual fixture vector retrieval and observable Qoder usage")
    return {"status": "valid", "protocol": PROTOCOL, "included_in_qa_metrics": False,
            "verified_successful_vector_searches": evidence["native_fixture_vector_successes"],
            "result_sha256": hashlib.sha256((output / "result.json").read_bytes()).hexdigest(),
            "installation": installation, **evidence}


def qoder_mcp_preflight(source: Path, output: Path, cache: Path | None = None, index: Path | None = None) -> None:
    """Run only the released native CLI/daemon/install route; legacy arguments are unused."""
    from native_runner import run_native_probe
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        run_native_probe(source, output)
        validation = validate_probe(output)
        runner.write_json(output / "validation.json", validation)
        print(json.dumps({**validation, "phase": "native_install_preflight", "status": "completed"}), flush=True)
    except Exception as error:
        failure = {"status": "invalid", "protocol": PROTOCOL, "included_in_qa_metrics": False,
                   "error_type": type(error).__name__, "error": runner.redact(str(error)),
                   "wall_seconds": round(time.monotonic() - started, 3)}
        runner.write_json(output / "validation.json", failure)
        raise


def standalone_native_probe(root: Path) -> None:
    """Create the independent synthetic fixture, preserving diagnostics after cleanup."""
    source = root / "source"
    source.mkdir(parents=True, exist_ok=False)
    (source / "probe.md").write_text(PROBE_TEXT, encoding="utf-8")
    try:
        for args in (["init", "-q", str(source)], ["-C", str(source), "add", "probe.md"],
                     ["-C", str(source), "-c", "user.name=Workspace QA", "-c", "user.email=benchmark@localhost",
                      "-c", "gc.auto=0", "commit", "-qm", "Synthetic native connectivity fixture"]):
            subprocess.run(["git", *args], check=True)
        qoder_mcp_preflight(source, root / "qoder")
    finally:
        shutil.rmtree(source)
