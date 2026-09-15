"""Collect official ZG calls from native Agent evidence, without an MCP tap.

``request`` is the unchanged Agent argument object, not an inferred backend
request. ``mcp_request`` describes a replay against the released tool name; it
does not claim that the MCP RPC frame was captured. This module is offline.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import first_query_analysis as first
from .observability import classify_call
from .query_trajectory import _assistant_text, _round
from .retrieval_replay import score_request

PROTOCOL = "official-native-query-diagnosis-v1"
TOOL = "zvec_grep_search"


def _identity(prefix: str, value: Any) -> str:
    return prefix + "-" + first._sha(first._canonical(value).encode())[:20]


def _official_tool(name: Any) -> bool:
    return isinstance(name, str) and re.search(r"(?:^|_)zvec_grep_search$", name.replace("-", "_").lower()) is not None


def _treatment(profile: Any) -> bool:
    return first._treatment(profile) or isinstance(profile, str) and profile.startswith("zvec-grep-")


def _trajectory_native(path: Path, sources: first._Sources) -> dict[str, Any]:
    """Retain fallback observations without inventing native success or scope."""
    data = sources.json(path)
    turns, calls = [], []
    for index, step in enumerate(data.get("steps", [])):
        if not isinstance(step, dict) or step.get("source") != "agent":
            continue
        mid = f"trajectory-step-{index}"
        turn = {"id": mid, "message_id": None, "scope": "trajectory-scope-unknown", "start_line": index + 1,
                "model_turn_index": len(turns) + 1, "sources": [sources.ref(path, pointer=f"/steps/{index}")],
                "usage_snapshots": [], "assistant_text": []}
        if isinstance(step.get("message"), str):
            turn["assistant_text"] = [{"text": step["message"], "source": {"line": index + 1}}]
        turns.append(turn)
        results = (step.get("observation") or {}).get("results", [])
        for ci, item in enumerate(step.get("tool_calls") or []):
            cid = item.get("tool_call_id")
            matches = [r for r in results if cid is not None and r.get("source_call_id") == cid]
            text = first._text(matches[0].get("content")) if len(matches) == 1 else None
            name, args = item.get("function_name"), item.get("arguments")
            source = sources.ref(path, pointer=f"/steps/{index}/tool_calls/{ci}")
            calls.append({"id": f"{mid}:call-{ci}", "call_id": cid, "message_id": None, "model_turn_id": mid,
                          "scope": turn["scope"], "tool_name": name, "raw_arguments": args, "is_zg": _official_tool(name),
                          "first_native_line": index + 1, "source": source, "argument_snapshots": [{"arguments": args, "source": source}],
                          "visible_text": text, "visible_payload": matches[0] if len(matches) == 1 else None,
                          "status": "unknown", "returned_line": index + 1 if len(matches) == 1 else None,
                          "start_ms": None, "end_ms": None,
                          "result_source": sources.ref(path, pointer=f"/steps/{index}/observation")})
    return {"adapter": "normalized_trajectory", "turns": turns, "calls": calls, "complete_observed_trace": False,
            "parse_diagnostics": ["Native stream missing; normalized steps do not establish native call status, scope or trace completeness."]}


def build_catalog(runs_dirs: list[Path], case: dict[str, Any]) -> dict[str, Any]:
    """Freeze original question and all observed official search decisions."""
    directories = [Path(p).resolve() for p in runs_dirs]
    if not directories or len(set(directories)) != len(directories):
        raise ValueError("Provide distinct nonempty run directories")
    sources = first._Sources(Path(os.path.commonpath(directories)))
    requests: dict[str, dict[str, Any]] = {}
    annotations: dict[str, dict[str, Any]] = {}
    occurrences, groups, unreplayable = [], [], []

    def register(args: dict[str, Any], context: dict[str, Any], occurrence: dict[str, Any] | None, *,
                 kind: str, context_status: str, limitation: str) -> tuple[str, str, str]:
        params = {"name": TOOL, "arguments": copy.deepcopy(args)}
        rid, cid = _identity("request", params), _identity("context", context)
        aid = _identity("annotation", {"request": args, "context_id": cid, "tool_name": TOOL})
        request = requests.setdefault(rid, {"request_id": rid, "tool_name": TOOL, "request": copy.deepcopy(args),
                                            "mcp_request": params, "kind": kind, "occurrences": []})
        annotation = annotations.setdefault(aid, {"annotation_id": aid, "request_id": rid, "request_ids": [rid],
            "context_id": cid, "request": copy.deepcopy(args), "kind": kind, **copy.deepcopy(context), "occurrences": [],
            "context_capture_status": context_status, "context_capture_limitation": limitation})
        if occurrence is not None:
            occurrence.update(request_id=rid, context_id=cid, annotation_id=aid)
            occurrences.append(occurrence)
            # Labels may contain occurrence provenance, never this query's output.
            ref = {key: copy.deepcopy(occurrence.get(key)) for key in ("occurrence_id", "group", "trial_id", "profile",
                "call_id", "native_call_id", "message_id", "model_turn_id", "model_turn_index", "zg_decision_round_index", "source")}
            request["occurrences"].append(ref)
            annotation["occurrences"].append(ref)
            if annotation["kind"] == "original":
                annotation["also_faithful_request"] = True
                request["also_faithful_request"] = True
        return rid, cid, aid

    register({"root": "/app", "query": case["question"], "limit": 10},
             {"original_question": case["question"], "prior_turn_feedback": [], "prior_assistant_text": []},
             None, kind="original", context_status="protocol_defined",
             limitation="Original-question reference: released default hybrid/freshness behavior; no autoUpdate override and no Agent occurrence.")
    seen_groups = set()
    for directory in directories:
        plan = sources.json(directory / "plan.json")
        if plan.get("case_id") != case["case_id"]:
            raise ValueError(f"E2E plan and case differ: {directory}")
        manifest = next((sources.json(directory / name) for name in ("experiment-manifest.json", "manifest.json")
                         if (directory / name).is_file()), {})
        group = plan.get("group_id") or plan.get("group") or manifest.get("group_id") or manifest.get("group") or directory.name
        if not isinstance(group, str) or group in seen_groups:
            raise ValueError("Groups require distinct string identifiers")
        seen_groups.add(group)
        rows, trial_ids = [], set()
        for planned in plan.get("trials", []):
            tid = planned["trial_id"]
            if tid in trial_ids:
                raise ValueError(f"Duplicate planned trial: {group}/{tid}")
            trial_ids.add(tid)
            trajectory = (directory / planned.get("trajectory_path", f"{tid}/agent/trajectory.json")).resolve()
            if not trajectory.is_relative_to(directory):
                raise ValueError("Trajectory path escapes run directory")
            agent = trajectory.parent
            native_path = next((agent / name for name in first.NATIVE_NAMES if (agent / name).is_file()), None)
            if native_path:
                native = first._native(native_path, sources)
                texts = _assistant_text(native_path, sources)
                for turn in native["turns"]:
                    turn["assistant_text"] = texts.get(turn["id"], [])
            else:
                native = _trajectory_native(trajectory, sources) if trajectory.is_file() else {
                    "adapter": None, "turns": [], "calls": [], "complete_observed_trace": False,
                    "parse_diagnostics": ["Missing native trace and trajectory"]}
            for call in native["calls"]:
                call["is_zg"] = _official_tool(call["tool_name"])
                call["category"] = classify_call(call["tool_name"] or "unknown", call["raw_arguments"] if isinstance(call["raw_arguments"], dict) else {})["category"]
                if call["argument_snapshots"]:
                    call["source"] = call["argument_snapshots"][-1]["source"]
            zg_calls = [c for c in native["calls"] if c["is_zg"]]
            complete = native["complete_observed_trace"]
            row = {"trial_id": tid, "profile": planned.get("profile"), "arm": planned.get("arm"),
                "prompt_variant": planned.get("prompt_version"), "block_id": planned.get("block_id"),
                "execution_status": sources.json(agent.parent / "result.json").get("status", planned.get("status", "unknown")),
                "native_adapter": native["adapter"], "native_trace_complete": complete,
                "zg_adoption_observed": True if zg_calls else False if complete else None,
                "zg_tool_calls_attempted": len(zg_calls) if complete else None,
                "zg_tool_calls_attempted_lower_bound": len(zg_calls), "first_zg_call": None, "first_zg_decision_round": None,
                "initial_request_identity": first._initial_request(agent, sources), "all_tool_calls": native["calls"],
                "model_turns": native["turns"], "zg_decision_rounds": [], "diagnostics": native["parse_diagnostics"]}
            for turn in native["turns"]:
                if not any(c["is_zg"] and c["model_turn_id"] == turn["id"] for c in native["calls"]):
                    continue
                decision = _round(turn, native["calls"], native["turns"], case["question"],
                                  len(row["zg_decision_rounds"]) + 1, bool(native["parse_diagnostics"]))
                row["zg_decision_rounds"].append(decision)
                for call in decision["zg_calls"]:
                    occ = {"occurrence_id": _identity("occurrence", [group, tid, call["id"]]),
                        "group": group, "trial_id": tid, "profile": row["profile"], "arm": row["arm"],
                        "prompt_variant": row["prompt_variant"], "call_id": call["call_id"], "native_call_id": call["id"],
                        "native_tool_name": call["tool_name"], "message_id": call["message_id"], "model_turn_id": call["model_turn_id"],
                        "model_turn_index": turn["model_turn_index"], "zg_decision_round_index": decision["zg_decision_round_index"],
                        "source": call["source"], "result_source": call.get("result_source"),
                        "native_status": call["status"], "public_text": call["visible_text"],
                        "public_text_sha256": first._sha(call["visible_text"].encode()) if isinstance(call["visible_text"], str) else None,
                        "agent_visible_observation": native_path is not None,
                        "origin": "native_agent_tool_event" if native_path else "normalized_trajectory_fallback"}
                    if not isinstance(call["raw_arguments"], dict):
                        unreplayable.append({**occ, "raw_arguments": call["raw_arguments"], "reason": "Arguments are not an observed JSON object; no request is fabricated."})
                        continue
                    rid, cid, aid = register(call["raw_arguments"], decision["annotation_context"], occ, kind="faithful",
                        context_status=decision["context_capture_status"], limitation=decision["context_capture_limitation"])
                    call.update(request_id=rid, context_id=cid, annotation_id=aid,
                                mcp_request={"name": TOOL, "arguments": copy.deepcopy(call["raw_arguments"])})
                    decision["context_id"] = cid
            if row["zg_decision_rounds"]:
                row["first_zg_decision_round"] = row["zg_decision_rounds"][0]
                row["first_zg_call"] = row["first_zg_decision_round"]["zg_calls"][0]
            rows.append(row)
        normalized = [{**r, "profile": "zg" if _treatment(r["profile"]) else "baseline"} for r in rows]
        groups.append({"group": group, "runs_dir": str(directory), "agent": manifest.get("agent"), "model": manifest.get("model"),
                       "original_question": case["question"], "trials": rows, **first._summaries(normalized),
                       "all_round_query_summary": {"zg_calls_observed": sum(len(r["zg_decision_rounds"][i]["zg_calls"]) for r in rows for i in range(len(r["zg_decision_rounds"]))),
                           "zg_decision_rounds_observed": sum(len(r["zg_decision_rounds"]) for r in rows)}})
    texts = {}
    for request in requests.values():
        fields: dict[str, list] = defaultdict(list)
        for field in first._query_fields(request["request"]):
            fields[field["text"]].append({"pointer": field["pointer"], "mode": field["mode"], "layer": "native_arguments"})
        for text, locations in fields.items():
            item = texts.setdefault(text, {"query_id": _identity("query", text), "text": text, "occurrences": [], "request_ids": [], "kinds": []})
            item["request_ids"].append(request["request_id"])
            item["kinds"].append(request["kind"])
            item["occurrences"].extend({**o, "locations": locations} for o in request["occurrences"])
        request["occurrence_count"] = len(request["occurrences"])
    for item in texts.values():
        item["occurrence_count"] = len(item["occurrences"])
        item["trial_count"] = len({(o["group"], o["trial_id"]) for o in item["occurrences"]})
    return {"schema_version": 2, "protocol": PROTOCOL, "kind": "e2e", "case_id": case["case_id"], "repo": case["repo"],
        "original_question": case["question"], "runs_dir": str(sources.root), "groups": groups,
        "request_catalog": sorted(requests.values(), key=lambda r: r["request_id"]),
        "annotation_catalog": sorted(annotations.values(), key=lambda a: (a["kind"] != "original", a["annotation_id"])),
        "query_catalog": sorted(texts.values(), key=lambda q: q["query_id"]), "occurrences": occurrences,
        "unreplayable_occurrences": unreplayable, "diagnostics": sources.errors,
        "input_artifacts": sorted(sources.files.values(), key=lambda f: f["path"]),
        "definitions": {"request": "Unmodified native Agent arguments; no internal backend request is inferred.",
            "mcp_request": "Replay descriptor for installed official zvec_grep_search, derived from native tool identity and unchanged arguments; not a captured RPC frame.",
            "original_request": "root=/app, original question, limit=10; released defaults determine hybrid/autoUpdate/freshness.",
            "index_identity": "Each replay build and official freshness behavior must be reported separately; this collector does not assert a frozen shared index.",
            "context": "Only observed earlier same-scope feedback and assistant text; current outputs/final answer excluded. Incomplete contexts remain occurrence-specific.",
            "sample_size": "Calls, contexts and repeated retrieval outputs do not add independent QA tasks."}}


def _score(text: Any, status: str, annotation: dict[str, Any], labels: dict[str, Any], entries: dict[str, Any]) -> dict[str, Any]:
    if status != "completed" or not isinstance(text, str):
        return {"status": "unknown", "reason": "No successful visible text output", "score": None}
    try:
        value = score_request(text, annotation["request"], labels, entries, context_id=annotation["context_id"])
        relevance = value["native"]["query_relevance"]
    except (ValueError, KeyError, TypeError) as error:
        return {"status": "unknown", "reason": f"Scoring contract unavailable: {type(error).__name__}", "score": None}
    return {"status": relevance["status"], "score": value, "target": relevance["target"],
            "reason": relevance.get("reason") or relevance.get("format_reason")}


def score_records(analysis: dict[str, Any], labels: dict[str, Any], entries: dict[str, Any], replayrows: Any) -> dict[str, Any]:
    """Score actual native text and fixed repetition 1; never select best repeat."""
    if labels.get("repo") != analysis.get("repo") or entries.get("repo") != analysis.get("repo"):
        raise ValueError("Labels, entries and native observations require the same source revision")
    rows = replayrows.get("rows", replayrows.get("trials", [])) if isinstance(replayrows, dict) else replayrows
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("Replay rows must be a list of objects")
    grouped: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if type(r.get("repetition")) is int and 1 <= r["repetition"] <= 5:
            grouped[r.get("request_id"), r["repetition"]].append(r)
    annotations = {a["annotation_id"]: a for a in analysis["annotation_catalog"]}
    actual = [{**copy.deepcopy(o), "assessment": _score(o.get("public_text"), o.get("native_status", "unknown"), annotations[o["annotation_id"]], labels, entries)}
              for o in analysis["occurrences"]]
    replays = []
    for request in analysis["request_catalog"]:
        observations = []
        for repetition in range(1, 6):
            candidates = grouped.get((request["request_id"], repetition), [])
            row = candidates[0] if len(candidates) == 1 else {}
            status = row.get("status", "unknown") if row else "ambiguous_duplicate" if candidates else "missing"
            if status == "success":
                status = "completed"
            result = row.get("result")
            content = result.get("content") if isinstance(result, dict) else None
            # The official client hashes every content block, but exposes only
            # text blocks to the text scorer. These are different identities.
            result_text = "\n".join(block["text"] for block in content
                                    if isinstance(block, dict) and block.get("type") == "text") if isinstance(content, list) else None
            text = row.get("text", result_text)
            digest = first._sha(text.encode()) if isinstance(text, str) else None
            content_digest = first._sha(first._canonical(content).encode()) if isinstance(result, dict) else None
            if isinstance(result, dict) and result.get("isError") is True:
                status = "error"
            elif result_text is not None and text != result_text:
                status = "text_result_mismatch"
            elif row.get("public_sha256") is not None and row["public_sha256"] != content_digest:
                status = "public_hash_mismatch"
            elif row.get("mcp_request") is not None and row["mcp_request"] != request["mcp_request"]:
                status = "request_mismatch"
            elif "request" in row and row["request"] != request["request"]:
                status = "request_mismatch"
            observations.append({"repetition": repetition, "status": status, "text": text,
                                 "public_text_sha256": digest, "public_content_sha256": content_digest,
                                 "source_rows": copy.deepcopy(candidates)})
        contexts = [{"annotation_id": a["annotation_id"], "context_id": a["context_id"], "kind": a["kind"], "quality_repetition": 1,
                     "assessment": _score(observations[0]["text"], observations[0]["status"], a, labels, entries)}
                    for a in annotations.values() if request["request_id"] in a.get("request_ids", [a["request_id"]])]
        hashes = [o["public_text_sha256"] for o in observations if o["status"] == "completed" and o["public_text_sha256"] is not None]
        replays.append({**copy.deepcopy(request), "observations": observations, "context_assessments": contexts,
                        "stability": {"planned": 5, "completed_text_outputs": len(hashes), "unique_output_hashes": len(set(hashes)),
                                      "identical_all_five": len(set(hashes)) == 1 if len(hashes) == 5 else None}})
    replay_by_id = {r["request_id"]: r for r in replays}
    comparisons = []
    for o in actual:
        replay = replay_by_id[o["request_id"]]["observations"][0]
        known = o["native_status"] == "completed" and replay["status"] == "completed" and o["public_text_sha256"] is not None and replay["public_text_sha256"] is not None
        comparisons.append({"occurrence_id": o["occurrence_id"], "request_id": o["request_id"], "group": o["group"],
                            "trial_id": o["trial_id"], "replay_repetition": 1,
                            "original_vs_replay_text_identical": o["public_text_sha256"] == replay["public_text_sha256"] if known else None})
    return {"schema_version": 1, "protocol": PROTOCOL, "case_id": analysis["case_id"], "metric_profile": "entry-ranking-v1",
        "actual": actual, "replays": replays, "actual_vs_replay": comparisons,
        "no_call_or_unknown_trials": [{"group": g["group"], "trial_id": t["trial_id"], "profile": t["profile"], "zg_adoption_observed": t["zg_adoption_observed"]}
                                      for g in analysis["groups"] for t in g["trials"] if not t["zg_decision_rounds"]],
        "unplanned_replay_rows": [r for r in rows if r.get("request_id") not in replay_by_id or type(r.get("repetition")) is not int or r["repetition"] not in range(1, 6)],
        "limitations": ["Actual Agent-visible results and replay results are scored separately; replay never replaces historical evidence.",
            "Repetition 1 determines replay quality; missing or failed first repeats stay unknown regardless of later results.",
            "Output consistency is conditional on recorded official requests/build identities, not proof of a frozen index or stable Agent behavior."]}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, action="append", required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_catalog(args.runs_dir, json.loads(args.case.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
