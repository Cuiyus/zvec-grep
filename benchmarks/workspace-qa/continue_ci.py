#!/usr/bin/env python3
"""Plan and assemble one pinned native QA continuation from local artifacts."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import shutil

import continuation
from failure_audit import TERMINAL, COMPLETED
from judge import validate_judgement_continuation
import runner


def positive_id(value, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError("Invalid " + label)
    return str(value)


def load_config(path: Path) -> dict:
    config = continuation.read_object(path)
    for key in ("source_run_id", "source_run_attempt"):
        config[key] = positive_id(config.get(key), key)
    if (config.get("protocol") != continuation.PROTOCOL
            or not re.fullmatch(r"[0-9a-f]{40}", str(config.get("source_commit", "")))
            or not isinstance(config.get("branch"), str) or not config["branch"]
            or config.get("workflow_path") != ".github/workflows/workspace-qa-qoder.yml"):
        raise ValueError("Invalid pinned native continuation source configuration")
    tasks = config.get("task_ids")
    if not isinstance(tasks, list):
        raise ValueError("Source configuration needs all ten task IDs")
    config["task_ids"] = [positive_id(t, "task ID") for t in tasks]
    locked = continuation.read_object(continuation.LOCK_PATH)
    if (len(tasks) != 10 or len(set(config["task_ids"])) != 10
            or set(config["task_ids"]) != {t["task_id"] for t in locked["tasks"]}):
        raise ValueError("Continuation source configuration must cover all ten frozen tasks")
    return config


def validate_source_run(document: dict, config: dict) -> None:
    expected = {"status": "completed", "head_sha": config["source_commit"],
                "head_branch": config["branch"], "path": config["workflow_path"]}
    for key, value in expected.items():
        if document.get(key) != value:
            raise ValueError("Original GitHub run identity mismatch: " + key)
    for source, target in (("id", "source_run_id"), ("run_attempt", "source_run_attempt")):
        if positive_id(document.get(source), source) != config[target]:
            raise ValueError("Original GitHub run identity mismatch: " + source)


def artifact_map(root: Path, config: dict, *, continued: bool) -> dict[str, Path]:
    if not root.exists() and continued:
        return {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Artifact collection must be a real directory")
    prefix = "workspace-qa-continuation-batch-" if continued else "workspace-qa-batch-"
    pattern = re.compile(re.escape(prefix) + r"([1-9][0-9]*)-([1-9][0-9]*)-([1-9][0-9]*)")
    result = {}
    for path in sorted(root.iterdir()):
        match = pattern.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_dir():
            raise ValueError("Unexpected artifact entry: " + path.name)
        task_id, run_id, attempt = match.groups()
        if task_id not in config["task_ids"]:
            raise ValueError("Artifact contains an unselected task: " + task_id)
        if not continued and (run_id != config["source_run_id"] or attempt != config["source_run_attempt"]):
            raise ValueError("Original artifact belongs to a different run or run attempt")
        if task_id in result:
            raise ValueError("Multiple artifacts for task " + task_id + "; refusing to choose an attempt")
        result[task_id] = path
    if not continued:
        missing = [t for t in config["task_ids"] if t not in result]
        if missing:
            raise ValueError("Missing original batch artifacts for tasks: " + ", ".join(missing))
    return result


def require_unambiguous(ledger: dict) -> None:
    ambiguous = [row["trial_id"] for row in ledger["trials"]
                 if row.get("status") not in TERMINAL and not continuation.is_unstarted(row)]
    if ambiguous:
        raise ValueError("Running or ambiguous trial evidence cannot be resumed: " + ", ".join(ambiguous))


def load_originals(root: Path, config: dict) -> dict[str, dict]:
    result = {}
    for task_id, artifact in artifact_map(root, config, continued=False).items():
        try:
            expected_plan = runner.make_plan(task_id, 10, 1729)
            if (artifact / "runs/manifest.json").is_file():
                bundle = continuation.load_prior(artifact, expected_plan)
                ci = bundle["manifest"].get("ci_identity", {})
                if (str(ci.get("GITHUB_RUN_ID")) != config["source_run_id"]
                        or ci.get("GITHUB_SHA") != config["source_commit"]
                        or str(ci.get("GITHUB_RUN_ATTEMPT")) != config["source_run_attempt"]):
                    raise ValueError("Original artifact manifest does not match the pinned source run")
            else:
                bundle = continuation.load_setup_prior(artifact, expected_plan)
            require_unambiguous(bundle["ledger"])
            if bundle.get("manifest", {}).get("continuation") is not None:
                raise ValueError("Expected the original full-run artifact, not a prior continuation")
            result[task_id] = bundle
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f"Task {task_id} original artifact is incomplete or invalid: {error}") from error
    return result


def plan(artifacts: Path, source_run_json: Path, source_config: Path) -> dict:
    config = load_config(source_config)
    validate_source_run(continuation.read_object(source_run_json), config)
    bundles = load_originals(artifacts, config)
    records = []
    for task_id in config["task_ids"]:
        bundle = bundles[task_id]
        records.append({"task_id": task_id, "artifact_name": bundle["root"].name,
            "mode": bundle["kind"],
            "preserved_trial_ids": bundle["preserved_trial_ids"], "pending_trial_ids": bundle["pending_trial_ids"],
            "prior_ledger_sha256": bundle["files_sha256"]["runs/trial-results.json"],
            "prior_manifest_sha256": bundle["files_sha256"].get("runs/manifest.json"),
            "prior_judgements_sha256": bundle["files_sha256"].get("runs/judgements.json")})
    pending = [{"task": r["task_id"], "mode": r["mode"]} for r in records if r["pending_trial_ids"]]
    return {"schema_version": 1, "protocol": config["protocol"],
        "source_run_id": config["source_run_id"], "source_run_attempt": config["source_run_attempt"],
        "source_commit": config["source_commit"], "branch": config["branch"],
        "matrix": {"include": pending}, "has_pending": bool(pending), "tasks": records,
        "counts": {"tasks": 10, "planned_trials": 200, "pending_tasks": len(pending),
            "attempted_trials": sum(len(r["preserved_trial_ids"]) for r in records),
            "pending_trials": sum(len(r["pending_trial_ids"]) for r in records)},
        "policy": "Only original planned trials without execution evidence are eligible; no failed or ambiguous trial is resampled."}


def validate_merged(artifact: Path, bundle: dict, config: dict) -> dict[str, str]:
    files = continuation.file_hashes(artifact)
    runs = artifact / "runs"
    if bundle.get("kind") == "setup_only":
        proof = continuation.validate_setup_continuation_evidence(runs, bundle)
        if (str(proof.get("source_run_id")) != config["source_run_id"]
                or str(proof.get("source_run_attempt")) != config["source_run_attempt"]
                or proof.get("source_commit") != config["source_commit"]
                or proof.get("original_artifact_name") != bundle["root"].name):
            raise ValueError("Setup-only continuation refers to a different source run")
        if continuation.read_object(artifact / "selection.json") != bundle["selection"]:
            raise ValueError("Setup-only continuation changed the task selection")
        ledger = continuation.read_object(runs / "trial-results.json")
        require_unambiguous(ledger)
        if any(continuation.is_unstarted(row) for row in ledger["trials"]):
            raise ValueError("Setup-only continuation did not attempt every original QA slot")
        transition = continuation.validate_transition(bundle["ledger"], ledger)
        if transition["preserved_trial_ids"] or transition["pending_trial_ids"] != bundle["pending_trial_ids"]:
            raise ValueError("Setup-only continuation changed the original all-unstarted classification")
        current_plan = continuation.read_object(runs / "plan.json")
        expected = copy.deepcopy(bundle["plan"])
        for planned, row in zip(expected["trials"], ledger["trials"], strict=True):
            planned["status"] = row["status"]
        if current_plan != expected:
            raise ValueError("Setup-only continuation changed its complete plan or order")
        judgements = continuation.read_object(runs / "judgements.json")
        if judgements.get("trial_results_sha256") != continuation.digest(runs / "trial-results.json"):
            raise ValueError("Setup-only judgements refer to a different ledger")
        manifest = continuation.read_object(runs / "manifest.json")
        suffix = artifact.name.rsplit("-", 2)[1:]
        ci = manifest.get("ci_identity", {})
        if [str(ci.get("GITHUB_RUN_ID")), str(ci.get("GITHUB_RUN_ATTEMPT"))] != suffix:
            raise ValueError("Continued artifact name and CI identity disagree")
        return files
    proof = continuation.validate_continuation_evidence(runs)
    for name, relative in (("ledger", "runs/trial-results.json"), ("manifest", "runs/manifest.json"),
                           ("judgements", "runs/judgements.json")):
        if proof.get(f"prior_{name}_sha256") != bundle["files_sha256"][relative]:
            raise ValueError("Continued artifact is bound to different original " + name + " evidence")
    if (str(proof.get("source_run_id")) != config["source_run_id"] or proof.get("source_commit") != config["source_commit"]
            or proof.get("preserved_trial_ids") != bundle["preserved_trial_ids"]
            or proof.get("pending_trial_ids") != bundle["pending_trial_ids"]):
        raise ValueError("Continued artifact refers to a different original run or trial subset")
    if continuation.read_object(artifact / "selection.json") != bundle["selection"]:
        raise ValueError("Continued artifact changed the task selection")
    ledger = continuation.read_object(runs / "trial-results.json")
    require_unambiguous(ledger)
    current_plan = continuation.read_object(runs / "plan.json")
    expected = copy.deepcopy(bundle["plan"])
    for planned, row in zip(expected["trials"], ledger["trials"], strict=True):
        planned["status"] = row["status"]
    if current_plan != expected:
        raise ValueError("Continued artifact changed its complete plan or recorded order")
    manifest = continuation.read_object(runs / "manifest.json")
    suffix = artifact.name.rsplit("-", 2)[1:]
    ci = manifest.get("ci_identity", {})
    if [str(ci.get("GITHUB_RUN_ID")), str(ci.get("GITHUB_RUN_ATTEMPT"))] != suffix:
        raise ValueError("Continued artifact name and CI identity disagree")
    judgements = continuation.read_object(runs / "judgements.json")
    if not isinstance(judgements.get("judgement_continuation"), dict):
        raise ValueError("Continued artifact lacks preserved judgement provenance")
    if validate_judgement_continuation(judgements, runs) != set(bundle["preserved_trial_ids"]):
        raise ValueError("Continued artifact did not preserve all original judgement rows")
    return files


def assemble(original: Path, continued: Path, output: Path, source_config: Path) -> dict:
    config = load_config(source_config)
    bundles = load_originals(original, config)
    continuations = artifact_map(continued, config, continued=True)
    pending_tasks = {task_id for task_id, bundle in bundles.items() if bundle["pending_trial_ids"]}
    if set(continuations) != pending_tasks:
        missing = sorted(pending_tasks - set(continuations), key=int)
        unexpected = sorted(set(continuations) - pending_tasks, key=int)
        raise ValueError("Continued artifact task set differs; missing=" + ",".join(missing)
                         + "; unexpected=" + ",".join(unexpected))
    selected = []
    for task_id in config["task_ids"]:
        bundle = bundles[task_id]
        artifact = continuations.get(task_id, bundle["root"])
        hashes = validate_merged(artifact, bundle, config) if task_id in continuations else bundle["files_sha256"]
        selected.append((task_id, artifact, hashes))
    if (output.is_symlink() or any(output.resolve().is_relative_to(r.resolve()) or r.resolve().is_relative_to(output.resolve())
                                  for r in (original, continued))
            or output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ValueError("Assembly output must be a separate new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    records, pending_count, attempted_count, completed_count = [], 0, 0, 0
    for task_id, artifact, hashes in selected:
        if continuation.file_hashes(artifact) != hashes:
            raise ValueError("Selected artifact changed after validation")
        destination = output / ("task-" + task_id)
        shutil.copytree(artifact, destination)
        if continuation.file_hashes(destination) != hashes:
            raise ValueError("Selected artifact bytes changed during assembly")
        rows = continuation.read_object(destination / "runs/trial-results.json")["trials"]
        pending_count += sum(continuation.is_unstarted(row) for row in rows)
        attempted_count += sum(not continuation.is_unstarted(row) for row in rows)
        completed_count += sum(row["status"] in COMPLETED for row in rows)
        records.append({"task_id": task_id, "artifact_name": artifact.name, "directory": destination.name,
                        "kind": "continuation" if task_id in continuations else "original",
                        "ledger_sha256": hashes["runs/trial-results.json"]})
    report = {"schema_version": 1, "protocol": config["protocol"], "source_run_id": config["source_run_id"],
        "source_run_attempt": config["source_run_attempt"], "source_commit": config["source_commit"],
        "tasks": records, "counts": {"tasks": 10, "planned_trials": 200, "attempted_trials": attempted_count,
            "pending_trials": pending_count, "qa_completed": completed_count},
        "exactly_one_artifact_per_task": True, "no_resampling": True}
    runner.write_json(output / "assembly.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    planning = subparsers.add_parser("plan")
    planning.add_argument("--artifacts", type=Path, required=True)
    planning.add_argument("--source-run-json", type=Path, required=True)
    planning.add_argument("--source-config", type=Path, required=True)
    planning.add_argument("--output", type=Path, required=True)
    assembly = subparsers.add_parser("assemble")
    assembly.add_argument("--original", type=Path, required=True)
    assembly.add_argument("--continued", type=Path, required=True)
    assembly.add_argument("--output", type=Path, required=True)
    assembly.add_argument("--source-config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            report = plan(args.artifacts, args.source_run_json, args.source_config)
            runner.write_json(args.output, report)
        else:
            report = assemble(args.original, args.continued, args.output, args.source_config)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, "Continuation orchestration refused incomplete or conflicting evidence: " + runner.redact(str(error)) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
