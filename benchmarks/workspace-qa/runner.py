#!/usr/bin/env python3
"""Run paired Qoder workspace QA trials using the pinned read-only zg runtime.

The caller supplies a complete frozen workspace and the unmodified task query.
Only final-answer delivery is adapted: the harness saves it as the task's report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

# Reuse the locked benchmark package when invoked directly from this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "swe-qa-bench"))
from zg_bench.swe_qa.readonly_agents import (  # noqa: E402
    QODER_SEARCH_TOOL, agent_environment, agent_spec, build_agent_command,
    build_agent_config, control_manifest, convert_agent_trace, expected_tools,
    read_native_events,
)
from zg_bench.swe_qa.readonly_run import (  # noqa: E402
    BRIDGE, PACKAGE, PACKAGE_DIR, PREPARE_INDEX, directory_identity,
    docker_command, mount, physical_changes, redact, run_checked, sha256,
    working_index, write_json,
)
from zg_bench.swe_qa.e2e_analysis import adapt_qoder  # noqa: E402
from zg_bench.swe_qa.readonly_judge import extract_final_answer  # noqa: E402
from zg_bench.settings import ZVEC_GREP_EMBEDDING_ENDPOINT  # noqa: E402

PROFILES = ("baseline", "with-zg")
PROTOCOL = "workspace-qa-qoder-v1"
MODEL = "qwen3.8-max"
EMBEDDING = "qwen/qwen3.7-text-embedding"
SPEC = agent_spec("qodercli", MODEL)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def positive(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def embedding_endpoint() -> str:
    endpoint = os.environ.get("QWEN_EMBEDDING_ENDPOINT", ZVEC_GREP_EMBEDDING_ENDPOINT).strip()
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Embedding endpoint must be an HTTP(S) URL without credentials, query or fragment")
    return endpoint


def with_embedding_environment(command: list[str], endpoint: str) -> list[str]:
    # Production config.ts reads ZVEC_GREP_ENDPOINT and QWEN_API_KEY. Docker
    # forwards only the named key; its value never enters argv or an artifact.
    return command + ["--env", "QWEN_API_KEY", "--env", "ZVEC_GREP_ENDPOINT=" + endpoint,
                      "--env", "ZG_QA_ALLOW_REMOTE_EMBEDDING=1"]


def identity_digest(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def answer_filename(value: str | None) -> str:
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError("answer filename must be a relative .md or .txt path")
    path = PurePosixPath(value)
    if (path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/"))
            or path.suffix.lower() not in {".md", ".txt"}):
        raise ValueError("answer filename must be a safe relative .md or .txt path")
    return path.as_posix()


def make_plan(task_id: str, repetitions: int, seed: int = 1729) -> dict[str, Any]:
    positive(repetitions, "repetitions")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task_id):
        raise ValueError("task_id must be a safe non-empty identifier")
    # Exactly equal AB/BA blocks for an even N; imbalance at most one for odd N.
    first_arms = [PROFILES[i % 2] for i in range(repetitions)]
    random.Random(f"{seed}:{task_id}").shuffle(first_arms)
    trials = []
    for repetition, first in enumerate(first_arms, 1):
        for profile in (first, next(p for p in PROFILES if p != first)):
            trial_id = f"{task_id}-r{repetition:02d}-{profile}"
            trials.append({"trial_id": trial_id, "task_id": task_id, "profile": profile,
                           "repetition": repetition, "block_id": repetition, "status": "planned",
                           "trajectory_path": f"{trial_id}/agent/trajectory.json"})
    return {"schema_version": 1, "protocol": PROTOCOL, "task_id": task_id,
            "repetitions_per_profile": repetitions, "order_seed": seed,
            "order_policy": "balanced AB/BA blocks, shuffled before execution", "trials": trials}


def instruction(question: str, filename: str, *, zg: bool) -> str:
    return (question + "\n\nThe complete task workspace is available at /app. "
            "This run uses read-only workspace QA: read the files needed to answer the task. "
            "Do not modify the workspace. The harness will save your final response verbatim to "
            + json.dumps(filename, ensure_ascii=False)
            + "; include the complete requested report in your final response. "
            "Use source file references where useful.\n\nTools registered for this session: "
            + ", ".join(expected_tools(SPEC, zg=zg))
            + ". Use their exact identifiers when making tool calls.")


def number(value: Any) -> int | float | None:
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def trial_metrics(agent_dir: Path, conversion: dict[str, Any]) -> dict[str, Any]:
    """Final inclusive counters are primary; per-message sums are an audit only."""
    final = conversion.get("final_metrics") or {}
    hidden = (final.get("extra") or {}).get("token_usage_available") is False
    metrics = {"input_tokens": None if hidden else number(final.get("total_prompt_tokens")),
               "output_tokens": None if hidden else number(final.get("total_completion_tokens")),
               "cached_input_tokens": None if hidden else number(final.get("total_cached_tokens")),
               "tool_calls": None, "zg_tool_calls": None, "tool_calls_successful": None,
               "zg_tool_calls_successful": None, "model_requests": None,
               "answer": None, "native_turn_input_tokens": None}
    path = agent_dir / SPEC.stream_filename
    if path.is_file():
        events, parse = read_native_events(path)
        timeline = adapt_qoder(events)["timeline"]
        calls = [x for x in timeline if x["kind"] == "tool"]
        turns = [x for x in timeline if x["kind"] == "model_turn"]
        if timeline:
            metrics.update(tool_calls=len(calls),
                           zg_tool_calls=sum(x["name"] == QODER_SEARCH_TOOL for x in calls),
                           tool_calls_successful=sum(x["status"] == "completed" for x in calls),
                           zg_tool_calls_successful=sum(x["name"] == QODER_SEARCH_TOOL and x["status"] == "completed" for x in calls),
                           model_requests=len(turns))
        tokens = [x["usage"]["input_tokens"] for x in turns]
        if tokens and all(number(x) is not None for x in tokens):
            metrics["native_turn_input_tokens"] = sum(tokens)
        metrics["native_parse"] = parse
        results = [e for e in events if e.get("type") == "result" and not e.get("parent_tool_use_id")]
        if conversion.get("has_final_answer") and results:
            terminal = results[-1]
            if terminal.get("subtype") == "success" and not terminal.get("is_error") and isinstance(terminal.get("result"), str):
                metrics["answer"] = terminal["result"]
    trajectory_path = agent_dir / "trajectory.json"
    if metrics["answer"] is None and conversion.get("has_final_answer") and trajectory_path.is_file():
        metrics["answer"] = extract_final_answer(json.loads(trajectory_path.read_text()))
    metrics["input_usage_reconciles"] = (
        metrics["input_tokens"] == metrics["native_turn_input_tokens"]
        if metrics["input_tokens"] is not None and metrics["native_turn_input_tokens"] is not None else None)
    metrics["input_token_convention"] = "Qoder native inclusive input_tokens; cache reads are already included and are not added again. Masked or missing usage is null."
    metrics["tool_call_convention"] = "Unique native tool IDs scoped by session and parent; attempted calls include tool errors."
    return metrics


def collect_results(output: Path, plan: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for planned in plan["trials"]:
        result_path = output / planned["trial_id"] / "result.json"
        if result_path.is_file():
            row = json.loads(result_path.read_text())
        else:
            row = {**planned, **dict.fromkeys(("input_tokens", "output_tokens", "cached_input_tokens",
                    "tool_calls", "zg_tool_calls", "wall_seconds", "answer", "candidate_output_path"))}
        rows.append(row)
    report = {"schema_version": 1, "protocol": PROTOCOL, "task_id": plan["task_id"],
              "repetitions_per_profile": plan["repetitions_per_profile"], "trials": rows}
    write_json(output / "trial-results.json", report)
    return report


def runtime_identity(image: str) -> dict[str, Any]:
    metadata = json.loads(run_checked(["docker", "image", "inspect", image]))[0]
    # Check the installed releases, not merely a mutable Docker tag's spelling.
    script = ("const fs=require('node:fs'),cp=require('node:child_process');"
              "const p=JSON.parse(fs.readFileSync('/opt/qa/node_modules/@zvec/zvec-grep/package.json','utf8'));"
              "console.log(JSON.stringify({zg:p.version,qoder:cp.execFileSync('qodercli',['--version'],{encoding:'utf8'}).trim()}))")
    versions = json.loads(run_checked(["docker", "run", "--rm", "--network", "none", image,
                                      "node", "-e", script], timeout=60))
    if versions != {"zg": "0.2.2", "qoder": SPEC.version}:
        raise RuntimeError("Runtime versions differ from zg 0.2.2 / Qoder " + SPEC.version)
    return {"image_id": metadata["Id"], "image_repo_digests": metadata.get("RepoDigests", []),
            "installed_versions": versions}


def cleanup_container(name: str) -> None:
    try:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def run_named(command: list[str], name: str, **kwargs: Any) -> str:
    command[2:2] = ["--name", name]
    try:
        return run_checked(command, **kwargs)
    finally:
        cleanup_container(name)


def execute(args: argparse.Namespace) -> int:
    plan = make_plan(args.task_id, args.repetitions, args.order_seed)
    positive(args.timeout, "timeout")
    filename = answer_filename(args.answer_filename) if args.answer_filename else None
    if not filename and not args.dry_run:
        raise ValueError("--answer-filename is required for an actual run")
    source, output = args.source_root.resolve(), args.output.resolve()
    question_path = args.question_file.resolve()
    if not source.is_dir():
        raise ValueError("source-root must be the prepared complete workspace directory")
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("source and output directories must not contain each other")
    if question_path.is_relative_to(source):
        raise ValueError("question-file must remain outside the corpus")
    question = question_path.read_text(encoding="utf-8")
    if not question.strip():
        raise ValueError("question-file is empty")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output must be new/empty; previous trials are never overwritten")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "plan.json", plan)
    collect_results(output, plan)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    try:
        return _execute(args, plan, source, output, question_path, question, filename)
    except Exception as error:
        write_json(output / "failure.json", {"status": "failed", "error_type": type(error).__name__,
                                              "message": redact(str(error)), "at": now()})
        raise
    finally:
        write_json(output / "plan.json", plan)
        collect_results(output, plan)


def _execute(args: argparse.Namespace, plan: dict[str, Any], source: Path, output: Path,
             question_path: Path, question: str, filename: str) -> int:
    if not os.environ.get(SPEC.credential_env):
        raise RuntimeError(SPEC.credential_env + " is required")
    if not os.environ.get("QWEN_API_KEY"):
        raise RuntimeError("QWEN_API_KEY is required for remote qwen3.7-text-embedding")
    endpoint = embedding_endpoint()
    identity = runtime_identity(args.image)
    commit = run_checked(["git", "-C", str(source), "rev-parse", "HEAD"])
    if not run_checked(["git", "-C", str(source), "ls-files"]):
        raise RuntimeError("Prepared workspace has no git-tracked files")
    empty_index = source / ".zvec-grep"
    if not empty_index.is_dir() or any(empty_index.iterdir()):
        raise RuntimeError("Prepared workspace must have an empty .zvec-grep directory")
    source_before = directory_identity(source, skip_git=True)
    limits = {"model_requests": 60, "tool_calls": 120, "input_tokens": 600000,
              "wall_seconds": args.timeout}
    prepared = output / "preparation"
    index, cache, logs = prepared / "index", prepared / "model-cache", prepared / "runtime"
    index.mkdir(parents=True)
    (index / "locks").mkdir()
    prefix = "workspaceqa-" + hashlib.sha256(str(output).encode()).hexdigest()[:12]
    manifest = {"schema_version": 1, "protocol": PROTOCOL, "task_id": args.task_id,
                "created_at": now(), "package": PACKAGE, "embedding_model": EMBEDDING,
                "embedding_endpoint": endpoint, "embedding_credential_env": "QWEN_API_KEY",
                "agent": SPEC.name, "agent_version": SPEC.version, "model": MODEL,
                "agent_spec": SPEC.to_dict(), "source_git_commit": commit,
                "source_files": source_before, "question_sha256": sha256(question_path),
                "answer_filename": filename, "run_limits": limits,
                "controls": control_manifest(SPEC, max_model_turns=limits["model_requests"]),
                "repetitions_per_profile": args.repetitions, "order_seed": args.order_seed,
                "gold_visible_to_agent": False, "corpus_readonly_mount": True,
                "index_policy": "one fresh remote-embedding preparation build; immutable seed plus at most one mutable copy; copy deleted after verification and provenance saved",
                "wall_seconds_scope": "Agent container execution, including zg bridge startup/shutdown full-corpus verification when inside the session. Excludes index preparation, index copying, and host post-trial verification; host verification is timed separately.",
                "answer_delivery": "Harness saves terminal response verbatim outside corpus to requested report path",
                "ci_identity": {k: os.environ.get(k) for k in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW", "RUNNER_OS", "RUNNER_ARCH")},
                **identity}
    write_json(output / "manifest.json", manifest)
    preparation = {"status": "running", "started_at": now(),
                   "included_in_qa_tokens_or_toolcalls": False}
    write_json(logs / "preparation.json", preparation)
    started = time.monotonic()
    print(json.dumps({"phase": "index_preparation", "status": "running"}), flush=True)
    try:
        command = with_embedding_environment(docker_command(args.image, source, logs, cache, index=index), endpoint)
        stdout = run_named(command + [args.image, "node", PREPARE_INDEX, "--root", "/app",
            "--package-dir", PACKAGE_DIR, "--embedding-model", EMBEDDING,
            "--model-cache-dir", "/models", "--log", "/logs/index-build.json"],
            prefix + "-prepare", timeout=1800, diagnostic_path=logs / "index-build-failure.json")
        (logs / "index-build.stdout.txt").write_text(redact(stdout))
        preparation["status"] = "completed"
    except Exception:
        preparation["status"] = "failed"
        raise
    finally:
        preparation.update(wall_seconds=round(time.monotonic() - started, 3), finished_at=now())
        write_json(logs / "preparation.json", preparation)
        manifest["index_preparation"] = preparation
        write_json(output / "manifest.json", manifest)
        print(json.dumps({"phase": "index_preparation", "status": preparation["status"],
                          "wall_seconds": preparation["wall_seconds"]}), flush=True)
    if directory_identity(source, skip_git=True) != source_before:
        raise RuntimeError("Corpus changed during index preparation")
    seed_before = directory_identity(index)
    query_flags = ["--root", "/app", "--package-dir", PACKAGE_DIR, "--embedding-model", EMBEDDING,
                   "--model-cache-dir", "/models", "--working-copy"]
    working_root = prepared / "working-indexes"
    preflight_index = working_index(index, working_root / "preflight")
    snapshot = prepared / "snapshot.json"
    command = with_embedding_environment(docker_command(args.image, source, logs, cache, index=preflight_index), endpoint)
    preflight_started = time.monotonic()
    preflight_status = "failed"
    print(json.dumps({"phase": "index_preflight", "status": "running"}), flush=True)
    try:
        run_named(command + [args.image, "node", BRIDGE, "preflight", *query_flags,
                             "--snapshot", "/logs/snapshot.json", "--log", "/logs/preflight.jsonl"],
                  prefix + "-preflight", timeout=900, diagnostic_path=logs / "preflight-failure.json")
        shutil.copyfile(logs / "snapshot.json", snapshot)
        preflight_status = "completed"
    finally:
        shutil.rmtree(preflight_index)
        print(json.dumps({"phase": "index_preflight", "status": preflight_status,
                          "wall_seconds": round(time.monotonic() - preflight_started, 3)}), flush=True)
    manifest.update(index_files=seed_before, source_snapshot_sha256=sha256(snapshot))
    write_json(output / "manifest.json", manifest)
    for trial in plan["trials"]:
        trial_root, zg = output / trial["trial_id"], trial["profile"] == "with-zg"
        agent_dir = trial_root / "agent"
        agent_dir.mkdir(parents=True)
        prompt = instruction(question, filename, zg=zg)
        write_json(trial_root / "instruction.json", {"text": prompt, "sha256": hashlib.sha256(prompt.encode()).hexdigest()})
        config = trial_root / SPEC.config_filename
        mcp = ["node", BRIDGE, "serve", *query_flags, "--snapshot", "/run/qa/snapshot.json", "--log", "/logs/zg-trace.jsonl"]
        write_json(config, build_agent_config(SPEC, zg=zg, mcp_command=mcp if zg else None,
                                             max_model_turns=limits["model_requests"]))
        trial_index = working_index(index, working_root / trial["trial_id"]) if zg else None
        before = directory_identity(trial_index) if zg else None
        command = docker_command(args.image, source, agent_dir, cache,
                                 index=trial_index, snapshot=snapshot if zg else None)
        if zg:
            command = with_embedding_environment(command, endpoint)
        container_name = prefix + "-" + hashlib.sha256(trial["trial_id"].encode()).hexdigest()[:10]
        config_path = "/run/qa/" + SPEC.config_filename
        command += ["--name", container_name, "--env", SPEC.credential_env] + mount(config, config_path)
        session_spec = {"command": build_agent_command(SPEC, prompt, config_path=config_path, zg=zg,
                                                      max_model_turns=limits["model_requests"]),
                        "env": agent_environment(SPEC, config_path=config_path), "config_path": config_path,
                        "limits": limits, "native_name": SPEC.stream_filename, "log_dir": "/logs"}
        write_json(agent_dir / "session-spec.json", session_spec)
        command += [args.image, "python3", "/opt/qa/qa-session.py", "--spec", "/logs/session-spec.json"]
        trial["status"] = "running"
        write_json(output / "plan.json", plan)
        print(json.dumps({"phase": "agent_trial", "trial_id": trial["trial_id"], "status": "running"}), flush=True)
        result = {**trial, "started_at": now(), "agent": SPEC.name, "agent_version": SPEC.version,
                  "model": MODEL, "candidate_output_path": None,
                  "provenance": {"manifest_path": "manifest.json", "source_git_commit": commit,
                                 "question_sha256": manifest["question_sha256"], "image_id": identity["image_id"]}}
        started = time.monotonic()
        with (agent_dir / "launcher.stdout.txt").open("w") as stdout, (agent_dir / "launcher.stderr.txt").open("w") as stderr:
            try:
                child = subprocess.Popen(command, stdout=stdout, stderr=stderr)
                result["returncode"] = child.wait(timeout=args.timeout + 30)
                result["status"] = "completed" if result["returncode"] == 0 else "failed"
            except subprocess.TimeoutExpired:
                cleanup_container(container_name)
                child.kill()
                child.wait()
                result["status"] = "timeout"
            except OSError as error:
                result.update(status="launch_failure", error_type=type(error).__name__)
            finally:
                elapsed = round(time.monotonic() - started, 3)
                cleanup_container(container_name)
        result["wall_seconds"] = elapsed
        session_path = agent_dir / "session.json"
        if session_path.is_file():
            result["session"] = json.loads(session_path.read_text())
            if result["session"].get("status") != "completed":
                result["status"] = result["session"].get("status", "failed")
        conversion = {}
        try:
            conversion = convert_agent_trace(agent_dir, SPEC, prompt, zg=zg)
            result.update(conversion)
            if conversion.get("contract_error_count"):
                result["status"] = "contract_failure"
            elif conversion.get("error_event_count") or not conversion.get("has_final_answer"):
                if result["status"] == "completed":
                    result["status"] = "protocol_failure"
        except Exception as error:
            result["conversion_error"] = redact(str(error))
            if result["status"] == "completed":
                result["status"] = "conversion_failure"
        try:
            result.update(trial_metrics(agent_dir, conversion))
        except Exception as error:
            result.update(dict.fromkeys(("input_tokens", "output_tokens", "cached_input_tokens", "tool_calls", "zg_tool_calls", "answer")))
            result["metrics_error"] = redact(str(error))
            if result["status"] == "completed":
                result["status"] = "measurement_failure"
        if result["status"] == "completed" and (result["input_tokens"] is None or not result["answer"]):
            result["status"] = "measurement_failure"
        if result["answer"]:
            candidate = trial_root / "candidate" / filename
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(result["answer"], encoding="utf-8")
            result["candidate_output_path"] = candidate.relative_to(output).as_posix()
        verification_started = time.monotonic()
        source_ok = directory_identity(source, skip_git=True) == source_before
        seed_ok = directory_identity(index) == seed_before
        semantic_ok = None
        if zg:
            verify_logs = trial_root / "index-verification"
            command = with_embedding_environment(docker_command(args.image, source, verify_logs, cache, index=trial_index, snapshot=snapshot), endpoint)
            try:
                run_named(command + [args.image, "node", BRIDGE, "verify", *query_flags,
                    "--snapshot", "/run/qa/snapshot.json", "--log", "/logs/verification.jsonl"],
                    container_name + "-verify", timeout=900, diagnostic_path=verify_logs / "failure.json")
                semantic_ok = True
            except Exception as error:
                semantic_ok = False
                result["index_verification_error"] = redact(str(error))
            after = directory_identity(trial_index)
            result["working_index_physical_changes"] = physical_changes(before, after)
            result["working_index_files_before_sha256"] = identity_digest(before)
            result["working_index_files_after_sha256"] = identity_digest(after)
        result.update(source_unchanged=source_ok, original_seed_unchanged=seed_ok,
                      working_index_semantic_unchanged=semantic_ok, finished_at=now(),
                      post_trial_verification_wall_seconds=round(time.monotonic() - verification_started, 3))
        if not source_ok or not seed_ok or semantic_ok is False:
            result["status"] = "integrity_failure"
        trial["status"] = result["status"]
        write_json(trial_root / "result.json", result)
        # Keep the evidence above, not ten full physical copies of a large index.
        # This also runs for failed agent sessions and failed semantic checks.
        if trial_index:
            shutil.rmtree(trial_index)
            result["working_index_disposal"] = "removed_after_verification_and_provenance_saved"
            write_json(trial_root / "result.json", result)
        write_json(output / "plan.json", plan)
        collect_results(output, plan)
        print(json.dumps({k: result[k] for k in ("trial_id", "status", "input_tokens", "tool_calls", "wall_seconds")}), flush=True)
        # Retain later trials as planned when the combination is deterministically invalid.
        if result["status"] in {"contract_failure", "launch_failure", "integrity_failure"}:
            break
    return 0 if all(t["status"] == "completed" for t in plan["trials"]) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--question-file", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--answer-filename")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--image", default="zg-readonly-qa:0.2.2")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--order-seed", type=int, default=1729)
    parser.add_argument("--dry-run", action="store_true")
    return execute(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
