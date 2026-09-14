#!/usr/bin/env python3
"""Revalidate a pinned completed smoke as data, without any model or agent call."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import time

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
RECORD_PATH = HERE / "data/smoke-validation.json"

# Keep an explicit complete list: a record cannot opt out of a changed dependency.
# Workflow orchestration, probe/recovery entry points, docs and tests are excluded.
EVALUATION_FILES = (
    "benchmarks/workspace-qa/runner.py",
    "benchmarks/workspace-qa/dataset.py",
    "benchmarks/workspace-qa/judge.py",
    "benchmarks/workspace-qa/report.py",
    "benchmarks/workspace-qa/run_task.py",
    "benchmarks/workspace-qa/qoder_probe.py",
    "benchmarks/workspace-qa/seed_cache.py",
    "benchmarks/workspace-qa/data/lock.json",
    "benchmarks/workspace-qa/data/selection.json",
    "benchmarks/swe-qa-bench/runtime/Dockerfile",
    "benchmarks/swe-qa-bench/runtime/package.json",
    "benchmarks/swe-qa-bench/runtime/package-lock.json",
    "benchmarks/swe-qa-bench/scripts/readonly-search.mjs",
    "benchmarks/swe-qa-bench/scripts/replay-search.mjs",
    "benchmarks/swe-qa-bench/scripts/prepare-index.mjs",
    "benchmarks/swe-qa-bench/scripts/embedding-probe.mjs",
    "benchmarks/swe-qa-bench/scripts/qa-session.py",
    "benchmarks/swe-qa-bench/pyproject.toml",
    "benchmarks/swe-qa-bench/uv.lock",
    "benchmarks/swe-qa-bench/zg_bench/__init__.py",
    "benchmarks/swe-qa-bench/zg_bench/settings.py",
    "benchmarks/swe-qa-bench/zg_bench/swe_qa/__init__.py",
    "benchmarks/swe-qa-bench/zg_bench/swe_qa/readonly_agents.py",
    "benchmarks/swe-qa-bench/zg_bench/swe_qa/readonly_run.py",
    "benchmarks/swe-qa-bench/zg_bench/swe_qa/readonly_judge.py",
    "benchmarks/swe-qa-bench/zg_bench/swe_qa/e2e_analysis.py",
    "benchmarks/swe-qa-bench/zg_bench/swe_qa/observability.py",
    "benchmarks/swe-qa-bench/zg_bench/swe_qa/judge.py",
    "benchmarks/swe-qa-bench/zg_bench/agents/__init__.py",
    "benchmarks/swe-qa-bench/zg_bench/agents/qodercli.py",
    "benchmarks/swe-qa-bench/zg_bench/agents/opencode.py",
)
EVIDENCE_FILES = (
    "runs/trial-results.json", "runs/judgements.json", "smoke_validation.json",
    "report/summary.json", "rejudge-provenance.json",
)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object: " + path.name)
    return value


def relative_path(name: str) -> Path:
    if (not isinstance(name, str) or not name or name != PurePosixPath(name).as_posix()
            or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
            or name == "." or "\\" in name or any(ord(c) < 32 for c in name)):
        raise ValueError("Expected canonical relative artifact path")
    return Path(name)


def hash_mapping(value, expected: tuple[str, ...], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(label + " must contain exactly the required file list")
    if any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item) for item in value.values()):
        raise ValueError(label + " contains an invalid SHA-256")
    return value


def compatibility(record_path: Path | None = None, repository: Path | None = None) -> dict:
    """Missing or stale compatibility data is a normal miss, never a model call."""
    result = {"reuse_eligible": False, "recovery_run_id": "", "artifact_name": ""}
    try:
        record = read_object(record_path or RECORD_PATH)
        if (record.get("phase") != "smoke" or record.get("qa_trials") != 2
                or record.get("pipeline_validation_complete") is not True
                or record.get("included_in_formal_benchmark") is not False
                or record.get("efficacy_claim_ready") is not False):
            raise ValueError("Record must describe a completed two-trial integration smoke")
        run_id, name = record.get("recovery_run_id"), record.get("recovery_artifact_name")
        if (type(run_id) is not int or run_id <= 0 or not isinstance(name, str)
                or not re.fullmatch(r"workspace-qa-rejudge-" + str(run_id) + r"-[1-9][0-9]*", name)):
            raise ValueError("Record requires an exact recovery run and artifact name")
        result.update(recovery_run_id=str(run_id), artifact_name=name)
        expected = hash_mapping(record.get("evaluation_files"), EVALUATION_FILES, "evaluation_files")
        hash_mapping(record.get("evidence_sha256"), EVIDENCE_FILES, "evidence_sha256")
        root = repository or REPOSITORY
        changed = [name for name, value in expected.items()
                   if not (root / name).is_file() or (root / name).is_symlink() or digest(root / name) != value]
        if changed:
            result.update(reason="Evaluation files changed", incompatible_files=changed)
        else:
            result.update(reuse_eligible=True, evaluation_files_sha256=hashlib.sha256(
                json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    except (OSError, ValueError, TypeError, KeyError) as error:
        result.update(reason=str(error), error_type=type(error).__name__)
    return result


def inventory(root: Path) -> dict[str, str]:
    """Never follow artifact links or accept devices; none of these files execute."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Artifact root must be a real directory")
    files = {}
    for base, directories, names in os.walk(root, followlinks=False):
        for name in directories + names:
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            relative_path(relative)
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("Artifact symlinks are forbidden")
            if stat.S_ISREG(mode):
                files[relative] = digest(path)
            elif not stat.S_ISDIR(mode):
                raise ValueError("Artifact contains a non-regular file")
    return files


