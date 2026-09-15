"""Collect released MCP requests, source-ground them, and score public outputs.

Native arguments are annotation requests; exact tools/call params are retained
separately as mcp_request for native replay. No normalized backend request is
invented. This module performs no model or retrieval calls.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import first_query_analysis as first
from .query_trajectory import _assistant_text, _round
from .retrieval_replay import score_request

TOOL = "zvec_grep_search"
LIMITATION = "Observed prior native tool feedback and assistant text only; hidden reasoning, model wire context and compaction are not reconstructed."


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256((value if isinstance(value, str) else _canonical(value)).encode()).hexdigest()


def _identity(prefix: str, value: Any) -> str:
    return prefix + "-" + _digest(value)[:20]


def _text(result: Any) -> str | None:
    return first._text(result.get("content")) if isinstance(result, dict) else None


class _Catalog:
    def __init__(self, case: dict[str, Any], kind: str):
        self.case, self.kind = case, kind
        self.requests, self.annotations, self.occurrences = {}, {}, []
        self.diagnostics, self.groups, self.samples = [], [], []
        context = {"original_question": case["question"], "prior_turn_feedback": [], "prior_assistant_text": []}
        self.add({"name": TOOL, "arguments": {"root": "/app", "query": case["question"], "limit": 10, "autoUpdate": False}},
                 context, None, kind="original", context_status="protocol_defined",
                 limitation="Protocol-defined original question; not an observed Agent request.")

    def add(self, params: dict[str, Any], context: dict[str, Any], occurrence: dict[str, Any] | None, *,
            kind: str = "faithful", context_status: str = "native_observed", limitation: str = LIMITATION) -> None:
        request = params["arguments"]
        rid, cid = _identity("request", params), _identity("context", context)
        aid = _identity("annotation", {"request": request, "context_id": cid, "tool_name": params["name"]})
        unit = self.requests.setdefault(rid, {"request_id": rid, "tool_name": params["name"], "request": copy.deepcopy(request),
                "mcp_request": copy.deepcopy(params), "kind": kind, "occurrences": []})
        annotation = self.annotations.setdefault(aid, {"annotation_id": aid, "request_id": rid, "request_ids": [],
                "context_id": cid, "kind": kind, "request": copy.deepcopy(request), **copy.deepcopy(context),
                "occurrences": [], "context_capture_status": context_status, "context_capture_limitation": limitation})
        if rid not in annotation["request_ids"]:
            annotation["request_ids"].append(rid)
        if occurrence is not None:
            occurrence = {**copy.deepcopy(occurrence), "request_id": rid, "context_id": cid, "annotation_id": aid}
            self.occurrences.append(occurrence)
            unit["occurrences"].append(occurrence)
            annotation["occurrences"].append(occurrence)
            if annotation["kind"] == "original":
                annotation["also_faithful_request"] = True

    def report(self) -> dict[str, Any]:
        queries = {}
        for occurrence in self.occurrences:
            request = self.requests[occurrence["request_id"]]["request"]
            for field in first._query_fields(request):
                text = field["text"]
                queries.setdefault(text, {"query_id": _identity("query", text), "text": text, "occurrences": []})["occurrences"].append(occurrence)
        return {"schema_version": 1, "protocol": "native-mcp-query-diagnosis-v1", "kind": self.kind,
            "case_id": self.case["case_id"], "original_question": self.case["question"], "repo": self.case["repo"],
            "request_catalog": list(self.requests.values()), "annotation_catalog": list(self.annotations.values()),
            "occurrences": self.occurrences, "query_catalog": list(queries.values()), "groups": self.groups, "samples": self.samples,
            "diagnostics": self.diagnostics,
            "definitions": {"request": "Unchanged native MCP arguments; not an internal backend request.",
                "mcp_request": "Exact observed tools/call params, including tool name, for released native replay.",
                "public_output": "MCP text content; association to Agent-visible content requires matching text hash.",
                "sample_size": "Queries and replay repetitions are not additional independent QA tasks."}}


def _tap(path: Path, sources: first._Sources) -> tuple[list[dict[str, Any]], list[Any]]:
    if not path.is_file():
        return [], [{"path": str(path), "status": "missing_mcp_log"}]
    events, diagnostics = sources.events(path)
    requests, pending = [], defaultdict(list)
    for line, event in events:
        message = event.get("effective_message", event.get("message"))
        if not isinstance(message, dict):
            diagnostics.append(f"Missing RPC message at line {line}")
            continue
        key = _canonical(message.get("id"))
        if event.get("direction") == "agent_to_zg" and message.get("method") == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict) or params.get("name") != TOOL:
                continue
            row = {"mcp_request": copy.deepcopy(params), "rpc_id": message.get("id"), "request_line": line,
                   "source": sources.ref(path, line, "/message/params"), "timestamp": event.get("timestamp"),
                   "response_line": None, "public_text": None, "public_text_sha256": None, "mcp_status": "missing_response"}
            requests.append(row)
            pending[key].append(row)
        elif event.get("direction") == "zg_to_agent" and "id" in message and not message.get("method"):
            matches = pending.pop(key, [])
            if len(matches) > 1:
                for row in matches:
                    row["mcp_status"] = "ambiguous_rpc_id"
                diagnostics.append(f"Overlapping duplicate RPC IDs at response line {line}")
            elif len(matches) == 1:
                row, result = matches[0], message.get("result")
                text = _text(result)
                row.update(response_line=line, response_source=sources.ref(path, line, "/message/result"),
                           public_text=text, public_text_sha256=_digest(text) if text is not None else None,
                           mcp_result=copy.deepcopy(result), rpc_error=message.get("error"),
                           mcp_status="error" if message.get("error") is not None or isinstance(result, dict) and result.get("isError") is True else "completed")
    return requests, diagnostics


def _link(requests: list[dict[str, Any]], native: dict[str, Any]) -> None:
    native_groups, rpc_groups = defaultdict(list), defaultdict(list)
    for call in native["calls"]:
        if call["is_zg"] and isinstance(call["raw_arguments"], dict) and isinstance(call["visible_text"], str):
            native_groups[(_canonical(call["raw_arguments"]), _digest(call["visible_text"]))].append(call)
    for row in requests:
        row.update(native_link_status="unknown", native_link_method=None, native_call=None)
        if isinstance(row["mcp_request"].get("arguments"), dict) and row["public_text_sha256"] is not None:
            rpc_groups[(_canonical(row["mcp_request"]["arguments"]), row["public_text_sha256"])].append(row)
    for signature, rpc in rpc_groups.items():
        calls = sorted(native_groups.get(signature, []), key=lambda c: c["first_native_line"])
        rpc.sort(key=lambda r: r["request_line"])
        unique = len(calls) == len(rpc) == 1
        serial = (len(calls) == len(rpc) and len(calls) > 1 and len({c["model_turn_id"] for c in calls}) == len(calls)
                  and all(a["response_line"] is not None and a["response_line"] < b["request_line"] for a, b in zip(rpc, rpc[1:])))
        if unique or serial:
            for row, call in zip(rpc, calls):
                row.update(native_link_status="matched", native_call=call,
                           native_link_method="exact_arguments_and_public_hash" if unique else "serial_occurrence_order_arguments_and_public_hash")
        elif calls:
            for row in rpc:
                row["native_link_status"] = "ambiguous"


def build_catalog(runs_dirs: list[Path], case: dict[str, Any]) -> dict[str, Any]:
    catalog = _Catalog(case, "e2e")
    seen_groups = set()
    for directory in map(Path, runs_dirs):
        directory = directory.resolve()
        sources = first._Sources(directory)
        plan = sources.json(directory / "plan.json")
        if plan.get("case_id") != case["case_id"]:
            raise ValueError("E2E plan and case differ")
        manifest = next((sources.json(directory / name) for name in ("experiment-manifest.json", "manifest.json") if (directory / name).is_file()), {})
        group = plan.get("group_id") or plan.get("group") or manifest.get("group_id") or manifest.get("group") or directory.name
        if not isinstance(group, str) or group in seen_groups:
            raise ValueError("groups require distinct string identifiers")
        seen_groups.add(group)
        group_rows = []
        for trial in plan.get("trials", []):
            tid = trial["trial_id"]
            agent = (directory / trial.get("trajectory_path", f"{tid}/agent/trajectory.json")).resolve().parent
            if not agent.is_relative_to(directory):
                raise ValueError("trajectory path escapes run directory")
            native_path = next((agent / name for name in first.NATIVE_NAMES if (agent / name).is_file()), None)
            native = first._native(native_path, sources) if native_path else {"turns": [], "calls": [], "parse_diagnostics": ["missing native trace"]}
            if native_path:
                texts = _assistant_text(native_path, sources)
                for turn in native["turns"]:
                    turn["assistant_text"] = texts.get(turn["id"], [])
            tap_path = next((p for p in (agent / "native-mcp.jsonl", agent.parent / "logs/native-mcp.jsonl", agent.parent / "native-mcp.jsonl") if p.is_file()), agent / "native-mcp.jsonl")
            requests, errors = _tap(tap_path, sources)
            _link(requests, native)
            rounds = {t["id"]: _round(t, native["calls"], native["turns"], case["question"], index, bool(native["parse_diagnostics"]))
                      for index, t in enumerate([t for t in native["turns"] if any(c["is_zg"] and c["model_turn_id"] == t["id"] for c in native["calls"])], 1)}
            for index, record in enumerate(requests, 1):
                if not isinstance(record["mcp_request"].get("arguments"), dict):
                    catalog.diagnostics.append({"group": group, "trial_id": tid, "status": "invalid_native_arguments", "record": record})
                    continue
                call = record["native_call"]
                decision = rounds.get(call["model_turn_id"]) if call else None
                oid = _identity("occurrence", [group, tid, record["source"]])
                context = decision["annotation_context"] if decision else {"original_question": case["question"], "prior_turn_feedback": [],
                    "prior_assistant_text": [], "incomplete_context_source": oid}
                occurrence = {"occurrence_id": oid, "group": group, "trial_id": tid, "sample_id": f"{group}/{tid}",
                    "profile": trial.get("profile"), "arm": trial.get("arm"), "prompt_variant": trial.get("prompt_version"),
                    "request_index": index, **{k: v for k, v in record.items() if k not in ("native_call", "mcp_request")},
                    "native_call_id": call["call_id"] if call else None, "native_source": call.get("source") if call else None,
                    "native_result_source": call.get("result_source") if call else None,
                    "model_turn_index": decision.get("model_turn_index") if decision else None,
                    "zg_decision_round_index": decision.get("zg_decision_round_index") if decision else None}
                catalog.add(record["mcp_request"], context, occurrence, context_status=decision["context_capture_status"] if decision else "unknown",
                            limitation=LIMITATION if decision else "Native call association is missing or ambiguous; prior feedback is unknown, not an empty first-turn context.")
            group_rows.append({"trial_id": tid, "profile": trial.get("profile"), "arm": trial.get("arm"), "mcp_requests": len(requests),
                               "native_parse_diagnostics": native["parse_diagnostics"], "tap_diagnostics": errors})
        catalog.groups.append({"group": group, "runs_dir": str(directory), "manifest": manifest, "trials": group_rows,
                               "input_artifacts": list(sources.files.values())})
        catalog.diagnostics.extend(sources.errors)
    return catalog.report()


def _wire_context(body: dict[str, Any], question: str, state_id: str) -> dict[str, Any]:
    calls, feedback, texts = {}, [], []
    for message in body.get("messages", []):
        if message.get("role") == "assistant":
            if isinstance(message.get("content"), str) and message["content"]:
                texts.append(message["content"])
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                raw = function.get("arguments")
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except ValueError:
                    args = {"unparsed_arguments": raw}
                calls[call.get("id")] = {"tool_name": function.get("name"), "raw_arguments": args}
        elif message.get("role") == "tool":
            feedback.append({**calls.get(message.get("tool_call_id"), {"tool_name": "unknown", "raw_arguments": None}),
                             "visible_text": first._text(message.get("content")), "status": "unknown", "tool_call_id": message.get("tool_call_id")})
    return {"original_question": question, "prior_turn_feedback": feedback, "prior_assistant_text": texts, "native_state_id": state_id}


def build_decision_catalog(plan_path: Path, case: dict[str, Any]) -> dict[str, Any]:
    from .prompt_diagnostics import load_plan
    plan_path = Path(plan_path)
    plan = load_plan(plan_path)
    catalog = _Catalog(case, "next_decision_diagnostic")
    for sample in plan["samples"]:
        path = plan_path.parent / "samples" / sample["sample_id"] / "result.json"
        result = _json(path) if path.is_file() else {}
        if result and result.get("plan_sha256") != plan["plan_sha256"]:
            raise ValueError("decision result differs from frozen plan")
        definition = plan["requests"][sample["request_key"]]
        context = _wire_context(definition["request"], case["question"], sample["state_id"])
        calls = result.get("validation", {}).get("calls", [])
        catalog.samples.append({**sample, "status": result.get("status", "missing"), "calls": copy.deepcopy(calls), "result_path": str(path)})
        for index, call in enumerate(calls):
            if not call.get("is_zg") or not call.get("valid") or not first._zg(call.get("name")) or not isinstance(call.get("arguments"), dict):
                continue
            params = {"name": TOOL, "arguments": call["arguments"]}
            occurrence = {"occurrence_id": _identity("decision", [sample["sample_id"], index]), **sample,
                "group": sample["group_id"], "prompt_variant": sample["variant"], "call_index": index, "native_call_id": call.get("call_id"),
                "native_tool_name": call.get("name"), "source": {"path": str(path), "sha256": _digest(path.read_text()), "pointer": f"/validation/calls/{index}"},
                "mcp_status": "not_executed_in_decision_diagnostic", "public_text": None, "public_text_sha256": None,
                "native_link_status": "provider_decision", "native_link_method": "actual_response_tool_call", "request_index": index + 1}
            catalog.add(params, context, occurrence, context_status="captured_model_messages",
                        limitation="Prior feedback extracted from the captured model request; no current response or final answer enters annotation. Agent execution/repair does not occur in this diagnostic.")
    report = catalog.report()
    report.update(plan_sha256=plan["plan_sha256"], plan_path=str(plan_path))
    return report


def _score(text: Any, status: str, unit: dict[str, Any], labels: dict[str, Any], entries: dict[str, Any]) -> dict[str, Any]:
    if status != "completed" or not isinstance(text, str):
        return {"status": "unknown", "reason": "No successful public text output", "score": None}
    try:
        value = score_request(text, unit["request"], labels, entries, context_id=unit["context_id"])
    except (ValueError, KeyError, TypeError) as error:
        return {"status": "unknown", "reason": f"Scoring contract unavailable: {type(error).__name__}", "score": None}
    relevance = value["native"]["query_relevance"]
    return {"status": relevance["status"], "score": value, "target": relevance["target"],
            "reason": relevance.get("reason") or relevance.get("format_reason")}


def score_records(analysis: dict[str, Any], labels: dict[str, Any], entries: dict[str, Any], replayrows: Any) -> dict[str, Any]:
    if labels.get("repo") != analysis.get("repo") or entries.get("repo") != analysis.get("repo"):
        raise ValueError("query labels, entries and observations require the same source revision")
    rows = replayrows.get("rows", replayrows.get("trials", [])) if isinstance(replayrows, dict) else replayrows
    if not isinstance(rows, list):
        raise ValueError("replay rows must be a list")
    by_request_repeat = defaultdict(list)
    for row in rows:
        if type(row.get("repetition")) is int and row["repetition"] in range(1, 6):
            by_request_repeat[row.get("request_id"), row["repetition"]].append(row)
    units = {u["annotation_id"]: u for u in analysis["annotation_catalog"]}
    actual = []
    for occurrence in analysis["occurrences"]:
        scored = _score(occurrence.get("public_text"), occurrence.get("mcp_status", "unknown"), units[occurrence["annotation_id"]], labels, entries)
        actual.append({**copy.deepcopy(occurrence), "assessment": scored, "agent_visible_link_verified": occurrence.get("native_link_status") == "matched"})
    replays = []
    for request in analysis["request_catalog"]:
        rid = request["request_id"]
        observations = []
        for repetition in range(1, 6):
            candidates = by_request_repeat.get((rid, repetition), [])
            row = candidates[0] if len(candidates) == 1 else {}
            status = row.get("status", "unknown") if row else "ambiguous_duplicate" if candidates else "missing"
            result = row.get("result")
            result_text = _text(result)
            text = row.get("text", result_text)
            if isinstance(result, dict) and result.get("isError") is True:
                status = "error"
            if result_text is not None and text != result_text:
                status = "text_result_mismatch"
            observations.append({"repetition": repetition, "status": status, "text": text,
                "public_text_sha256": _digest(text) if isinstance(text, str) else None, "source_rows": copy.deepcopy(candidates)})
        contexts = []
        for annotation in units.values():
            if rid not in annotation.get("request_ids", [annotation["request_id"]]):
                continue
            first = observations[0]
            contexts.append({"annotation_id": annotation["annotation_id"], "context_id": annotation["context_id"], "kind": annotation["kind"],
                             "quality_repetition": 1, "assessment": _score(first["text"], first["status"], annotation, labels, entries)})
        hashes = [o["public_text_sha256"] for o in observations if o["status"] == "completed" and o["public_text_sha256"] is not None]
        replays.append({**copy.deepcopy(request), "observations": observations, "context_assessments": contexts,
                       "stability": {"planned": 5, "completed_text_outputs": len(hashes), "unique_output_hashes": len(set(hashes)),
                                     "identical_all_five": len(set(hashes)) == 1 if len(hashes) == 5 else None}})
    replay_by_id = {r["request_id"]: r for r in replays}
    comparisons = []
    for occurrence in actual:
        replay = replay_by_id[occurrence["request_id"]]["observations"][0]
        known = occurrence.get("mcp_status") == "completed" and replay["status"] == "completed" and occurrence.get("public_text_sha256") is not None and replay["public_text_sha256"] is not None
        comparisons.append({"occurrence_id": occurrence["occurrence_id"], "request_id": occurrence["request_id"],
            "group": occurrence.get("group"), "profile": occurrence.get("profile"), "prompt_variant": occurrence.get("prompt_variant"),
            "original_vs_replay_text_identical": occurrence["public_text_sha256"] == replay["public_text_sha256"] if known else None,
            "agent_visible_link_verified": occurrence["agent_visible_link_verified"], "replay_repetition": 1})
    known_ids = set(replay_by_id)
    return {"schema_version": 1, "case_id": analysis["case_id"], "metric_profile": "entry-ranking-v1", "kind": analysis.get("kind"),
            "actual": actual, "replays": replays, "actual_vs_replay": comparisons,
            "unplanned_replay_rows": [r for r in rows if r.get("request_id") not in known_ids or type(r.get("repetition")) is not int or r["repetition"] not in range(1, 6)],
            "limitations": ["First replay only evaluates quality; repetitions 2–5 test output consistency, never best-of selection.",
                "Actual MCP text and newly replayed text are separate observations; replay does not replace Agent-visible historical evidence.",
                "Unknown output formats and unreviewed query intents remain unknown, not misses."]}


def render_markdown(report: dict[str, Any]) -> str:
    def cell(value):
        return str(value if value is not None else "unknown").replace("|", "\\|").replace("\n", " ")
    lines = [f"# Native retrieval diagnosis: {report['case_id']}", "",
             "Original E2E observations and replay observations remain separate. Replay uses an independently rebuilt normal index; no cross-run index identity is assumed.", "",
             "| Request | Kind | Context | Status | Hit@1 | Hit@5 | Hit@10 | RR@10 | Five outputs identical |",
             "|---|---|---|---|---:|---:|---:|---:|---|"]
    for replay in report.get("replays", []):
        for context in replay.get("context_assessments", []):
            assessment = context.get("assessment", {})
            target = assessment.get("target", {})
            values = [replay["request_id"], context.get("kind"), context.get("context_id"),
                      assessment.get("status"), *[target.get(f"hit_at_{k}") for k in (1, 5, 10)],
                      target.get("rr_at_10"), replay.get("stability", {}).get("identical_all_five")]
            lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    comparisons = report.get("actual_vs_replay", [])
    lines += ["", f"Observed E2E occurrences: {len(report.get('actual', []))}.",
              f"Actual/replay text matches: {sum(r.get('original_vs_replay_text_identical') is True for r in comparisons)}; "
              f"differences: {sum(r.get('original_vs_replay_text_identical') is False for r in comparisons)}; "
              f"unknown: {sum(r.get('original_vs_replay_text_identical') is None for r in comparisons)}.", "",
              *["- " + text for text in report.get("limitations", [])], ""]
    return "\n".join(lines)


def screening_evidence(plan_path: Path, analysis: dict[str, Any], labels: dict[str, Any], entries: dict[str, Any], replayrows: Any) -> dict[str, Any]:
    from .prompt_diagnostics import load_plan
    plan = load_plan(Path(plan_path))
    if analysis.get("plan_sha256") != plan["plan_sha256"]:
        raise ValueError("screening analysis and plan differ")
    scores = score_records(analysis, labels, entries, replayrows)
    label_by_id = {q["query_id"]: q for q in labels["queries"]}
    replay_by_request = {r["request_id"]: r for r in scores["replays"]}
    occurrences = defaultdict(list)
    for occurrence in analysis["occurrences"]:
        occurrences[occurrence["sample_id"]].append(occurrence)
    records = {s["sample_id"]: s for s in analysis.get("samples", [])}
    rows = []
    for sample in plan["samples"]:
        row = {"sample_id": sample["sample_id"], "source_verified": False, "goal_correct": None, "source_refs": [],
               "rationale": "No independently reviewed, source-grounded single-query decision assessment.", "retrieval": {"status": "unknown"}}
        matches = occurrences.get(sample["sample_id"], [])
        all_calls = records.get(sample["sample_id"], {}).get("calls", [])
        if len(matches) == len(all_calls) == 1:
            occurrence = matches[0]
            aid = occurrence["annotation_id"]
            label = label_by_id.get(aid, {})
            replay = replay_by_request[occurrence["request_id"]]
            assessment = next(a["assessment"] for a in replay["context_assessments"] if a["annotation_id"] == aid)
            targets = set(label.get("accepted_target_ids", []))
            source_hashes = {source["path"]: source.get("sha256") for source in labels.get("source_files", [])}
            source_refs = [{"target_id": t["target_id"], "path": t["path"],
                            "start_line": t.get("entry_start_line", t.get("definition_line")),
                            "end_line": t.get("entry_end_line", t.get("definition_line")),
                            "definition": t.get("definition"), "source_file_sha256": source_hashes.get(t["path"])}
                           for t in labels.get("targets", []) if t["target_id"] in targets]
            verified = (label.get("annotation_status") == "reviewed" and label.get("classification") in {"original", "equivalent_rewrite", "legitimate_subgoal"}
                        and bool(source_refs) and labels.get("annotation_provenance", {}).get("kind") == "model_assisted_source_verified_cross_review")
            if verified:
                row.update(source_verified=True, goal_correct=True, source_refs=source_refs,
                           rationale=label.get("goal") or "Accepted positive targets after source verification and independent group review.")
                if assessment["status"] == "scored":
                    target = assessment["target"]
                    row["retrieval"] = {"status": "complete", "hit_at_10": target["hit_at_10"], "rr_at_10": target["rr_at_10"],
                                        "request_id": occurrence["request_id"], "annotation_id": aid, "quality_repetition": 1}
        elif len(matches) > 1:
            row["rationale"] = "Multiple-query decision requires a reviewed batch objective; no best-query or invented aggregate score is used."
        rows.append(row)
    return {"schema_version": 1, "plan_sha256": plan["plan_sha256"], "assessment_frozen": True, "rows": rows,
            "analysis_sha256": _digest(analysis), "labels_sha256": _digest(labels), "replay_rows_sha256": _digest(replayrows),
            "limitations": ["Goal correctness is fallible model-assisted source review, not formal proof or human gold.",
                "Non-search, no-tool and multi-call decisions remain unknown pending their own source-grounded decision review."]}
