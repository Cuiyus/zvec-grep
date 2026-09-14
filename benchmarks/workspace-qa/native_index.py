#!/usr/bin/env python3
"""Prepare/check a benchmark seed through the released zg CLI, never its SDK."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

MODEL = "qwen/qwen3.7-text-embedding"
PROTOCOL = "workspace-qa-qoder-native-install-v3"


def redact(text: str) -> str:
    for name in ("QWEN_API_KEY", "GLM_API_KEY", "QODER_PERSONAL_ACCESS_TOKEN"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def commands(root: str, check_only: bool) -> list[list[str]]:
    result = [["zg", "auth", "grant", root, "--capability", "embedding", "--scope", "workspace", "--embedding", MODEL]]
    if not check_only:
        result.append(["zg", "index", root, "--mode", "direct", "--embedding", MODEL,
                       "--max-filesize", "1048576"])
    result.append(["zg", "status", root, "--mode", "direct", "--check-ready"])
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/app"))
    parser.add_argument("--output", type=Path, default=Path("/logs/native-index.json"))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {"protocol": PROTOCOL, "status": "failed", "embedding_model": MODEL,
              "method": "released_zg_cli", "check_only": args.check_only, "commands": []}
    try:
        if not os.environ.get("QWEN_API_KEY"):
            raise RuntimeError("QWEN_API_KEY is required")
        for i, command in enumerate(commands(str(args.root), args.check_only)):
            print(json.dumps({"phase": "native_index_command", "command": command}), flush=True)
            execution_started = time.monotonic()
            process = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     timeout=1750 if command[1] == "index" else 120)
            stdout, stderr = redact(process.stdout), redact(process.stderr)
            stem = args.output.parent / f"native-index-{i + 1}-{command[1]}"
            stem.with_suffix(".stdout.txt").write_text(stdout)
            stem.with_suffix(".stderr.txt").write_text(stderr)
            report["commands"].append({"argv": command, "returncode": process.returncode,
                "wall_seconds": round(time.monotonic() - execution_started, 3)})
            print(stdout[-4000:], flush=True)
            if process.returncode:
                print(stderr[-8000:], flush=True)
                raise RuntimeError(f"Native zg {command[1]} exited {process.returncode}")
        manifest = args.root / ".zvec-grep/manifest.json"
        value = json.loads(manifest.read_text())
        report["index_manifest"] = value
        report["index_manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        embedding = value.get("embedding", {})
        if (embedding.get("provider") != "qwen" or embedding.get("model") not in
                {MODEL, "qwen3.7-text-embedding"} or embedding.get("dimension") != 1024):
            raise RuntimeError("Native index does not record the required remote embedding model")
        if value.get("embeddingRuntime", {}).get("apiKey"):
            raise RuntimeError("A reusable index must not persist provider credentials")
        report.update(status="completed", fresh=True)
    except Exception as error:
        report.update(error_type=type(error).__name__, error=redact(str(error)))
    finally:
        report["wall_seconds"] = round(time.monotonic() - started, 3)
        args.output.write_text(redact(json.dumps(report, ensure_ascii=False, indent=2)) + "\n")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
