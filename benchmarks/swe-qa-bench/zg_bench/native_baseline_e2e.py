"""Single-arm native OpenCode control for the frozen Node/Rust E2E matrix."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from zg_bench.core.protocol import SCORE_KEYS
from zg_bench.engines.judge import (
    default_completion, judge_candidate, judge_generation_metadata, judge_temperature,
)
from zg_bench.engines.registry import resolve_opencode_model
from zg_bench.metrics.summary import summarize_profile
from zg_bench.metrics.usage import SESSION_USAGE_METRICS, usage_scope
from zg_bench.settings import BENCHMARK_SEED, OPENCODE_VERSION
from zg_bench.swe_qa.collect import _completed_job, _profile_result, _select_trials
from zg_bench.swe_qa.pairs import load_references
from zg_bench.version_e2e import DATA, ROOT, prebuild_task_image, write_json

REPETITIONS = 10
LOCKED_INPUTS = {'zg_bench/swe_qa/data/selection.json': '1e415bb4ddb54a037ccfd49815a9b61e3de792492468218dce9d0bf8269d1302', 'zg_bench/swe_qa/data/references.json': '9b9407d93c76ab7fd081effe2545921a47ed31db35e6cdd28f9120b7f632baab', 'zg_bench/swe_qa/data/index.ignore': '9e216b4ed9b3d6d3a16f846c563d872300c5d15d9bbb483a5997b661da2c0be2', 'uv.lock': '136fb8c5a3cc96828223968c572f39847f85476415bc59fa28f9765d37b7b751', 'ci-config.json': 'dc5a7af4b9a5a106acbdc451af3e8bcef1f54ce7c804b09e154eedf4db6fb5a5', 'zg_bench/metrics/summary.py': 'c5db597e3417409ae6e40e531de6569083a7fab7c6d153f3dc4b8ee6198e3888', 'zg_bench/reports/render.py': '880f5fdad007adfd59719272e6d183afcbe1dc03f166a02afdaedbf182e181a3'}


def validate_inputs():
    actual = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in LOCKED_INPUTS}
    if actual != LOCKED_INPUTS or OPENCODE_VERSION != "1.18.4":
        raise ValueError("Baseline inputs or OpenCode differ from the sealed comparison")
    return actual


def command(task, model, output, repetition):
    return [
        "zg-bench", "run", "swe-qa-bench", "--tier", "full", "--task", task,
        "--agent", "opencode", "--model", f"custom-openai/{model}",
        "--profile", "baseline", "--n-attempts", "1", "--max-retries", "2",
        "--jobs-dir", str(output / "runs" / f"r{repetition}"),
        "--job-name", f"native-opencode-{task}-r{repetition}",
    ]


def collect(output, task, model):
    trials = []
    for repetition in range(1, REPETITIONS + 1):
        jobs = [p for p in (output / "runs" / f"r{repetition}").glob("*")
                if p.is_dir() and (p / "result.json").is_file()]
        if len(jobs) != 1:
            raise ValueError(f"r{repetition}: expected one native baseline job")
        job = jobs[0]
        _completed_job(job, expected_trials=1)
        [(trial_dir, result)] = _select_trials(job, task, expected_trials=1)
        agent = result["config"]["agent"]
        kwargs = agent["kwargs"]
        if (agent.get("import_path") or agent.get("name")) != "zg_bench.agents.opencode:ResilientOpenCode":
            raise ValueError("Baseline must use the native OpenCode adapter")
        config = kwargs["opencode_config"]
        if isinstance(config, str):
            config = json.loads(config)
        if config.get("mcp") or any("zvec" in key or "embedding" in key for key in kwargs):
            raise ValueError("Native baseline unexpectedly contains zg or embedding setup")
        if kwargs.get("version") != OPENCODE_VERSION or kwargs.get("collect_session_usage") is not True:
            raise ValueError("Baseline Agent version or accounting differs")
        if (trial_dir / "agent/zvec-grep-setup.json").exists():
            raise ValueError("Native baseline unexpectedly installed zg")
        info = result["agent_info"]
        if info["name"] != "opencode" or info["version"] != OPENCODE_VERSION:
            raise ValueError("Executed Agent version differs")
        usage = json.loads((trial_dir / "agent/session-usage.json").read_text())
        models = {(m["provider_id"], m["model_id"]) for s in usage["sessions"] for m in s["provider_models"]}
        if models != {("custom-openai", model)}:
            raise ValueError(f"Actual baseline model differs: {models}")
        snapshot = json.loads((trial_dir / "agent/opencode-session-snapshot.json").read_text())
        if any("zvec_grep" in json.loads(row["data"]).get("name", "")
               for row in snapshot if row["kind"] == "tool"):
            raise ValueError("Native baseline unexpectedly called zg")
        trials.append(_profile_result(profile="baseline", job_dir=job, trial_dir=trial_dir,
                                      result=result, trial_index=repetition))
    return trials


def judge(trials, reference, model):
    runtime = resolve_opencode_model(model, require_credentials=True)
    completion = default_completion()
    def score(trial):
        result = judge_candidate(
            completion_fn=completion, api_key=runtime.api_key,
            api_base=runtime.configuration.base_url, question=reference["question"],
            reference=reference["reference_answer"], candidate=trial["answer"],
            attempts=3, model=model,
        )
        return {
            "trial_index": trial["trial_index"], "trial_name": trial["trial_name"],
            "judge": result,
            "metrics": {key: trial[key] for key in (
                "input_tokens", "output_tokens", "tool_calls", "agent_wall_seconds", "cost_usd",
                *SESSION_USAGE_METRICS, "session_usage", "usage_collection_wall_seconds",
            ) if key in trial} | {"usage_scope": usage_scope(trial)},
        }
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(score, trials))


def run(args):
    locked = validate_inputs()
    tasks = {row["task_slug"] for row in json.loads((DATA / "selection.json").read_text())["tasks"]}
    if args.task not in tasks:
        raise ValueError("Task must belong to the frozen 20-task selection")
    output = args.output.resolve()
    meta = dict(kind="native-opencode-no-zg", model=args.model, task=args.task,
        repetitions=REPETITIONS, embedding=None, zg_enabled=False, opencode_version=OPENCODE_VERSION,
        harness_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        locked_input_sha256=locked, execution=[])
    write_json(output / "provenance.json", meta)
    meta["task_image"] = prebuild_task_image(ROOT / "datasets" / args.task, output)
    write_json(output / "provenance.json", meta)
    for repetition in range(1, REPETITIONS + 1):
        started = time.monotonic()
        result = subprocess.run(command(args.task, args.model, output, repetition))
        meta["execution"].append(dict(repetition=repetition, returncode=result.returncode,
            wall_seconds_including_setup_and_retries=time.monotonic() - started))
        write_json(output / "provenance.json", meta)
    if any(row["returncode"] != 0 for row in meta["execution"]):
        raise RuntimeError("Native baseline has failed executions; retain all evidence without partial scores")
    trials = collect(output, args.task, args.model)
    write_json(output / "trials.json", trials)
    reference = load_references(DATA / "references.json")[args.task]
    judged = judge(trials, reference, args.model)
    report = dict(schema_version=1, benchmark="peng-weihan/SWE-QA-Bench", kind=meta["kind"],
        task_id=reference["task_id"], role=reference.get("role"), category=reference.get("category"),
        model=args.model, profile=summarize_profile(judged), provenance=meta,
        judge=dict(model=args.model, self_judge=True, seed=BENCHMARK_SEED,
            temperature=judge_temperature(args.model), rubric=list(SCORE_KEYS),
            **judge_generation_metadata(args.model)))
    write_json(output / "report/report.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--model", choices=("glm-5.2", "qwen3.8-max"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
