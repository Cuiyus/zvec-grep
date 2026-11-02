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
EVIDENCE_ADAPTERS = {
    runner.OPENCODE_IMPORT_PATH: "zg_bench.agents.case_evidence:CaseEvidenceBaselineOpenCode",
    runner.ZVEC_OPENCODE_IMPORT_PATH: "zg_bench.agents.case_evidence:CaseEvidenceOpenCode",
}
ADAPTER_PROFILES = {
    runner.OPENCODE_IMPORT_PATH: "baseline",
    EVIDENCE_ADAPTERS[runner.OPENCODE_IMPORT_PATH]: "baseline",
    runner.ZVEC_OPENCODE_IMPORT_PATH: "zvec-grep",
    EVIDENCE_ADAPTERS[runner.ZVEC_OPENCODE_IMPORT_PATH]: "zvec-grep",
}


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
    adapter = result[index]
    if adapter not in EVIDENCE_ADAPTERS:
        raise ValueError("case recording requires a supported OpenCode adapter")
    result[index] = EVIDENCE_ADAPTERS[adapter]
    for index, value in enumerate(result):
        if value.startswith("opencode_config="):
            config = json.loads(value.split("=", 1)[1])
            options = config["provider"]["custom-openai"]["options"]
            upstream = options["baseURL"]
            options["baseURL"] = "http://127.0.0.1:18768"
            if adapter == runner.ZVEC_OPENCODE_IMPORT_PATH:
                config["mcp"]["zvec_grep"]["url"] = "http://127.0.0.1:18769/mcp"
            result[index] = "opencode_config=" + json.dumps(config, separators=(",", ":"))
            result.extend(["--agent-kwarg", "evidence_upstream=" + upstream])
            return result
    raise ValueError("missing pinned OpenCode provider config")


def run_plan(stage: str, profile: str) -> list[dict]:
    """Keep the old single-profile layout; paired jobs use collector suffixes."""
    profiles = runner.selected_profiles(profile)
    attempts = 1 if stage == "preflight" else 10
    return [{"profile": item, "job_name": f"{stage}-{item}" if len(profiles) > 1 else stage,
             "attempts": attempts} for item in profiles]


def trial_profile(trial: Path) -> str:
    """Use recorded agent identity and reject conflicts with profile job names."""
    config = json.loads((trial / "config.json").read_text())
    profile = ADAPTER_PROFILES.get(config.get("agent", {}).get("name"))
    if profile is None:
        job_config = json.loads((trial.parent / "config.json").read_text())
        candidates = {ADAPTER_PROFILES.get(agent.get("name")) for agent in job_config.get("agents", [])}
        candidates.discard(None)
        if len(candidates) == 1:
            profile = candidates.pop()
    named = next((item for item in runner.PROFILES if trial.parent.name.endswith("-" + item)), None)
    if profile is None or (named is not None and named != profile):
        raise ValueError("missing or conflicting profile identity")
    return profile


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


def is_title_request(meta: dict, body: dict, completion: dict) -> bool:
    """Recognize OpenCode's auxiliary title job, never a task or tool request."""
    messages = body.get("messages", [])
    return (
        meta.get("route") == "model" and meta.get("method") == "POST"
        and completion.get("status") == 200 and not body.get("tools")
        and len(messages) in (2, 3)
        and messages[0].get("role") == "system"
        and isinstance(messages[0].get("content"), str)
        and messages[0]["content"].startswith(
            "You are a title generator. You output ONLY a thread title. Nothing else.")
        and messages[1].get("role") == "user"
        and isinstance(messages[1].get("content"), str)
        and messages[1]["content"].startswith("Generate a title for this conversation:")
        and all(m.get("role") == "user" and isinstance(m.get("content"), str) for m in messages[2:])
    )


