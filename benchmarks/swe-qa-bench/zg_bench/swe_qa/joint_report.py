"""Join this E2E run to its frozen query labels and retrieval diagnostic replay.

Actual tool observations and replay observations remain separate. This report
shows observable associations, never inferred model causation or new QA samples.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from . import e2e_analysis as e2e
from . import first_query_analysis as first
from .shared_anchor_diagnostic import build_diagnostic

PROTOCOL = "readonly-e2e-retrieval-joint-v6"


def _json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _target(score: dict[str, Any] | None) -> dict[str, Any]:
    return ((score or {}).get("native", {}).get("query_relevance") or {}).get("target") or {}


def _context_score(observation: dict[str, Any], annotation_id: str | None) -> dict[str, Any] | None:
    found = [row for row in observation.get("context_scores", []) if row.get("annotation_id") == annotation_id]
    return found[0].get("request_scores") if len(found) == 1 else None


def _known_sum(values: list[Any]) -> int | float | None:
    return sum(values) if all(e2e._number(x) is not None for x in values) else None


def _following(trial: dict[str, Any], call: dict[str, Any], observed_score: dict[str, Any] | None,
               labels: dict[str, Any]) -> dict[str, Any]:
    returned = call.get("returned_line")
    complete = trial.get("native_trace_complete") is True
    turns = [turn for turn in trial.get("model_turns", []) if returned is not None
             and turn["scope"] == call["scope"] and turn["id"] != call["model_turn_id"] and turn["start_line"] > returned]
    ids = {turn["id"] for turn in turns}
    calls = [c for c in trial.get("all_tool_calls", []) if c["model_turn_id"] in ids]
    if returned is None:
        complete = False
    target_ids = {match["target_id"] for match in _target(observed_score).get("matches", [])}
    targets = [t for t in labels.get("targets", []) if t["target_id"] in target_ids]
    reads, citations = [], []
    for later in calls:
        if later.get("category") != "read" or later.get("status") != "completed" or not isinstance(later.get("visible_text"), str):
            continue
        args = later.get("raw_arguments") or {}
        hint = (args.get("filePath") or args.get("file_path") or args.get("path")) if isinstance(args, dict) else None
        lines = set(e2e.numbered_lines(later["visible_text"], hint if isinstance(hint, str) else None))
        for target in targets:
            expected = (target["path"], target["definition_line"], target["definition"].rstrip())
            if expected in lines:
                reads.append({"call_id": later["call_id"], "native_call_id": later["id"], "target_id": target["target_id"],
                              "path": target["path"], "line": target["definition_line"], "source": later.get("result_source")})
    for turn in turns:
        for part in turn.get("assistant_text", []):
            for target in targets:
                pattern = re.escape(target["path"]) + r":(\d+)(?:[-–](\d+))?"
                for match in re.finditer(pattern, part["text"]):
                    start, end = int(match[1]), int(match[2] or match[1])
                    if start <= target["definition_line"] <= end:
                        citations.append({"target_id": target["target_id"], "citation": match[0], "source": part["source"]})
    native_usage = []
    for turn in turns:
        snapshots = turn.get("usage_snapshots") or []
        native_usage.append(e2e._usage(snapshots[-1].get("usage") if snapshots else None, trial.get("native_adapter"))["input_tokens"])
    counts = {"tool_calls": len(calls), "search_calls": sum(c.get("category") == "search" for c in calls),
              "read_calls": sum(c.get("category") == "read" for c in calls)}
    target_known = _target(observed_score).get("status") == "scored"
    return {"feedback_boundary": "Different scoped model message started after this tool result returned; same-message calls excluded.",
        "trace_complete": complete, "later_call_ids": [c["id"] for c in calls],
        **{key: value if complete else None for key, value in counts.items()},
        "observed_lower_bounds": counts,
        "native_input_tokens": _known_sum(native_usage) if complete else None,
        "native_input_tokens_observed_lower_bound": sum(x for x in native_usage if x is not None),
        "native_input_missing_turns": sum(x is None for x in native_usage),
        "native_input_scope": "Later same-scope native model turns, using adapter token convention; not an allocation of authoritative E2E final totals.",
        "read_of_returned_target_definition": {"status": "observed" if reads else "unknown" if not target_known else "no_returned_target" if not targets else "not_observed" if complete else "unknown",
                                              "evidence": reads},
        "explicit_later_target_line_citation": {"status": "observed" if citations else "unknown" if not target_known else "no_returned_target" if not targets else "not_observed" if complete else "unknown",
                                                "evidence": citations},
        "interpretation": "Reading a definition or citing a line is an observable action, not proof that the model internally used it."}


def _find_actual(rows: list[dict[str, Any]], group: str, trial: str, call: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [row for row in rows if row.get("group") == group and row.get("trial_id") == trial
                  and row.get("call_id") == call["call_id"] and row.get("annotation_id") == call.get("annotation_id")
                  and row.get("native_call_id", call["id"]) == call["id"]]
    if len(candidates) > 1:
        candidates = [row for row in candidates if row.get("native_call_id") == call["id"]]
    return candidates[0] if len(candidates) == 1 else None


def _pair_summary(trials: list[dict[str, Any]]) -> dict[str, Any]:
    by_block: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        if trial.get("block_id") is not None:
            by_block.setdefault(str(trial["block_id"]), []).append(trial)
    pairs = []
    for block, rows in by_block.items():
        baseline = [r for r in rows if r["profile"] == "baseline"]
        treatment = [r for r in rows if first._treatment(r["profile"])]
        b, z = (baseline[0] if len(baseline) == 1 else None), (treatment[0] if len(treatment) == 1 else None)
        diff = {}
        for key in ("input_tokens", "tool_calls_attempted"):
            bv, zv = (b["metrics"].get(key) if b else None), (z["metrics"].get(key) if z else None)
            diff[key] = bv - zv if bv is not None and zv is not None else None
        pairs.append({"block_id": block, "baseline_trial": b["trial_id"] if b else None,
            "zg_trial": z["trial_id"] if z else None, "baseline_status": b["status"] if b else None,
            "zg_status": z["status"] if z else None, "baseline_quality": b["quality_status"] if b else None,
            "zg_quality": z["quality_status"] if z else None, "baseline_minus_zg": diff})
    return {"pairs": pairs, "all_planned_pairs": {key: e2e.describe([p["baseline_minus_zg"][key] for p in pairs])
            for key in ("input_tokens", "tool_calls_attempted")},
            "interpretation": "Prescheduled temporal pairs are descriptive. Failed-run spending reductions are not successful savings; no matched RNG seeds or non-inferiority claim."}


def _fallback_ledger(directory: Path, group: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep planned failures visible if case validation or E2E analysis is absent."""
    plan = _json(directory / "plan.json")
    result = []
    for trial in group["trials"]:
        planned = next((p for p in plan.get("trials", []) if p.get("trial_id") == trial["trial_id"]), {})
        agent_dir = (directory / planned.get("trajectory_path", f"{trial['trial_id']}/agent/trajectory.json")).parent
        actual = _json(agent_dir.parent / "result.json")
        trajectory = _json(agent_dir / "trajectory.json")
        final = actual.get("final_metrics") or trajectory.get("final_metrics") or {}
        input_tokens = None if (final.get("extra") or {}).get("token_usage_available") is False else e2e._number(final.get("total_prompt_tokens"))
        result.append({"trial_id": trial["trial_id"], "profile": trial.get("profile"), "block_id": planned.get("block_id", planned.get("repetition")),
            "status": actual.get("status") or trial.get("execution_status"), "quality_status": "unscored", "quality_assessment": None,
            "metrics": {"input_tokens": input_tokens, "tool_calls_attempted": len(trial["all_tool_calls"]) if trial.get("native_trace_complete") else None},
            "exception": actual.get("error"), "readonly_integrity": {}})
    return result


