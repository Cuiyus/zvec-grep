"""Retained file evidence across Harbor's merged stdout/stderr transport.

These helpers never interpret environment.exec output as machine evidence.
The command writes three private files; every download is attempted before
status or JSON validation. Failed/partial files remain in the host directory.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import time
from typing import Any
import uuid


_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_DOWNLOAD_TIMEOUT_SECONDS = 30


class EvidenceError(RuntimeError):
    def __init__(self, code: str, directory: Path):
        self.code = code
        self.directory = directory
        super().__init__(code)


@dataclass(frozen=True)
class CapturedJSON:
    data: Any
    path: Path
    metadata: dict


def safe_identifier(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_LABEL.fullmatch(value):
        raise ValueError("unsafe_evidence_identifier")
    return value


def _new_directory(host_dir: Path, label: str) -> Path:
    directory = Path(host_dir) / safe_identifier(label)
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.mkdir(mode=0o700)  # Refuse overwrite, including failed attempts.
    return directory


def _write_manifest(directory: Path, manifest: dict) -> None:
    (directory / "capture.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")


async def _download(environment, source: str, target: Path) -> dict:
    result = {"source_path": source, "filename": target.name, "complete": False}
    try:
        await asyncio.wait_for(environment.download_file(source, target),
                               timeout=_DOWNLOAD_TIMEOUT_SECONDS)
        if target.is_symlink() or not target.is_file():
            raise ValueError("download_did_not_produce_regular_file")
        result["complete"] = True
    except Exception as exc:
        # Only explicit evidence paths are downloaded; retain the diagnostic
        # describing a failed transfer as well as any partial destination.
        result["error_type"] = type(exc).__name__
        result["error_message"] = str(exc)
    if target.is_file() and not target.is_symlink():
        raw = target.read_bytes()
        result.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    return result


def _parse(directory: Path, manifest: dict, filename: str) -> CapturedJSON:
    try:
        data = json.loads((directory / filename).read_bytes())
    except (ValueError, UnicodeError) as exc:
        manifest["validation_error"] = "malformed_json"
        manifest["parse_error_type"] = type(exc).__name__
        if isinstance(exc, json.JSONDecodeError):
            manifest["parse_error_position"] = exc.pos
        _write_manifest(directory, manifest)
        raise EvidenceError("malformed_json", directory) from exc
    manifest["valid"] = True
    _write_manifest(directory, manifest)
    return CapturedJSON(data, directory / filename, manifest)


async def capture_json(environment, host_dir: Path, label: str, command: str,
                       timeout_sec: int) -> CapturedJSON:
    """Run one trusted evidence command as root, retain all bytes, then parse.

    The controller remains responsible for stopping the slot's containers on
    timeout. This function still downloads partial files before propagating it.
    Neither the private directory nor helper code enters /workspace.
    """
    if not isinstance(timeout_sec, int) or isinstance(timeout_sec, bool) or timeout_sec <= 0:
        raise ValueError("invalid_capture_timeout")
    directory = _new_directory(host_dir, label)
    remote = "/root/qa-evidence-v2-" + uuid.uuid4().hex
    paths = {name: remote + "/" + name
             for name in ("stdout.json", "stderr.txt", "status.txt")}
    # The inner subshell isolates exit/set -e from the status writer. A failed
    # mkdir must not overwrite an earlier directory. All files inherit 0600.
    script = (
        "set +e; umask 077; mkdir -m 700 -- " + shlex.quote(remote) +
        " || exit 97; ( " + command + "\n) > " + shlex.quote(paths["stdout.json"]) +
        " 2> " + shlex.quote(paths["stderr.txt"]) +
        "; qa_capture_status=$?; printf '%s\\n' \"$qa_capture_status\" > " +
        shlex.quote(paths["status.txt"]) + "; exit 0"
    )
    manifest = {"schema_version": 1, "kind": "command_file_capture", "label": label,
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                "remote_directory": remote, "valid": False, "files": {},
                "timeout_seconds": timeout_sec}
    started = time.monotonic()
    transport = {}
    cancellation = None
    try:
        result = await asyncio.wait_for(
            environment.exec(command=script, timeout_sec=timeout_sec, user="root"),
            timeout=timeout_sec + 5)
        transport = {"return_code": result.return_code,
                     "stdout": result.stdout, "stderr": result.stderr}
    except asyncio.CancelledError as exc:
        cancellation = exc
        transport = {"error_type": type(exc).__name__, "error_message": str(exc)}
    except Exception as exc:
        transport = {"error_type": type(exc).__name__, "error_message": str(exc)}
    finally:
        (directory / "transport.json").write_text(
            json.dumps(transport, indent=2, sort_keys=True) + "\n")
        transport_bytes = (directory / "transport.json").read_bytes()
        manifest["transport_file"] = {
            "filename": "transport.json", "bytes": len(transport_bytes),
            "sha256": hashlib.sha256(transport_bytes).hexdigest()}
        # Deliberately independent attempts: a missing stdout must not discard
        # the stderr/status, and an error does not erase a partial local copy.
        for name, source in paths.items():
            manifest["files"][name] = await _download(environment, source, directory / name)
        manifest["wall_seconds"] = time.monotonic() - started
        manifest["transport"] = transport
        _write_manifest(directory, manifest)
    if cancellation is not None:
        raise cancellation
    if "error_type" in transport or transport.get("return_code") != 0:
        manifest["validation_error"] = "command_transport_failed"
    elif not all(row["complete"] for row in manifest["files"].values()):
        manifest["validation_error"] = "file_capture_incomplete"
    else:
        status = (directory / "status.txt").read_bytes()
        if status != b"0\n":
            manifest["validation_error"] = "command_status_not_zero"
    if "validation_error" in manifest:
        _write_manifest(directory, manifest)
        raise EvidenceError(manifest["validation_error"], directory)
    return _parse(directory, manifest, "stdout.json")


async def download_json(environment, host_dir: Path, label: str,
                        source_path: str) -> CapturedJSON:
    """Download an already-file-backed native report without a cat/exec hop."""
    source = PurePosixPath(source_path)
    if not source.is_absolute() or ".." in source.parts:
        raise ValueError("unsafe_download_source")
    directory = _new_directory(host_dir, label)
    started = time.monotonic()
    record = await _download(environment, source_path, directory / "stdout.json")
    manifest = {"schema_version": 1, "kind": "native_file_download", "label": label,
                "valid": False, "files": {"stdout.json": record},
                "wall_seconds": time.monotonic() - started}
    _write_manifest(directory, manifest)
    if not record["complete"]:
        manifest["validation_error"] = "file_capture_incomplete"
        _write_manifest(directory, manifest)
        raise EvidenceError("file_capture_incomplete", directory)
    return _parse(directory, manifest, "stdout.json")
