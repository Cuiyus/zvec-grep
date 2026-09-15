#!/usr/bin/env python3
"""One CI job: prepare, execute a pair series, score, and always retain its ledger."""
from __future__ import annotations
import argparse
import json
import hashlib
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib import request, error

HERE = Path(__file__).resolve().parent
PROTOCOL = "workspace-qa-qoder-native-install-v3"
ZG_SEARCH_TOOL = "mcp__zvec_grep__zvec_grep_search"


def _mcp_evidence(agent: Path) -> dict:
    from qoder_probe import native_mcp_evidence
    return native_mcp_evidence(agent)


def _setup_probe_evidence(runs: Path) -> dict:
    from qoder_probe import validate_probe
    probe = {"status": "invalid", "included_in_qa_metrics": False,
             "path": "sdk-preflight/qoder", "verified_successful_vector_searches": None}
    try:
        root = runs.parent.resolve()
        path = (root / "sdk-preflight/qoder").resolve()
        if not path.is_relative_to(root):
            raise ValueError("Setup probe escapes the current run")
        probe.update(validate_probe(path))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        probe.update(reason="Same-run native installation and fixture vector evidence is missing or invalid",
                     error_type=type(exc).__name__)
    return probe


def smoke_validation(runs: Path, phase: str) -> dict:
    """Validate integration in smoke; formal trials never depend on choosing zg."""
    result = {"schema_version": 3, "protocol": PROTOCOL, "phase": phase, "status": "not_applicable" if phase == "batch" else "invalid",
              "scope": "Standard zg installation and native Qoder retrieval; natural non-use requires a verified same-run native vector probe",
              "trials": [], "startup_checks": [], "verified_successful_searches": 0,
              "preserves_all_trial_metrics_and_judgements": True}
    if phase == "batch":
        return result
    if phase != "smoke":
        raise ValueError("phase must be smoke or batch")
    result["setup_probe"] = _setup_probe_evidence(runs)
    try:
        ledger_path = runs / "trial-results.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        result["trial_results_sha256"] = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        if not isinstance(ledger, dict) or not isinstance(ledger.get("trials"), list):
            raise ValueError("Smoke ledger requires trials")
        if ledger.get("protocol") != PROTOCOL:
            raise ValueError("Smoke requires the native installation protocol; old bridge evidence cannot be reused")
        result["qa_execution_complete"] = bool(ledger["trials"]) and all(
            trial.get("status") == "completed" for trial in ledger["trials"])
        for trial in ledger["trials"]:
            if trial.get("status") != "completed":
                continue
            trial_id = trial.get("trial_id")
            if not isinstance(trial_id, str) or not trial_id or Path(trial_id).name != trial_id:
                raise ValueError("Invalid smoke trial_id")
            agent = (runs / trial_id / "agent").resolve()
            if not agent.is_relative_to(runs.resolve()):
                raise ValueError("Smoke trace path escapes runs")
            from qoder_probe import native_startup_evidence
            startup = native_startup_evidence(agent)
            result["startup_checks"].append({"trial_id": trial_id, "profile": trial.get("profile"), **startup})
            if trial.get("profile") != "with-zg":
                continue
            evidence = _mcp_evidence(agent)
            from qoder_probe import installation_evidence
            installation = installation_evidence(agent)
            measured = trial.get("zg_tool_calls_successful")
            reconciles = type(measured) is int and measured == evidence["native_successes"]
            attempts = trial.get("zg_tool_calls")
            attempts_reconcile = type(attempts) is int and attempts == evidence["native_attempts"]
            integrity = (trial.get("source_unchanged") is True
                and isinstance(trial.get("installation"), dict)
                and trial["installation"].get("manifest_sha256") == installation["manifest_sha256"]
                and evidence["native_missing_results"] == 0 and evidence["native_empty_successes"] == 0
                and evidence["startup_evidence"]["status"] not in {"failed", "incomplete"})
            non_use = attempts_reconcile and attempts == 0
            eligible = measured > 0 if reconciles else False
            if non_use and result["setup_probe"]["status"] == "valid":
                eligible = True
            row = {"trial_id": trial_id, "measured_successes": measured, "installation": installation, **evidence,
                   "success_counts_reconcile": reconciles, "attempt_counts_reconcile": attempts_reconcile,
                   "integrity_valid": integrity, "natural_non_use": non_use,
                   "valid": reconciles and attempts_reconcile and integrity
                       and evidence["mcp_registered_and_connected"] and eligible}
            result["trials"].append(row)
            if reconciles:
                result["verified_successful_searches"] += evidence["native_successes"]
        if (result["qa_execution_complete"] and result["setup_probe"]["status"] == "valid"
                and result["trials"] and all(row["valid"] for row in result["trials"])
                and all(row["status"] not in {"failed", "incomplete"} for row in result["startup_checks"])):
            result["status"] = "valid"
        else:
            result["reason"] = "Smoke requires a fresh native installation probe, unchanged sources, registered QA MCP and reconciled successful calls or natural non-use"
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result.update(status="invalid", reason="Smoke search evidence is missing or invalid", error_type=type(exc).__name__)
    return result