def verify_source(source: Path, record: dict, files: dict[str, str]) -> None:
    expected = hash_mapping(record.get("evidence_sha256"), EVIDENCE_FILES, "evidence_sha256")
    for name, value in expected.items():
        if files.get(name) != value:
            raise ValueError("Pinned smoke evidence hash mismatch: " + name)
    provenance = read_object(source / "rejudge-provenance.json")
    ci = provenance.get("ci_identity", {})
    if (provenance.get("status") != "completed" or provenance.get("no_new_qa_trials") is not True
            or provenance.get("new_qa_trials") != 0 or provenance.get("original_qa_evidence_unchanged") is not True
            or provenance.get("source_run_id") != record.get("qa_run_id")
            or str(ci.get("run_id")) != str(record["recovery_run_id"])
            or ci.get("commit") != record.get("recovery_commit")):
        raise ValueError("Recovered smoke provenance disagrees with the pinned record")
    old_summary = read_object(source / "report/summary.json")
    if (old_summary.get("pipeline_validation_complete") is not True
            or old_summary.get("summary", {}).get("complete") is not True
            or old_summary.get("efficacy_claim_ready") is not False):
        raise ValueError("Pinned smoke report is not a complete integration result")
    # Anchor raw probe/trial logs through the pinned prior validation before the
    # current checker reads them. A success flag alone is insufficient evidence.
    validation = read_object(source / "smoke_validation.json")
    raw = {"sdk-preflight/qoder/result.json": validation["setup_probe"]["result_sha256"],
           "sdk-preflight/qoder/agent/qodercli-stream.jsonl": validation["setup_probe"]["native_sha256"],
           "sdk-preflight/qoder/agent/zg-trace.jsonl": validation["setup_probe"]["bridge_sha256"]}
    for trial in validation.get("trials", []):
        trial_id = relative_path(trial["trial_id"])
        if len(trial_id.parts) != 1:
            raise ValueError("Invalid validation trial ID")
        raw[f"runs/{trial_id}/agent/qodercli-stream.jsonl"] = trial["native_sha256"]
        raw[f"runs/{trial_id}/agent/zg-trace.jsonl"] = trial["bridge_sha256"]
    for name, value in raw.items():
        if files.get(name) != value:
            raise ValueError("Raw smoke evidence hash mismatch: " + name)
    ledger = read_object(source / "runs/trial-results.json")
    if (ledger.get("task_id") != record.get("task_id") or ledger.get("repetitions_per_profile") != 1
            or len(ledger.get("trials", [])) != 2):
        raise ValueError("Pinned ledger must contain the original two smoke trials")
    for trial in ledger["trials"]:
        path = relative_path(trial["candidate_output_path"])
        answer = trial.get("answer")
        if (not isinstance(answer, str) or files.get("runs/" + path.as_posix())
                != hashlib.sha256(answer.encode()).hexdigest()):
            raise ValueError("Candidate report differs from the pinned QA answer")


