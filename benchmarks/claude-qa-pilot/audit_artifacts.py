#!/usr/bin/env python3
"""Read-only audit of pilot artifacts; emit JSON to stdout without rewriting them."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
ZG_TOOL = "mcp__zvec_grep__zvec_grep_search"
BUDGET_DENIAL_MARKER = "The investigation budget is exhausted."


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def number(value) -> bool:
    return type(value) in (int, float) and value >= 0 and (type(value) is int or math.isfinite(value))


def file_record(path: Path) -> dict:
    return {"path": str(path), "exists": path.exists(),
            "sha256": sha(path.read_bytes()) if path.exists() else None}


def parse_stream(text: str) -> dict:
    calls, results, models, events, issues = {}, {}, set(), [], []
    raw_call_blocks, raw_result_blocks = 0, 0
    for line_number, line in enumerate(text.splitlines(), 1):
        try:
            event = json.loads(line)
        except ValueError:
            if line.strip():
                issues.append(f"invalid_json_line:{line_number}")
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        if event.get("type") == "assistant" and message.get("model") not in (None, "<synthetic>"):
            models.add(message["model"])
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and event.get("type") == "assistant":
                raw_call_blocks += 1
                tool_id = block.get("id")
                if not isinstance(tool_id, str) or not tool_id:
                    issues.append(f"missing_tool_use_id:{line_number}")
                    continue
                if tool_id in calls and calls[tool_id]["name"] != block.get("name"):
                    issues.append("conflicting_tool_name:" + tool_id)
                calls[tool_id] = {"name": block.get("name"), "input": block.get("input"),
                                  "first_line": calls.get(tool_id, {}).get("first_line", line_number)}
            elif block.get("type") == "tool_result":
                raw_result_blocks += 1
                tool_id = block.get("tool_use_id")
                if not isinstance(tool_id, str) or not tool_id:
                    issues.append(f"missing_tool_result_id:{line_number}")
                    continue
                value = results.setdefault(tool_id, {"is_error": False, "flags": set(), "copies": 0,
                                                     "budget_denial_marker": False})
                flag = bool(block.get("is_error"))
                value["is_error"] |= flag
                value["budget_denial_marker"] |= BUDGET_DENIAL_MARKER in json.dumps(block.get("content"), ensure_ascii=False)
                value["flags"].add(flag)
                value["copies"] += 1
    final_results = [event for event in events if event.get("type") == "result"]
    final = final_results[-1] if final_results else {}
    if isinstance(final.get("modelUsage"), dict):
        models.update(final["modelUsage"])
    for tool_id, value in results.items():
        if len(value["flags"]) > 1:
            issues.append("conflicting_tool_results_error_wins:" + tool_id)
    return {"calls": calls, "results": results, "models": sorted(models), "final": final,
            "final_result_count": len(final_results), "issues": issues,
            "raw_call_blocks": raw_call_blocks, "raw_result_blocks": raw_result_blocks,
            "duplicate_call_blocks": raw_call_blocks - len(calls),
            "duplicate_result_blocks": raw_result_blocks - len(results)}


def join_tools(parsed: dict, state: dict | None, limit, seconds, zg_tool: str = ZG_TOOL) -> dict:
    observed, results = parsed["calls"], parsed["results"]
    hooks = state.get("calls", {}) if isinstance(state, dict) else None
    errors = []
    hook_present = hooks is not None
    if hook_present and not isinstance(hooks, dict):
        return {"hook_present": True, "valid": False, "errors": ["invalid_hook_calls"]}
    if hook_present and set(hooks) != set(observed):
        errors.append("hook_call_inventory_mismatch")
    admitted_so_far = 0
    if hook_present:
        for tool_id, decision in hooks.items():
            if not isinstance(decision, dict):
                errors.append("invalid_hook_entry:" + tool_id)
                continue
            admitted, elapsed = decision.get("admitted"), decision.get("elapsed_seconds")
            if type(admitted) is not bool or not number(elapsed):
                errors.append("invalid_hook_decision_or_time:" + tool_id)
                continue
            if decision.get("name") != observed.get(tool_id, {}).get("name"):
                errors.append("hook_tool_name_mismatch:" + tool_id)
            if number(limit) and number(seconds):
                expected = admitted_so_far < limit and elapsed < seconds
                if admitted != expected:
                    errors.append("decision_disagrees_with_budget_and_recorded_order:" + tool_id)
                expected_reason = "call_limit" if admitted_so_far >= limit else "time_limit" if elapsed >= seconds else "within_budget"
                if decision.get("reason") is not None and decision["reason"] != expected_reason:
                    errors.append("hook_reason_mismatch:" + tool_id)
            admitted_so_far += int(admitted)
    rows = []
    for tool_id, call in observed.items():
        reply = results.get(tool_id)
        decision = hooks.get(tool_id) if hook_present else None
        admission = decision.get("admitted") if isinstance(decision, dict) else None
        if reply is None:
            category = "unresolved"
        elif not hook_present or type(admission) is not bool:
            category = "unclassified_without_hook"
        elif admission:
            category = "admitted_error" if reply["is_error"] else "admitted_success"
        elif reply["is_error"] and reply["budget_denial_marker"]:
            category = "budget_denied"
        elif reply["is_error"]:
            category = "budget_denial_result_unverified"
            errors.append("denied_result_missing_budget_message:" + tool_id)
        else:
            category = "budget_denial_bypassed"
            errors.append("budget_denial_not_enforced:" + tool_id)
        rows.append({"tool_use_id": tool_id, "name": call["name"], "first_stream_line": call["first_line"],
                     "classification": category, "admitted": admission, "result_observed": reply is not None,
                     "result_is_error": reply["is_error"] if reply else None,
                     "budget_denial_marker_observed": reply["budget_denial_marker"] if reply else False,
                     "elapsed_seconds": decision.get("elapsed_seconds") if isinstance(decision, dict) else None,
                     "budget_reason": decision.get("reason") if isinstance(decision, dict) else None,
                     "root_argument": call["input"].get("root") if isinstance(call["input"], dict) else None})

    def summarize(selected):
        counts = {key: sum(row["classification"] == key for row in selected)
                  for key in ("admitted_success", "admitted_error", "budget_denied", "unresolved",
                              "budget_denial_bypassed", "budget_denial_result_unverified", "unclassified_without_hook")}
        if not hook_present:
            for key in ("admitted_success", "admitted_error", "budget_denied", "budget_denial_bypassed"):
                counts[key] = None
        return {"attempts": len(selected), **counts,
                "budget_denial_decisions": sum(row["admitted"] is False for row in selected) if hook_present else None,
                "budget_denied_unresolved": sum(row["admitted"] is False and not row["result_observed"] for row in selected) if hook_present else None,
                "observed_success_results": sum(row["result_observed"] and not row["result_is_error"] for row in selected),
                "observed_error_results": sum(row["result_is_error"] is True for row in selected)}

    return {"hook_present": hook_present, "valid": not errors if hook_present else None, "errors": errors,
            "all_tools": summarize(rows), "zg": summarize([row for row in rows if row["name"] == zg_tool]),
            "recorded_admitted": admitted_so_far if hook_present else None,
            "max_admitted_elapsed_seconds": max((row["elapsed_seconds"] for row in rows
                if row["admitted"] is True and number(row["elapsed_seconds"])), default=None),
            "unmatched_result_ids": sorted(set(results) - set(observed)), "calls": rows}


def audit_trial(trial: Path, manifest: dict, args) -> dict:
    stream_path, hook_path = trial / "stream.jsonl", trial / "tool-budget.json"
    parsed = parse_stream(stream_path.read_text())
    metrics = read_json(trial / "metrics.json") or {}
    wrapper = read_json(hook_path)
    state = wrapper.get("state") if isinstance(wrapper, dict) else None
    if state is None and isinstance(wrapper, dict) and "calls" in wrapper:
        state = wrapper
    limit = wrapper.get("limit") if isinstance(wrapper, dict) else None
    seconds = wrapper.get("research_seconds") if isinstance(wrapper, dict) else None
    limit = limit if limit is not None else manifest.get("tool_budget")
    seconds = seconds if seconds is not None else manifest.get("research_seconds")
    issues = list(parsed["issues"])
    expected = {"limit": args.expected_limit, "research_seconds": args.expected_research_seconds,
                "hook_sha256": args.expected_hook_sha256}
    actual = {"limit": limit, "research_seconds": seconds, "hook_sha256": manifest.get("budget_hook_sha256")}
    for key, value in expected.items():
        if value is not None and actual[key] != value:
            issues.append("expected_parameter_mismatch:" + key)
    for key, declared in (("limit", manifest.get("tool_budget")), ("research_seconds", manifest.get("research_seconds"))):
        if declared is not None and actual[key] != declared:
            issues.append("manifest_parameter_mismatch:" + key)
    if manifest.get("tool_budget") is not None and state is None:
        issues.append("declared_budget_missing_hook_state")
    if isinstance(wrapper, dict) and wrapper.get("valid") is False:
        issues.extend("controller_budget_audit:" + str(error) for error in wrapper.get("errors", ["invalid"]))
    joined = join_tools(parsed, state, limit, seconds, manifest.get("zg_tool", ZG_TOOL))
    issues.extend(joined["errors"])
    if joined["hook_present"] and (not number(limit) or not number(seconds)):
        issues.append("missing_enforcement_parameters")
    final = parsed["final"]
    usage = final.get("usage")
    usage_complete = isinstance(usage, dict) and all(number(usage.get(key)) for key in TOKEN_FIELDS)
    answer = final.get("result")
    answer_nonempty = isinstance(answer, str) and bool(answer.strip())
    result_success = final.get("subtype") == "success" and not final.get("is_error")
    final_answer_delivered = result_success and answer_nonempty
    answer_path = trial / "answer.txt"
    answer_matches = answer_path.exists() and isinstance(answer, str) and answer_path.read_text() == answer
    if final_answer_delivered and not answer_matches:
        issues.append("saved_answer_differs_from_final_result")
    if manifest.get("requested_model") and parsed["models"] != [manifest["requested_model"]]:
        issues.append("resolved_model_mismatch")
    if metrics.get("tool_calls") is not None and metrics["tool_calls"] != len(parsed["calls"]):
        issues.append("metrics_tool_count_mismatch")
    if metrics.get("usage_complete") is not None and metrics["usage_complete"] != usage_complete:
        issues.append("metrics_usage_complete_mismatch")
    complete_input = sum(usage[key] for key in TOKEN_FIELDS if key != "output_tokens") if usage_complete else None
    return {"profile": trial.name, "evidence": {
                "stream": file_record(stream_path), "hook_audit": file_record(hook_path),
                "metrics": file_record(trial / "metrics.json"), "answer": file_record(answer_path),
                "hook_state_canonical_sha256": sha(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()) if state is not None else None},
            "parameters": {"expected": expected, "recorded": actual,
                           "hook_audit_reported_valid": wrapper.get("valid") if isinstance(wrapper, dict) else None},
            "deduplication": {key: parsed[key] for key in ("raw_call_blocks", "raw_result_blocks", "duplicate_call_blocks", "duplicate_result_blocks")},
            "tool_join": joined, "resolved_models": parsed["models"],
            "usage": {"complete": usage_complete, "authoritative_final_usage": usage,
                      "input_tokens_including_cache": complete_input,
                      "output_tokens": usage["output_tokens"] if usage_complete else None,
                      "original_metrics_usage_source": metrics.get("usage_source"),
                      "fallback_is_complete_accounting": False},
            "final_answer": {"result_count": parsed["final_result_count"], "result_subtype": final.get("subtype"),
                             "delivered": final_answer_delivered, "nonempty": answer_nonempty,
                             "matches_saved_answer": answer_matches, "characters": len(answer) if isinstance(answer, str) else None,
                             "quality_assessed": False},
            "trial": {"reported_valid": metrics.get("valid"), "reported_protocol_valid": metrics.get("protocol_valid"),
                      "reported_errors": metrics.get("errors"), "exit_code": metrics.get("exit_code"),
                      "wall_seconds": metrics.get("wall_seconds"),
                      "efficiency_comparable_before_pair_checks": usage_complete and final_answer_delivered
                          and metrics.get("valid") is True and not issues},
            "audit_valid": not issues, "audit_issues": issues}


def safe_json(value):
    if isinstance(value, dict):
        return {key: safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_nonfinite_number": repr(value)}
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True, help="Task artifact directory containing manifest.json and r01-* subdirectories")
    parser.add_argument("--expected-limit", type=int)
    parser.add_argument("--expected-research-seconds", type=int)
    parser.add_argument("--expected-hook-sha256")
    args = parser.parse_args()
    root = args.artifact_dir.resolve()
    manifest = read_json(root / "manifest.json") or {}
    summary = read_json(root / "summary.json") or {}
    scans = {name: read_json(root / name) for name in ("secret-scan.json", "collection-secret-scan.json")}
    trials = [audit_trial(path.parent, manifest, args) for path in sorted(root.glob("r*-*/stream.jsonl"))]
    source = read_json(root / "source-manifest.json") or {}
    computed_source_hash = sha(json.dumps(source["files"], sort_keys=True, separators=(",", ":")).encode()) if isinstance(source.get("files"), dict) else None
    source_hashes_match = bool(computed_source_hash and computed_source_hash == source.get("sha256") == manifest.get("source_sha256"))
    checks = {"source_unchanged": summary.get("source_unchanged") is True,
              "source_manifest_hashes_match": source_hashes_match,
              "secret_scan_passed": all(isinstance(value, dict) and value.get("passed") is True for value in scans.values()),
              "all_planned_trials_completed": summary.get("all_planned_trials_completed") is True,
              "planned_count_matches_observed": summary.get("planned_trial_count") == len(trials),
              "all_trials_audited": bool(trials) and all(trial["audit_valid"] for trial in trials)}
    report = {"schema_version": 1, "read_only": True, "artifact_dir": str(root),
              "auditor_sha256": sha(Path(__file__).read_bytes()),
              "protocol_version": manifest.get("protocol_version"), "prompt_sha256": manifest.get("prompt_sha256"),
              "source_sha256": manifest.get("source_sha256"), "manifest": file_record(root / "manifest.json"),
              "source_manifest": file_record(root / "source-manifest.json"), "pair_checks": checks,
              "pair_efficiency_accounting_comparable": all(checks.values()) and all(trial["trial"]["efficiency_comparable_before_pair_checks"] for trial in trials),
              "classification_definitions": {
                  "admitted_success": "Hook admitted and matching non-error tool_result observed; usefulness is not judged.",
                  "admitted_error": "Hook admitted and matching error tool_result observed.",
                  "budget_denied": "Hook denied and matching error tool_result contains the frozen budget message; not an executed retrieval error.",
                  "unresolved": "No matching tool_result; includes pending denied calls. budget_denied_unresolved is a subset.",
                  "no_hook": "Admission-based counts are null; no budget compliance is inferred.",
                  "duplicates": "Unique tool_use_id; repeated results count once, conflicting flags retain error and raise audit issue."},
              "limitations": ["No answer-quality judgment or statistical inference.",
                              "Budget parameters and elapsed times are recorded hook/controller evidence, not independent wall-clock metering.",
                              "Complete input includes repeated cached context; reported provider billing is not verified.",
                              "Changed protocols are not pooled."], "trials": trials}
    print(json.dumps(safe_json(report), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