def inspect_trial(trial: Path, root: Path) -> tuple[dict, list[dict]]:
    agent = trial / "agent"
    errors, queries = [], []
    row = {"trial": trial.name, "path": str(trial.relative_to(root))}
    try:
        row["profile"] = trial_profile(trial)
    except (OSError, ValueError, KeyError, TypeError):
        row["profile"] = None
        errors.append("missing_or_conflicting_profile")

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
    errors.extend("session_usage:" + error for error in usage.get("errors", []))
    if not usage.get("complete"):
        errors.append("session_usage_incomplete")
    total = usage.get("total")
    if not isinstance(total, dict) or any(
        not isinstance(total.get(field), int) or isinstance(total.get(field), bool) or total[field] < 0
        for field in ("input_tokens", "output_tokens", "llm_calls", "tool_calls")
    ):
        errors.append("session_usage_totals_invalid")
    row["usage"] = usage.get("total")
    row["session_count"] = len(session_ids)
    row["session_ids"] = sorted(session_ids)
    row["execution_complete"] = usage.get("complete", False)
    model_calls, mcp_calls, task_model_calls, auxiliary_model_calls = 0, 0, 0, 0
    incomplete_auxiliary_wires = []
    for meta_path in sorted((agent / "wire").glob("*/*.metadata.json")):
        prefix = str(meta_path).removesuffix(".metadata.json")
        try:
            meta = json.loads(meta_path.read_text())
            request = Path(prefix + ".request.bin").read_text()
            completion = json.loads(Path(prefix + ".completion.json").read_text())
            body = json.loads(request) if meta["route"] == "model" else None
            auxiliary = body is not None and is_title_request(meta, body, completion)
            if not completion.get("complete"):
                # Preserve partial background title bytes. Task POSTs must finish.
                if auxiliary:
                    incomplete_auxiliary_wires.append(meta["id"])
                elif meta["method"] != "GET":
                    errors.append("incomplete_wire:" + meta["id"])
            response_path = Path(prefix + ".response.bin")
            if not response_path.exists() and meta["method"] == "GET":
                continue
            response = decode_response(response_path, completion)
            if meta["route"] == "model":
                if "messages" not in body:
                    errors.append("model_messages_missing:" + meta["id"])
                model_calls += 1
                auxiliary_model_calls += int(auxiliary)
                task_model_calls += int(not auxiliary)
            else:
                for call in rpc_messages(request):
                    if call.get("method") != "tools/call":
                        continue
                    mcp_calls += 1
                    responses = [r for r in rpc_messages(response) if r.get("id") == call.get("id")]
                    queries.append({
                        "trial": trial.name, "profile": row["profile"], "wire_id": meta["id"],
                        "started_ms": meta["started_ms"], "request": call,
                        "responses": responses,
                        "raw_prefix": str(Path(prefix).relative_to(root)),
                        "analysis": "pending_source_review",
                    })
                    if len(responses) != 1:
                        errors.append("missing_or_ambiguous_rpc_response:" + meta["id"])
        except (OSError, ValueError, KeyError) as error:
            errors.append("invalid_wire:" + meta_path.name + ":" + type(error).__name__)
    if not task_model_calls:
        errors.append("no_model_requests")
    if row["profile"] == "baseline" and mcp_calls:
        errors.append("baseline_has_mcp_calls")
    result = trial / "result.json"
    try:
        raw = json.loads(result.read_text())
        row["verifier_result"] = raw.get("verifier_result")
        row["exception_info"] = raw.get("exception_info")
        if raw.get("exception_info") is not None:
            errors.append("trial_exception")
        if not raw.get("finished_at"):
            errors.append("trial_incomplete")
    except (OSError, ValueError):
        errors.append("missing_or_invalid_trial_result")
    row.update(model_calls=model_calls, task_model_calls=task_model_calls,
               auxiliary_model_calls=auxiliary_model_calls,
               incomplete_auxiliary_wires=incomplete_auxiliary_wires,
               mcp_calls=mcp_calls, evidence_complete=not errors, errors=errors)
    return row, queries


