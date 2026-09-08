"""A frozen-index, read-only, single-case QA experiment (five runs per arm).

The Docker image contains released zg 0.2.2. No gold or benchmark repository is
mounted in the agent. The corpus is read-only; each query process gets an isolated
copy of the prepared index, checked against all stored documents and vectors.
By default each experiment builds its index once before measured queries begin.
The original index seed is never mounted in a container. The existing Harbor
OpenCode converter is reused only to preserve the established ATIF usage format.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..settings import OPENCODE_CUSTOM_BASE_URL

PACKAGE = "@zvec/zvec-grep@0.2.2"
EMBEDDING = "local/potion-code-16m-v2"
PACKAGE_DIR = "/opt/qa/node_modules/@zvec/zvec-grep"
BRIDGE = "/opt/qa/readonly-search.mjs"
PREPARE_INDEX = "/opt/qa/prepare-index.mjs"
PROFILES = ("baseline", "zvec-grep")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def redact(value: str) -> str:
    for key in ("GLM_API_KEY", "OPENAI_API_KEY", "QODER_PERSONAL_ACCESS_TOKEN", "QWEN_API_KEY"):
        secret = os.environ.get(key)
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def run_checked(command: list[str], *, diagnostic_path: Path | None = None, **kwargs: Any) -> str:
    try:
        result = subprocess.run(command, check=True, text=True, capture_output=True, **kwargs)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = error.stderr or ""
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        detail = redact(detail)[-16000:]
        if diagnostic_path:
            write_json(diagnostic_path, {"status": "failed", "kind": type(error).__name__, "stderr": detail})
        raise RuntimeError(f"{command[0]} failed ({type(error).__name__}): {detail}") from None
    return result.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def directory_identity(root: Path, *, skip_git: bool = False) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if skip_git and relative.parts[0] == ".git":
            continue
        if path.is_symlink():
            result[relative.as_posix()] = "symlink:" + os.readlink(path)
        elif path.is_file():
            result[relative.as_posix()] = sha256(path)
    return result


def working_index(seed: Path, destination: Path) -> Path:
    """Native read-only open can change storage headers; never expose the seed."""
    shutil.copytree(seed, destination, symlinks=True)
    (destination / "locks").mkdir(exist_ok=True)
    for path in [destination, *destination.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | 0o200)
    return destination


def physical_changes(before: dict[str, str], after: dict[str, str]) -> list[dict[str, str | None]]:
    return [{"path": path, "before": before.get(path), "after": after.get(path)}
            for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)]


def make_plan(case_id: str, repetitions: int = 5, seed: int = 1729) -> dict[str, Any]:
    if repetitions != 5:
        raise ValueError("This frozen protocol requires exactly five trials per profile")
    rng = random.Random(seed)
    trials = []
    for repetition in range(1, repetitions + 1):
        order = list(PROFILES)
        rng.shuffle(order)
        for profile in order:
            trial_id = f"{case_id}-r{repetition:02d}-{profile}"
            trials.append({
                "trial_id": trial_id, "profile": profile, "repetition": repetition,
                "trajectory_path": f"{trial_id}/agent/trajectory.json", "status": "planned",
            })
    return {"schema_version": 1, "case_id": case_id, "repetitions_per_profile": repetitions,
            "order_seed": seed, "trials": trials}


def choose_seed(seed_dir: Path, commit: str) -> tuple[Path, dict[str, Any]]:
    """Match content/config identity, not the old agent or npm package cache key."""
    candidates = []
    for identity_path in sorted(seed_dir.rglob("identity.json")):
        try:
            identity = json.loads(identity_path.read_text())
        except (OSError, ValueError):
            continue
        workspace = identity_path.parent / "workspace"
        complete = identity_path.parent / "complete"
        if (identity.get("repo_commit") == commit
                and identity.get("embedding_model") == EMBEDDING
                and identity.get("workdir") == "/app"
                and workspace.is_dir() and complete.is_file()):
            key = hashlib.sha256(json.dumps(identity, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
            if complete.read_text().strip() != key:
                continue
            candidates.append((workspace, identity))
    if not candidates:
        raise RuntimeError("No matching EXISTING index seed for --seed-dir; omit it to prepare a new index")
    return candidates[0]


def common_config(model: str, *, zg: bool) -> dict[str, Any]:
    model_id = model.split("/", 1)[-1]
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json", "model": model,
        "autoupdate": False, "share": "disabled", "lsp": False,
        "provider": {"custom-openai": {
            "npm": "@ai-sdk/openai-compatible",
            "options": {"apiKey": "{env:OPENAI_API_KEY}", "baseURL": OPENCODE_CUSTOM_BASE_URL},
            "models": {model_id: {"name": model_id}},
        }},
        "permission": {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow", "list": "allow"},
    }
    if zg:
        config["permission"]["zvec_grep_*"] = "allow"
        config["mcp"] = {"zvec_grep": {
            "type": "local", "enabled": True, "timeout": 600000,
            "command": ["node", BRIDGE, "serve", "--root", "/app", "--package-dir", PACKAGE_DIR,
                        "--embedding-model", EMBEDDING, "--model-cache-dir", "/models", "--snapshot", "/run/qa/snapshot.json",
                        "--working-copy", "--log", "/logs/zg-trace.jsonl"],
        }}
    return config


def mount(source: Path, target: str, *, readonly: bool = True) -> list[str]:
    spec = f"type=bind,source={source.resolve()},target={target}"
    if readonly:
        spec += ",readonly"
    return ["--mount", spec]


def docker_command(image: str, source: Path, output: Path, cache: Path,
                   *, index: Path | None = None, snapshot: Path | None = None) -> list[str]:
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    command = ["docker", "run", "--rm", "--cpus", "4", "--memory", "8g", "--pids-limit", "512",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--workdir", "/app"]
    command += mount(source, "/app") + mount(output, "/logs", readonly=False)
    command += mount(cache, "/models", readonly=False)
    command += ["--env", "ZVEC_GREP_MODEL_CACHE=/models"]
    if index:
        command += mount(index, "/app/.zvec-grep", readonly=False)
        # Filesystem read locks are coordination state, not corpus/vector data.
        command += ["--tmpfs", "/app/.zvec-grep/locks:rw,mode=1777"]
    if snapshot:
        command += mount(snapshot, "/run/qa/snapshot.json")
    return command


def convert_trace(agent_dir: Path, model: str, instruction: str) -> dict[str, Any]:
    from harbor.agents.installed.opencode import OpenCode
    events = []
    for line in (agent_dir / "opencode.txt").read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except ValueError:
            pass
    adapter = OpenCode(logs_dir=agent_dir, model_name=model, version="1.18.4")
    adapter._instruction = instruction
    trajectory = adapter._convert_events_to_trajectory(events)
    if trajectory is None:
        raise RuntimeError("No complete OpenCode trajectory in the native event stream")
    write_json(agent_dir / "trajectory.json", trajectory.model_dump(mode="json", exclude_none=True))
    errors = [event for event in events if event.get("type") == "error"]
    from .readonly_judge import extract_final_answer
    has_final_answer = extract_final_answer(trajectory.model_dump(mode="json", exclude_none=True)) is not None
    return {"event_count": len(events), "error_event_count": len(errors),
            "has_final_answer": has_final_answer,
            "final_metrics": trajectory.final_metrics.model_dump(mode="json", exclude_none=True)}


def wire_contract(agent_dir: Path, model: str, expected: list[str]) -> dict[str, Any]:
    """Check effective task request settings, not just the intended config."""
    path = agent_dir / "wire.jsonl"
    if not path.is_file():
        return {"valid": False, "reason": "provider_trace_missing"}
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    requests = [e for e in events if e.get("event") == "request"]
    responses = {e["request_id"]: e for e in events if e.get("event") == "response"}
    task_requests = [e for e in requests if e.get("tool_names")]
    observed_models = sorted({e.get("model") for e in responses.values() if e.get("model")})
    temperatures = [e.get("temperature") for e in task_requests]
    catalogs = [sorted(e["tool_names"]) for e in task_requests]
    identity_ok = bool(requests) and all(
        responses.get(e["request_id"], {}).get("model", "").lower() == model.lower()
        and responses.get(e["request_id"], {}).get("status") == 200 for e in requests)
    catalog_ok = bool(task_requests) and all(set(catalog) == set(expected) for catalog in catalogs)
    temperature_ok = bool(temperatures) and all(t == 0 for t in temperatures)
    # A transport interruption is an experimental failure, not evidence that
    # the whole agent/model combination has a deterministically wrong setup.
    model_mismatch = any(value.lower() != model.lower() for value in observed_models)
    configuration_mismatch = (model_mismatch or any(set(c) != set(expected) for c in catalogs)
                              or any(t != 0 for t in temperatures))
    return {"valid": identity_ok and catalog_ok and temperature_ok,
            "configuration_mismatch": configuration_mismatch,
            "provider_model_valid": identity_ok, "requested_model": model,
            "observed_models": observed_models, "tool_catalog_valid": catalog_ok,
            "expected_tools": sorted(expected), "observed_catalogs": catalogs,
            "temperature_zero_verified": temperature_ok, "observed_task_temperatures": temperatures,
            "provider_request_count": len(requests), "task_requests_with_tools": len(task_requests),
            "limitation": "Provider-reported model alias is verified; exact hosted weight revision is not exposed."}


def execute_experiment(args: argparse.Namespace) -> int:
    case = json.loads(args.case.read_text())
    controlled = getattr(args, "controlled", False)
    agent_name = getattr(args, "agent", "opencode")
    spec = None
    limits = {"model_requests": 30, "tool_calls": 60, "input_tokens": 300000, "wall_seconds": args.timeout}
    if controlled:
        from .readonly_agents import (agent_spec, agent_environment, build_agent_config,
            build_agent_command, control_manifest, convert_agent_trace, expected_tools)
        spec = agent_spec(agent_name, args.model, base_url=OPENCODE_CUSTOM_BASE_URL if agent_name == "opencode" else None)
    elif agent_name != "opencode":
        raise ValueError("Qoder requires the controlled protocol")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output must be new/empty; historical results are never overwritten")
    output.mkdir(parents=True, exist_ok=True)
    plan = make_plan(case["case_id"], args.repetitions, args.order_seed)
    if controlled:
        plan.update(protocol="readonly-qa-v3", agent=spec.name, model=spec.model, run_limits=limits,
                    intent="single-case development experiment; no population-level efficacy inference")
        for trial in plan["trials"]:
            trial.update(agent=spec.name, model=spec.model, block_id=trial["repetition"])
    write_json(output / "plan.json", plan)
    retrieval_output = output / "retrieval"
    retrieval_output.mkdir()
    (retrieval_output / "events.jsonl").touch()
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    credential_env = "QODER_PERSONAL_ACCESS_TOKEN" if agent_name == "qodercli" else "GLM_API_KEY"
    if not os.environ.get(credential_env):
        write_json(output / "preflight.json", {"status": "blocked", "reason": "credential_unavailable", "required_env": credential_env})
        raise RuntimeError(f"{credential_env} is required (never pass credentials as command arguments)")
    seed_option = getattr(args, "seed_dir", None)
    seed, seed_identity = choose_seed(seed_option.resolve(), case["repo"]["commit"]) if seed_option else (None, None)
    prepared = output / "preparation"
    prepared.mkdir()
    source = prepared / "source"
    run_checked(["git", "init", str(source)])
    run_checked(["git", "-C", str(source), "fetch", "--depth=1", case["repo"]["url"], case["repo"]["commit"]])
    run_checked(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"])
    if run_checked(["git", "-C", str(source), "rev-parse", "HEAD"]) != case["repo"]["commit"]:
        raise RuntimeError("Checked out commit does not match case")
    entries_path = getattr(args, "entries", None)
    entries = None
    if entries_path:
        from .retrieval_eval import load_manifest
        entries = load_manifest(entries_path, source_root=source)
        if entries["repo"] != case["repo"] or entries["source_case_sha256"] != sha256(args.case):
            raise ValueError("Entry annotations and QA source identities differ")
        if next(q["text"] for q in entries["queries"] if q["query_id"] == "original") != case["question"]:
            raise ValueError("Primary retrieval query differs from the original QA question")
    (source / ".zvec-grep").mkdir(exist_ok=True)
    index = prepared / "index"
    source_before = directory_identity(source, skip_git=True)
    model_cache = prepared / "model-cache"
    preparation_logs = prepared / "runtime"
    preparation = {"mode": "existing_seed" if seed else "build_once", "started_at": datetime.now(UTC).isoformat(),
                   "included_in_qa_tokens_or_toolcalls": False, "cross_ci_cache": False, "status": "running"}
    write_json(preparation_logs / "preparation.json", preparation)
    preparation_start = time.monotonic()
    if seed:
        shutil.copytree(seed, index, symlinks=True)
    else:
        index.mkdir()
        (index / "locks").mkdir()
        build = docker_command(args.image, source, preparation_logs, model_cache, index=index)
        build_name = "zgqa-prepare-" + hashlib.sha256(str(output).encode()).hexdigest()[:12]
        build += ["--name", build_name, args.image, "node", PREPARE_INDEX,
                  "--root", "/app", "--package-dir", PACKAGE_DIR,
                  "--embedding-model", EMBEDDING, "--model-cache-dir", "/models",
                  "--log", "/logs/index-build.json"]
        print(json.dumps({"phase": "index_preparation", "status": "running", "package": PACKAGE}), flush=True)
        try:
            build_stdout = run_checked(build, timeout=1800, diagnostic_path=preparation_logs / "index-build-failure.json")
            (preparation_logs / "index-build.stdout.txt").write_text(redact(build_stdout))
        except RuntimeError:
            preparation.update(status="failed", wall_seconds=round(time.monotonic() - preparation_start, 3))
            write_json(preparation_logs / "preparation.json", preparation)
            try:
                subprocess.run(["docker", "rm", "--force", build_name], capture_output=True, timeout=30)
            except (subprocess.SubprocessError, OSError) as cleanup_error:
                preparation["cleanup_error"] = type(cleanup_error).__name__
                write_json(preparation_logs / "preparation.json", preparation)
            raise
        if directory_identity(source, skip_git=True) != source_before:
            preparation.update(status="integrity_failure", wall_seconds=round(time.monotonic() - preparation_start, 3))
            write_json(preparation_logs / "preparation.json", preparation)
            raise RuntimeError("Source changed during index preparation")
        seed = index
        seed_identity = {"format_version": 1, "repo_commit": case["repo"]["commit"],
                         "embedding_model": EMBEDDING, "workdir": "/app", "zvec_grep_package": PACKAGE,
                         "origin": "built_once_in_this_experiment"}
    preparation.update(status="completed", wall_seconds=round(time.monotonic() - preparation_start, 3),
                       finished_at=datetime.now(UTC).isoformat())
    write_json(preparation_logs / "preparation.json", preparation)
    print(json.dumps({"phase": "index_preparation", **preparation}), flush=True)
    index_before = directory_identity(index)
    seed_before = directory_identity(seed)
    working_root = prepared / "working-indexes"
    snapshot = prepared / "snapshot.json"
    preflight_index = working_index(index, working_root / "preflight")
    base = docker_command(args.image, source, preparation_logs, model_cache, index=preflight_index)
    query_flags = ["--root", "/app", "--package-dir", PACKAGE_DIR, "--embedding-model", EMBEDDING,
                   "--model-cache-dir", "/models", "--working-copy"]
    run_checked(base + [args.image, "node", BRIDGE, "preflight", *query_flags,
                        "--snapshot", "/logs/snapshot.json", "--log", "/logs/preflight.jsonl"], timeout=900,
                diagnostic_path=preparation_logs / "preflight-failure.json")
    shutil.copyfile(preparation_logs / "snapshot.json", snapshot)
    image_identity = json.loads(run_checked(["docker", "image", "inspect", args.image]))[0]
    manifest = {"protocol": "readonly-qa-v2", "created_at": datetime.now(UTC).isoformat(),
                "case_sha256": sha256(args.case), "case_id": case["case_id"], "repo": case["repo"],
                "package": PACKAGE, "embedding_model": EMBEDDING, "agent": "opencode", "agent_version": "1.18.4",
                "model": args.model, "provider_url": OPENCODE_CUSTOM_BASE_URL,
                "image_id": image_identity["Id"], "image_repo_digests": image_identity.get("RepoDigests", []),
                "seed_identity": seed_identity, "source_files": source_before, "index_files": index_before,
                "index_preparation": preparation, "index_build_scope": "preparation_only",
                "repetitions_per_profile": 5, "order_seed": args.order_seed,
                "corpus_readonly_mount": True, "index_readonly_mount": False, "index_build_allowed": False,
                "index_policy": "immutable original seed; isolated writable copy per mode/trial; verify every stored document and vector",
                "gold_visible_to_agent": False, "selection_runs_reused_as_treatment_control": False,
                "cache_policy": "shared local embedding weights; fresh agent container/session/config per trial"}
    if controlled:
        manifest.update(protocol="readonly-qa-v3", agent=spec.name, agent_version=spec.version,
                        model=spec.model, provider_url=spec.base_url, agent_spec=spec.to_dict(),
                        run_limits=limits, controls=control_manifest(spec, max_model_turns=limits["model_requests"]),
                        tool_instruction_policy="Exact registered tool identifiers listed equally for both arms; no forced tool or query strategy.")
        manifest["ci_identity"] = {key: os.environ.get(key) for key in
            ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW", "RUNNER_OS", "RUNNER_ARCH")}
    write_json(output / "manifest.json", manifest)
    query_plan = [{"query_id": "original", "text": case["question"]}]
    if entries_path:
        query_plan = entries["queries"]
        manifest["entries_sha256"] = sha256(entries_path)
        write_json(output / "entries.json", entries)
        write_json(output / "manifest.json", manifest)
    retrieval_failures = []
    for query, mode in [(query, mode) for query in query_plan for mode in ("fts", "vector", "hybrid")]:
        query_id = query["query_id"]
        prefix = mode if not entries_path else query_id + "-" + mode
        retrieval_index = working_index(index, working_root / f"retrieval-{prefix}")
        retrieval_base = docker_command(args.image, source, retrieval_output, model_cache, index=retrieval_index, snapshot=snapshot)
        command = retrieval_base + [args.image, "node", BRIDGE, "retrieve", *query_flags,
                                   "--snapshot", "/run/qa/snapshot.json", "--log", "/logs/events.jsonl",
                                   "--query", query["text"], "--mode", mode, "--repetitions", "5", "--limit", "10"]
        retrieve_name = "zgqa-retrieve-" + hashlib.sha256((str(output) + prefix).encode()).hexdigest()[:16]
        command[2:2] = ["--name", retrieve_name]
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=1200)
            (retrieval_output / f"{prefix}.stdout.jsonl").write_text(redact(result.stdout))
            (retrieval_output / f"{prefix}.stderr.txt").write_text(redact(result.stderr))
            write_json(retrieval_output / f"{prefix}.status.json", {"status": "completed" if result.returncode == 0 else "failed", "returncode": result.returncode, "query_id": query_id})
            if result.returncode:
                retrieval_failures.append(prefix)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", retrieve_name], capture_output=True, timeout=30)
            write_json(retrieval_output / f"{prefix}.status.json", {"status": "timeout", "query_id": query_id})
            retrieval_failures.append(prefix)
        if directory_identity(source, skip_git=True) != source_before or directory_identity(index) != index_before or directory_identity(seed) != seed_before:
            raise RuntimeError("Corpus/original index integrity failure; remaining planned trials retained")
    manifest["embedding_weight_files_after_retrieval"] = directory_identity(model_cache)
    manifest["retrieval_failures"] = retrieval_failures
    write_json(output / "manifest.json", manifest)
    # No source hints or human gold are added to the natural QA prompt.
    instruction = (case["question"] + "\n\nAnswer using the repository at /app. This is a read-only QA task. "
                   "Support the important claims with source file paths and line references. Do not modify files.")
    write_json(output / "instruction.json", {"text": instruction})
    for trial in plan["trials"]:
        trial_root = output / trial["trial_id"]
        agent_dir = trial_root / "agent"
        agent_dir.mkdir(parents=True)
        zg = trial["profile"] == "zvec-grep"
        trial_instruction = instruction
        config = trial_root / (spec.config_filename if controlled else "opencode.json")
        if controlled:
            mcp_command = ["node", BRIDGE, "serve", *query_flags, "--snapshot", "/run/qa/snapshot.json", "--log", "/logs/zg-trace.jsonl"]
            write_json(config, build_agent_config(spec, zg=zg, mcp_command=mcp_command if zg else None,
                       max_model_turns=limits["model_requests"]))
            trial_instruction += "\n\nTools registered for this session: " + ", ".join(expected_tools(spec, zg=zg)) + ". Use their exact identifiers when making tool calls."
            write_json(trial_root / "instruction.json", {"text": trial_instruction, "sha256": hashlib.sha256(trial_instruction.encode()).hexdigest()})
        else:
            write_json(config, common_config(args.model, zg=zg))
        trial_index = working_index(index, working_root / trial["trial_id"]) if zg else None
        trial_index_before = directory_identity(trial_index) if trial_index else None
        command = docker_command(args.image, source, agent_dir, model_cache,
                                 index=trial_index, snapshot=snapshot if zg else None)
        name = "zgqa-" + hashlib.sha256(str(trial_root).encode()).hexdigest()[:16]
        if controlled:
            config_mount = "/run/qa/" + spec.config_filename
            command += ["--name", name, "--env", spec.credential_env]
            command += mount(config, config_mount)
            session_spec = {"command": build_agent_command(spec, trial_instruction, config_path=config_mount, zg=zg,
                                                            max_model_turns=limits["model_requests"]),
                            "env": agent_environment(spec, config_path=config_mount), "config_path": config_mount,
                            "limits": limits, "native_name": spec.stream_filename, "log_dir": "/logs",
                            "tap_upstream": spec.base_url if spec.name == "opencode" else None}
            write_json(agent_dir / "session-spec.json", session_spec)
            command += [args.image, "python3", "/opt/qa/qa-session.py", "--spec", "/logs/session-spec.json"]
            env = {**os.environ, spec.credential_env: os.environ[credential_env]}
        else:
            command += ["--name", name, "--env", "OPENAI_API_KEY", "--env", "OPENCODE_CONFIG=/run/qa/opencode.json"]
            command += mount(config, "/run/qa/opencode.json")
            command += [args.image, "opencode", "--model", args.model, "run", "--format", "json", "--thinking", "--", instruction]
            env = {**os.environ, "OPENAI_API_KEY": os.environ["GLM_API_KEY"]}
        started = time.monotonic()
        trial["status"] = "running"
        write_json(output / "plan.json", plan)
        result_data: dict[str, Any] = {"trial_id": trial["trial_id"], "profile": trial["profile"], "repetition": trial["repetition"],
            "started_at": datetime.now(UTC).isoformat(), "agent_info": {"name": spec.name if controlled else "opencode", "version": spec.version if controlled else "1.18.4", "model_name": spec.model if controlled else args.model}}
        with (agent_dir / ("launcher.stdout.txt" if controlled else "opencode.txt")).open("w") as stdout, (agent_dir / ("launcher.stderr.txt" if controlled else "stderr.txt")).open("w") as stderr:
            try:
                process = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=env)
                returncode = process.wait(timeout=args.timeout + 30 if controlled else args.timeout)
                result_data["returncode"] = returncode
                trial["status"] = "completed" if returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
                process.kill()
                process.wait()
                trial["status"] = "timeout"
                result_data["error"] = "trial timeout"
            except OSError as error:
                trial["status"] = "launch_failure"
                result_data["error"] = type(error).__name__
        result_data["wall_seconds"] = round(time.monotonic() - started, 3)
        if controlled and (agent_dir / "session.json").is_file():
            session_result = json.loads((agent_dir / "session.json").read_text())
            result_data["session"] = session_result
            if session_result["status"] != "completed":
                trial["status"] = session_result["status"]
        try:
            result_data.update(convert_agent_trace(agent_dir, spec, trial_instruction, zg=zg) if controlled else convert_trace(agent_dir, args.model, instruction))
            if result_data["error_event_count"] and trial["status"] == "completed":
                trial["status"] = "failed"
            if not result_data["has_final_answer"] and trial["status"] == "completed":
                trial["status"] = "protocol_failure"
            if controlled and result_data.get("contract_error_count", 0):
                trial["status"] = "contract_failure"
            if controlled and spec.name == "opencode":
                result_data["wire_contract"] = wire_contract(agent_dir, spec.provider_model, expected_tools(spec, zg=zg))
                if not result_data["wire_contract"]["valid"]:
                    if result_data["wire_contract"].get("configuration_mismatch"):
                        trial["status"] = "contract_failure"
                    elif trial["status"] == "completed":
                        trial["status"] = "measurement_failure"
        except Exception as error:
            trial["status"] = "failed" if trial["status"] == "completed" else trial["status"]
            result_data["conversion_error"] = str(error)
        source_ok = directory_identity(source, skip_git=True) == source_before
        seed_ok = directory_identity(index) == index_before and directory_identity(seed) == seed_before
        semantic_ok = None
        if trial_index:
            verification_logs = trial_root / "index-verification"
            verification = docker_command(args.image, source, verification_logs, model_cache,
                                          index=trial_index, snapshot=snapshot)
            try:
                run_checked(verification + [args.image, "node", BRIDGE, "verify", *query_flags,
                    "--snapshot", "/run/qa/snapshot.json", "--log", "/logs/verification.jsonl"], timeout=900,
                    diagnostic_path=verification_logs / "failure.json")
                semantic_ok = True
            except RuntimeError as error:
                semantic_ok = False
                result_data["index_verification_error"] = str(error)
            result_data["working_index_physical_changes"] = physical_changes(trial_index_before, directory_identity(trial_index))
        index_ok = seed_ok and semantic_ok is not False
        result_data.update(status=trial["status"], source_unchanged=source_ok, index_unchanged=index_ok,
                           original_seed_unchanged=seed_ok, working_index_semantic_unchanged=semantic_ok,
                           index_integrity_definition="original seed bytes and all working-copy documents/vectors; physical working-copy drift reported separately",
                           finished_at=datetime.now(UTC).isoformat())
        if not source_ok or not index_ok:
            trial["status"] = result_data["status"] = "integrity_failure"
        write_json(trial_root / "result.json", result_data)
        write_json(output / "plan.json", plan)
        print(json.dumps({"trial_id": trial["trial_id"], "status": trial["status"], "wall_seconds": result_data["wall_seconds"]}), flush=True)
        if controlled and trial["status"] in {"contract_failure", "launch_failure"}:
            write_json(output / "preflight.json", {"status": "failed", "trial_id": trial["trial_id"],
                       "reason": trial["status"], "remaining_trials": "retained as planned; deterministic contract failure stops this combination"})
            break
        if not source_ok or not index_ok:
            raise RuntimeError("Corpus/index integrity failure; remaining planned trials retained")
    manifest["embedding_weight_files_after_e2e"] = directory_identity(model_cache)
    manifest["embedding_weights_unchanged_during_e2e"] = manifest["embedding_weight_files_after_retrieval"] == manifest["embedding_weight_files_after_e2e"]
    write_json(output / "manifest.json", manifest)
    return 0 if all(t["status"] == "completed" for t in plan["trials"]) and not retrieval_failures else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--seed-dir", type=Path, help="Optional existing seed; omitted by CI to prepare a new index once per experiment")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="zg-readonly-qa:0.2.2")
    parser.add_argument("--model", choices=["custom-openai/glm-5.2", "custom-openai/qwen3.8-max"], default="custom-openai/glm-5.2")
    parser.add_argument("--agent", choices=["opencode", "qodercli"], default="opencode")
    parser.add_argument("--controlled", action="store_true", help="Use v3 native contracts, visible tool identifiers, observed budgets and provider tracing")
    parser.add_argument("--entries", type=Path, help="Frozen entry/query manifest; original question remains the E2E task")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--order-seed", type=int, default=1729)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    return execute_experiment(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
