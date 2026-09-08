"""Offline explanatory accounting for planned read-only QA experiments.

Native message usage is never reconstructed from tool text or final totals.
Entry discovery and source-line visibility are diagnostics, not answer grading.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .observability import classify_call, validate_case

TOKEN_FIELDS = ("input_tokens", "native_input_tokens", "cache_read_tokens",
                "cache_write_tokens", "output_tokens", "reasoning_tokens")
PHASES = ("discovery", "expansion", "final_generation", "unclassified")
METRICS = ("input_tokens", "output_tokens", "wall_seconds", "model_turns",
           "tool_calls_attempted", "tool_calls_successful", "tool_calls_error",
           "search_calls_attempted", "search_calls_successful", "read_calls_attempted",
           "visible_tool_bytes", "read_unique_lines", "read_repeated_lines")


def _number(value: Any) -> int | float | None:
    return value if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 else None


def _sum(values: list[Any]) -> int | float | None:
    return sum(values) if values and all(_number(x) is not None for x in values) else None


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_text(x) for x in value]
        return "\n".join(x for x in parts if x is not None) if all(x is not None for x in parts) else None
    if isinstance(value, dict) and value.get("type") in (None, "text"):
        return value.get("text") if isinstance(value.get("text"), str) else None
    return None


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def read_events(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    events, errors = [], []
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
            else:
                errors.append(f"non-object native event at line {index}")
        except json.JSONDecodeError:
            errors.append(f"invalid native JSON at line {index}")
    return events, errors


def _usage(usage: Any, adapter: str) -> dict[str, Any]:
    usage = usage if isinstance(usage, dict) else {}
    out = dict.fromkeys(TOKEN_FIELDS)
    if adapter == "opencode":
        cache = usage.get("cache") or {}
        out.update(native_input_tokens=_number(usage.get("input")), cache_read_tokens=_number(cache.get("read")),
                   cache_write_tokens=_number(cache.get("write")), output_tokens=_number(usage.get("output")),
                   reasoning_tokens=_number(usage.get("reasoning")))
        # Harbor OpenCode's prompt convention: input + cache.read, not +cache.write.
        out["input_tokens"] = _sum([out["native_input_tokens"], out["cache_read_tokens"]])
    elif adapter == "qodercli":
        # Qoder 1.1.45 normalizes input to include cached input already.
        out.update(input_tokens=_number(usage.get("input_tokens")), output_tokens=_number(usage.get("output_tokens")),
                   cache_read_tokens=_number(usage.get("cache_read_input_tokens")),
                   cache_write_tokens=_number(usage.get("cache_creation_input_tokens")),
                   reasoning_tokens=_number(usage.get("reasoning_tokens")))
        if not any(_number(x) and x > 0 for x in out.values()):
            out = dict.fromkeys(TOKEN_FIELDS)  # Native masked-zero usage is unavailable.
    else:
        out.update(input_tokens=_number(usage.get("prompt_tokens")), output_tokens=_number(usage.get("completion_tokens")),
                   cache_read_tokens=_number(usage.get("cached_tokens")),
                   reasoning_tokens=_number((usage.get("extra") or {}).get("reasoning_tokens")))
    out["source"] = adapter
    return out


def _failure(error: Any) -> str | None:
    if not isinstance(error, str) or not error:
        return None
    if re.search(r"(?:unavailable|unknown) tool|tool .+ (?:not found|does not exist)", error, re.I):
        return "unavailable_tool"
    return "tool_error"


def _scope(event: dict[str, Any]) -> str:
    return str(event.get("session_id") or event.get("sessionID") or "root") + ":" + str(event.get("parent_tool_use_id") or "root")


def adapt_opencode(events: list[dict[str, Any]]) -> dict[str, Any]:
    """One step-start/finish message is a model turn; call IDs deduplicate states."""
    turns, calls, diagnostics = {}, {}, []
    current = None
    for order, event in enumerate(events):
        part = event.get("part") or {}
        kind = event.get("type")
        scope = _scope(event)
        message = part.get("messageID")
        if kind in {"step_start", "step_finish"}:
            key = scope + ":" + str(message or current or f"unidentified-{order}")
            current = message or current or f"unidentified-{order}"
            turn = turns.setdefault(key, {"kind": "model_turn", "id": key, "order": order, "timestamp": event.get("timestamp"),
                                          "usage": _usage({}, "opencode"), "has_answer_text": False})
            if kind == "step_finish":
                turn.update(usage=_usage(part.get("tokens"), "opencode"), finish_reason=part.get("reason"))
        elif kind == "text" and message:
            key = scope + ":" + str(message)
            if key in turns and _text(part):
                turns[key]["has_answer_text"] = True
        elif kind == "tool_use":
            state = part.get("state") or {}
            call_id = part.get("callID")
            key = scope + ":" + str(call_id or f"unidentified-{order}")
            if not call_id:
                diagnostics.append("native tool without call ID: cannot deduplicate it")
            existing = calls.get(key)
            if existing and existing.get("status") in {"completed", "error"} and state.get("status") not in {"completed", "error"}:
                continue
            text = _text(state.get("output"))
            error = state.get("error") if isinstance(state.get("error"), str) else None
            if text is None and error is not None:
                text = error  # Native tool errors are visible feedback even when ATIF drops them.
            times = state.get("time") or {}
            duration = times.get("end", 0) - times.get("start", 0) if all(_number(times.get(k)) is not None for k in ("start", "end")) else None
            calls[key] = {"kind": "tool", "id": key, "call_id": call_id, "order": existing["order"] if existing else order,
                          "timestamp": event.get("timestamp"), "model_turn_id": scope + ":" + str(message or current),
                          "name": part.get("tool") or "unknown", "arguments": state.get("input"), "status": state.get("status") or "unknown",
                          "text": text, "error": error, "metadata": state.get("metadata") or {}, "duration_ms": _number(duration)}
    return {"adapter": "opencode_native", "timeline": sorted([*turns.values(), *calls.values()], key=lambda x: x["order"]), "diagnostics": diagnostics}


def adapt_qoder(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Full native blocks share message IDs; session result usage is not a turn."""
    turns, calls, observations, diagnostics = {}, {}, {}, []
    for order, event in enumerate(events):
        scope = _scope(event)
        message = event.get("message") or {}
        blocks = message.get("content") or []
        if not isinstance(blocks, list):
            continue
        if event.get("type") == "assistant":
            message_id = message.get("id")
            key = scope + ":" + str(message_id or f"unidentified-{order}")
            if not message_id:
                diagnostics.append("native assistant without message ID: turn count may be incomplete")
            turn = turns.setdefault(key, {"kind": "model_turn", "id": key, "order": order, "timestamp": event.get("timestamp"),
                                          "usage": _usage({}, "qodercli"), "has_answer_text": False})
            usage = _usage(message.get("usage"), "qodercli")
            if any(usage[k] is not None for k in TOKEN_FIELDS):
                turn["usage"] = usage
            for block_index, block in enumerate(blocks):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    turn["has_answer_text"] = True
                if block.get("type") == "tool_use":
                    call_id = block.get("id")
                    call_key = scope + ":" + str(call_id or f"unidentified-{order}-{block_index}")
                    calls.setdefault(call_key, {"kind": "tool", "id": call_key, "call_id": call_id, "order": order + (block_index + 1) / (len(blocks) + 1),
                                               "model_turn_id": key, "name": block.get("name") or "unknown", "arguments": block.get("input"),
                                               "timestamp": event.get("timestamp"), "status": "unknown", "text": None, "error": None, "metadata": {}, "duration_ms": None})
        elif event.get("type") == "user":
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                key = scope + ":" + str(block.get("tool_use_id"))
                observations[key] = {"text": _text(block.get("content")), "status": "error" if block.get("is_error") is True else "completed",
                                     "observation_order": order, "timestamp": event.get("timestamp")}
    for key, observation in observations.items():
        if key not in calls:
            diagnostics.append("orphan tool result: " + key)
            continue
        calls[key].update(observation)
        if observation["status"] == "error":
            calls[key]["error"] = observation["text"]
    return {"adapter": "qodercli_native", "timeline": sorted([*turns.values(), *calls.values()], key=lambda x: x["order"]), "diagnostics": diagnostics}


