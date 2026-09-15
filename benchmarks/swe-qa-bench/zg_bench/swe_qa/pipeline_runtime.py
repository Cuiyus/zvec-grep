"""Shared E2E preparation and post-E2E retrieval diagnostics for read-only QA v6.

The portable preparation is built once, before E2E, and copied to each consumer.
Every consumer checks both byte identities and the runtime's semantic snapshot.
No hosted model endpoint is contacted by either command in this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .readonly_run import (BRIDGE, EMBEDDING, PACKAGE, PACKAGE_DIR, PREPARE_INDEX,
    directory_identity, docker_command, run_checked, sha256, working_index)
from .retrieval_replay import (METRIC_PROFILE, digest, evaluate_replays, request_queries,
    score_cells, score_request, write)
from .embedding_integrity import compare_embedding_cache

PROTOCOL = "readonly-qa-v6"
COMBINATIONS = {("opencode", "custom-openai/glm-5.2"),
                ("opencode", "custom-openai/qwen3.8-max"), ("qodercli", "qwen3.8-max")}
PREPARED_MANIFEST = "prepared-manifest.json"


def _identity(run_id: str | None, commit: str | None, *, allow_evidence_origin: bool = False) -> dict:
    if not run_id or not commit:
        raise ValueError("Same-run pipeline requires explicit nonempty CI run ID and commit")
    evidence_run = os.environ.get("QA_EVIDENCE_RUN_ID")
    evidence_commit = os.environ.get("QA_EVIDENCE_COMMIT")
    if evidence_run or evidence_commit:
        if not allow_evidence_origin:
            raise ValueError("Evidence-origin override is only allowed for post-E2E diagnostics")
        if not evidence_run or not evidence_commit or not os.environ.get("GITHUB_RUN_ID") or not os.environ.get("GITHUB_SHA"):
            raise ValueError("Diagnostic resume requires both evidence and analysis CI identities")
        if str(run_id) != evidence_run or commit != evidence_commit:
            raise ValueError("Explicit identity differs from the declared evidence origin")
        return {"GITHUB_RUN_ID": str(run_id), "GITHUB_SHA": commit, "GITHUB_RUN_ATTEMPT": os.environ.get("QA_EVIDENCE_ATTEMPT"),
                "execution_kind": "post_e2e_diagnosis_resume",
                "analysis_ci_identity": {key: os.environ.get(key) for key in
                    ("GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_RUN_ATTEMPT")}}
    for key, value in (("GITHUB_RUN_ID", str(run_id)), ("GITHUB_SHA", commit)):
        observed = os.environ.get(key)
        if observed and observed != value:
            raise ValueError(f"Explicit identity differs from current {key}")
    return {"GITHUB_RUN_ID": str(run_id), "GITHUB_SHA": commit,
            "GITHUB_RUN_ATTEMPT": os.environ.get("GITHUB_RUN_ATTEMPT")}


def _flags() -> list[str]:
    return ["--root", "/app", "--package-dir", PACKAGE_DIR, "--embedding-model", EMBEDDING,
            "--model-cache-dir", "/models", "--working-copy"]


def _replay_provenance(shared: dict, prepared_manifest_sha256: str) -> dict:
    """Describe a validated E2E cohort independently of the CI running diagnostics.

    Explicit CLI run/commit arguments identify the evidence, not the executing CI.
    An absent execution identity therefore remains unknown even with those args.
    """
    keys = ("GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_RUN_ATTEMPT")
    evidence = {key: shared["ci_identity"].get(key) for key in keys}
    analysis = {key: os.environ.get(key) or None for key in keys}
    execution_keys = ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
    known = all(identity.get(key) for identity in (evidence, analysis) for key in execution_keys)
    cross_ci = any(str(evidence[key]) != str(analysis[key]) for key in execution_keys) if known else None
    return {"source_run": evidence["GITHUB_RUN_ID"], "source_commit": evidence["GITHUB_SHA"],
        "prepared_manifest_sha256": prepared_manifest_sha256,
        "evidence_ci_identity": evidence, "analysis_ci_identity": analysis,
        "cross_ci_index_reuse": cross_ci, "same_e2e_cohort": True,
        "index_reuse_between_e2e_cohorts": False, "index_reuse_scope": "same_e2e_cohort"}


def _snapshot_identity(snapshot: dict, case: dict) -> None:
    if (snapshot.get("source", {}).get("git_commit") != case["repo"]["commit"]
            or snapshot.get("package", {}).get("name") != "@zvec/zvec-grep"
            or snapshot.get("package", {}).get("version") != "0.2.2"
            or not snapshot.get("source", {}).get("sha256")
            or not snapshot.get("index", {}).get("documents", {}).get("sha256")):
        raise ValueError("Prepared snapshot identifies a different source/package or lacks semantic identity")


def validate_prepared(prepared: Path, case_path: Path, *, run_id: str, commit: str) -> dict:
    """Validate relocatable content; no absolute host paths are hashed as identity."""
    ci = _identity(run_id, commit, allow_evidence_origin=True)
    case = json.loads(case_path.read_text())
    manifest = json.loads((prepared / PREPARED_MANIFEST).read_text())
    if (manifest.get("protocol") != PROTOCOL or not manifest.get("image_id") or manifest.get("package") != PACKAGE
            or manifest.get("embedding_model") != EMBEDDING
            or manifest.get("case_sha256") != sha256(case_path)
            or manifest.get("repo") != case["repo"] or manifest.get("case_id") != case["case_id"]
            or any(manifest.get("ci_identity", {}).get(k) != ci[k] for k in ("GITHUB_RUN_ID", "GITHUB_SHA"))):
        raise ValueError("Prepared runtime is not from this CI run, commit, case and package")
    for name in ("source", "index", "model-cache"):
        expected = manifest.get("file_identities", {}).get(name)
        observed = directory_identity(prepared / name, skip_git=name == "source")
        matches = compare_embedding_cache(expected, observed)["valid"] if name == "model-cache" else observed == expected
        if not expected or not matches:
            raise ValueError(f"Prepared {name} content hash mismatch or incomplete manifest")
    snapshot_file = prepared / "runtime" / "snapshot.json"
    if not snapshot_file.is_file() or sha256(snapshot_file) != manifest.get("snapshot_sha256"):
        raise ValueError("Prepared snapshot hash mismatch")
    _snapshot_identity(json.loads(snapshot_file.read_text()), case)
    return manifest


def copy_prepared(prepared: Path, destination: Path, case_path: Path, *, run_id: str, commit: str) -> dict:
    manifest = validate_prepared(prepared, case_path, run_id=run_id, commit=commit)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Prepared destination must be new/empty")
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("source", "index", "model-cache", "runtime"):
        shutil.copytree(prepared / name, destination / name, symlinks=True)
    shutil.copyfile(prepared / PREPARED_MANIFEST, destination / PREPARED_MANIFEST)
    validate_prepared(destination, case_path, run_id=run_id, commit=commit)
    return manifest


def verify_prepared_semantics(prepared: Path, *, image: str, logs: Path, working: Path) -> None:
    shared = json.loads((prepared / PREPARED_MANIFEST).read_text())
    current_image = json.loads(run_checked(["docker", "image", "inspect", image]))[0]["Id"]
    if not shared.get("image_id") or current_image != shared["image_id"]:
        raise ValueError("Consumer image differs from the exact image used to prepare this CI runtime")
    copy = working_index(prepared / "index", working)
    command = docker_command(image, prepared / "source", logs, prepared / "model-cache", index=copy,
                             snapshot=prepared / "runtime" / "snapshot.json")
    name = "zg-v6-verify-" + digest([str(logs), str(working)])[:16]
    command[2:2] = ["--name", name, "--network", "none"]
    try:
        run_checked(command + [image, "node", BRIDGE, "verify", *_flags(),
                    "--snapshot", "/run/qa/snapshot.json", "--log", "/logs/verification.jsonl"],
                    timeout=900, diagnostic_path=logs / "failure.json")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


def prepare(case_path: Path, output: Path, *, image: str, run_id: str, commit: str) -> dict:
    ci = _identity(run_id, commit)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Preparation output must be new/empty")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    case = json.loads(case_path.read_text())
    source = output / "source"
    run_checked(["git", "init", str(source)])
    run_checked(["git", "-C", str(source), "fetch", "--depth=1", case["repo"]["url"], case["repo"]["commit"]])
    run_checked(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"])
    if run_checked(["git", "-C", str(source), "rev-parse", "HEAD"]) != case["repo"]["commit"]:
        raise ValueError("Prepared source checkout differs from case")
    (source / ".zvec-grep").mkdir(exist_ok=True)
    before = directory_identity(source, skip_git=True)
    index = output / "index"; index.mkdir(); (index / "locks").mkdir()
    logs = output / "runtime"; cache = output / "model-cache"
    name = "zg-v6-build-" + digest([run_id, commit, str(output)])[:16]
    command = docker_command(image, source, logs, cache, index=index)
    command[2:2] = ["--name", name]
    try:
        stdout = run_checked(command + [image, "node", PREPARE_INDEX, "--root", "/app",
            "--package-dir", PACKAGE_DIR, "--embedding-model", EMBEDDING,
            "--model-cache-dir", "/models", "--log", "/logs/index-build.json"],
            timeout=1800, diagnostic_path=logs / "build-failure.json")
        (logs / "index-build.stdout.txt").write_text(stdout)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
    index_before = directory_identity(index)
    working = output / "preflight-working-index"
    copy = working_index(index, working)
    command = docker_command(image, source, logs, cache, index=copy)
    command[2:2] = ["--name", name + "-preflight", "--network", "none"]
    try:
        run_checked(command + [image, "node", BRIDGE, "preflight", *_flags(),
            "--snapshot", "/logs/snapshot.json", "--log", "/logs/preflight.jsonl"],
            timeout=900, diagnostic_path=logs / "preflight-failure.json")
    finally:
        subprocess.run(["docker", "rm", "-f", name + "-preflight"], capture_output=True, timeout=30)
    shutil.rmtree(working)
    if directory_identity(source, skip_git=True) != before or directory_identity(index) != index_before:
        raise RuntimeError("Source or seed changed during preparation preflight")
    snapshot_file = logs / "snapshot.json"
    _snapshot_identity(json.loads(snapshot_file.read_text()), case)
    manifest = {"schema_version": 1, "protocol": PROTOCOL, "created_at": datetime.now(UTC).isoformat(),
        "ci_identity": ci, "case_id": case["case_id"], "case_sha256": sha256(case_path), "repo": case["repo"],
        "package": PACKAGE, "embedding_model": EMBEDDING, "snapshot_sha256": sha256(snapshot_file),
        "image_id": json.loads(run_checked(["docker", "image", "inspect", image]))[0]["Id"],
        "file_identities": {"source": before, "index": index_before, "model-cache": directory_identity(cache)},
        "wall_seconds": round(time.monotonic() - started, 3), "cross_ci_index_reuse": False,
        "new_model_calls": 0, "new_e2e_trials": 0, "retrieval_probes_before_e2e": 0,
        "index_policy": "One seed built in this CI run; all E2E groups and diagnostic requests use isolated copies."}
    write(output / PREPARED_MANIFEST, manifest)
    validate_prepared(output, case_path, run_id=run_id, commit=commit)
    return manifest


def validate_group_manifests(recorded_runs: Path, case_path: Path, prepared: Path, *, run_id: str, commit: str) -> dict:
    shared = validate_prepared(prepared, case_path, run_id=run_id, commit=commit)
    identity = sha256(prepared / PREPARED_MANIFEST)
    paths = sorted(recorded_runs.glob("*/manifest.json"))
    if len(paths) != 3:
        raise ValueError("Expected exactly three current-run E2E group manifests")
    combinations = []
    embedding_checks = {}
    for path in paths:
        value = json.loads(path.read_text())
        embedding_checks[path.parent.name] = compare_embedding_cache(
            value.get("embedding_weight_files_before_e2e"), value.get("embedding_weight_files_after_e2e"))
        combinations.append((value.get("agent"), value.get("model")))
        if (value.get("protocol") != PROTOCOL or not value.get("e2e_only")
                or value.get("prepared_manifest_sha256") != identity
                or value.get("case_sha256") != shared["case_sha256"] or value.get("repo") != shared["repo"]
                or value.get("case_id") != shared["case_id"] or value.get("package") != PACKAGE
                or value.get("embedding_model") != EMBEDDING
                or value.get("repetitions_per_profile") != 5 or value.get("image_id") != shared.get("image_id")
                or any(value.get("ci_identity", {}).get(k) != shared["ci_identity"][k]
                       for k in ("GITHUB_RUN_ID", "GITHUB_SHA"))
                or value.get("source_files") != shared["file_identities"]["source"]
                or value.get("index_files") != shared["file_identities"]["index"]
                or value.get("embedding_weight_files_before_e2e") != shared["file_identities"]["model-cache"]
                or not embedding_checks[path.parent.name]["valid"]):
            raise ValueError(f"E2E group does not match current frozen runtime: {path.parent.name}")
        plan = json.loads((path.parent / "plan.json").read_text())
        trials = plan.get("trials", [])
        if (len(trials) != 10 or len({t.get("trial_id") for t in trials}) != 10
                or {t.get("repetition") for t in trials if t.get("profile") == "baseline"} != set(range(1, 6))
                or {t.get("repetition") for t in trials if t.get("profile") == "zvec-grep"} != set(range(1, 6))):
            raise ValueError("E2E plan does not retain all five paired trials")
        for trial in trials:
            result_path = path.parent / trial["trial_id"] / "result.json"
            if not result_path.is_file():
                if trial.get("status") == "planned":
                    continue
                raise ValueError("Executed E2E trial lacks its integrity result")
            result = json.loads(result_path.read_text())
            if result.get("source_unchanged") is not True or result.get("original_seed_unchanged") is not True:
                raise ValueError("E2E source or seed integrity was not confirmed")
            if trial["profile"] == "zvec-grep" and result.get("working_index_semantic_unchanged") is not True:
                raise ValueError("E2E working-index semantic integrity was not confirmed")
    if len(set(combinations)) != 3 or set(combinations) != COMBINATIONS:
        raise ValueError("Expected the three protocol agent/model combinations")
    return {"source_run": str(run_id), "source_commit": commit, "prepared_manifest_sha256": identity,
            "group_manifest_sha256": {p.parent.name: sha256(p) for p in paths},
            "embedding_integrity": embedding_checks,
            "analysis_ci_identity": {key: os.environ.get(key) for key in ("GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_RUN_ATTEMPT")},
            "validated": True}


def build_v6_plan(analysis: dict, question: str, *, source_run: str, source_commit: str,
                  prepared_manifest_sha256: str, repetitions: int = 5) -> dict:
    if repetitions != 5:
        raise ValueError("The v6 protocol requires five repetitions per unique complete request")
    units = []
    annotations = analysis["annotation_catalog"]
    for item in analysis["request_catalog"]:
        request = item["request"]
        labels = [a for a in annotations if a["request_id"] == item["request_id"] and a.get("occurrences")]
        if not labels:
            raise ValueError("Recorded request has no frozen contextual annotation unit")
        units.append({"unit_id": "faithful-" + digest(request)[:20], "request_id": item["request_id"],
            "kind": "faithful", "mode": "recorded-request", "request": request,
            "query_texts": request_queries(request), "occurrences": item["occurrences"], "annotations": labels,
            "quality_unit": "Joint complete request output, scored separately for each observed prior-feedback context"})
    originals = [a for a in annotations if a["kind"] == "original"]
    expected_original = {"root": "/app", "query": question, "limit": 10, "autoUpdate": False, "trace": True}
    if len(originals) != 1 or originals[0]["request"] != expected_original:
        raise ValueError("Original question must have exactly the protocol-fixed hybrid annotation request")
    units.sort(key=lambda u: u["unit_id"])
    units.append({"unit_id": "original-hybrid", "kind": "original", "mode": "hybrid",
        "request_id": originals[0]["request_id"], "request": expected_original,
        "query_texts": [question], "occurrences": [], "annotations": originals,
        "quality_unit": "Protocol-defined original hybrid request; not an Agent-selected request"})
    same_original = next((u for u in units[:-1] if u["request"] == expected_original), None)
    if same_original:
        units[-1]["execution_unit_id"] = same_original["unit_id"]
        units[-1]["quality_unit"] += "; references the identical recorded request's five executions without rerunning it"
    if len({u["unit_id"] for u in units}) != len(units):
        raise ValueError("Duplicate request catalog entries or replay unit hash collision")
    return {"schema_version": 3, "protocol": PROTOCOL, "question": question,
        "source_run": str(source_run), "source_commit": source_commit,
        "prepared_manifest_sha256": prepared_manifest_sha256, "metric_profile": METRIC_PROFILE,
        "repetitions": repetitions, "quality_repetition": 1, "independent_tasks": 1,
        "planned_executions": (len(units) - bool(same_original)) * repetitions, "faithful_units": len(units) - 1,
        "unique_execution_units": len(units) - bool(same_original), "reported_units": len(units),
        "original_units": 1, "controlled_units": 0, "include_mode_diagnostics": False,
        "unreplayable_planned_trials_or_calls": analysis.get("unreplayable_occurrences", []),
        "report_groups": {"primary": {"enabled": True, "unit_ids": [u["unit_id"] for u in units[:-1]]},
            "original": {"enabled": True, "unit_ids": ["original-hybrid"]},
            "diagnostic": {"enabled": False, "unit_ids": []}},
        "comparison_policy": "Same-E2E-cohort full requests, all contexts retained. Original hybrid separate; no best-of-repeat selection.",
        "units": units}


def observed_scores_v6(analysis: dict, labels: dict, entries: dict) -> list[dict]:
    by_annotation = {a["annotation_id"]: a for a in analysis["annotation_catalog"]}
    rows = []
    for group in analysis["groups"]:
        for trial in group["trials"]:
            if trial.get("profile") != "zvec-grep":
                continue
            rounds = trial.get("zg_decision_rounds", [])
            if not rounds:
                rows.append({"group": group["group"], "trial_id": trial["trial_id"],
                    "status": "no_zg_call" if trial.get("zg_adoption_observed") is False else "unknown",
                    "request_scores": None})
            for decision in rounds:
                for call in decision["zg_calls"]:
                    annotation = by_annotation.get(call.get("annotation_id"))
                    text = call.get("visible_text")
                    known = annotation is not None and isinstance(text, str)
                    rows.append({"group": group["group"], "trial_id": trial["trial_id"], "call_id": call["call_id"],
                        "native_call_id": call.get("id"), "message_id": call.get("message_id"),
                        "annotation_id": annotation["annotation_id"] if annotation else None,
                        "context_id": annotation["context_id"] if annotation else None,
                        "model_turn_index": decision["model_turn_index"],
                        "zg_decision_round_index": decision["zg_decision_round_index"],
                        "status": "scored" if known else "unknown", "output_source": call.get("result_source"),
                        "output_sha256": hashlib.sha256(text.encode()).hexdigest() if isinstance(text, str) else None,
                        "request_scores": score_request(text, annotation["request"], labels, entries,
                            context_id=annotation["context_id"]) if known else None})
    return rows


def v6_replay_markdown(report: dict) -> str:
    lines = ["# 本轮 E2E 查询的检索诊断", "",
        f"完整性及执行校验通过 {report['scored_executions']} / {report['planned_executions']} 次；单个 QA，固定第 1 次质量观察。", "",
        "同一完整请求执行一次五次回放；按实际上下文分别评分。unknown 不计零，重复和子查询不增加独立 QA 样本。", ""]
    for kind, title in (("faithful", "真实完整请求"), ("original", "原题固定 hybrid 参照")):
        lines += [f"## {title}", "", "| request | context | query 类型 | 目标排名 | Hit@1 | Hit@5 | Hit@10 | RR@10 | 原任务入口排名 | 输出一致 |",
                  "|---|---|---|---:|---|---|---|---:|---:|---|"]
        for unit in report["units"]:
            if unit["kind"] != kind:
                continue
            for context in unit["quality_observation"].get("context_scores", []):
                scores = (context.get("request_scores") or {}).get("native")
                lines.append("| " + " | ".join([unit["unit_id"], context["context_id"], *score_cells(scores),
                    str(unit["stability"]["all_public_outputs_identical"])]) + " |")
        lines.append("")
    lines.append("本次回放与 E2E 当时返回分别保存。命中表示已标注入口出现，不证明答案完整或成本下降的因果关系。")
    return "\n".join(lines) + "\n"


def execute_from_prepared(plan: dict, args: argparse.Namespace, labels: dict, entries: dict) -> dict:
    from .query_relevance import load_labels
    from .retrieval_eval import load_manifest
    output = args.output.resolve()
    prepared = output / "preparation"
    shared = copy_prepared(args.prepared_dir, prepared, args.case, run_id=args.run_id, commit=args.commit)
    if sha256(prepared / PREPARED_MANIFEST) != plan["prepared_manifest_sha256"]:
        raise ValueError("Replay plan refers to another prepared runtime")
    source = prepared / "source"; index = prepared / "index"; cache = prepared / "model-cache"
    load_manifest(args.entries, source_root=source)
    load_labels(args.labels, source_root=source)
    verify_prepared_semantics(prepared, image=args.image, logs=prepared / "verification",
                              working=prepared / "working-indexes" / "verification")
    validate_prepared(prepared, args.case, run_id=args.run_id, commit=args.commit)
    snapshot_file = prepared / "runtime" / "snapshot.json"
    snapshot = json.loads(snapshot_file.read_text())
    provenance = _replay_provenance(shared, plan["prepared_manifest_sha256"])
    write(output / "runtime-manifest.json", {"protocol": PROTOCOL, "metric_profile": METRIC_PROFILE,
        "package": PACKAGE, "embedding_model": EMBEDDING, "source_identity": snapshot["source"],
        **provenance, "snapshot_sha256": sha256(snapshot_file),
        "ci_identity": _identity(args.run_id, args.commit, allow_evidence_origin=True),
        "new_index_builds": 0, "new_model_calls": 0, "new_e2e_trials": 0,
        "image": json.loads(run_checked(["docker", "image", "inspect", args.image]))[0]["Id"]})
    for position, unit in enumerate(plan["units"], 1):
        if unit.get("execution_unit_id", unit["unit_id"]) != unit["unit_id"]:
            continue
        dest = output / "replay" / unit["unit_id"]; dest.mkdir(parents=True)
        write(dest / "request.json", unit)
        working = working_index(index, prepared / "working-indexes" / unit["unit_id"])
        name = "zg-v6-replay-" + digest([str(output), unit["unit_id"]])[:16]
        command = docker_command(args.image, source, dest, cache, index=working, snapshot=snapshot_file)
        command[2:2] = ["--name", name, "--network", "none"]
        command += [args.image, "node", "/opt/qa/replay-search.mjs", "--root", "/app", "--package-dir", PACKAGE_DIR,
            "--embedding-model", EMBEDDING, "--model-cache-dir", "/models", "--snapshot", "/run/qa/snapshot.json",
            "--log", "/logs/events.jsonl", "--request-file", "/logs/request.json", "--repetitions", "5"]
        print(json.dumps({"unit": unit["unit_id"], "position": position, "total_units": len(plan["units"])}), flush=True)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=900)
            (dest / "stdout.jsonl").write_text(result.stdout); (dest / "stderr.txt").write_text(result.stderr)
            write(dest / "status.json", {"status": "completed" if result.returncode == 0 else "failed", "returncode": result.returncode})
        except subprocess.TimeoutExpired as error:
            for filename, stream in (("stdout.jsonl", error.stdout), ("stderr.txt", error.stderr)):
                (dest / filename).write_bytes(stream if isinstance(stream, bytes) else (stream or "").encode())
            write(dest / "status.json", {"status": "timeout", "returncode": None})
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
            shutil.rmtree(working)
        try:
            validate_prepared(prepared, args.case, run_id=args.run_id, commit=args.commit)
        except ValueError as error:
            write(output / "integrity-failure.json", {"unit": unit["unit_id"], "error": str(error), "remaining_units_retained": True})
            break
    report = evaluate_replays(plan, output / "replay", labels, entries, snapshot)
    embedding_check = compare_embedding_cache(shared["file_identities"]["model-cache"], directory_identity(cache))
    final = {"source_unchanged": directory_identity(source, skip_git=True) == shared["file_identities"]["source"],
        "seed_unchanged": directory_identity(index) == shared["file_identities"]["index"],
        "embedding_weights_unchanged": embedding_check["valid"],
        "embedding_cache_directory_unchanged": embedding_check["cache_directory_unchanged"],
        "embedding_integrity": embedding_check}
    write(output / "final-integrity.json", final)
    report["prepared_runtime_integrity"] = final
    report["evidence_provenance"] = provenance
    report["same_run_provenance"] = {"source_run": args.run_id, "source_commit": args.commit,
        "prepared_manifest_sha256": plan["prepared_manifest_sha256"],
        "deprecated": True, "replacement": "evidence_provenance",
        "note": "Legacy name identifies one E2E cohort, not necessarily one CI execution."}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate current-run group/runtime identities before paid annotation")
    for flag in ("recorded-runs", "prepared-dir", "case"):
        validate.add_argument("--" + flag, required=True, type=Path)
    validate.add_argument("--run-id", required=True)
    validate.add_argument("--commit", required=True)
    for name in ("prepare", "replay"):
        child = commands.add_parser(name)
        child.add_argument("--case", required=True, type=Path)
        child.add_argument("--output", required=True, type=Path)
        child.add_argument("--image", default="zg-readonly-qa:0.2.2")
        child.add_argument("--run-id", required=True)
        child.add_argument("--commit", required=True)
        if name == "replay":
            for flag in ("analysis", "recorded-runs", "entries", "labels", "prepared-dir"):
                child.add_argument("--" + flag, required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "validate":
        result = validate_group_manifests(args.recorded_runs, args.case, args.prepared_dir,
                                         run_id=args.run_id, commit=args.commit)
        print(json.dumps(result))
        return 0
    if args.command == "prepare":
        result = prepare(args.case, args.output, image=args.image, run_id=args.run_id, commit=args.commit)
        print(json.dumps({"status": "prepared", "wall_seconds": result["wall_seconds"],
                          "prepared_manifest_sha256": sha256(args.output / PREPARED_MANIFEST)}))
        return 0
    from .query_relevance import load_labels
    from .retrieval_eval import load_manifest
    from .query_trajectory import analyze
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Replay output must be new/empty")
    args.output.mkdir(parents=True, exist_ok=True)
    provenance = validate_group_manifests(args.recorded_runs, args.case, args.prepared_dir,
                                          run_id=args.run_id, commit=args.commit)
    case = json.loads(args.case.read_text())
    analysis = json.loads(args.analysis.read_text())
    regenerated = analyze(args.recorded_runs, question=case["question"])
    # Re-analysis anchors the frozen catalog to the actual current-run native logs.
    for key in ("request_catalog", "annotation_catalog", "input_artifacts"):
        if regenerated[key] != analysis[key]:
            raise ValueError(f"Frozen analysis differs from current-run native evidence: {key}")
    entries = load_manifest(args.entries, source_root=args.prepared_dir / "source")
    labels = load_labels(args.labels, source_root=args.prepared_dir / "source")
    if (labels.get("repo") != case["repo"] or labels.get("source_case_sha256") != sha256(args.case)
            or labels.get("original_question") != case["question"]
            or labels.get("legacy_task_entries_sha256") != sha256(args.entries)
            or labels.get("schema_version") != 2 or labels.get("analysis_sha256") != sha256(args.analysis)):
        raise ValueError("Shared labels do not identify this frozen QA/source")
    expected_bindings = [{"request": a["request"], "context_id": a["context_id"], "query_id": a["annotation_id"]}
                         for a in analysis["annotation_catalog"]]
    if sorted(map(digest, labels.get("request_bindings", []))) != sorted(map(digest, expected_bindings)):
        raise ValueError("Shared labels do not cover exactly the frozen request-and-context catalog")
    for name, path in (("case.json", args.case), ("entries.json", args.entries),
                       ("query-intents.json", args.labels), ("query-trajectory.json", args.analysis)):
        shutil.copyfile(path, args.output / name)
    plan = build_v6_plan(analysis, case["question"], source_run=args.run_id, source_commit=args.commit,
                         prepared_manifest_sha256=provenance["prepared_manifest_sha256"])
    write(args.output / "replay-plan.json", plan)
    observed = observed_scores_v6(analysis, labels, entries)
    write(args.output / "observed-query-scores.json", observed)
    provenance.update(protocol=PROTOCOL, metric_profile=METRIC_PROFILE, analysis_sha256=sha256(args.analysis),
        labels_sha256=sha256(args.labels), plan_sha256=sha256(args.output / "replay-plan.json"),
        new_model_calls=0, new_e2e_trials=0, new_index_builds=0)
    write(args.output / "provenance.json", provenance)
    report = execute_from_prepared(plan, args, labels, entries)
    report["observed_e2e_calls"] = observed
    write(args.output / "replay-report.json", report)
    (args.output / "replay-report.md").write_text(v6_replay_markdown(report))
    print(json.dumps({"planned_executions": report["planned_executions"], "scored_executions": report["scored_executions"]}))
    return 0 if (report["scored_executions"] == report["planned_executions"]
                 and all(report["prepared_runtime_integrity"].get(key) is True for key in
                         ("source_unchanged", "seed_unchanged", "embedding_weights_unchanged"))) else 1


if __name__ == "__main__":
    raise SystemExit(main())
