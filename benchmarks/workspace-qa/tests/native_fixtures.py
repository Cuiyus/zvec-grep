"""Offline consumers' fixtures; installer validation has its own dedicated tests."""
import runner
import hashlib
import json
from pathlib import Path

PROTOCOL = "workspace-qa-qoder-native-install-v3"
TOOL = "mcp__zvec_grep__zvec_grep_search"
MARKER = "WORKSPACE_QA_NATIVE_PROBE_7f3a"


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_installation(agent, profile="with-zg"):
    dump(agent / "install-manifest.json", {"protocol": PROTOCOL, "profile": profile})
    return installation_stub(agent, profile=profile)


def installation_stub(agent, *, profile="with-zg"):
    path = agent / "install-manifest.json"
    value = json.loads(path.read_text())
    if value != {"protocol": PROTOCOL, "profile": profile}:
        raise ValueError("invalid installation fixture")
    return {"manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "profile": profile}


def write_native(agent, outcomes=(True,), *, marker=MARKER, vector=True):
    agent.mkdir(parents=True, exist_ok=True)
    events = [{"type": "system", "subtype": "init", "qodercli_version": "1.1.45", "model": runner.SPEC.cli_model,
               "tools": [TOOL], "mcp_servers": [{"name": "zvec_grep", "status": "connected"}]}]
    for index, success in enumerate(outcomes):
        key = str(index)
        events.append({"type": "assistant", "session_id": "fixture-session", "message": {"content": [
            {"type": "tool_use", "id": key, "name": TOOL,
             "input": {"root": "/app", "vector" if vector else "query": "repository code search"}}]}})
        if success is not None:
            events.append({"type": "user", "session_id": "fixture-session", "message": {"content": [
                {"type": "tool_result", "tool_use_id": key, "is_error": not success,
                 "content": f"freshness: fresh\nprobe.md:1 {marker}" if success else "embedding unavailable"}]}})
    (agent / "qodercli-stream.jsonl").write_text("\n".join(json.dumps(event) for event in events))
    return events


def write_probe(output, outcomes=(True,), **kwargs):
    agent = output / "agent"
    installation = write_installation(agent)
    write_native(agent, outcomes, **kwargs)
    result = {"protocol": PROTOCOL, "phase": "setup_qoder_mcp_probe", "status": "completed",
              "included_in_benchmark": False, "embedding_model": "qwen/qwen3.7-text-embedding",
              "model": runner.MODEL, "model_identity": {"valid": True}, "source_unchanged": True,
              "input_tokens": 123, "tool_calls": len(outcomes), "zg_tool_calls": len(outcomes),
              "zg_tool_calls_successful": sum(value is True for value in outcomes), "installation": installation}
    dump(output / "result.json", result)
    return result