def adapt_atif(trajectory: dict[str, Any]) -> dict[str, Any]:
    rows, calls = [], {}
    for order, step in enumerate(trajectory.get("steps") or []):
        if step.get("source") != "agent":
            continue
        key = "atif:" + str(step.get("step_id", order))
        rows.append({"kind": "model_turn", "id": key, "order": order, "timestamp": step.get("timestamp"),
                     "usage": _usage(step.get("metrics"), "atif"), "has_answer_text": bool(_text(step.get("message"))),
                     "llm_call_count": _number(step.get("llm_call_count"))})
        for index, call in enumerate(step.get("tool_calls") or []):
            call_id = call.get("tool_call_id") or f"{key}:call-{index}"
            calls.setdefault(call_id, {"kind": "tool", "id": call_id, "call_id": call_id, "order": order + (index + 1) / (len(step["tool_calls"]) + 1),
                                      "model_turn_id": key, "name": call.get("function_name") or "unknown", "arguments": call.get("arguments"),
                                      "timestamp": step.get("timestamp"), "status": "unknown", "text": None, "error": None, "metadata": {}, "duration_ms": None})
    for step in trajectory.get("steps") or []:
        for observation in (step.get("observation") or {}).get("results") or []:
            call = calls.get(observation.get("source_call_id"))
            if call is not None:
                call["text"] = _text(observation.get("content"))
                if observation.get("is_error") is True:
                    call.update(status="error", error=call["text"])
                elif observation.get("is_error") is False:
                    call["status"] = "completed"
                # ATIF observations without explicit state cannot prove tool success.
    return {"adapter": "atif", "timeline": sorted([*rows, *calls.values()], key=lambda x: x["order"]),
            "diagnostics": ["ATIF-only fallback: native statuses and native token components may be unavailable"]}


