"""Aggregate Workspace QA trials without dropping missing planned observations."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any


PROTOCOL = "workspace-qa-qoder-native-install-v3"
PROFILES = ("baseline", "with-zg")
SUCCESS_STATUSES = {"completed", "success", "succeeded"}
METRICS = ("input_tokens", "output_tokens", "cached_input_tokens", "tool_calls", "zg_tool_calls", "wall_seconds", "rubric_score")
PRIMARY_METRICS = ("input_tokens", "tool_calls", "wall_seconds", "rubric_score")
from failure_audit import TERMINAL


class ReportError(ValueError):
    pass


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReportError(f"JSON must be an object: {path}")
    return value


def number(value: Any) -> float | None:
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def macro_mean(rows: list[dict[str, Any]], metric: str) -> tuple[float | None, int, int]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = number(row.get(metric))
        if value is not None:
            grouped[row["task_id"]].append(value)
    return ((mean(mean(values) for values in grouped.values()) if grouped else None),
            len(grouped), sum(len(values) for values in grouped.values()))


def manifest_plan(manifest_path: Path | None) -> tuple[dict[str, str], int | None]:
    if manifest_path is None:
        return {}, None
    manifest = read_object(manifest_path)
    if manifest.get("experiment", {}).get("protocol") != PROTOCOL:
        raise ReportError("manifest requires the native installation protocol; old bridge experiments are excluded")
    tasks, repetitions = manifest.get("tasks"), manifest.get("repetitions")
    if not isinstance(tasks, list) or not tasks:
        raise ReportError("manifest must declare nonempty tasks")
    selected = {}
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("task_id"), str) or not task["task_id"]:
            raise ReportError("manifest tasks require string task_id")
        if task["task_id"] in selected:
            raise ReportError("duplicate task in manifest")
        selected[task["task_id"]] = task.get("slice", "unspecified")
    if repetitions is not None and (type(repetitions) is not int or repetitions < 1):
        raise ReportError("manifest repetitions must be positive")
    return selected, repetitions


def load_rows(runs_dir: Path, *, manifest_path: Path | None = None, repetitions: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected, manifest_repetitions = manifest_plan(manifest_path)
    expected_model = (read_object(manifest_path).get("experiment", {}).get("requested_model")
                      if manifest_path else None)
    models = set()
    def record_model(model):
        if model is None:
            return
        if not isinstance(model, str) or not model.strip():
            raise ReportError("invalid agent model identity")
        models.add(model.lower())
        if len(models) > 1:
            raise ReportError("mixed agent models or model differs from the selected experiment")
    record_model(expected_model)
    if repetitions is not None and (type(repetitions) is not int or repetitions < 1):
        raise ReportError("repetitions must be a positive integer")
    repetitions = repetitions if repetitions is not None else manifest_repetitions
    ledger_files = sorted(runs_dir.rglob("trial-results.json"))
    declarations: dict[str, int] = {}
    declared_repetitions: dict[str, set[int]] = defaultdict(set)
    unsharded_tasks: set[str] = set()
    continuations: dict[str, dict[str, Any]] = {}
    trials: dict[tuple[str, str, int], dict[str, Any]] = {}
    trial_ids: dict[str, tuple[str, str, int]] = {}
    for path in ledger_files:
        ledger = read_object(path)
        if ledger.get("protocol") != PROTOCOL:
            raise ReportError("ledger protocol is not the native installation protocol; old or mixed experiments are excluded")
        runtime_manifest_path = path.parent / "manifest.json"
        runtime_manifest = read_object(runtime_manifest_path) if runtime_manifest_path.is_file() else None
        if runtime_manifest is not None and runtime_manifest.get("protocol") != PROTOCOL:
            raise ReportError("runtime manifest protocol differs from the native installation protocol")
        record_model(ledger.get("model"))
        if runtime_manifest is not None:
            record_model(runtime_manifest.get("model"))
            agent_spec = runtime_manifest.get("agent_spec", {})
            if not isinstance(agent_spec, dict):
                raise ReportError("invalid runtime agent spec")
            record_model(agent_spec.get("model"))
        task_id, expected = ledger.get("task_id"), ledger.get("repetitions_per_profile")
        if not isinstance(task_id, str) or not task_id or type(expected) is not int or expected < 1:
            raise ReportError(f"invalid trial-results declaration: {path}")
        if selected and task_id not in selected:
            raise ReportError(f"unexpected task artifact {task_id}; use a separate runs directory")
        if repetitions is not None and expected != repetitions:
            raise ReportError(f"task {task_id} declares {expected} repetitions, expected {repetitions}")
        shard = ledger.get("shard_repetitions")
        if shard is None:
            if task_id in declarations:
                raise ReportError(f"duplicate task ledger: {task_id}")
            declared = set(range(1, expected + 1))
            unsharded_tasks.add(task_id)
        else:
            if (not isinstance(shard, list) or not shard or len(shard) != len(set(shard))
                    or any(type(value) is not int or not 1 <= value <= expected for value in shard)
                    or task_id in unsharded_tasks or declared_repetitions[task_id].intersection(shard)):
                raise ReportError(f"invalid or overlapping task shard: {task_id}")
            declared = set(shard)
        declarations.setdefault(task_id, expected)
        if declarations[task_id] != expected:
            raise ReportError(f"task shards disagree on repetition count: {task_id}")
        declared_repetitions[task_id].update(declared)
        continuation = runtime_manifest.get("continuation") if runtime_manifest is not None else None
        if continuation is not None:
            from continuation import validate_continuation_evidence
            try:
                validate_continuation_evidence(path.parent)
            except (ValueError, OSError, TypeError, KeyError) as exc:
                raise ReportError("invalid original continuation provenance") from exc
            continuations[task_id] = continuation
        if not isinstance(ledger.get("trials"), list):
            raise ReportError("trial-results requires trials array")
        if shard is not None and {trial.get("repetition") for trial in ledger["trials"]} != declared:
            raise ReportError("task shard trials differ from declared repetitions")
        for trial in ledger["trials"]:
            if not isinstance(trial, dict) or trial.get("task_id") != task_id or trial.get("profile") not in PROFILES:
                raise ReportError("trial identity does not match ledger")
            record_model(trial.get("model"))
            if expected_model and trial.get("status") in SUCCESS_STATUSES and not trial.get("model"):
                raise ReportError("completed trial is missing its agent model identity")
            rep, trial_id = trial.get("repetition"), trial.get("trial_id")
            if type(rep) is not int or not 1 <= rep <= expected:
                raise ReportError("repetition is outside the declared plan")
            if (not isinstance(trial_id, str) or not trial_id or trial_id in trial_ids
                    or Path(trial_id).name != trial_id or trial_id in {".", ".."}):
                raise ReportError("duplicate or missing trial_id")
            key = (task_id, trial["profile"], rep)
            if key in trials:
                raise ReportError("multiple observations for a planned task/profile/repetition")
            agent = (path.parent / trial_id / "agent").resolve()
            if not agent.is_relative_to(path.parent.resolve()):
                raise ReportError("trial evidence escapes task directory")
            started = bool(trial.get("started_at")) or isinstance(trial.get("session"), dict)
            started = started or (path.parent / trial_id).exists()
            status = trial.get("status")
            attempted = (True if status in TERMINAL or status == "running" or started
                         else False if status in {"planned", "pending"} else None)
            installation = None
            if trial.get("status") in SUCCESS_STATUSES:
                if runtime_manifest is None or runtime_manifest.get("task_id") != task_id:
                    raise ReportError("completed native trials require their matching runtime manifest")
                if trial.get("source_unchanged") is not True:
                    raise ReportError("completed native trials require unchanged source evidence")
                if Path(trial_id).name != trial_id:
                    raise ReportError("trial_id escapes evidence directory")
                agent = (path.parent / trial_id / "agent").resolve()
                if not agent.is_relative_to(path.parent.resolve()):
                    raise ReportError("installation evidence escapes task directory")
                from qoder_probe import installation_evidence
                try:
                    installation = installation_evidence(agent, profile=trial["profile"])
                except (ValueError, OSError, TypeError, KeyError) as exc:
                    raise ReportError("completed native trial has invalid installation evidence") from exc
                reference = trial.get("installation")
                if not isinstance(reference, dict) or reference.get("manifest_sha256") != installation.get("manifest_sha256"):
                    raise ReportError("trial installation hash differs from its original installation evidence")
            trial_ids[trial_id], trials[key] = key, dict(trial, ledger_path=str(path.relative_to(runs_dir)),
                installation_evidence=installation, execution_attempted=attempted, terminal_recorded=status in TERMINAL,
                preserved_from_original=continuation is not None and trial_id in continuation["preserved_trial_ids"],
                effective_manifest_path=(str((path.parent / continuation["prior_manifest_path"]).relative_to(runs_dir))
                    if continuation is not None and trial_id in continuation["preserved_trial_ids"]
                    else str(runtime_manifest_path.relative_to(runs_dir))))
    if not selected:
        selected = {task: "unspecified" for task in declarations}
    if not selected:
        raise ReportError("no task plan or trial-results.json files found")
    if set(selected) - set(declarations) and repetitions is None:
        raise ReportError("repetitions required to preserve wholly missing task denominator")
    judgments, judgment_ids = {}, set()
    judgment_files = sorted(runs_dir.rglob("judgements.json"))
    for path in judgment_files:
        document = read_object(path)
        task_id = document.get("task_id")
        if task_id not in selected:
            raise ReportError("judgement for an unexpected task")
        if task_id in continuations and document.get("judgement_continuation") is None:
            raise ReportError("continued ledger requires audited judgement import provenance")
        if document.get("judgement_continuation") is not None:
            from judge import validate_judgement_continuation
            try:
                validate_judgement_continuation(document, path.parent)
            except (ValueError, OSError, TypeError, KeyError) as exc:
                raise ReportError("original imported judgements changed or provenance is invalid") from exc
        if not isinstance(document.get("trials"), list):
            raise ReportError("judgements requires trials array")
        ledger_path = path.parent / "trial-results.json"
        if ledger_path.is_file() and document.get("trial_results_sha256"):
            actual = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
            if actual != document["trial_results_sha256"]:
                raise ReportError("judgements refer to a different trial ledger")
        for judgment in document["trials"]:
            if not isinstance(judgment, dict):
                raise ReportError("judgement row must be an object")
            trial_id = judgment.get("trial_id")
            if not isinstance(trial_id, str) or trial_id in judgment_ids:
                raise ReportError("duplicate or invalid judgement trial_id")
            judgment_ids.add(trial_id)
            if trial_id not in trial_ids or trial_ids[trial_id][0] != task_id:
                raise ReportError("judgement cannot be associated with an observed trial")
            identity = trial_ids[trial_id]
            if judgment.get("profile") != identity[1] or judgment.get("repetition") != identity[2]:
                raise ReportError("judgement profile/repetition does not match trial")
            judgments[trial_id] = dict(judgment, judge_model=document.get("judge_model"),
                                       rubric_count=len(document.get("rubrics", [])),
                                       judgment_path=str(path.relative_to(runs_dir)))
    rows = []
    for task_id in sorted(selected):
        expected = declarations.get(task_id, repetitions)
        assert expected is not None
        for profile in PROFILES:
            for repetition in range(1, expected + 1):
                trial = trials.get((task_id, profile, repetition))
                row: dict[str, Any] = {"task_id": task_id, "slice": selected[task_id], "profile": profile,
                    "repetition": repetition, "trial_id": None, "execution_status": "missing_trial",
                    "execution_attempted": None, "terminal_recorded": False,
                    "judge_status": "missing_judgement", "judge_model": None, "judge_latency_seconds": None,
                    "rubric_count": None, "rubrics_passed": None, "rubric_score": None,
                    **{metric: None for metric in METRICS if metric != "rubric_score"}}
                if trial is not None:
                    row.update(trial_id=trial["trial_id"], execution_status=trial.get("status", "unknown"),
                               model=trial.get("model"),
                               execution_attempted=trial["execution_attempted"], terminal_recorded=trial["terminal_recorded"],
                               ledger_path=trial["ledger_path"], model_identity=trial.get("model_identity"),
                               provenance=trial.get("provenance"), error=trial.get("error"),
                               preserved_from_original=trial["preserved_from_original"],
                               effective_manifest_path=trial["effective_manifest_path"],
                               installation=trial.get("installation"), installation_evidence=trial.get("installation_evidence"),
                               candidate_output_path=trial.get("candidate_output_path"))
                    # Failed execution metrics are retained separately and do not enter successful-pair savings.
                    row["observed_metrics"] = {metric: trial.get(metric) for metric in METRICS if metric != "rubric_score"}
                    if trial.get("status") in SUCCESS_STATUSES:
                        row.update({metric: number(trial.get(metric)) for metric in METRICS if metric != "rubric_score"})
                    judgment = judgments.get(trial["trial_id"])
                    if judgment is not None:
                        row.update(judge_status=judgment.get("status", "unknown"), judge_model=judgment.get("judge_model"),
                                   judge_latency_seconds=number(judgment.get("judge_latency_seconds")),
                                   judgment_path=judgment["judgment_path"])
                        if judgment.get("status") == "judged":
                            answer = trial.get("answer")
                            if not isinstance(answer, str) or hashlib.sha256(answer.encode("utf-8")).hexdigest() != judgment.get("answer_sha256"):
                                raise ReportError("scored judgement does not match candidate answer hash")
                            if trial.get("status") not in SUCCESS_STATUSES:
                                raise ReportError("non-completed trial must not have a scored judgement")
                            criteria = judgment.get("criteria")
                            count = judgment["rubric_count"]
                            if not isinstance(criteria, list) or not count or len(criteria) != count:
                                raise ReportError("scored judgement does not contain every original rubric")
                            ids = []
                            for criterion in criteria:
                                if not isinstance(criterion, dict) or type(criterion.get("score")) is not bool or type(criterion.get("id")) is not int:
                                    raise ReportError("invalid scored criterion")
                                ids.append(criterion["id"])
                            if sorted(ids) != list(range(count)):
                                raise ReportError("scored criterion IDs do not match original rubric IDs")
                            passed = sum(c["score"] for c in criteria)
                            score = passed / count
                            if number(judgment.get("score")) != score:
                                raise ReportError("judgement score differs from the full original rubric mean")
                            row.update(rubric_count=count, rubrics_passed=passed, rubric_score=score)
                rows.append(row)
    plan = {"protocol": PROTOCOL, "continuations": continuations, "task_ids": sorted(selected), "expected_tasks": len(selected), "expected_trials": len(rows),
            "agent_model": next(iter(models), None),
            "expected_pairs": len(rows) // 2, "repetitions_per_task": {task: declarations.get(task, repetitions) for task in sorted(selected)},
            "task_plan_source": "manifest" if manifest_path else "observed_ledgers_only",
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest_path else None,
            "ledger_files": len(ledger_files), "judgement_files": len(judgment_files)}
    return rows, plan


def execution_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Execution coverage includes preserved failures and never implies efficacy."""
    planned = len(rows)
    attempted = sum(row.get("execution_attempted") is True for row in rows)
    terminal = sum(row.get("terminal_recorded") is True for row in rows)
    completed = sum(row["execution_status"] in SUCCESS_STATUSES for row in rows)
    judged = sum(row["execution_status"] in SUCCESS_STATUSES and row["judge_status"] == "judged" for row in rows)
    all_judged = completed == judged
    return {"planned": planned, "attempted": attempted,
            "not_started": sum(row.get("execution_attempted") is False for row in rows),
            "attempt_status_unknown": sum(row.get("execution_attempted") is None for row in rows),
            "terminal_recorded": terminal, "qa_completed": completed, "qa_judged": judged,
            "all_trials_attempted": bool(rows) and attempted == planned,
            "all_successful_answers_judged": all_judged,
            "execution_complete": bool(rows) and attempted == terminal == planned and all_judged}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    task_ids = sorted({row["task_id"] for row in rows})
    grouped = {profile: [row for row in rows if row["profile"] == profile] for profile in PROFILES}
    profiles = {}
    for profile, items in grouped.items():
        profiles[profile] = {"expected": len(items), "completed": sum(r["execution_status"] in SUCCESS_STATUSES for r in items),
            "judged": sum(r["judge_status"] == "judged" for r in items),
            "execution_status_counts": dict(Counter(r["execution_status"] for r in items)),
            "judge_status_counts": dict(Counter(r["judge_status"] for r in items)),
            "execution": execution_summary(items), "metrics": {}}
        for metric in METRICS:
            average, tasks, observations = macro_mean(items, metric)
            profiles[profile]["metrics"][metric] = {"mean": average, "observations": observations, "tasks": tasks,
                                                      "expected_observations": len(items)}
    indexed = {(r["task_id"], r["profile"], r["repetition"]): r for r in rows}
    pairs = {}
    expected_pairs = len(rows) // 2
    for metric in METRICS:
        left, right = [], []
        for row in grouped["baseline"]:
            other = indexed.get((row["task_id"], "with-zg", row["repetition"]))
            if other is not None and number(row.get(metric)) is not None and number(other.get(metric)) is not None:
                left.append(row)
                right.append(other)
        baseline_mean, tasks, observations = macro_mean(left, metric)
        with_zg_mean, _, _ = macro_mean(right, metric)
        delta = None if baseline_mean is None or with_zg_mean is None else with_zg_mean - baseline_mean
        savings = None if baseline_mean in (None, 0) or with_zg_mean is None else (baseline_mean - with_zg_mean) / baseline_mean * 100
        pairs[metric] = {"baseline_mean": baseline_mean, "with_zg_mean": with_zg_mean,
            "delta_with_zg_minus_baseline": delta, "percent_savings": savings if metric != "rubric_score" else None,
            "quality_delta_percentage_points": delta * 100 if metric == "rubric_score" and delta is not None else None,
            "matched_pairs": observations, "expected_pairs": expected_pairs, "tasks": tasks,
            "complete": observations == expected_pairs and tasks == len(task_ids)}
    complete = bool(rows) and all(profiles[p]["completed"] == profiles[p]["expected"] and profiles[p]["judged"] == profiles[p]["expected"] for p in PROFILES)
    complete = complete and all(pairs[metric]["complete"] for metric in PRIMARY_METRICS)
    return {"task_ids": task_ids, "expected_trials": len(rows), "expected_pairs": expected_pairs,
            "complete": complete, "execution": execution_summary(rows), "profiles": profiles, "paired_metrics": pairs}


