"""Pure planning and accounting for the predeclared prompt-stability experiment.

No model calls, grading, imputation, winner selection or trajectory repair occur
here. Results and judge assessments are lists (or objects with a ``trials``
list), joined by trial_id. A result contains execution_status/status, metrics,
explicit usage_complete/tools_complete booleans and optional behavior.
Unknown completeness cannot qualify an observed low cost as a measured delta.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


PROFILES = {"B": "baseline", "C": "zvec-grep-current", "N": "zvec-grep-candidate"}
METRICS = ("input_tokens", "tool_calls_attempted")
JUDGE_CRITERIA = ("factual_correctness", "necessary_completeness", "evidence_support")
REPETITIONS = 10
ORDER_SEED = 1729
MODEL_SEED = 20260915
T95_DF9 = 2.2621571627409915
T_ASSUMPTIONS = ("Auxiliary two-sided 95% t interval for the mean paired difference; "
                 "n=10, df=9, independent approximately normal block differences. "
                 "Ten repetitions do not prove stable benefit or quality equivalence.")


def make_plan(case_id: str, repetitions: int = REPETITIONS, seed: int = ORDER_SEED,
              model_seed: int = MODEL_SEED, candidate: str | None = "Pxx") -> dict[str, Any]:
    """Freeze ten blocks, each containing each arm once in balanced positions.

    ``candidate=None`` explicitly means no promotion: run only B and C. Pxx is
    a draft placeholder and must be replaced before a confirming experiment.
    ``order_seed`` controls execution order. ``model_seed`` is fixed across
    arms and repetitions and must be verified from captured provider requests.
    """
    if not isinstance(case_id, str) or not case_id.strip() or any(c in case_id for c in "/\\"):
        raise ValueError("case_id must be a non-empty path-safe identifier")
    if type(repetitions) is not int or repetitions != REPETITIONS:
        raise ValueError("this confirmation protocol requires exactly ten repetitions")
    if type(seed) is not int:
        raise ValueError("order seed must be an integer")
    if type(model_seed) is not int or not 0 <= model_seed <= 2**31 - 1:
        raise ValueError("model seed must be a 32-bit non-negative integer")
    if candidate is not None and (not isinstance(candidate, str) or not candidate.strip() or candidate == "P00"):
        raise ValueError("candidate must identify a distinct frozen version, or be None for no promotion")
    arms = list(PROFILES) if candidate is not None else ["B", "C"]
    rng = random.Random(seed)
    orders = []
    # Each complete Latin cycle contributes one observation at every position.
    # The final partial cycle contributes at most one extra at any position.
    while len(orders) < repetitions:
        base = list(arms)
        rng.shuffle(base)
        cycle = [base[i:] + base[:i] for i in range(len(arms))]
        rng.shuffle(cycle)
        orders.extend(cycle[:repetitions - len(orders)])
    rng.shuffle(orders)
    trials = []
    for block, order in enumerate(orders, 1):
        for position, arm in enumerate(order, 1):
            trial_id = f"{case_id}-b{block:02d}-{arm}"
            trials.append({"trial_id": trial_id, "case_id": case_id, "arm": arm,
                           "profile": PROFILES[arm], "prompt_version": None if arm == "B" else "P00" if arm == "C" else candidate,
                           "block": block, "repetition": block, "order": len(trials) + 1,
                           "model_seed": model_seed,
                           "position": position, "trajectory_path": f"{trial_id}/agent/trajectory.json",
                           "status": "planned"})
    return {"schema_version": 1, "protocol": "e2e-prompt-stability-v1", "case_id": case_id,
            "repetitions": repetitions, "repetitions_per_profile": repetitions,
            "order_seed": seed, "model_seed": model_seed, "candidate": candidate,
            "candidate_status": "no_promotion" if candidate is None else "placeholder" if candidate == "Pxx" else "selected_requires_manifest_hash",
            "planned_trials": len(trials), "trials": trials}


def _number(value: Any) -> int | float | None:
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _safe_copy(value: Any) -> Any:
    """Preserve malformed non-finite observations explicitly in strict JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_nonfinite_number": str(value)}
    if isinstance(value, dict):
        return {key: _safe_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_copy(item) for item in value]
    return copy.deepcopy(value)