def annotate_smoke_report(output: Path, validation: dict) -> None:
    """Keep observations intact while separating their completeness from smoke validity."""
    if validation["phase"] != "smoke":
        return
    summary_path, markdown_path = output / "summary.json", output / "summary.md"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["smoke_validation"] = validation
        summary["pipeline_validation_complete"] = validation["status"] == "valid" and summary.get("summary", {}).get("complete") is True
        summary["efficacy_claim_ready"] = False
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if markdown_path.is_file():
        text = markdown_path.read_text(encoding="utf-8")
        label = validation["status"].upper()
        probe = validation.get("setup_probe", {})
        note = (f"**Smoke MCP validation: {label}.** Confirmed successful QA searches: {validation['verified_successful_searches']}. "
                f"Same-run setup vector probe: {probe.get('status', 'unavailable')}; "
                f"verified vector searches: {probe.get('verified_successful_vector_searches', 0)}. "
                "Natural QA non-use is a valid observation when this separate probe passes; attempted QA calls with no success still fail validation. "
                "Setup usage is excluded from QA metrics. "
                "Trial measurements and rubric scores below remain unchanged.\n\n"
                "Smoke observations validate the workflow and are excluded from formal efficacy estimates.\n\n")
        if validation["status"] != "valid":
            note += "**The integration smoke did not pass; these observations do not establish a working zg comparison.**\n\n"
        text = text.replace("Coverage:", "Observation coverage:")
        markdown_path.write_text(note + text, encoding="utf-8")


def sdk_preflight(output: Path):
    """Keep the artifact path while validating the standard install/daemon route."""
    from qoder_probe import standalone_native_probe
    standalone_native_probe(output)


