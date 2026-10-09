"""PR179 adoption comparison: both arms run the unchanged zvec-grep profile.

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
    _completed_job, _job_dirs, _profile_result, _select_trials,
)
from zg_bench.swe_qa.judge import judge_pairs

COMMITS = {
    "before": "f6358f4b7ccac4d55d037ef49900fef2ea552148",
    "after": "2c553b4b510758d1dba7b9f1c1af52d277534a49",
}
SLOTS = {"before": "baseline", "after": "zvec-grep"}
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "zg_bench/swe_qa/data"


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
        binary = archive.extractfile("package/bin/zg")
        if binary is None:
            raise ValueError("Pinned package has no native zg binary")
        binary_sha = hashlib.file_digest(binary, "sha256").hexdigest()
    return {
        "source_commit": COMMITS[variant],
        "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "native_cli_sha256": binary_sha,
        "actual_harness_profile": "zvec-grep",
        "comparison_slot": SLOTS[variant],
    }


def version_order(task, repetition):
    # Deterministic alternating order, balanced across all 20 tasks.
    tasks = json.loads((DATA / "selection.json").read_text())["tasks"]
    position = next(i for i, item in enumerate(tasks) if item["task_slug"] == task)
    return ("before", "after") if (position + repetition) % 2 else ("after", "before")


def collect(root, task, identities, repetitions=3):
    profiles = {}
    for variant, slot in SLOTS.items():
        trials = []
        for rep in range(1, repetitions + 1):
            jobs = _job_dirs(root / "runs" / variant / f"r{rep}", "zvec-grep")
            if len(jobs) != 1:
                raise ValueError(f"{variant} repetition {rep}: expected one zg job, got {len(jobs)}")
            job = jobs[0]
            _completed_job(job, expected_trials=1)
            selected = _select_trials(job, task, expected_trials=1)
            trial_dir, result = selected[0]
            setup = json.loads((trial_dir / "agent/zvec-grep-setup.json").read_text())
            if setup.get("status") != "ready":
                raise ValueError("zg setup did not reach ready")
            for key in ("package_sha256", "native_cli_sha256"):
                if setup.get(key) != identities[variant][key]:
                    raise ValueError(f"{variant}: installed {key} mismatch")
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
        "comparison_kind": "zg-before-vs-zg-pr179",
        "comparison_labels": {"baseline": "before+zg", "zvec-grep": "after PR179+zg"},
        "provenance": identities,
    }


def run(args):
    output = args.output.resolve()
    packages = args.packages.resolve()
    identities = {v: identity(packages / v, v) for v in SLOTS}
    if identities["before"]["native_cli_sha256"] == identities["after"]["native_cli_sha256"]:
        raise ValueError("Before and after binaries are identical")
    config = json.loads((ROOT / "ci-config.json").read_text())
    embedding = config["embedding_models"][args.embedding]
    meta = {
        "model": args.model, "embedding": embedding, "task": args.task,
        "repetitions": 3, "provenance": identities,
        "harness_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "locked_input_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (
            "zg_bench/swe_qa/data/selection.json", "zg_bench/swe_qa/data/references.json",
            "zg_bench/swe_qa/data/index.ignore", "uv.lock", "ci-config.json",
        )}, "execution": [],
    }
    write_json(output / "provenance.json", meta)
    for rep in range(1, 4):
        for variant in version_order(args.task, rep):
            env = dict(os.environ, PR179_EXPECTED_ZG_BINARY_SHA256=identities[variant]["native_cli_sha256"])
            command = [
                "zg-bench", "run", "swe-qa-bench", "--tier", "full", "--task", args.task,
                "--agent", "opencode", "--model", f"custom-openai/{args.model}",
                "--profile", "zvec-grep", "--n-attempts", "1", "--max-retries", "2",
                "--embedding-model", embedding,
                "--zvec-grep-package", str(packages / variant / "candidate.tgz"),
                "--jobs-dir", str(output / "runs" / variant / f"r{rep}"),
                "--job-name", f"pr179-{args.task}-{variant}-r{rep}",
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
            result.check_returncode()
    pair = collect(output, args.task, identities)
    write_json(output / "pair.json", pair)
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
    run(parser.parse_args())