def _records(value: Any, name: str) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    values = value.get("trials") if isinstance(value, dict) else value
    if not isinstance(values, list):
        raise ValueError(f"{name} must be a list or an object containing trials")
    rows = {}
    for row in values:
        ident = row.get("trial_id") if isinstance(row, dict) else None
        if not isinstance(ident, str) or not ident or ident in rows:
            raise ValueError(f"{name} requires unique non-empty trial IDs")
        rows[ident] = row
    return rows


def _description(values: list[int | float]) -> dict[str, Any]:
    return {"n": len(values), "values": values,
            "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "minimum": min(values) if values else None, "maximum": max(values) if values else None,
            "sample_sd": statistics.stdev(values) if len(values) > 1 else None}


def _quality(row: dict[str, Any]) -> str:
    value = row.get("consensus_status", row.get("quality"))
    return value if isinstance(value, str) and value else "unscored"


def _judge_scores(review: dict[str, Any]) -> dict[str, dict[str, int | None]]:
    """Keep both judges' criterion scores separate; '?' and missing stay unknown."""
    judgments = review.get("judgments") if isinstance(review.get("judgments"), dict) else {}
    scores = {}
    for judge, judgment in judgments.items():
        if not isinstance(judgment, dict):
            continue
        assessment = judgment.get("assessment") if isinstance(judgment.get("assessment"), dict) else {}
        scores[judge] = {}
        for criterion in JUDGE_CRITERIA:
            detail = assessment.get(criterion)
            value = detail.get("score") if isinstance(detail, dict) else None
            scores[judge][criterion] = value if type(value) is int and value in (0, 1) else None
    return scores


def _score_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    judges = sorted({judge for row in rows for judge in row["judge_scores"]})
    summary = {}
    for judge in judges:
        summary[judge] = {}
        for criterion in JUDGE_CRITERIA:
            values = [score for row in rows
                      if (score := row["judge_scores"].get(judge, {}).get(criterion)) is not None]
            summary[judge][criterion] = {"scored": len(values), "planned": len(rows), "ones": sum(values),
                                         "mean": statistics.mean(values) if values else None}
    return summary


def _repetition(values: list[Any], planned: int) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for value in values:
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        item = groups.setdefault(digest, {"sha256": digest, "value": value, "count": 0})
        item["count"] += 1
    ordered = sorted(groups.values(), key=lambda g: (-g["count"], g["sha256"]))
    modal = max((g["count"] for g in ordered), default=0)
    return {"planned": planned, "observed": len(values), "missing": planned - len(values),
            "unique": len(groups), "modal_count": modal,
            "modal_fraction_of_observed": modal / len(values) if values else None, "groups": ordered}


def _validate_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(_records(plan, "plan").values())
    repetitions = plan.get("repetitions_per_profile", plan.get("repetitions"))
    if not rows or repetitions != REPETITIONS:
        raise ValueError("plan must contain ten predeclared blocks")
    arms = {row.get("arm") for row in rows}
    if arms not in ({"B", "C"}, {"B", "C", "N"}):
        raise ValueError("plan requires B/C or B/C/N arms")
    for block in range(1, repetitions + 1):
        members = [row for row in rows if row.get("block") == block]
        if len(members) != len(arms) or {row["arm"] for row in members} != arms:
            raise ValueError("each block must have exactly one trial per arm")
        if {row.get("position") for row in members} != set(range(1, len(arms) + 1)):
            raise ValueError("each block requires unique execution positions")
    if len(rows) != repetitions * len(arms) or {r.get("order") for r in rows} != set(range(1, len(rows) + 1)):
        raise ValueError("plan order must cover exactly the scheduled trials")
    for row in rows:
        if row.get("profile") != PROFILES[row["arm"]]:
            raise ValueError("plan arm/profile mismatch")
        if row["order"] != (row["block"] - 1) * len(arms) + row["position"]:
            raise ValueError("plan execution order must agree with block and position")
    for arm in arms:
        counts = [sum(r["arm"] == arm and r["position"] == p for r in rows) for p in range(1, len(arms) + 1)]
        if max(counts) - min(counts) > 1:
            raise ValueError("arm execution positions must be near-balanced")
    return sorted(rows, key=lambda row: row["order"])


