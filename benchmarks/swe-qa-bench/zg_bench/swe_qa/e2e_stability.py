"""Pure planning and accounting for the predeclared prompt-stability experiment.

No model calls, grading, imputation, winner selection or trajectory repair occur
here. Results and quality are lists (or objects with a ``trials`` list), joined
by trial_id. A result contains execution_status/status, metrics, explicit
usage_complete/tools_complete booleans and optional behavior. Unknown
completeness cannot qualify an observed low cost as a benefit.
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
T95_DF4 = 2.7764451051977987
T_ASSUMPTIONS = ("Auxiliary two-sided 95% t interval for the mean paired difference; "
                 "n=5, df=4, independent approximately normal block differences. "
                 "Five repetitions do not prove stable benefit or quality equivalence.")


def make_plan(case_id: str, repetitions: int = 5, seed: int = 1729,
              candidate: str | None = "Pxx") -> dict[str, Any]:
    """Freeze five blocks, each containing each arm once in balanced positions.

    ``candidate=None`` explicitly means no promotion: run only B and C. Pxx is
    a draft placeholder and must be replaced before a confirming experiment.
    ``order_seed`` controls execution order, never the model's sampling seed.
    """
    if not isinstance(case_id, str) or not case_id.strip() or any(c in case_id for c in "/\\"):
        raise ValueError("case_id must be a non-empty path-safe identifier")
    if type(repetitions) is not int or repetitions != 5:
        raise ValueError("this confirmation protocol requires exactly five repetitions")
    if type(seed) is not int:
        raise ValueError("order seed must be an integer")
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
                           "position": position, "trajectory_path": f"{trial_id}/agent/trajectory.json",
                           "status": "planned"})
    return {"schema_version": 1, "protocol": "e2e-prompt-stability-v1", "case_id": case_id,
            "repetitions": repetitions, "repetitions_per_profile": repetitions,
            "order_seed": seed, "model_seed": None, "candidate": candidate,
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
    if not rows or plan.get("repetitions_per_profile", plan.get("repetitions")) != 5:
        raise ValueError("plan must contain five predeclared blocks")
    arms = {row.get("arm") for row in rows}
    if arms not in ({"B", "C"}, {"B", "C", "N"}):
        raise ValueError("plan requires B/C or B/C/N arms")
    for block in range(1, 6):
        members = [row for row in rows if row.get("block") == block]
        if len(members) != len(arms) or {row["arm"] for row in members} != arms:
            raise ValueError("each block must have exactly one trial per arm")
        if {row.get("position") for row in members} != set(range(1, len(arms) + 1)):
            raise ValueError("each block requires unique execution positions")
    if len(rows) != 5 * len(arms) or {r.get("order") for r in rows} != set(range(1, len(rows) + 1)):
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
    remain visible but do not enter quality-qualified comparisons.
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
               "quality": _quality(review), "usage_complete": result.get("usage_complete") is True,
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
            "first_query_repetition": _repetition(queries, len(members)), "cost": cost}
    pairs = {}
    by_block_arm = {(r["block"], r["arm"]): r for r in rows}
    for left, right in (("C", "B"), ("N", "B"), ("N", "C")):
        if left not in arm_reports:
            continue
        comparison = {"left": left, "right": right, "direction": "left minus right; negative cost difference means lower observed cost",
                      "planned_pairs": 5, "metrics": {}}
        for metric in METRICS:
            flag = "usage_complete" if metric == "input_tokens" else "tools_complete"
            entries = []
            for block in range(1, 6):
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
            eligible = [e["observed_delta"] for e in entries if e["benefit_eligible"]]
            summary = _description(eligible)
            interval = None
            if len(eligible) == 5:
                radius = T95_DF4 * statistics.stdev(eligible) / math.sqrt(5)
                interval = {"lower": summary["mean"] - radius, "upper": summary["mean"] + radius,
                            "n": 5, "df": 4, "assumptions": T_ASSUMPTIONS}
            comparison["metrics"][metric] = {"pairs": entries, "eligible_pairs": len(eligible),
                "complete_five_pair_estimate": len(eligible) == 5,
                "qualified_difference_summary": summary, "auxiliary_t95": interval,
                "status": "five_qualified_pairs" if len(eligible) == 5 else "insufficient_complete_quality_qualified_pairs"}
        pairs[f"{left}-{right}"] = comparison
    controls = copy.deepcopy(controls) if controls is not None else {}
    unknown_controls = [key for key, value in controls.items() if value is None or value == "unknown" or
                        isinstance(value, dict) and value.get("status") in ("unknown", "unverified", "not_observable")]
    return {"schema_version": 1, "protocol": plan.get("protocol"), "case_id": plan.get("case_id"),
            "candidate": plan.get("candidate"), "order_seed": plan.get("order_seed"),
            "plan": copy.deepcopy(plan), "source_reference": copy.deepcopy(source_reference),
            "controls": controls, "unknown_controls": unknown_controls,
            "controls_record_status": "provided" if controls else "unknown",
            "scope": "Single development QA, five independent sessions per arm; no population or quality-equivalence claim.",
            "input_token_convention": "Use the native adapter's reported input convention, including cached input where that adapter specifies it; billing cost is separate.",
            "planned_trials": len(rows), "observed_trials": sum(r["execution_status"] != "missing" for r in rows),
            "execution_counts": dict(Counter(r["execution_status"] for r in rows)),
            "quality_counts": dict(Counter(r["quality"] for r in rows)), "trials": rows, "arms": arm_reports,
            "comparisons": pairs, "unplanned_results": [_safe_copy(r) for k, r in observed.items() if k not in planned_ids],
            "unplanned_quality": [_safe_copy(r) for k, r in reviewed.items() if k not in planned_ids],
            "limitations": ["Observed costs include failures; only completed, quality-passed, complete pairs qualify for cost comparison.",
                "Qualified subsets are conditional and must not replace planned denominators.", T_ASSUMPTIONS,
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
             "| Block | Arm | Position | Execution | Quality | Input tokens | Tools | Usage complete | Tools complete | zg used |",
             "|---:|---|---:|---|---|---:|---:|---|---|---|"]
    for row in report["trials"]:
        values = [row["block"], row["arm"], row["position"], row["execution_status"], row["quality"],
                  row["metrics"]["input_tokens"], row["metrics"]["tool_calls_attempted"],
                  row["usage_completeness"], row["tools_completeness"], row["zg_adopted"]]
        lines.append("| " + " | ".join(_cell(x) for x in values) + " |")
    lines += ["", "Descriptive costs use all observed statuses, including partial failed runs; they are not benefit estimates.", "",
              "| Arm | Metric | Observed / planned | Observed mean | Median | Range | Completed, passed, complete / planned |",
              "|---|---|---:|---:|---:|---|---:|"]
    for arm, summary in report["arms"].items():
        for metric, data in summary["cost"].items():
            stats = data["observed_all_statuses"]
            values = [arm, metric, f"{stats['n']}/{summary['planned']}", stats["mean"], stats["median"],
                      f"{_cell(stats['minimum'])} … {_cell(stats['maximum'])}",
                      f"{data['completed_quality_passed_and_complete']['n']}/{summary['planned']}"]
            lines.append("| " + " | ".join(_cell(x) for x in values) + " |")
    lines += ["", "Only completed, quality-passed pairs with verified metric completeness enter the qualified summaries.",
              "C−B compares current integration; N−B candidate integration; N−C the prompt change. Negative deltas indicate lower cost.", "",
              "| Comparison | Metric | Five observed block deltas | Qualified pairs | Qualified mean | Median | Range | Auxiliary t95 |",
              "|---|---|---|---:|---:|---:|---|---|"]
    for name, comparison in report["comparisons"].items():
        for metric, data in comparison["metrics"].items():
            stats, interval = data["qualified_difference_summary"], data["auxiliary_t95"]
            deltas = ", ".join(_cell(p["observed_delta"]) + ("*" if not p["benefit_eligible"] else "") for p in data["pairs"])
            values = [name, metric, deltas, f"{data['eligible_pairs']}/5", stats["mean"], stats["median"],
                      f"{_cell(stats['minimum'])} … {_cell(stats['maximum'])}",
                      f"{_cell(interval['lower'])} … {_cell(interval['upper'])}" if interval else "unavailable"]
            lines.append("| " + " | ".join(_cell(x) for x in values) + " |")
    lines += ["", "* Excluded from qualified comparison; all per-block reasons remain in JSON. Conditional subsets do not establish full planned-sample benefit.", "", T_ASSUMPTIONS, "",
              "| Arm | Quality passed / planned | zg used / planned | Unknown adoption | First complete request: unique / observed | Modal count / observed |",
              "|---|---:|---:|---:|---:|---:|"]
    for arm, summary in report["arms"].items():
        repetition, adoption = summary["first_complete_request_repetition"], summary["zg_adoption"]
        values = [arm, f"{summary['completed_quality_passed']}/{summary['planned']}", f"{adoption['yes']}/{adoption['planned']}",
                  adoption["unknown"], f"{repetition['unique']}/{repetition['observed']}", f"{repetition['modal_count']}/{repetition['observed']}"]
        lines.append("| " + " | ".join(_cell(x) for x in values) + " |")
    lines += ["", "Exact request grouping preserves argument values, omitted fields and array order; JSON key order is ignored. Repetition is not correctness.",
              "Full observations, quality reviews, unknowns, per-arm cost summaries and exclusions are retained in JSON.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--case-id", required=True)
    plan_parser.add_argument("--seed", type=int, default=1729)
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
        value = make_plan(args.case_id, seed=args.seed, candidate=None if args.no_promotion else args.candidate)
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
