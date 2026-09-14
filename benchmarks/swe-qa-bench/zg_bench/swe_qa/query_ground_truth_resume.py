"""Resume fixed query annotation by verifying and reusing existing candidate data.

Only candidate sessions with the identical group and canonical packet can be
reused. Their original files/status/cost remain preserved. Every semantic review
is fresh; this command creates no new E2E observations or retrieval requests.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from . import query_ground_truth as gt
from .readonly_agents import agent_spec, expected_tools, qoder_contract, read_native_events, _qoder_identity
from .readonly_judge import extract_final_answer
from .readonly_run import directory_identity, run_checked, sha256, wire_contract, write_json

METRICS = ("model_requests", "tool_calls", "input_tokens")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


def _cost(attempts: list[dict]) -> dict:
    result = {"attempt_count": len(attempts), "included_in_e2e": False}
    for metric in METRICS:
        values = [a.get("observed", {}).get(metric) for a in attempts]
        known = [v for v in values if type(v) in (int, float)]
        result[metric] = sum(known) if len(known) == len(values) else None
        result[metric + "_observed_sum"] = sum(known)
        result[metric + "_unknown_attempts"] = len(values) - len(known)
    result["observed_sum_scope"] = "Sum of available attempt observations; unknown/interrupted cost is not zero."
    return result


def validate_resume_inputs(args: argparse.Namespace) -> dict:
    old = args.resume_candidates_dir.resolve()
    identity = _json(args.resume_identity)
    evidence = identity.get("evidence_identity", {})
    if (identity.get("protocol") != "readonly-qa-v6-diagnosis-resume"
            or not evidence.get("run_id") or not evidence.get("commit")
            or os.environ.get("QA_EVIDENCE_RUN_ID") != str(evidence["run_id"])
            or os.environ.get("QA_EVIDENCE_COMMIT") != evidence["commit"]):
        raise ValueError("Candidate resume identity does not match the explicit original E2E evidence environment")
    if not os.environ.get("GITHUB_RUN_ID") or not os.environ.get("GITHUB_SHA"):
        raise ValueError("Current diagnostic CI run and commit must be explicit")
    case = _json(args.case); analysis = _json(args.analysis)
    catalog = gt.annotation_catalog(analysis, case)
    if canonical(_json(old / "catalog.json")) != canonical(catalog):
        raise ValueError("Current frozen catalog differs from the old candidate catalog")
    old_analysis_path = args.resume_identity.parent / "query-analysis.json"
    if canonical(_json(old_analysis_path).get("annotation_catalog")) != canonical(catalog):
        raise ValueError("Old diagnostic analysis does not identify the candidate catalog")
    if canonical(_json(old_analysis_path).get("request_catalog")) != canonical(analysis.get("request_catalog")):
        raise ValueError("Current complete request catalog differs from the original diagnostic catalog")
    old_snapshot = getattr(args, "resume_snapshot", None) or args.resume_identity.parent.parent / "prepared/runtime/snapshot.json"
    current_prepared = args.source_root.resolve().parent
    current_snapshot = current_prepared / "runtime/snapshot.json"
    old_snap = _json(old_snapshot)
    if (sha256(old_snapshot) != sha256(current_snapshot)
            or old_snap.get("source", {}).get("git_commit") != case["repo"]["commit"]
            or old_snap.get("package", {}).get("name") != "@zvec/zvec-grep"
            or old_snap.get("package", {}).get("version") != "0.2.2"):
        raise ValueError("Candidate and current source/index/package snapshots differ")
    prepared_path = current_prepared / "prepared-manifest.json"
    prepared = _json(prepared_path)
    recorded = _json(args.resume_identity.parent / "evidence-manifest-identities.json")
    prepared_records = [r for r in recorded if r.get("path") == "prepared/prepared-manifest.json"]
    if (len(prepared_records) != 1 or prepared_records[0]["sha256"] != sha256(prepared_path)
            or prepared.get("ci_identity", {}).get("GITHUB_RUN_ID") != str(evidence["run_id"])
            or prepared.get("ci_identity", {}).get("GITHUB_SHA") != evidence["commit"]
            or prepared.get("repo") != case["repo"] or prepared.get("case_sha256") != sha256(args.case)
            or prepared.get("snapshot_sha256") != sha256(current_snapshot)):
        raise ValueError("Candidate resume does not share the original prepared runtime identity")
    if run_checked(["git", "-C", str(args.source_root), "rev-parse", "HEAD"]) != case["repo"]["commit"]:
        raise ValueError("Candidate resume source checkout differs from QA case")
    actual_source = directory_identity(args.source_root, skip_git=True)
    if actual_source != prepared.get("file_identities", {}).get("source"):
        raise ValueError("Candidate resume source bytes differ from the prepared source inventory")
    return {"protocol": "query-ground-truth-candidate-resume-v1", "evidence_identity": evidence,
        "previous_diagnostic_identity": identity.get("analysis_identity"),
        "current_diagnostic_identity": {"run_id": os.environ["GITHUB_RUN_ID"], "commit": os.environ["GITHUB_SHA"],
                                       "attempt": os.environ.get("GITHUB_RUN_ATTEMPT")},
        "analysis_sha256": sha256(args.analysis), "previous_analysis_sha256": sha256(old_analysis_path),
        "case_sha256": sha256(args.case), "entries_sha256": sha256(args.entries),
        "snapshot_sha256": sha256(old_snapshot), "prepared_manifest_sha256": sha256(prepared_path),
        "resume_identity_sha256": sha256(args.resume_identity), "catalog_sha256": gt.digest(canonical(catalog)),
        "new_e2e_trials": 0, "new_index_builds": 0, "new_retrieval_executions": 0,
        "all_semantic_reviews_fresh": True}


def _candidate_check(directory: Path, result: dict, packet: dict, group: str) -> dict:
    """Check original execution independently; a completed but unverifiable record fails closed."""
    expected_ids = [a["annotation_id"] for a in packet.get("annotations", [])]
    if not expected_ids or len(set(expected_ids)) != len(expected_ids):
        raise ValueError("Old candidate packet has missing or duplicate annotation IDs")
    if result.get("group") != group or result.get("phase") != "candidate" or result.get("packet_sha256") != sha256(directory / "packet/input.json"):
        raise ValueError("Old candidate result group/phase/packet hash cannot be verified")
    errors = []
    session_path = directory / "agent/session.json"
    session = _json(session_path) if session_path.exists() else {}
    if (result.get("returncode") != 0 or session.get("returncode") != 0 or session.get("status") != "completed"
            or result.get("session") != session or result.get("has_final_answer") is not True
            or result.get("error_event_count") != 0 or result.get("contract_error_count") != 0):
        errors.append("native_session_or_saved_execution_contract_incomplete")
    agent = "qodercli" if group.startswith("qoder-") else "opencode"
    model = "glm-5.2" if group == "opencode-glm52" else "qwen3.8-max"
    spec = agent_spec(agent, model, base_url=gt.OPENCODE_CUSTOM_BASE_URL if agent == "opencode" else None)
    if result.get("agent_spec") != spec.to_dict():
        errors.append("candidate_agent_model_spec_differs_from_fixed_group")
    native_path = directory / "agent" / spec.stream_filename
    native, parse = read_native_events(native_path) if native_path.exists() else ([], {"invalid_json_lines": ["missing"]})
    if parse.get("invalid_json_lines") or any(e.get("type") == "error" for e in native):
        errors.append("native_stream_missing_or_contains_errors")
    contract = None
    if agent == "opencode":
        contract = wire_contract(directory / "agent", spec.provider_model, expected_tools(spec, zg=False))
        if not contract.get("valid") or contract != result.get("wire_contract"):
            errors.append("opencode_wire_contract_not_verified")
    else:
        contract = qoder_contract(native, zg=False)
        if not contract.get("valid") or contract != result.get("tool_contract") or not _qoder_identity(native, spec).get("valid"):
            errors.append("qoder_native_contract_not_verified")
        if result.get("tool_error_count") != 0:
            errors.append("qoder_tool_or_packet_permission_errors")
    raw_path = directory / "raw-answer.txt"
    raw = raw_path.read_text() if raw_path.exists() else None
    trajectory_path = directory / "agent/trajectory.json"
    final = extract_final_answer(_json(trajectory_path)) if trajectory_path.exists() else None
    if not raw or raw.strip() != final:
        errors.append("raw_answer_does_not_match_terminal_trajectory")
    if agent == "opencode":
        texts = [e.get("part", {}).get("text") for e in native if e.get("type") == "text" and isinstance(e.get("part", {}).get("text"), str)]
        if not texts or not raw or texts[-1].strip() != raw.strip():
            errors.append("raw_answer_does_not_match_last_native_text")
    parsed = None
    if raw:
        try:
            parsed = gt.parse_response(raw)
            ids = [a.get("annotation_id") for a in parsed["annotations"] if isinstance(a, dict)]
            if len(ids) != len(parsed["annotations"]) or len(ids) != len(set(ids)) or set(ids) != set(expected_ids):
                errors.append("candidate_answer_does_not_cover_exact_batch_annotation_ids")
        except (ValueError, KeyError, TypeError):
            errors.append("candidate_answer_cannot_be_parsed_as_one_annotation_object")
    else:
        errors.append("raw_answer_missing")
    if errors and result.get("status") == "completed":
        raise ValueError("Completed old candidate cannot be safely reused: " + ", ".join(errors))
    return {"reusable": not errors, "reasons": errors, "parsed": parsed if not errors else None,
        "reparsed": not errors and (result.get("status") != "completed" or result.get("parsed") != parsed),
        "native_contract": contract, "expected_annotation_ids": expected_ids,
        "raw_answer_sha256": sha256(raw_path) if raw_path.exists() else None}


class CandidateResume:
    def __init__(self, args: argparse.Namespace, *, fresh_runner: Callable[..., dict] | None = None):
        self.args = args
        self.provenance = validate_resume_inputs(args)
        self.fresh_runner = fresh_runner or gt.run_session
        self.lock = threading.RLock()
        self.candidates: dict[tuple[str, str], dict] = {}
        self.historical: list[dict] = []
        self.current: list[dict] = []
        self.consumed: set[tuple[str, str]] = set()
        self.original_files = directory_identity(args.resume_candidates_dir)
        old_catalog = gt.annotation_catalog(_json(args.analysis), _json(args.case))
        ids = {a["annotation_id"] for a in old_catalog}
        packet_rows = [{k: unit.get(k) for k in ("annotation_id", "kind", "request", "original_question", "context_id",
                        "prior_turn_feedback", "prior_assistant_text", "context_capture_limitation")} for unit in old_catalog]
        expected_rows = {row["annotation_id"]: row for row in packet_rows}
        self.planned_packets = {(group, canonical(packet)) for group in gt.GROUPS
            for offset in range(0, len(packet_rows), args.batch_size)
            for packet in gt.split_annotation_packet(group, "candidate", {"annotations": packet_rows[offset:offset + args.batch_size]})}
        for phase in ("candidate", "review"):
            for directory in sorted((args.resume_candidates_dir / phase).glob("*/*")):
                if not directory.is_dir():
                    continue
                group = directory.parent.name
                result_path = directory / "result.json"
                result = _json(result_path) if result_path.exists() else {}
                session_path = directory / "agent/session.json"
                session = _json(session_path) if session_path.exists() else {}
                complete_usage = session.get("status") == "completed" and session.get("returncode") == 0
                history = {"group": group, "phase": phase, "path": str(directory.resolve()),
                    "original_status": result.get("status", "interrupted_without_result"),
                    "source_files_sha256": directory_identity(directory),
                    "observed": session.get("observed", {}) if complete_usage else {},
                    "usage_status": "completed_native_session" if complete_usage else "missing_final_usage",
                    "reusable": False}
                self.historical.append(history)
                if phase != "candidate":
                    history["reason"] = "Every review is fresh; interrupted review usage is not treated as zero."
                    continue
                packet_path = directory / "packet/input.json"
                if not packet_path.exists():
                    if result.get("status") == "completed":
                        raise ValueError("Completed candidate packet is missing")
                    history["reason"] = "Candidate packet missing; original failed attempt retained."
                    continue
                packet = _json(packet_path)
                if group not in gt.GROUPS or not set(a.get("annotation_id") for a in packet.get("annotations", [])) <= ids:
                    raise ValueError("Old candidate packet belongs to another group or frozen catalog")
                if set(packet) != {"annotations"} or any(canonical(row) != canonical(expected_rows[row["annotation_id"]])
                                                       for row in packet["annotations"]):
                    raise ValueError("Old candidate packet query/context differs from the frozen catalog")
                key = (group, canonical(packet))
                if key in self.candidates:
                    raise ValueError("Duplicate group+packet candidate attempts; no best-result selection allowed")
                check = _candidate_check(directory, result, packet, group) if result else {"reusable": False, "reasons": ["interrupted_without_result"]}
                history.update(reusable=check["reusable"], validation=check)
                self.candidates[key] = {"directory": directory, "result": result, "packet": packet, "check": check, "history": history}
        if any(item["check"]["reusable"] and key not in self.planned_packets for key, item in self.candidates.items()):
            raise ValueError("Current candidate batching would skip a verified old packet; preserve the original batching")

    def __call__(self, **kwargs) -> dict:
        phase, group, packet = kwargs["phase"], kwargs["group"], kwargs["packet"]
        key = (group, canonical(packet))
        if phase == "candidate" and key not in self.planned_packets:
            raise ValueError("New candidate packet differs from the frozen planned canonical packets")
        old = self.candidates.get(key) if phase == "candidate" else None
        attempt = {"group": group, "phase": phase, "path": str(kwargs["output"]),
            "execution_kind": "reused_candidate" if old and old["check"]["reusable"] else "fresh_session",
            "status": "starting", "observed": {}, "packet_key_sha256": gt.digest(canonical(packet))}
        with self.lock:
            self.current.append(attempt)
            self._persist()
        if old and old["check"]["reusable"]:
            with self.lock:
                if key in self.consumed:
                    raise ValueError("Candidate packet requested more than once")
                self.consumed.add(key)
            destination = kwargs["output"]
            destination.mkdir(parents=True, exist_ok=False)
            shutil.copytree(old["directory"], destination / "reused-source", symlinks=True)
            write_json(destination / "packet/input.json", packet)
            shutil.copyfile(old["directory"] / "raw-answer.txt", destination / "raw-answer.txt")
            result = {"group": group, "phase": phase, "status": "completed", "execution_kind": "reused_candidate",
                "packet_sha256": sha256(destination / "packet/input.json"), "parsed": copy.deepcopy(old["check"]["parsed"]),
                "included_in_e2e": False, "zg_available": False, "new_model_calls": 0,
                "session": {"status": "reused_without_execution", "observed": {metric: 0 for metric in METRICS}},
                "resume": {"source_path": str(old["directory"].resolve()), "source_files_sha256": old["history"]["source_files_sha256"],
                    "original_status": old["result"].get("status"), "reparsed": old["check"]["reparsed"],
                    "old_diagnostic_identity": self.provenance["previous_diagnostic_identity"],
                    "historical_usage": old["history"]["observed"], "historical_usage_counted_in_new_cost": False}}
            write_json(destination / "result.json", result)
        else:
            # Old packet checks are exact. A new packet may only arise from the
            # current fixed catalog (e.g. a smaller inline-Qoder packet).
            result = self.fresh_runner(**kwargs)
            result["execution_kind"] = "fresh_session"
            if old:
                result["resume_previous_failed_attempt"] = {"source_path": str(old["directory"].resolve()),
                    "original_status": old["result"].get("status"), "non_reuse_reasons": old["check"]["reasons"]}
                write_json(kwargs["output"] / "result.json", result)
        with self.lock:
            attempt.update(execution_kind=result["execution_kind"], status=result.get("status"),
                           observed=result.get("session", {}).get("observed", {}))
            self._persist()
        return result

    def _persist(self) -> None:
        """Called under the reentrant lock; preserve a useful audit if a new session is interrupted."""
        self.args.output.mkdir(parents=True, exist_ok=True)
        saved = self.args.output / "resume-source-evidence"
        if not saved.exists():
            shutil.copytree(self.args.resume_candidates_dir, saved, symlinks=True)
        write_json(self.args.output / "candidate-resume-audit.json", self.audit())

    def audit(self) -> dict:
        with self.lock:
            current = sorted(copy.deepcopy(self.current), key=lambda x: (x["phase"], x["group"], x["path"]))
        unused = [str(item["directory"]) for key, item in self.candidates.items() if item["check"]["reusable"] and key not in self.consumed]
        return {**self.provenance, "historical_attempts": self.historical, "current_attempts": current,
            "historical_annotation_cost": _cost(self.historical), "current_annotation_cost": _cost(current),
            "reused_candidate_sessions": sum(a["execution_kind"] == "reused_candidate" for a in current),
            "fresh_candidate_sessions": sum(a["phase"] == "candidate" and a["execution_kind"] == "fresh_session" for a in current),
            "fresh_review_sessions": sum(a["phase"] == "review" for a in current),
            "unused_verified_candidates": unused,
            "original_candidate_artifacts_unchanged": directory_identity(self.args.resume_candidates_dir) == self.original_files,
            "cost_policy": "All previous candidate failures and interrupted reviews retained once as historical cost. Reused candidates contribute zero new cost; fresh candidate/review sessions are separate. Unknown cost is not zero."}


def execute_resume(args: argparse.Namespace, *, fresh_runner: Callable[..., dict] | None = None) -> dict:
    if args.output.exists():
        raise ValueError("Resume output must be new; historical results are never overwritten")
    try:
        runner = CandidateResume(args, fresh_runner=fresh_runner)
    except (ValueError, OSError, KeyError, TypeError) as error:
        write_json(args.output / "candidate-resume-preflight-failure.json", {
            "status": "failed_closed", "error": str(error), "new_model_calls": 0,
            "resume_identity": str(args.resume_identity), "resume_candidates_dir": str(args.resume_candidates_dir)})
        raise
    result = None
    try:
        result = gt.execute(args, session_runner=runner)
        audit = runner.audit()
        if audit["unused_verified_candidates"] or not audit["original_candidate_artifacts_unchanged"]:
            raise ValueError("Verified prior candidates were skipped or original artifacts changed")
        result["candidate_resume"] = {k: audit[k] for k in ("evidence_identity", "previous_diagnostic_identity",
            "current_diagnostic_identity", "reused_candidate_sessions", "fresh_candidate_sessions", "fresh_review_sessions",
            "historical_annotation_cost", "current_annotation_cost")}
        write_json(args.output / "annotation-manifest.json", result)
        return result
    finally:
        with runner.lock:
            runner._persist()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("analysis", "case", "entries", "source-root", "output", "resume-candidates-dir", "resume-identity"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--resume-snapshot", type=Path)
    parser.add_argument("--image", default="zg-readonly-qa:0.2.2")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args(argv)
    report = execute_resume(args)
    print(json.dumps({k: report[k] for k in ("status", "query_count", "scorable_queries", "unknown_queries", "labels_sha256", "candidate_resume")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