def summarize(plan: dict[str, Any], results: Any, quality: Any = None, *,
              source_reference: Any = None, controls: dict[str, Any] | None = None) -> dict[str, Any]:
    """Account for every planned trial and all three within-block differences.

    Required metric names are input_tokens and tool_calls_attempted. Token
    comparisons require usage_complete=True; tool comparisons separately
    require tools_complete=True. Quality comes from a frozen external review,
    never from self-reported execution success. Observed partial/failed costs
    remain visible but do not enter measurement-complete comparisons.
    """
    planned = _validate_plan(plan)
    observed, reviewed = _records(results, "results"), _records(quality, "quality")
    planned_ids = {t["trial_id"] for t in planned}
    rows = []
    for trial in planned:
        ident = trial["trial_id"]
        result, review = observed.get(ident, {}), reviewed.get(ident, {})
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        status = result.get("execution_status", result.get("status", "unknown")) if ident in observed else "missing"
        if not isinstance(status, str) or not status:
            status = "unknown"
        behavior = result.get("behavior") if isinstance(result.get("behavior"), dict) else {}
        request = behavior.get("first_zg_request")
        row = {**copy.deepcopy(trial), "planned_status": trial.get("status"), "execution_status": status,
               "quality": _quality(review), "judge_scores": _judge_scores(review),
               "usage_complete": result.get("usage_complete") is True,
               "usage_completeness": result.get("usage_complete") if type(result.get("usage_complete")) is bool else None,
               "tools_complete": result.get("tools_complete") is True,
               "tools_completeness": result.get("tools_complete") if type(result.get("tools_complete")) is bool else None,
               "metrics": {key: _number(metrics.get(key)) for key in (*METRICS, "cache_read_tokens", "cache_write_tokens", "output_tokens")},
               "zg_adopted": behavior.get("zg_adopted") if type(behavior.get("zg_adopted")) is bool else None,
               "first_zg_request": copy.deepcopy(request) if isinstance(request, dict) and request else None,
               "observation": _safe_copy(result), "quality_review": _safe_copy(review)}
        row["quality_passed_completed"] = status == "completed" and row["quality"] == "pass"
        rows.append(row)
    arm_reports = {}
    for arm in PROFILES:
        members = [r for r in rows if r["arm"] == arm]
        if not members:
            continue
        requests = [r["first_zg_request"] for r in members if r["first_zg_request"] is not None]
        queries = []
        for request in requests:
            args = request.get("arguments") if isinstance(request.get("arguments"), dict) else request
            if isinstance(args.get("query"), str):
                queries.append(args["query"])
        cost = {}
        for metric in METRICS:
            flag = "usage_complete" if metric == "input_tokens" else "tools_complete"
            cost[metric] = {"observed_all_statuses": _description([r["metrics"][metric] for r in members if r["metrics"][metric] is not None]),
                            "completed_quality_passed_and_complete": _description([r["metrics"][metric] for r in members
                                if r["quality_passed_completed"] and r[flag] and r["metrics"][metric] is not None])}
        arm_reports[arm] = {"profile": PROFILES[arm], "planned": len(members),
            "execution_counts": dict(Counter(r["execution_status"] for r in members)),
            "quality_counts": dict(Counter(r["quality"] for r in members)),
            "completed_quality_passed": sum(r["quality_passed_completed"] for r in members),
            "usage_completeness_counts": {"complete": sum(r["usage_completeness"] is True for r in members),
                "incomplete": sum(r["usage_completeness"] is False for r in members),
                "unknown": sum(r["usage_completeness"] is None for r in members)},
            "zg_adoption": {"yes": sum(r["zg_adopted"] is True for r in members),
                            "no": sum(r["zg_adopted"] is False for r in members),
                            "unknown": sum(r["zg_adopted"] is None for r in members), "planned": len(members)},
            "first_complete_request_repetition": _repetition(requests, len(members)),
            "first_query_repetition": _repetition(queries, len(members)), "cost": cost,
            "judge_scores": _score_summary(members)}
    pairs = {}
    by_block_arm = {(r["block"], r["arm"]): r for r in rows}
    for left, right in (("C", "B"), ("N", "B"), ("N", "C")):
        if left not in arm_reports:
            continue
        comparison = {"left": left, "right": right, "direction": "left minus right; negative cost difference means lower observed cost",
                      "planned_pairs": REPETITIONS, "metrics": {}, "judge_score_deltas": {}}
        judges = sorted(set(arm_reports[left]["judge_scores"]) | set(arm_reports[right]["judge_scores"]))
        for judge in judges:
            comparison["judge_score_deltas"][judge] = {}
            for criterion in JUDGE_CRITERIA:
                differences = []
                for block in range(1, REPETITIONS + 1):
                    a, b = by_block_arm[block, left], by_block_arm[block, right]
                    av = a["judge_scores"].get(judge, {}).get(criterion)
                    bv = b["judge_scores"].get(judge, {}).get(criterion)
                    if av is not None and bv is not None:
                        differences.append(av - bv)
                comparison["judge_score_deltas"][judge][criterion] = {
                    "scored_pairs": len(differences), "planned_pairs": REPETITIONS,
                    "mean": statistics.mean(differences) if differences else None,
                    "improved": sum(value > 0 for value in differences),
                    "unchanged": sum(value == 0 for value in differences),
                    "declined": sum(value < 0 for value in differences)}
        for metric in METRICS:
            flag = "usage_complete" if metric == "input_tokens" else "tools_complete"
            entries = []
            for block in range(1, REPETITIONS + 1):
                a, b = by_block_arm[block, left], by_block_arm[block, right]
                av, bv = a["metrics"][metric], b["metrics"][metric]
                reasons = []
                for label, row in ((left, a), (right, b)):
                    if row["execution_status"] != "completed":
                        reasons.append(f"{label}:execution_{row['execution_status']}")
                    if row["quality"] != "pass":
                        reasons.append(f"{label}:quality_{row['quality']}")
                    if not row[flag]:
                        reasons.append(f"{label}:{flag}_not_verified")
                    if row["metrics"][metric] is None:
                        reasons.append(f"{label}:metric_unknown")
                entries.append({"block": block, "left_trial_id": a["trial_id"], "right_trial_id": b["trial_id"],
                                "left": av, "right": bv, "observed_delta": av - bv if av is not None and bv is not None else None,
                                "benefit_eligible": not reasons, "exclusions": reasons})
            measured = []
            for entry in entries:
                a, b = by_block_arm[entry["block"], left], by_block_arm[entry["block"], right]
                entry["measurement_complete"] = (a["execution_status"] == b["execution_status"] == "completed"
                                                 and a[flag] and b[flag] and entry["observed_delta"] is not None)
                if entry["measurement_complete"]:
                    measured.append(entry["observed_delta"])
            eligible = [e["observed_delta"] for e in entries if e["benefit_eligible"]]
            summary = _description(eligible)
            measured_summary = _description(measured)
            interval = None
            if len(eligible) == REPETITIONS:
                radius = T95_DF9 * statistics.stdev(eligible) / math.sqrt(REPETITIONS)
                interval = {"lower": summary["mean"] - radius, "upper": summary["mean"] + radius,
                            "n": REPETITIONS, "df": REPETITIONS - 1, "assumptions": T_ASSUMPTIONS}
            comparison["metrics"][metric] = {"pairs": entries, "eligible_pairs": len(eligible),
                "measured_pairs": len(measured), "measured_difference_summary": measured_summary,
                "complete_planned_pair_estimate": len(eligible) == REPETITIONS,
                "qualified_difference_summary": summary, "auxiliary_t95": interval,
                "status": "ten_qualified_pairs" if len(eligible) == REPETITIONS else "insufficient_complete_quality_qualified_pairs"}
        pairs[f"{left}-{right}"] = comparison
    controls = copy.deepcopy(controls) if controls is not None else {}
    unknown_controls = [key for key, value in controls.items() if value is None or value == "unknown" or
                        isinstance(value, dict) and value.get("status") in ("unknown", "unverified", "not_observable")]
    return {"schema_version": 1, "protocol": plan.get("protocol"), "case_id": plan.get("case_id"),
            "candidate": plan.get("candidate"), "order_seed": plan.get("order_seed"), "model_seed": plan.get("model_seed"),
            "plan": copy.deepcopy(plan), "source_reference": copy.deepcopy(source_reference),
            "controls": controls, "unknown_controls": unknown_controls,
            "controls_record_status": "provided" if controls else "unknown",
            "scope": "Single development QA, ten independent sessions per arm with a fixed requested model seed; no population or quality-equivalence claim.",
            "input_token_convention": "Use the native adapter's reported input convention, including cached input where that adapter specifies it; billing cost is separate.",
            "planned_trials": len(rows), "observed_trials": sum(r["execution_status"] != "missing" for r in rows),
            "execution_counts": dict(Counter(r["execution_status"] for r in rows)),
            "quality_counts": dict(Counter(r["quality"] for r in rows)), "trials": rows, "arms": arm_reports,
            "comparisons": pairs, "unplanned_results": [_safe_copy(r) for k, r in observed.items() if k not in planned_ids],
            "unplanned_quality": [_safe_copy(r) for k, r in reviewed.items() if k not in planned_ids],
            "limitations": ["Observed costs include failures; paired cost summaries require completed runs and complete measurements, independently of judge scores.",
                "Measured subsets are conditional and must not replace planned denominators.", T_ASSUMPTIONS,
                "Query repetition describes observable behavior, not correctness or a causal explanation of total cost.",
                "Sampling and server behavior not verified by effective-request evidence remain unknown."]}