def _relative_path(path: Any) -> str | None:
    if not isinstance(path, str) or not path.strip():
        return None
    path = path.strip().replace("\\", "/")
    if path.startswith("/app/"):
        return path[len("/app/"):]
    if path.startswith("./"):
        path = path[2:]
    return path if not path.startswith("/") and ".." not in Path(path).parts else None


def numbered_lines(text: str, path_hint: str | None = None) -> list[tuple[str, int, str]]:
    """Only numbered visible lines; a header's parent range is never expanded."""
    path = _relative_path(path_hint)
    visible_path = re.search(r"<path>([^<]+)</path>", text)
    if path and visible_path and _relative_path(visible_path[1]) != path:
        return []
    lines = []
    for line in text.splitlines():
        tag = re.fullmatch(r"<path>(.+)</path>", line.strip())
        header = re.fullmatch(r"(/app/[^:]+|[^\s:]+\.[\w]+):", line.strip())
        if tag or header:
            path = _relative_path((tag or header).group(1))
            continue
        # ZG numbered previews need a visible public header, not raw item metadata.
        if line.startswith("#") and "matchedBy=" in line:
            match = re.search(r"(?:^|\s)((?:/app/)?[\w./-]+\.[\w]+)(?::\d+(?:-\d+)?)?(?:\s|$)", line)
            path = _relative_path(match.group(1)) if match else None
            continue
        match = re.match(r"^\s*(?:Line )?(\d+): ?(.*)$", line)
        if not match:
            match = re.match(r"^\s*(\d+)[|→\t](.*)$", line)
        if path and match:
            lines.append((path, int(match.group(1)), match.group(2).rstrip()))
    return lines


def _ranges(keys: set[tuple[str, int]]) -> list[dict[str, Any]]:
    result = []
    for path in sorted({x[0] for x in keys}):
        for line in sorted(x[1] for x in keys if x[0] == path):
            if result and result[-1]["path"] == path and result[-1]["end_line"] + 1 == line:
                result[-1]["end_line"] = line
            else:
                result.append({"path": path, "start_line": line, "end_line": line})
    return result


def _empty_search(call: dict[str, Any]) -> bool | None:
    if call["status"] != "completed":
        return None
    matches = _number((call.get("metadata") or {}).get("matches"))
    if matches is not None:
        return matches == 0
    text = call.get("text")
    if isinstance(text, str):
        if text.strip() in {"freshness: fresh", "freshness: fresh\nNo results.", "freshness: fresh\nNo matches."}:
            return True
        if not text.strip() or re.fullmatch(r"(?:No (?:files|results|matches)(?: found)?\.?|Found 0 matches)\s*", text, re.I):
            return True
        if re.search(r"(?m)^#\d+\b.*matchedBy=|^Found [1-9]\d* matches", text):
            return False
    return None


def _phase_cost(rows: list[dict[str, Any]], available: bool) -> dict[str, Any]:
    turns = [x for x in rows if x["kind"] == "model_turn"]
    calls = [x for x in rows if x["kind"] == "tool"]
    result = {"model_turns": len(turns) if available else None, "tool_calls_attempted": len(calls) if available else None}
    for field in TOKEN_FIELDS:
        values = [x["usage"][field] for x in turns]
        result[field] = _sum(values) if values else 0 if available else None
        result[field + "_observed_lower_bound"] = sum(x for x in values if x is not None) if values else 0 if available else None
        result[field + "_missing_turns"] = sum(x is None for x in values) if available else None
    sizes = [x.get("visible_bytes") for x in calls]
    result["visible_tool_bytes"] = _sum(sizes) if sizes else 0 if available else None
    return result


