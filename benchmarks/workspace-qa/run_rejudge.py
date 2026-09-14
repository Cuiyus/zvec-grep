#!/usr/bin/env python3
"""Recover incomplete judging of one pinned smoke artifact, without new QA runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time
from urllib.parse import quote

import dataset
import report
import run_task
import runner

HERE = Path(__file__).resolve().parent
RECOVERY_PATH = HERE / "data/judge-recovery.json"
LOCK_PATH = HERE / "data/lock.json"


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object: " + path.name)
    return value


def relative_path(name: str) -> Path:
    if (not isinstance(name, str) or not name or name == "."
            or name != PurePosixPath(name).as_posix() or any(ord(c) < 32 for c in name)):
        raise ValueError("Artifact path must be a canonical relative file path")
    return dataset.safe_relative(name)


def verify_artifact(root: Path, recovery: dict) -> dict[str, str]:
    files = recovery.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Recovery requires the complete extracted artifact manifest")
    directories = set()
    for name, digest in files.items():
        path = relative_path(name)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid artifact SHA-256")
        directories.update(parent.as_posix() for parent in path.parents if parent != Path("."))
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Artifact root must be a real directory")
    observed = set()
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("Artifact symlinks are forbidden")
            if stat.S_ISDIR(mode):
                if relative not in directories:
                    raise ValueError("Unexpected artifact directory")
            elif stat.S_ISREG(mode):
                observed.add(relative)
            else:
                raise ValueError("Artifact contains a non-regular file")
    if observed != set(files):
        raise ValueError("Artifact has missing or additional files")
    for name, expected in files.items():
        if dataset.digest(root / relative_path(name)) != expected:
            raise ValueError("Artifact SHA-256 mismatch: " + name)
    for name, field in (("runs/trial-results.json", "source_trial_results_sha256"),
                        ("runs/judgements.json", "source_judgements_sha256")):
        if files.get(name) != recovery.get(field):
            raise ValueError("Recovery evidence hashes disagree")
    return files


def download_judge_evidence(lock: dict, task: dict, destination: Path) -> None:
    """Download only the original rubric-associated files, never a persona archive."""
    frozen = lock["dataset"]
    base = (f"https://huggingface.co/datasets/{frozen['repo']}/resolve/"
            f"{frozen['revision']}/task_lite_clean_cn/3/")
    metadata_path = destination / "metadata.json"
    dataset.download(base + "metadata.json", metadata_path, task["metadata_sha256"])
    if dataset.digest(metadata_path) != task["metadata_sha256"]:
        raise ValueError("Original metadata checksum mismatch")
    metadata = read_object(metadata_path)
    if metadata.get("output_files") != [task["answer_filename"]]:
        raise ValueError("Original output filename differs from lock")
    locked = {item["stored_relpath"]: item for item in task["inputs"]}
    sources = metadata.get("data_manifest", [])
    if len(locked) != len(task["inputs"]) or len(sources) != len(locked):
        raise ValueError("Original source manifest differs from lock")
    if {item.get("stored_relpath") for item in sources} != set(locked):
        raise ValueError("Original source paths differ from lock")
    for item in sources:
        expected = locked[item["stored_relpath"]]
        if item.get("sha256") not in (None, expected["sha256"]):
            raise ValueError("Original source hash differs from lock")
        path = destination / relative_path(item["stored_relpath"])
        dataset.download(base + quote(item["stored_relpath"]), path, expected["sha256"])
        if dataset.digest(path) != expected["sha256"] or path.stat().st_size != expected["size_bytes"]:
            raise ValueError("Original source content differs from lock")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.artifact_root.is_symlink() or args.output.is_symlink():
        raise ValueError("Input and output directories cannot be symlinks")
    source, output = args.artifact_root.resolve(), args.output.resolve()
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("Recovery output must be separate from original artifact")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Recovery output must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    provenance = {"schema_version": 1, "phase": "judge_recovery", "status": "failed",
                  "no_new_qa_trials": True, "new_qa_trials": 0,
                  "ci_identity": {"run_id": os.environ.get("GITHUB_RUN_ID"),
                                  "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                                  "commit": os.environ.get("GITHUB_SHA")},
                  "artifact_zip_digest_verified_locally": False,
                  "evidence_verification": "exact extracted file manifest SHA-256"}
    recovery, files, copied = {}, {}, False
    try:
        recovery, lock = read_object(RECOVERY_PATH), read_object(LOCK_PATH)
        if recovery.get("task_id") != "3" or recovery.get("phase") != "smoke":
            raise ValueError("This recovery is restricted to the pinned task 3 smoke")
        provenance.update({key: recovery[key] for key in (
            "source_run_id", "source_commit", "artifact_id", "artifact_name", "artifact_sha256",
            "task_id", "source_trial_results_sha256", "source_judgements_sha256")})
        files = verify_artifact(source, recovery)
        provenance["verified_file_manifest_sha256"] = hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for name in files:
            destination = output / relative_path(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative_path(name), destination)
            if dataset.digest(destination) != files[name]:
                raise ValueError("Artifact changed while copying: " + name)
        copied = True
        for name in files:
            if name == "runs/judgements.json" or name == "smoke_validation.json" or name.startswith("report/"):
                relative = "judgements.json" if name == "runs/judgements.json" else name
                original = output / "rejudge-original" / relative_path(relative)
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(output / relative_path(name), original)
        task = next(task for task in lock["tasks"] if task["task_id"] == "3")
        selection = read_object(output / "selection.json")
        if (selection.get("repetitions") != 1 or selection.get("tasks") != [task]
                or selection.get("dataset") != lock["dataset"]):
            raise ValueError("Original selection differs from frozen task 3 smoke")
        ledger = read_object(output / "runs/trial-results.json")
        if ledger.get("task_id") != "3" or ledger.get("repetitions_per_profile") != 1:
            raise ValueError("Original ledger is not the pinned task 3 smoke")
        if not os.environ.get("GLM_API_KEY", "").strip():
            raise ValueError("GLM_API_KEY is required for judge recovery")
        evidence = output / "judge-evidence/tasks/3"
        download_judge_evidence(lock, task, evidence)
        judged = subprocess.run([sys.executable, str(HERE / "judge.py"), "--metadata",
            str(evidence / "metadata.json"), "--task-dir", str(evidence), "--runs-dir",
            str(output / "runs"), "--resume"], cwd=HERE, check=False)
        provenance["judge_exit_code"] = judged.returncode
        if judged.returncode != 0:
            raise RuntimeError("Resumed judge did not score every original trial")
        provenance["status"] = "completed"
    except Exception as error:
        provenance.update(status="failed", error_type=type(error).__name__, error=runner.redact(str(error)))
    finally:
        if copied:
            try:
                verify_artifact(source, recovery)
                # Judge/report outputs may change; every original QA byte must stay fixed.
                for name, expected in files.items():
                    if name == "runs/judgements.json" or name == "smoke_validation.json" or name.startswith("report/"):
                        continue
                    if dataset.digest(output / relative_path(name)) != expected:
                        raise ValueError("Recovery modified original QA evidence: " + name)
                provenance["trial_results_sha256"] = dataset.digest(output / "runs/trial-results.json")
                provenance["original_qa_evidence_unchanged"] = True
                validation = run_task.smoke_validation(output / "runs", "smoke")
                runner.write_json(output / "smoke_validation.json", validation)
                summary = report.write_report(runs_dir=output / "runs", output=output / "report",
                                              manifest_path=output / "selection.json")
                run_task.annotate_smoke_report(output / "report", validation)
                provenance["report_complete"] = summary["summary"]["complete"]
                provenance["smoke_validation"] = validation["status"]
                if not provenance["report_complete"] or validation["status"] != "valid":
                    provenance["status"] = "failed"
            except Exception as error:
                provenance.update(status="failed", finalization_error_type=type(error).__name__,
                                  finalization_error=runner.redact(str(error)))
        provenance["wall_seconds"] = round(time.monotonic() - started, 3)
        runner.write_json(output / "rejudge-provenance.json", provenance)
        print(json.dumps(provenance, ensure_ascii=False), flush=True)
    return 0 if provenance["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
