#!/usr/bin/env python3
"""Plan opt-in CI execution and exact persona cache identities without network I/O."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
from typing import Any
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
RANGE_FORMAT_VERSION = 1
RANGE_BLOCK_BYTES = 16 * 1024 * 1024
RANGE_CACHE_BYTES = 4 * 1024 * 1024 * 1024
INDEX_FORMAT_VERSION = 1
INDEX_MAX_FILE_SIZE_BYTES = 1024 * 1024


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot read CI dependency: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Share the inner cache's build dependencies; no repository HEAD or image timestamp.
_SEED_CACHE = load_module("workspace_qa_ci_seed_cache", HERE / "seed_cache.py")
INDEX_DEPENDENCIES = _SEED_CACHE.BUILD_FILES


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def execution_scope(event: dict[str, Any], event_name: str | None = None) -> str:
    """Commit messages remain inert data; only the HEAD commit opts a push in."""
    if event_name is None:
        event_name = "workflow_dispatch" if "inputs" in event else "push" if "head_commit" in event else "unknown"
    if event_name == "workflow_dispatch":
        inputs = event.get("inputs")
        if not isinstance(inputs, dict) or inputs.get("scope") not in {"validate", "probe", "rejudge", "smoke", "full"}:
            raise ValueError("workflow_dispatch requires scope validate, probe, rejudge, smoke, or full")
        return inputs["scope"]
    if event_name != "push":
        return "validate"
    head = event.get("head_commit")
    message = head.get("message", "") if isinstance(head, dict) else ""
    if not isinstance(message, str):
        raise ValueError("Push HEAD commit message must be text")
    # Full includes smoke and therefore takes precedence when both are present.
    if "[workspace-qa-full]" in message:
        return "full"
    if "[workspace-qa-smoke]" in message:
        return "smoke"
    if "[workspace-qa-probe]" in message:
        return "probe"
    if "[workspace-qa-rejudge]" in message:
        return "rejudge"
    return "validate"


def digest_object(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{label} must be nonempty text without control characters")
    return value


def cache_plan(lock: dict[str, Any], task_id: str, cache_root: Path, *,
               repository: Path = REPOSITORY, persona_roots: dict[str, str] | None = None,
               runner_os: str | None = None, runner_arch: str | None = None,
               embedding_endpoint: str | None = None,
               runtime_identity: dict[str, Any] | None = None) -> dict[str, Any]:
    tasks = [task for task in lock.get("tasks", []) if isinstance(task, dict) and task.get("task_id") == task_id]
    if len(tasks) != 1:
        raise ValueError("task-id must match exactly one locked task")
    if persona_roots is None:
        persona_roots = load_module("workspace_qa_ci_dataset", repository / "benchmarks/workspace-qa/dataset.py").PERSONA_ROOTS
    persona = required_string(tasks[0].get("persona"), "persona")
    persona_root = persona_roots.get(persona)
    if not isinstance(persona_root, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", persona_root):
        raise ValueError("persona is missing a safe dataset.PERSONA_ROOTS entry")
    workspace = lock.get("workspace")
    if not isinstance(workspace, dict):
        raise ValueError("lock requires workspace archive identity")
    archive = {key: required_string(workspace.get(key), "workspace." + key) for key in ("repo", "revision", "archive", "sha256")}
    size = workspace.get("size_bytes")
    if type(size) is not int or size <= 0 or not re.fullmatch(r"[0-9a-fA-F]{64}", archive["sha256"]):
        raise ValueError("workspace requires positive archive size and SHA-256")
    archive["size_bytes"] = size
    system = required_string(runner_os or os.environ.get("RUNNER_OS") or platform.system(), "runner OS")
    arch = required_string(runner_arch or os.environ.get("RUNNER_ARCH") or platform.machine(), "runner architecture")
    range_identity = {"format_version": RANGE_FORMAT_VERSION, "archive": archive,
                      "persona": persona, "persona_root": persona_root, "runner_os": system, "runner_arch": arch,
                      "block_bytes": RANGE_BLOCK_BYTES, "cache_budget_bytes": RANGE_CACHE_BYTES}
    experiment = lock.get("experiment", {})
    version = required_string(experiment.get("zg_version"), "experiment.zg_version")
    if version != "0.2.2":
        raise ValueError("This benchmark requires zg 0.2.2")
    embedding = experiment.get("embedding", {})
    model = required_string(embedding.get("model"), "embedding model")
    configured_cap = experiment.get("index", {}).get("max_file_size_bytes")
    if type(configured_cap) is not int or configured_cap != INDEX_MAX_FILE_SIZE_BYTES:
        raise ValueError("This benchmark requires the uniform 1 MiB index file cap")
    if embedding_endpoint is None:
        settings = load_module("workspace_qa_ci_settings", repository / "benchmarks/swe-qa-bench/zg_bench/settings.py")
        embedding_endpoint = os.environ.get("QWEN_EMBEDDING_ENDPOINT", settings.ZVEC_GREP_EMBEDDING_ENDPOINT).strip()
    endpoint = required_string(embedding_endpoint, "embedding endpoint")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Embedding endpoint must be HTTP(S) without credentials, query, or fragment")
    if runtime_identity is not None:
        versions = runtime_identity.get("installed_versions", {})
        for key in ("zg", "qoder", "node"):
            required_string(versions.get(key), "installed runtime " + key)
        if versions["zg"] != version:
            raise ValueError("Installed zg version differs from lock")
        required_string(runtime_identity.get("os"), "runtime OS")
        required_string(runtime_identity.get("architecture"), "runtime architecture")
        build = _SEED_CACHE.build_identity(runtime_identity, repository)
    else:
        build = {"installed_versions": None, "os": None, "architecture": None,
                 "build_files": {relative: hashlib.sha256((repository / relative).read_bytes()).hexdigest() for relative in INDEX_DEPENDENCIES}}
    index_identity = {"format_version": INDEX_FORMAT_VERSION, "range_identity": range_identity,
                      "protocol": experiment.get("protocol"),
                      "zg_version": version, "embedding_model": model, "embedding_endpoint": endpoint,
                      "embedding_type": embedding.get("type"), "allow_local_fallback": embedding.get("allow_local_fallback"),
                      "max_file_size_bytes": configured_cap, "source_mount": "/app",
                      "corpus_policy": experiment.get("corpus_policy"), "build": build}
    root = cache_root.expanduser().resolve()
    required_string(str(root), "cache-root")
    prefix = "workspace-qa-" + persona_root.lower()
    return {"task_id": required_string(task_id, "task-id"), "persona": persona, "persona_root": persona_root,
            "range_cache_key": prefix + "-range-v1-" + digest_object(range_identity),
            "range_cache_path": str(root / "range" / persona_root), "range_cache_bytes": RANGE_CACHE_BYTES,
            "index_cache_key": prefix + "-index-v1-" + digest_object(index_identity),
            "index_cache_path": str(root / "index" / persona_root),
            "index_runtime_bound": "true" if runtime_identity is not None else "false"}


def write_outputs(path: Path, outputs: dict[str, Any]) -> None:
    lines = []
    for key, value in outputs.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError("Invalid GitHub output name")
        lines.append(key + "=" + required_string(str(value), "GitHub output") + "\n")
    with path.open("a", encoding="utf-8") as stream:
        stream.writelines(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id")
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--lock", type=Path, default=HERE / "data/lock.json")
    parser.add_argument("--runtime-identity", type=Path, help="runner.runtime_identity(image) JSON; pass after Docker build for exact index restore/save")
    parser.add_argument("--event-path", type=Path)
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME"))
    parser.add_argument("--github-output", type=Path, default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args(argv)
    try:
        if args.task_id is not None:
            if args.cache_root is None or args.event_path is not None:
                raise ValueError("Cache mode requires --cache-root and cannot use --event-path")
            outputs = cache_plan(read_object(args.lock), args.task_id, args.cache_root,
                                 runtime_identity=read_object(args.runtime_identity) if args.runtime_identity else None)
        else:
            if args.cache_root is not None or args.runtime_identity is not None:
                raise ValueError("Cache options require --task-id")
            event_path = args.event_path or (Path(os.environ["GITHUB_EVENT_PATH"]) if os.environ.get("GITHUB_EVENT_PATH") else None)
            if event_path is None:
                raise ValueError("Scope mode requires --event-path or GITHUB_EVENT_PATH")
            outputs = {"scope": execution_scope(read_object(event_path), args.event_name)}
        if args.github_output:
            write_outputs(args.github_output, outputs)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"CI planning failed: {exc}\n")
    print(json.dumps(outputs, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
