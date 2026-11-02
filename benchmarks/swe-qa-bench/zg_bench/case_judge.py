"""Standard paired SWE-QA grading with archived requests and responses."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid

from .case_analysis import write_json
from .engines.judge import default_completion
from .swe_qa.collect import collect_pair
from .swe_qa.judge import judge_pairs


def recorded_completion(completion, output: Path):
    """Record model-visible judge input/output without authentication values."""
    output.mkdir(parents=True, exist_ok=True)

    def call(**kwargs):
        identifier = uuid.uuid4().hex
        prefix = output / identifier
        request = {key: value for key, value in kwargs.items() if key != "api_key"}
        request["messages_sha256"] = hashlib.sha256(
            json.dumps(kwargs["messages"], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        write_json(prefix.with_suffix(".request.json"), request)
        started = time.monotonic()
        try:
            response = completion(**kwargs)
            raw = response if isinstance(response, dict) else response.model_dump(mode="json")
            write_json(prefix.with_suffix(".response.json"), raw)
        except Exception as error:
            write_json(prefix.with_suffix(".completion.json"), {
                "complete": False, "error_type": type(error).__name__,
                "elapsed_seconds": time.monotonic() - started,
            })
            raise
        write_json(prefix.with_suffix(".completion.json"), {
            "complete": True, "elapsed_seconds": time.monotonic() - started,
        })
        return response

    return call


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--expected-trials", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("judge output must be empty; preserve earlier attempts separately")
    args.output.mkdir(parents=True, exist_ok=True)
    references = Path(__file__).parent / "swe_qa/data/references.json"
    pair = args.output / "pair.json"
    collect_pair(runs_dir=args.evidence / "jobs", task=args.task,
                 output=pair, expected_trials=args.expected_trials)
    # Freeze the exact rubric/reference used; never include this in agent input.
    (args.output / "references.json").write_bytes(references.read_bytes())
    report = judge_pairs(
        pairs_root=pair, references_path=references, output_dir=args.output,
        expected=[args.task], model="qwen3.8-max", attempts=3,
        completion_fn=recorded_completion(default_completion(), args.output / "calls"),
    )
    expected = 2 * args.expected_trials
    if report["gate"]["successful_judgements"] != expected:
        raise RuntimeError(f"expected {expected} successful judgements")
    print(json.dumps({"judged_answers": expected, "model": "qwen3.8-max",
                      "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
