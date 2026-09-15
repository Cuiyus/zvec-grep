"""E2E prompt experiment using released zg install and fresh potionv2 indexes.

Each session receives an isolated writable copy because released zg validates
that its repository root is writable. Source hashes before and after the run
prove that neither the Agent nor zg changed QA files outside `.zvec-grep`.
Native model/tool behavior is not repaired or routed by this harness. Setup,
decision diagnostics, QA, grading and retrieval are separate ledgers.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..settings import OPENCODE_CUSTOM_BASE_URL
from . import e2e_stability
from .readonly_agents import agent_spec, build_agent_config, control_manifest, convert_agent_trace
from .readonly_run import (EMBEDDING, PACKAGE, directory_identity, docker_command,
                           redact, run_checked, sha256, wire_contract, write_json)

GROUPS = {
    "opencode-glm52": ("opencode", "glm-5.2"),
    "opencode-qwen38max": ("opencode", "qwen3.8-max"),
}
PROTOCOL = "native-install-e2e-prompt-v1"
LIMITS = {"model_requests": 30, "tool_calls": 60, "input_tokens": 300000, "wall_seconds": 900}
MODEL_SEED = e2e_stability.MODEL_SEED
SCREEN_WORKERS_PER_GROUP = 2


def progress(phase: str, status: str, **details: Any) -> None:
    print(json.dumps({"phase": phase, "status": status, **details}, ensure_ascii=False), flush=True)


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text()) if path.is_file() else default


def fresh(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"Output must be new: {path}")
    path.mkdir(parents=True, exist_ok=True)


def source_checkout(case: dict, destination: Path) -> Path:
    if destination.exists():
        raise ValueError("Source destination must be new")
    run_checked(["git", "init", str(destination)])
    run_checked(["git", "-C", str(destination), "fetch", "--depth=1", case["repo"]["url"], case["repo"]["commit"]])
    run_checked(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"])
    if run_checked(["git", "-C", str(destination), "rev-parse", "HEAD"]) != case["repo"]["commit"]:
        raise ValueError("QA source revision mismatch")
    (destination / ".zvec-grep").mkdir(exist_ok=True)
    return destination


def instruction(case: dict) -> str:
    return (case["question"] + "\n\nAnswer using the repository at /app. This is a read-only QA task. "
            "Support the important claims with source file paths and line references. Do not modify files.")


def native_tools(agent: str, zg: bool) -> list[str]:
    if agent != "opencode":
        raise ValueError("This native benchmark cohort only supports OpenCode")
    common = ["read", "grep", "glob"]
    search = "zvec_grep_zvec_grep_search"
    return [*common, *([search] if zg else [])]


def runtime_spec(group: str, case: dict, *, zg: bool, variant: str = "P00",
                 model_seed: int = MODEL_SEED) -> tuple[Any, dict]:
    from .prompt_diagnostics import render_candidate_prompts
    if variant not in {"P00", "P10", "P01", "P11"} or (not zg and variant != "P00"):
        raise ValueError("Invalid native prompt variant")
    agent, model = GROUPS[group]
    spec = agent_spec(agent, model, base_url=OPENCODE_CUSTOM_BASE_URL if agent == "opencode" else None)
    config = build_agent_config(spec, zg=False, max_model_turns=LIMITS["model_requests"],
                                model_seed=model_seed)
    value = {"agent": agent, "model": model, "arm": "zg" if zg else "baseline",
             "prompt_variant": variant, "instruction": instruction(case), "workspace": "/app",
             "model_seed": model_seed,
             "log_dir": "/logs", "model_cache": "/models", "limits": dict(LIMITS),
             "base_config": config, "tap_upstream": spec.base_url}
    if variant != "P00":
        rendered = render_candidate_prompts(search_tool=native_tools(agent, True)[-1],
                                            rg_tool="native grep" if agent == "opencode" else "native Grep",
                                            include_zg_rg=False)
        if variant in {"P10", "P11"}:
            value["guidance_override"] = rendered["guidance_override"]
        if variant in {"P01", "P11"}:
            value["description_overrides"] = rendered["description_overrides"]
    return spec, value


def frozen_overrides(candidate: str | None, case: dict) -> dict:
    if candidate is not None and candidate not in {"P10", "P01", "P11"}:
        raise ValueError("Candidate must be a concrete tested prompt variant or None")
    result = {}
    for group in GROUPS:
        _, runtime = runtime_spec(group, case, zg=True, variant=candidate or "P00")
        values = {key: runtime[key] for key in ("guidance_override", "description_overrides") if key in runtime}
        encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        result[group] = {"overrides": values, "sha256": hashlib.sha256(encoded).hexdigest()}
    return result


def launch(*, image: str, source: Path, logs: Path, cache: Path, workspace: Path,
           spec: dict, script: str = "native-agent-session.py", credential: str | None = None,
           timeout: int = 3000) -> int:
    logs.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    session_source = workspace / "source"
    if session_source.exists():
        raise ValueError("Native session workspace must be new")
    shutil.copytree(source, session_source, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", ".zvec-grep"))
    (session_source / ".zvec-grep").mkdir()
    source_before = directory_identity(session_source)
    index = None
    if spec["arm"] == "zg":
        index = workspace / "index"
        index.mkdir(parents=True, exist_ok=True)
    write_json(logs / "native-session-spec.json", spec)
    # The provenance file contains the intended endpoint; the native runner
    # saves its effective argv/env separately, never credentials.
    write_json(logs / "session-spec.json", {"tap_upstream": spec.get("tap_upstream"),
                                           "protocol": PROTOCOL})
    command = docker_command(image, session_source, logs, cache, index=index, source_readonly=False)
    name = "zg-native-" + hashlib.sha256(str(logs.resolve()).encode()).hexdigest()[:16]
    command += ["--name", name]
    env = dict(os.environ)
    if credential:
        if not os.environ.get(credential):
            raise ValueError(f"Missing required environment variable: {credential}")
        command += ["--env", "OPENAI_API_KEY"]
        env["OPENAI_API_KEY"] = os.environ[credential]
    command += [image, "python3", "/opt/qa/" + script, "--spec", "/logs/native-session-spec.json"]
    started = time.monotonic()
    with (logs / "launcher.stdout.txt").open("w") as stdout, (logs / "launcher.stderr.txt").open("w") as stderr:
        try:
            process = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr)
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
            process.kill()
            process.wait()
            code = 124
    unchanged = directory_identity(session_source) == source_before
    write_json(logs / "source-integrity.json", {
        "status": "unchanged" if unchanged else "changed",
        "scope": "isolated session source excluding .git and .zvec-grep",
        "source_files_before": len(source_before),
        "source_files_after": len(directory_identity(session_source)),
    })
    if not unchanged and code == 0:
        code = 86
    write_json(logs / "launcher.json", {"returncode": code, "wall_seconds_including_preparation": time.monotonic() - started,
                                        "qa_wall_seconds_source": "session.json", "script": script})
    return code


def trial_result(trial: dict, agent_dir: Path, spec: Any, text: str, code: int, source_ok: bool) -> dict:
    zg = trial["arm"] != "B"
    session = read_json(agent_dir / "session.json", {})
    installed = read_json(agent_dir / "install-manifest.json", {})
    status = session.get("status", "preparation_failure")
    if code and status == "completed":
        status = "contract_failure" if code == 4 else "failed"
    observed = session.get("observed", {})
    row = {"trial_id": trial["trial_id"], "profile": trial["profile"], "arm": trial["arm"],
           "block": trial["block"], "execution_status": status, "returncode": code,
           "source_unchanged": source_ok, "session": session, "installation": installed,
           "metrics": {"input_tokens": observed.get("input_tokens"), "tool_calls_attempted": observed.get("tool_calls")},
           "usage_complete": observed.get("input_tokens") is not None and not observed.get("invalid_usage_events"),
           "tools_complete": bool(session) and status not in {"preparation_failure", "launch_failure"}}
    try:
        conversion = convert_agent_trace(agent_dir, spec, text, zg=zg,
                                         installed_tools=native_tools(spec.name, zg))
        row["conversion"] = conversion
        if conversion["error_event_count"] or not conversion["has_final_answer"]:
            row["execution_status"] = "failed" if status == "completed" else status
        if conversion.get("contract_error_count"):
            row["execution_status"] = "contract_failure"
        row["tools_complete"] = row["tools_complete"] and not conversion.get("parse", {}).get("invalid_json_lines")
        if spec.name == "opencode":
            contract = wire_contract(agent_dir, spec.provider_model, native_tools(spec.name, zg),
                                     expected_seed=trial["model_seed"])
            row["wire_contract"] = contract
            if not contract["valid"]:
                row["usage_complete"] = False
                if contract.get("configuration_mismatch"):
                    row["execution_status"] = "contract_failure"
                elif row["execution_status"] == "completed":
                    row["execution_status"] = "measurement_failure"
    except Exception as error:
        row["conversion_error"] = {"type": type(error).__name__, "message": redact(str(error))}
        row["tools_complete"] = False
        if row["execution_status"] == "completed":
            row["execution_status"] = "measurement_failure"
    if not source_ok:
        row["execution_status"] = "source_integrity_failure"
    tap = agent_dir / "native-mcp.jsonl"
    requests = []
    if tap.is_file():
        for number, line in enumerate(tap.read_text().splitlines(), 1):
            try:
                event = json.loads(line)
                message = event.get("message", {})
                if event.get("direction") == "agent_to_zg" and message.get("method") == "tools/call":
                    requests.append(message.get("params"))
            except (ValueError, AttributeError, TypeError):
                row.setdefault("mcp_parse_errors", []).append(number)
        if row.get("mcp_parse_errors"):
            row["tools_complete"] = False
            if row["execution_status"] == "completed":
                row["execution_status"] = "measurement_failure"
    row["behavior"] = {"zg_adopted": bool(requests) if tap.is_file() else False if not zg else None,
                       "first_zg_request": requests[0] if requests else None, "zg_call_count": len(requests)}
    if row.get("mcp_parse_errors"):
        row["behavior"].update(zg_adopted=True if requests else None,
                                zg_call_count=None, zg_call_count_observed_lower_bound=len(requests))
    return row


def run_group(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    fresh(output)
    case = read_json(args.case)
    selection = read_json(args.selection)
    if not selection or not selection.get("ready"):
        raise ValueError("A frozen ready prompt selection is required before confirmation")
    candidate = selection.get("candidate")
    expected_overrides = frozen_overrides(candidate, case)
    if selection.get("prompt_runtime_overrides") != expected_overrides:
        raise ValueError("Current prompt text differs from frozen screening selection")
    if selection.get("case_sha256") != sha256(args.case):
        raise ValueError("Prompt selection belongs to a different case")
    plan = e2e_stability.make_plan(case["case_id"], repetitions=e2e_stability.REPETITIONS,
                                   candidate=candidate, seed=e2e_stability.ORDER_SEED,
                                   model_seed=MODEL_SEED)
    plan.update(protocol=PROTOCOL, group=args.group, selection_sha256=sha256(args.selection))
    write_json(output / "plan.json", plan)
    spec, _ = runtime_spec(args.group, case, zg=False)
    manifest = {"protocol": PROTOCOL, "group": args.group, "case_id": case["case_id"],
                "case_sha256": sha256(args.case), "source": case["repo"], "package": PACKAGE,
                "embedding": EMBEDDING, "agent": spec.to_dict(),
                "controls": control_manifest(spec, max_model_turns=30, model_seed=MODEL_SEED),
                "index_policy": "fresh independent index for each zg trial; no frozen identity requirement",
                "prompt_selection": selection, "started_at": datetime.now(UTC).isoformat(),
                "ci": {k: os.environ.get(k) for k in ("GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_RUN_ATTEMPT")}}
    write_json(output / "manifest.json", manifest)
    source = source_checkout(case, output / "corpus")
    source_before = directory_identity(source, skip_git=True)
    rows = []
    for number, trial in enumerate(plan["trials"], 1):
        trial_dir = output / trial["trial_id"]
        agent_dir = trial_dir / "agent"
        spec, runtime = runtime_spec(args.group, case, zg=trial["arm"] != "B",
                                     variant=trial.get("prompt_version") or "P00",
                                     model_seed=trial["model_seed"])
        trial["status"] = "running"
        write_json(output / "plan.json", plan)
        progress("native_e2e", "running", group=args.group, trial=trial["trial_id"],
                 completed=number - 1, planned=len(plan["trials"]))
        code = launch(image=args.image, source=source, logs=agent_dir, cache=output / "model-cache",
                      workspace=output / "workspaces" / trial["trial_id"], spec=runtime,
                      credential="GLM_API_KEY")
        integrity = read_json(agent_dir / "source-integrity.json", {"status": "unchanged"})
        row = trial_result(trial, agent_dir, spec, instruction(case), code,
                           directory_identity(source, skip_git=True) == source_before
                           and integrity.get("status") == "unchanged")
        rows.append(row)
        trial["status"] = row["execution_status"]
        write_json(trial_dir / "result.json", row)
        write_json(output / "results.json", {"trials": rows})
        write_json(output / "plan.json", plan)
        progress("native_e2e", trial["status"], group=args.group, trial=trial["trial_id"],
                 completed=number, planned=len(plan["trials"]), metrics=row["metrics"])
        if row["execution_status"] in {"contract_failure", "preparation_failure", "source_integrity_failure"}:
            # An affirmative installation/configuration failure stops spend;
            # remaining planned rows stay visible, not silently resampled.
            break
    manifest["finished_at"] = datetime.now(UTC).isoformat()
    write_json(output / "manifest.json", manifest)
    report = e2e_stability.summarize(plan, rows, source_reference=case["repo"], controls=manifest["controls"])
    write_json(output / "e2e-stability.json", report)
    (output / "e2e-stability.md").write_text(e2e_stability.render_markdown(report))
    (output / "e2e-ci-summary.md").write_text(e2e_stability.render_ci_conclusion({args.group: report}, {}))
    return 0 if all(t["status"] == "completed" for t in plan["trials"]) else 1


def review_group(args: argparse.Namespace) -> None:
    from .quality_review import review_runs
    output = args.output.resolve()
    plan = read_json(output / "plan.json")
    # A physical copy of trajectories makes a compatibility grading view with
    # stable trial IDs. It is not an additional run and its aliases are not used
    # for treatment statistics. The judge receives answer/source only.
    view = output / "grading-view"
    fresh(view)
    alias_plan = copy.deepcopy(plan)
    import shutil
    for row in alias_plan["trials"]:
        row["profile"] = "baseline" if row["arm"] == "B" else "zvec-grep"
        relative = Path(row["trajectory_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe trajectory path in grading view")
        origin = output / relative
        if not origin.resolve().is_relative_to(output.resolve()):
            raise ValueError("Trajectory path escapes experiment")
        if origin.is_file():
            target = view / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
    write_json(view / "plan.json", alias_plan)
    quality = review_runs(runs_dir=view, case_path=args.judge_case, output=output / "quality-review.json",
                          expected_per_profile=None)
    report = e2e_stability.summarize(plan, read_json(output / "results.json", {}), quality,
                                   source_reference=read_json(args.case)["repo"],
                                   controls=read_json(output / "manifest.json")["controls"])
    write_json(output / "e2e-stability.json", report)
    (output / "e2e-stability.md").write_text(e2e_stability.render_markdown(report))
    (output / "e2e-ci-summary.md").write_text(e2e_stability.render_ci_conclusion({output.name: report}, {}))


def replay_catalog(*, analysis: dict, source: Path, output: Path, image: str, case: dict,
                   cache: Path, index: Path | None = None) -> list[dict]:
    fresh(output)
    _, runtime = runtime_spec("opencode-glm52", case, zg=True)
    runtime["tap_upstream"] = None
    runtime["replay_plan"] = "/logs/replay-plan.json"
    runtime["prepare_index"] = index is None
    request_plan = {"schema_version": 1, "repetitions": 5,
                    "index_policy": "rebuild_from_same_source_and_config" if index is None else "reuse_this_trial_index_without_freeze",
                    "requests": analysis["request_catalog"]}
    write_json(output / "replay-plan.json", request_plan)
    workspace = output / "workspace"
    if index is not None:
        workspace.mkdir()
        (workspace / "index").symlink_to(index.resolve(), target_is_directory=True)
    code = launch(image=image, source=source, logs=output, cache=cache, workspace=workspace,
                  spec=runtime, credential=None, timeout=7200)
    rows = []
    for path in (output / "native-replay.jsonl",):
        if path.is_file():
            rows.extend(json.loads(line) for line in path.read_text().splitlines() if line)
    write_json(output / "replay-status.json", {"returncode": code, "observations": len(rows),
                                              "planned": 5 * len(analysis["request_catalog"]),
                                              "index_policy": request_plan["index_policy"]})
    return rows


def annotate(*, analysis_path: Path, args: argparse.Namespace, source: Path, output: Path) -> dict:
    from .query_ground_truth import execute
    return execute(argparse.Namespace(analysis=analysis_path, case=args.case, entries=args.entries,
                                      source_root=source, output=output, image=args.image,
                                      batch_size=8, timeout=1200))


def screen(args: argparse.Namespace) -> int:
    from . import prompt_diagnostics as pd
    from . import native_query_diagnosis as nq
    output = args.output.resolve()
    fresh(output)
    case = read_json(args.case)
    progress("screen", "starting", groups=len(GROUPS), variants=4,
             repetitions=pd.REPETITIONS, max_parallel=len(GROUPS) * SCREEN_WORKERS_PER_GROUP)
    source = source_checkout(case, output / "corpus")
    captures = {}
    traces = []
    for group in ("opencode-glm52", "opencode-qwen38max"):
        progress("native_capture", "running", group=group)
        agent_dir = output / "capture" / group
        spec, runtime = runtime_spec(group, case, zg=True)
        code = launch(image=args.image, source=source, logs=agent_dir, cache=output / "model-cache",
                      workspace=output / "capture-indexes" / group, spec=runtime,
                      script="capture-native-state.py", credential=None)
        capture = read_json(agent_dir / "capture-manifest.json", {})
        if code or capture.get("status") != "captured":
            raise ValueError(f"Native initial request capture failed for {group}; no paid screening started")
        captures[group] = agent_dir
        traces.append({"group_id": group, "trial_id": "initial-native-capture", "agent_dir": str(agent_dir),
                       "agent": "opencode", "model": spec.provider_model, "capture_only": True,
                       "intended_endpoint": spec.base_url, "native_request_builder_verified": True})
        progress("native_capture", "completed", group=group)
    states = pd.extract_states(traces, output / "states")
    config = pd.default_prompt_config(captures)
    write_json(output / "prompt-config.json", config)
    pd.build_plan(states, config, output / "decisions")
    plan_path = output / "decisions" / "plan.json"
    # Register the no-promotion outcome before looking at any model decision.
    write_json(output / "candidate-policy.json", {"fallback": None, "status": "retain_current_if_inconclusive",
                "reason": "Without reviewed improvement evidence, keep P00 and run only B/C; do not pick a winner from noise.",
                "no_posthoc_variant_shopping": True})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(pd.run_plan, plan_path, credential_env="GLM_API_KEY",
                               endpoint=OPENCODE_CUSTOM_BASE_URL, group_id=group,
                               max_workers=SCREEN_WORKERS_PER_GROUP) for group in captures]
        for future in futures:
            future.result()
    preliminary = pd.analyze(plan_path)
    progress("prompt_screen", "completed", completed=preliminary["completed"], planned=preliminary["planned"])
    write_json(output / "screening-preliminary.json", preliminary)
    analysis = nq.build_decision_catalog(plan_path, case)
    analysis_path = output / "query-analysis.json"
    write_json(analysis_path, analysis)
    progress("screen_ground_truth", "running", requests=len(analysis["request_catalog"]))
    annotate(analysis_path=analysis_path, args=args, source=source, output=output / "annotations")
    progress("screen_ground_truth", "completed", requests=len(analysis["request_catalog"]))
    progress("screen_retrieval_replay", "running", requests=len(analysis["request_catalog"]), repetitions=5)
    replay = replay_catalog(analysis=analysis, source=source, output=output / "retrieval",
                            image=args.image, case=case, cache=output / "model-cache")
    progress("screen_retrieval_replay", "completed", observations=len(replay))
    labels = read_json(output / "annotations" / "query-intents.json")
    evidence = nq.screening_evidence(plan_path, analysis, labels, read_json(args.entries), replay)
    evidence_path = output / "screening-evidence.json"
    write_json(evidence_path, evidence)
    assessed = pd.analyze(plan_path, evidence_path)
    write_json(output / "screening-analysis.json", assessed)
    # Inconclusive screening never manufactures a winning candidate. Native
    # installation preflight passed; B/C can still characterize current zg.
    plan = pd.load_plan(plan_path)
    sample_rows = [read_json(plan_path.parent / "samples" / s["sample_id"] / "result.json", {}) for s in plan["samples"]]
    complete = bool(sample_rows) and all(r.get("status") == "completed" for r in sample_rows)
    selected = assessed.get("selection", {})
    candidate = selected.get("variant")
    promoted = candidate in {"P10", "P01", "P11"}
    selection = {"schema_version": 1, "ready": True, "candidate": candidate if promoted else None,
                 "status": "screening_supported_candidate" if promoted else "retain_current_insufficient_evidence",
                 "decision_sampling_complete": complete,
                 "screening": selected, "screening_plan_sha256": sha256(plan_path),
                 "screening_evidence_sha256": sha256(evidence_path), "candidate_policy_sha256": sha256(output / "candidate-policy.json"),
                 "frozen_at": datetime.now(UTC).isoformat(),
                 "confirmation_trials_per_arm": e2e_stability.REPETITIONS,
                 "case_sha256": sha256(args.case),
                 "prompt_runtime_overrides": frozen_overrides(candidate if promoted else None, case),
                 "unavailable": states["unavailable"], "missing_categories": states["missing_categories"],
                 "scope": "One native initial state per registered OpenCode model combination; later states are not represented."}
    write_json(output / "selection.json", selection)
    (output / "screening-summary.md").write_text(pd.render_ci_summary(assessed, selection))
    progress("screen", "completed", selection_status=selection["status"], candidate=selection["candidate"])
    return 0


def diagnose(args: argparse.Namespace) -> int:
    from . import native_query_diagnosis as nq
    output = args.output.resolve()
    fresh(output)
    case = read_json(args.case)
    runs = [args.runs_dir / group for group in GROUPS]
    if any(not (directory / "plan.json").is_file() for directory in runs):
        raise ValueError("All registered group artifacts are required")
    source = source_checkout(case, output / "corpus")
    progress("diagnosis_catalog", "running", groups=len(runs))
    analysis = nq.build_catalog(runs, case)
    path = output / "query-analysis.json"
    write_json(path, analysis)
    progress("diagnosis_catalog", "completed", requests=len(analysis["request_catalog"]),
             occurrences=len(analysis["occurrences"]))
    progress("diagnosis_ground_truth", "running", requests=len(analysis["request_catalog"]))
    annotate(analysis_path=path, args=args, source=source, output=output / "annotations")
    progress("diagnosis_ground_truth", "completed", requests=len(analysis["request_catalog"]))
    progress("diagnosis_retrieval_replay", "running", requests=len(analysis["request_catalog"]), repetitions=5)
    replay = replay_catalog(analysis=analysis, source=source, output=output / "retrieval",
                            image=args.image, case=case, cache=output / "model-cache")
    progress("diagnosis_retrieval_replay", "completed", observations=len(replay))
    report = nq.score_records(analysis, read_json(output / "annotations" / "query-intents.json"),
                              read_json(args.entries), replay)
    write_json(output / "retrieval-diagnosis.json", report)
    (output / "retrieval-diagnosis.md").write_text(nq.render_markdown(report))
    e2e_reports = {group: read_json(directory / "e2e-stability.json", {}) for group, directory in zip(GROUPS, runs)}
    conclusion = e2e_stability.render_ci_conclusion(e2e_reports, report)
    (output / "benchmark-conclusion.md").write_text(conclusion)
    progress("diagnosis", "completed", reports=len(e2e_reports), retrieval_requests=len(report.get("replays", [])))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["screen", "run", "review", "diagnose"])
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--entries", type=Path)
    parser.add_argument("--judge-case", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="zg-native-qa:0.2.2")
    parser.add_argument("--group", choices=list(GROUPS))
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--runs-dir", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run":
        return run_group(args)
    if args.command == "review":
        review_group(args)
        return 0
    if args.command == "screen":
        return screen(args)
    return diagnose(args)


if __name__ == "__main__":
    raise SystemExit(main())