def analyze_trace(adapted: dict[str, Any], case: dict[str, Any], entries: dict[str, Any] | None = None) -> dict[str, Any]:
    """Annotate native/ATIF timeline without changing authoritative final counters."""
    available = adapted.get("available", True)
    rows = copy.deepcopy(adapted.get("timeline") or [])
    matcher = None
    if entries is not None:
        from .retrieval_eval import match_visible_entries
        matcher = match_visible_entries
    gold_lines: dict[tuple[str, int], str] = {}
    for evidence in case["evidence"]:
        for offset, line in enumerate(evidence["text"].splitlines()):
            gold_lines[(evidence["path"], evidence["start_line"] + offset)] = line.rstrip()
    seen_reads, seen_searches, seen_successful_searches = set(), set(), set()
    proof, read_positions = set(), set()
    first_entry = None
    calls = [x for x in rows if x["kind"] == "tool"]
    turns = [x for x in rows if x["kind"] == "model_turn"]
    last_call_order = max((x.get("observation_order", x["order"]) for x in calls), default=-1)
    final_turn = next((x for x in reversed(turns) if x["order"] > last_call_order and x.get("has_answer_text")), None)
    for index, row in enumerate(rows, 1):
        row["sequence"] = index
        if row["kind"] != "tool":
            continue
        args = row.get("arguments") if isinstance(row.get("arguments"), dict) else {}
        category = classify_call(row["name"], args)["category"]
        row.update(category=category, failure_kind=_failure(row.get("error")))
        row["execution_confirmed"] = True if row["status"] == "completed" else False if row["failure_kind"] == "unavailable_tool" else None
        text = row.get("text")
        row["visible_bytes"] = len(text.encode()) if isinstance(text, str) else None
        row["error_text_bytes"] = len(row["error"].encode()) if isinstance(row.get("error"), str) else None
        path_hint = (args.get("filePath") or args.get("file_path") or args.get("path")) if category == "read" else None
        if not isinstance(path_hint, str):
            path_hint = None
        actual_lines = numbered_lines(text, path_hint) if isinstance(text, str) else []
        row["numbered_visible_lines"] = len(actual_lines) if isinstance(text, str) else None
        row["read_new_lines"] = row["read_repeated_lines"] = None
        row["read_line_accounting_complete"] = None
        if category == "read" and isinstance(text, str):
            fresh = repeated = 0
            for path, line, content in actual_lines:
                key = (path, line, content)
                repeated += key in seen_reads
                fresh += key not in seen_reads
                seen_reads.add(key)
                read_positions.add((path, line))
            row.update(read_new_lines=fresh, read_repeated_lines=repeated, actual_read_ranges=_ranges({(p, n) for p, n, _ in actual_lines}))
            row["read_line_accounting_complete"] = bool(actual_lines) or "<type>directory</type>" in text or not text.strip()
        for path, line, content in actual_lines:
            if row["status"] != "error" and (path, line) in gold_lines and content == gold_lines[(path, line)] and content.strip():
                proof.add((path, line))
        row["source_evidence_nonblank_lines_seen"] = len(proof)
        row["empty_search"] = _empty_search(row) if category == "search" else None
        row["repeated_search_attempt"] = row["repeated_successful_search"] = None
        if category == "search":
            query_key = json.dumps([row["name"], args], sort_keys=True, ensure_ascii=False)
            row["repeated_search_attempt"] = query_key in seen_searches
            seen_searches.add(query_key)
            if row["status"] == "completed":
                row["repeated_successful_search"] = query_key in seen_successful_searches
                seen_successful_searches.add(query_key)
        hits = matcher(text, entries, path_hint=path_hint) if matcher and isinstance(text, str) and row["status"] != "error" else []
        primary_ids = {x["target_id"] for x in (entries or {}).get("targets", []) if x.get("primary") is True}
        row["visible_entries"] = hits if entries is not None and isinstance(text, str) else None
        useful = [x for x in hits if x["target_id"] in primary_ids and x.get("level") == "function"]
        if useful and (first_entry is None or row.get("observation_order", row["order"]) < first_entry["order"]):
            first_entry = {"sequence": index, "call_id": row["call_id"], "target_ids": sorted({x["target_id"] for x in useful}),
                           "order": row.get("observation_order", row["order"]), "definition": "First visible source-validated primary function entry; not all evidence needed for QA."}
    for row in rows:
        if row is final_turn:
            row["phase"] = "final_generation"
        elif entries is None:
            row["phase"] = "unclassified"
        elif first_entry is None or row["order"] <= first_entry["order"]:
            row["phase"] = "discovery"
        else:
            row["phase"] = "expansion"
    phase_costs = {phase: _phase_cost([x for x in rows if x["phase"] == phase], available) for phase in PHASES}
    if entries is None:
        for phase in ("discovery", "expansion"):
            phase_costs[phase] = _phase_cost([], False)
    if first_entry is not None:
        prefix = [x for x in rows if x["order"] <= first_entry["order"]]
        first_entry["cost_through_entry"] = _phase_cost(prefix, available)
    def count(predicate: Any) -> int | None:
        return sum(bool(predicate(x)) for x in calls) if available else None
    metrics = {
        "model_turns": len(turns) if available else None,
        "tool_calls_attempted": len(calls) if available else None,
        "tool_calls_successful": count(lambda x: x["status"] == "completed"),
        "tool_calls_error": count(lambda x: x["status"] == "error"),
        "tool_calls_unknown_status": count(lambda x: x["status"] not in {"completed", "error"}),
        "unavailable_tool_errors": count(lambda x: x["failure_kind"] == "unavailable_tool"),
        "search_calls_attempted": count(lambda x: x["category"] == "search"),
        "search_calls_successful": count(lambda x: x["category"] == "search" and x["status"] == "completed"),
        "zg_tool_calls_attempted": count(lambda x: "zvec" in x["name"].lower() and "grep" in x["name"].lower()),
        "zg_tool_calls_successful": count(lambda x: "zvec" in x["name"].lower() and "grep" in x["name"].lower() and x["status"] == "completed"),
        "read_calls_attempted": count(lambda x: x["category"] == "read"),
        "empty_searches_confirmed": count(lambda x: x["empty_search"] is True),
        "search_emptiness_unknown": count(lambda x: x["category"] == "search" and x["empty_search"] is None),
        "repeated_search_attempts": count(lambda x: x["repeated_search_attempt"] is True),
        "repeated_successful_searches": count(lambda x: x["repeated_successful_search"] is True),
        "visible_tool_bytes": _sum([x["visible_bytes"] for x in calls]) if calls else 0 if available else None,
        "read_unique_lines": len(seen_reads) if available else None,
        "read_repeated_lines": sum(x["read_repeated_lines"] or 0 for x in calls) if available else None,
    }
    read_complete = all(x["read_line_accounting_complete"] is True for x in calls if x["category"] == "read")
    metrics["read_unique_lines_observed_lower_bound"] = metrics["read_unique_lines"]
    metrics["read_repeated_lines_observed_lower_bound"] = metrics["read_repeated_lines"]
    if not read_complete:
        metrics["read_unique_lines"] = metrics["read_repeated_lines"] = None
    expected = {key for key, text in gold_lines.items() if text.strip()}
    evidence = {"scope": "Supplemental annotated source lines; exact visible numbered text, no blank lines, no inferred parent ranges.",
                "unique_nonblank_line_denominator": len(expected), "observed_nonblank_lines": len(proof) if available else None,
                "line_coverage": len(proof) / len(expected) if available and expected else None, "observed_ranges": _ranges(proof),
                "by_evidence": []}
    for item in case["evidence"]:
        needed = {(item["path"], item["start_line"] + i) for i, line in enumerate(item["text"].splitlines()) if line.strip()}
        evidence["by_evidence"].append({"id": item["id"], "nonblank_lines": len(needed), "observed": len(needed & proof) if available else None,
                                        "all_nonblank_lines_observed_across_outputs": needed <= proof if available else None})
    # Do not duplicate potentially huge source outputs in the explanatory JSON.
    for row in rows:
        if row["kind"] == "tool":
            text = row.pop("text", None)
            row["visible_text_sha256"] = hashlib.sha256(text.encode()).hexdigest() if isinstance(text, str) else None
            row.pop("metadata", None)
    return {"adapter": adapted.get("adapter"), "available": available, "diagnostics": adapted.get("diagnostics", []),
            "metrics": metrics, "native_turn_totals": {k: _sum([x["usage"][k] for x in turns]) for k in TOKEN_FIELDS},
            "first_useful_entry": first_entry, "entry_annotation_available": entries is not None, "read_line_accounting_complete": read_complete if available else None,
            "phases": phase_costs, "actual_read_ranges": _ranges(read_positions), "source_line_union_diagnostic": evidence, "timeline": rows}


