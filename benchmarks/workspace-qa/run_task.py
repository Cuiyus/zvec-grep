#!/usr/bin/env python3
"""One CI job: prepare, execute a pair series, score, and always retain its ledger."""
from __future__ import annotations
import argparse
import json
import hashlib
import math
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time
from urllib import request, error

HERE = Path(__file__).resolve().parent
ZG_SEARCH_TOOL = "mcp__zvec_grep__zvec_grep_search"


def _jsonl(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Raw trace must contain JSON objects")
    return rows


def _mcp_evidence(agent: Path) -> dict:
    """Reconcile actual MCP observations without treating setup calls as QA calls."""
    native_path, bridge_path = agent / "qodercli-stream.jsonl", agent / "zg-trace.jsonl"
    native, bridge = _jsonl(native_path), _jsonl(bridge_path)
    calls, observations = {}, {}
    initialized = False
    for event in native:
        if event.get("type") == "system" and event.get("subtype") == "init":
            initialized = (event.get("qodercli_version") == "1.1.45"
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
                calls[(*scope, block["id"])] = block.get("input", {})
            elif event.get("type") == "user" and block.get("type") == "tool_result":
                observations[(*scope, block.get("tool_use_id"))] = block.get("is_error") is not True
    searches = [row for row in bridge if row.get("event") == "search" and row.get("origin") == "agent-mcp"]
    vector_results = [row for row in searches if row.get("status") == "success"
        and any(route.get("mode") == "vector" for route in row.get("request", {}).get("routes", []))
        and "probe.md" in row.get("text", "")]
    return {"mcp_registered_and_connected": initialized,
            "native_attempts": len(calls),
            "native_successes": sum(observations.get(call) is True for call in calls),
            "native_errors": sum(observations.get(call) is False for call in calls),
            "native_vector_successes": sum(observations.get(call) is True and isinstance(args, dict)
                and isinstance(args.get("vector"), str) and bool(args["vector"])
                for call, args in calls.items()),
            "bridge_attempts": len(searches),
            "bridge_successes": sum(row.get("status") == "success" for row in searches),
            "bridge_errors": sum(row.get("status") == "error" for row in searches),
            "bridge_fixture_vector_successes": len(vector_results),
            "native_sha256": hashlib.sha256(native_path.read_bytes()).hexdigest(),
            "bridge_sha256": hashlib.sha256(bridge_path.read_bytes()).hexdigest()}


def _setup_probe_evidence(runs: Path) -> dict:
    probe = {"status": "invalid", "included_in_qa_metrics": False,
             "path": "sdk-preflight/qoder", "verified_successful_vector_searches": 0}
    try:
        root = runs.parent.resolve()
        path = (root / "sdk-preflight/qoder").resolve()
        if not path.is_relative_to(root):
            raise ValueError("Setup probe escapes the current run")
        report_path = path / "result.json"
        report = json.loads(report_path.read_text())
        probe["result_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        evidence = _mcp_evidence(path / "agent")
        probe.update(evidence)
        measured, vectors = report.get("zg_tool_calls_successful"), report.get("successful_vector_searches")
        valid = (report.get("status") == "completed" and report.get("phase") == "setup_qoder_mcp_probe"
            and report.get("included_in_benchmark") is False
            and report.get("embedding_model") == "qwen/qwen3.7-text-embedding"
            and report.get("model") == "qwen3.8-max" and report.get("model_identity", {}).get("valid") is True
            and evidence["mcp_registered_and_connected"]
            and type(measured) is int and measured > 0
            and measured == evidence["native_successes"] == evidence["bridge_successes"]
            and type(vectors) is int and vectors > 0
            and vectors == evidence["native_vector_successes"] == evidence["bridge_fixture_vector_successes"])
        if valid:
            probe.update(status="valid", verified_successful_vector_searches=vectors)
        else:
            probe["reason"] = "Setup report and raw Qoder vector evidence do not agree"
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        probe.update(reason="Same-run setup Qoder vector evidence is missing or invalid", error_type=type(exc).__name__)
    return probe


def smoke_validation(runs: Path, phase: str) -> dict:
    """Validate integration in smoke; formal trials never depend on choosing zg."""
    result = {"schema_version": 2, "phase": phase, "status": "not_applicable" if phase == "batch" else "invalid",
              "scope": "QA MCP integrity and outcomes; natural non-use requires a verified same-run Qoder vector probe",
              "trials": [], "verified_successful_searches": 0,
              "preserves_all_trial_metrics_and_judgements": True}
    if phase == "batch":
        return result
    if phase != "smoke":
        raise ValueError("phase must be smoke or batch")
    result["setup_probe"] = _setup_probe_evidence(runs)
    try:
        ledger_path = runs / "trial-results.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        result["trial_results_sha256"] = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        if not isinstance(ledger, dict) or not isinstance(ledger.get("trials"), list):
            raise ValueError("Smoke ledger requires trials")
        result["qa_execution_complete"] = bool(ledger["trials"]) and all(
            trial.get("status") == "completed" for trial in ledger["trials"])
        for trial in ledger["trials"]:
            if trial.get("profile") != "with-zg" or trial.get("status") != "completed":
                continue
            trial_id = trial.get("trial_id")
            if not isinstance(trial_id, str) or not trial_id or Path(trial_id).name != trial_id:
                raise ValueError("Invalid smoke trial_id")
            agent = (runs / trial_id / "agent").resolve()
            if not agent.is_relative_to(runs.resolve()):
                raise ValueError("Smoke trace path escapes runs")
            evidence = _mcp_evidence(agent)
            measured = trial.get("zg_tool_calls_successful")
            reconciles = type(measured) is int and measured == evidence["native_successes"] == evidence["bridge_successes"]
            attempts = trial.get("zg_tool_calls")
            attempts_reconcile = type(attempts) is int and attempts == evidence["native_attempts"] == evidence["bridge_attempts"]
            integrity = all(trial.get(key) is True for key in (
                "source_unchanged", "original_seed_unchanged", "working_index_semantic_unchanged"))
            non_use = attempts_reconcile and attempts == 0
            eligible = measured > 0 if reconciles else False
            if non_use and result["setup_probe"]["status"] == "valid":
                eligible = True
            row = {"trial_id": trial_id, "measured_successes": measured, **evidence,
                   "success_counts_reconcile": reconciles, "attempt_counts_reconcile": attempts_reconcile,
                   "integrity_valid": integrity, "natural_non_use": non_use,
                   "valid": reconciles and attempts_reconcile and integrity
                       and evidence["mcp_registered_and_connected"] and eligible}
            result["trials"].append(row)
            if reconciles:
                result["verified_successful_searches"] += evidence["native_successes"]
        if result["qa_execution_complete"] and result["trials"] and all(row["valid"] for row in result["trials"]):
            result["status"] = "valid"
        else:
            result["reason"] = "Smoke requires intact registered QA MCP and reconciled successful calls, or natural non-use with a verified same-run vector probe"
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result.update(status="invalid", reason="Smoke search evidence is missing or invalid", error_type=type(exc).__name__)
    return result


def annotate_smoke_report(output: Path, validation: dict) -> None:
    """Keep observations intact while separating their completeness from smoke validity."""
    if validation["phase"] != "smoke":
        return
    summary_path, markdown_path = output / "summary.json", output / "summary.md"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["smoke_validation"] = validation
        summary["pipeline_validation_complete"] = validation["status"] == "valid" and summary.get("summary", {}).get("complete") is True
        summary["efficacy_claim_ready"] = False
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if markdown_path.is_file():
        text = markdown_path.read_text(encoding="utf-8")
        label = validation["status"].upper()
        probe = validation.get("setup_probe", {})
        note = (f"**Smoke MCP validation: {label}.** Confirmed successful QA searches: {validation['verified_successful_searches']}. "
                f"Same-run setup vector probe: {probe.get('status', 'unavailable')}; "
                f"verified vector searches: {probe.get('verified_successful_vector_searches', 0)}. "
                "Natural QA non-use is a valid observation when this separate probe passes; attempted QA calls with no success still fail validation. "
                "Setup usage is excluded from QA metrics. "
                "Trial measurements and rubric scores below remain unchanged.\n\n"
                "Smoke observations validate the workflow and are excluded from formal efficacy estimates.\n\n")
        if validation["status"] != "valid":
            note += "**The integration smoke did not pass; these observations do not establish a working zg comparison.**\n\n"
        text = text.replace("Coverage:", "Observation coverage:")
        markdown_path.write_text(note + text, encoding="utf-8")


def sdk_preflight(output: Path):
    """Validate actual zg SDK authorization/index/query before the full corpus."""
    from runner import docker_command, embedding_endpoint, run_named, with_embedding_environment
    source, logs, cache, index = [output / name for name in ("source", "runtime", "model-cache", "index")]
    for path in (source, logs, cache, index, source / ".zvec-grep", index / "locks"):
        path.mkdir(parents=True, exist_ok=True)
    (source / "probe.md").write_text("代码仓库问答：检索源代码并引用文件。 Repository code search finds source files.\n")
    for args in (["init", "-q", str(source)], ["-C", str(source), "add", "probe.md"],
                 ["-C", str(source), "-c", "user.name=Workspace QA", "-c", "user.email=benchmark@localhost",
                  "-c", "gc.auto=0", "commit", "-qm", "Synthetic SDK connectivity fixture"]):
        subprocess.run(["git", *args], check=True)
    image = "zg-readonly-qa:0.2.2"
    command = with_embedding_environment(docker_command(image, source, logs, cache, index=index), embedding_endpoint())
    try:
        run_named(command + [image, "node", "/opt/qa/embedding-probe.mjs"],
                  "workspaceqa-sdk-probe", timeout=240, diagnostic_path=logs / "failure.json")
        result = json.loads((logs / "result.json").read_text())
        if result.get("status") != "completed" or result.get("vector_query_retrieved_fixture") is not True:
            raise RuntimeError("zg SDK remote embedding probe did not complete")
        print(json.dumps({"phase": "sdk_preflight", "status": "completed", "wall_seconds": result["wall_seconds"]}), flush=True)
        from qoder_probe import qoder_mcp_preflight
        qoder_mcp_preflight(source, output / "qoder", cache, index)
    finally:
        for path in (source, cache, index):
            shutil.rmtree(path)


def embedding_preflight(output: Path):
    from runner import EMBEDDING, embedding_endpoint
    endpoint = embedding_endpoint()
    payload = {"model": EMBEDDING.split("/", 1)[1], "input": ["代码仓库问答 / repository question answering"],
               "dimensions": 1024, "encoding_format": "float"}
    started = time.monotonic()
    result = {"requested_model": payload["model"], "endpoint": endpoint, "dimension": 1024,
              "phase": "setup_connectivity_probe", "included_in_agent_tokens": False, "status": "failed"}
    try:
        req = request.Request(endpoint, data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["QWEN_API_KEY"]})
        with request.urlopen(req, timeout=90) as response:
            body = json.load(response)
        data = body.get("data", [])
        if len(data) != 1 or len(data[0].get("embedding", [])) != 1024:
            raise ValueError("Remote embedding preflight returned an unexpected vector shape")
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in data[0]["embedding"]):
            raise ValueError("Remote embedding preflight returned an invalid vector")
        if body.get("model") not in (None, payload["model"]):
            raise ValueError("Embedding endpoint returned a different model")
        result.update(status="completed", resolved_model=body.get("model"), usage=body.get("usage"),
                      request_sha256=hashlib.sha256(json.dumps(payload).encode()).hexdigest())
    except Exception as exc:
        result.update(error_type=type(exc).__name__)
        if isinstance(exc, error.HTTPError):
            result["http_status"] = exc.code
        raise
    finally:
        result["wall_seconds"] = time.monotonic() - started
        output.write_text(json.dumps(result, indent=2) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-id", required=True)
    p.add_argument("--repetitions", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--upstream", type=Path, required=True)
    p.add_argument("--phase", choices=("smoke", "batch"), required=True)
    args = p.parse_args(argv)
    lock = json.loads((HERE / "data/lock.json").read_text())
    task = next(t for t in lock["tasks"] if t["task_id"] == args.task_id)
    if args.repetitions < 1:
        raise ValueError("repetitions must be positive")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    preparation, runs = root / "dataset", root / "runs"
    from runner import make_plan, collect_results
    plan = make_plan(args.task_id, args.repetitions)
    # A setup failure must still account for every planned trial.
    root.joinpath("planned.json").write_text(json.dumps(plan, indent=2) + "\n")
    scoped = {**lock, "tasks": [task], "repetitions": args.repetitions}
    root.joinpath("selection.json").write_text(json.dumps(scoped, ensure_ascii=False, indent=2) + "\n")
    outcome = 1
    try:
        # Stop before downloads, while retaining the already-frozen trial ledger.
        for name in ("QODER_PERSONAL_ACCESS_TOKEN", "GLM_API_KEY", "QWEN_API_KEY"):
            if not os.environ.get(name):
                raise RuntimeError(f"Required GitHub Actions secret is missing: {name}")
        embedding_preflight(root / "embedding-preflight.json")
        print(json.dumps({"phase": "sdk_preflight", "status": "starting"}), flush=True)
        sdk_preflight(root / "sdk-preflight")
        print(json.dumps({"phase": "dataset_preparation", "status": "starting"}), flush=True)
        subprocess.run([sys.executable, str(HERE / "dataset.py"), "--task-id", args.task_id,
                        "--output", str(preparation), "--upstream", str(args.upstream)], check=True)
        print(json.dumps({"phase": "paired_trials", "status": "starting"}), flush=True)
        result = subprocess.run([sys.executable, str(HERE / "runner.py"), "--task-id", args.task_id,
                                 "--source-root", str(preparation / "source"), "--question-file", str(preparation / "question.txt"),
                                 "--answer-filename", task["answer_filename"], "--output", str(runs),
                                 "--repetitions", str(args.repetitions), "--timeout", "900"])
        # Retain and judge completed candidates even if a different trial failed.
        judged = subprocess.run([sys.executable, str(HERE / "judge.py"), "--metadata",
                                 str(preparation / "tasks" / args.task_id / "metadata.json"), "--task-dir",
                                 str(preparation / "tasks" / args.task_id), "--runs-dir", str(runs)])
        outcome = 0 if result.returncode == 0 and judged.returncode == 0 else 1
    except Exception as error:
        root.joinpath("setup-failure.json").write_text(json.dumps({"status": "failed", "error_type": type(error).__name__}) + "\n")
        raise
    finally:
        if not (runs / "trial-results.json").exists():
            runs.mkdir(parents=True, exist_ok=True)
            collect_results(runs, plan)
        validation = smoke_validation(runs, args.phase)
        root.joinpath("smoke_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n")
        if validation["status"] == "invalid":
            outcome = 1
        reported = subprocess.run([sys.executable, str(HERE / "report.py"), "--runs-dir", str(runs),
                                   "--manifest", str(root / "selection.json"), "--output", str(root / "report"), "--require-complete"])
        if reported.returncode:
            outcome = 1
        annotate_smoke_report(root / "report", validation)
    return outcome


if __name__ == "__main__":
    raise SystemExit(main())