def build_report(runs_dir: Path, analysis: dict[str, Any], replay_plan: dict[str, Any] | None = None,
                 replay_report: dict[str, Any] | None = None, labels: dict[str, Any] | None = None,
                 annotation_audit: dict[str, Any] | None = None, *, case: dict[str, Any] | None = None,
                 entries: dict[str, Any] | None = None, observed_scores: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    root = Path(runs_dir).resolve()
    plan, replay, labels, audit = replay_plan or {}, replay_report or {}, labels or {}, annotation_audit or {}
    if analysis.get("protocol") != "readonly-query-trajectory-v6":
        raise ValueError("Joint v6 report requires the all-round query trajectory")
    # Analysis is portable across artifact download directories, but every source
    # identity is rechecked against the files at this run's root.
    for source in analysis.get("input_artifacts", []):
        path = (root / source["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError(f"Query analysis source missing or changed: {source['path']}")
    actual_rows = observed_scores if observed_scores is not None else replay.get("observed_e2e_calls", [])
    if isinstance(actual_rows, dict):
        actual_rows = actual_rows.get("observations") or actual_rows.get("calls") or actual_rows.get("observed_e2e_calls") or []
    limitations, groups = [], []
    for group in analysis["groups"]:
        directory = root if (root / "plan.json").is_file() or (root / "ci-audit.json").is_file() else root / group["group"]
        own_case = case
        if own_case is None:
            candidate = directory / "case.json"
            if not candidate.is_file():
                candidate = Path(__file__).resolve().parents[2] / "cases" / f"{group.get('case_id', '')}.json"
            own_case = _json(candidate) or None
        own_entries = entries if entries is not None else _json(directory / "entries.json") or None
        try:
            e2e_report = e2e.analyze_runs(directory, own_case, own_entries) if own_case else None
        except (ValueError, KeyError, OSError, TypeError) as error:
            e2e_report = None
            limitations.append(f"{group['group']}: E2E quality/source analysis unavailable: {error}")
        if e2e_report is None:
            limitations.append(f"{group['group']}: fallback cost ledger; answer quality remains unscored.")
        e2e_rows = e2e_report["trials"] if e2e_report else _fallback_ledger(directory, group)
        by_trial = {row["trial_id"]: row for row in e2e_rows}
        trials = []
        for trial in group["trials"]:
            measured = by_trial[trial["trial_id"]]
            chains = []
            for decision in trial.get("zg_decision_rounds", []):
                for call in decision["zg_calls"]:
                    observed = _find_actual(actual_rows, group["group"], trial["trial_id"], call)
                    actual_hash = hashlib.sha256(call["visible_text"].encode()).hexdigest() if isinstance(call.get("visible_text"), str) else None
                    actual_scores = observed.get("request_scores") if observed and observed.get("output_sha256") == actual_hash and actual_hash is not None else None
                    candidates = [unit for unit in replay.get("units", []) if unit.get("kind") == "faithful"
                                  and unit.get("request") == (call.get("backend") or {}).get("request")]
                    unit = candidates[0] if len(candidates) == 1 else None
                    quality = (unit or {}).get("quality_observation") or {}
                    replay_scores = _context_score(quality, call.get("annotation_id"))
                    replay_hash = quality.get("output_sha256")
                    chain = {"call_id": call["call_id"], "native_call_id": call["id"], "request_id": call.get("request_id"),
                        "annotation_id": call.get("annotation_id"), "context_id": decision["context_id"],
                        "model_turn_index": decision["model_turn_index"], "zg_decision_round_index": decision["zg_decision_round_index"],
                        "has_prior_turn_feedback": decision["has_prior_turn_feedback"], "raw_arguments": call["raw_arguments"],
                        "request": (call.get("backend") or {}).get("request"), "source": call["source"],
                        "actual_observation": {"status": "scored" if _target(actual_scores).get("status") == "scored" else "unknown", "native_status": call["status"],
                            "output_sha256": actual_hash, "source": call.get("result_source"), "request_scores": actual_scores},
                        "replay_observation": {"unit_id": unit.get("unit_id") if unit else None, "quality_repetition": plan.get("quality_repetition", 1),
                            "status": quality.get("status", "not_available"), "output_sha256": replay_hash, "request_scores": replay_scores,
                            "stability": unit.get("stability") if unit else None},
                        "actual_and_replay_output_identical": actual_hash == replay_hash if actual_hash and replay_hash else None,
                        "following_observed_actions": _following(trial, call, actual_scores, labels)}
                    chains.append(chain)
            metrics = {key: measured.get("metrics", {}).get(key) for key in ("input_tokens", "output_tokens", "tool_calls_attempted", "tool_calls_successful", "tool_calls_error", "search_calls_attempted", "read_calls_attempted", "wall_seconds")}
            trials.append({"trial_id": trial["trial_id"], "profile": trial.get("profile"), "block_id": measured.get("block_id"),
                "status": measured.get("status"), "quality_status": measured.get("quality_status", "unscored"),
                "quality_assessment": measured.get("quality_assessment"), "metrics": metrics, "exception": measured.get("exception"),
                "readonly_integrity": measured.get("readonly_integrity"), "zg_adoption_observed": trial.get("zg_adoption_observed"),
                "native_trace_complete": trial.get("native_trace_complete"), "query_chains": chains})
        arms = {}
        for profile in dict.fromkeys(t["profile"] for t in trials):
            rows = [t for t in trials if t["profile"] == profile]
            arms[profile] = {"planned_trials": len(rows), "status_counts": dict(Counter(t["status"] for t in rows)),
                "quality_counts": dict(Counter(t["quality_status"] for t in rows)),
                "all_planned_costs": {key: e2e.describe([t["metrics"][key] for t in rows]) for key in ("input_tokens", "tool_calls_attempted")}}
        groups.append({"group": group["group"], "agent": group.get("agent"), "model": group.get("model"),
            "adoption_summary": group["adoption_summary"],
            **{key: group[key] for key in ("first_main_query_text_consistency", "first_raw_arguments_consistency", "first_zg_decision_round_batch_consistency", "initial_request_body_consistency")},
            "profiles": arms, "within_block_cost_differences": _pair_summary(trials), "trials": trials})
    chains = [c for g in groups for t in g["trials"] for c in t["query_chains"]]
    missing_stages = [name for name, present in (("replay_plan", bool(plan)), ("replay_report", bool(replay)), ("shared_labels", bool(labels)), ("annotation_audit", bool(audit))) if not present]
    scored = sum(_target(c["actual_observation"]["request_scores"]).get("status") == "scored" for c in chains)
    replay_scored = sum(_target(c["replay_observation"]["request_scores"]).get("status") == "scored" for c in chains)
    status = "partial" if missing_stages or limitations or scored != len(chains) or replay_scored != len(chains) else "complete"
    return {"schema_version": 1, "protocol": PROTOCOL, "status": status, "metric_profile": "entry-ranking-v1",
        "independent_qa_tasks": 1, "planned_e2e_trials": sum(len(g["trials"]) for g in groups),
        "source_run": plan.get("source_run"), "source_commit": plan.get("source_commit"),
        "groups": groups, "missing_stages": missing_stages, "annotation_audit": audit,
        "query_observation_summary": {"actual_zg_calls_observed": len(chains), "actual_calls_with_scorable_target_labels": scored,
            "actual_calls_with_scorable_replay": replay_scored,
            "actual_replay_differences": sum(c["actual_and_replay_output_identical"] is False for c in chains),
            "actual_replay_comparison_unknown": sum(c["actual_and_replay_output_identical"] is None for c in chains)},
        "original_reference": [unit for unit in replay.get("units", []) if unit.get("kind") == "original"],
        "shared_anchor_diagnostic": build_diagnostic(labels, groups, replay),
        "limitations": limitations + ["Query consistency is measured within each agent/model group. Different groups are not repeated samples of one policy.",
            "No independent QA sample is added by multiple calls, contexts, or repeated retrieval executions.",
            "Positive entry labels are not exhaustive relevance judgments; unknown is not a retrieval miss.",
            "Actual returned output is scored separately from replay and never replaced by it.",
            "Later read/citation and lower cost are observed associations, not evidence of the model's internal use or a causal query intervention.",
            "Five trials on one development case do not establish general answer non-inferiority or stable gains on other tasks."]}


def render_markdown(report: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        return "unknown" if value is None else str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["# 本轮 E2E 与检索诊断联合报告", "", f"链路状态：{report['status']}；独立 QA：1；计划 E2E：{report['planned_e2e_trials']}。每次运行均保留；缺失不计零，失败运行的低成本不视为收益。", "",
        "| 组 | 运行 | 状态 | 答案判定 | 实际 input token | tool call | zg 调用 |", "|---|---|---|---|---:|---:|---:|"]
    summary = ["| 组 / arm | 答案判定计数 | input 均值 / 中位数 [最小, 最大] | tool call 均值 / 中位数 [最小, 最大] | input / calls 已知数 |", "|---|---|---|---|---|"]
    def distribution(stats: dict[str, Any]) -> str:
        def number(value: Any) -> str:
            return "unknown" if value is None else f"{value:.2f}" if isinstance(value, float) else str(value)
        return number(stats["mean"]) + " / " + number(stats["median"]) + " [" + number(stats["min"]) + ", " + number(stats["max"]) + "]"
    for group in report["groups"]:
        for arm, values in group["profiles"].items():
            costs = values["all_planned_costs"]
            row = (group["group"] + " / " + str(arm), json.dumps(values["quality_counts"], ensure_ascii=False, sort_keys=True),
                distribution(costs["input_tokens"]), distribution(costs["tool_calls_attempted"]),
                str(costs["input_tokens"]["known"]) + "/" + str(values["planned_trials"]) + " ; " + str(costs["tool_calls_attempted"]["known"]) + "/" + str(values["planned_trials"]))
            summary.append("| " + " | ".join(cell(x) for x in row) + " |")
    summary += ["", "| 组 | input 均值差 (baseline − zg) / 百分比 | tool call 均值差 / 百分比 |", "|---|---:|---:|"]
    for group in report["groups"]:
        baseline = group["profiles"].get("baseline", {})
        treatments = [arm for name, arm in group["profiles"].items() if first._treatment(name)]
        treatment = treatments[0] if len(treatments) == 1 else {}
        differences = []
        for key in ("input_tokens", "tool_calls_attempted"):
            b = baseline.get("all_planned_costs", {}).get(key, {})
            z = treatment.get("all_planned_costs", {}).get(key, {})
            full = b.get("missing") == z.get("missing") == 0 and b.get("known", 0) == z.get("known", -1) and b.get("known", 0) > 0
            delta = b["mean"] - z["mean"] if full else None
            percent = delta / b["mean"] * 100 if delta is not None and b["mean"] else None
            differences.append((f"{delta:.2f}" if delta is not None else "unknown") + " / " + (f"{percent:.2f}%" if percent is not None else "unknown"))
        summary.append("| " + " | ".join([cell(group["group"]), *differences]) + " |")
    summary += ["", "均值差只在两 arm 的计划成本全部可用且次数相等时计算；百分比以 baseline 均值为分母。正值仅表示本次实测成本较低，须结合答案判定与失败情况解释。", ""]
    lines[4:4] = summary
    for group in report["groups"]:
        for trial in group["trials"]:
            values = (group["group"], trial["trial_id"], trial["status"], trial["quality_status"], trial["metrics"]["input_tokens"], trial["metrics"]["tool_calls_attempted"], len(trial["query_chains"]))
            lines.append("| " + " | ".join(cell(x) for x in values) + " |")
    lines += ["", "| 组 | 首批不同版本数 | 最常见首批次数 / 可观察运行 | 无 zg / 未知 |", "|---|---:|---|---|"]
    for group in report["groups"]:
        freq, adoption = group["first_zg_decision_round_batch_consistency"], group["adoption_summary"]
        lines.append(f"| {cell(group['group'])} | {freq['unique_values']} | {cell(freq['modal_count'])} / {freq['observed_trials']} | {adoption['no_call_trials']} / {adoption['unknown_trials']} |")
    lines += ["", "| 组 / 运行 / 调用 | zg 决策轮 | 当时目标排名 | 回放目标排名 | 输出一致 | 此后检索 / 工具 | 后续读到目标定义 |", "|---|---:|---:|---:|---|---|---|"]
    for group in report["groups"]:
        for trial in group["trials"]:
            for chain in trial["query_chains"]:
                actual, replay = (_target(chain[key]["request_scores"]) for key in ("actual_observation", "replay_observation"))
                following = chain["following_observed_actions"]
                rank = lambda target: target.get("first_hit_rank") if target.get("status") == "scored" and target.get("first_hit_rank") is not None else "miss" if target.get("status") == "scored" else "unknown"
                values = (group["group"] + " / " + trial["trial_id"] + " / " + str(chain["call_id"]), chain["zg_decision_round_index"], rank(actual), rank(replay), chain["actual_and_replay_output_identical"],
                    cell(following["search_calls"]) + " / " + cell(following["tool_calls"]), following["read_of_returned_target_definition"]["status"])
                lines.append("| " + " | ".join(cell(x) for x in values) + " |")
    lines += ["", "## 同一已审查入口的补充对照", "",
              "不同查询的部分正例集合可能不同，不能直接把各自首个命中排名的差异归因于检索。以下仅取同上下文原题及所有等价改写已接受入口的交集；不改主分数，不含合法子目标。", ""]
    for context in report.get("shared_anchor_diagnostic", {}).get("contexts", []):
        symbols = ", ".join(t.get("symbol") or t["target_id"] for t in context["common_targets"]) or "unknown"
        lines += [f"上下文 `{context['context_id']}`；接受集合种数 {context['accepted_target_set_variants']}；共同入口：`{symbols}`。", "",
                  "| 请求 | 原主排名 | 共同入口排名 | 共同入口 RR@10 |", "|---|---:|---:|---:|"]
        for observation in context["observations"]:
            if observation["kind"] != "replay":
                continue
            score = observation["common_anchor_score"]
            rank_value = score["first_hit_rank"] if score["first_hit_rank"] is not None else "miss" if score["status"] == "scored" else "unknown"
            lines.append("| " + " | ".join(cell(v) for v in (observation["unit_id"], observation["primary_first_hit_rank"], rank_value, score["rr_at_10"])) + " |")
        lines.append("")
    lines += ["", "原题参照单列保存在 JSON；实际请求的第 1 次回放承担质量观察，其余重复只检查检索一致性。", "", "限制：", ""]
    lines.extend("- " + value for value in report["limitations"])
    if report["missing_stages"]:
        lines += ["", "缺少阶段：" + ", ".join(report["missing_stages"])]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--replay-dir", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--annotation-dir", type=Path, required=True)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--entries", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    analysis, plan, replay = _json(args.analysis), _json(args.replay_dir / "replay-plan.json"), _json(args.replay_dir / "replay-report.json")
    labels = _json(args.labels)
    audit_path = next((args.annotation_dir / name for name in ("annotation-manifest.json", "ground-truth-audit.json", "annotation-audit.json", "audit.json", "manifest.json") if (args.annotation_dir / name).is_file()), None)
    audit = _json(audit_path)
    provenance_path = args.replay_dir / "provenance.json"
    provenance = _json(provenance_path)
    for artifact, key, path in ((labels, "analysis_sha256", args.analysis), (audit, "analysis_sha256", args.analysis),
                                (audit, "labels_sha256", args.labels), (provenance, "analysis_sha256", args.analysis),
                                (provenance, "labels_sha256", args.labels), (provenance, "plan_sha256", args.replay_dir / "replay-plan.json")):
        if artifact.get(key) and (not path.is_file() or artifact[key] != hashlib.sha256(path.read_bytes()).hexdigest()):
            raise ValueError(f"Joint report provenance mismatch: {key}")
    observed_path = args.replay_dir / "observed-query-scores.json"
    observed = json.loads(observed_path.read_text(encoding="utf-8")) if observed_path.is_file() else None
    report = build_report(args.runs_dir, analysis, plan, replay, labels, audit, case=_json(args.case) or None,
                          entries=_json(args.entries) or None, observed_scores=observed or None)
    inputs = [args.analysis, args.labels, args.replay_dir / "replay-plan.json", args.replay_dir / "replay-report.json", observed_path, provenance_path] + ([audit_path] if audit_path else [])
    report["artifact_provenance"] = [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in inputs if path.is_file()]
    protected = {p.resolve() for p in inputs} | {(args.runs_dir / source["path"]).resolve() for source in analysis.get("input_artifacts", [])}
    if args.output.resolve() in protected or args.output.with_suffix(".md").resolve() in protected or args.output.suffix == ".md":
        parser.error("Output cannot replace an input artifact and must be JSON")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()
