"""Freeze all observed E2E query decisions before annotation or retrieval replay.

The first-decision fields remain compatible with the v5 collector.  Request
identity and annotation identity are deliberately different: identical complete
backend requests share execution, but different prior feedback retains separate
query relevance labels. Native event order is not a model feedback boundary.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import first_query_analysis as first
from .observability import classify_call

PROTOCOL = "readonly-query-trajectory-v6"


def _identity(prefix: str, value: Any) -> str:
    return prefix + "-" + first._sha(first._canonical(value).encode())[:16]


def _feedback(call: dict[str, Any]) -> dict[str, Any]:
    return {key: call.get(key) for key in ("tool_name", "raw_arguments", "status", "visible_text")}


def _assistant_text(path: Path, sources: first._Sources) -> dict[str, list[dict[str, Any]]]:
    """Keep observed assistant text, not hidden reasoning or imagined wire inputs."""
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    events, _ = sources.events(path)
    for line, event in events:
        scope = first._scope(event)
        if event.get("type") == "text":
            part = event.get("part") or {}
            mid, text = part.get("messageID"), part.get("text")
            if mid and isinstance(text, str):
                result[scope + ":" + str(mid)].append({"text": text, "source": sources.ref(path, line, "/part/text")})
        elif event.get("type") == "assistant":
            message = event.get("message") or {}
            mid = message.get("id")
            for index, block in enumerate(message.get("content") or []):
                if mid and isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    item = {"text": block["text"], "source": sources.ref(path, line, f"/message/content/{index}/text")}
                    # Qoder can emit the same complete text block repeatedly.
                    rows = result[scope + ":" + str(mid)]
                    if not any(row["text"] == item["text"] for row in rows):
                        rows.append(item)
    return result


def _round(turn: dict[str, Any], calls: list[dict[str, Any]], turns: list[dict[str, Any]],
           question: str | None, index: int, parse_gaps: bool) -> dict[str, Any]:
    same = [call for call in calls if call["model_turn_id"] == turn["id"]]
    batch = [call for call in same if call["is_zg"]]
    earliest = batch[0]
    prior = [call for call in calls if call["scope"] == turn["scope"]
             and call["model_turn_id"] != turn["id"] and call["returned_line"] is not None
             and call["returned_line"] < turn["start_line"]]
    earlier_turns = [t for t in turns if t["scope"] == turn["scope"] and t["start_line"] < turn["start_line"]]
    prior_text = [part["text"] for t in earlier_turns for part in t.get("assistant_text", [])
                  if part["source"]["line"] < turn["start_line"]]
    context = {"original_question": question, "prior_turn_feedback": [_feedback(call) for call in prior],
               "prior_assistant_text": prior_text}
    # Incomplete contexts cannot be safely shared with an apparently empty first
    # prompt. Preserve an occurrence-specific discriminator when parse gaps exist.
    if parse_gaps:
        context["incomplete_context_source"] = turn["sources"][0] if turn["sources"] else turn["id"]
    context_id = _identity("context", context)
    same_returned = [call for call in same if call["id"] != earliest["id"] and call["returned_line"] is not None
                     and call["returned_line"] < earliest["first_native_line"]]
    overlaps = []
    for i, a in enumerate(batch):
        for b in batch[i + 1:]:
            times = [a["start_ms"], a["end_ms"], b["start_ms"], b["end_ms"]]
            if all(isinstance(v, (float, int)) and not isinstance(v, bool) for v in times) and max(times[0], times[2]) < min(times[1], times[3]):
                overlaps.append([a["call_id"], b["call_id"]])
    return {**turn, "zg_decision_round_index": index, "all_tool_calls": same, "zg_calls": batch,
            "prior_turn_feedback": prior, "has_prior_turn_feedback": bool(prior) if not parse_gaps else True if prior else None,
            "same_message_feedback_returned_before_first_zg_native_event": same_returned,
            "overlapping_zg_execution_pairs": overlaps, "same_message_is_not_proof_of_feedback_conditioned_rewrite": True,
            "context_id": context_id, "annotation_context": context,
            "context_capture_status": "observed_with_parse_gaps" if parse_gaps else "native_observed",
            "context_capture_limitation": "Earlier native tool feedback and assistant text only; full model wire context, hidden reasoning and prompt compaction are not reconstructed."}


def _augment_trial(directory: Path, trial: dict[str, Any], planned: dict[str, Any],
                   sources: first._Sources, question: str | None) -> None:
    trial.update(zg_decision_rounds=[], all_tool_calls=[], model_turns=[])
    agent = (directory / planned.get("trajectory_path", f"{trial['trial_id']}/agent/trajectory.json")).parent
    path = next((agent / name for name in first.NATIVE_NAMES if (agent / name).is_file()), None)
    if path is None:
        return
    native = first._native(path, sources)
    sidecars, _ = first._sidecars(agent / "zg-trace.jsonl", sources)
    text_by_turn = _assistant_text(path, sources)
    for turn in native["turns"]:
        turn["assistant_text"] = text_by_turn.get(turn["id"], [])
    for call in native["calls"]:
        call["category"] = classify_call(call["tool_name"] or "unknown", call["raw_arguments"] if isinstance(call["raw_arguments"], dict) else {})["category"]
        if call["is_zg"]:
            first._link_backend(call, sidecars)
    trial.update(all_tool_calls=native["calls"], model_turns=native["turns"])
    for turn in native["turns"]:
        if any(c["is_zg"] and c["model_turn_id"] == turn["id"] for c in native["calls"]):
            trial["zg_decision_rounds"].append(_round(turn, native["calls"], native["turns"], question,
                len(trial["zg_decision_rounds"]) + 1, bool(native["parse_diagnostics"])))
    if trial["zg_decision_rounds"]:
        trial["first_zg_decision_round"] = trial["zg_decision_rounds"][0]
        trial["first_zg_call"] = trial["first_zg_decision_round"]["zg_calls"][0]


def _catalogs(groups: list[dict[str, Any]]) -> tuple[list, list, list, list]:
    texts, requests, annotations, unlinked = {}, {}, {}, []
    for group in groups:
        for trial in group["trials"]:
            for decision in trial.get("zg_decision_rounds", []):
                context_id = decision["context_id"]
                for call in decision["zg_calls"]:
                    backend = call.get("backend") or {}
                    request = backend.get("request")
                    occurrence = {"group": group["group"], "trial_id": trial["trial_id"], "profile": trial.get("profile"),
                        "call_id": call["call_id"], "native_call_id": call["id"], "message_id": call["message_id"],
                        "model_turn_id": call["model_turn_id"], "model_turn_index": decision["model_turn_index"],
                        "zg_decision_round_index": decision["zg_decision_round_index"], "context_id": context_id,
                        "source": call["source"], "backend_source": backend.get("source"),
                        "native_call_status": call["status"], "backend_status": backend.get("status"),
                        "backend_link_status": call["backend_link"]["status"]}
                    call["context_id"] = context_id
                    if isinstance(request, dict) and call["backend_link"]["status"] == "matched":
                        request_id = _identity("request", request)
                        annotation_id = _identity("annotation", {"request": request, "context_id": context_id})
                        call.update(request_id=request_id, annotation_id=annotation_id)
                        occurrence.update(request_id=request_id, annotation_id=annotation_id)
                        requests.setdefault(request_id, {"request_id": request_id, "request": request, "occurrences": []})["occurrences"].append(occurrence)
                        annotation = annotations.setdefault(annotation_id, {"kind": "faithful", "annotation_id": annotation_id,
                            "request_id": request_id, "context_id": context_id, "request": request,
                            **decision["annotation_context"], "occurrences": [],
                            "context_capture_status": decision["context_capture_status"],
                            "context_capture_limitation": decision["context_capture_limitation"]})
                        annotation["occurrences"].append(occurrence)
                    else:
                        call.update(request_id=None, annotation_id=None)
                        unlinked.append({**occurrence, "request_id": None, "annotation_id": None,
                            "raw_arguments": call["raw_arguments"], "reason": "Complete backend request unavailable or association ambiguous; native arguments are not substituted."})
                    fields = [("raw_arguments", q) for q in first._query_fields(call["raw_arguments"])]
                    if backend:
                        fields += [("backend_request", q) for q in first._query_fields(request)]
                        fields += [("backend_executed_routes", q) for q in first._query_fields({"routes": backend.get("executed_routes")})]
                    locations: dict[str, list] = defaultdict(list)
                    for layer, query in fields:
                        locations[query["text"]].append({"layer": layer, "pointer": query["pointer"], "mode": query["mode"]})
                    for text, positions in locations.items():
                        item = texts.setdefault(text, {"query_id": "query-" + first._sha(text.encode())[:16],
                                                       "text": text, "occurrences": []})
                        item["occurrences"].append({**occurrence, "locations": positions})
    for item in texts.values():
        item["occurrence_count"] = len(item["occurrences"])
        item["trial_count"] = len({(x["group"], x["trial_id"]) for x in item["occurrences"]})
    for item in requests.values():
        item["occurrence_count"] = len(item["occurrences"])
        item["trial_count"] = len({(x["group"], x["trial_id"]) for x in item["occurrences"]})
    questions = {group.get("original_question") for group in groups if isinstance(group.get("original_question"), str)}
    if len(questions) > 1:
        raise ValueError("All experiment groups must preserve the same original question")
    if questions:
        question = next(iter(questions))
        request = {"root": "/app", "query": question, "limit": 10, "autoUpdate": False, "trace": True}
        context = {"original_question": question, "prior_turn_feedback": [], "prior_assistant_text": []}
        cid = _identity("context", context)
        aid = _identity("annotation", {"request": request, "context_id": cid})
        # An identical actual first request can share a relevance label while the
        # original reference remains a separately reported, non-E2E occurrence.
        if aid in annotations:
            annotations[aid]["kind"] = "original"
            annotations[aid]["also_faithful_request"] = True
        else:
            annotations[aid] = {"kind": "original", "annotation_id": aid, "request_id": _identity("request", request),
                "context_id": cid, "request": request, **context, "occurrences": [], "context_capture_status": "protocol_defined",
                "context_capture_limitation": "Protocol-defined original reference; not an observed E2E tool call."}
    return (sorted(texts.values(), key=lambda x: x["query_id"]), sorted(requests.values(), key=lambda x: x["request_id"]),
            sorted(annotations.values(), key=lambda x: (x["kind"] != "original", x["annotation_id"])), unlinked)


def analyze(runs_dir: Path, *, question: str | None = None) -> dict[str, Any]:
    report = first.analyze(runs_dir, question=question)
    root = Path(report["runs_dir"])
    sources = first._Sources(root)
    sources.files.update({x["path"]: x for x in report["input_artifacts"]})
    for group in report["groups"]:
        directory = root if (root / "plan.json").exists() or (root / "ci-audit.json").exists() else root / group["group"]
        plan = sources.json(directory / "plan.json")
        planned = {x["trial_id"]: x for x in plan.get("trials", [])}
        for trial in group["trials"]:
            _augment_trial(directory, trial, planned.get(trial["trial_id"], {}), sources, group.get("original_question"))
        group.update(first._summaries(group["trials"]))
        group["all_round_query_summary"] = {"zg_decision_rounds_observed": sum(len(t["zg_decision_rounds"]) for t in group["trials"]),
            "zg_calls_observed": sum(len(d["zg_calls"]) for t in group["trials"] for d in t["zg_decision_rounds"]),
            "later_queries_are_feedback_conditioned": True,
            "independent_qa_tasks": 1}
    catalog, requests, annotations, unlinked = _catalogs(report["groups"])
    report.update(schema_version=2, protocol=PROTOCOL, query_catalog=catalog, request_catalog=requests,
                  annotation_catalog=annotations, unreplayable_occurrences=unlinked,
                  input_artifacts=sorted(sources.files.values(), key=lambda x: x["path"]))
    report["definitions"].update(zg_decision_rounds="All observed scoped model messages containing ZG calls, not only the first batch.",
        annotation_identity="Exact complete backend request plus observed prior-feedback context. Current call output and final answer are excluded.",
        sample_size="One independent QA task. Calls, contexts and five retrieval repeats are not extra E2E trials.")
    report["diagnostics"].extend(sources.errors)
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# 本轮 E2E 全轮查询目录", "", "只提取本轮已发生的调用；无新 E2E、模型标注或检索回放。首次批次一致性与后续反馈条件下的查询分开解释。", "",
        "| 组 | 计划 zg 运行 | 首批不同版本 / 可观察运行 | 全部 zg 决策轮 | 全部 zg 调用 |", "|---|---:|---|---:|---:|"]
    for group in report["groups"]:
        freq = group["first_zg_decision_round_batch_consistency"]
        summary = group["all_round_query_summary"]
        lines.append(f"| {group['group']} | {group['adoption_summary']['planned_treatment_trials']} | {freq['unique_values']} / {freq['observed_trials']} | {summary['zg_decision_rounds_observed']} | {summary['zg_calls_observed']} |")
    lines += ["", f"完整真实请求 {len(report['request_catalog'])} 个；待标注请求＋上下文 {len(report['annotation_catalog'])} 个（含原题参照）；无法忠实关联的调用 {len(report['unreplayable_occurrences'])} 次。", "",
              "完整原始参数、输出、此前反馈、同轮调用、每项来源与哈希保存在 JSON。相同请求共享回放，不同此前上下文分别标注。", ""]
    return "\n".join(lines)


def write_report(report: dict[str, Any], output: Path) -> None:
    output = Path(output).resolve()
    inputs = {(Path(report["runs_dir"]) / x["path"]).resolve() for x in report["input_artifacts"]}
    if output.suffix == ".md" or output in inputs or output.with_suffix(".md") in inputs:
        raise ValueError("Refusing to overwrite an input artifact; output must be JSON")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--question")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    question = args.question
    if args.case:
        case_question = json.loads(args.case.read_text())["question"]
        if question is not None and question != case_question:
            parser.error("--question differs from the original case")
        question = case_question
    write_report(analyze(args.runs_dir, question=question), args.output)


if __name__ == "__main__":
    main()
