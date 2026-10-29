"""Single-case execution and lossless evidence inventory (no LLM grading)."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys

from . import runner
from .settings import OPENCODE_VERSION

MODEL = "custom-openai/qwen3.8-max"
EMBEDDING = "local/potion-code-16m-v2"
DATA = Path(__file__).parent / "swe_qa/data/selection.json"


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def recording_command(command: list[str]) -> list[str]:
    """Alter transport only; retain every provider/model option and MCP setting."""
    result = list(command)
    index = result.index("--agent") + 1
    if result[index] != runner.ZVEC_OPENCODE_IMPORT_PATH:
        raise ValueError("case recording requires the ZG OpenCode adapter")
    result[index] = "zg_bench.agents.case_evidence:CaseEvidenceOpenCode"
    for index, value in enumerate(result):
        if value.startswith("opencode_config="):
            config = json.loads(value.split("=", 1)[1])
            options = config["provider"]["custom-openai"]["options"]
            upstream = options["baseURL"]
            options["baseURL"] = "http://127.0.0.1:18768"
            config["mcp"]["zvec_grep"]["url"] = "http://127.0.0.1:18769/mcp"
            result[index] = "opencode_config=" + json.dumps(config, separators=(",", ":"))
            result.extend(["--agent-kwarg", "evidence_upstream=" + upstream])
            return result
    raise ValueError("missing pinned OpenCode provider config")


def decode_response(path: Path, completion: dict) -> str:
    data = path.read_bytes()
    encoding = completion.get("response_headers", {}).get("content-encoding", "")
    if encoding == "gzip":
        data = gzip.decompress(data)
    elif encoding not in ("", "identity"):
        raise ValueError("unsupported response encoding: " + encoding)
    return data.decode("utf-8")


def rpc_messages(body: str) -> list[dict]:
    """MCP JSON or SSE; preserve raw bytes alongside this navigation index."""
    if not body.strip():
        return []
    if body.lstrip().startswith(("{", "[")):
        parsed = json.loads(body)
        return parsed if isinstance(parsed, list) else [parsed]
    messages = []
    for event in body.replace("\r\n", "\n").split("\n\n"):
        payload = "\n".join(line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:"))
        if payload and payload != "[DONE]":
            messages.append(json.loads(payload))
    return messages


def inspect_trial(trial: Path, root: Path) -> tuple[dict, list[dict]]:
    agent = trial / "agent"
    errors, queries = [], []
    row = {"trial": trial.name, "path": str(trial.relative_to(root))}

    def read(name, default=None):
        try:
            return json.loads((agent / name).read_text())
        except (OSError, ValueError):
            errors.append("missing_or_invalid:" + name)
            return default

    capture = read("evidence-capture.json", {})
    if not capture.get("complete"):
        errors.append("capture_incomplete")
    before, after = read("corpus-before.json"), read("corpus-after.json")
    if before != after:
        errors.append("corpus_changed")
    if before and any("error" in entry for entry in before.get("files", [])):
        errors.append("corpus_hash_errors")
    sessions = read("full-sessions.json", [])
    sessions = [{**r, "data": json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]} for r in sessions]
    session_ids = {r["data"]["id"] for r in sessions if r["kind"] == "session"}
    for entry in sessions:
        data = entry["data"]
        if entry["kind"] == "session" and data.get("parent_id") and data["parent_id"] not in session_ids:
            errors.append("missing_parent:" + data["parent_id"])
        if entry["kind"] != "session" and data["session_id"] not in session_ids:
            errors.append("missing_session:" + data["session_id"])
    if not session_ids or not any(r["kind"] == "message" for r in sessions):
        errors.append("empty_sessions")
    usage = read("session-usage.json", {})
    errors.extend("session_usage:" + error for error in usage.get("errors", []) if error != "capture_incomplete")
    row["usage"] = usage.get("total")
    row["session_count"] = len(session_ids)
    row["session_ids"] = sorted(session_ids)
    row["execution_complete"] = usage.get("complete", False)
    model_calls, mcp_calls = 0, 0
    for meta_path in sorted((agent / "wire").glob("*/*.metadata.json")):
        prefix = str(meta_path).removesuffix(".metadata.json")
        try:
            meta = json.loads(meta_path.read_text())
            request = Path(prefix + ".request.bin").read_text()
            completion = json.loads(Path(prefix + ".completion.json").read_text())
            if not completion.get("complete"):
                # A long-lived MCP GET may be closed at teardown; POST bodies must finish.
                if meta["method"] != "GET":
                    errors.append("incomplete_wire:" + meta["id"])
            response_path = Path(prefix + ".response.bin")
            if not response_path.exists() and meta["method"] == "GET":
                continue
            response = decode_response(response_path, completion)
            if meta["route"] == "model":
                body = json.loads(request)
                if "messages" not in body:
                    errors.append("model_messages_missing:" + meta["id"])
                model_calls += 1
            else:
                for call in rpc_messages(request):
                    if call.get("method") != "tools/call":
                        continue
                    mcp_calls += 1
                    responses = [r for r in rpc_messages(response) if r.get("id") == call.get("id")]
                    queries.append({
                        "trial": trial.name, "wire_id": meta["id"],
                        "started_ms": meta["started_ms"], "request": call,
                        "responses": responses,
                        "raw_prefix": str(Path(prefix).relative_to(root)),
                        "analysis": "pending_source_review",
                    })
                    if len(responses) != 1:
                        errors.append("missing_or_ambiguous_rpc_response:" + meta["id"])
        except (OSError, ValueError, KeyError) as error:
            errors.append("invalid_wire:" + meta_path.name + ":" + type(error).__name__)
    if not model_calls:
        errors.append("no_model_requests")
    row.update(model_calls=model_calls, mcp_calls=mcp_calls, evidence_complete=not errors, errors=errors)
    result = trial / "result.json"
    if result.exists():
        raw = json.loads(result.read_text())
        row["verifier_result"] = raw.get("verifier_result")
        row["exception_info"] = raw.get("exception_info")
    else:
        errors.append("missing_trial_result")
        row["evidence_complete"] = False
    return row, queries


def inventory(output: Path, expected: int) -> dict:
    trials = sorted({p.parent for p in (output / "jobs").glob("*/*/config.json")})
    rows, queries = [], []
    for trial in trials:
        row, found = inspect_trial(trial, output)
        rows.append(row)
        queries.extend(found)
    session_ids = [sid for row in rows for sid in row["session_ids"]]
    errors = ["reused_session_across_trials"] if len(session_ids) != len(set(session_ids)) else []
    report = {"schema_version": 1, "expected_trials": expected, "observed_trials": len(rows),
              "capture_ready": not errors and len(rows) == expected and all(r["evidence_complete"] for r in rows),
              "errors": errors,
              "source_review_complete": False, "trials": rows}
    write_json(output / "capture-summary.json", report)
    (output / "requests.jsonl").write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in queries))
    files = [{"path": str(p.relative_to(output)), "size": p.stat().st_size,
              "sha256": sha256(p)}
             for p in sorted(output.rglob("*")) if p.is_file() and p.name != "evidence-manifest.json"]
    write_json(output / "evidence-manifest.json", files)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--task", required=True)
    run.add_argument("--stage", choices=["preflight", "formal"], required=True)
    run.add_argument("--package", type=Path, required=True)
    run.add_argument("--candidate-commit", required=True)
    run.add_argument("--output", type=Path, required=True)
    inspect = sub.add_parser("inventory")
    inspect.add_argument("--output", type=Path, required=True)
    inspect.add_argument("--expected", type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == "inventory":
        return 0 if inventory(args.output, args.expected)["capture_ready"] else 1
    selection = json.loads(DATA.read_text())
    case = next((c for c in selection["tasks"] if c["task_slug"] == args.task), None)
    if not case:
        parser.error("task must be in the checked-in SWE-QA selection")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("output must be empty; failed attempts must not be overwritten")
    output.mkdir(parents=True, exist_ok=True)
    attempts = 1 if args.stage == "preflight" else 10
    write_json(output / "run-lock.json", {
        "schema_version": 1, "stage": args.stage, "case": case, "attempts": attempts,
        "max_retries": 0, "zg_commit": args.candidate_commit,
        "package_sha256": sha256(args.package),
        "harness_commit": os.environ.get("HARNESS_COMMIT"), "opencode_version": OPENCODE_VERSION,
        "model": MODEL, "embedding": EMBEDDING, "device": "cpu",
        "benchmark_revision": selection["benchmark_revision"],
    })
    suite = runner.load_suite("swe-qa-bench", tier="full", task_overrides=[args.task])
    prepared = runner.prepare_setup_cache("opencode", "zvec-grep", zvec_grep_package=str(args.package), embedding_model=EMBEDDING)
    command = recording_command(runner.build_harbor_command(
        suite, profile="zvec-grep", agent="opencode", model=MODEL,
        jobs_dir=output / "jobs", job_name=args.stage, n_attempts=attempts, max_retries=0,
        zvec_grep_package=prepared.zvec_grep_package,
        zvec_grep_package_sha256=prepared.zvec_grep_package_sha256, embedding_model=EMBEDDING,
    ))
    write_json(output / "command.json", command)
    environment = runner.execution_environment(agent="opencode", model=MODEL)
    code = 1
    try:
        code = runner.execute(command, jobs_dir=output / "jobs", environment=environment)
    finally:
        report = inventory(output, attempts)
        print(json.dumps({"harbor_exit": code, "capture_ready": report["capture_ready"],
                          "observed_trials": report["observed_trials"]}), flush=True)
    return 0 if report["capture_ready"] and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
