"""Content-addressed cache of completed production indexes, never QA artifacts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

BUILD_FILES = (
    "benchmarks/swe-qa-bench/runtime/package-lock.json",
    "benchmarks/workspace-qa/Dockerfile",
    "benchmarks/workspace-qa/native_index.py",
    "benchmarks/workspace-qa/native_runner.py",
    "benchmarks/workspace-qa/native_session.py",
    "benchmarks/swe-qa-bench/zg_bench/swe_qa/readonly_run.py",
    "benchmarks/workspace-qa/dataset.py",
    "benchmarks/workspace-qa/office_markdown.py",
    "benchmarks/workspace-qa/runner.py",
    "benchmarks/workspace-qa/seed_cache.py",
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def build_identity(runtime: dict[str, Any], repository_root: Path | None = None) -> dict[str, Any]:
    """Shared by the runner and outer CI cache planner; image timestamps are excluded."""
    root = repository_root or Path(__file__).resolve().parents[2]
    versions = runtime.get("installed_versions")
    if not isinstance(versions, dict) or not versions.get("zg") or not versions.get("qoder"):
        raise ValueError("Cache identity requires actual installed runtime versions")
    return {"installed_versions": versions, "os": runtime.get("os"),
            "architecture": runtime.get("architecture"),
            "build_files": {name: file_hash(root / name) for name in BUILD_FILES}}


def make_identity(source_files: dict[str, str], runtime: dict[str, Any], *,
                  protocol: str, embedding_model: str, endpoint: str,
                  max_file_size_bytes: int) -> dict[str, Any]:
    return {"schema_version": 1, "protocol": protocol,
            "source_fingerprint": digest(source_files), "source_file_count": len(source_files),
            "source_fingerprint_scope": "relative paths and content hashes; excludes Git metadata and mtimes",
            "build": build_identity(runtime), "embedding_model": embedding_model,
            "embedding_endpoint": endpoint,
            "index_options": {"root": "/app", "maxFileSizeBytes": max_file_size_bytes}}


def index_hashes(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Cached index is not a directory")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Cached index must not contain symlinks")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_hash(path)
    if not result:
        raise ValueError("Cached index is empty")
    return result


def restore(cache_root: Path | None, destination: Path, identity: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    result = {"status": "disabled" if cache_root is None else "miss", "key": digest(identity)}
    if cache_root is None:
        return {**result, "validation_seconds": 0.0}
    entry = cache_root / result["key"]
    try:
        if entry.is_symlink():
            raise ValueError("Cached entry is a symlink")
        metadata = json.loads((entry / "metadata.json").read_text())
        if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
            raise ValueError("Cached metadata must be a supported object")
        if (entry / "COMPLETE").read_text().strip() != digest(metadata):
            raise ValueError("Cache completion marker mismatch")
        if metadata.get("identity") != identity or metadata.get("status") != "completed":
            raise ValueError("Cache identity or completion mismatch")
        if metadata.get("preflight_passed") is not True:
            raise ValueError("Cache did not pass preflight")
        build = metadata.get("original_build_info", {})
        if (not isinstance(build, dict) or build.get("status") != "completed"
                or build.get("fresh") is not True or build.get("source_unchanged") is not True):
            raise ValueError("Cached build metadata is incomplete")
        expected = metadata.get("index_files")
        if index_hashes(entry / "index") != expected:
            raise ValueError("Cached index file hashes mismatch")
        shutil.copytree(entry / "index", destination, dirs_exist_ok=True)
        if index_hashes(destination) != expected:
            raise ValueError("Restored index file hashes mismatch")
        result.update(status="hit", original_build_info=metadata["original_build_info"],
                      index_files=expected)
    except (OSError, ValueError, KeyError, TypeError) as error:
        # A partial restore must never be mistaken for the fresh empty build root.
        if destination.exists():
            shutil.rmtree(destination)
        (destination / "locks").mkdir(parents=True)
        result["reason"] = type(error).__name__ + ": " + str(error)
    result["validation_seconds"] = round(time.monotonic() - started, 6)
    return result


def publish(cache_root: Path | None, index: Path, identity: dict[str, Any],
            original_build_info: dict[str, Any], *, preflight_passed: bool) -> dict[str, Any]:
    if cache_root is None:
        return {"status": "disabled", "wall_seconds": 0.0}
    started = time.monotonic()
    temporary = None
    try:
        if (not preflight_passed or original_build_info.get("status") != "completed"
                or original_build_info.get("fresh") is not True
                or original_build_info.get("source_unchanged") is not True):
            raise ValueError("Only completed fresh source-preserving builds may be cached")
        cache_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".seed-building-", dir=cache_root))
        before = index_hashes(index)
        shutil.copytree(index, temporary / "index")
        if index_hashes(temporary / "index") != before or index_hashes(index) != before:
            raise ValueError("Index changed while publishing cache")
        metadata = {"schema_version": 1, "status": "completed", "identity": identity,
                    "preflight_passed": True, "index_files": before,
                    "original_build_info": original_build_info}
        (temporary / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        (temporary / "COMPLETE").write_text(digest(metadata) + "\n")
        entry = cache_root / digest(identity)
        if entry.is_symlink():
            entry.unlink()
        elif entry.exists():
            shutil.rmtree(entry)
        os.replace(temporary, entry)
        temporary = None
        result = {"status": "saved", "key": digest(identity)}
    except (OSError, ValueError, TypeError) as error:
        result = {"status": "not_saved", "reason": type(error).__name__ + ": " + str(error)}
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    return {**result, "wall_seconds": round(time.monotonic() - started, 6)}
