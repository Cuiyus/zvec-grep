"""Run one predeclared pair from the fixed current-task repetition study."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path, PurePosixPath
import random

import live
import repeat_inputs_v1 as preparation
import repeat_identity_v1 as identity
from robust_statistics_v1 import TRIM_POLICY, TASK_SCREEN_POLICY
from repeat_analysis_v1 import BOOTSTRAP_SEED, BOOTSTRAP_RESAMPLES

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PREFIX = "benchmarks/native-claude-qa-pilot/"
PROTOCOL = "native-claude-current-tasks-repeat-v1"
BASE_PLAN = PREFIX + "live-plan-v2.json"
BASE_PLAN_SHA256 = "b4e8fb15b6cae82a9083add53ad7c04e67673720f7a50825fde587b7538ad026"
WORKFLOW = ".github/workflows/native-claude-qa-repeat-v1.yml"
SETUP = ".github/actions/native-claude-repeat-setup/action.yml"
DECISION = PREFIX + "repeat-decision-v1.json"
SCOPE_POLICY = PREFIX + "repeat-scope-policy-v1.json"
UPSTREAM_SHA = "33a931e53350c039b824367efe1d44e747399e80"
RUST_TREE = "de02030056a3224012725799d845bf2be9edae2b"
SEED = 20261020
CONTROLS = {PREFIX + name for name in (
    "repeat_live_v1.py", "repeat_inputs_v1.py", "repeat_identity_v1.py", "repeat_analysis_v1.py",
    "robust_statistics_v1.py", "test_repeat_live_v1.py", "test_repeat_analysis_v1.py",
    "test_robust_statistics_pr261.py", "test_robust_statistics_v1.py",
    "grader-contract-v1.json", "expansion-grader-contract-v1.json"
)} | {WORKFLOW, SETUP, DECISION, SCOPE_POLICY}
PLAN_KEYS = {
    "schema_version", "protocol_id", "frozen", "status", "created_at_utc", "frozen_at_utc",
    "base_plan", "current_task_ids", "tasks", "repetitions_per_task_profile", "n_attempts_per_pair",
    "max_retries", "scheduled_pairs", "scheduled_solver_trials", "schedule_seed", "schedule",
    "max_parallel_pairs", "profile_order_balance", "historical_evidence", "user_authorization",
    "runtime_policy", "grading_contracts", "statistics", "stopping", "ci_scope",
    "execution_workflow", "execution_decision", "frozen_harness_sha256", "prepared",
    "scope_policy", "prior_execution_evidence", "upstream_verification",
    "grading_policy",
}


def schedule():
    rows = []
    for task_position, task_id in enumerate(preparation.TASK_IDS):
        for index in range(1, 11):
            profiles = ["baseline", "zvec-grep"] if (task_position + index) % 2 else ["zvec-grep", "baseline"]
            rows.append({"key": f"{task_id}-r{index:02d}", "task_id": task_id,
                         "trial_index": index, "profiles": profiles})
    random.Random(SEED).shuffle(rows)
    return rows


def selected_pair(task_id, repetition):
    found = [row for row in schedule() if row["task_id"] == task_id and row["trial_index"] == repetition]
    if type(repetition) is not int or len(found) != 1:
        raise ValueError("undeclared_task_or_repeat")
    return found[0]


def validate_plan(args, repo_root=REPO):
    base_path = live.pilot.safe_file(repo_root, BASE_PLAN)
    if live.pilot.digest(base_path) != BASE_PLAN_SHA256:
        raise ValueError("frozen_base_plan_changed")
    base, proof = live.validate_plan(base_path, args.main_root, args.reference_prepared, args.candidate, repo_root)
    selected_pair(args.task_id, args.repetition)
    plan = live.pilot.load(args.plan)
    integers = ("schema_version", "repetitions_per_task_profile", "n_attempts_per_pair", "max_retries",
                "scheduled_pairs", "scheduled_solver_trials", "max_parallel_pairs", "schedule_seed")
    if not isinstance(plan, dict) or set(plan) - PLAN_KEYS or any(type(plan.get(key)) is not int for key in integers):
        raise ValueError("repeat_plan_schema_no_runtime_overrides")
    if (plan.get("schema_version") != 1 or plan.get("protocol_id") != PROTOCOL or plan.get("frozen") is not True
            or plan.get("current_task_ids") != list(preparation.TASK_IDS)
            or plan.get("repetitions_per_task_profile") != 10 or plan.get("n_attempts_per_pair") != 1
            or plan.get("max_retries") != 0 or plan.get("scheduled_pairs") != 30
            or plan.get("scheduled_solver_trials") != 60 or plan.get("max_parallel_pairs") != 3
            or plan.get("schedule_seed") != SEED or plan.get("schedule") != schedule()):
        raise ValueError("repeat_schedule_or_freeze_mismatch")
    if plan.get("base_plan") != {"path": BASE_PLAN, "sha256": BASE_PLAN_SHA256}:
        raise ValueError("repeat_base_reference_mismatch")
    if plan.get("prepared") != preparation.references(args.prepared_group, args.reference_prepared, repo_root):
        raise ValueError("repeat_prepared_reference_mismatch")
    tasks = plan.get("tasks", [])
    if [task.get("id") for task in tasks] != list(preparation.TASK_IDS):
        raise ValueError("repeat_task_set_mismatch")
    for task in tasks:
        metadata, _, _ = preparation.selected_inputs(args.prepared_group, args.reference_prepared, task["id"], repo_root)
        if any(task.get(key) != metadata.get(key) for key in ("source_commit", "source_file_count", "source_inventory_sha256")):
            raise ValueError("repeat_source_identity_mismatch")
        for field, stored in (("instruction", "question"), ("evaluator", "rubric")):
            actual = metadata[stored]
            if task.get(field) != actual["path"] or task.get(field + "_sha256") != actual["sha256"]:
                raise ValueError("repeat_question_or_rubric_mismatch")
            path = repo_root / "benchmarks/zvec-issue-qa" / actual["path"]
            if live.pilot.digest(path) != actual["sha256"]:
                raise ValueError("repeat_grading_asset_changed")
    statistics = plan.get("statistics", {})
    if (statistics.get("trim_policy") != TRIM_POLICY or statistics.get("task_screen_policy") != TASK_SCREEN_POLICY
            or statistics.get("bootstrap") != {"seed": BOOTSTRAP_SEED, "resamples": BOOTSTRAP_RESAMPLES,
                "confidence_level": 0.95, "unit": "original within-task baseline/zg pair", "task_resampling": False}):
        raise ValueError("repeat_statistics_not_frozen")
    if plan.get("execution_workflow") != WORKFLOW or plan.get("execution_decision") != DECISION:
        raise ValueError("repeat_control_path_mismatch")
    pins = plan.get("frozen_harness_sha256")
    if not isinstance(pins, dict) or not CONTROLS <= pins.keys():
        raise ValueError("repeat_control_pins_missing")
    for relative, expected in pins.items():
        if live.pilot.digest(live.pilot.safe_file(repo_root, relative)) != expected:
            raise ValueError("repeat_control_changed:" + relative)
    if plan.get("scope_policy") != {"path": SCOPE_POLICY, "sha256": pins[SCOPE_POLICY]}:
        raise ValueError("repeat_scope_policy_not_frozen")
    upstream = plan.get("upstream_verification", {})
    if upstream.get("current_main") != UPSTREAM_SHA or upstream.get("rust_tree_sha") != RUST_TREE:
        raise ValueError("repeat_current_rust_identity_mismatch")
    for record in plan.get("prior_execution_evidence", []):
        if not isinstance(record, dict) or record.get("sha256") != pins.get(record.get("path")):
            raise ValueError("repeat_prior_evidence_not_pinned")
    contracts = plan.get("grading_contracts", [])
    expected_contracts = [PREFIX + "grader-contract-v1.json", PREFIX + "expansion-grader-contract-v1.json"]
    if ([record.get("path") for record in contracts] != expected_contracts
            or any(record.get("sha256") != pins[record["path"]] for record in contracts)):
        raise ValueError("repeat_original_grading_contracts_mismatch")
    decision = live.pilot.load(repo_root / DECISION)
    if (decision.get("protocol_id") != PROTOCOL or decision.get("frozen") is not True
            or decision.get("paid_execution_authorized") is not True
            or decision.get("status") != "approved_for_fixed_current_task_repetitions"
            or decision.get("task_ids") != list(preparation.TASK_IDS)
            or decision.get("scheduled_pairs") != 30 or decision.get("scheduled_solver_trials") != 60):
        raise ValueError("repeat_execution_decision_missing")
    return plan, base, proof


def private_live(pair):
    spec = importlib.util.spec_from_file_location("_native_claude_repeat_" + pair["key"], HERE / "live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROTOCOL, module.PROFILES = PROTOCOL, pair["profiles"]
    return module


def context_path(output):
    return output.with_name(output.name + "-repeat-context.json")


def context_record(args, plan, pair, prepared_root, dry_run):
    root = PurePosixPath(prepared_root)
    if not root.is_absolute() or ".." in root.parts or str(root) != prepared_root:
        raise ValueError("invalid_recorded_prepared_root")
    return {"schema_version": 1, "protocol_id": PROTOCOL, **pair,
            "plan_sha256": live.pilot.digest(args.plan), "prepared": plan["prepared"],
            "recorded_prepared_root": prepared_root, "dry_run": dry_run}


def execute(args, plan, base, proof, repo_root=REPO):
    pair = selected_pair(args.task_id, args.repetition)
    module = private_live(pair)
    native_args = copy.copy(args)
    native_args.prepared = args.reference_prepared if args.task_id == "q001" else args.prepared_group / args.task_id
    sidecar = context_path(args.output)
    if args.command == "collect":
        if sidecar.is_symlink() or not sidecar.is_file():
            raise ValueError("repeat_context_missing")
        saved = live.pilot.load(sidecar)
        if not isinstance(saved, dict) or saved != context_record(args, plan, pair, saved.get("recorded_prepared_root", ""), False):
            raise ValueError("repeat_context_mismatch")
        destination = args.output / "task-identity.json"
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("repeat_identity_output_exists")
        args.output.mkdir(parents=True, exist_ok=True)
        collected, error = None, None
        try:
            collected = module.collect(native_args, base, proof)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            error = type(exc).__name__
        result = identity.verify(args.prepared_group, args.task_id, args.output,
            reference_prepared=args.reference_prepared,
            recorded_prepared_root=saved["recorded_prepared_root"], expected_profiles=pair["profiles"], repo_root=repo_root)
        result.update(native_collection_completed=collected is not None, native_collection_error_type=error,
                      native_structural_valid=bool(collected and collected.get("structural_valid") is True),
                      context_sha256=live.pilot.digest(sidecar), pair_key=pair["key"], trial_index=args.repetition)
        result["joint_gate_valid"] = result["native_structural_valid"] and result["task_identity_valid"]
        with destination.open("x") as stream:
            json.dump(result, stream, indent=2, sort_keys=True); stream.write("\n")
        return 0 if result["joint_gate_valid"] else 1
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError("repeat_output_exists")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open("x") as stream:
        json.dump(context_record(args, plan, pair, native_args.prepared.resolve().as_posix(), args.command == "dry-run"),
                  stream, indent=2, sort_keys=True); stream.write("\n")
    record = module.run_pair(native_args, base, dry_run=args.command == "dry-run")
    return 0 if args.command == "dry-run" or all(
        slot.get("status") == "exited" and slot.get("returncode") == 0 and module.cleanup_complete(slot)
        for slot in record["slots"]) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "main-root", "reference-prepared", "prepared-group", "candidate", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--task", dest="task_id", choices=preparation.TASK_IDS, required=True)
    parser.add_argument("--repetition", type=int, required=True)
    parser.add_argument("command", choices=("dry-run", "run", "collect"))
    args = parser.parse_args(argv)
    for name in ("plan", "main_root", "reference_prepared", "prepared_group", "candidate", "output"):
        setattr(args, name, getattr(args, name).resolve())
    plan, base, proof = validate_plan(args)
    return execute(args, plan, base, proof)


if __name__ == "__main__":
    raise SystemExit(main())
