#!/usr/bin/env python3
"""Export and validate the frozen QA source; never copy gold into the corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile


HERE = Path(__file__).resolve().parent


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def contains(directory: Path, path: Path) -> bool:
    return directory == path or directory in path.parents


def relative_file(directory: Path, value: str, label: str) -> Path:
    """Resolve a dataset/source file without allowing an escape via paths or links."""
    if not isinstance(value, str) or not value:
        raise SystemExit(f"Invalid {label} path.")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(f"{label} path must be relative to its directory: {value}")
    path = (directory / relative).resolve()
    if path == directory or not contains(directory, path) or not path.is_file():
        raise SystemExit(f"{label} must be a file inside its directory: {value}")
    return path


def select_task(dataset: dict, task_id: str | None) -> dict:
    tasks = dataset.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise SystemExit("Dataset must contain at least one task.")
    by_id = {}
    for task in tasks:
        identifier = task.get("id") if isinstance(task, dict) else None
        if not isinstance(identifier, str) or not identifier:
            raise SystemExit("Every task must have a nonempty string ID.")
        if identifier in by_id:
            raise SystemExit(f"Duplicate task ID: {identifier}")
        by_id[identifier] = task
    if task_id is None:
        if len(tasks) != 1:
            raise SystemExit("Multi-task datasets require an explicit --task ID.")
        return tasks[0]
    if task_id not in by_id:
        raise SystemExit(f"Unknown task ID: {task_id}")
    return by_id[task_id]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=HERE / "dataset.json")
    parser.add_argument("--task", help="Task ID; optional only for a single-task dataset.")
    args = parser.parse_args(argv)
    dataset_path = args.dataset.resolve()
    dataset = json.loads(dataset_path.read_text())
    task = select_task(dataset, args.task)
    revision = task["source_commit"]
    task_files = {}
    for key in ("instruction", "evaluator"):
        task_files[key] = relative_file(dataset_path.parent, task[key], key)
        if sha256(task_files[key].read_bytes()) != task[f"{key}_sha256"]:
            raise SystemExit(f"Frozen {key} hash mismatch; create a new dataset revision.")
    output = args.output.resolve()
    manifest_path = args.manifest.resolve()
    repo = args.repo.resolve()
    if output.exists() or args.output.is_symlink():
        raise SystemExit("Output must not exist; use a fresh isolated source directory.")
    if manifest_path.exists() or args.manifest.is_symlink():
        raise SystemExit("Manifest must not exist; use a fresh controller file.")
    if contains(output, manifest_path) or contains(manifest_path, output):
        raise SystemExit("Manifest must be outside the solver source directory.")
    for protected in (HERE, dataset_path.parent, repo):
        if contains(protected, output) or contains(output, protected):
            raise SystemExit("Source directory must be separate from benchmark and Git directories.")
        if contains(protected, manifest_path) or contains(manifest_path, protected):
            raise SystemExit("Manifest must be outside benchmark and Git directories.")

    resolved = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", f"{revision}^{{commit}}"],
        text=True,
    ).strip()
    if resolved != revision:
        raise SystemExit("Source revision mismatch.")
    output.mkdir(parents=True)
    with tempfile.TemporaryFile() as archive:
        subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", revision],
            stdout=archive, check=True,
        )
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode="r:") as tar:
            for member in tar.getmembers():
                destination = (output / member.name).resolve()
                if output not in destination.parents:
                    raise SystemExit(f"Unsafe archive member: {member.name}")
                if not member.isfile() and not member.isdir():
                    raise SystemExit(f"Unsupported archive member: {member.name}")
            tar.extractall(output)

    rubric = json.loads(task_files["evaluator"].read_text())
    if rubric.get("task_id") != task["id"] or rubric.get("source_commit") != revision:
        raise SystemExit("Evaluator task/source identity differs from the selected task.")
    evidence = rubric["evidence_spans"]
    for span in evidence:
        content = relative_file(output, span["path"], "Evidence").read_bytes()
        if sha256(content) != span["file_sha256"]:
            raise SystemExit(f"Evidence file mismatch: {span['path']}")
        lines = content.splitlines(keepends=True)
        start, end = span["start_line"], span["end_line"]
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise SystemExit(f"Invalid evidence line range: {span['id']}")
        selected = b"".join(lines[start - 1:end])
        if sha256(selected) != span["span_sha256"]:
            raise SystemExit(f"Evidence span mismatch: {span['id']}")

    forbidden = [marker.encode() for marker in task["forbidden_corpus_markers"]]
    inventory = []
    for path in sorted(output.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(output).as_posix()
        data = path.read_bytes()
        for marker in forbidden:
            if marker in data:
                raise SystemExit(f"Potential answer leakage in {relative}: {marker!r}")
        inventory.append({"path": relative, "bytes": len(data), "sha256": sha256(data)})

    encoded = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    if len(inventory) != task["source_file_count"]:
        raise SystemExit("Exported source file count differs from the frozen inventory.")
    if sha256(encoded) != task["source_inventory_sha256"]:
        raise SystemExit("Exported source differs from the frozen inventory.")
    manifest = {
        "source_repository": task["source_repository"],
        "source_commit": revision,
        "source_file_count": len(inventory),
        "inventory_sha256": sha256(encoded),
        "git_history_present": (output / ".git").exists(),
        "evidence_spans_verified": len(evidence),
        "known_leakage_markers_found": False,
        "files": inventory,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("x") as handle:
        handle.write(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({key: value for key, value in manifest.items() if key != "files"}))


if __name__ == "__main__":
    main()
