"""Execute the real zg install integration in isolated, read-only QA containers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import runner as r
import seed_cache
from native_session import validate_installation

PROTOCOL = "workspace-qa-qoder-native-install-v3"
IMAGE = "zg-readonly-qa:0.2.2"


def remote_environment(command: list[str]) -> list[str]:
    return command + ["--env", "QWEN_API_KEY", "--env", "ZVEC_GREP_ENDPOINT=" + r.embedding_endpoint(),
                      "--env", "ZVEC_GREP_EMBEDDING=" + r.EMBEDDING]


def native_index(source: Path, index: Path, logs: Path, cache: Path, *, image: str,
                 check_only: bool = False) -> dict:
    index.mkdir(parents=True, exist_ok=True)
    (index / "locks").mkdir(exist_ok=True)
    # Reap native zg's detached daemon instead of leaving it under Python PID 1.
    command = remote_environment(r.docker_command(image, source, logs, cache, index=index) + ["--init"])
    command += [image, "python3", "/opt/qa/native-index.py", "--output", "/logs/native-index.json"]
    if check_only:
        command.append("--check-only")
    name = "native-index-" + hashlib.sha256(str(logs).encode()).hexdigest()[:12]
    r.run_named(command, name, timeout=1800, diagnostic_path=logs / "failure.json",
                stream_output=logs / "command")
    result = json.loads((logs / "native-index.json").read_text())
    if result.get("status") != "completed" or result.get("protocol") != PROTOCOL:
        raise RuntimeError("Native zg index CLI did not validate the expected protocol")
    return result


def run_native_trial(source: Path, agent: Path, index: Path | None, cache: Path, *,
                     prompt: str, profile: str, limits: dict, image: str = IMAGE,
                     model_request_retries: int = 0) -> dict:
    """Installation is setup; all model and MCP runtime work is timed by qa-session."""
    agent.mkdir(parents=True, exist_ok=True)
    zg = profile == "with-zg"
    if zg != (index is not None):
        raise ValueError("Only the with-zg profile receives an index")
    spec = {"protocol": PROTOCOL, "profile": profile, "prompt": prompt, "model": r.SPEC.cli_model,
            "embedding_model": r.EMBEDDING, "root": "/app", "limits": limits,
            "model_request_retries": model_request_retries}
    r.write_json(agent / "native-spec.json", spec)
    command = r.docker_command(image, source, agent, cache, index=index) + ["--init"]
    if zg:
        command = remote_environment(command)
    name = "native-qa-" + hashlib.sha256(str(agent).encode()).hexdigest()[:12]
    command += ["--name", name, "--env", r.SPEC.credential_env,
                image, "python3", "/opt/qa/native-session.py", "--spec", "/logs/native-spec.json"]
    result = {"protocol": PROTOCOL, "profile": profile, "started_at": r.now(), "status": "launch_failure",
              "agent": r.SPEC.name, "agent_version": r.SPEC.version, "model": r.MODEL,
              "input_tokens": None, "tool_calls": None, "wall_seconds": None}
    started = time.monotonic()
    with (agent / "launcher.stdout.txt").open("w") as stdout, (agent / "launcher.stderr.txt").open("w") as stderr:
        child = None
        try:
            child = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            remaining = limits["wall_seconds"] + 300
            while True:
                try:
                    result["returncode"] = child.wait(timeout=min(30, remaining))
                    break
                except subprocess.TimeoutExpired:
                    elapsed = time.monotonic() - started
                    remaining = limits["wall_seconds"] + 300 - elapsed
                    if remaining <= 0:
                        raise
                    print(json.dumps({"phase": "agent_trial_heartbeat", "profile": profile,
                        "status": "running", "container_elapsed_seconds": round(elapsed, 1),
                        "qa_input_token_limit": limits["input_tokens"]}), flush=True)
            result["status"] = "completed" if result["returncode"] == 0 else "failed"
        except subprocess.TimeoutExpired:
            result["status"] = "timeout"
            r.cleanup_container(name)
            if child is not None:
                child.kill()
                child.wait()
        except OSError as error:
            result.update(error_type=type(error).__name__)
        finally:
            r.cleanup_container(name)
    result["container_total_wall_seconds"] = round(time.monotonic() - started, 3)
    installation_path = agent / "install-manifest.json"
    try:
        result["installation"] = validate_installation(agent, profile=profile)
        result["installation_sha256"] = r.sha256(installation_path)
        result["installation_wall_seconds"] = result["installation"].get("setup_wall_seconds")
    except (OSError, ValueError, TypeError) as error:
        result["installation_error"] = r.redact(str(error))
    session_path = agent / "session.json"
    if session_path.is_file():
        session = json.loads(session_path.read_text())
        result.update(session=session, wall_seconds=r.number(session.get("wall_seconds")))
        if session.get("status") != "completed":
            result["status"] = session.get("status", "failed")
    else:
        result["status"] = "launch_failure"
    conversion = {}
    try:
        conversion = r.convert_agent_trace(agent, r.SPEC, prompt, zg=zg)
        result.update(conversion)
        if conversion.get("contract_error_count"):
            result["status"] = "contract_failure"
        elif (conversion.get("error_event_count") or not conversion.get("has_final_answer")) and result["status"] == "completed":
            result["status"] = "protocol_failure"
    except Exception as error:
        result["conversion_error"] = r.redact(str(error))
        if result["status"] == "completed":
            result["status"] = "conversion_failure"
    try:
        result.update(r.trial_metrics(agent, conversion))
    except Exception as error:
        result.update(answer=None, metrics_error=r.redact(str(error)))
        if result["status"] == "completed":
            result["status"] = "measurement_failure"
    if result["status"] == "completed" and (result.get("input_tokens") is None or not result.get("answer")):
        result["status"] = "measurement_failure"
    if result.get("installation_error") and result["status"] == "completed":
        result["status"] = "contract_failure"
    try:
        from qoder_probe import native_startup_evidence
        result["startup_evidence"] = native_startup_evidence(agent)
        if (result["startup_evidence"]["status"] in {"failed", "incomplete"}
                and result["status"] == "completed"):
            result["status"] = "contract_failure"
    except (OSError, ValueError, TypeError) as error:
        result["startup_evidence_error"] = r.redact(str(error))
        if result["status"] == "completed":
            result["status"] = "contract_failure"
    result["finished_at"] = r.now()
    return result


def execute_native(args: argparse.Namespace, plan: dict, source: Path, output: Path,
                   question_path: Path, question: str, filename: str) -> int:
    for name in (r.SPEC.credential_env, "QWEN_API_KEY"):
        if not os.environ.get(name):
            raise RuntimeError(name + " is required")
    identity = r.runtime_identity(args.image)
    commit = r.run_checked(["git", "-C", str(source), "rev-parse", "HEAD"])
    if not r.run_checked(["git", "-C", str(source), "ls-files"]):
        raise RuntimeError("Workspace has no git-tracked files")
    if not (source / ".zvec-grep").is_dir() or any((source / ".zvec-grep").iterdir()):
        raise RuntimeError("Prepared source must contain an empty index mount directory")
    before = r.directory_identity(source, skip_git=True)
    limits = {"model_requests": 60, "tool_calls": 120,
              "input_tokens": getattr(args, "input_token_limit", 600000), "wall_seconds": args.timeout}
    prepared = output / "preparation"
    index, cache, logs = prepared / "index", prepared / "model-cache", prepared / "runtime"
    index.mkdir(parents=True)
    (index / "locks").mkdir()
    cache_root = Path(os.environ["WORKSPACE_QA_INDEX_CACHE"]).resolve() if os.environ.get("WORKSPACE_QA_INDEX_CACHE") else None
    if cache_root and any(cache_root.is_relative_to(p) or p.is_relative_to(cache_root) for p in (source, output)):
        raise ValueError("Index cache must be outside source and output")
    cache_identity = seed_cache.make_identity(before, identity, protocol=PROTOCOL, embedding_model=r.EMBEDDING,
        endpoint=r.embedding_endpoint(), max_file_size_bytes=r.INDEX_MAX_FILE_SIZE_BYTES)
    manifest = {"schema_version": 2, "protocol": PROTOCOL, "integration_method": "zg_install",
        "install_command": ["zg", "install", "--target", "qoder", "--yes"],
        "task_id": args.task_id, "created_at": r.now(), "package": r.PACKAGE, "embedding_model": r.EMBEDDING,
        "embedding_endpoint": r.embedding_endpoint(), "agent": r.SPEC.name, "agent_version": r.SPEC.version,
        "model": r.MODEL, "agent_spec": r.SPEC.to_dict(), "source_git_commit": commit,
        "model_controls": {**r.control_manifest(r.SPEC, max_model_turns=limits["model_requests"]),
                           "qoder_model_request_retries": getattr(args, "model_request_retries", 0)},
        "source_files": before, "question_sha256": r.sha256(question_path), "answer_filename": filename,
        "corpus_variant": os.environ.get("WORKSPACE_QA_CORPUS_VARIANT", "original"),
        "preprocessing_manifest_sha256": os.environ.get("WORKSPACE_QA_PREPROCESSING_SHA256"),
        "run_limits": limits, "model_request_retries": getattr(args, "model_request_retries", 0),
        "repetitions_per_profile": args.repetitions, "order_seed": args.order_seed,
        "gold_visible_to_agent": False, "corpus_readonly_mount": True,
        "index_options": {"root": "/app", "maxFileSizeBytes": r.INDEX_MAX_FILE_SIZE_BYTES},
        "index_policy": "native CLI seed; separate writable copy per with-zg trial; normal native refresh allowed",
        "wall_seconds_scope": "qa-session agent interval including native MCP startup/search; native install, index preparation and host integrity checks are recorded separately",
        "answer_delivery": "Harness saves terminal response verbatim outside corpus to requested report path",
        "ci_identity": {k: os.environ.get(k) for k in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW", "RUNNER_OS", "RUNNER_ARCH")}, **identity}
    preserved = set()
    if getattr(args, "continuation_code_review", None):
        manifest["continuation_code_review"] = json.loads(args.continuation_code_review.read_text())
    if getattr(args, "continue_from", None):
        from continuation import load_prior, validate_runtime, import_prior
        from failure_audit import TERMINAL
        if not getattr(args, "continuation_code_review", None):
            raise ValueError("Continuation requires a reviewed source-to-current code diff")
        prior = load_prior(args.continue_from, plan)
        # An interrupted observation is not rerunnable, but cannot be imported
        # as a terminal result either. In particular, collect_results must not
        # replace a running ledger's partial usage with an empty planned row.
        for trial_id in prior["preserved_trial_ids"]:
            status = prior["rows_by_id"][trial_id]["status"]
            if status not in TERMINAL:
                raise ValueError(f"Continuation requires a terminal original result: {trial_id} ({status})")
        validate_runtime(prior, manifest, question, filename)
        manifest["continuation"] = import_prior(prior, output, plan)
        preserved = set(manifest["continuation"]["preserved_trial_ids"])
        r.write_json(output / "plan.json", plan)
        r.collect_results(output, plan)
    r.write_json(output / "manifest.json", manifest)
    cached = seed_cache.restore(cache_root, index, cache_identity)
    cached["published"] = False
    preparation = {"status": "running", "cache": cached, "method": "released_zg_cli", "wall_seconds": 0.0,
                   "included_in_qa_tokens_or_toolcalls": False}
    r.write_json(logs / "preparation.json", preparation)
    print(json.dumps({"phase": "native_index_cache", "status": cached["status"]}), flush=True)
    started = time.monotonic()
    try:
        built = native_index(source, index, logs / "native", cache, image=args.image, check_only=cached["status"] == "hit")
        source_ok = r.directory_identity(source, skip_git=True) == before
        if not source_ok:
            raise RuntimeError("Source changed during native index preparation")
        built["source_unchanged"] = source_ok
        if cached["status"] != "hit":
            cached["publication"] = seed_cache.publish(cache_root, index, cache_identity, built, preflight_passed=True)
            cached["published"] = cached["publication"]["status"] == "saved"
        preparation.update(status="completed", native_index=built)
    finally:
        preparation["wall_seconds"] = round(time.monotonic() - started, 3)
        if preparation["status"] == "running":
            preparation["status"] = "failed"
        r.write_json(logs / "preparation.json", preparation)
        manifest["index_preparation"] = preparation
        r.write_json(output / "manifest.json", manifest)
    seed_before = r.directory_identity(index)
    manifest["index_files"] = seed_before
    r.write_json(output / "manifest.json", manifest)
    for trial in plan["trials"]:
        if trial["trial_id"] in preserved:
            continue
        root = output / trial["trial_id"]
        root.mkdir()
        zg = trial["profile"] == "with-zg"
        prompt = r.instruction(question, filename, zg=zg)
        r.write_json(root / "instruction.json", {"text": prompt, "sha256": hashlib.sha256(prompt.encode()).hexdigest()})
        working = r.working_index(index, prepared / "working-indexes" / trial["trial_id"]) if zg else None
        index_before = r.directory_identity(working) if zg else None
        trial["status"] = "running"
        r.write_json(output / "plan.json", plan)
        r.collect_results(output, plan)
        print(json.dumps({"phase": "agent_trial", "trial_id": trial["trial_id"], "status": "running"}), flush=True)
        result = {**trial, **run_native_trial(source, root / "agent", working, cache,
                  prompt=prompt, profile=trial["profile"], limits=limits, image=args.image,
                  model_request_retries=getattr(args, "model_request_retries", 0)),
                  "candidate_output_path": None, "provenance": {"manifest_path": "manifest.json",
                  "source_git_commit": commit, "question_sha256": manifest["question_sha256"], "image_id": identity["image_id"]}}
        if result.get("answer"):
            candidate = root / "candidate" / filename
            candidate.parent.mkdir(parents=True)
            candidate.write_text(result["answer"])
            result["candidate_output_path"] = candidate.relative_to(output).as_posix()
        verified = time.monotonic()
        result.update(source_unchanged=r.directory_identity(source, skip_git=True) == before,
                      original_seed_unchanged=r.directory_identity(index) == seed_before,
                      working_index_semantic_unchanged=None,
                      native_index_refresh_allowed=True)
        if working:
            result["working_index_physical_changes"] = r.physical_changes(index_before, r.directory_identity(working))
            shutil.rmtree(working)
            result["working_index_disposal"] = "removed_after_native_trial_and_provenance_saved"
        result["post_trial_verification_wall_seconds"] = round(time.monotonic() - verified, 3)
        if not result["source_unchanged"] or not result["original_seed_unchanged"]:
            result["status"] = "integrity_failure"
        trial["status"] = result["status"]
        r.write_json(root / "result.json", result)
        r.write_json(output / "plan.json", plan)
        r.collect_results(output, plan)
        print(json.dumps({k: result.get(k) for k in ("trial_id", "status", "input_tokens", "tool_calls", "zg_tool_calls", "wall_seconds")}), flush=True)
        if result["status"] in {"contract_failure", "launch_failure", "integrity_failure"}:
            break
    return 0 if all(t["status"] == "completed" for t in plan["trials"]) else 1


def run_native_probe(source: Path, output: Path) -> dict:
    """One real native-installed Qoder semantic call over a synthetic fixture."""
    output.mkdir(parents=True, exist_ok=True)
    (source / ".zvec-grep").mkdir(exist_ok=True)
    before = r.directory_identity(source, skip_git=True)
    index, cache = output / "index", output / "model-cache"
    print(json.dumps({"phase": "native_install_preflight", "step": "index", "status": "starting"}), flush=True)
    native_index(source, index, output / "index-preparation", cache, image=IMAGE)
    print(json.dumps({"phase": "native_install_preflight", "step": "index", "status": "completed"}), flush=True)
    prompt = ("This is a connectivity check on a synthetic fixture, not a benchmark question. "
              "Call mcp__zvec_grep__zvec_grep_search exactly once with "
              '{"root":"/app","vector":"代码仓库 repository source files","limit":1}. '
              "Do not use other tools. After the tool returns, reply with the retrieved filename only. "
              "If it fails, report the error without retrying.")
    # Qoder's Security SessionStart hook can legitimately spend more than two
    # minutes reaching its service before the model turn begins.  Keep the
    # structured terminal hook requirement, but give the native startup the
    # same practical network allowance as a small QA turn.
    print(json.dumps({"phase": "native_install_preflight", "step": "qoder_mcp_call", "status": "starting"}), flush=True)
    result = run_native_trial(source, output / "agent", index, cache, prompt=prompt, profile="with-zg",
        limits={"model_requests": 4, "tool_calls": 4, "input_tokens": 50000, "wall_seconds": 300})
    print(json.dumps({"phase": "native_install_preflight", "step": "qoder_mcp_call",
                      "status": result.get("status"), "wall_seconds": result.get("wall_seconds")}), flush=True)
    result.update(phase="setup_qoder_mcp_probe", included_in_benchmark=False,
                  embedding_model=r.EMBEDDING,
                  source_unchanged=r.directory_identity(source, skip_git=True) == before)
    r.write_json(output / "result.json", result)
    return result
