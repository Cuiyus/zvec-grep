"""Validate native QA continuations without rerunning an observed trial.

The caller obtains the pinned artifact and audits permitted code changes in CI.
These helpers treat every artifact as data, preserve previous bytes, and never
execute a command or load a Python module supplied by an artifact.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat

import runner
from native_session import PROTOCOL, INSTALL_COMMAND, session_spec, validate_installation

LOCK_PATH = Path(__file__).resolve().parent / "data/lock.json"
IDENTITY_FIELDS = ("trial_id", "task_id", "profile", "repetition", "block_id", "trajectory_path")
PRIOR_PATHS = {"ledger": "continuation-evidence/prior-ledger.json",
               "manifest": "continuation-evidence/prior-manifest.json",
               "judgements": "continuation-evidence/prior-judgements.json"}
SETUP_EVIDENCE_PATH = "continuation-evidence/setup-only/evidence.json"
RUNTIME_FIELDS = ("schema_version", "protocol", "integration_method", "install_command", "task_id",
    "package", "embedding_model", "embedding_endpoint", "agent", "agent_version", "model", "agent_spec",
    "source_files", "question_sha256", "answer_filename", "run_limits", "repetitions_per_profile",
    "order_seed", "gold_visible_to_agent", "corpus_readonly_mount", "index_options", "index_policy",
    "wall_seconds_scope", "answer_delivery", "installed_versions", "os", "architecture")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_object(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Continuation JSON must be a regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Continuation evidence must be a JSON object: " + path.name)
    return value


def relative_path(value: str) -> Path:
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(c) < 32 for c in value) or PurePosixPath(value).is_absolute()
            or any(p in {"", ".", ".."} for p in value.split("/"))):
        raise ValueError("Continuation evidence path is not canonical")
    return Path(value)


def file_hashes(root: Path) -> dict[str, str]:
    """Reject symlinks, special files and unsafe names before any file is read."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Continuation root must be a real directory")
    paths = []
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(base) / name
            relative_path(path.relative_to(root).as_posix())
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode):
                paths.append(path)
            elif not stat.S_ISDIR(mode):
                raise ValueError("Continuation evidence must not contain symlinks or special files")
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(paths)}


def is_unstarted(row: dict) -> bool:
    """A planned status cannot erase already recorded usage or other evidence."""
    return (isinstance(row, dict) and row.get("status") == "planned"
            and all(value is None for key, value in row.items()
                    if key not in {*IDENTITY_FIELDS, "status"}))


