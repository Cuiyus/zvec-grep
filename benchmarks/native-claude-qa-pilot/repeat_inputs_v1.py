"""Select unchanged q001/q002/q003 preparation for the repetition study."""
from pathlib import Path

import expansion_v1 as reserves

TASK_IDS = ("q001", "q002", "q003")
REPO = reserves.REPO
pilot = reserves.pilot


def validate(prepared_group: Path, reference_prepared: Path, repo_root: Path = REPO):
    # The existing reserve validator also verifies the full original q001 export,
    # its original plan and templates before checking both reserve snapshots.
    return reserves.validate(prepared_group, reference_prepared, repo_root)


def selected_inputs(prepared_group: Path, reference_prepared: Path, task_id: str,
                    repo_root: Path = REPO):
    if task_id not in TASK_IDS:
        raise ValueError("undeclared_repeat_task")
    validate(prepared_group, reference_prepared, repo_root)
    root = reference_prepared if task_id == "q001" else prepared_group / task_id
    source = pilot.load(root / "source-manifest.json")
    if task_id == "q001":
        dataset = pilot.load(repo_root / "benchmarks/zvec-issue-qa/dataset.json")
        task = next(row for row in dataset["tasks"] if row["id"] == task_id)
        metadata = {
            "task_id": task_id, "source_commit": task["source_commit"],
            "source_inventory_sha256": task["source_inventory_sha256"],
            "source_file_count": task["source_file_count"],
            "question": {"path": task["instruction"], "sha256": task["instruction_sha256"]},
            "rubric": {"path": task["evaluator"], "sha256": task["evaluator_sha256"]},
            "native_task_hashes": reserves.task_hashes(root / "tasks/zvec-q001"),
        }
    else:
        metadata = pilot.load(root / "prepared.json")
    return metadata, source["files"], root


def references(prepared_group: Path, reference_prepared: Path, repo_root: Path = REPO):
    validate(prepared_group, reference_prepared, repo_root)
    return {
        "group_sha256": pilot.digest(prepared_group / "group.json"),
        "metadata_sha256": {task: pilot.digest(
            (reference_prepared if task == "q001" else prepared_group / task) / "prepared.json")
            for task in TASK_IDS},
        "source_manifest_sha256": {task: pilot.digest(
            (reference_prepared if task == "q001" else prepared_group / task) / "source-manifest.json")
            for task in TASK_IDS},
    }