def _cell(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# E2E prompt stability: {report['case_id']}", "", report["scope"], "",
             f"Planned: {report['planned_trials']}; observed: {report['observed_trials']}; execution: `{json.dumps(report['execution_counts'], sort_keys=True)}`.",
             f"Source reference: `{json.dumps(report['source_reference'], ensure_ascii=False, sort_keys=True)}`.",
             f"Controls: {report['controls_record_status']}; explicitly unknown: {', '.join(report['unknown_controls']) or 'none listed (not proof all controls are verified)' }.", "",
             "Observed costs below include failures and partial measurements. Missing values are unknown, never zero.", "",
             "| Block | Arm | Position | Execution | Input tokens | Tools | Usage complete | Tools complete | zg used |",
             "|---:|---|---:|---|---:|---:|---|---|---|"]
    for row in report["trials"]:
        values = [row["block"], row["arm"], row["position"], row["execution_status"],
                  row["metrics"]["input_tokens"], row["metrics"]["tool_calls_attempted"],
                  row["usage_completeness"], row["tools_completeness"], row["zg_adopted"]]
        lines.append("| " + " | ".join(_cell(x) for x in values) + " |")
    lines += ["", "Judge scores: 1 means criterion met, 0 means not met; unknown answers are excluded from the mean and retained in the denominator.", "",
              "| Arm | Judge | Criterion | Mean | Scored / planned |",
              "|---|---|---|---:|---:|"]
    for arm, summary in report["arms"].items():
        for judge, criteria in summary.get("judge_scores", {}).items():
            for criterion, score in criteria.items():
                lines.append("| " + " | ".join((_cell(arm), _cell(judge), criterion,
                    _cell(score["mean"]), f"{score['scored']}/{score['planned']}")) + " |")
    lines += ["", "Descriptive costs use all observed statuses, including partial failed runs; they are not benefit estimates.", "",
              "| Arm | Metric | Observed / planned | Observed mean | Median | Range |",
              "|---|---|---:|---:|---:|---|"]
    for arm, summary in report["arms"].items():
        for metric, data in summary["cost"].items():
            stats = data["observed_all_statuses"]
            values = [arm, metric, f"{stats['n']}/{summary['planned']}", stats["mean"], stats["median"],
                      f"{_cell(stats['minimum'])} … {_cell(stats['maximum'])}"]
            lines.append("| " + " | ".join(_cell(x) for x in values) + " |")
    lines += ["", "Only completed pairs with verified metric completeness enter measured cost summaries, independently of judge scores.",
              "C−B compares current integration; N−B candidate integration; N−C the prompt change. Negative deltas indicate lower cost.", "",
              "| Comparison | Metric | Ten observed block deltas | Measured pairs | Measured mean | Median | Range |",
              "|---|---|---|---:|---:|---:|---|"]
    for name, comparison in report["comparisons"].items():
        for metric, data in comparison["metrics"].items():
            stats = data["measured_difference_summary"]
            deltas = ", ".join(_cell(p["observed_delta"]) + ("*" if not p["measurement_complete"] else "") for p in data["pairs"])
            values = [name, metric, deltas, f"{data['measured_pairs']}/{comparison['planned_pairs']}", stats["mean"], stats["median"],
                      f"{_cell(stats['minimum'])} … {_cell(stats['maximum'])}"]
            lines.append("| " + " | ".join(_cell(x) for x in values) + " |")
        lines += ["", f"{name} judge score deltas (left minus right):", "",
                  "| Judge | Criterion | Mean delta | Scored pairs | Improved / unchanged / declined |",
                  "|---|---|---:|---:|---:|"]
        for judge, criteria in comparison.get("judge_score_deltas", {}).items():
            for criterion, score in criteria.items():
                lines.append("| " + " | ".join((_cell(judge), criterion, _cell(score["mean"]),
                    f"{score['scored_pairs']}/{score['planned_pairs']}",
                    f"{score['improved']}/{score['unchanged']}/{score['declined']}")) + " |")
    lines += ["", "* Excluded from measured comparison; reasons and all raw observations remain in JSON.", "", T_ASSUMPTIONS, "",
              "| Arm | zg used / planned | Unknown adoption | First complete request: unique / observed | Modal count / observed |",
              "|---|---:|---:|---:|---:|"]
    for arm, summary in report["arms"].items():
        repetition, adoption = summary["first_complete_request_repetition"], summary["zg_adoption"]
        values = [arm, f"{adoption['yes']}/{adoption['planned']}",
                  adoption["unknown"], f"{repetition['unique']}/{repetition['observed']}", f"{repetition['modal_count']}/{repetition['observed']}"]
        lines.append("| " + " | ".join(_cell(x) for x in values) + " |")
    lines += ["", "Exact request grouping preserves argument values, omitted fields and array order; JSON key order is ignored. Repetition is not correctness.",
              "Full observations, quality reviews, unknowns, per-arm cost summaries and exclusions are retained in JSON.", ""]
    return "\n".join(lines)


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}" if denominator else "0/0"