def capture_report(output: Path, expected: int) -> tuple[dict, list[dict]]:
    trials = sorted({p.parent for p in (output / "jobs").glob("*/*/config.json")})
    rows, queries = [], []
    for trial in trials:
        row, found = inspect_trial(trial, output)
        rows.append(row)
        queries.extend(found)
    session_ids = [sid for row in rows for sid in row["session_ids"]]
    errors = ["reused_session_across_trials"] if len(session_ids) != len(set(session_ids)) else []
    profiles = {profile: sum(row["profile"] == profile for row in rows) for profile in runner.PROFILES}
    lock_path = output / "run-lock.json"
    if lock_path.exists():
        try:
            lock = json.loads(lock_path.read_text())
            expected_profiles = lock.get("profiles", ["zvec-grep"])
            attempts = lock["attempts"]
            if expected != attempts * len(expected_profiles):
                errors.append("expected_count_differs_from_run_lock")
            for profile in runner.PROFILES:
                if profiles[profile] != (attempts if profile in expected_profiles else 0):
                    errors.append("profile_trial_count:" + profile)
        except (OSError, ValueError, KeyError, TypeError):
            errors.append("invalid_run_lock")
    report = {"schema_version": 1, "expected_trials": expected, "observed_trials": len(rows),
              "capture_ready": not errors and len(rows) == expected and all(r["evidence_complete"] for r in rows),
              "errors": errors, "profile_trial_counts": profiles,
              "source_review_complete": False, "trials": rows}
    return report, queries


def assess_capture(evidence: Path, expected: int) -> dict:
    """Reassess immutable evidence; the caller saves the result elsewhere."""
    manifest = json.loads((evidence / "evidence-manifest.json").read_text())
    for entry in manifest:
        path = (evidence / entry["path"]).resolve()
        if not path.is_relative_to(evidence.resolve()):
            raise ValueError("manifest path outside evidence")
        if path.stat().st_size != entry["size"] or sha256(path) != entry["sha256"]:
            raise ValueError("immutable evidence hash mismatch: " + entry["path"])
    report, queries = capture_report(evidence, expected)
    lock_path = evidence / "run-lock.json"
    case = json.loads(lock_path.read_text()).get("case", {}) if lock_path.exists() else {}
    attestations = json.loads(DATA.with_name("evidence-directory-symlinks.json").read_text())
    attested = next((a for a in attestations if a["repository"] == case.get("repository")
                     and a["commit"] == case.get("repository_commit")), None)
    source_gaps = []
    if attested:
        allowed = {entry["path"]: entry for entry in attested["entries"]}
        for entry in allowed.values():
            target = entry["target"].encode()
            if entry["mode"] != "120000" or hashlib.sha1(
                b"blob " + str(len(target)).encode() + b"\0" + target
            ).hexdigest() != entry["git_blob_sha1"]:
                raise ValueError("invalid committed directory-symlink attestation")
        for row in report["trials"]:
            agent = evidence / row["path"] / "agent"
            before = json.loads((agent / "corpus-before.json").read_text())
            after = json.loads((agent / "corpus-after.json").read_text())
            errors = [entry for entry in before.get("files", []) if "error" in entry]
            if (errors and before == after and before.get("commit") == attested["commit"]
                    and all(entry == {"path": entry["path"], "error": "EISDIR"}
                            and entry["path"] in allowed for entry in errors)):
                row["errors"] = [error for error in row["errors"] if error != "corpus_hash_errors"]
                row["evidence_complete"] = not row["errors"]
                source_gaps.append({"trial": row["trial"], "paths": [entry["path"] for entry in errors]})
    if source_gaps:
        report["directory_symlink_capture_gap"] = {
            "attestation": attested, "trials": source_gaps,
            "original_working_tree_link_targets_captured": False,
            "qualification": "Committed directory symlinks explain EISDIR. Original E2E link targets were not captured; this metadata gap remains, and no source-content mismatch or other read error is waived.",
        }
        report["capture_ready"] = (not report["errors"] and report["observed_trials"] == expected
                                   and all(row["evidence_complete"] for row in report["trials"]))
    original_queries = [json.loads(line) for line in (evidence / "requests.jsonl").read_text().splitlines()]
    if queries != original_queries:
        raise ValueError("reassessment changed recorded requests")
    return {"schema_version": 1, "original_manifest_sha256": sha256(evidence / "evidence-manifest.json"),
            "original_capture_summary_sha256": sha256(evidence / "capture-summary.json"),
            "raw_evidence_modified": False, "report": report,
            "qualification": "Known auxiliary title streams may be partial; task model/MCP responses, sessions, usage and trial completion remain required."
            + (" Original working-tree targets of attested directory symlinks were not captured; see report.directory_symlink_capture_gap." if source_gaps else "")}