def render_markdown(report: dict[str, Any]) -> str:
    summary, plan = report["summary"], report["plan"]
    execution = summary["execution"]
    def fmt(value: Any) -> str:
        return "N/A" if value is None else f"{value:.3f}"
    model = plan.get("agent_model") or "unspecified model"
    lines = [f"# Workspace Lite CN · Qoder + {model} QA comparison", "",
        "Custom Qoder QA rubric adapter; original rubrics retained in full. This is not the official ClaudeCode judge or a leaderboard result.", "",
        f"Integration: standard zg 0.2.2 install; protocol {PROTOCOL}. Original installation artifacts are hash-verified for completed trials.", "",
        f"Planned: {plan['expected_tasks']} tasks × 2 profiles = {plan['expected_trials']} trials ({plan['expected_pairs']} paired repetitions).",
        f"Coverage: {'COMPLETE' if report['efficacy_claim_ready'] else 'INCOMPLETE — descriptive observations only; no efficacy claim.'}", "",
        f"Execution: {execution['attempted']} / {execution['planned']} attempted; {execution['terminal_recorded']} terminal outcomes; {execution['qa_completed']} QA answers completed; {execution['qa_judged']} completed answers judged.",
        "Execution coverage includes original failures and does not mean every answer succeeded or establish answer quality. Preserved failures are never replaced by later trials.", "",
        "Means use equal task weights. Paired comparisons use the same task and repetition in both profiles; missing/error observations stay unscored and remain in the planned denominator.",
        "Positive savings means lower cost; positive rubric delta means higher quality. Missing cache telemetry is unknown, not zero.", "",
        "| Profile | Attempted / planned | Terminal / planned | Completed / planned | Judged / planned |", "|---|---:|---:|---:|---:|"]
    for profile in PROFILES:
        values = summary["profiles"][profile]
        lines.append(f"| {profile} | {values['execution']['attempted']} / {values['expected']} | {values['execution']['terminal_recorded']} / {values['expected']} | {values['completed']} / {values['expected']} | {values['judged']} / {values['expected']} |")
    lines.extend(["", "| Paired metric | Baseline mean | With zg mean | Savings % / quality Δ pp | Pairs / planned | Tasks |", "|---|---:|---:|---:|---:|---:|"])
    for metric in METRICS:
        item = summary["paired_metrics"][metric]
        change = item["quality_delta_percentage_points"] if metric == "rubric_score" else item["percent_savings"]
        lines.append(f"| {metric} | {fmt(item['baseline_mean'])} | {fmt(item['with_zg_mean'])} | {fmt(change)} | {item['matched_pairs']} / {item['expected_pairs']} | {item['tasks']} |")
    lines.extend(["", "| Task | Slice | Completed baseline / zg | Scored baseline / zg | Input savings % | Tool savings % | Time savings % | Rubric Δ pp |", "|---|---|---:|---:|---:|---:|---:|---:|"])
    for task_id, values in report["by_task"].items():
        baseline, zg = (values["profiles"][p] for p in PROFILES)
        pairs = values["paired_metrics"]
        savings = [fmt(pairs[k]["percent_savings"]) for k in ("input_tokens", "tool_calls", "wall_seconds")]
        lines.append(f"| {task_id} | {report['task_slices'][task_id]} | {baseline['completed']} / {zg['completed']} | {baseline['judged']} / {zg['judged']} | {' | '.join(savings)} | {fmt(pairs['rubric_score']['quality_delta_percentage_points'])} |")
    lines.extend(["", "| QA group | Planned trials | Input savings % | Tool savings % | Time savings % | Rubric Δ pp | Scored pairs / planned |", "|---|---:|---:|---:|---:|---:|---:|"])
    for name, values in report["by_qa_group"].items():
        pairs = values["paired_metrics"]
        savings = [fmt(pairs[k]["percent_savings"]) for k in ("input_tokens", "tool_calls", "wall_seconds")]
        quality = pairs["rubric_score"]
        lines.append(f"| {name} | {values['expected_trials']} | {' | '.join(savings)} | {fmt(quality['quality_delta_percentage_points'])} | {quality['matched_pairs']} / {quality['expected_pairs']} |")
    lines.extend(["", "The complete raw trial table is in rows.md; detailed task and slice summaries are in summary.json.", "",
                  "Wall time covers the runner's agent interval; setup/index preparation is recorded separately by the runner. Judge latency and tokens are not agent cost.",
                  "The rubric score is the unmodified full boolean mean, including upstream rubric grounding defects. Model judgments require human calibration; no non-inferiority claim is made from these descriptive means.", ""])
    return "\n".join(lines)


