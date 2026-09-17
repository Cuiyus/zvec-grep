#!/usr/bin/env python3
"""Run paired Qoder workspace QA trials using the pinned read-only zg runtime.

The caller supplies a complete frozen workspace and the unmodified task query.
Only final-answer delivery is adapted: the harness saves it as the task's report.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import math
import os
import random
import re
import selectors
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_cache  # noqa: E402
from zg_bench.swe_qa.readonly_agents import (  # noqa: E402
    QODER_SEARCH_TOOL, REMOTE_EMBEDDING_ENV_NAMES, agent_environment, agent_spec, build_agent_command,
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
PROTOCOL = "workspace-qa-qoder-native-install-v3"
MODEL = os.environ.get("WORKSPACE_QA_MODEL", "qwen3.8-max").lower()
EMBEDDING = "qwen/qwen3.7-text-embedding"
INDEX_MAX_FILE_SIZE_BYTES = 1048576
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
        raise ValueError("answer filename must be a relative text-output path")
    path = PurePosixPath(value)
    if (path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/"))
            or path.suffix.lower() not in {".md", ".txt", ".csv"}):
        raise ValueError("answer filename must be a safe relative .md, .txt or .csv path")
    return path.as_posix()


def make_plan(task_id: str, repetitions: int, seed: int = 1729,
              shard_repetitions: list[int] | None = None) -> dict[str, Any]:
    positive(repetitions, "repetitions")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task_id):
        raise ValueError("task_id must be a safe non-empty identifier")
    # Exactly equal AB/BA blocks for an even N; imbalance at most one for odd N.
    first_arms = [PROFILES[i % 2] for i in range(repetitions)]
    random.Random(f"{seed}:{task_id}").shuffle(first_arms)
    selected = set(range(1, repetitions + 1))
    if shard_repetitions is not None:
        if (not shard_repetitions or len(shard_repetitions) != len(set(shard_repetitions))
                or any(type(value) is not int or not 1 <= value <= repetitions for value in shard_repetitions)):
            raise ValueError("shard repetitions must be unique integers inside the full plan")
        selected = set(shard_repetitions)
    trials = []
    for repetition, first in enumerate(first_arms, 1):
        if repetition not in selected:
            continue
        for profile in (first, next(p for p in PROFILES if p != first)):
            trial_id = f"{task_id}-r{repetition:02d}-{profile}"
            trials.append({"trial_id": trial_id, "task_id": task_id, "profile": profile,
                           "repetition": repetition, "block_id": repetition, "status": "planned",
                           "trajectory_path": f"{trial_id}/agent/trajectory.json"})
    plan = {"schema_version": 1, "protocol": PROTOCOL, "task_id": task_id,
            "model": MODEL,
            "repetitions_per_profile": repetitions, "order_seed": seed,
            "order_policy": "balanced AB/BA blocks, shuffled before execution", "trials": trials}
    if shard_repetitions is not None:
        plan["shard_repetitions"] = sorted(selected)
    return plan


def instruction(question: str, filename: str, *, zg: bool) -> str:
    if os.environ.get("WORKSPACE_QA_CORPUS_VARIANT", "original") == "office-markdown-v1":
        from office_markdown import COMMON_NOTICE
        question += "\n\n" + COMMON_NOTICE
    elif os.environ.get("WORKSPACE_QA_CORPUS_VARIANT", "original") == "pdf-text-v1":
        from pdf_text import COMMON_NOTICE
        question += "\n\n" + COMMON_NOTICE
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
    extra = final.get("extra") or {}
    metrics["input_usage_complete"] = extra.get("token_usage_complete")
    metrics["input_tokens_observed_lower_bound"] = number(extra.get("input_tokens_observed_lower_bound"))
    metrics["input_usage_incomplete_reason"] = extra.get("input_usage_incomplete_reason")
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
              "model": plan.get("model", MODEL),
              "repetitions_per_profile": plan["repetitions_per_profile"], "trials": rows}
    if "shard_repetitions" in plan:
        report["shard_repetitions"] = plan["shard_repetitions"]
    write_json(output / "trial-results.json", report)
    return report


def runtime_identity(image: str) -> dict[str, Any]:
    metadata = json.loads(run_checked(["docker", "image", "inspect", image]))[0]
    # Check the installed releases, not merely a mutable Docker tag's spelling.
    script = ("const fs=require('node:fs'),cp=require('node:child_process');"
              "const p=JSON.parse(fs.readFileSync('/opt/qa/node_modules/@zvec/zvec-grep/package.json','utf8'));"
              "console.log(JSON.stringify({zg:p.version,node:process.version,qoder:cp.execFileSync('qodercli',['--version'],{encoding:'utf8'}).trim()}))")
    versions = json.loads(run_checked(["docker", "run", "--rm", "--network", "none", image,
                                      "node", "-e", script], timeout=60))
    if {key: versions.get(key) for key in ("zg", "qoder")} != {"zg": "0.2.2", "qoder": SPEC.version}:
        raise RuntimeError("Runtime versions differ from zg 0.2.2 / Qoder " + SPEC.version)
    return {"image_id": metadata["Id"], "image_repo_digests": metadata.get("RepoDigests", []),
            "installed_versions": versions, "os": metadata.get("Os"),
            "architecture": metadata.get("Architecture")}


def cleanup_container(name: str) -> None:
    try:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def run_streamed(command: list[str], *, timeout: float, diagnostic_path: Path,
                 output_prefix: Path) -> str:
    """Stream complete redacted stderr lines, retaining both pipes even on failure.

    Reads are multiplexed so large stdout cannot block stderr or the deadline.
    Partial UTF-8 and secrets split across reads stay buffered until a full line.
    """
    paths = {name: output_prefix.with_name(output_prefix.name + f".{name}.txt")
             for name in ("stdout", "stderr")}
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    handles = {name: path.open("w", encoding="utf-8") for name, path in paths.items()}
    buffers = dict.fromkeys(paths, "")
    decoders = {name: codecs.getincrementaldecoder("utf-8")("replace") for name in paths}
    child = None
    failure = None
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout

    def emit(name: str, line: str) -> None:
        safe = redact(line)
        handles[name].write(safe)
        handles[name].flush()
        if name == "stderr":
            sys.stderr.write(safe)
            sys.stderr.flush()

    def consume(name: str, data: bytes, *, final: bool = False) -> None:
        buffers[name] += decoders[name].decode(data, final=final)
        while "\n" in buffers[name]:
            line, buffers[name] = buffers[name].split("\n", 1)
            emit(name, line + "\n")
        if final and buffers[name]:
            emit(name, buffers[name])
            buffers[name] = ""

    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for name in paths:
            selector.register(getattr(child, name), selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if failure:
                    break
                failure = "TimeoutExpired"
                if child.poll() is None:
                    child.kill()
                deadline = time.monotonic() + 5  # Drain partial output after killing the CLI.
                continue
            for key, _ in selector.select(min(0.2, remaining)):
                data = os.read(key.fd, 65536)
                if data:
                    consume(key.data, data)
                else:
                    consume(key.data, b"", final=True)
                    selector.unregister(key.fileobj)
        if not failure:
            try:
                child.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                failure = "TimeoutExpired"
        if not failure and child.returncode:
            failure = "CalledProcessError"
    except (OSError, subprocess.SubprocessError) as error:
        failure = type(error).__name__
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
            for key in list(selector.get_map().values()):
                consume(key.data, b"", final=True)
            for name in paths:
                getattr(child, name).close()
        selector.close()
        for handle in handles.values():
            handle.close()
    if failure:
        stderr = paths["stderr"].read_text(encoding="utf-8")[-16000:]
        write_json(diagnostic_path, {"status": "failed", "kind": failure,
            "returncode": child.returncode if child else None, "timeout_seconds": timeout,
            "stderr": stderr, "stdout_path": str(paths["stdout"]),
            "stderr_path": str(paths["stderr"]), "logs_redacted": True})
        raise RuntimeError(f"{command[0]} failed ({failure}); see {diagnostic_path}")
    return paths["stdout"].read_text(encoding="utf-8").strip()


def run_named(command: list[str], name: str, *, stream_output: Path | None = None,
              **kwargs: Any) -> str:
    command[2:2] = ["--name", name]
    try:
        if stream_output is not None:
            return run_streamed(command, output_prefix=stream_output, **kwargs)
        return run_checked(command, **kwargs)
    finally:
        cleanup_container(name)


def execute(args: argparse.Namespace) -> int:
    shard_repetition = getattr(args, "shard_repetition", None)
    shard = [shard_repetition] if shard_repetition is not None else None
    plan = make_plan(args.task_id, args.repetitions, args.order_seed, shard)
    positive(args.timeout, "timeout")
    positive(getattr(args, "input_token_limit", 600000), "input-token-limit")
    retries = getattr(args, "model_request_retries", 0)
    if type(retries) is not int or not 0 <= retries <= 3:
        raise ValueError("model-request-retries must be an integer from 0 to 3")
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
    from native_runner import execute_native
    return execute_native(args, plan, source, output, question_path, question, filename)


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
    parser.add_argument("--input-token-limit", type=int, default=600000,
                        help="Same cumulative input-token ceiling for both profiles")
    parser.add_argument("--model-request-retries", type=int, default=0,
                        help="Pinned Qoder request retry ceiling, identical for both profiles")
    parser.add_argument("--order-seed", type=int, default=1729)
    parser.add_argument("--shard-repetition", type=int,
                        help="Execute one repetition from the full deterministic plan")
    parser.add_argument("--continue-from", type=Path,
                        help="Verified prior task artifact; execute only its unstarted trials")
    parser.add_argument("--continuation-code-review", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return execute(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