def inventory(output: Path, expected: int) -> dict:
    report, queries = capture_report(output, expected)
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
    run.add_argument("--profile", choices=["baseline", "zvec-grep", "all"], default="zvec-grep")
    run.add_argument("--package", type=Path, required=True)
    run.add_argument("--candidate-commit", required=True)
    run.add_argument("--output", type=Path, required=True)
    inspect = sub.add_parser("inventory")
    inspect.add_argument("--output", type=Path, required=True)
    inspect.add_argument("--expected", type=int, required=True)
    assess = sub.add_parser("assess")
    assess.add_argument("--evidence", type=Path, required=True)
    assess.add_argument("--expected", type=int, required=True)
    assess.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "inventory":
        return 0 if inventory(args.output, args.expected)["capture_ready"] else 1
    if args.command == "assess":
        if args.output.exists() or args.output.resolve().is_relative_to(args.evidence.resolve()):
            parser.error("assessment output must be new and outside immutable evidence")
        assessment = assess_capture(args.evidence, args.expected)
        write_json(args.output, assessment)
        return 0 if assessment["report"]["capture_ready"] else 1
    selection = json.loads(DATA.read_text())
    case = next((c for c in selection["tasks"] if c["task_slug"] == args.task), None)
    if not case:
        parser.error("task must be in the checked-in SWE-QA selection")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("output must be empty; failed attempts must not be overwritten")
    output.mkdir(parents=True, exist_ok=True)
    plan = run_plan(args.stage, args.profile)
    attempts = plan[0]["attempts"]
    profiles = [item["profile"] for item in plan]
    write_json(output / "run-lock.json", {
        "schema_version": 1, "stage": args.stage, "case": case, "attempts": attempts,
        "profiles": profiles, "jobs": plan, "expected_trials": attempts * len(profiles),
        "max_retries": 0, "zg_commit": args.candidate_commit,
        "package_sha256": sha256(args.package),
        "harness_commit": os.environ.get("HARNESS_COMMIT"), "opencode_version": OPENCODE_VERSION,
        "model": MODEL, "embedding": EMBEDDING, "device": "cpu",
        "benchmark_revision": selection["benchmark_revision"],
    })
    suite = runner.load_suite("swe-qa-bench", tier="full", task_overrides=[args.task])
    environment = runner.execution_environment(agent="opencode", model=MODEL)
    exits = {}
    try:
        for item in plan:
            profile = item["profile"]
            exits[profile] = {"exit_code": 1}
            try:
                prepared = runner.prepare_setup_cache("opencode", profile,
                    zvec_grep_package=str(args.package), embedding_model=EMBEDDING)
                command = recording_command(runner.build_harbor_command(
                    suite, profile=profile, agent="opencode", model=MODEL,
                    jobs_dir=output / "jobs", job_name=item["job_name"], n_attempts=attempts, max_retries=0,
                    zvec_grep_package=prepared.zvec_grep_package,
                    zvec_grep_package_sha256=prepared.zvec_grep_package_sha256, embedding_model=EMBEDDING,
                ))
                write_json(output / "commands" / f"{profile}.json", command)
                if len(plan) == 1:
                    write_json(output / "command.json", command)
                exits[profile]["exit_code"] = runner.execute(command, jobs_dir=output / "jobs", environment=environment)
            except Exception as error:
                exits[profile]["error"] = type(error).__name__
            finally:
                write_json(output / "execution-results.json", exits)
    finally:
        report = inventory(output, attempts * len(profiles))
        print(json.dumps({"harbor_exits": exits, "capture_ready": report["capture_ready"],
                          "observed_trials": report["observed_trials"]}), flush=True)
    return 0 if report["capture_ready"] and all(item["exit_code"] == 0 for item in exits.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
