"""Query-conditioned analysis/replay of recorded first ZG decisions. No LLM calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .first_query_analysis import analyze, write_report
from .retrieval_eval import load_manifest

SOURCE_RUN = "34255587426"
SOURCE_COMMIT = "dc0c2f2a7c52cbeb127e90e9a8cfcac2b8ab81a7"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def request_queries(request: dict) -> list[str]:
    values = [request.get("query"), *request.get("queries", []),
              *[r.get("query") for r in request.get("routes", [])]]
    # A string containing JSON array notation is still one actual query.
    return list(dict.fromkeys(v for v in values if isinstance(v, str) and v.strip()))


def build_plan(analysis: dict, question: str, *, repetitions: int = 5) -> dict:
    if repetitions != 5:
        raise ValueError("This development protocol freezes five retrieval repetitions")
    texts = list(dict.fromkeys([question, *[q["text"] for q in analysis["query_catalog"]]]))
    catalog = {q["text"]: q for q in analysis["query_catalog"]}
    units = []
    for text in texts:
        qid = "original" if text == question else catalog[text]["query_id"]
        for mode in ("fts", "vector", "hybrid"):
            request = {"root": "/app", "limit": 10, "autoUpdate": False, "trace": True}
            request.update({"query": text} if mode == "hybrid" else {"routes": [{"mode": mode, "query": text}]})
            units.append({"unit_id": f"{qid}-{mode}", "kind": "controlled", "mode": mode,
                          "query_id": qid, "query_texts": [text], "request": request,
                          "occurrences": catalog.get(text, {}).get("occurrences", []),
                          "quality_unit": "one recorded formulation, correlated with the same QA task"})
    faithful = {}
    unreplayable = []
    for group in analysis["groups"]:
        for trial in group["trials"]:
            if trial.get("profile") != "zvec-grep":
                continue
            decision = trial.get("first_zg_decision_round")
            if not decision:
                unreplayable.append({"group": group["group"], "trial_id": trial["trial_id"],
                                     "adoption": trial.get("zg_adoption_observed"),
                                     "reason": trial.get("query_evidence_status", "no_first_decision")})
                continue
            for call in decision["zg_calls"]:
                backend = call.get("backend")
                origin = {"group": group["group"], "trial_id": trial["trial_id"],
                          "call_id": call["call_id"], "model_turn_index": decision["model_turn_index"],
                          "has_prior_turn_feedback": decision["has_prior_turn_feedback"]}
                if not backend or call.get("backend_link", {}).get("status") != "matched":
                    unreplayable.append({**origin, "reason": "exact_backend_request_not_available"})
                    continue
                request = backend["request"]
                key = digest(request)
                if key not in faithful:
                    faithful[key] = {"unit_id": "faithful-" + key[:20], "kind": "faithful",
                                     "mode": "recorded-request", "request": request,
                                     "query_texts": request_queries(request), "occurrences": [],
                                     "quality_unit": "joint output of one recorded backend request; route effects not separable"}
                faithful[key]["occurrences"].append({**origin, "raw_arguments": call["raw_arguments"],
                                                    "backend_source": backend.get("source")})
    units.extend(faithful[key] for key in sorted(faithful))
    if len({u["unit_id"] for u in units}) != len(units):
        raise ValueError("Replay unit ID collision")
    return {"schema_version": 1, "protocol": "readonly-query-retrieval-v4", "question": question,
            "source_run": SOURCE_RUN, "source_commit": SOURCE_COMMIT,
            "repetitions": repetitions, "quality_repetition": 1, "byte_budgets": [4096, 8192],
            "independent_tasks": 1, "distinct_query_texts_including_original": len(texts),
            "controlled_units": sum(u["kind"] == "controlled" for u in units),
            "faithful_units": len(faithful), "planned_executions": len(units) * repetitions,
            "unreplayable_planned_trials_or_calls": unreplayable, "units": units,
            "scope": "All observed first-decision queries retained; no invented probes. Post-hoc development labels, not a held-out evaluation."}


def score_output(text: str, texts: list[str], labels: dict, entries: dict) -> dict:
    from .query_relevance import score_query_text
    return {view: [score_query_text(text, labels, entries, query=query, budget=budget)
                   for query in texts]
            for view, budget in [("native", None), ("bytes_4096", 4096), ("bytes_8192", 8192)]}


def score_request(text: str, request: dict, labels: dict, entries: dict) -> dict:
    from .query_relevance import score_query_text
    return {view: score_query_text(text, labels, entries, request=request, budget=budget)
            for view, budget in [("native", None), ("bytes_4096", 4096), ("bytes_8192", 8192)]}


def observed_scores(analysis: dict, labels: dict, entries: dict) -> list[dict]:
    rows = []
    for group in analysis["groups"]:
        for trial in group["trials"]:
            if trial.get("profile") != "zvec-grep":
                continue
            decision = trial.get("first_zg_decision_round")
            if not decision:
                rows.append({"group": group["group"], "trial_id": trial["trial_id"],
                             "status": "no_zg_call" if trial.get("zg_adoption_observed") is False else "unknown",
                             "scores": None})
                continue
            for call in decision["zg_calls"]:
                backend = call.get("backend")
                texts = request_queries(backend["request"]) if backend else []
                text = call.get("visible_text")
                rows.append({"group": group["group"], "trial_id": trial["trial_id"], "call_id": call["call_id"],
                             "status": "scored" if isinstance(text, str) and texts else "unknown",
                             "execution_status": trial.get("execution_status"), "query_texts": texts,
                             "model_turn_index": decision["model_turn_index"],
                             "has_prior_turn_feedback": decision["has_prior_turn_feedback"],
                             "output_source": call.get("result_source"),
                             "output_sha256": hashlib.sha256(text.encode()).hexdigest() if isinstance(text, str) else None,
                             "joint_output_not_route_attribution": len(texts) > 1,
                             "request_scores": score_request(text, backend["request"], labels, entries) if isinstance(text, str) and backend else None,
                             "scores": score_output(text, texts, labels, entries) if isinstance(text, str) and texts else None})
    return rows


def evaluate_replays(plan: dict, root: Path, labels: dict, entries: dict, snapshot: dict) -> dict:
    rows = []
    for unit in plan["units"]:
        directory = root / unit["unit_id"]
        parse_errors = []
        try:
            status = json.loads((directory / "status.json").read_text())
            if not isinstance(status, dict): raise ValueError("Status must be an object")
        except (OSError, ValueError):
            status = {}
            parse_errors.append({"file": "status.json", "line": None})
        events = []
        for filename in ["stdout.jsonl", "events.jsonl"]:
            loaded = []
            path = directory / filename
            if path.exists():
                for number, line in enumerate(path.read_text().splitlines(), 1):
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict): raise ValueError("Event must be an object")
                        loaded.append(event)
                    except ValueError: parse_errors.append({"file": filename, "line": number})
            if filename == "stdout.jsonl": events = loaded
            else: audit = loaded
        # Exit 1 can mean an individual query failed while the other planned
        # attempts and the final integrity check completed. Keep those attempts.
        integrity_ok = (status.get("returncode") in (0, 1) and not parse_errors
                        and len([e for e in audit if e.get("event") == "start"]) == 1
                        and any(e.get("event") == "start"
                                and e.get("source_identity", {}).get("sha256") == snapshot["source"]["sha256"]
                                and e.get("index_identity", {}).get("documents", {}).get("sha256") == snapshot["index"]["documents"]["sha256"] for e in audit)
                        and all(any(e.get("event") == "integrity" and e.get("stage") == stage
                                    and e.get("unchanged") is True and not e.get("mismatches") for e in audit)
                                for stage in ("start", "end"))
                        and any(e.get("event") == "end" and e.get("integrity") == "semantic_unchanged" for e in audit))
        repeated = []
        for repetition in range(1, plan["repetitions"] + 1):
            found = [e for e in events if e.get("repetition") == repetition]
            event = found[0] if len(found) == 1 else None
            error = None
            if not integrity_ok: error = "unit_execution_or_integrity_incomplete"
            elif event is None: error = "missing_or_duplicate_repetition"
            elif event.get("unit_id") != unit["unit_id"] or event.get("status") != "success": error = "query_execution_failed"
            elif event.get("request") != unit["request"]: error = "executed_request_differs_from_frozen_plan"
            elif event.get("source_identity", {}).get("sha256") != snapshot["source"]["sha256"]: error = "source_identity_mismatch"
            elif event.get("index_identity", {}).get("documents", {}).get("sha256") != snapshot["index"]["documents"]["sha256"]: error = "index_identity_mismatch"
            elif not isinstance(event.get("text"), str): error = "public_output_missing"
            elif hashlib.sha256(event["text"].encode()).hexdigest() != event.get("text_sha256"): error = "public_output_hash_mismatch"
            elif len([a for a in audit if a.get("event") == "search" and a.get("repetition") == repetition]) != 1:
                error = "missing_or_duplicate_audited_search"
            elif len([a for a in audit if a.get("event") == "search" and a.get("repetition") == repetition
                      and {k: v for k, v in a.items() if k not in ("schema_version", "run_id")} == event]) != 1:
                error = "stdout_not_matched_to_one_audited_search"
            repeated.append({"repetition": repetition, "status": "unknown" if error else "scored", "error": error,
                             "duration_ms": event.get("duration_ms") if event and not error else None,
                             "output_sha256": event.get("text_sha256") if event and not error else None,
                             "request_scores": score_request(event["text"], unit["request"], labels, entries) if not error and unit["kind"] == "faithful" else None,
                             "scores": score_output(event["text"], unit["query_texts"], labels, entries) if not error else None})
        known = [r for r in repeated if r["status"] == "scored"]
        latencies = [r["duration_ms"] for r in known if isinstance(r["duration_ms"], (int, float))]
        rows.append({"unit_id": unit["unit_id"], "kind": unit["kind"], "mode": unit["mode"],
                     "query_texts": unit["query_texts"], "request": unit["request"],
                     "occurrence_count": len(unit["occurrences"]), "quality_observation": repeated[0],
                     "repeats": repeated, "parse_errors": parse_errors,
                     "stability": {"known_repeats": len(known), "planned_repeats": plan["repetitions"],
                                   "distinct_public_outputs": len({r["output_sha256"] for r in known}),
                                   "all_public_outputs_identical": len({r["output_sha256"] for r in known}) == 1 if len(known) == plan["repetitions"] else None,
                                   "latency_median_ms": statistics.median(latencies) if latencies else None},
                     "scope": unit["quality_unit"]})
    return {"schema_version": 1, "protocol": plan["protocol"], "independent_tasks": 1,
            "planned_executions": plan["planned_executions"],
            "scored_executions": sum(r["status"] == "scored" for u in rows for r in u["repeats"]),
            "units": rows, "scope": "Repeated fixed queries estimate retrieval variation, not new agent behavior samples."}


def score_cells(score: dict | None) -> list[str]:
    if not score:
        return ["unknown"] * 4
    query = score["query_relevance"]
    target = query["target"]
    def rank(metric: dict) -> str:
        if metric.get("status") == "unknown": return "unknown"
        return str(metric["first_hit_rank"]) if metric.get("first_hit_rank") is not None else "未命中"
    task = score["task_entry_score"].get("levels", {}).get("function", {})
    return [query.get("classification") or "unknown", rank(target), rank(task),
            str(target["bytes_through_first_hit"]) if target.get("bytes_through_first_hit") is not None else "—"]


def observed_markdown(rows: list[dict]) -> str:
    lines = ["# 原有首次批次输出的 query 目标评分", "",
             "只重评原始产物；无新模型调用。完整请求绑定是主列，多 route 的联合输出不归因到某条 route。标注为已看过旧输出的开发标注，非独立人工 gold。", "",
             "| 组合 / trial / call | 状态 | query 类型 | 目标排名 | 原任务依赖入口排名 | 到目标字节 |", "|---|---|---|---:|---:|---:|"]
    for row in rows:
        key = "/".join(row[k] for k in ("group", "trial_id", "call_id") if k in row)
        cells = score_cells((row.get("request_scores") or {}).get("native"))
        lines.append("| " + " | ".join([key, row["status"], *cells]) + " |")
    lines += ["", "排名来自实际公开输出，包括 faithful 的第 15/20 个槽位；Hit@1/5/10 仍按对应截断计算。未命中仅表示未找到已标正目标，未审条目不能视为不相关。"]
    return "\n".join(lines) + "\n"


def replay_markdown(report: dict) -> str:
    lines = ["# Query-conditioned retrieval replay", "",
             f"完成评分 {report['scored_executions']} / {report['planned_executions']} 次；独立 QA 任务 1 道。质量统一使用预定第 1 次，五次只检验固定请求的检索重复性。", "",
             "| unit | query 类型 | 目标排名 | 原任务依赖入口排名 | 到目标字节 | 4 KiB 目标排名 | 相同输出 / 5 次 |", "|---|---|---:|---:|---:|---:|---|"]
    for unit in report["units"]:
        observation = unit["quality_observation"]
        def view(name: str) -> dict | None:
            if unit["kind"] == "faithful": return (observation.get("request_scores") or {}).get(name)
            items = (observation.get("scores") or {}).get(name, [])
            return items[0] if items else None
        stable = unit["stability"]
        stability = str(stable["all_public_outputs_identical"]) + f" ({stable['known_repeats']}/5 已评分)"
        lines.append("| " + " | ".join([unit["unit_id"], *score_cells(view("native")), score_cells(view("bytes_4096"))[1], stability]) + " |")
    lines += ["", "受控视图固定 limit=10，逐条文本运行 FTS / vector / hybrid。faithful 视图保持实际请求及省略的默认参数，完整 request 的开发标注为主列；附加分文本分数仍基于联合输出，不构成 route 归因。", "",
              "query 对应文本与实际来源见 replay-plan.json。JSON 同时保留 Hit@1/5/10、RR@10、bridge、原任务入口、4/8 KiB 预算视图、原始哈希和全部失败。字节是输出窗口代理，不是模型 input token。不同改写来自同题，不能算成 12 道独立题。"]
    return "\n".join(lines) + "\n"


def execute(plan: dict, args: argparse.Namespace, labels: dict, entries: dict) -> dict:
    from .readonly_run import (BRIDGE, EMBEDDING, PACKAGE, PACKAGE_DIR, PREPARE_INDEX,
                               directory_identity, docker_command, mount, run_checked, working_index)
    from .query_relevance import load_labels
    case = json.loads(args.case.read_text())
    output = args.output.resolve(); prepared = output / "preparation"; prepared.mkdir()
    source = prepared / "source"
    run_checked(["git", "init", str(source)])
    run_checked(["git", "-C", str(source), "fetch", "--depth=1", case["repo"]["url"], case["repo"]["commit"]])
    run_checked(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"])
    load_manifest(args.entries, source_root=source)
    load_labels(args.labels, source_root=source)
    (source / ".zvec-grep").mkdir()
    logs = prepared / "runtime"; cache = prepared / "model-cache"; index = prepared / "index"
    index.mkdir(); (index / "locks").mkdir()
    before_source = directory_identity(source, skip_git=True)
    started = time.monotonic()
    def run_preparation(command: list[str], phase: str, timeout: int) -> str:
        name = "zg-prepare-" + digest([str(output), phase])[:16]
        command[2:2] = ["--name", name]
        try:
            return run_checked(command, timeout=timeout, diagnostic_path=logs / f"{phase}-failure.json")
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
    command = docker_command(args.image, source, logs, cache, index=index)
    command += [args.image, "node", PREPARE_INDEX, "--root", "/app", "--package-dir", PACKAGE_DIR,
                "--embedding-model", EMBEDDING, "--model-cache-dir", "/models", "--log", "/logs/index-build.json"]
    print("Building a fresh local-embedding index; no model endpoint credentials are used.", flush=True)
    result = run_preparation(command, "build", 1800)
    (logs / "index-build.stdout.txt").write_text(result)
    if directory_identity(source, skip_git=True) != before_source: raise RuntimeError("Source mutated during preparation")
    before_index = directory_identity(index)
    snapshot_copy = working_index(index, prepared / "working-indexes" / "preflight")
    command = docker_command(args.image, source, logs, cache, index=snapshot_copy)
    flags = ["--root", "/app", "--package-dir", PACKAGE_DIR, "--embedding-model", EMBEDDING,
             "--model-cache-dir", "/models", "--working-copy"]
    run_preparation(command + [args.image, "node", BRIDGE, "preflight", *flags,
                           "--snapshot", "/logs/snapshot.json", "--log", "/logs/preflight.jsonl"], "preflight", 900)
    snapshot_file = logs / "snapshot.json"; snapshot = json.loads(snapshot_file.read_text())
    write(output / "runtime-manifest.json", {"protocol": plan["protocol"], "package": PACKAGE,
          "embedding_model": EMBEDDING, "source_repo": case["repo"], "source_identity": snapshot["source"],
          "index_preparation_seconds": time.monotonic() - started, "cross_ci_index_reuse": False,
          "image": json.loads(run_checked(["docker", "image", "inspect", args.image]))[0]["Id"],
          "ci_identity": {k: os.environ.get(k) for k in ("GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_RUN_ATTEMPT")},
          "new_model_calls": 0, "new_e2e_trials": 0,
          "index_policy": "Fresh build; immutable original seed and isolated writable copy for each distinct request."})
    for position, unit in enumerate(plan["units"], 1):
        uid = unit["unit_id"]; dest = output / "replay" / uid; dest.mkdir(parents=True)
        write(dest / "request.json", unit)
        copy = working_index(index, prepared / "working-indexes" / uid)
        name = "zg-replay-" + hashlib.sha256((str(output) + uid).encode()).hexdigest()[:16]
        command = docker_command(args.image, source, dest, cache, index=copy, snapshot=snapshot_file)
        command[2:2] = ["--name", name, "--network", "none"]
        command += [args.image, "node", "/opt/qa/replay-search.mjs", "--root", "/app", "--package-dir", PACKAGE_DIR,
                    "--embedding-model", EMBEDDING, "--model-cache-dir", "/models", "--snapshot", "/run/qa/snapshot.json",
                    "--log", "/logs/events.jsonl", "--request-file", "/logs/request.json", "--repetitions", "5"]
        print(json.dumps({"unit": uid, "position": position, "total_units": len(plan["units"])}), flush=True)
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=900)
            (dest / "stdout.jsonl").write_text(done.stdout); (dest / "stderr.txt").write_text(done.stderr)
            write(dest / "status.json", {"returncode": done.returncode, "status": "completed" if done.returncode == 0 else "failed"})
        except subprocess.TimeoutExpired as error:
            for filename, stream in (("stdout.jsonl", error.stdout), ("stderr.txt", error.stderr)):
                (dest / filename).write_bytes(stream if isinstance(stream, bytes) else (stream or "").encode())
            write(dest / "status.json", {"status": "timeout", "returncode": None})
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        if directory_identity(source, skip_git=True) != before_source or directory_identity(index) != before_index:
            write(output / "integrity-failure.json", {"unit": uid, "remaining_units_retained": True})
            break
    report = evaluate_replays(plan, output / "replay", labels, entries, snapshot)
    write(output / "replay-report.json", report)
    (output / "replay-report.md").write_text(replay_markdown(report))
    write(output / "final-integrity.json", {"source_unchanged": directory_identity(source, skip_git=True) == before_source,
                                           "seed_unchanged": directory_identity(index) == before_index,
                                           "embedding_weight_files": directory_identity(cache)})
    return report


def main(argv: list[str] | None = None) -> int:
    from .query_relevance import load_labels
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("analyze", "run"))
    parser.add_argument("--recorded-runs", required=True, type=Path)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--entries", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image", default="zg-readonly-qa:0.2.2")
    args = parser.parse_args(argv)
    if args.output.exists() and any(args.output.iterdir()): raise ValueError("Output must be new/empty")
    args.output.mkdir(parents=True, exist_ok=True)
    case = json.loads(args.case.read_text()); entries = load_manifest(args.entries); labels = load_labels(args.labels)
    if (labels["repo"] != case["repo"] or labels["original_question"] != case["question"]
            or labels["source_case_sha256"] != file_digest(args.case)
            or labels["legacy_task_entries_sha256"] != file_digest(args.entries)):
        raise ValueError("Query labels and case identify different tasks or corpus commits")
    for name, path in (("case.json", args.case), ("entries.json", args.entries), ("query-intents.json", args.labels)):
        shutil.copyfile(path, args.output / name)
    # No harvesting from unrelated cases, commits, or replacement runs.
    manifests = list(args.recorded_runs.glob("*/manifest.json"))
    if len(manifests) != 3: raise ValueError("Expected all three original group artifacts")
    combinations = set()
    for p in manifests:
        m = json.loads(p.read_text()); ci = m.get("ci_identity", {})
        combinations.add((m.get("agent"), m.get("model")))
        if (str(ci.get("GITHUB_RUN_ID")) != SOURCE_RUN or ci.get("GITHUB_SHA") != SOURCE_COMMIT
                or m["case_sha256"] != file_digest(args.case) or m.get("repo") != case["repo"]
                or m.get("package") != "@zvec/zvec-grep@0.2.2"):
            raise ValueError("Recorded corpus/CI identity differs from frozen development source")
    if combinations != {("opencode", "custom-openai/glm-5.2"), ("opencode", "custom-openai/qwen3.8-max"), ("qodercli", "qwen3.8-max")}:
        raise ValueError("Expected exactly the three original agent/model combinations")
    analysis = analyze(args.recorded_runs, question=case["question"])
    write_report(analysis, args.output / "first-query-analysis.json")
    changed = [item["path"] for item in analysis["input_artifacts"]
               if file_digest(args.recorded_runs / item["path"]) != item["sha256"]]
    write(args.output / "recorded-input-integrity.json", {"consumed_files": len(analysis["input_artifacts"]),
          "consumed_files_unchanged": not changed, "changed_paths": changed})
    if changed: raise ValueError("Recorded input changed during analysis")
    plan = build_plan(analysis, case["question"])
    write(args.output / "replay-plan.json", plan)
    observed = observed_scores(analysis, labels, entries)
    write(args.output / "observed-query-scores.json", observed)
    (args.output / "observed-query-scores.md").write_text(observed_markdown(observed))
    write(args.output / "provenance.json", {"source_run": SOURCE_RUN, "source_commit": SOURCE_COMMIT,
          "generated_at": datetime.now(UTC).isoformat(), "analysis_only": args.command == "analyze",
          "case_sha256": file_digest(args.case), "entries_sha256": file_digest(args.entries),
          "query_labels_sha256": file_digest(args.labels), "plan_sha256": file_digest(args.output / "replay-plan.json"),
          "new_model_calls": 0, "new_e2e_trials": 0})
    if args.command == "run":
        report = execute(plan, args, labels, entries)
        print(json.dumps({"planned_executions": report["planned_executions"], "scored_executions": report["scored_executions"]}))
        return 0 if report["scored_executions"] == report["planned_executions"] else 1
    print(json.dumps({"queries": plan["distinct_query_texts_including_original"], "planned_replays": plan["planned_executions"], "new_model_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