def describe(values: list[int | float | None]) -> dict[str, Any]:
    known = [x for x in values if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)]
    mean = statistics.mean(known) if known else None
    sd = statistics.stdev(known) if len(known) > 1 else None
    return {"values": values, "known": len(known), "missing": len(values) - len(known), "mean": mean,
            "median": statistics.median(known) if known else None, "sd": sd,
            "min": min(known) if known else None, "max": max(known) if known else None,
            "cv": sd / abs(mean) if sd is not None and mean else None}


def _wire_diagnostics(agent_dir: Path) -> dict[str, Any]:
    path = agent_dir / "wire.jsonl"
    if not path.is_file():
        return {"available": False, "provider_parameters_observed": False,
                "reason": "Provider request trace unavailable; native selected-model names are not parameter verification."}
    events, errors = read_events(path)
    requests = {x.get("request_id"): x for x in events if x.get("event") == "request" and x.get("request_id") is not None}
    responses = {x.get("request_id"): x for x in events if x.get("event") == "response" and x.get("request_id") is not None}
    complete = bool(requests) and requests.keys() == responses.keys() and not errors
    usage = {key: _sum([_number((responses[x].get("usage") or {}).get(key)) for x in requests if x in responses]) if complete else None
             for key in ("prompt_tokens", "completion_tokens")}
    return {"available": True, "provider_parameters_observed": bool(requests), "errors": errors,
            "requests": list(requests.values()), "responses": list(responses.values()),
            "request_count": len(requests), "response_count": len(responses), "all_requests_have_response": complete,
            "provider_all_request_usage": usage,
            "scope": "Supplemental provider-wire counters can include maintenance requests outside ATIF. They never replace primary adapter totals."}


