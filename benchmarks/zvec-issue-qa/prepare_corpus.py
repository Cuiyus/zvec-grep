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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    dataset = json.loads((HERE / "dataset.json").read_text())
    task = dataset["tasks"][0]
    revision = task["source_commit"]
    for key in ("instruction", "evaluator"):
        if sha256((HERE / task[key]).read_bytes()) != task[f"{key}_sha256"]:
            raise SystemExit(f"Frozen {key} hash mismatch; create a new dataset revision.")
    output = args.output.resolve()
    manifest_path = args.manifest.resolve()
    if output.exists():
        raise SystemExit("Output must not exist; use a fresh isolated source directory.")
    if output == manifest_path or output in manifest_path.parents:
        raise SystemExit("Manifest must be outside the solver source directory.")
    if HERE == output or output in HERE.parents:
        raise SystemExit("Source directory cannot contain benchmark/evaluator files.")

    resolved = subprocess.check_output(
        ["git", "-C", str(args.repo), "rev-parse", f"{revision}^{{commit}}"],
        text=True,
    ).strip()
    if resolved != revision:
        raise SystemExit("Source revision mismatch.")
    output.mkdir(parents=True)
    with tempfile.TemporaryFile() as archive:
        subprocess.run(
            ["git", "-C", str(args.repo), "archive", "--format=tar", revision],
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

    rubric = json.loads((HERE / task["evaluator"]).read_text())
    evidence = rubric["evidence_spans"]
    for span in evidence:
        content = (output / span["path"]).read_bytes()
        if sha256(content) != span["file_sha256"]:
            raise SystemExit(f"Evidence file mismatch: {span['path']}")
        lines = content.splitlines(keepends=True)
        selected = b"".join(lines[span["start_line"] - 1:span["end_line"]])
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
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({key: value for key, value in manifest.items() if key != "files"}))


if __name__ == "__main__":
    main()