def reuse(source: Path, output: Path) -> int:
    if source.is_symlink() or output.is_symlink():
        raise ValueError("Input and output directories cannot be symlinks")
    source, output = source.resolve(), output.resolve()
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("Reuse output must be separate from the original artifact")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Reuse output must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    provenance = {"schema_version": 1, "phase": "smoke_reuse", "status": "failed",
                  "no_new_qa_trials": True, "new_qa_trials": 0, "no_model_or_agent_calls": True,
                  "included_in_formal_benchmark": False, "efficacy_claim_ready": False,
                  "ci_identity": {name: os.environ.get(name) for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")}}
    try:
        eligible = compatibility()
        provenance["compatibility"] = eligible
        if not eligible["reuse_eligible"]:
            raise ValueError("Current evaluation files are incompatible with the verified smoke")
        record = read_object(RECORD_PATH)
        provenance.update({name: record[name] for name in (
            "qa_run_id", "qa_commit", "recovery_run_id", "recovery_commit", "recovery_artifact_name")})
        files = inventory(source)
        verify_source(source, record, files)
        provenance["pinned_evidence_sha256"] = record["evidence_sha256"]
        provenance["input_file_manifest_sha256"] = hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for name, expected in files.items():
            target = output / relative_path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
            if digest(target) != expected:
                raise ValueError("Artifact changed while copying: " + name)
        for name in ("smoke_validation.json", "report/summary.json", "report/summary.md"):
            original = output / "reuse-original" / name
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output / name, original)
        # Only trusted checkout modules run; artifact configs, scripts and shell
        # commands are inert data. Neither imported function performs model I/O.
        import report
        import run_task
        lock = read_object(HERE / "data/lock.json")
        selected = next(task for task in lock["tasks"] if task["task_id"] == record["task_id"])
        selection = read_object(output / "selection.json")
        if (selection.get("repetitions") != 1 or selection.get("tasks") != [selected]
                or selection.get("dataset") != lock["dataset"]):
            raise ValueError("Smoke task selection differs from the current frozen evaluation")
        validation = run_task.smoke_validation(output / "runs", "smoke")
        if validation != read_object(source / "smoke_validation.json") or validation.get("status") != "valid":
            raise ValueError("Current smoke checker did not reproduce the verified integration evidence")
        (output / "smoke_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n")
        summary = report.write_report(runs_dir=output / "runs", output=output / "report", manifest_path=output / "selection.json")
        run_task.annotate_smoke_report(output / "report", validation)
        final = read_object(output / "report/summary.json")
        if (summary["summary"]["complete"] is not True or final.get("pipeline_validation_complete") is not True
                or final.get("efficacy_claim_ready") is not False):
            raise ValueError("Current smoke measurements or rubric report are incomplete")
        if inventory(source) != files:
            raise ValueError("Original artifact changed during revalidation")
        for name, expected in files.items():
            if name == "smoke_validation.json" or name.startswith("report/"):
                continue
            if digest(output / name) != expected:
                raise ValueError("Revalidation changed QA or judge evidence: " + name)
        (output / "summary.md").write_text(
            f"Verified smoke reused from QA run {record['qa_run_id']} and recovery run {record['recovery_run_id']}. "
            "No new QA trials or model calls. These smoke observations are excluded from formal results.\n\n"
            + (output / "report/summary.md").read_text(encoding="utf-8"), encoding="utf-8")
        provenance.update(status="completed", original_qa_evidence_unchanged=True,
                          pipeline_validation_complete=True, smoke_validation="valid")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration) as error:
        provenance.update(error_type=type(error).__name__, error=str(error))
    finally:
        provenance["wall_seconds"] = round(time.monotonic() - started, 3)
        (output / "smoke-reuse-provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(provenance, ensure_ascii=False), flush=True)
    return 0 if provenance["status"] == "completed" else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-compatibility", action="store_true")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.check_compatibility:
        if args.artifact_root or args.output:
            parser.error("Compatibility mode does not accept artifact or output directories")
        result = compatibility()
        if args.github_output:
            with args.github_output.open("a") as target:
                target.write("reuse_eligible=" + str(result["reuse_eligible"]).lower() + "\n")
                for name in ("recovery_run_id", "artifact_name"):
                    target.write(name + "=" + result[name] + "\n")
        print(json.dumps(result), flush=True)
        return 0
    if not args.artifact_root or not args.output or args.github_output:
        parser.error("Reuse requires --artifact-root and --output; --github-output is compatibility-only")
    return reuse(args.artifact_root, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
