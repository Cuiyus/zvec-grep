"""Node 0.2.2 versus frozen Rust main comparison: both arms run the unchanged zvec-grep profile.

The frozen scorer uses legacy slots baseline/zvec-grep. Here these slots mean
before+zg/after+zg, explicitly recorded in every pair and report. Neither arm
is a no-zg control. Scoring prompts, references and session accounting are
provided by the existing harness without changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

from zg_bench.metrics.usage import compatible_usage_scope
from zg_bench.swe_qa.collect import (
    _completed_job, _profile_result, _select_trials,
)
from zg_bench.swe_qa.judge import judge_pairs
from zg_bench.reports.validation import validate_task_report

SLOTS = {"node": "baseline", "rust": "zvec-grep"}
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "zg_bench/swe_qa/data"
EXPERIMENT = json.loads((ROOT.parent / "version-comparison/experiment.json").read_text())
COMMITS = {v: EXPERIMENT[v]["source_commit"] for v in SLOTS}
REPETITIONS = EXPERIMENT["e2e_repetitions"]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def identity(package_dir, variant):
    package = package_dir / "candidate.tgz"
    subprocess.run([
        "node", str(ROOT.parent / "shared/rust-package-cache.mjs"),
        "verify", "--directory", str(package_dir), "--commit", COMMITS[variant],
    ], check=True)
    with tarfile.open(package) as archive:
        metadata = json.load(archive.extractfile("package/package.json"))
        entrypoint = metadata["bin"]["zg"] if isinstance(metadata["bin"], dict) else metadata["bin"]
        if variant == "node":
            if metadata["version"] != "0.2.2" or entrypoint != "dist/cli/index.js":
                raise ValueError("Wrong Node release")
            if hashlib.sha256(package.read_bytes()).hexdigest() != EXPERIMENT["node"]["tarball_sha256"]:
                raise ValueError("Published Node tarball mismatch")
        elif entrypoint != "bin/zg":
            raise ValueError("Rust package must expose bin/zg")
        binary = archive.extractfile("package/" + entrypoint)
        if binary is None:
            raise ValueError("Pinned package has no native zg binary")
        binary_sha = hashlib.file_digest(binary, "sha256").hexdigest()
    return {
        "source_commit": COMMITS[variant],
        "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "cli_sha256": binary_sha,
        "actual_harness_profile": "zvec-grep",
        "comparison_slot": SLOTS[variant],
    }


def version_order(task, repetition):
    # Deterministic alternating order, balanced across all 20 tasks.
    tasks = json.loads((DATA / "selection.json").read_text())["tasks"]
    position = next(i for i, item in enumerate(tasks) if item["task_slug"] == task)
    return ("node", "rust") if (position + repetition) % 2 else ("rust", "node")


def collect(root, task, identities, repetitions=REPETITIONS, expected_model=None, expected_embedding=None):
    profiles = {}
    for variant, slot in SLOTS.items():
        trials = []
        for rep in range(1, repetitions + 1):
            repetition_root = root / "runs" / variant / f"r{rep}"
            # Harbor only appends profile suffixes for multi-profile runs.
            # Single-profile jobs retain --job-name verbatim. Identify their
            # immediate job directory, then apply the unchanged evidence gates.
            jobs = [p for p in repetition_root.glob("*") if p.is_dir()
                    and (p / "result.json").is_file()
                    and any(p.glob("*/agent/trajectory.json"))]
            if len(jobs) != 1:
                raise ValueError(f"{variant} repetition {rep}: expected one zg job, got {len(jobs)}")
            job = jobs[0]
            _completed_job(job, expected_trials=1)
            selected = _select_trials(job, task, expected_trials=1)
            trial_dir, result = selected[0]
            setup = json.loads((trial_dir / "agent/zvec-grep-setup.json").read_text())
            if setup.get("status") != "ready":
                raise ValueError("zg setup did not reach ready")
            if expected_embedding and setup.get("embedding_model") != expected_embedding:
                raise ValueError("Installed embedding model differs from the experiment")
            for key in ("package_sha256", "cli_sha256"):
                if setup.get(key) != identities[variant][key]:
                    raise ValueError(f"{variant}: installed {key} mismatch")
            if expected_model:
                usage = json.loads((trial_dir / "agent/session-usage.json").read_text())
                models = {(m["provider_id"], m["model_id"]) for s in usage["sessions"] for m in s["provider_models"]}
                if models != {("custom-openai", expected_model)}:
                    raise ValueError(f"Actual session models differ from the experiment: {models}")
            row = _profile_result(
                profile="zvec-grep", job_dir=job, trial_dir=trial_dir,
                result=result, trial_index=rep,
            )
            row.update(variant=variant, provenance=identities[variant])
            trials.append(row)
        profiles[slot] = {
            "profile": "zvec-grep", "variant": variant,
            "trial_count": repetitions, "trials": trials,
        }
    return {
        "schema_version": 2, "task_id": task, "task_slug": task,
        "valid": True, "expected_trials": repetitions, "actual_trials": repetitions,
        "profiles": profiles,
        "usage_scope": compatible_usage_scope([t for p in profiles.values() for t in p["trials"]]),
        "comparison_kind": "node022-vs-rust-main",
        "comparison_labels": {"baseline": "Node 0.2.2+zg", "zvec-grep": "Rust a09cd1236eee+zg"},
        "provenance": identities,
    }


def run(args):
    output = args.output.resolve()
    packages = args.packages.resolve()
    identities = {v: identity(packages / v, v) for v in SLOTS}
    if identities["node"]["cli_sha256"] == identities["rust"]["cli_sha256"]:
        raise ValueError("Before and after binaries are identical")
    config = json.loads((ROOT / "ci-config.json").read_text())
    embedding = config["embedding_models"][args.embedding]
    meta = {
        "model": args.model, "embedding": embedding, "task": args.task,
        "repetitions": REPETITIONS, "provenance": identities,
        "harness_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "locked_input_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (
            "zg_bench/swe_qa/data/selection.json", "zg_bench/swe_qa/data/references.json",
            "zg_bench/swe_qa/data/index.ignore", "uv.lock", "ci-config.json",
        )}, "execution": [],
    }
    if args.continue_after_failure:
        meta["execution_policy"] = "attempt_all_five_pairs_and_retain_failures_before_collection"
    if args.collect_only:
        recovered = json.loads((output / "provenance.json").read_text())
        for key in ("model", "embedding", "task", "repetitions", "provenance", "locked_input_sha256"):
            if recovered[key] != meta[key]:
                raise ValueError(f"Recovered evidence has mismatched {key}")
        meta = recovered
        meta["collector_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        meta["evidence_run"] = os.environ.get("VERSION_EVIDENCE_RUN")
    write_json(output / "provenance.json", meta)
    for rep in ([] if args.collect_only else range(1, REPETITIONS + 1)):
        for variant in version_order(args.task, rep):
            env = dict(os.environ, ZG_BENCH_EXPECTED_CLI_SHA256=identities[variant]["cli_sha256"], ZG_BENCH_CLI_RUNTIME=variant)
            command = [
                "zg-bench", "run", "swe-qa-bench", "--tier", "full", "--task", args.task,
                "--agent", "opencode", "--model", f"custom-openai/{args.model}",
                "--profile", "zvec-grep", "--n-attempts", "1", "--max-retries", "2",
                "--embedding-model", embedding,
                "--zvec-grep-package", str(packages / variant / "candidate.tgz"),
                "--jobs-dir", str(output / "runs" / variant / f"r{rep}"),
                "--job-name", f"node-rust-{args.task}-{variant}-r{rep}",
            ]
            if args.embedding == "remote":
                endpoint = os.environ.get("ZVEC_GREP_ENDPOINT")
                if not endpoint or not os.environ.get("ZVEC_GREP_API_KEY"):
                    raise ValueError("Remote embedding requires the configured endpoint and credential")
                command += ["--embedding-endpoint", endpoint]
            started = time.monotonic()
            result = subprocess.run(command, env=env)
            meta["execution"].append({"repetition": rep, "variant": variant,
                "wall_seconds_including_setup_and_retries": time.monotonic() - started,
                "returncode": result.returncode})
            write_json(output / "provenance.json", meta)
            if not args.continue_after_failure:
                result.check_returncode()
    failed_executions = [row for row in meta["execution"] if row["returncode"] != 0]
    if failed_executions:
        raise RuntimeError(
            f"{len(failed_executions)} of {len(meta['execution'])} frozen executions failed; "
            "all attempted executions are retained and no incomplete quality aggregate is published"
        )
    pair = collect(output, args.task, identities, expected_model=args.model, expected_embedding=embedding)
    write_json(output / "pair.json", pair)
    cached_report = output / "report/report.json"
    if args.collect_only and cached_report.exists():
        report = json.loads(cached_report.read_text())
        case = validate_task_report(report, cached_report)
        if case["task_id"].replace(":", "-") != args.task or report["judge"]["model"] != args.model:
            raise ValueError("Existing blind scores belong to a different task or model")
        if report.get("comparison_kind") != pair["comparison_kind"] or report["provenance"]["provenance"] != identities:
            raise ValueError("Existing blind scores belong to different program versions")
        for slot in SLOTS.values():
            for judged, original in zip(case["profiles"][slot]["trials"], pair["profiles"][slot]["trials"], strict=True):
                if judged["trial_name"] != original["trial_name"] or any(
                    judged["metrics"][key] != original[key] for key in ("input_tokens", "output_tokens", "tool_calls", "agent_wall_seconds")
                ):
                    raise ValueError("Existing blind scores do not match the recovered trial evidence")
        meta["blind_judgements_reused_from_run"] = os.environ.get("VERSION_REPORT_RUN")
    else:
        report = judge_pairs(
            pairs_root=output / "pair.json", references_path=DATA / "references.json",
            output_dir=output / "report", expected=[args.task], model=args.model,
        )
    report.update(comparison_kind=pair["comparison_kind"], comparison_labels=pair["comparison_labels"], provenance=meta)
    write_json(output / "report/report.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--model", choices=("glm-5.2", "qwen3.8-max"), required=True)
    parser.add_argument("--embedding", choices=("local", "remote"), required=True)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collect-only", action="store_true", help="Validate and judge existing completed trials without executing an agent")
    parser.add_argument("--continue-after-failure", action="store_true", help="Attempt all five pairs in an isolated retry, retaining failures without publishing incomplete scores")
    run(parser.parse_args())
