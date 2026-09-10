"""Offline extraction of the complete first ZG decision round, without query replay.

Native event order establishes observed order, not a causal order within one
model message. Exact strings are retained, including strings that resemble JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

INSTRUCTION_SUFFIX = "\n\nAnswer using the repository at /app. This is a read-only QA task. Support the important claims with source file paths and line references. Do not modify files."
NATIVE_NAMES = ("opencode.txt", "qodercli-stream.jsonl", "qodercli.txt")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_text(x) for x in value]
        return "\n".join(parts) if all(isinstance(x, str) for x in parts) else None
    if isinstance(value, dict) and value.get("type") in (None, "text"):
        return value.get("text") if isinstance(value.get("text"), str) else None
    return None


def _zg(name: Any) -> bool:
    # Deliberately exclude plain search and unknown/invalid-tool dispatchers.
    return isinstance(name, str) and "zvec_grep_search" in name.replace("-", "_").lower()


def _treatment(profile: Any) -> bool:
    return isinstance(profile, str) and profile.lower() in {"zg", "zvec-grep", "zvec_grep"}


class _Sources:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files: dict[str, dict[str, Any]] = {}
        self.errors: list[dict[str, Any]] = []

    def read(self, path: Path) -> str:
        data = path.read_bytes()
        key = str(path.resolve().relative_to(self.root))
        self.files[key] = {"path": key, "sha256": _sha(data), "bytes": len(data)}
        return data.decode("utf-8", errors="replace")

    def ref(self, path: Path, line: int | None = None, pointer: str | None = None) -> dict[str, Any]:
        key = str(path.resolve().relative_to(self.root))
        if key not in self.files:
            self.read(path)
        return {**self.files[key], "line": line, "pointer": pointer}

    def json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            value = json.loads(self.read(path))
            if isinstance(value, dict):
                return value
            raise ValueError("expected JSON object")
        except (ValueError, OSError) as error:
            self.errors.append({"path": str(path), "error": str(error)})
            return {}

    def events(self, path: Path) -> tuple[list[tuple[int, dict[str, Any]]], list[str]]:
        events, errors = [], []
        for line, raw in enumerate(self.read(path).splitlines(), 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("expected object")
                events.append((line, value))
            except ValueError:
                errors.append(f"Invalid JSON event at line {line}")
        return events, errors


def _scope(event: dict[str, Any]) -> str:
    return str(event.get("session_id") or event.get("sessionID") or "unknown-session") + ":" + str(event.get("parent_tool_use_id") or "root")


def _native(path: Path, sources: _Sources) -> dict[str, Any]:
    events, errors = sources.events(path)
    adapter = "opencode" if any(e.get("type") in {"step_start", "step_finish", "tool_use"} and isinstance(e.get("part"), dict) for _, e in events) else "qodercli"
    turns: dict[str, dict[str, Any]] = {}
    calls: dict[str, dict[str, Any]] = {}
    results = []
    terminal = False

    def turn(key: str, message_id: Any, scope: str, line: int) -> dict[str, Any]:
        return turns.setdefault(key, {"id": key, "message_id": message_id, "scope": scope,
                                      "start_line": line, "sources": [], "usage_snapshots": []})

    def call(key: str, call_id: Any, name: Any, arguments: Any, t: dict[str, Any], line: int, pointer: str) -> dict[str, Any]:
        if call_id is None:
            errors.append(f"Missing call ID at line {line}; generated occurrence identity only")
        row = calls.setdefault(key, {"id": key, "call_id": call_id, "message_id": t["message_id"],
            "model_turn_id": t["id"], "scope": t["scope"], "tool_name": name, "raw_arguments": arguments,
            "is_zg": _zg(name), "first_native_line": line, "source": sources.ref(path, line, pointer),
            "status": "unknown", "visible_text": None, "visible_payload": None,
            "returned_line": None, "start_ms": None, "end_ms": None, "argument_snapshots": []})
        snapshot = {"arguments": arguments, "source": sources.ref(path, line, pointer)}
        if not row["argument_snapshots"] or row["argument_snapshots"][-1]["arguments"] != arguments:
            row["argument_snapshots"].append(snapshot)
        # Complete state snapshots replace earlier pending-state arguments, not calls.
        row["raw_arguments"] = arguments
        return row

    for line, event in events:
        scope = _scope(event)
        kind = event.get("type")
        if adapter == "opencode":
            part = event.get("part") or {}
            mid = part.get("messageID")
            if kind not in {"step_start", "step_finish", "tool_use", "text", "reasoning"}:
                continue
            if not mid:
                errors.append(f"Missing model message ID at line {line}")
            key = scope + ":" + str(mid or f"unidentified-{line}")
            t = turn(key, mid, scope, line)
            t["sources"].append(sources.ref(path, line))
            if kind == "step_start":
                terminal = False
            if kind == "step_finish":
                t["usage_snapshots"].append({"usage": part.get("tokens"), "source": sources.ref(path, line, "/part/tokens")})
                if part.get("reason") in {"stop", "end_turn"}:
                    terminal = True
            elif kind == "tool_use":
                state = part.get("state") or {}
                cid = part.get("callID")
                row = call(scope + ":" + str(cid or f"unidentified-{line}"), cid, part.get("tool"), state.get("input"), t, line, "/part/state/input")
                status = state.get("status") or "unknown"
                if row["status"] not in {"completed", "error"} or status in {"completed", "error"}:
                    row["status"] = status
                    row["visible_payload"] = state.get("output")
                    row["visible_text"] = _text(state.get("output"))
                    row["error"] = state.get("error")
                    if row["visible_text"] is None and isinstance(state.get("error"), str):
                        row["visible_text"] = state["error"]
                    times = state.get("time") or {}
                    row.update(start_ms=times.get("start"), end_ms=times.get("end"))
                    if status in {"completed", "error"}:
                        row["returned_line"] = line
                        row["result_source"] = sources.ref(path, line, "/part/state")
        else:
            message = event.get("message") or {}
            blocks = message.get("content") or []
            if kind == "result":
                terminal = True
            if kind == "assistant":
                terminal = False
                mid = message.get("id")
                if not mid:
                    errors.append(f"Missing model message ID at line {line}")
                t = turn(scope + ":" + str(mid or f"unidentified-{line}"), mid, scope, line)
                t["sources"].append(sources.ref(path, line))
                if "usage" in message:
                    t["usage_snapshots"].append({"usage": message["usage"], "source": sources.ref(path, line, "/message/usage")})
                for i, block in enumerate(blocks if isinstance(blocks, list) else []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        cid = block.get("id")
                        call(scope + ":" + str(cid or f"unidentified-{line}-{i}"), cid, block.get("name"), block.get("input"), t, line, f"/message/content/{i}/input")
            elif kind == "user":
                for i, block in enumerate(blocks if isinstance(blocks, list) else []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        results.append((scope + ":" + str(block.get("tool_use_id")), block, line, i))
    for key, block, line, index in results:
        if key not in calls:
            errors.append(f"Orphan native tool result at line {line}")
            continue
        calls[key].update(status="error" if block.get("is_error") is True else "completed",
                          visible_payload=block.get("content"), visible_text=_text(block.get("content")),
                          returned_line=line, result_source=sources.ref(path, line, f"/message/content/{index}"))
    ordered_turns = sorted(turns.values(), key=lambda t: t["start_line"])
    for index, t in enumerate(ordered_turns, 1):
        t["model_turn_index"] = index
    return {"adapter": adapter, "turns": ordered_turns, "calls": list(calls.values()),
            "parse_diagnostics": errors, "terminal_event_observed": terminal,
            "complete_observed_trace": terminal and bool(ordered_turns) and not errors}


def _query_fields(arguments: Any) -> list[dict[str, Any]]:
    """Extract literal query fields; never JSON-decode an array-looking string."""
    if not isinstance(arguments, dict):
        return []
    found = []
    for field in ("query", "queries", "fts", "vector", "hybrid"):
        value = arguments.get(field)
        values = enumerate(value) if isinstance(value, list) else [(None, value)]
        for index, text in values:
            if isinstance(text, str):
                found.append({"text": text, "pointer": "/" + field + (f"/{index}" if index is not None else ""),
                              "mode": field if field in {"fts", "vector", "hybrid"} else None})
    for index, route in enumerate(arguments.get("routes") or []):
        if isinstance(route, dict) and isinstance(route.get("query"), str):
            found.append({"text": route["query"], "pointer": f"/routes/{index}/query", "mode": route.get("mode")})
    return found


def _sidecars(path: Path, sources: _Sources) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], []
    events, errors = sources.events(path)
    found = []
    for line, event in events:
        if event.get("event") != "search":
            continue
        diagnostics = (event.get("result") or {}).get("diagnostics") or {}
        found.append({"source": sources.ref(path, line), "call_id": event.get("call_id") or event.get("callID"),
                      "sequence": event.get("sequence"), "request": event.get("request"),
                      "executed_routes": (diagnostics.get("index") or {}).get("routes"),
                      "query_groups": (diagnostics.get("index") or {}).get("queryGroups"),
                      "status": event.get("status"), "error": event.get("error"),
                      "visible_text": event.get("text"), "reported_text_sha256": event.get("text_sha256"),
                      "text_sha256": _sha(event["text"].encode()) if isinstance(event.get("text"), str) else None,
                      "started_at": event.get("started_at"), "duration_ms": event.get("duration_ms")})
    return found, errors


def _link_backend(call: dict[str, Any], sidecars: list[dict[str, Any]]) -> None:
    candidates = [x for x in sidecars if call["call_id"] is not None and x["call_id"] == call["call_id"]]
    method = "explicit_call_id"
    if not candidates and isinstance(call["visible_text"], str):
        text_hash = _sha(call["visible_text"].encode())
        candidates = [x for x in sidecars if x["text_sha256"] == text_hash]
        method = "exact_visible_text_sha256"
        if len(candidates) > 1:
            raw_queries = {x["text"] for x in _query_fields(call["raw_arguments"])}
            narrowed = [x for x in candidates if raw_queries and raw_queries <= {q["text"] for q in _query_fields(x["request"])}]
            if narrowed:
                candidates = narrowed
                method += "_and_query_fields"
    call["backend_link"] = {"status": "matched" if len(candidates) == 1 else "ambiguous" if candidates else "not_available",
                            "method": method if candidates else None,
                            "candidate_sources": [x["source"] for x in candidates]}
    call["backend"] = candidates[0] if len(candidates) == 1 else None


def _frequency(rows: list[tuple[Any, str]]) -> dict[str, Any]:
    counts: dict[str, dict[str, Any]] = {}
    for value, trial_id in rows:
        key = _canonical(value)
        item = counts.setdefault(key, {"value": value, "count": 0, "trial_ids": []})
        item["count"] += 1
        item["trial_ids"].append(trial_id)
    variants = sorted(counts.values(), key=lambda x: (-x["count"], _canonical(x["value"])))
    return {"observed_trials": len(rows), "unique_values": len(variants),
            "modal_count": variants[0]["count"] if variants else None,
            "modal_fraction": variants[0]["count"] / len(rows) if rows else None,
            "variants": variants}


def _question(directory: Path, manifest: dict[str, Any], sources: _Sources, override: str | None) -> tuple[str | None, Any, str | None]:
    if override is not None:
        return override, {"kind": "explicit_argument"}, None
    for name in ("case.json", "entries.json"):
        path = directory / name
        value = sources.json(path)
        if isinstance(value.get("question"), str):
            return value["question"], sources.ref(path, pointer="/question"), None
    if isinstance(manifest.get("question"), str):
        return manifest["question"], sources.ref(directory / "manifest.json", pointer="/question"), None
    path = directory / "instruction.json"
    instruction = sources.json(path).get("text")
    if isinstance(instruction, str) and instruction.endswith(INSTRUCTION_SUFFIX):
        return instruction[:-len(INSTRUCTION_SUFFIX)], sources.ref(path, pointer="/text"), instruction
    return None, None, instruction if isinstance(instruction, str) else None


def _initial_request(agent_dir: Path, sources: _Sources) -> dict[str, Any]:
    path = agent_dir / "wire.jsonl"
    if not path.exists():
        return {"status": "not_available", "reason": "No wire trace; initial request identity unknown"}
    events, errors = sources.events(path)
    first = next(((line, event) for line, event in events
                  if event.get("event") == "request" and event.get("tool_names")), None)
    if first is None:
        return {"status": "not_available", "reason": "No request with a nonempty tool catalogue", "diagnostics": errors}
    line, event = first
    rid = event.get("request_id")
    body_path = agent_dir / "wire-requests" / f"request-{rid:03d}.json" if isinstance(rid, int) else None
    body = sources.json(body_path) if body_path is not None else {}
    captured_hash = event.get("request_sha256")
    # The proxy persisted pretty JSON; OpenCode sent insertion-order compact JSON.
    # A matching hash verifies reconstruction. A mismatch remains explicit, not a
    # claim that this serialization is the original wire body.
    reconstructed_hash = _sha(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()) if body else None
    schema = body.get("tools")
    schema_hash = _sha(json.dumps(schema, sort_keys=True).encode()) if isinstance(schema, list) else None
    return {"status": "observed" if body else "metadata_only", "request_id": rid,
            "source": sources.ref(path, line), "body_source": sources.ref(body_path) if body else None,
            "model": body.get("model") if body else event.get("model"),
            "temperature": body.get("temperature") if body else event.get("temperature"),
            "top_p": body.get("top_p") if body else event.get("top_p"),
            "parameter_presence": {key: key in body for key in ("model", "temperature", "top_p")} if body else None,
            "tool_names": event.get("tool_names"), "schema_sha256": event.get("schema_sha256"),
            "computed_schema_sha256": schema_hash,
            "schema_sha256_verified": schema_hash == event.get("schema_sha256") if schema_hash else None,
            "tool_names_verified": event.get("tool_names") == [tool.get("function", {}).get("name") for tool in schema] if isinstance(schema, list) else None,
            "request_sha256": captured_hash, "reconstructed_request_sha256": reconstructed_hash,
            "request_sha256_verified": reconstructed_hash == captured_hash if reconstructed_hash and captured_hash else None,
            "canonical_body_sha256": _sha(_canonical(body).encode()) if body else None,
            "metadata_matches_body": all(event.get(k) == body.get(k) for k in ("model", "temperature", "top_p")) if body else None,
            "scope": "First nonempty-tool-catalogue request only; does not establish equality of later first-ZG prompts",
            "diagnostics": errors}


def _trial(directory: Path, planned: dict[str, Any], sources: _Sources) -> dict[str, Any]:
    tid = planned["trial_id"]
    agent_dir = (directory / planned.get("trajectory_path", f"{tid}/agent/trajectory.json")).parent
    result = sources.json(agent_dir.parent / "result.json")
    row = {"trial_id": tid, "profile": planned.get("profile"), "repetition": planned.get("repetition"),
           "block_id": planned.get("block_id"), "agent": planned.get("agent"), "model": planned.get("model"),
           "execution_status": result.get("status") or planned.get("status") or "unknown",
           "zg_adoption_observed": None, "zg_tool_calls_attempted": None, "zg_tool_calls_successful": None,
           "zg_tool_calls_attempted_lower_bound": None, "first_zg_call": None, "first_zg_decision_round": None,
           "query_evidence_status": "not_available", "diagnostics": []}
    row["initial_request_identity"] = _initial_request(agent_dir, sources)
    native_path = next((agent_dir / name for name in NATIVE_NAMES if (agent_dir / name).exists()), None)
    sidecars, errors = _sidecars(agent_dir / "zg-trace.jsonl", sources)
    row["diagnostics"].extend(errors)
    row["observed_backend_searches"] = len(sidecars) if (agent_dir / "zg-trace.jsonl").exists() and not errors else None
    if native_path is None:
        row["diagnostics"].append("Native trace missing; first model decision and raw parameters unavailable")
        if sidecars:
            row["zg_adoption_observed"] = True
        return row
    native = _native(native_path, sources)
    row["native_source"] = sources.ref(native_path)
    row["native_adapter"] = native["adapter"]
    row["native_trace_complete"] = native["complete_observed_trace"]
    row["diagnostics"].extend(native["parse_diagnostics"])
    calls = native["calls"]
    zg_calls = [x for x in calls if x["is_zg"]]
    row["zg_tool_calls_attempted_lower_bound"] = len(zg_calls)
    if native["complete_observed_trace"]:
        row["zg_tool_calls_attempted"] = len(zg_calls)
        row["zg_tool_calls_successful"] = sum(x["status"] == "completed" for x in zg_calls)
    row["zg_adoption_observed"] = True if zg_calls or sidecars else False if native["complete_observed_trace"] else None
    if not zg_calls:
        row["query_evidence_status"] = "no_call" if row["zg_adoption_observed"] is False else "not_available"
        return row
    first = zg_calls[0]
    for c in zg_calls:
        _link_backend(c, sidecars)
    t = next(t for t in native["turns"] if t["id"] == first["model_turn_id"])
    same = [c for c in calls if c["model_turn_id"] == t["id"]]
    batch = [c for c in same if c["is_zg"]]
    # Only earlier messages returned before this message began count as prior context.
    prior = [c for c in calls if c["scope"] == t["scope"] and c["model_turn_id"] != t["id"] and c["returned_line"] is not None and c["returned_line"] < t["start_line"]]
    returned_same = [c for c in same if c["id"] != first["id"] and c["returned_line"] is not None and c["returned_line"] < first["first_native_line"]]
    overlaps = []
    for i, a in enumerate(batch):
        for b in batch[i + 1:]:
            times = [a["start_ms"], a["end_ms"], b["start_ms"], b["end_ms"]]
            if all(isinstance(v, (float, int)) and not isinstance(v, bool) for v in times) and max(times[0], times[2]) < min(times[1], times[3]):
                overlaps.append([a["call_id"], b["call_id"]])
    row.update(first_zg_call=first, query_evidence_status="observed" if not native["parse_diagnostics"] else "observed_with_parse_gaps")
    row["first_zg_decision_round"] = {**t, "all_tool_calls": same, "zg_calls": batch,
        "prior_turn_feedback": prior, "has_prior_turn_feedback": bool(prior) if not native["parse_diagnostics"] else True if prior else None,
        "same_message_feedback_returned_before_first_zg_native_event": returned_same,
        "overlapping_zg_execution_pairs": overlaps,
        "same_message_is_not_proof_of_feedback_conditioned_rewrite": True}
    return row


def _summaries(trials: list[dict[str, Any]]) -> dict[str, Any]:
    arms = [x for x in trials if _treatment(x["profile"])]
    text_rows, raw_rows, batch_rows = [], [], []
    for row in arms:
        first, batch = row["first_zg_call"], row["first_zg_decision_round"]
        if first is not None:
            args = first["raw_arguments"]
            if isinstance(args, dict) and isinstance(args.get("query"), str):
                text_rows.append((args["query"], row["trial_id"]))
            if args is not None:
                raw_rows.append((args, row["trial_id"]))
        if batch is not None:
            batch_rows.append(([{"tool_name": c["tool_name"], "raw_arguments": c["raw_arguments"]} for c in batch["zg_calls"]], row["trial_id"]))
    initial_rows = [(row["initial_request_identity"]["canonical_body_sha256"], row["trial_id"])
                    for row in arms if row.get("initial_request_identity", {}).get("canonical_body_sha256")]
    return {"initial_request_body_consistency": _frequency(initial_rows),
            "adoption_summary": {"planned_treatment_trials": len(arms),
             "adopted_trials": sum(x["zg_adoption_observed"] is True for x in arms),
             "no_call_trials": sum(x["zg_adoption_observed"] is False for x in arms),
             "unknown_trials": sum(x["zg_adoption_observed"] is None for x in arms)},
            "first_main_query_text_consistency": _frequency(text_rows),
            "first_raw_arguments_consistency": _frequency(raw_rows),
            "first_zg_decision_round_batch_consistency": _frequency(batch_rows)}


def _subset(directory: Path, sources: _Sources, override: str | None) -> dict[str, Any]:
    path = directory / "ci-audit.json"
    data = sources.json(path)
    records = data.get("records") or []
    meta = next((x for x in records if x.get("kind") == "group"), {})
    trials = []
    for i, record in enumerate(records):
        if not record.get("trial_id"):
            continue
        metrics = record.get("metrics") or {}
        attempted, successful = metrics.get("zg_tool_calls_attempted"), metrics.get("zg_tool_calls_successful")
        known = isinstance(attempted, int) and not isinstance(attempted, bool) and attempted >= 0
        trials.append({"trial_id": record["trial_id"], "profile": record.get("profile"),
                       "execution_status": record.get("status") or "unknown",
                       "zg_adoption_observed": attempted > 0 if known else None,
                       "zg_tool_calls_attempted": attempted if known else None,
                       "zg_tool_calls_successful": successful,
                       "source": sources.ref(path, pointer=f"/records/{i}"),
                       "initial_request_identity": {"status": "not_available", "reason": "CI subset lacks initial request body"},
                       "query_evidence_status": "not_available", "first_zg_call": None,
                       "first_zg_decision_round": None,
                       "diagnostics": ["CI-derived counts only; native first-query arguments and batches unavailable"]})
    return {"group": directory.name, "agent": meta.get("agent"), "model": meta.get("model"),
            "source_kind": "ci_derived_subset", "original_question": override,
            "question_source": {"kind": "explicit_argument"} if override is not None else None,
            "trials": trials, **_summaries(trials)}


def _catalog(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queries: dict[str, dict[str, Any]] = {}
    for group in groups:
        for trial in group["trials"]:
            batch = trial.get("first_zg_decision_round")
            if not batch:
                continue
            for call in batch["zg_calls"]:
                fields = [("raw_arguments", q) for q in _query_fields(call["raw_arguments"])]
                backend = call.get("backend")
                if backend:
                    fields.extend(("backend_request", q) for q in _query_fields(backend["request"]))
                    fields.extend(("backend_executed_routes", q) for q in _query_fields({"routes": backend["executed_routes"]}))
                by_text: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for layer, field in fields:
                    by_text[field["text"]].append({"layer": layer, "pointer": field["pointer"], "mode": field["mode"]})
                for text, locations in by_text.items():
                    item = queries.setdefault(text, {"query_id": "query-" + _sha(text.encode())[:16], "text": text, "occurrences": []})
                    item["occurrences"].append({"group": group["group"], "trial_id": trial["trial_id"],
                        "call_id": call["call_id"], "message_id": call["message_id"], "locations": locations,
                        "native_call_status": call["status"], "backend_status": backend["status"] if backend else None,
                        "backend_link_status": call["backend_link"]["status"], "source": call["source"]})
    for item in queries.values():
        item["occurrence_count"] = len(item["occurrences"])
        item["trial_count"] = len({(x["group"], x["trial_id"]) for x in item["occurrences"]})
    return sorted(queries.values(), key=lambda x: x["query_id"])


def analyze(runs_dir: Path, *, question: str | None = None) -> dict[str, Any]:
    """Read one experiment or an immediate directory of experiment groups."""
    root = Path(runs_dir).resolve()
    if not root.is_dir():
        raise ValueError(f"Runs directory does not exist: {root}")
    directories = [root] if any((root / name).exists() for name in ("plan.json", "ci-audit.json")) else [
        p for p in sorted(root.iterdir()) if p.is_dir() and any((p / name).exists() for name in ("plan.json", "ci-audit.json"))]
    if not directories:
        raise ValueError("No experiment plan.json or ci-audit.json found")
    sources = _Sources(root)
    groups = []
    for directory in directories:
        if not (directory / "plan.json").exists():
            groups.append(_subset(directory, sources, question))
            continue
        manifest = sources.json(directory / "manifest.json")
        plan = sources.json(directory / "plan.json")
        original, question_source, instruction = _question(directory, manifest, sources, question)
        trials = [_trial(directory, p, sources) for p in plan.get("trials") or []]
        groups.append({"group": directory.name, "agent": manifest.get("agent"), "model": manifest.get("model"),
                       "source_kind": "native_artifact", "case_id": plan.get("case_id") or manifest.get("case_id"),
                       "original_question": original, "question_source": question_source, "instruction": instruction,
                       "trials": trials, **_summaries(trials)})
    return {"schema_version": 1, "protocol": "offline-first-query-analysis-v1", "runs_dir": str(root),
            "scope": {"offline_extraction_only": True, "query_replay_performed": False, "new_e2e_runs": 0,
                      "semantic_intent_scoring_performed": False},
            "definitions": {
                "first_zg_call": "First observed native ZG tool occurrence, in native event order.",
                "first_zg_decision_round": "All tool calls sharing that call's scoped model message ID, including concurrent calls and later native blocks.",
                "prior_feedback": "A different model message's result returned before the first ZG message began. Same-message returns are recorded separately, not treated as a conditioned rewrite.",
                "raw_arguments": "Literal native argument object; omitted fields stay omitted, effective defaults unknown.",
                "query_catalog_frequency": "One occurrence per distinct exact text per call; layer/route duplicates retained as locations. Repeats are not independent tasks.",
                "consistency": "Exact string equality or canonical JSON equality; no semantic grading or significance claim. Initial body frequency refers to the persisted canonical JSON; original wire equality additionally requires request_sha256_verified=true.",
                "missing": "Missing/incomplete evidence is null/not_available; no-call requires a complete observed native trace or explicit CI-derived zero count.",
                "backend_link": "Explicit call ID or unique exact visible-text hash (and query fields when needed); ambiguous matches remain unresolved."},
            "groups": groups, "query_catalog": _catalog(groups),
            "input_artifacts": sorted(sources.files.values(), key=lambda x: x["path"]),
            "diagnostics": sources.errors}


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# 首次 ZG Query 离线采集", "", "仅离线提取既有轨迹，未回放 query、未新增 E2E，也未进行语义意图评分。省略参数不猜默认值；重复 query 不是独立任务。", "",
             "| 组合 | 已知采用 / 计划 | 无调用 | 未知 | 首 query 文本种数 / 已观察 | 首参数种数 | 首批次种数 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for group in report["groups"]:
        a = group["adoption_summary"]
        text, raw, batch = (group[k] for k in ("first_main_query_text_consistency", "first_raw_arguments_consistency", "first_zg_decision_round_batch_consistency"))
        fmt = lambda x: str(x["unique_values"]) if x["observed_trials"] else "N/A"
        lines.append(f"| {group['group']} | {a['adopted_trials']} / {a['planned_treatment_trials']} | {a['no_call_trials']} | {a['unknown_trials']} | {fmt(text)} / {text['observed_trials']} | {fmt(raw)} | {fmt(batch)} |")
    for group in report["groups"]:
        lines += ["", f"## {group['group']}", "", "原题：" + (group.get("original_question") or "not_available"), ""]
        initial = [t["initial_request_identity"] for t in group["trials"] if _treatment(t.get("profile")) and t.get("initial_request_identity", {}).get("status") == "observed"]
        consistency = group["initial_request_body_consistency"]
        unique = consistency["unique_values"] if initial else "N/A"
        verified = sum(x["request_sha256_verified"] is True for x in initial)
        lines += [f"首个非空工具目录请求：完整 body 已观察 {len(initial)} 次；body 种数 {unique}；wire body SHA 校验通过 {verified} 次。此项只描述初始请求，不能推断晚于第一轮的 ZG 输入相同。", ""]
        for trial in group["trials"]:
            if not _treatment(trial.get("profile")):
                continue
            batch = trial["first_zg_decision_round"]
            lines += [f"- **{trial['trial_id']}**：执行 `{trial['execution_status']}`；query `{trial['query_evidence_status']}`。"]
            if not batch:
                lines.append(f"  已知 ZG 尝试次数：{trial['zg_tool_calls_attempted'] if trial['zg_tool_calls_attempted'] is not None else 'N/A'}；首参数 / 批次 N/A。")
                continue
            prior = batch["has_prior_turn_feedback"]
            lines.append(f"  首次模型轮 {batch['model_turn_index']}；ZG 批次 {len(batch['zg_calls'])} 次；前轮反馈 {'有' if prior is True else '无' if prior is False else '未知'}；同消息先返回 {len(batch['same_message_feedback_returned_before_first_zg_native_event'])} 次；已证实执行重叠 {len(batch['overlapping_zg_execution_pairs'])} 对。")
            for call in batch["zg_calls"]:
                src = call["source"]
                lines.append(f"  `{call['call_id']}` / `{call['message_id']}`；来源 `{src['path']}:{src['line']}`。")
                lines += ["", "```json", json.dumps(call["raw_arguments"], ensure_ascii=False, sort_keys=True), "```", ""]
    lines += ["完整原始可见输出、backend request / 实际路由、每项来源哈希与行号见同名 JSON。CI 派生子集只能支持其已保留字段；query 缺失不表示没有调用。", ""]
    return "\n".join(lines)


def write_report(report: dict[str, Any], output: Path) -> None:
    """Write only new derived JSON/Markdown; refuse to overwrite an input file."""
    output = Path(output).resolve()
    markdown = output.with_suffix(".md")
    if output == markdown:
        raise ValueError("Output must have a non-.md suffix (normally .json)")
    inputs = {(Path(report["runs_dir"]) / x["path"]).resolve() for x in report["input_artifacts"]}
    if output in inputs or markdown in inputs:
        raise ValueError("Refusing to overwrite an input artifact")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown.write_text(render_markdown(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("analyze")
    command.add_argument("--runs-dir", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--question")
    args = parser.parse_args(argv)
    report = analyze(args.runs_dir, question=args.question)
    write_report(report, args.output)


if __name__ == "__main__":
    main()
