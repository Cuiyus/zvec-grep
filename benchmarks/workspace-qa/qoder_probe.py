"""Exercise the real Qoder -> MCP -> remote vector path before corpus downloads."""
from __future__ import annotations

import json
from pathlib import Path
import time

import runner


def qoder_mcp_preflight(source: Path, output: Path, cache: Path, index: Path) -> None:
    """Use the synthetic SDK fixture; this diagnostic is never a benchmark trial."""
    output.mkdir(parents=True, exist_ok=True)
    agent = output / "agent"
    agent.mkdir()
    image = "zg-readonly-qa:0.2.2"
    endpoint = runner.embedding_endpoint()
    started = time.monotonic()
    report = {"phase": "setup_qoder_mcp_probe", "status": "failed",
              "included_in_benchmark": False, "embedding_model": runner.EMBEDDING,
              "model": runner.MODEL, "successful_vector_searches": 0}
    flags = ["--root", "/app", "--package-dir", runner.PACKAGE_DIR,
             "--embedding-model", runner.EMBEDDING, "--model-cache-dir", "/models", "--working-copy"]
    prompt = ("This is a connectivity check on a synthetic fixture, not a benchmark question. "
              "Call " + runner.QODER_SEARCH_TOOL + " exactly once with "
              '{"vector":"代码仓库 repository source files","limit":1}. '
              "Do not use other tools. After the tool returns, reply with the retrieved filename only. "
              "If the tool fails, report its error without retrying.")
    try:
        command = runner.with_embedding_environment(
            runner.docker_command(image, source, output, cache, index=index), endpoint)
        runner.run_named(command + [image, "node", runner.BRIDGE, "preflight", *flags,
            "--snapshot", "/logs/snapshot.json", "--log", "/logs/preflight.jsonl"],
            "workspaceqa-qoder-probe-preflight", timeout=120,
            diagnostic_path=output / "preflight-failure.json")
        config = output / runner.SPEC.config_filename
        config_path = "/run/qa/" + runner.SPEC.config_filename
        mcp = ["node", runner.BRIDGE, "serve", *flags,
               "--snapshot", "/run/qa/snapshot.json", "--log", "/logs/zg-trace.jsonl"]
        runner.write_json(config, runner.build_agent_config(
            runner.SPEC, zg=True, mcp_command=mcp, max_model_turns=4,
            mcp_env_names=runner.REMOTE_EMBEDDING_ENV_NAMES))
        spec = {"command": runner.build_agent_command(runner.SPEC, prompt, config_path=config_path,
                                                      zg=True, max_model_turns=4),
                "env": runner.agent_environment(runner.SPEC, config_path=config_path),
                "config_path": config_path,
                "limits": {"model_requests": 4, "tool_calls": 4, "input_tokens": 50000, "wall_seconds": 120},
                "native_name": runner.SPEC.stream_filename, "log_dir": "/logs"}
        runner.write_json(agent / "session-spec.json", spec)
        command = runner.with_embedding_environment(runner.docker_command(
            image, source, agent, cache, index=index, snapshot=output / "snapshot.json"), endpoint)
        command += ["--env", runner.SPEC.credential_env] + runner.mount(config, config_path)
        runner.run_named(command + [image, "python3", "/opt/qa/qa-session.py", "--spec", "/logs/session-spec.json"],
                         "workspaceqa-qoder-probe", timeout=150,
                         diagnostic_path=agent / "launcher-failure.json",
                         stream_output=agent / "launcher")
        session = json.loads((agent / "session.json").read_text())
        conversion = runner.convert_agent_trace(agent, runner.SPEC, prompt, zg=True)
        metrics = runner.trial_metrics(agent, conversion)
        report.update(model_identity=conversion.get("model_identity"),
                      input_tokens=metrics.get("input_tokens"), tool_calls=metrics.get("tool_calls"),
                      zg_tool_calls_successful=metrics.get("zg_tool_calls_successful"))
        traces = [json.loads(line) for line in (agent / "zg-trace.jsonl").read_text().splitlines() if line.strip()]
        successes = [event for event in traces if event.get("event") == "search"
                     and event.get("origin") == "agent-mcp" and event.get("status") == "success"
                     and any(route.get("mode") == "vector" for route in event.get("request", {}).get("routes", []))
                     and "probe.md" in event.get("text", "")]
        report["successful_vector_searches"] = len(successes)
        if (session.get("status") != "completed" or conversion.get("contract_error_count")
                or conversion.get("error_event_count") or not conversion.get("has_final_answer")
                or not (conversion.get("model_identity") or {}).get("valid")
                or metrics.get("input_tokens") is None
                or not metrics.get("zg_tool_calls_successful") or not successes):
            raise RuntimeError("Qoder MCP probe requires a successful remote vector search and observable native model usage")
        report["status"] = "completed"
    except Exception as error:
        report.update(error_type=type(error).__name__, error=runner.redact(str(error)))
        raise
    finally:
        report["wall_seconds"] = round(time.monotonic() - started, 3)
        runner.write_json(output / "result.json", report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
