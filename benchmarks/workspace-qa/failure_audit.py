#!/usr/bin/env python3
"""Describe planned trial outcomes and failure evidence without changing verdicts."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from typing import Any


PROFILES = ("baseline", "with-zg")
COMPLETED = {"completed", "success", "succeeded"}
TERMINAL = COMPLETED | {"budget_exhausted", "timeout", "failed", "launch_failure", "protocol_failure",
                        "conversion_failure", "measurement_failure", "integrity_failure", "contract_failure"}
INFRASTRUCTURE_OR_PROTOCOL = {"launch_failure", "protocol_failure", "conversion_failure",
                              "measurement_failure", "integrity_failure", "contract_failure"}


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def numeric(value: Any) -> int | float | None:
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def display_path(path: Path, root: Path) -> str:
    path, root = path.resolve(), root.resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def build_audit(runs_dir: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = read_object(manifest_path)
    tasks, repetitions = manifest.get("tasks"), manifest.get("repetitions")
    if not isinstance(tasks, list) or not tasks or type(repetitions) is not int or repetitions < 1:
        raise ValueError("Manifest requires selected tasks and positive repetitions")
    task_ids = []
    for task in tasks:
        task_id = task.get("task_id") if isinstance(task, dict) else None
        if not isinstance(task_id, str) or not task_id or task_id in task_ids:
            raise ValueError("Manifest task IDs must be unique nonempty strings")
        task_ids.append(task_id)
    planned = {(task, profile, repetition) for task in task_ids for profile in PROFILES for repetition in range(1, repetitions + 1)}
    records: dict[tuple[str, str, int], tuple[dict, Path]] = {}
    conflicts, seen_tasks, unsharded_tasks, declared_repetitions, anomalies = set(), set(), set(), {}, []
    for path in sorted(runs_dir.rglob("trial-results.json")):
        relative = display_path(path, runs_dir)
        try:
            ledger = read_object(path)
            task_id = ledger.get("task_id")
            if task_id not in task_ids or ledger.get("repetitions_per_profile") != repetitions or not isinstance(ledger.get("trials"), list):
                raise ValueError("Ledger does not match the locked task/repetition plan")
            shard = ledger.get("shard_repetitions")
            if shard is None:
                if task_id in seen_tasks:
                    raise ValueError("Duplicate task ledger; do not combine attempts or smoke artifacts")
                declared = set(range(1, repetitions + 1))
                unsharded_tasks.add(task_id)
            else:
                prior = declared_repetitions.setdefault(task_id, set())
                if (not isinstance(shard, list) or not shard or len(shard) != len(set(shard))
                        or any(type(value) is not int or not 1 <= value <= repetitions for value in shard)
                        or task_id in unsharded_tasks or prior.intersection(shard)):
                    raise ValueError("Invalid or overlapping task shard")
                declared = set(shard)
                prior.update(declared)
            seen_tasks.add(task_id)
            if shard is not None and {trial.get("repetition") for trial in ledger["trials"]} != declared:
                raise ValueError("Task shard trials differ from declared repetitions")
            for trial in ledger["trials"]:
                if not isinstance(trial, dict):
                    raise ValueError("Trial row is not an object")
                key = (trial.get("task_id"), trial.get("profile"), trial.get("repetition"))
                if type(trial.get("repetition")) is not int or key not in planned or key[0] != task_id:
                    raise ValueError("Trial does not match the locked plan")
                trial_id = trial.get("trial_id")
                if not isinstance(trial_id, str) or not trial_id or Path(trial_id).name != trial_id or trial_id in {".", ".."}:
                    raise ValueError("Unsafe or missing trial ID")
                if key in records:
                    conflicts.add(key)
                    raise ValueError("Duplicate planned trial")
                records[key] = (trial, path)
        except (OSError, ValueError, TypeError) as exc:
            anomalies.append({"path": relative, "error_type": type(exc).__name__, "reason": str(exc)})
    rows = []
    for task_id, profile, repetition in sorted(planned):
        key = (task_id, profile, repetition)
        record = records.get(key)
        row: dict[str, Any] = {"task_id": task_id, "profile": profile, "repetition": repetition,
            "trial_id": None, "execution_status": "missing_record", "session_status": None,
            "category": "missing_evidence", "attempted": None, "terminal_recorded": False,
            "qa_completed": False, "ledger_path": None, "session_path": None,
            "session_evidence": None, "limit_reason": None, "limit_threshold": None,
            "input_tokens": None, "input_tokens_observed_lower_bound": None,
            "input_usage_missing_turns": None, "invalid_usage_events": None,
            "tool_calls": None, "wall_seconds": None, "model_requests": None,
            "zg_tool_calls": None, "zg_tool_calls_successful": None,
            "returncode": None, "error_type": None, "setup_failure": None}
        if record is None or key in conflicts:
            if key in conflicts:
                row.update(execution_status="conflicting_records", category="invalid_evidence")
            rows.append(row)
            continue
        trial, ledger_path = record
        trial_id, status = trial["trial_id"], trial.get("status", "unknown")
        row.update(trial_id=trial_id, execution_status=status, ledger_path=display_path(ledger_path, runs_dir),
                   input_tokens=numeric(trial.get("input_tokens")), tool_calls=numeric(trial.get("tool_calls")),
                   wall_seconds=numeric(trial.get("wall_seconds")), model_requests=numeric(trial.get("model_requests")),
                   zg_tool_calls=numeric(trial.get("zg_tool_calls")),
                   zg_tool_calls_successful=numeric(trial.get("zg_tool_calls_successful")),
                   returncode=trial.get("returncode"), error_type=trial.get("error_type"))
        agent = (ledger_path.parent / trial_id / "agent").resolve()
        if not agent.is_relative_to(ledger_path.parent.resolve()):
            anomalies.append({"path": row["ledger_path"], "reason": "Trial evidence path escapes its run directory"})
            row.update(category="invalid_evidence")
            rows.append(row)
            continue
        session_path, native_path = agent / "session.json", agent / "qodercli-stream.jsonl"
        session = trial.get("session") if isinstance(trial.get("session"), dict) else None
        if session is not None:
            row["session_evidence"] = "embedded_in_ledger"
        if session_path.is_file():
            row["session_path"] = display_path(session_path, runs_dir.resolve())
            try:
                sidecar = read_object(session_path)
                row["session_sha256"] = hashlib.sha256(session_path.read_bytes()).hexdigest()
                if session is not None and sidecar != session:
                    anomalies.append({"path": row["session_path"], "reason": "Session sidecar differs from embedded ledger session"})
                    session = None
                    row["session_evidence"] = "conflicting"
                else:
                    session = sidecar
                    row["session_evidence"] = "session_file"
            except (OSError, ValueError) as exc:
                anomalies.append({"path": row["session_path"], "error_type": type(exc).__name__, "reason": "Session file could not be read"})
        if session is not None:
            observed = session.get("observed") if isinstance(session.get("observed"), dict) else {}
            limits = session.get("limits") if isinstance(session.get("limits"), dict) else {}
            limit_reason = session.get("limit_reason")
            row.update(session_status=session.get("status"),
                       limit_reason=limit_reason if isinstance(limit_reason, str) else None,
                       limit_threshold=numeric(limits.get(limit_reason)) if isinstance(limit_reason, str) else None,
                       input_tokens_observed_lower_bound=numeric(observed.get("input_tokens_observed_lower_bound")),
                       input_usage_missing_turns=numeric(observed.get("input_usage_missing_turns")),
                       invalid_usage_events=numeric(observed.get("invalid_usage_events")),
                       session_input_tokens=numeric(observed.get("input_tokens")),
                       session_model_requests=numeric(observed.get("model_requests")),
                       session_tool_calls=numeric(observed.get("tool_calls")),
                       session_wall_seconds=numeric(session.get("wall_seconds")),
                       session_returncode=session.get("returncode"), limits=limits)
        setup_path = ledger_path.parent.parent / "setup-failure.json"
        if setup_path.is_file():
            try:
                setup = read_object(setup_path)
                row["setup_failure"] = {"path": display_path(setup_path, runs_dir), "status": setup.get("status"), "error_type": setup.get("error_type")}
            except (OSError, ValueError) as exc:
                anomalies.append({"path": str(setup_path), "error_type": type(exc).__name__, "reason": "Setup failure artifact is unreadable"})
        has_native = native_path.is_file() and native_path.stat().st_size > 0
        started = bool(trial.get("started_at")) or session is not None or has_native
        row["terminal_recorded"] = status in TERMINAL
        row["qa_completed"] = status in COMPLETED
        if status in TERMINAL:
            row["attempted"] = True
            row["category"] = ("qa_completed" if row["qa_completed"] else "budget_exhausted" if status == "budget_exhausted"
                               else "runner_timeout" if status == "timeout"
                               else "infrastructure_or_protocol_failure" if status in INFRASTRUCTURE_OR_PROTOCOL
                               else "failure_cause_unknown")
        elif status in {"planned", "pending"} and not started:
            row.update(attempted=False, category="not_started")
        elif status == "running" or started:
            row.update(attempted=True, category="no_final_trial_record")
        else:
            row["category"] = "unknown_status"
        row["diagnostics"] = {key: trial[key] for key in ("error", "conversion_error", "metrics_error", "index_verification_error",
                                                        "source_unchanged", "original_seed_unchanged", "working_index_semantic_unchanged") if key in trial}
        rows.append(row)
    def summarize(items: list[dict]) -> dict:
        expected = len(items)
        terminal = sum(row["terminal_recorded"] for row in items)
        completed = sum(row["qa_completed"] for row in items)
        return {"planned": expected, "attempted": sum(row["attempted"] is True for row in items),
                "not_started": sum(row["attempted"] is False for row in items),
                "attempt_status_unknown": sum(row["attempted"] is None for row in items),
                "terminal_recorded": terminal, "qa_completed": completed,
                "terminal_record_coverage": terminal / expected if expected else None,
                "qa_completion_rate": completed / expected if expected else None,
                "categories": dict(Counter(row["category"] for row in items)),
                "execution_status_counts": dict(Counter(row["execution_status"] for row in items))}
    summary = summarize(rows)
    return {"schema_version": 1, "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "summary": summary, "by_profile": {profile: summarize([row for row in rows if row["profile"] == profile]) for profile in PROFILES},
            "execution_coverage_complete": summary["terminal_recorded"] == len(rows) and not anomalies,
            "does_not_change_ci_verdict_or_retry_policy": True,
            "definitions": {"execution_coverage": "All planned trials have a recorded terminal outcome, including failures; this is not GitHub workflow success.",
                            "qa_completion": "Runner recorded a completed answer; no claim that the answer is correct or that judging is complete.",
                            "unknown": "Missing evidence stays unknown. Explicit planned trials without start evidence are not_started.",
                            "tokens": "input_tokens is the reported final total, never replaced by the separately labelled observed lower bound.",
                            "failure_policy": "Budget exhaustion and low scores remain original outcomes; this audit does not retry or change budgets."},
            "artifact_anomalies": anomalies, "trials": rows,
            "failures": [row for row in rows if not row["qa_completed"]]}


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = ["# Trial execution and failure audit", "",
             f"Planned trials: **{summary['planned']}**. Recorded terminal outcomes: **{summary['terminal_recorded']}**. QA answers completed: **{summary['qa_completed']}**.",
             "Execution coverage includes recorded failures. QA completion does not establish answer correctness, judge completeness, or GitHub workflow success.",
             "Budget exhaustion remains a test outcome; no failed trial is retried or reclassified by this audit. Final token totals and observed lower bounds are separate.", "",
             "| Profile | Planned | Attempted | Terminal outcomes | QA completed | Not started | Unknown attempt status |", "|---|---:|---:|---:|---:|---:|---:|"]
    for profile, item in report["by_profile"].items():
        lines.append(f"| {profile} | {item['planned']} | {item['attempted']} | {item['terminal_recorded']} | {item['qa_completed']} | {item['not_started']} | {item['attempt_status_unknown']} |")
    lines.extend(["", "| Trial | Status | Limit reason | Observed input lower bound | Missing usage turns | Tool calls | Wall seconds |", "|---|---|---|---:|---:|---:|---:|"])
    def cell(value: Any) -> str:
        return "unknown" if value is None else str(value).replace("|", "\\|").replace("\n", " ")
    for row in report["failures"]:
        identity = row["trial_id"] or f"{row['task_id']} / {row['profile']} / {row['repetition']}"
        lines.append("| " + " | ".join(cell(value) for value in (identity, row["execution_status"], row["limit_reason"], row["input_tokens_observed_lower_bound"], row["input_usage_missing_turns"], row["tool_calls"], row["wall_seconds"])) + " |")
    if report["artifact_anomalies"]:
        lines.extend(["", f"Artifact anomalies: **{len(report['artifact_anomalies'])}**. See summary.json; execution coverage is not certified complete."])
    lines.extend(["", "Detailed reasons, budgets, lower bounds, evidence paths, and unknown fields are preserved in failures.json. This audit does not change the existing report's completeness gate.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="directory for summary.json, summary.md, and failures.json")
    args = parser.parse_args(argv)
    try:
        report = build_audit(args.runs_dir.resolve(), args.manifest)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (args.output / "failures.json").write_text(json.dumps(report["failures"], ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (args.output / "summary.md").write_text(markdown(report), encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"Failure audit could not be generated: {exc}\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