def analyze_trial(runs_dir: Path, planned: dict[str, Any], case: dict[str, Any], entries: dict[str, Any] | None,
                  manifest: dict[str, Any]) -> dict[str, Any]:
    trial_id = str(planned["trial_id"])
    trajectory_path = runs_dir / planned.get("trajectory_path", f"{trial_id}/agent/trajectory.json")
    agent_dir = trajectory_path.parent
    trial_dir = agent_dir.parent
    errors = []
    def optional(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            return _json(path)
        except (ValueError, OSError) as error:
            errors.append(f"{path.name}: {error}")
            return {}
    result = optional(trial_dir / "result.json")
    trajectory = optional(trajectory_path)
    session = optional(agent_dir / "session.json")
    agent_info = result.get("agent_info") or {}
    agent = planned.get("agent") or planned.get("agent_name") or agent_info.get("name") or manifest.get("agent")
    model = planned.get("model") or planned.get("model_name") or agent_info.get("model_name") or manifest.get("model")
    status = result.get("status") or session.get("status") or ("missing" if not trajectory and not result else planned.get("status", "unknown"))
    if any(result.get(k) is False for k in ("source_unchanged", "index_unchanged", "original_seed_unchanged", "working_index_semantic_unchanged")):
        status = "integrity_failure"
    candidates = ([("qodercli.txt", adapt_qoder), ("qodercli-stream.jsonl", adapt_qoder)] if "qoder" in str(agent).lower()
                  else [("opencode.txt", adapt_opencode)])
    adapted = None
    native_path = None
    for filename, adapter in candidates:
        path = agent_dir / filename
        if path.is_file():
            events, parse_errors = read_events(path)
            errors.extend(parse_errors)
            adapted = adapter(events)
            native_path = str(path.relative_to(runs_dir))
            adapted["available"] = bool(adapted["timeline"])
            if not adapted["available"] and trajectory.get("steps"):
                adapted = None
                errors.append("native stream contains no recognized timeline; used ATIF fallback")
            break
    if adapted is None:
        adapted = adapt_atif(trajectory) if isinstance(trajectory.get("steps"), list) else {"adapter": None, "timeline": [], "available": False, "diagnostics": ["No usable native or ATIF trajectory"]}
    trace = analyze_trace(adapted, case, entries)
    final = result.get("final_metrics") or trajectory.get("final_metrics") or {}
    hidden = (final.get("extra") or {}).get("token_usage_available") is False
    totals = {"input_tokens": None if hidden else _number(final.get("total_prompt_tokens")),
              "output_tokens": None if hidden else _number(final.get("total_completion_tokens")),
              "cache_read_tokens": None if hidden else _number(final.get("total_cached_tokens"))}
    context = (result.get("agent_result") or {}).get("context") or {}
    if not final and context:
        totals.update(input_tokens=_number(context.get("n_input_tokens")), output_tokens=_number(context.get("n_output_tokens")),
                      cache_read_tokens=_number(context.get("n_cache_tokens")))
    native_totals = trace["native_turn_totals"]
    reconciliation = {key: {"adapter_final": value, "sum_native_turns": native_totals.get(key),
                             "matches": value == native_totals[key] if value is not None and native_totals.get(key) is not None else None}
                      for key, value in totals.items()}
    metrics = {**trace["metrics"], **totals, "wall_seconds": _number(result.get("wall_seconds", session.get("wall_seconds")))}
    return {"trial_id": trial_id, "profile": planned.get("profile"), "block_id": planned.get("block_id", planned.get("repetition")),
            "agent": agent, "model": model, "planned_trial": planned, "status": status, "errors": errors,
            "metrics": metrics, "trace": trace, "native_trace_path": native_path, "usage_reconciliation": reconciliation,
            "primary_usage_convention": "Unchanged adapter final prompt counter. OpenCode: native input + cache.read; cache.write separate. Qoder: native inclusive input; do not add cache again.",
            "agent_info": agent_info, "session": session or None, "exception": result.get("error") or result.get("exception_info"),
            "readonly_integrity": {k: result.get(k) for k in ("source_unchanged", "index_unchanged", "original_seed_unchanged", "working_index_semantic_unchanged")},
            "provider_trace": _wire_diagnostics(agent_dir)}


def paired_description(trials: list[dict[str, Any]]) -> dict[str, Any]:
    blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trial in trials:
        if trial["block_id"] is not None:
            blocks[str(trial["block_id"])].append(trial)
    pairs = []
    for block_id, block in blocks.items():
        arms = {profile: [x for x in block if x["profile"] == profile] for profile in ("baseline", "zvec-grep")}
        baseline = arms["baseline"][0] if len(arms["baseline"]) == 1 else None
        zg = arms["zvec-grep"][0] if len(arms["zvec-grep"]) == 1 else None
        differences = {}
        for metric in METRICS:
            b = baseline["metrics"].get(metric) if baseline else None
            z = zg["metrics"].get(metric) if zg else None
            differences[metric] = b - z if b is not None and z is not None else None
        pairs.append({"block_id": block_id, "trial_ids_in_plan_order": [x["trial_id"] for x in block],
                      "baseline_status": baseline["status"] if baseline else None, "zg_status": zg["status"] if zg else None,
                      "both_completed": bool(baseline and zg and baseline["status"] == zg["status"] == "completed"),
                      "ambiguous_or_missing_arm": baseline is None or zg is None, "baseline_minus_zg": differences})
    def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        out = {}
        for metric in METRICS:
            values = [x["baseline_minus_zg"][metric] for x in rows]
            out[metric] = {**describe(values), "zg_lower_cost_count": sum(x is not None and x > 0 for x in values),
                           "equal_cost_count": sum(x == 0 for x in values if x is not None),
                           "zg_higher_cost_count": sum(x is not None and x < 0 for x in values)}
        return out
    return {"definition": "Descriptive baseline minus ZG costs within prescheduled AB/BA temporal blocks. These are not matched RNG seeds; no causal or population confidence claim. Failed arms remain, and lower failed-run spend is not success.",
            "pairs": pairs, "all_planned_pairs": summary(pairs),
            "completed_pairs_only_secondary": summary([x for x in pairs if x["both_completed"]]),
            "trials_without_block_id": [x["trial_id"] for x in trials if x["block_id"] is None]}


def analyze_runs(runs_dir: Path, case: dict[str, Any], entries: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_case(case)
    if entries is not None and (entries.get("case_id") != case["case_id"] or entries.get("repo", {}).get("commit") != case["repo"]["commit"]):
        raise ValueError("Entry annotations must match the case and pinned source commit")
    plan = _json(runs_dir / "plan.json")
    manifest = _json(runs_dir / "manifest.json") if (runs_dir / "manifest.json").is_file() else {}
    planned = plan.get("trials")
    if not isinstance(planned, list) or not planned:
        raise ValueError("plan.json must enumerate all planned trial arms")
    if len({x.get("trial_id") for x in planned}) != len(planned) or any(not x.get("trial_id") for x in planned):
        raise ValueError("planned trial IDs must be present and unique")
    trials = [analyze_trial(runs_dir, row, case, entries, manifest) for row in planned]
    quality_path = runs_dir / "quality-review.json"
    quality_protocol = "readonly-source-qa-v2-calibrated"
    if not quality_path.is_file():
        quality_path = runs_dir / "judged.json"
        quality_protocol = "legacy-single-judge"
    judged = _json(quality_path) if quality_path.is_file() else {}
    if judged and judged.get("case_id") != case["case_id"]:
        raise ValueError("Quality review case differs from the analyzed case")
    if judged.get("plan_sha256") and judged["plan_sha256"] != hashlib.sha256((runs_dir / "plan.json").read_bytes()).hexdigest():
        raise ValueError("Quality review plan hash differs from the analyzed trial plan")
    quality_ids = [x.get("trial_id") for x in judged.get("trials", [])]
    if len(set(quality_ids)) != len(quality_ids):
        raise ValueError("Quality review has duplicate trial IDs")
    for trial in trials:
        trial["quality_assessment"] = next((x for x in judged.get("trials", []) if x.get("trial_id") == trial["trial_id"]), None)
        quality = trial["quality_assessment"] or {}
        trial["quality_status"] = quality.get("consensus_status") or quality.get("quality") or "unscored"
    configurations: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for trial in trials:
        configurations[(trial["agent"], trial["model"])].append(trial)
    groups = []
    for (agent, model), rows in configurations.items():
        arms = {}
        for profile in dict.fromkeys(x["profile"] for x in rows):
            arm = [x for x in rows if x["profile"] == profile]
            arms[profile] = {"planned_count": len(arm), "status_counts": dict(Counter(x["status"] for x in arm)),
                             "quality_counts": dict(Counter(x["quality_status"] for x in arm)),
                             "all_planned_metrics": {key: describe([x["metrics"].get(key) for x in arm]) for key in METRICS},
                             "completed_only_secondary": {key: describe([x["metrics"].get(key) for x in arm if x["status"] == "completed"]) for key in METRICS}}
        groups.append({"agent": agent, "model": model, "profiles": arms, "within_block_cost_differences": paired_description(rows)})
    return {"schema_version": 1, "case_id": case["case_id"], "scope": "One frozen read-only QA case; descriptive E2E repeats and observable cost explanations.",
            "plan": plan, "manifest": manifest, "entry_manifest_id": (entries or {}).get("manifest_id"),
            "configurations": groups, "trials": trials,
            "quality_review_source": {"path": quality_path.name, "protocol": quality_protocol, "sha256": hashlib.sha256(quality_path.read_bytes()).hexdigest()} if judged else None,
            "quality_gate": judged.get("quality_gate"),
            "quality_review_calibration": judged.get("calibration"),
            "quality_review_scope": judged.get("scope"),
            "definitions": {"phases": "Discovery includes the first validated primary function entry and the model turn that requested it. Expansion follows. Final generation is the last answer-bearing turn after all tool observations. No detected entry keeps exploration in discovery; absent entry annotations leave it unclassified. No final turn is invented.",
                            "read_lines": "Unique/repeated (exact path, visible line number, exact line text) triples. Reported parent ranges and requested read limits do not imply visibility. Numbered lines are not independent facts.",
                            "source_line_union": "Separate lower-bound diagnostic over nonblank custom annotated lines; each needs exact numbered visible source text. It can join lines across outputs and does not replace old full-span metrics or judge QA.",
                            "repeated_search": "Exact repeated tool name plus canonical JSON arguments, not semantic query equivalence. Attempted and successful repeats are separated.",
                            "costs": "Final adapter counters remain primary. Native per-turn components and any provider-wire totals are separate. UTF-8 visible bytes are not input tokens."},
            "limitations": ["All planned arms, including failure and missing usage, remain in the report. Means disclose their known/missing denominator.",
                            "Five same-case repetitions do not provide five independent QA problems or establish answer non-inferiority.",
                            "AB/BA blocks control temporal proximity, not model randomness or identical seeds; paired differences are descriptive.",
                            "Entry discovery measures a useful location, not complete evidence or correct reasoning. Source annotations are supplemental, not official exhaustive SWE-QA relevance labels.",
                            "Unknown tool outcomes and missing native usage stay unknown. Observed counters from partial traces can be lower bounds.",
                            "Requested model/limits are not proof of actual provider parameters; only available wire traces can document transmitted values."]}


def render_markdown(report: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return "N/A"
        return (f"{value:.2f}" if isinstance(value, float) else str(value)).replace("|", "\\|").replace("\n", " ")
    lines = [f"# E2E explanatory report: {report['case_id']}", "", report["scope"], "",
             "All planned arms remain. Positive paired cost differences mean ZG spent less; failed-run savings are not success.", "",
             "| Agent/model | Trial | Status | Quality | Input | Calls attempted/success/error | First useful entry call | Read unique/repeated lines |", "|---|---|---|---|---:|---|---|---|"]
    for trial in report["trials"]:
        metrics, trace = trial["metrics"], trial["trace"]
        entry = trace["first_useful_entry"] or {}
        values = (str(trial["agent"]) + "/" + str(trial["model"]), trial["trial_id"], trial["status"], trial["quality_status"], metrics["input_tokens"],
                  "/".join(cell(metrics[k]) for k in ("tool_calls_attempted", "tool_calls_successful", "tool_calls_error")), entry.get("call_id"),
                  cell(metrics["read_unique_lines"]) + "/" + cell(metrics["read_repeated_lines"]))
        lines.append("| " + " | ".join(cell(x) for x in values) + " |")
    quality_source = report.get("quality_review_source") or {}
    lines.extend(["", "Quality review protocol: " + cell(quality_source.get("protocol")) + ". Quality never filters the planned cost ledger.", ""])
    if report.get("quality_gate") is not None:
        lines.append("Quality gate: `" + json.dumps(report["quality_gate"], sort_keys=True) + "`. This is not a non-inferiority test.")
    for group in report["configurations"]:
        for profile, arm in group["profiles"].items():
            lines.append(f"- {cell(group['agent'])}/{cell(group['model'])} {cell(profile)}: " + ", ".join(f"{status}={count}" for status, count in sorted(arm["quality_counts"].items())))
    lines.extend(["", "| Agent/model | Paired metric (baseline − ZG) | Known/missing | Mean | Median | SD | Min | Max | ZG lower/equal/higher |", "|---|---|---|---:|---:|---:|---:|---:|---|"])
    for group in report["configurations"]:
        for metric in ("input_tokens", "tool_calls_attempted", "wall_seconds"):
            stats = group["within_block_cost_differences"]["all_planned_pairs"][metric]
            values = (str(group["agent"]) + "/" + str(group["model"]), metric, f"{stats['known']}/{stats['missing']}",
                      *(stats[k] for k in ("mean", "median", "sd", "min", "max")),
                      "/".join(str(stats[k]) for k in ("zg_lower_cost_count", "equal_cost_count", "zg_higher_cost_count")))
            lines.append("| " + " | ".join(cell(x) for x in values) + " |")
    for trial in report["trials"]:
        lines.extend(["", f"## {trial['trial_id']}", "", "| Phase | Model turns | Input | Output | Calls | Visible bytes |", "|---|---:|---:|---:|---:|---:|"])
        for phase, costs in trial["trace"]["phases"].items():
            values = (phase, *(costs[k] for k in ("model_turns", "input_tokens", "output_tokens", "tool_calls_attempted", "visible_tool_bytes")))
            lines.append("| " + " | ".join(cell(x) for x in values) + " |")
        lines.extend(["", "| Event | Phase | Type | Input / cached read | Tool / result | Visible bytes | Entries |", "|---:|---|---|---|---|---:|---|"])
        for row in trial["trace"]["timeline"]:
            usage = row.get("usage") or {}
            values = (row["sequence"], row["phase"], row["kind"], cell(usage.get("input_tokens")) + "/" + cell(usage.get("cache_read_tokens")),
                      (row.get("name", "") + " / " + row.get("status", "")) if row["kind"] == "tool" else row.get("finish_reason", ""),
                      row.get("visible_bytes"), ", ".join(x["target_id"] for x in row.get("visible_entries") or []))
            lines.append("| " + " | ".join(cell(x) for x in values) + " |")
        failures = [x for x in trial["trace"]["timeline"] if x.get("status") == "error"]
        for failure in failures:
            lines.extend(["", f"- Tool failure `{failure['call_id']}`: {cell(failure.get('error'))}"])
        if trial["profile"] == "zvec-grep" and trial["metrics"].get("zg_tool_calls_successful") == 0:
            lines.extend(["", "- Zero successful native ZG tool calls observed; this trial remains in its planned treatment arm."])
        for error in trial["errors"] + trial["trace"]["diagnostics"]:
            lines.append("- Trace diagnostic: " + cell(error))
    lines.extend(["", "Definitions:", ""] + [f"- {key}: {value}" for key, value in report["definitions"].items()])
    lines.extend(["", "Limitations:", ""] + ["- " + x for x in report["limitations"]] + [""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("analyze")
    command.add_argument("--runs-dir", type=Path, required=True)
    command.add_argument("--case", type=Path, required=True)
    command.add_argument("--entries", type=Path)
    command.add_argument("--source-root", type=Path)
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    entries = None
    if args.entries:
        from .retrieval_eval import load_manifest
        entries = load_manifest(args.entries, source_root=args.source_root)
    report = analyze_runs(args.runs_dir, _json(args.case), entries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()
