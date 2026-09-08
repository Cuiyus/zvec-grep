"""Two-judge source-grounded QA review with a frozen, disclosed calibration gate.

Each model supplies at most one valid judgment per answer. Transport/JSON
failures may be retried, but a valid unfavorable decision is never retried.
Calibration labels, profile, costs, traces and the other judge's decisions are
not sent to either judge. This single-case development check is not external
validation of judge accuracy or of benchmark non-inferiority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Callable

from ..settings import OPENCODE_CUSTOM_GLM_BASE_URL
from . import SweQaError
from .judge import _default_completion, _response_content, _response_mapping, _response_usage
from .readonly_judge import (
    CRITERIA,
    _assessment_status,
    _object,
    _plan,
    extract_final_answer,
    judge_messages,
    load_case,
    parse_assessment,
)

MODELS = ("openai/glm-5.2", "openai/qwen3.8-max")
DECISIONS = ("pass", "fail", "uncertain", "disagreement", "uncalibrated", "execution_incomplete", "unscored")


def _digest(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def load_review_case(path: Path) -> dict[str, Any]:
    case = load_case(path)
    protocol = case.get("judge_protocol", {})
    if protocol.get("version") != "readonly-source-qa-v2-calibrated" or protocol.get("models") != list(MODELS):
        raise SweQaError("judge-v2 case must freeze both judge models")
    if protocol.get("candidate_valid_judgments_per_model") != 1 or protocol.get("max_attempts") != 2:
        raise SweQaError("judge-v2 permits one valid decision and at most two attempts")
    calibration = case.get("calibration_examples")
    if not isinstance(calibration, list) or len(calibration) != 4:
        raise SweQaError("judge-v2 requires its four fixed calibration examples")
    seen = set()
    for sample in calibration:
        ident = sample.get("calibration_id")
        if not isinstance(ident, str) or not ident or ident in seen:
            raise SweQaError("calibration IDs must be unique")
        seen.add(ident)
        if not isinstance(sample.get("answer"), str) or not sample["answer"].strip():
            raise SweQaError("calibration answer is missing")
        if sample.get("expected_quality") not in ("pass", "fail"):
            raise SweQaError("calibration quality must be frozen")
        scores = sample.get("expected_scores")
        if not isinstance(scores, dict) or not scores or not set(scores) <= set(CRITERIA):
            raise SweQaError("calibration criterion expectations are missing")
        if any(type(value) is not int or value not in (0, 1) for value in scores.values()):
            raise SweQaError("calibration criteria require binary expectations")
    if {s["expected_quality"] for s in calibration} != {"pass", "fail"}:
        raise SweQaError("calibration must contain both positive and negative examples")
    original_path = path.parent / protocol.get("source_case", "")
    if original_path.is_file():
        if _digest(original_path.read_bytes()) != protocol.get("source_case_sha256"):
            raise SweQaError("original case hash differs from judge-v2 provenance")
        original = _object(original_path, "original case")
        for key in ("case_id", "question", "repo", "evidence", "sufficient_sets"):
            if case.get(key) != original.get(key):
                raise SweQaError(f"judge-v2 changed original QA gold: {key}")
    return case


def review_messages(case: dict[str, Any], answer: str) -> list[dict[str, str]]:
    messages = judge_messages(case, answer)
    messages[0]["content"] += "\nCase-specific grading guidance:\n" + case["judge_protocol"]["guidance"]
    # judge_messages deliberately selects source fields; calibration examples,
    # expected labels and provenance never enter this request.
    return messages


def _model_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().casefold().removeprefix("openai/")


def judge_one(*, row: dict[str, Any], case: dict[str, Any], answer: str, model: str,
              api_key: str, api_base: str, completion_fn: Callable[..., Any] | None,
              attempts: int, checkpoint: Callable[[], None]) -> None:
    row.update(model=model, status="pending", attempts=[], messages=review_messages(case, answer))
    if not api_key or not api_base or completion_fn is None:
        row.update(status="judge_unavailable", quality="unscored")
        checkpoint()
        return
    evidence_ids = {e["id"] for e in [*case["evidence"], *case.get("judge_only_evidence", [])]}
    row.update(status="judge_failed", quality="unscored")
    for number in range(1, attempts + 1):
        attempt = {"attempt": number, "requested_model": model, "status": "requesting"}
        row["attempts"].append(attempt)
        checkpoint()
        started = time.monotonic()
        try:
            response = completion_fn(model=model, api_key=api_key, api_base=api_base,
                temperature=0, messages=row["messages"], response_format={"type": "json_object"},
                extra_body={"enable_thinking": False}, timeout=120, max_tokens=3000)
        except Exception as error:
            # Exception strings can contain provider credentials or prompts.
            attempt.update(status="transport_error", error_type=type(error).__name__)
        else:
            try:
                mapping = _response_mapping(response)
                attempt.update(resolved_model=mapping.get("model"), response_id=mapping.get("id"))
                identity = _model_id(mapping.get("model"))
                if identity is None or identity != _model_id(model):
                    status = "identity_unverified" if identity is None else "identity_mismatch"
                    attempt.update(status=status, identity_verified=False)
                    row.update(status=status, quality="unscored")
                    try:
                        attempt.update(usage=_response_usage(response), content=_response_content(response))
                    except (SweQaError, ValueError, TypeError) as error:
                        attempt["output_error_type"] = type(error).__name__
                else:
                    attempt["identity_verified"] = True
                    attempt.update(usage=_response_usage(response), content=_response_content(response))
                    assessment = parse_assessment(attempt["content"], evidence_ids)
                    attempt["status"] = "judged"
                    row.update(status="judged", assessment=assessment, quality=_assessment_status(assessment))
            except (SweQaError, ValueError, TypeError) as error:
                attempt.update(status="invalid_assessment", error_type=type(error).__name__)
        attempt["latency_seconds"] = time.monotonic() - started
        checkpoint()
        if row["status"] in {"judged", "identity_unverified", "identity_mismatch"}:
            break  # Never retry a valid decision or shop for a different backend identity.


def consensus(judgments: dict[str, dict[str, Any]], calibration: dict[str, dict[str, Any]],
              execution_status: str) -> dict[str, Any]:
    qualities = {model: judgments.get(model, {}).get("quality", "unscored") for model in MODELS}
    complete = all(judgments.get(model, {}).get("status") == "judged" for model in MODELS)
    raw = next(iter(qualities.values())) if complete and len(set(qualities.values())) == 1 else "disagreement" if complete else "unscored"
    criterion_disagreements = {}
    if complete:
        for criterion in CRITERIA:
            scores = {model: judgments[model]["assessment"][criterion]["score"] for model in MODELS}
            if len(set(scores.values())) > 1:
                criterion_disagreements[criterion] = scores
    calibrated = all(calibration.get(model, {}).get("status") == "passed" for model in MODELS)
    status = "unscored" if not complete else "uncalibrated" if not calibrated else "execution_incomplete" if execution_status != "completed" else raw
    return {"raw_consensus": raw, "consensus_status": status, "quality": status,
            "flags": {"both_judges_calibrated": calibrated, "judgment_disagreement": raw == "disagreement",
                      "criterion_disagreement": bool(criterion_disagreements), "execution_completed": execution_status == "completed",
                      "self_judge_in_pair": True, "not_human_verified": True},
            "criterion_disagreements": criterion_disagreements}


def _markdown(report: dict[str, Any]) -> str:
    lines = [f"# Calibrated two-model QA review: {report['case_id']}", "",
             "Single-case development review. Calibration was authored after earlier answers were inspected; it is not held-out validation.",
             "GLM candidates include a GLM self-judge; Qwen candidates include a Qwen self-judge. The pair is two fallible models, not independent human verification.",
             "A missing or mismatched response model remains unscored. Only case-insensitive IDs and the optional openai/ prefix are accepted aliases.",
             "One valid decision per model and answer. Retries repair transport or JSON failures only; valid failures are never retried.", "",
             "| Judge | Calibration | Matched fixed probes |", "|---|---|---:|"]
    for model in MODELS:
        row = report["calibration"][model]
        lines.append(f"| {model} | {row['status']} | {row.get('matched_examples', 0)}/{row['planned_examples']} |")
    lines += ["", "| Trial | Profile | GLM | Qwen | Raw consensus | Reportable status | Criterion disagreement |",
              "|---|---|---|---|---|---|---|"]
    for row in report["trials"]:
        values = [row["trial_id"], row["profile"], *[row.get("judgments", {}).get(m, {}).get("quality", "unscored") for m in MODELS],
                  row.get("raw_consensus", "unscored"), row.get("consensus_status", "unscored"), str(row.get("flags", {}).get("criterion_disagreement", False))]
        lines.append("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in values) + " |")
    lines += ["", "A reportable pass requires both calibrated judges to pass and the QA execution to have completed.",
              "Calibration failures, disagreement, uncertainty and missing answers remain explicit; no majority vote or forced pass.",
              "Source support does not measure the candidate's observed retrieval evidence. Tokens/costs are never shown to judges.",
              "All prompts, responses, criterion reasons, model identities and failed attempts are retained in JSON.", ""]
    return "\n".join(lines)


def review_runs(*, runs_dir: Path, case_path: Path, output: Path,
                expected_per_profile: int | None = 5, attempts: int = 2, seed: int = 0,
                completion_fn: Callable[..., Any] | None = None) -> dict[str, Any]:
    if type(attempts) is not int or not 1 <= attempts <= 2:
        raise SweQaError("quality review permits one or two attempts, never repeated valid votes")
    if output.suffix != ".json" or output.exists() or output.with_suffix(".md").exists():
        raise SweQaError("quality review requires a new .json output path; existing judgments are preserved")
    case = load_review_case(case_path)
    planned = _plan(runs_dir, case["case_id"])
    if expected_per_profile is not None and (expected_per_profile < 1 or any(
            sum(t["profile"] == p for t in planned) != expected_per_profile for p in ("baseline", "zvec-grep"))):
        raise SweQaError("plan does not contain the expected number of trials per profile")
    api_key = os.environ.get("GLM_API_KEY", "").strip()
    api_base = os.environ.get("GLM_BASE_URL", OPENCODE_CUSTOM_GLM_BASE_URL).strip()
    report: dict[str, Any] = {"schema_version": 2, "case_id": case["case_id"],
        "case_sha256": _digest(case_path.read_bytes()), "plan_sha256": _digest((runs_dir / "plan.json").read_bytes()),
        "judge_protocol": case["judge_protocol"], "judge_models": list(MODELS), "temperature": 0, "order_seed": seed,
        "source_reference": case["reference_answer"], "source_reference_provenance": case.get("reference_provenance"),
        "planned_trials": len(planned), "calibration": {m: {"status": "pending", "planned_examples": 4, "examples": []} for m in MODELS},
        "trials": [], "summary": {}, "scope": "One case, two fallible model judges, no population-level quality claim."}

    def checkpoint() -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        serialized, markdown = json.dumps(report, ensure_ascii=False, indent=2) + "\n", _markdown(report)
        if api_key:
            serialized, markdown = serialized.replace(api_key, "[REDACTED]"), markdown.replace(api_key, "[REDACTED]")
        temporary = output.with_suffix(".json.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(output)
        output.with_suffix(".md").write_text(markdown, encoding="utf-8")

    order = list(range(len(planned)))
    random.Random(seed).shuffle(order)
    for number, index in enumerate(order, 1):
        trial = planned[index]
        row = {k: v for k, v in trial.items() if k != "path"}
        row.update(candidate_id=f"candidate-{number:03d}", status="pending", judgments={}, quality="unscored",
                   raw_consensus="unscored", consensus_status="unscored")
        report["trials"].append(row)
        path = trial["path"]
        if path is None or not path.is_file():
            row["status"] = "missing_trajectory"
            continue
        row["trajectory_sha256"] = _digest(path.read_bytes())
        try:
            answer = extract_final_answer(_object(path, "trajectory"))
        except SweQaError as error:
            row.update(status="invalid_trajectory", error_type=type(error).__name__)
            continue
        if answer is None:
            row["status"] = "missing_final_answer"
            continue
        row.update(status="answer_available", answer=answer, answer_sha256=_digest(answer))
    checkpoint()
    has_answers = any(row["status"] == "answer_available" for row in report["trials"])
    if has_answers and api_key and api_base and completion_fn is None:
        try:
            completion_fn = _default_completion()
        except SweQaError:
            completion_fn = None
    for model in MODELS:
        calibration = report["calibration"][model]
        if not has_answers:
            calibration["status"] = "not_run_no_candidates"
            continue
        calibration_order = list(range(len(case["calibration_examples"])))
        random.Random(seed).shuffle(calibration_order)
        for index in calibration_order:
            sample = case["calibration_examples"][index]
            row = {"calibration_id": sample["calibration_id"], "expected_quality": sample["expected_quality"],
                   "expected_scores": sample["expected_scores"], "expected_rationale": sample["rationale"],
                   "answer_sha256": _digest(sample["answer"])}
            calibration["examples"].append(row)
            judge_one(row=row, case=case, answer=sample["answer"], model=model, api_key=api_key, api_base=api_base,
                      completion_fn=completion_fn, attempts=attempts, checkpoint=checkpoint)
            row["calibration_match"] = (row.get("quality") == sample["expected_quality"] and
                all(row.get("assessment", {}).get(c, {}).get("score") == value for c, value in sample["expected_scores"].items()))
            row["criterion_expectation_matches"] = {c: row.get("assessment", {}).get(c, {}).get("score") == value
                                                   for c, value in sample["expected_scores"].items()}
        calibration["matched_examples"] = sum(r["calibration_match"] for r in calibration["examples"])
        calibration["status"] = ("unavailable" if any(r["status"] != "judged" for r in calibration["examples"])
                                 else "passed" if calibration["matched_examples"] == 4 else "failed")
        checkpoint()
    for row in report["trials"]:
        if row["status"] != "answer_available":
            row.update(consensus({}, report["calibration"], row["execution_status"]))
            continue
        for model in MODELS:
            judgment: dict[str, Any] = {}
            row["judgments"][model] = judgment
            judge_one(row=judgment, case=case, answer=row["answer"], model=model, api_key=api_key, api_base=api_base,
                      completion_fn=completion_fn, attempts=attempts, checkpoint=checkpoint)
        row["status"] = "reviewed" if all(j["status"] == "judged" for j in row["judgments"].values()) else "judge_failed"
        row.update(consensus(row["judgments"], report["calibration"], row["execution_status"]))
        checkpoint()
    report["trials"].sort(key=lambda row: (row["profile"], row["trial_id"]))
    report["summary"] = {profile: {"planned": sum(row["profile"] == profile for row in report["trials"]),
        **{status: sum(row["profile"] == profile and row["quality"] == status for row in report["trials"]) for status in DECISIONS}}
        for profile in ("baseline", "zvec-grep")}
    report["quality_gate"] = {"both_judges_calibrated": all(c["status"] == "passed" for c in report["calibration"].values()),
                              "all_planned_answers_pass": all(row["quality"] == "pass" for row in report["trials"]),
                              "not_a_noninferiority_test": True}
    checkpoint()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True, dest="case_path")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-per-profile", type=int, default=5)
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        report = review_runs(**vars(args))
    except SweQaError as error:
        parser.exit(2, f"quality review: {error}\n")
    print(json.dumps({"planned_trials": report["planned_trials"], "quality_gate": report["quality_gate"], "summary": report["summary"]}))
    # Disagreement/calibration failure is reportable execution, not a tool
    # failure. Missing valid decisions remain an incomplete review.
    return 0 if all(row["status"] == "reviewed" for row in report["trials"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