def _percent_change(left: Any, right: Any) -> str:
    if type(left) not in (int, float) or type(right) not in (int, float) or right == 0:
        return "unknown"
    return f"{(left - right) / right:+.1%}"


def _wire_counts(report: dict[str, Any]) -> tuple[int, int, int, int]:
    observed = [row for row in report.get("trials", []) if row.get("execution_status") != "missing"]
    contracts = [row.get("observation", {}).get("wire_contract", {}) for row in observed]
    return (sum(contract.get("valid") is True for contract in contracts), len(observed),
            sum(contract.get("temperature_zero_verified") is True for contract in contracts),
            sum(contract.get("seed_verified") is True for contract in contracts))


def render_ci_conclusion(e2e_reports: dict[str, dict[str, Any]], retrieval: dict[str, Any]) -> str:
    """Render one decision-oriented dashboard across E2E, Agent and retrieval layers."""
    case_id = retrieval.get("case_id") or next((report.get("case_id") for report in e2e_reports.values()), "unknown")
    lines = [f"# Benchmark conclusion: {case_id}", "",
             "Judge scores are reported per model and criterion (0/1); unknown scores are excluded with their denominators shown. Negative cost changes mean zg used fewer resources. Cost deltas require completed runs and complete measurements, independently of judge scores.", "",
             "## 1. Run health and sampling controls", "",
             "| Agent + model | Completed trials | Measured input pairs | temp=0 verified | seed verified |",
             "|---|---:|---:|---:|---:|"]
    for group, report in e2e_reports.items():
        arms = report.get("arms", {})
        baseline, current = arms.get("B", {}), arms.get("C", {})
        comparison = report.get("comparisons", {}).get("C-B", {})
        pair = comparison.get("metrics", {}).get("input_tokens", {})
        _, observed, temperatures, seeds = _wire_counts(report)
        lines.append("| " + " | ".join((group,
            _ratio(report.get("execution_counts", {}).get("completed", 0), report.get("planned_trials", 0)),
            _ratio(pair.get("measured_pairs", 0), comparison.get("planned_pairs", REPETITIONS)),
            _ratio(temperatures, observed), _ratio(seeds, observed))) + " |")

    lines += ["", "`temperature=0` and the fixed seed are verified from outgoing provider requests. This proves the controls were applied; behavior stability is reported separately.", "",
              "## 2. Agent behavior", "",
              "| Agent + model | zg adoption | First request unique / observed | Modal request share | First query unique / observed |",
              "|---|---:|---:|---:|---:|"]
    for group, report in e2e_reports.items():
        current = report.get("arms", {}).get("C", {})
        adoption = current.get("zg_adoption", {})
        repetition = current.get("first_complete_request_repetition", {})
        observed = repetition.get("observed", 0)
        modal = repetition.get("modal_count", 0)
        queries = current.get("first_query_repetition", {})
        lines.append("| " + " | ".join((group,
            _ratio(adoption.get("yes", 0), adoption.get("planned", 0)),
            _ratio(repetition.get("unique", 0), observed),
            f"{modal / observed:.0%}" if observed else "unknown",
            _ratio(queries.get("unique", 0), queries.get("observed", 0)))) + " |")

    lines += ["", "## 3. E2E judge scores", "",
              "| Agent + model | Judge | Criterion | Baseline mean (n/10) | zg mean (n/10) | Paired zg−baseline (n/10) |",
              "|---|---|---|---:|---:|---:|"]
    for group, report in e2e_reports.items():
        arms = report.get("arms", {})
        baseline, current = arms.get("B", {}), arms.get("C", {})
        deltas = report.get("comparisons", {}).get("C-B", {}).get("judge_score_deltas", {})
        judges = sorted(set(baseline.get("judge_scores", {})) | set(current.get("judge_scores", {})))
        for judge in judges:
            for criterion in JUDGE_CRITERIA:
                b = baseline.get("judge_scores", {}).get(judge, {}).get(criterion, {})
                c = current.get("judge_scores", {}).get(judge, {}).get(criterion, {})
                delta = deltas.get(judge, {}).get(criterion, {})
                lines.append("| " + " | ".join((_cell(group), _cell(judge), criterion,
                    f"{_cell(b.get('mean'))} ({b.get('scored', 0)}/{b.get('planned', REPETITIONS)})",
                    f"{_cell(c.get('mean'))} ({c.get('scored', 0)}/{c.get('planned', REPETITIONS)})",
                    f"{_cell(delta.get('mean'))} ({delta.get('scored_pairs', 0)}/{delta.get('planned_pairs', REPETITIONS)})")) + " |")
    lines += ["", "Each mean is the share of numeric 1 scores. Paired differences use only blocks where that judge scored both answers on that criterion. Judge models are not merged.", "",
              "## 4. E2E cost", "",
              "| Agent + model | Mean input B → C | Descriptive change | Measured paired input Δ | Mean tools B → C | Descriptive change | Measured paired tool Δ |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    findings = []
    for group, report in e2e_reports.items():
        arms = report.get("arms", {})
        baseline, current = arms.get("B", {}), arms.get("C", {})
        def mean(arm: dict[str, Any], metric: str) -> Any:
            return arm.get("cost", {}).get(metric, {}).get("observed_all_statuses", {}).get("mean")
        comparisons = report.get("comparisons", {}).get("C-B", {}).get("metrics", {})
        input_pair = comparisons.get("input_tokens", {})
        tool_pair = comparisons.get("tool_calls_attempted", {})
        input_b, input_c = mean(baseline, "input_tokens"), mean(current, "input_tokens")
        tool_b, tool_c = mean(baseline, "tool_calls_attempted"), mean(current, "tool_calls_attempted")
        input_delta = input_pair.get("measured_difference_summary", {}).get("mean")
        tool_delta = tool_pair.get("measured_difference_summary", {}).get("mean")
        lines.append("| " + " | ".join((_cell(group), f"{_cell(input_b)} → {_cell(input_c)}",
            _percent_change(input_c, input_b), _cell(input_delta), f"{_cell(tool_b)} → {_cell(tool_c)}",
            _percent_change(tool_c, tool_b), _cell(tool_delta))) + " |")
        planned = report.get("comparisons", {}).get("C-B", {}).get("planned_pairs", REPETITIONS)
        eligible = input_pair.get("measured_pairs", 0)
        adoption = current.get("zg_adoption", {}).get("yes", 0)
        if eligible < planned:
            findings.append(f"**{group}: incomplete cost evidence** — {eligible}/{planned} input pairs have complete measurements; the paired cost delta is descriptive for this subset.")
        if adoption < current.get("planned", 0) / 2:
            findings.append(f"**{group}: adoption is the main bottleneck** — zg was used in only {adoption}/{current.get('planned', 0)} runs.")
        if eligible == planned:
            if type(input_delta) in (int, float) and type(tool_delta) in (int, float) and input_delta < 0 and tool_delta < 0:
                findings.append(f"**{group}: lower measured cost observed** — all {eligible} input pairs are available and both mean paired cost deltas are negative; read judge scores above separately for quality.")
            else:
                findings.append(f"**{group}: no consistent two-metric saving** — the measured input/tool deltas do not both show a reduction.")

    if retrieval:
        scored = []
        for replay in retrieval.get("replays", []):
            for context in replay.get("context_assessments", []):
                assessment = context.get("assessment", {})
                if assessment.get("status") == "scored":
                    scored.append(assessment.get("target", {}))
        stable = [replay.get("stability", {}).get("identical_all_five") for replay in retrieval.get("replays", [])]
        comparisons = retrieval.get("actual_vs_replay", [])
        rr_values = [row.get("rr_at_10") for row in scored if type(row.get("rr_at_10")) in (int, float)]
        lines += ["", "## 5. Retrieval explanation", "",
                  "| Scored requests | Hit@1 | Hit@5 | Hit@10 | Mean RR@10 | Five-replay identical | E2E output = replay output |",
                  "|---:|---:|---:|---:|---:|---:|---:|",
                  "| " + " | ".join((str(len(scored)),
                      _ratio(sum(row.get("hit_at_1") is True for row in scored), len(scored)),
                      _ratio(sum(row.get("hit_at_5") is True for row in scored), len(scored)),
                      _ratio(sum(row.get("hit_at_10") is True for row in scored), len(scored)),
                      f"{statistics.mean(rr_values):.3f}" if rr_values else "unknown",
                      _ratio(sum(value is True for value in stable), len(stable)),
                      _ratio(sum(row.get("original_vs_replay_text_identical") is True for row in comparisons),
                             sum(row.get("original_vs_replay_text_identical") is not None for row in comparisons)))) + " |"]
    lines += ["", "## Decision", "", *[f"- {finding}" for finding in findings], "",
              "Fixed sampling parameters reduce one source of variation but do not make an Agent deterministic. Exact request repetition and adoption above are the observed stability evidence.", "",
              "The detailed per-trial reports and every exclusion remain in the uploaded artifacts.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--case-id", required=True)
    plan_parser.add_argument("--seed", type=int, default=ORDER_SEED)
    plan_parser.add_argument("--model-seed", type=int, default=MODEL_SEED)
    plan_parser.add_argument("--candidate", default="Pxx")
    plan_parser.add_argument("--no-promotion", action="store_true")
    plan_parser.add_argument("--output", type=Path, required=True)
    report_parser = sub.add_parser("report")
    for name in ("plan", "results", "quality", "source-reference", "controls"):
        report_parser.add_argument("--" + name, type=Path, required=name in ("plan", "results"))
    report_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.command == "report" and args.output.with_suffix(".md").exists():
        parser.error("output already exists; preserve prior plans and reports")
    if args.command == "plan":
        value = make_plan(args.case_id, seed=args.seed, model_seed=args.model_seed,
                          candidate=None if args.no_promotion else args.candidate)
    else:
        def read(path: Path | None) -> Any:
            return json.loads(path.read_text(encoding="utf-8")) if path is not None else None
        value = summarize(read(args.plan), read(args.results), read(args.quality),
                          source_reference=read(args.source_reference), controls=read(args.controls))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if args.command == "report":
        args.output.with_suffix(".md").write_text(render_markdown(value), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
