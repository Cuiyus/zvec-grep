"""Collect and self-judge the zg-only arm of the search tool name experiment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from zg_bench.core.io import load_object
from zg_bench.engines.judge import default_completion, judge_concurrency, judge_task_trials
from zg_bench.engines.registry import resolve_opencode_model
from zg_bench.metrics.summary import summarize_profile
from zg_bench.swe_qa.collect import collect_pair
from zg_bench.swe_qa.pairs import load_references


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--pair-output", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    bench = Path(__file__).resolve().parents[2]
    config = load_object(bench / "ci-config.json", label="CI config")
    pair = collect_pair(
        runs_dir=args.runs_dir,
        task=args.task,
        output=args.pair_output,
        expected_trials=config["trials_per_profile"],
        profile_names=("zvec-grep",),
    )
    model = os.environ["SWE_QA_MODEL"]
    runtime = resolve_opencode_model(f"custom-openai/{model}", require_credentials=True)
    assert runtime.api_key is not None
    references = load_references(bench / "zg_bench/swe_qa/data/references.json")
    reference = references[args.task]
    trials = judge_task_trials(
        pair=pair,
        reference=reference,
        completion_fn=default_completion(),
        api_key=runtime.api_key,
        api_base=runtime.configuration.base_url,
        attempts=3,
        concurrency=judge_concurrency(),
        model=model,
        profile_names=("zvec-grep",),
    )["zvec-grep"]
    report = {
        "schema_version": 1,
        "scope": "tool-name-19",
        "model": model,
        "search_tool_name": os.environ["ZG_MCP_SEARCH_TOOL_NAME"],
        "candidate_commit": os.environ["BENCH_CANDIDATE_COMMIT"],
        "harness_commit": os.environ["BENCH_HARNESS_COMMIT"],
        "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "cases": [{
            "task_id": reference["task_id"],
            "trial_count": len(trials),
            "profiles": {"zvec-grep": summarize_profile(trials)},
        }],
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
