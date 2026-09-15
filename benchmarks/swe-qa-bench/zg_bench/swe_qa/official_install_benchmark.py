"""Official zg install -> native E2E -> source labels -> native MCP replay.

This is a new 30-trial cohort. Historical bridge trials are not reused as arms.
Only the current released guidance is evaluated; no prompt screening occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from ..settings import OPENCODE_CUSTOM_BASE_URL
from .readonly_agents import agent_spec, control_manifest, convert_agent_trace, expected_tools
from .readonly_run import (EMBEDDING, PACKAGE, directory_identity, docker_command,
                           make_plan, mount, redact, run_checked, sha256, wire_contract, write_json)

PROTOCOL = "official-install-readonly-qa-v1"
GROUPS = {"opencode-glm52": ("opencode", "glm-5.2"),
          "opencode-qwen38max": ("opencode", "qwen3.8-max"),
          "qoder-qwen38max": ("qodercli", "qwen3.8-max")}
LIMITS = {"model_requests": 30, "tool_calls": 60, "input_tokens": 300000, "wall_seconds": 900}


def read(path: Path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def fresh(path: Path):
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"Refusing to overwrite existing evidence: {path}")
    path.mkdir(parents=True, exist_ok=True)


def checkout(case: dict, destination: Path) -> Path:
    if destination.exists():
        raise ValueError("Source checkout must be new")
    run_checked(["git", "init", str(destination)])
    run_checked(["git", "-C", str(destination), "fetch", "--depth=1", case["repo"]["url"], case["repo"]["commit"]])
    run_checked(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"])
    if run_checked(["git", "-C", str(destination), "rev-parse", "HEAD"]) != case["repo"]["commit"]:
        raise ValueError("Source revision mismatch")
    (destination / ".zvec-grep").mkdir(exist_ok=True)
    return destination


def instruction(case: dict) -> str:
    return (case["question"] + "\n\nAnswer using the repository at /app. This is a read-only QA task. "
            "Support the important claims with source file paths and line references. Do not modify files.")


def session_spec(group: str, case: dict, profile: str) -> tuple[object, dict]:
    agent, model = GROUPS[group]
    spec = agent_spec(agent, model, base_url=OPENCODE_CUSTOM_BASE_URL if agent == "opencode" else None)
    return spec, {"agent": agent, "model": model, "profile": profile, "root": "/app", "log_dir": "/logs",
                  "instruction": instruction(case), "base_url": spec.base_url, "limits": dict(LIMITS)}


def launch(*, image: str, source: Path, logs: Path, workspace: Path, cache: Path,
           spec: dict, credential: str | None = None, timeout: int = 3000) -> int:
    fresh(logs)
    index = workspace / "index" if spec["profile"] == "zvec-grep" else None
    if index is not None:
        fresh(index)
    write_json(logs / "session-spec.json", spec)
    # Fresh official indexes use normal writable storage, including native locks.
    # The historical bridge's tmpfs lock overlay is not part of this integration.
    command = docker_command(image, source, logs, cache)
    if index is not None:
        command += mount(index, "/app/.zvec-grep", readonly=False)
    name = "zg-official-" + hashlib.sha256(str(logs.resolve()).encode()).hexdigest()[:16]
    command += ["--name", name]
    env = dict(os.environ)
    if credential:
        if not env.get(credential):
            raise ValueError(f"Missing required credential environment: {credential}")
        destination = "OPENAI_API_KEY" if spec["agent"] == "opencode" else "QODER_PERSONAL_ACCESS_TOKEN"
        env[destination] = env[credential]
        command += ["--env", destination]
    command += [image, "python3", "/opt/qa/official-install-session.py", "--spec", "/logs/session-spec.json"]
    started = time.monotonic()
    with (logs / "launcher.stdout.txt").open("w") as stdout, (logs / "launcher.stderr.txt").open("w") as stderr:
        process = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
            process.kill()
            process.wait()
            code = 124
    write_json(logs / "launcher.json", {"returncode": code, "wall_seconds_including_setup": time.monotonic() - started,
                                        "qa_wall_seconds_source": "session.json"})
    return code


def collect_result(trial: dict, logs: Path, spec, code: int, source_ok: bool, question: str) -> dict:
    session = read(logs / "session.json", {})
    installation = read(logs / "install-manifest.json", {})
    status = session.get("status", "preparation_failure")
    if status == "preparation_failed":
        status = "preparation_failure"
    if code and status == "completed":
        status = "contract_failure" if code == 4 else "failed"
    observed = session.get("observed", {})
    row = {"trial_id": trial["trial_id"], "profile": trial["profile"], "repetition": trial["repetition"],
           "status": status, "returncode": code, "session": session, "installation": installation,
           "source_unchanged": source_ok, "input_tokens": observed.get("input_tokens"),
           "tool_calls": observed.get("tool_calls"), "usage_complete": bool(session) and observed.get("input_tokens") is not None
           and not observed.get("invalid_usage_events")}
    try:
        converted = convert_agent_trace(logs, spec, question, zg=trial["profile"] == "zvec-grep")
        row["conversion"] = converted
        if converted.get("contract_error_count"):
            row["status"] = "contract_failure"
        elif (converted.get("error_event_count") or not converted.get("has_final_answer")) and row["status"] == "completed":
            row["status"] = "failed"
        if spec.name == "opencode":
            contract = wire_contract(logs, spec.provider_model, expected_tools(spec, zg=trial["profile"] == "zvec-grep"))
            row["wire_contract"] = contract
            if not contract["valid"]:
                row["usage_complete"] = False
                if contract.get("configuration_mismatch"):
                    row["status"] = "contract_failure"
                elif row["status"] == "completed":
                    row["status"] = "measurement_failure"
    except Exception as error:
        row["conversion_error"] = {"type": type(error).__name__, "message": redact(str(error))}
        row["usage_complete"] = False
        if row["status"] == "completed":
            row["status"] = "measurement_failure"
    if not source_ok:
        row["status"] = "source_integrity_failure"
    if trial["profile"] == "zvec-grep" and installation.get("installation_verified") is not True:
        row["status"] = "contract_failure" if session else "preparation_failure"
    return row


def summarize(plan: dict, rows: list[dict], quality: dict | None = None) -> dict:
    by_id = {row["trial_id"]: row for row in rows}
    groups = {}
    for profile in ("baseline", "zvec-grep"):
        planned = [t for t in plan["trials"] if t["profile"] == profile]
        observed = [by_id[t["trial_id"]] for t in planned if t["trial_id"] in by_id]
        group = {"planned": len(planned), "observed": len(observed),
                 "completed": sum(r["status"] == "completed" for r in observed), "metrics": {}}
        for key in ("input_tokens", "tool_calls"):
            known = [r[key] for r in observed if isinstance(r.get(key), (int, float)) and not isinstance(r[key], bool)]
            complete = len(known) == len(planned) and all(r.get("usage_complete") for r in observed)
            group["metrics"][key] = {"known_values": known, "complete": complete,
                "mean": statistics.mean(known) if known and complete else None,
                "median": statistics.median(known) if known and complete else None,
                "range": [min(known), max(known)] if known else None,
                "observed_sum": sum(known) if known else None}
        groups[profile] = group
    changes = {}
    for key in ("input_tokens", "tool_calls"):
        a, b = (groups[p]["metrics"][key]["mean"] for p in ("baseline", "zvec-grep"))
        changes[key] = 100 * (b / a - 1) if a and b is not None else None
    pairs = []
    for repetition in range(1, 6):
        pair = {t["profile"]: by_id.get(t["trial_id"], {}) for t in plan["trials"] if t["repetition"] == repetition}
        pairs.append({"repetition": repetition, **{k: pair["zvec-grep"].get(k) - pair["baseline"].get(k)
            if all(pair[p].get("usage_complete") and isinstance(pair[p].get(k), (int, float)) for p in pair)
            else None for k in ("input_tokens", "tool_calls")}})
    return {"protocol": PROTOCOL, "case_id": plan["case_id"], "group": plan["group"], "independent_qa_tasks": 1,
            "groups": groups, "mean_relative_change_percent": changes, "pairs": pairs, "trials": rows,
            "quality": quality, "quality_noninferiority_established": False,
            "limitations": ["All five planned trials per arm are retained; unknown usage is not zero.",
                "Five repeats of one development QA do not establish population efficacy or quality non-inferiority.",
                "Official installed guidance is retained. QA read-only permissions and budgets are experimental controls.",
                "Original bridge measurements are not same-cohort controls."]}


def markdown(report: dict) -> str:
    lines = ["# 官方 zg install 单题重测", "", f"组合：{report['group']}；QA：{report['case_id']}。", "",
             "| arm | 完成 / 计划 | 平均 input token | 平均 tool call |", "|---|---:|---:|---:|"]
    for name, group in report["groups"].items():
        lines.append(f"| {name} | {group['completed']} / {group['planned']} | {group['metrics']['input_tokens']['mean']} | {group['metrics']['tool_calls']['mean']} |")
    lines += ["", "所有失败、未使用 zg 和缺失计量均保留；质量判断与原始答案单列。", "", "限制：", ""]
    lines += ["- " + item for item in report["limitations"]]
    return "\n".join(lines) + "\n"


def run_group(args) -> int:
    output = args.output.resolve(); fresh(output)
    case = read(args.case)
    plan = make_plan(case["case_id"])
    plan.update(protocol=PROTOCOL, group=args.group, no_prompt_variants=True)
    write_json(output / "plan.json", plan)
    source = checkout(case, output / "corpus")
    source_before = directory_identity(source, skip_git=True)
    spec, _ = session_spec(args.group, case, "baseline")
    manifest = {"protocol": PROTOCOL, "group": args.group, "case_sha256": sha256(args.case), "source": case["repo"],
                "package": PACKAGE, "embedding": EMBEDDING, "agent": spec.to_dict(),
                "controls": control_manifest(spec, max_model_turns=30), "no_prompt_variants": True,
                "index_policy": "fresh index per zg trial; native freshness behavior remains active",
                "integration": "official zg install; original native MCP command and guidance auto-discovery",
                "ci_identity": {k: os.environ.get(k) for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")},
                "started_at": datetime.now(UTC).isoformat()}
    write_json(output / "manifest.json", manifest)
    rows = []
    for trial in plan["trials"]:
        spec, session = session_spec(args.group, case, trial["profile"])
        root = output / trial["trial_id"]
        trial["status"] = "running"; write_json(output / "plan.json", plan)
        print(json.dumps({"trial": trial["trial_id"], "status": "running"}), flush=True)
        code = launch(image=args.image, source=source, logs=root / "agent", workspace=output / "workspaces" / trial["trial_id"],
                      cache=output / "model-cache", spec=session,
                      credential="GLM_API_KEY" if spec.name == "opencode" else "QODER_PERSONAL_ACCESS_TOKEN")
        row = collect_result(trial, root / "agent", spec, code, directory_identity(source, skip_git=True) == source_before, instruction(case))
        rows.append(row); trial["status"] = row["status"]
        write_json(root / "result.json", row); write_json(output / "plan.json", plan)
        write_json(output / "results.json", {"trials": rows})
        print(json.dumps({"trial": trial["trial_id"], "status": trial["status"], "input_tokens": row["input_tokens"], "tool_calls": row["tool_calls"]}), flush=True)
        if row["status"] in {"preparation_failure", "contract_failure", "source_integrity_failure"}:
            break
    report = summarize(plan, rows)
    write_json(output / "official-report.json", report)
    (output / "official-report.md").write_text(markdown(report))
    return 0 if len(rows) == 10 and all(r["status"] == "completed" for r in rows) else 1


def preflight(args) -> int:
    output = args.output.resolve(); fresh(output)
    case = read(args.case); source = checkout(case, output / "corpus")
    for group in ("opencode-glm52", "qoder-qwen38max"):
        _, spec = session_spec(group, case, "zvec-grep")
        spec["prepare_only"] = True
        code = launch(image=args.image, source=source, logs=output / group, workspace=output / "workspaces" / group,
                      cache=output / "model-cache", spec=spec, credential=None)
        if code:
            return code
    return 0


def review(args):
    from .quality_review import review_runs
    quality = review_runs(runs_dir=args.output, case_path=args.judge_case,
                          output=args.output / "quality-review.json", expected_per_profile=5)
    report = summarize(read(args.output / "plan.json"), read(args.output / "results.json", {"trials": []})["trials"], quality)
    write_json(args.output / "official-report.json", report)
    (args.output / "official-report.md").write_text(markdown(report))
    return 0


def replay_stage_status(scores: dict, code: int) -> dict:
    observations = [o for r in scores["replays"] for o in r["observations"]]
    contexts = [c for r in scores["replays"] for c in r["context_assessments"]]
    counts = Counter(o["status"] for o in observations)
    invalid = {"missing", "ambiguous_duplicate", "public_hash_mismatch", "text_result_mismatch", "request_mismatch", "unknown"}
    complete = code == 0 and not scores["unplanned_replay_rows"] and not any(k in invalid for k in counts)
    scorable = sum(c["assessment"].get("status") == "scored" for c in contexts)
    clean = complete and counts.get("completed", 0) == len(observations) and scorable == len(contexts)
    return {"status": "complete" if clean else "complete_with_errors_or_unknown" if complete else "incomplete",
            "execution_complete": complete, "planned_observations": len(scores["replays"]) * 5,
            "observation_status_counts": dict(counts), "contexts": len(contexts), "scorable_contexts": scorable,
            "unknown_contexts": len(contexts) - scorable, "unplanned_observations": len(scores["unplanned_replay_rows"]),
            "quality_or_efficacy_pass": False}


def diagnose(args):
    from .official_query_diagnosis import build_catalog, score_records
    from .query_ground_truth import execute
    from .retrieval_eval import load_manifest
    output = args.output.resolve(); fresh(output)
    case = read(args.case)
    runs = [args.runs_dir / group for group in GROUPS]
    manifests = [read(path / "manifest.json") for path in runs]
    if any(not m or m.get("protocol") != PROTOCOL for m in manifests):
        raise ValueError("All three official-install group artifacts are required")
    identities = {json.dumps(m["ci_identity"], sort_keys=True) for m in manifests}
    if len(identities) != 1 or any(m["case_sha256"] != sha256(args.case) for m in manifests):
        raise ValueError("E2E cohort identities differ")
    current = {k: os.environ.get(k) for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")}
    if current["GITHUB_RUN_ID"] and manifests[0]["ci_identity"] != current:
        raise ValueError("Diagnosis requires this CI cohort, not a prior installation experiment")
    for path in runs:
        plan = read(path / "plan.json", {})
        trials = plan.get("trials", [])
        if len(trials) != 10 or any(t.get("status") in {None, "planned", "running", "contract_failure", "preparation_failure", "preparation_failed", "source_integrity_failure", "measurement_failure"} for t in trials):
            raise ValueError("An installation/measurement stage is incomplete; preserve it before further model spending")
        for trial in trials:
            result = read(path / trial["trial_id"] / "result.json")
            logs = path / trial["trial_id"] / "agent"
            if (not result or result.get("trial_id") != trial["trial_id"] or result.get("status") != trial.get("status")
                    or not (logs / "session.json").is_file()
                    or not any((logs / n).is_file() for n in ("opencode.txt", "qodercli-stream.jsonl"))):
                raise ValueError("Planned trial is missing its native evidence or result identity")
    analysis = build_catalog(runs, case)
    analysis_path = output / "query-analysis.json"; write_json(analysis_path, analysis)
    if any(t["execution_status"] == "completed" and not t["native_trace_complete"] for g in analysis["groups"] for t in g["trials"]):
        raise ValueError("A completed trial lacks complete native evidence; do not label fabricated or missing context")
    source = checkout(case, output / "corpus")
    entries = load_manifest(args.entries, source_root=source)
    annotation = execute(argparse.Namespace(analysis=analysis_path, case=args.case, entries=args.entries,
        source_root=source, output=output / "ground-truth", image=args.image, batch_size=8, timeout=1200))
    labels = read(output / "ground-truth" / "query-intents.json")
    replay = output / "replay"
    # The launcher requires a new directory; put the plan elsewhere until it creates logs.
    _, spec = session_spec("opencode-glm52", case, "zvec-grep")
    spec["replay_plan_inline"] = {"schema_version": 1, "repetitions": 5, "requests": analysis["request_catalog"],
                                  "index_policy": "new official index from same source; no frozen-index equivalence claim"}
    code = launch(image=args.image, source=source, logs=replay, workspace=output / "replay-workspace",
                  cache=output / "model-cache", spec=spec, credential=None, timeout=7200)
    path = replay / "official-replay.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line] if path.is_file() else []
    scores = score_records(analysis, labels, entries, rows)
    stage_status = replay_stage_status(scores, code)
    reports = {g: read(p / "official-report.json") for g, p in zip(GROUPS, runs)}
    joined = []
    for group in analysis["groups"]:
        results = {r["trial_id"]: r for r in reports[group["group"]]["trials"]}
        quality = {r["trial_id"]: r for r in (reports[group["group"]].get("quality") or {}).get("trials", [])}
        for trial in group["trials"]:
            tid = trial["trial_id"]
            joined.append({"group": group["group"], "trial_id": tid, "profile": trial["profile"],
                "e2e_result": results.get(tid), "quality": quality.get(tid), "behavior": trial,
                "actual_retrieval_assessments": [r for r in scores["actual"] if r["group"] == group["group"] and r["trial_id"] == tid],
                "actual_vs_replay": [r for r in scores["actual_vs_replay"] if r["group"] == group["group"] and r["trial_id"] == tid]})
    report = {"protocol": PROTOCOL, "case_id": case["case_id"], "e2e_ci_identity": manifests[0]["ci_identity"],
        "planned_e2e_trials": 30, "e2e_groups": reports, "joined_trials": joined,
        "query_analysis_sha256": sha256(analysis_path), "annotation_manifest": annotation,
        "labels_sha256": sha256(output / "ground-truth" / "query-intents.json"), "retrieval": scores,
        "replay_status": {"returncode": code, "planned": len(analysis["request_catalog"]) * 5, "observed": len(rows)},
        "status": stage_status["status"], "stage_status": stage_status,
        "scope": "Official installed default guidance; native Agent traces; newly built retrieval index; one QA.",
        "quality_noninferiority_established": False}
    write_json(output / "joint-report.json", report)
    (output / "joint-report.md").write_text("# 官方安装完整链路\n\n" + f"状态：{report['status']}；E2E 计划 30 次；检索观察 {len(rows)} 次。\n\n" +
        "\n\n".join(markdown(r) for r in report["e2e_groups"].values() if r) +
        "\n检索原始请求、GroundTruth、逐次命中/排名/一致性见 joint-report.json；新索引回放不覆盖或替代 Agent 当时所见。\n")
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "run", "review", "diagnose"])
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="zg-official-qa:0.2.2")
    parser.add_argument("--group", choices=list(GROUPS))
    parser.add_argument("--judge-case", type=Path)
    parser.add_argument("--entries", type=Path)
    parser.add_argument("--runs-dir", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run" and not args.group:
        parser.error("run requires --group")
    if args.command == "review" and not args.judge_case:
        parser.error("review requires --judge-case")
    if args.command == "diagnose" and (not args.entries or not args.runs_dir):
        parser.error("diagnose requires --entries and --runs-dir")
    return {"preflight": preflight, "run": run_group, "review": review, "diagnose": diagnose}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
