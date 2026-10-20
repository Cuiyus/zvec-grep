"""Execute only the user-requested ten fresh zvec q001 pairs.

The outer plan defines this cohort. The unchanged inner plan is a runtime and
identity reference, not authorization to run its cancelled thirty-pair cohort.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import repeat_live_v1 as inner

HERE = Path(__file__).resolve().parent
REPO = inner.REPO
PREFIX = inner.PREFIX
PROTOCOL = "native-claude-zvec-q001-repeat-v1"
INNER_PLAN = PREFIX + "repeat-plan-v1.json"
INNER_SHA = "dd3493b0a8b1253fe20cbbd7d6dd2256e4ea0ba7ca4d637c8ba0296dbbce2898"
WORKFLOW = ".github/workflows/native-claude-q001-repeat-v1.yml"
DECISION = PREFIX + "q001-repeat-decision-v1.json"
CONTROLS = {WORKFLOW, DECISION, INNER_PLAN} | {PREFIX + name for name in (
    "q001_repeat_v1.py", "q001_repeat_analysis_v1.py", "test_q001_repeat_v1.py")}


def schedule():
    return [row for row in inner.schedule() if row["task_id"] == "q001"]


def inner_args(args, repo_root=REPO):
    result = copy.copy(args)
    result.plan = repo_root / INNER_PLAN
    result.task_id = "q001"
    return result


def validate_plan(args, repo_root=REPO):
    if inner.live.pilot.digest(repo_root / INNER_PLAN) != INNER_SHA:
        raise ValueError("q001_inner_runtime_reference_changed")
    if getattr(args, "task_id", "q001") != "q001":
        raise ValueError("q001_only")
    inner.selected_pair("q001", args.repetition)
    plan = inner.live.pilot.load(args.plan)
    required = {"schema_version": 1, "protocol_id": PROTOCOL, "frozen": True,
        "task_ids": ["q001"], "repetitions_per_profile": 10, "scheduled_pairs": 10,
        "scheduled_solver_trials": 20, "max_parallel_pairs": 5, "max_retries": 0,
        "n_attempts_per_pair": 1, "schedule": schedule(),
        "runtime_reference": {"path": INNER_PLAN, "sha256": INNER_SHA},
        "execution_workflow": WORKFLOW, "execution_decision": DECISION}
    if not isinstance(plan, dict) or any(type(plan.get(k)) is not type(v) or plan[k] != v
                                         for k, v in required.items()):
        raise ValueError("q001_frozen_cohort_mismatch")
    if set(plan) != set(required) | {"created_at_utc", "frozen_harness_sha256", "statistics",
            "grading_policy", "historical_evidence", "user_authorization", "stopping"}:
        raise ValueError("q001_no_runtime_overrides")
    pins = plan.get("frozen_harness_sha256")
    if not isinstance(pins, dict) or not CONTROLS <= pins.keys():
        raise ValueError("q001_control_pins_missing")
    for path, digest in pins.items():
        if inner.live.pilot.digest(inner.live.pilot.safe_file(repo_root, path)) != digest:
            raise ValueError("q001_control_changed:" + path)
    decision = inner.live.pilot.load(repo_root / DECISION)
    if any(type(decision.get(k)) is not type(v) or decision[k] != v
           for k, v in required.items() if k not in ("schedule", "execution_decision")):
        raise ValueError("q001_decision_cohort_mismatch")
    if decision.get("paid_execution_authorized") is not True:
        raise ValueError("q001_execution_not_authorized")
    if any(decision.get(k) != plan[k] for k in ("statistics", "grading_policy", "stopping")):
        raise ValueError("q001_decision_policy_mismatch")
    reference, base, proof = inner.validate_plan(inner_args(args, repo_root), repo_root)
    for key in ("trim_policy", "task_screen_policy", "bootstrap"):
        if plan["statistics"].get(key) != reference["statistics"][key]:
            raise ValueError("q001_original_statistics_changed")
    return plan, reference, base, proof


def context_path(output):
    return output.with_name(output.name + "-q001-repeat-context.json")


def context_record(args):
    return {"schema_version": 1, "protocol_id": PROTOCOL,
        **inner.selected_pair("q001", args.repetition),
        "cohort_plan_sha256": inner.live.pilot.digest(args.plan),
        "inner_runtime_plan_sha256": INNER_SHA, "scheduled_pairs": 10,
        "scheduled_solver_trials": 20, "dry_run": args.command == "dry-run"}


def execute(args, plan, reference, base, proof, repo_root=REPO):
    path = context_path(args.output)
    expected = context_record(args)
    if args.command == "collect":
        if path.is_symlink() or not path.is_file() or inner.live.pilot.load(path) != expected:
            raise ValueError("q001_cohort_context_missing_or_mismatched")
    else:
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError("q001_output_exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            json.dump(expected, stream, indent=2, sort_keys=True)
            stream.write("\n")
    return inner.execute(inner_args(args, repo_root), reference, base, proof, repo_root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "main-root", "reference-prepared", "prepared-group", "candidate", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--repetition", type=int, required=True)
    parser.add_argument("command", choices=("dry-run", "run", "collect"))
    args = parser.parse_args(argv)
    for name in ("plan", "main_root", "reference_prepared", "prepared_group", "candidate", "output"):
        setattr(args, name, getattr(args, name).resolve())
    return execute(args, *validate_plan(args))


if __name__ == "__main__":
    raise SystemExit(main())