def _rows(ledger: dict) -> list[dict]:
    if (ledger.get("schema_version") != 1 or ledger.get("protocol") != PROTOCOL
            or ledger.get("repetitions_per_profile") != 10):
        raise ValueError("Continuation requires the native 10-repetition ledger")
    rows = ledger.get("trials")
    if not isinstance(rows, list) or len(rows) != 20:
        raise ValueError("Continuation must preserve the complete 20-trial denominator")
    ids = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("status"), str)
                or not row["status"] or row.get("task_id") != ledger.get("task_id")):
            raise ValueError("Invalid continuation trial row")
        trial_id = row.get("trial_id")
        if not isinstance(trial_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", trial_id) or trial_id in ids:
            raise ValueError("Invalid or duplicate continuation trial ID")
        ids.add(trial_id)
        repetition = row.get("repetition")
        if (type(repetition) is not int or not 1 <= repetition <= 10
                or row.get("block_id") != repetition or row.get("profile") not in {"baseline", "with-zg"}
                or trial_id != f"{ledger['task_id']}-r{repetition:02d}-{row['profile']}"
                or row.get("trajectory_path") != f"{trial_id}/agent/trajectory.json"):
            raise ValueError("Continuation trial does not match the frozen repetition identity")
        if row["status"] == "planned" and not is_unstarted(row):
            raise ValueError("Planned trial contains execution evidence")
    return rows


def validate_transition(prior_ledger: dict, current_ledger: dict) -> dict:
    """Only originally unstarted rows can change; every observed row is final."""
    old, new = _rows(prior_ledger), _rows(current_ledger)
    for key in ("schema_version", "protocol", "task_id", "repetitions_per_profile"):
        if prior_ledger.get(key) != current_ledger.get(key):
            raise ValueError("Continuation ledger identity mismatch: " + key)
    preserved, pending = [], []
    for before, after in zip(old, new, strict=True):
        if any(before.get(key) != after.get(key) for key in IDENTITY_FIELDS):
            raise ValueError("Continuation changed a trial identity or its order")
        if is_unstarted(before):
            pending.append(before["trial_id"])
        else:
            if before != after:
                raise ValueError("Continuation modified a previously attempted trial")
            preserved.append(before["trial_id"])
    return {"preserved_trial_ids": preserved, "pending_trial_ids": pending}


def _native_manifest(manifest: dict) -> None:
    if (manifest.get("schema_version") != 2 or manifest.get("protocol") != PROTOCOL
            or manifest.get("integration_method") != "zg_install"
            or manifest.get("install_command") != INSTALL_COMMAND
            or manifest.get("gold_visible_to_agent") is not False
            or manifest.get("corpus_readonly_mount") is not True):
        raise ValueError("Continuation requires original native standard-install evidence")
    files = manifest.get("source_files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Continuation source identity is missing")
    for name, value in files.items():
        relative_path(name)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Continuation source hash is invalid")
    if any(field not in manifest for field in RUNTIME_FIELDS):
        raise ValueError("Continuation runtime identity is incomplete")


def _validate_plan(plan: dict, ledger: dict) -> None:
    rows = _rows(ledger)
    expected = runner.make_plan(ledger["task_id"], 10, plan.get("order_seed"))
    for key in ("schema_version", "protocol", "task_id", "repetitions_per_profile", "order_seed", "order_policy"):
        if plan.get(key) != expected.get(key):
            raise ValueError("Continuation plan identity differs: " + key)
    if not isinstance(plan.get("trials"), list) or len(plan["trials"]) != 20:
        raise ValueError("Continuation plan must have all 20 trials")
    for planned, row, canonical in zip(plan["trials"], rows, expected["trials"], strict=True):
        if any(planned.get(key) != canonical.get(key) or row.get(key) != canonical.get(key) for key in IDENTITY_FIELDS):
            raise ValueError("Continuation plan changed the frozen order or trial identity")
        if planned.get("status") != row["status"]:
            raise ValueError("Continuation plan and ledger status disagree")


def _row_provenance(row: dict, manifest: dict) -> None:
    provenance = row.get("provenance")
    # Interrupted runs can have a directory and a running ledger row before a
    # terminal result exists. Such rows remain attempted, never rerunnable.
    if provenance is None and row["status"] == "running":
        return
    expected = {"manifest_path": "manifest.json", "source_git_commit": manifest["source_git_commit"],
                "question_sha256": manifest["question_sha256"], "image_id": manifest["image_id"]}
    if not isinstance(provenance, dict) or any(provenance.get(k) != v for k, v in expected.items()):
        raise ValueError("Original trial provenance differs from its original manifest")


def load_prior(prior_artifact_root: Path, plan: dict) -> dict:
    root = Path(prior_artifact_root)
    files = file_hashes(root)
    values = {"selection": read_object(root / "selection.json"),
              "manifest": read_object(root / "runs/manifest.json"),
              "plan": read_object(root / "runs/plan.json"),
              "ledger": read_object(root / "runs/trial-results.json"),
              "judgements": read_object(root / "runs/judgements.json")}
    manifest, ledger = values["manifest"], values["ledger"]
    _native_manifest(manifest)
    selected = read_object(LOCK_PATH)
    selected["tasks"] = [t for t in selected["tasks"] if t["task_id"] == ledger["task_id"]]
    selected["repetitions"] = 10
    if values["selection"] != selected:
        raise ValueError("Continuation dataset selection differs from the frozen lock")
    task = next((t for t in values["selection"]["tasks"] if t["task_id"] == ledger["task_id"]), None)
    if not task or manifest["task_id"] != ledger["task_id"] or manifest["answer_filename"] != task["answer_filename"]:
        raise ValueError("Continuation task or output filename differs from the frozen selection")
    _validate_plan(values["plan"], ledger)
    expected_plan = copy.deepcopy(values["plan"])
    for row in expected_plan["trials"]:
        row["status"] = "planned"
    if plan != expected_plan:
        raise ValueError("Continuation cannot replace the original complete plan")
    if manifest["order_seed"] != plan["order_seed"] or manifest["repetitions_per_profile"] != 10:
        raise ValueError("Continuation manifest and plan disagree")
    if values["judgements"].get("trial_results_sha256") != files["runs/trial-results.json"]:
        raise ValueError("Original judgements refer to a different trial ledger")
    judgments = values["judgements"]
    judged_rows = judgments.get("trials")
    if (judgments.get("schema_version") != 1 or judgments.get("task_id") != ledger["task_id"]
            or judgments.get("repetitions_per_profile") != 10 or judgments.get("expected_trials") != 20
            or not isinstance(judged_rows, list) or len(judged_rows) != 20
            or any(not isinstance(row, dict) for row in judged_rows)
            or {row.get("trial_id") for row in judged_rows} != {row["trial_id"] for row in ledger["trials"]}):
        raise ValueError("Original judgements must describe the same complete native trial set")
    classification = validate_transition(ledger, ledger)
    by_id = {r["trial_id"]: r for r in ledger["trials"]}
    for row in ledger["trials"]:
        trial_dir = root / "runs" / row["trial_id"]
        if is_unstarted(row):
            if trial_dir.exists():
                raise ValueError("Planned trial has a directory or execution evidence; refusing to rerun")
            continue
        if not trial_dir.is_dir():
            raise ValueError("Original attempted trial directory is missing")
        result_path = trial_dir / "result.json"
        if result_path.exists():
            if read_object(result_path) != row:
                raise ValueError("Original trial result and ledger disagree")
        elif row["status"] != "running":
            raise ValueError("Original terminal result is missing")
        _row_provenance(row, manifest)
        candidate = row.get("candidate_output_path")
        if candidate is not None:
            expected = f"{row['trial_id']}/candidate/{manifest['answer_filename']}"
            if candidate != expected or (root / "runs" / relative_path(candidate)).read_text() != row.get("answer"):
                raise ValueError("Original candidate does not match its recorded answer")
        if row["status"] == "completed":
            validate_installation(trial_dir / "agent", profile=row["profile"])
    return {"root": root, **values, **classification, "rows_by_id": by_id,
            "files_sha256": files, "compatibility": None, "kind": "partial_trials"}


def load_setup_prior(prior_artifact_root: Path, plan: dict) -> dict:
    """Validate an original artifact that failed before the first QA trial."""
    root = Path(prior_artifact_root)
    files = file_hashes(root)
    selection = read_object(root / "selection.json")
    frozen = read_object(root / "planned.json")
    ledger = read_object(root / "runs/trial-results.json")
    failure = read_object(root / "setup-failure.json")
    selected = read_object(LOCK_PATH)
    task_id = plan.get("task_id")
    selected["tasks"] = [task for task in selected["tasks"] if task["task_id"] == task_id]
    selected["repetitions"] = 10
    if selection != selected or frozen != plan:
        raise ValueError("Setup-only artifact differs from the frozen task selection or plan")
    _validate_plan(plan, ledger)
    if any(not is_unstarted(row) for row in ledger["trials"]):
        raise ValueError("Setup-only recovery requires zero attempted QA trials")
    if failure.get("status") != "failed" or not isinstance(failure.get("error_type"), str):
        raise ValueError("Setup-only artifact lacks its original setup failure")
    forbidden = [root / "runs" / name for name in ("manifest.json", "plan.json", "judgements.json")]
    trial_dirs = [root / "runs" / row["trial_id"] for row in ledger["trials"]]
    if any(path.exists() for path in forbidden + trial_dirs):
        raise ValueError("Setup-only artifact contains QA execution evidence")
    return {"root": root, "selection": selection, "plan": frozen, "ledger": ledger,
            "setup_failure": failure, "preserved_trial_ids": [],
            "pending_trial_ids": [row["trial_id"] for row in ledger["trials"]],
            "rows_by_id": {row["trial_id"]: row for row in ledger["trials"]},
            "files_sha256": files, "compatibility": None, "kind": "setup_only"}


def stage_setup_prior(bundle: dict, staging: Path, review: dict, source_config: dict) -> dict:
    """Copy a zero-QA original artifact to content-addressed evidence before execution."""
    if bundle.get("kind") != "setup_only" or file_hashes(bundle["root"]) != bundle["files_sha256"]:
        raise ValueError("Setup-only original artifact changed after validation")
    if (not isinstance(review, dict) or review.get("status") != "verified"
            or review.get("base_commit") != source_config.get("source_commit")
            or not re.fullmatch(r"[0-9a-f]{40}", str(review.get("head_commit", "")))):
        raise ValueError("Setup-only recovery lacks the trusted CI code-change review")
    staging = Path(staging)
    if staging.exists() or staging.resolve().is_relative_to(bundle["root"].resolve()):
        raise ValueError("Setup-only staging must be a new separate directory")
    blobs = staging / "blobs"
    blobs.mkdir(parents=True)
    stored = {}
    for relative, sha in bundle["files_sha256"].items():
        target = blobs / sha
        if not target.exists():
            shutil.copyfile(bundle["root"] / relative_path(relative), target)
        if digest(target) != sha:
            raise ValueError("Setup-only original bytes changed during staging")
        stored[relative] = f"blobs/{sha}"
    evidence = {"schema_version": 1, "kind": "setup_only", "no_qa_attempts": True,
        "no_resampling": True, "source_run_id": str(source_config.get("source_run_id")),
        "source_run_attempt": str(source_config.get("source_run_attempt")),
        "source_commit": source_config.get("source_commit"), "code_review": copy.deepcopy(review),
        "original_artifact_name": bundle["root"].name,
        "original_files_sha256": copy.deepcopy(bundle["files_sha256"]),
        "stored_files": stored, "preserved_trial_ids": [],
        "pending_trial_ids": list(bundle["pending_trial_ids"])}
    (staging / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    return evidence


def install_setup_prior(staging: Path, runs: Path, manifest: dict) -> dict:
    """Attach staged setup-only evidence to a completed continuation artifact."""
    staging, runs = Path(staging), Path(runs)
    evidence = read_object(staging / "evidence.json")
    review = manifest.get("continuation_code_review")
    if (review != evidence.get("code_review")
            or review.get("head_commit") != manifest.get("ci_identity", {}).get("GITHUB_SHA")):
        raise ValueError("Setup-only continuation manifest differs from its reviewed code")
    target = runs / "continuation-evidence/setup-only"
    if target.exists():
        raise ValueError("Setup-only continuation evidence already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging, target)
    manifest["setup_continuation"] = copy.deepcopy(evidence)
    return evidence


def validate_setup_continuation_evidence(runs: Path, original_bundle: dict | None = None) -> dict:
    manifest = read_object(Path(runs) / "manifest.json")
    _native_manifest(manifest)
    evidence = read_object(Path(runs) / SETUP_EVIDENCE_PATH)
    if (manifest.get("setup_continuation") != evidence or evidence.get("schema_version") != 1
            or evidence.get("kind") != "setup_only" or evidence.get("no_qa_attempts") is not True
            or evidence.get("no_resampling") is not True or evidence.get("preserved_trial_ids") != []):
        raise ValueError("Setup-only continuation provenance is missing or inconsistent")
    review = manifest.get("continuation_code_review")
    if (review != evidence.get("code_review") or review.get("status") != "verified"
            or review.get("base_commit") != evidence.get("source_commit")
            or review.get("head_commit") != manifest.get("ci_identity", {}).get("GITHUB_SHA")):
        raise ValueError("Setup-only continuation code review differs")
    hashes, stored = evidence.get("original_files_sha256"), evidence.get("stored_files")
    if not isinstance(hashes, dict) or not hashes or not isinstance(stored, dict) or set(hashes) != set(stored):
        raise ValueError("Setup-only original file map is invalid")
    root = Path(runs) / "continuation-evidence/setup-only"
    for relative, sha in hashes.items():
        relative_path(relative)
        stored_path = stored[relative]
        if stored_path != f"blobs/{sha}" or digest(root / relative_path(stored_path)) != sha:
            raise ValueError("Setup-only retained original bytes differ")
    if original_bundle is not None:
        if original_bundle.get("kind") != "setup_only" or hashes != original_bundle.get("files_sha256"):
            raise ValueError("Setup-only continuation refers to a different original artifact")
        if evidence.get("pending_trial_ids") != original_bundle.get("pending_trial_ids"):
            raise ValueError("Setup-only pending trial set differs")
    return evidence


def validate_runtime(bundle: dict, current_manifest: dict, question: str, filename: str) -> dict:
    previous = bundle["manifest"]
    _native_manifest(current_manifest)
    for field in RUNTIME_FIELDS:
        if previous[field] != current_manifest[field]:
            raise ValueError("Continuation runtime mismatch: " + field)
    if filename != previous["answer_filename"] or hashlib.sha256(question.encode()).hexdigest() != previous["question_sha256"]:
        raise ValueError("Continuation original question or answer filename changed")
    for trial_id in bundle["preserved_trial_ids"]:
        row = bundle["rows_by_id"][trial_id]
        trial_dir = bundle["root"] / "runs" / trial_id
        prompt = runner.instruction(question, filename, zg=row["profile"] == "with-zg")
        instruction_path, spec_path = trial_dir / "instruction.json", trial_dir / "agent/native-spec.json"
        if instruction_path.exists():
            expected = {"text": prompt, "sha256": hashlib.sha256(prompt.encode()).hexdigest()}
            if read_object(instruction_path) != expected:
                raise ValueError("Continuation generated a different original instruction")
        elif row["status"] != "running":
            raise ValueError("Original trial instruction is missing")
        if spec_path.exists():
            spec = read_object(spec_path)
            if (spec.get("prompt") != prompt or spec.get("limits") != previous["run_limits"]
                    or spec.get("profile") != row["profile"]):
                raise ValueError("Original native session specification differs")
            expected_session = session_spec(spec, Path("/logs"))
            actual_session = trial_dir / "agent/session-spec.json"
            if actual_session.exists() and read_object(actual_session) != expected_session:
                raise ValueError("Continuation changed native command, tools, environment or limits")
        elif row["status"] == "completed":
            raise ValueError("Original completed trial lacks its native specification")
    old_ci, new_ci = previous.get("ci_identity", {}), current_manifest.get("ci_identity", {})
    review = current_manifest.get("continuation_code_review")
    if (not isinstance(review, dict) or review.get("status") != "verified"
            or review.get("base_commit") != old_ci.get("GITHUB_SHA")
            or review.get("head_commit") != new_ci.get("GITHUB_SHA")
            or not re.fullmatch(r"[0-9a-f]{40}", str(review.get("base_commit", "")))
            or not re.fullmatch(r"[0-9a-f]{40}", str(review.get("head_commit", "")))):
        raise ValueError("Continuation lacks the trusted CI code-change review")
    proof = {"status": "verified", "code_review": copy.deepcopy(review),
        "source_files_unchanged": True, "original_instructions_unchanged": True,
        "runtime_parameters_unchanged": True,
        "source_image_id": previous.get("image_id"), "current_image_id": current_manifest.get("image_id"),
        "source_git_commit": previous.get("source_git_commit"), "current_source_git_commit": current_manifest.get("source_git_commit"),
        "image_id_policy": "record rebuild identity; require identical installed versions, platform, evaluated commands and reviewed build inputs",
        "source_git_policy": "generated snapshot timestamps may differ; every original working file path and SHA-256 must match"}
    bundle["compatibility"] = proof
    return proof


def import_prior(bundle: dict, output_runs: Path, plan: dict) -> dict:
    if not bundle.get("compatibility") or bundle["compatibility"].get("status") != "verified":
        raise ValueError("Runtime compatibility must be verified before importing original trials")
    root = bundle["root"]
    if file_hashes(root) != bundle["files_sha256"]:
        raise ValueError("Original artifact changed after validation")
    destination = Path(output_runs)
    if destination.is_symlink() or destination.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(destination.resolve()):
        raise ValueError("Continuation output must be separate from the original artifact")
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "continuation-evidence").exists() or any((destination / t).exists() for t in bundle["rows_by_id"]):
        raise ValueError("Original trial import refuses to overwrite evidence")
    expected = runner.make_plan(bundle["ledger"]["task_id"], 10, bundle["plan"]["order_seed"])
    if plan != expected:
        raise ValueError("Import requires the unchanged new complete plan")
    copied = {}
    for relative, expected_sha in bundle["files_sha256"].items():
        parts = relative_path(relative).parts
        if len(parts) >= 3 and parts[0] == "runs" and parts[1] in bundle["preserved_trial_ids"]:
            target = destination / Path(*parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
            if digest(target) != expected_sha:
                raise ValueError("Original trial bytes changed during import")
            copied[target.relative_to(destination).as_posix()] = expected_sha
    source_names = {"ledger": "trial-results.json", "manifest": "manifest.json", "judgements": "judgements.json"}
    provenance = {"schema_version": 1, "source_run_id": bundle["manifest"]["ci_identity"].get("GITHUB_RUN_ID"),
        "source_commit": bundle["manifest"]["ci_identity"].get("GITHUB_SHA"),
        "preserved_trial_ids": list(bundle["preserved_trial_ids"]), "pending_trial_ids": list(bundle["pending_trial_ids"]),
        "preserved_files_sha256": copied, "compatibility": copy.deepcopy(bundle["compatibility"]), "no_resampling": True}
    for name, relative in PRIOR_PATHS.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "runs" / source_names[name], target)
        if digest(target) != bundle["files_sha256"]["runs/" + source_names[name]]:
            raise ValueError("Original control evidence changed during import")
        provenance[f"prior_{name}_path"] = relative
        provenance[f"prior_{name}_sha256"] = digest(target)
    for trial in plan["trials"]:
        if trial["trial_id"] in bundle["preserved_trial_ids"]:
            trial["status"] = bundle["rows_by_id"][trial["trial_id"]]["status"]
    return provenance


def validate_continuation_evidence(runs: Path) -> dict:
    """Shared read-only identity gate for the reporting and judging stages."""
    if runs.is_symlink() or not runs.is_dir():
        raise ValueError("Continuation runs root must be a real directory")
    manifest = read_object(runs / "manifest.json")
    evidence = manifest.get("continuation")
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1 or evidence.get("no_resampling") is not True:
        raise ValueError("Native continuation provenance is missing")
    # Preparation can contain gigabytes of vectors. Only prior evidence and
    # preserved trial directories belong to this proof; never rescan the seed.
    all_files = {"continuation-evidence/" + name: value
                 for name, value in file_hashes(runs / "continuation-evidence").items()}
    previous = {}
    for name, relative in PRIOR_PATHS.items():
        if evidence.get(f"prior_{name}_path") != relative or all_files.get(relative) != evidence.get(f"prior_{name}_sha256"):
            raise ValueError("Continuation original evidence path or SHA-256 differs: " + name)
        previous[name] = read_object(runs / relative)
    _native_manifest(manifest)
    _native_manifest(previous["manifest"])
    for field in RUNTIME_FIELDS:
        if manifest[field] != previous["manifest"][field]:
            raise ValueError("Continuation runtime mismatch: " + field)
    transition = validate_transition(previous["ledger"], read_object(runs / "trial-results.json"))
    if any(evidence.get(key) != value for key, value in transition.items()):
        raise ValueError("Continuation preserved/pending trial classification differs")
    prior_manifest = previous["manifest"]
    compatibility = evidence.get("compatibility", {})
    review = manifest.get("continuation_code_review")
    if (compatibility.get("status") != "verified" or not isinstance(review, dict)
            or compatibility.get("code_review") != review or review.get("status") != "verified"
            or review.get("base_commit") != prior_manifest.get("ci_identity", {}).get("GITHUB_SHA")
            or review.get("head_commit") != manifest.get("ci_identity", {}).get("GITHUB_SHA")
            or compatibility.get("source_image_id") != prior_manifest.get("image_id")
            or compatibility.get("current_image_id") != manifest.get("image_id")
            or compatibility.get("source_git_commit") != prior_manifest.get("source_git_commit")
            or compatibility.get("current_source_git_commit") != manifest.get("source_git_commit")):
        raise ValueError("Continuation runtime review proof differs")
    if (evidence.get("source_run_id") != prior_manifest.get("ci_identity", {}).get("GITHUB_RUN_ID")
            or evidence.get("source_commit") != prior_manifest.get("ci_identity", {}).get("GITHUB_SHA")
            or previous["judgements"].get("trial_results_sha256") != evidence["prior_ledger_sha256"]):
        raise ValueError("Continuation original run or judge identity differs")
    preserved = evidence.get("preserved_files_sha256")
    if not isinstance(preserved, dict) or not preserved:
        raise ValueError("Continuation original file hashes are missing")
    actual = {trial_id + "/" + name: value for trial_id in transition["preserved_trial_ids"]
              for name, value in file_hashes(runs / trial_id).items()}
    if actual != preserved:
        raise ValueError("Continuation changed files within a previously attempted trial")
    for row in previous["ledger"]["trials"]:
        if row["trial_id"] in transition["preserved_trial_ids"]:
            _row_provenance(row, prior_manifest)
    return {**copy.deepcopy(evidence), "prior_ledger": previous["ledger"],
            "prior_manifest": prior_manifest, "prior_judgements": previous["judgements"]}
