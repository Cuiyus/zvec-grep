"""Judge Task 363's preserved output at the wall-clock cutoff without rerunning Qoder.

This is a diagnostic rubric score. The original agent status stays budget_exhausted;
the result is not a completed QA trial or an official Workspace-Bench judge score.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote

import dataset
import judge


SOURCE_RUN = "35731286815"
SOURCE_COMMIT = "e5b7ecd399f1f4fac838ec3e3ef172b135522561"
TASK_ID = "363"
ARTIFACT_NAME = f"workspace-lite-cn-official-363-long-smoke-363-{SOURCE_RUN}-1"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_sources(lock: dict, root: Path) -> Path:
    task = next(t for t in lock["tasks"] if t["task_id"] == TASK_ID)
    base = (f"https://huggingface.co/datasets/{lock['dataset']['repo']}/resolve/"
            f"{lock['dataset']['revision']}/task_lite_clean_cn/{TASK_ID}/")
    rows = [("metadata.json", task["metadata_sha256"])] + [
        (item["stored_relpath"], item["sha256"]) for item in task["inputs"]]

    def fetch(row: tuple[str, str]) -> None:
        relative, digest = row
        dataset.download(base + quote(relative), root / dataset.safe_relative(relative), digest)

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(fetch, rows))
    return root / "metadata.json"


def prepare(artifact: Path, source: Path, lock: dict) -> tuple[dict, dict, list[dict]]:
    runs = artifact / "runs"
    manifest = judge.read_object(runs / "manifest.json")
    ci = manifest.get("ci_identity", {})
    if (str(ci.get("GITHUB_RUN_ID")) != SOURCE_RUN or ci.get("GITHUB_SHA") != SOURCE_COMMIT
            or manifest.get("task_id") != TASK_ID or manifest.get("integration_method") != "zg_install"
            or manifest.get("package") != "@zvec/zvec-grep@0.2.2"):
        raise judge.JudgeError("source artifact identity or zg integration differs")
    ledger = judge.read_object(runs / "trial-results.json")
    prior = judge.read_object(runs / "judgements.json")
    if (ledger.get("task_id") != TASK_ID or prior.get("task_id") != TASK_ID
            or prior.get("trial_results_sha256") != sha((runs / "trial-results.json").read_bytes())
            or prior.get("judge_model") != "glm-5.2"):
        raise judge.JudgeError("source trial/judge provenance differs")
    trials = {t["profile"]: t for t in ledger["trials"]}
    rows = {t["profile"]: t for t in prior["trials"]}
    if (set(trials) != {"baseline", "with-zg"} or len(ledger["trials"]) != 2
            or trials["baseline"]["status"] != "budget_exhausted"
            or trials["baseline"].get("session", {}).get("limit_reason") != "wall_seconds"
            or trials["with-zg"]["status"] != "completed"
            or rows["with-zg"]["status"] != "judged"
            or rows["baseline"]["status"] != "execution_not_completed"):
        raise judge.JudgeError("source outcome differs from the audited cutoff case")
    if (trials["baseline"].get("source_unchanged") is not True
            or trials["with-zg"].get("source_unchanged") is not True
            or trials["with-zg"].get("zg_tool_calls") != 0):
        raise judge.JudgeError("source integrity or observed zg calls differ")
    metadata_path = fetch_sources(lock, source)
    metadata = judge.read_object(metadata_path)
    if (sha(metadata_path.read_bytes()) != prior.get("metadata_sha256")
            or metadata.get("rubrics") != prior.get("rubrics")
            or metadata.get("rubric_types") != prior.get("rubric_types")):
        raise judge.JudgeError("original task metadata/rubrics differ from judged source")
    os.environ["WORKSPACE_QA_CORPUS_VARIANT"] = "pdf-text-v1"
    os.environ["WORKSPACE_QA_EXECUTION_MODE"] = "official-writable"
    os.environ["WORKSPACE_QA_PDF_ENGINE"] = "pdfium"
    evidence = judge.load_evidence(metadata_path, source)
    if evidence["source_bytes"] != prior["source_bytes"]:
        raise judge.JudgeError("PDF text extraction differs from the original judge")
    with_zg = trials["with-zg"]
    reference = judge.build_messages(evidence, with_zg["answer"],
        judge.candidate_outputs(with_zg, runs, with_zg["answer"]))
    reference_sha = sha(json.dumps(reference, ensure_ascii=False, sort_keys=True).encode())
    if reference_sha != rows["with-zg"].get("prompt_sha256"):
        raise judge.JudgeError("reconstructed judge prompt differs from source run")
    baseline = trials["baseline"]
    outputs = judge.candidate_outputs(baseline, runs, "")
    if len(outputs) != 1 or outputs[0].get("pdf_valid") is not True:
        raise judge.JudgeError("cutoff output is not one verified agent-created PDF")
    messages = judge.build_messages(evidence, "", outputs)
    context = {"source_run_id": SOURCE_RUN, "source_commit": SOURCE_COMMIT,
        "source_artifact": ARTIFACT_NAME, "source_ledger_sha256": sha((runs / "trial-results.json").read_bytes()),
        "source_judgements_sha256": sha((runs / "judgements.json").read_bytes()),
        "reference_with_zg_prompt_sha256": reference_sha,
        "baseline_candidate_sha256": baseline["candidate_sha256"],
        "baseline_execution_status": baseline["status"],
        "baseline_limit_reason": baseline["session"]["limit_reason"],
        "baseline_input_tokens_observed_lower_bound": baseline["session"]["observed"]["input_tokens_observed_lower_bound"],
        "baseline_tool_calls": baseline.get("tool_calls"), "baseline_wall_seconds": baseline.get("wall_seconds"),
        "baseline_pdf_pages": outputs[0]["pdf_pages"],
        "judge_prompt_sha256": sha(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()),
        "judge_model": prior["judge_model"], "original_rubric_count": len(prior["rubrics"]),
        "with_zg_original_judge_score": rows["with-zg"]["score"],
        "with_zg_formal_zg_tool_calls": with_zg["zg_tool_calls"]}
    return context, prior, messages


def assess(context: dict, prior: dict, messages: list[dict], *, completion=judge.http_completion,
           sleep=time.sleep) -> dict:
    key = os.environ.get("GLM_API_KEY", "").strip()
    base, model = judge.settings()
    base = os.environ.get("GLM_BASE_URL", base).strip()
    if not key or not base or model != prior["judge_model"]:
        raise judge.JudgeError("pinned GLM judge is unavailable")
    attempts = []
    for number in range(1, 4):
        row = {"number": number}
        attempts.append(row)
        try:
            response = completion(api_key=key, base_url=base, model=model,
                                  messages=messages, timeout=180)
            criteria = judge.assess_response(response, len(prior["rubrics"]), model)
            row.update(status="judged", response=response)
            return {"schema_version": 1, "adapter": "artifact-at-cutoff-diagnostic-v1",
                "official_judge": False, "leaderboard_comparable": False,
                "trial_completed": False, "output_present_at_cutoff": True,
                "status": "judged_cutoff_output", "score": sum(x["score"] for x in criteria) / len(criteria),
                "criteria": criteria, "attempts": attempts, **context}
        except Exception as exc:
            row.update(status="failed", error_type=type(exc).__name__, retryable=judge.retryable(exc))
            if not judge.retryable(exc) or number == 3:
                raise
            sleep(min(2**number, 8))
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lock = judge.read_object(Path(__file__).parent / "data/official-tasks-334-363-one-pair-lock.json")
    context, prior, messages = prepare(args.artifact, args.source, lock)
    result = assess(context, prior, messages)
    judge.write_json(args.output, result, secret=os.environ.get("GLM_API_KEY", ""))
    print(json.dumps({"status": result["status"], "score": result["score"],
        "baseline_trial_completed": result["trial_completed"],
        "with_zg_formal_zg_tool_calls": result["with_zg_formal_zg_tool_calls"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