def reuse_source_preflight(source: Path, root: Path, source_config: dict, review: dict) -> dict:
    """Reuse the successful source-run setup probe when its implementation is frozen."""
    from continuation import file_hashes, read_object
    from qoder_probe import validate_probe
    source = source.resolve()
    expected_name = (f"workspace-qa-batch-3-{source_config['source_run_id']}-"
                     f"{source_config['source_run_attempt']}")
    manifest = read_object(source / "runs/manifest.json")
    ci = manifest.get("ci_identity", {})
    if (source.name != expected_name or str(ci.get("GITHUB_RUN_ID")) != str(source_config["source_run_id"])
            or str(ci.get("GITHUB_RUN_ATTEMPT")) != str(source_config["source_run_attempt"])
            or ci.get("GITHUB_SHA") != source_config["source_commit"]
            or manifest.get("protocol") != PROTOCOL or manifest.get("integration_method") != "zg_install"
            or manifest.get("install_command") != ["zg", "install", "--target", "qoder", "--yes"]):
        raise ValueError("Shared preflight does not belong to the pinned native source run")
    if (review.get("status") != "verified" or review.get("base_commit") != source_config["source_commit"]):
        raise ValueError("Shared preflight lacks the source-to-current code review")
    probe = validate_probe(source / "sdk-preflight/qoder")
    embedding = read_object(source / "embedding-preflight.json")
    if (probe.get("status") != "valid" or embedding.get("status") != "completed"
            or embedding.get("requested_model") != "qwen3.7-text-embedding"):
        raise ValueError("Pinned source-run preflight was not successful")
    before = file_hashes(source)
    shutil.copytree(source / "sdk-preflight", root / "sdk-preflight")
    shutil.copyfile(source / "embedding-preflight.json", root / "embedding-preflight.json")
    if file_hashes(source) != before:
        raise ValueError("Shared preflight artifact changed during validation")
    evidence = {"schema_version": 1, "protocol": PROTOCOL, "status": "valid",
        "included_in_qa_metrics": False, "source_run_id": str(source_config["source_run_id"]),
        "source_run_attempt": str(source_config["source_run_attempt"]),
        "source_commit": source_config["source_commit"], "source_artifact": source.name,
        "source_artifact_files_sha256": before, "code_review": review,
        "policy": "Reused source-run connectivity probe; native installation is still performed and recorded independently in every with-zg QA trial."}
    (root / "sdk-preflight/preflight-reuse.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    return evidence


def embedding_preflight(output: Path):
    from runner import EMBEDDING, embedding_endpoint
    endpoint = embedding_endpoint()
    payload = {"model": EMBEDDING.split("/", 1)[1], "input": ["代码仓库问答 / repository question answering"],
               "dimensions": 1024, "encoding_format": "float"}
    started = time.monotonic()
    result = {"requested_model": payload["model"], "endpoint": endpoint, "dimension": 1024,
              "phase": "setup_connectivity_probe", "included_in_agent_tokens": False, "status": "failed"}
    try:
        req = request.Request(endpoint, data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["QWEN_API_KEY"]})
        with request.urlopen(req, timeout=90) as response:
            body = json.load(response)
        data = body.get("data", [])
        if len(data) != 1 or len(data[0].get("embedding", [])) != 1024:
            raise ValueError("Remote embedding preflight returned an unexpected vector shape")
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in data[0]["embedding"]):
            raise ValueError("Remote embedding preflight returned an invalid vector")
        if body.get("model") not in (None, payload["model"]):
            raise ValueError("Embedding endpoint returned a different model")
        result.update(status="completed", resolved_model=body.get("model"), usage=body.get("usage"),
                      request_sha256=hashlib.sha256(json.dumps(payload).encode()).hexdigest())
    except Exception as exc:
        result.update(error_type=type(exc).__name__)
        if isinstance(exc, error.HTTPError):
            result["http_status"] = exc.code
        raise
    finally:
        result["wall_seconds"] = time.monotonic() - started
        output.write_text(json.dumps(result, indent=2) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-id", required=True)
    p.add_argument("--repetitions", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--upstream", type=Path, required=True)
    p.add_argument("--phase", choices=("smoke", "batch"), required=True)
    p.add_argument("--continue-from", type=Path)
    p.add_argument("--unstarted-from", type=Path)
    p.add_argument("--continuation-source-config", type=Path)
    p.add_argument("--continuation-code-review", type=Path)
    p.add_argument("--shared-preflight", type=Path)
    args = p.parse_args(argv)
    if args.continue_from and args.unstarted_from:
        raise ValueError("Choose partial-trial or setup-only continuation, not both")
    continuing = bool(args.continue_from or args.unstarted_from)
    if continuing and (args.phase != "batch" or not args.continuation_code_review):
        raise ValueError("Continuation requires batch phase and a verified code review")
    if continuing and args.repetitions != 10:
        raise ValueError("Continuation requires the original 10 repetitions per profile")
    if (args.unstarted_from or args.shared_preflight) and not args.continuation_source_config:
        raise ValueError("Setup-only or shared-preflight continuation requires the pinned source configuration")
    if args.shared_preflight and not continuing:
        raise ValueError("Shared source-run preflight is only valid for an audited continuation")
    lock = json.loads((HERE / "data/lock.json").read_text())
    task = next(t for t in lock["tasks"] if t["task_id"] == args.task_id)
    if args.repetitions < 1:
        raise ValueError("repetitions must be positive")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    preparation, runs = root / "dataset", root / "runs"
    from runner import make_plan, collect_results
    plan = make_plan(args.task_id, args.repetitions)
    # A setup failure must still account for every planned trial.
    root.joinpath("planned.json").write_text(json.dumps(plan, indent=2) + "\n")
    scoped = {**lock, "tasks": [task], "repetitions": args.repetitions}
    root.joinpath("selection.json").write_text(json.dumps(scoped, ensure_ascii=False, indent=2) + "\n")
    setup_staging = None
    if args.unstarted_from:
        from continuation import load_setup_prior, stage_setup_prior
        source_config = json.loads(args.continuation_source_config.read_text())
        review = json.loads(args.continuation_code_review.read_text())
        bundle = load_setup_prior(args.unstarted_from, plan)
        if bundle["selection"] != scoped:
            raise ValueError("Setup-only continuation selection differs")
        setup_staging = root / "setup-continuation-staging"
        stage_setup_prior(bundle, setup_staging, review, source_config)
    outcome = 1
    try:
        # Stop before downloads, while retaining the already-frozen trial ledger.
        for name in ("QODER_PERSONAL_ACCESS_TOKEN", "GLM_API_KEY", "QWEN_API_KEY"):
            if not os.environ.get(name):
                raise RuntimeError(f"Required GitHub Actions secret is missing: {name}")
        if args.shared_preflight:
            source_config = json.loads(args.continuation_source_config.read_text())
            review = json.loads(args.continuation_code_review.read_text())
            reuse_source_preflight(args.shared_preflight, root, source_config, review)
            print(json.dumps({"phase": "native_install_preflight", "status": "reused_verified_source_run"}), flush=True)
        else:
            embedding_preflight(root / "embedding-preflight.json")
            print(json.dumps({"phase": "native_install_preflight", "status": "starting"}), flush=True)
            sdk_preflight(root / "sdk-preflight")
        print(json.dumps({"phase": "dataset_preparation", "status": "starting"}), flush=True)
        subprocess.run([sys.executable, str(HERE / "dataset.py"), "--task-id", args.task_id,
                        "--output", str(preparation), "--upstream", str(args.upstream)], check=True)
        print(json.dumps({"phase": "paired_trials", "status": "starting"}), flush=True)
        runner_command = [sys.executable, str(HERE / "runner.py"), "--task-id", args.task_id,
                                 "--source-root", str(preparation / "source"), "--question-file", str(preparation / "question.txt"),
                                 "--answer-filename", task["answer_filename"], "--output", str(runs),
                                 "--repetitions", str(args.repetitions), "--timeout", "900"]
        if args.continue_from:
            runner_command += ["--continue-from", str(args.continue_from),
                               "--continuation-code-review", str(args.continuation_code_review)]
        elif args.unstarted_from:
            runner_command += ["--continuation-code-review", str(args.continuation_code_review)]
        result = subprocess.run(runner_command)
        if setup_staging:
            from continuation import install_setup_prior
            manifest_path = runs / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            install_setup_prior(setup_staging, runs, manifest)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        # Retain and judge completed candidates even if a different trial failed.
        judge_command = [sys.executable, str(HERE / "judge.py"), "--metadata",
                                 str(preparation / "tasks" / args.task_id / "metadata.json"), "--task-dir",
                                 str(preparation / "tasks" / args.task_id), "--runs-dir", str(runs)]
        if args.continue_from:
            judge_command += ["--continue-from-ledger", str(runs / "continuation-evidence/prior-ledger.json"),
                              "--continue-from-judgements", str(runs / "continuation-evidence/prior-judgements.json")]
        judged = subprocess.run(judge_command)
        if continuing and (result.returncode not in (0, 1) or judged.returncode not in (0, 1)):
            # Exit 1 can describe a preserved failed observation. Signals and
            # CLI/process failures cannot be excused by a complete report.
            raise RuntimeError(f"Continuation process failed abnormally: runner={result.returncode}, judge={judged.returncode}")
        outcome = 0 if result.returncode == 0 and judged.returncode == 0 else 1
    except Exception as error:
        root.joinpath("setup-failure.json").write_text(json.dumps({"status": "failed", "error_type": type(error).__name__}) + "\n")
        raise
    finally:
        if not (runs / "trial-results.json").exists():
            runs.mkdir(parents=True, exist_ok=True)
            collect_results(runs, plan)
        validation = smoke_validation(runs, args.phase)
        root.joinpath("smoke_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n")
        if validation["status"] == "invalid":
            outcome = 1
        reported = subprocess.run([sys.executable, str(HERE / "report.py"), "--runs-dir", str(runs),
                                   "--manifest", str(root / "selection.json"), "--output", str(root / "report"),
                                   "--require-executed" if continuing else "--require-complete"])
        if continuing:
            # Original failures remain failures; completion means every original
            # slot was attempted and every completed answer has its judgement.
            outcome = reported.returncode
        elif reported.returncode:
            outcome = 1
        annotate_smoke_report(root / "report", validation)
    return outcome


if __name__ == "__main__":
    raise SystemExit(main())