def render_rows_markdown(rows: list[dict[str, Any]]) -> str:
    columns = ("task_id", "profile", "repetition", "execution_status", "judge_status", "input_tokens", "tool_calls", "wall_seconds", "rubric_score", "judge_latency_seconds")
    lines = ["# Raw planned QA trial observations", "", "N/A remains unscored or unavailable; all planned trials are retained.", "",
             "| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        values = ["N/A" if row.get(key) is None else str(row[key]).replace("|", "\\|").replace("\n", " ") for key in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def write_report(*, runs_dir: Path, output: Path, manifest_path: Path | None = None, repetitions: int | None = None) -> dict[str, Any]:
    rows, plan = load_rows(runs_dir, manifest_path=manifest_path, repetitions=repetitions)
    summary = summarize(rows)
    slices = {row["task_id"]: row["slice"] for row in rows}
    qa_group = lambda row: "code_qa" if row["slice"] == "code_qa" else ("unspecified" if row["slice"] == "unspecified" else "other_readonly_qa")
    report = {"schema_version": 2, "protocol": PROTOCOL, "adapter": "custom-qoder-qa-rubric-adapter-v1", "leaderboard_comparable": False,
        "plan": plan, "summary": summary, "efficacy_claim_ready": summary["complete"] and plan["task_plan_source"] == "manifest",
        "execution_complete": summary["execution"]["execution_complete"] and plan["task_plan_source"] == "manifest",
        "weighting": "equal task weights; paired within task and repetition, per metric",
        "task_slices": slices,
        "by_task": {task: summarize([r for r in rows if r["task_id"] == task]) for task in plan["task_ids"]},
        "by_slice": {name: summarize([r for r in rows if r["slice"] == name]) for name in sorted(set(slices.values()))},
        "by_qa_group": {name: summarize([r for r in rows if qa_group(r) == name]) for name in sorted({qa_group(r) for r in rows})}}
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    (output / "rows.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    columns = ["task_id", "slice", "profile", "repetition", "trial_id", "model", "execution_status", "judge_status", *METRICS,
               "rubrics_passed", "rubric_count", "judge_model", "judge_latency_seconds", "model_identity", "candidate_output_path", "ledger_path", "judgment_path", "observed_metrics", "execution_attempted", "terminal_recorded", "preserved_from_original", "effective_manifest_path", "provenance", "installation", "installation_evidence", "error"]
    with (output / "rows.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()})
    (output / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    (output / "rows.md").write_text(render_rows_markdown(rows), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="directory for summary.json/md and rows.json/csv/md")
    parser.add_argument("--manifest", dest="manifest_path", type=Path)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--require-complete", action="store_true", help="fail after writing if any planned observation or primary metric is missing")
    parser.add_argument("--require-executed", action="store_true", help="require a terminal attempt for every planned trial and a score for every completed answer; preserved failures remain failures")
    args = vars(parser.parse_args(argv))
    require_complete = args.pop("require_complete")
    require_executed = args.pop("require_executed")
    try:
        result = write_report(**args)
    except (ReportError, OSError, ValueError) as exc:
        parser.exit(2, f"report failed: {exc}\n")
    print(f"Report: {result['plan']['expected_trials']} planned trials; complete={result['summary']['complete']}")
    return 1 if ((require_complete and not result["efficacy_claim_ready"])
                 or (require_executed and not result["execution_complete"])) else 0


if __name__ == "__main__":
    raise SystemExit(main())
