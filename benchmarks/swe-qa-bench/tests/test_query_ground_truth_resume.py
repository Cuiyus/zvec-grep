from __future__ import annotations

import argparse
import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from zg_bench.swe_qa import query_ground_truth as gt
from zg_bench.swe_qa import query_ground_truth_resume as resume
from zg_bench.swe_qa.readonly_agents import agent_spec, expected_tools, qoder_contract, _qoder_identity
from zg_bench.swe_qa.readonly_run import directory_identity, sha256, wire_contract, write_json


ENV = {"QA_EVIDENCE_RUN_ID": "original-e2e", "QA_EVIDENCE_COMMIT": "original-ci-commit",
       "GITHUB_RUN_ID": "new-diagnostic", "GITHUB_SHA": "new-diagnostic-commit", "GITHUB_RUN_ATTEMPT": "1"}
SOURCE_COMMIT = "a" * 40


def packet_for(catalog):
    return {"annotations": [{k: row.get(k) for k in ("annotation_id", "kind", "request", "original_question", "context_id",
                            "prior_turn_feedback", "prior_assistant_text", "context_capture_limitation")} for row in catalog]}


def write_old_candidate(root, group, packet, *, parser_failure=False, denied=False, name="batch-000"):
    directory = root / "candidate" / group / name
    write_json(directory / "packet/input.json", packet)
    agent = "qodercli" if group.startswith("qoder-") else "opencode"
    model = "glm-5.2" if group == "opencode-glm52" else "qwen3.8-max"
    spec = agent_spec(agent, model, base_url=gt.OPENCODE_CUSTOM_BASE_URL if agent == "opencode" else None)
    parsed = {"annotations": [] if denied else [{"annotation_id": row["annotation_id"], "classification": "original" if row["kind"] == "original" else "equivalent_rewrite", "targets": []}
                                               for row in packet["annotations"]]}
    raw = json.dumps(parsed)
    if parser_failure:
        raw = "Here is the requested data:\n```json\n" + raw + "\n```"
    (directory / "raw-answer.txt").write_text(raw)
    session = {"status": "completed", "returncode": 0,
               "observed": {"model_requests": 4, "tool_calls": 5, "input_tokens": 600}}
    write_json(directory / "agent/session.json", session)
    write_json(directory / "agent/trajectory.json", {"steps": [{"source": "agent", "message": raw}]})
    if agent == "opencode":
        native = [{"type": "text", "part": {"type": "text", "text": raw}}]
        wire = [{"event": "request", "request_id": "one", "model": spec.provider_model, "temperature": 0,
                 "tool_names": expected_tools(spec, zg=False)},
                {"event": "response", "request_id": "one", "status": 200, "model": spec.provider_model}]
        (directory / "agent/wire.jsonl").write_text("\n".join(map(json.dumps, wire)))
    else:
        native = [{"type": "system", "subtype": "init", "tools": expected_tools(spec, zg=False), "permissionMode": "dontAsk"},
                  {"type": "assistant", "message": {"model": "qwen3.8-max", "content": [{"type": "text", "text": raw}]}},
                  {"type": "result", "subtype": "success", "modelUsage": {"qwen3.8-max": {}}, "result": raw}]
    (directory / "agent" / spec.stream_filename).write_text("\n".join(map(json.dumps, native)))
    result = {"group": group, "phase": "candidate", "status": "failed" if parser_failure or denied else "completed",
        "packet_sha256": sha256(directory / "packet/input.json"), "agent_spec": spec.to_dict(),
        "returncode": 0, "session": session, "has_final_answer": True, "error_event_count": 0,
        "contract_error_count": 0, "tool_error_count": 1 if denied else 0}
    if agent == "opencode":
        result["wire_contract"] = wire_contract(directory / "agent", spec.provider_model, expected_tools(spec, zg=False))
    else:
        result["tool_contract"] = qoder_contract(native, zg=False)
        result["model_identity"] = _qoder_identity(native, spec)
    if not parser_failure:
        result["parsed"] = parsed
    write_json(directory / "result.json", result)
    return directory


def fixture(root):
    case_path = root / "case.json"; entries_path = root / "entries.json"
    case = {"question": "Question", "case_id": "reflex-6", "repo": {"url": "https://example.invalid/repo", "commit": SOURCE_COMMIT}}
    write_json(case_path, case); write_json(entries_path, {})
    catalog = [{"annotation_id": "a1", "kind": "original", "request": {"root": "/app", "query": "Question"},
                "context_id": "c1", "original_question": "Question", "prior_turn_feedback": []},
               {"annotation_id": "a2", "kind": "faithful", "request": {"root": "/app", "query": "rewritten"},
                "context_id": "c2", "original_question": "Question", "prior_turn_feedback": []}]
    analysis = {"annotation_catalog": catalog, "request_catalog": [{"request_id": "r", "request": catalog[1]["request"]}]}
    analysis_path = root / "current-analysis.json"; write_json(analysis_path, analysis)
    prior = root / "prior/diagnostic"; old = prior / "ground-truth"
    write_json(old / "catalog.json", catalog); write_json(prior / "query-analysis.json", analysis)
    identity = {"protocol": "readonly-qa-v6-diagnosis-resume",
        "evidence_identity": {"run_id": ENV["QA_EVIDENCE_RUN_ID"], "commit": ENV["QA_EVIDENCE_COMMIT"], "attempt": "1"},
        "analysis_identity": {"run_id": "old-diagnostic", "commit": "old-diagnostic-commit", "attempt": "1"}}
    write_json(prior / "resume-identity.json", identity)
    source = root / "prepared/source"; source.mkdir(parents=True); (source / "core.py").write_text("class Example: pass\n")
    snapshot = {"source": {"git_commit": SOURCE_COMMIT, "sha256": "source-id"}, "package": {"name": "@zvec/zvec-grep", "version": "0.2.2"}}
    write_json(root / "prepared/runtime/snapshot.json", snapshot)
    write_json(root / "prior/prepared/runtime/snapshot.json", snapshot)
    prepared = {"repo": case["repo"], "case_sha256": sha256(case_path), "snapshot_sha256": sha256(root / "prepared/runtime/snapshot.json"),
        "ci_identity": {"GITHUB_RUN_ID": ENV["QA_EVIDENCE_RUN_ID"], "GITHUB_SHA": ENV["QA_EVIDENCE_COMMIT"]},
        "file_identities": {"source": directory_identity(source, skip_git=True)}}
    write_json(root / "prepared/prepared-manifest.json", prepared)
    write_json(prior / "evidence-manifest-identities.json", [{"path": "prepared/prepared-manifest.json", "sha256": sha256(root / "prepared/prepared-manifest.json")}])
    packet = packet_for(catalog)
    for group in gt.GROUPS:
        write_old_candidate(old, group, packet, parser_failure=group == "opencode-glm52", denied=group == "qoder-qwen38max")
    write_json(old / "review/opencode-glm52/batch-000/packet/input.json", {"queries": [], "proposals": []})
    (old / "review/opencode-glm52/batch-000/agent").mkdir()
    (old / "review/opencode-glm52/batch-000/agent/opencode.txt").write_text('{"type":"step_start"}\n')
    args = argparse.Namespace(analysis=analysis_path, case=case_path, entries=entries_path, source_root=source,
        output=root / "new-labels", resume_candidates_dir=old, resume_identity=prior / "resume-identity.json",
        resume_snapshot=None, image="pinned-image", batch_size=8, timeout=1200)
    return args, packet


class CandidateResumeTests(unittest.TestCase):
    def context(self):
        return patch.dict(os.environ, ENV, clear=True)

    def test_original_completed_and_parser_failed_candidates_are_reused_without_new_cost(self):
        with tempfile.TemporaryDirectory() as folder, self.context(), patch.object(resume, "run_checked", return_value=SOURCE_COMMIT):
            args, packet = fixture(Path(folder)); original = directory_identity(args.resume_candidates_dir)
            with patch.object(gt, "run_session") as fresh:
                wrapper = resume.CandidateResume(args)
                for group in gt.GROUPS[:2]:
                    result = wrapper(group=group, phase="candidate", packet=packet, source_root=args.source_root,
                        image=args.image, output=args.output / "candidate" / group / "new-batch-name", timeout=1200)
                    self.assertEqual(result["status"], "completed")
                    self.assertEqual(result["session"]["observed"]["input_tokens"], 0)
                    self.assertEqual(result["resume"]["historical_usage"]["input_tokens"], 600)
                    self.assertEqual(result["resume"]["reparsed"], group == "opencode-glm52")
                fresh.assert_not_called()
            audit = wrapper.audit()
            self.assertEqual(audit["reused_candidate_sessions"], 2)
            self.assertEqual(audit["historical_annotation_cost"]["input_tokens_observed_sum"], 1800)
            self.assertIsNone(audit["historical_annotation_cost"]["input_tokens"])
            self.assertEqual(audit["historical_annotation_cost"]["input_tokens_unknown_attempts"], 1)
            self.assertEqual(audit["current_annotation_cost"]["input_tokens"], 0)
            self.assertEqual(directory_identity(args.resume_candidates_dir), original)

    def test_qoder_denied_or_empty_candidates_are_retained_and_replaced_only_by_fresh_calls(self):
        with tempfile.TemporaryDirectory() as folder, self.context(), patch.object(resume, "run_checked", return_value=SOURCE_COMMIT):
            args, packet = fixture(Path(folder))
            def fresh(**kwargs):
                return {"status": "completed", "session": {"observed": {"model_requests": 2, "tool_calls": 3, "input_tokens": 44}}}
            wrapper = resume.CandidateResume(args, fresh_runner=fresh)
            result = wrapper(group=gt.GROUPS[2], phase="candidate", packet=packet, source_root=args.source_root,
                             image=args.image, output=args.output / "candidate/qoder/new", timeout=1200)
            self.assertEqual(result["execution_kind"], "fresh_session")
            self.assertEqual(result["resume_previous_failed_attempt"]["original_status"], "failed")
            self.assertIn("qoder_tool_or_packet_permission_errors", result["resume_previous_failed_attempt"]["non_reuse_reasons"])
            self.assertEqual(wrapper.audit()["current_annotation_cost"]["input_tokens"], 44)

    def test_completed_candidate_with_missing_duplicate_or_foreign_ids_fails_closed(self):
        for mutation in ("missing", "duplicate", "foreign", "native_error", "raw_mismatch"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder, self.context(), patch.object(resume, "run_checked", return_value=SOURCE_COMMIT):
                args, packet = fixture(Path(folder)); path = args.resume_candidates_dir / "candidate/opencode-qwen38max/batch-000"
                result = json.loads((path / "result.json").read_text())
                if mutation == "native_error":
                    result["error_event_count"] = 1
                    write_json(path / "result.json", result)
                elif mutation == "raw_mismatch":
                    (path / "raw-answer.txt").write_text('{"annotations":[]}')
                else:
                    parsed = copy.deepcopy(result["parsed"])
                    if mutation == "missing": parsed["annotations"].pop()
                    if mutation == "duplicate": parsed["annotations"].append(parsed["annotations"][0])
                    if mutation == "foreign": parsed["annotations"][0]["annotation_id"] = "unseen-id"
                    raw = json.dumps(parsed); (path / "raw-answer.txt").write_text(raw)
                    write_json(path / "agent/trajectory.json", {"steps": [{"source": "agent", "message": raw}]})
                    (path / "agent/opencode.txt").write_text(json.dumps({"type": "text", "part": {"text": raw}}))
                with patch.object(gt, "run_session") as fresh:
                    with self.assertRaisesRegex(ValueError, "Completed old candidate"):
                        resume.CandidateResume(args)
                    fresh.assert_not_called()

    def test_packet_content_change_or_duplicate_old_attempt_is_not_result_selection(self):
        for mutation in ("packet", "duplicate"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder, self.context(), patch.object(resume, "run_checked", return_value=SOURCE_COMMIT):
                args, packet = fixture(Path(folder)); path = args.resume_candidates_dir / "candidate/opencode-qwen38max/batch-000"
                if mutation == "duplicate":
                    shutil.copytree(path, path.with_name("another-attempt"))
                else:
                    changed = copy.deepcopy(packet); changed["annotations"][0]["request"]["query"] = "changed query"
                    write_json(path / "packet/input.json", changed)
                with self.assertRaises(ValueError):
                    resume.CandidateResume(args)

    def test_source_snapshot_catalog_and_evidence_identity_mismatch_fail_before_any_paid_session(self):
        for mutation in ("source", "snapshot", "catalog", "identity"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder, self.context(), patch.object(resume, "run_checked", return_value=SOURCE_COMMIT):
                args, _ = fixture(Path(folder))
                if mutation == "source": (args.source_root / "core.py").write_text("different")
                if mutation == "snapshot": write_json(args.source_root.parent / "runtime/snapshot.json", {})
                if mutation == "catalog": write_json(args.resume_candidates_dir / "catalog.json", [])
                if mutation == "identity": os.environ["QA_EVIDENCE_RUN_ID"] = "unrelated"
                with patch.object(gt, "execute") as execute:
                    with self.assertRaises(ValueError): resume.execute_resume(args)
                    execute.assert_not_called()
                    self.assertEqual(json.loads((args.output / "candidate-resume-preflight-failure.json").read_text())["new_model_calls"], 0)

    def test_changed_new_packet_and_changed_batch_size_cannot_silently_rerun_verified_candidates(self):
        with tempfile.TemporaryDirectory() as folder, self.context(), patch.object(resume, "run_checked", return_value=SOURCE_COMMIT):
            args, packet = fixture(Path(folder)); wrapper = resume.CandidateResume(args)
            changed = copy.deepcopy(packet); changed["annotations"][0]["request"]["query"] = "different"
            with self.assertRaisesRegex(ValueError, "canonical packets"):
                wrapper(group=gt.GROUPS[0], phase="candidate", packet=changed, output=args.output / "unexpected")
            args.batch_size = 1
            with self.assertRaisesRegex(ValueError, "batching"):
                resume.CandidateResume(args)

    def test_parallel_candidates_and_fresh_reviews_keep_separate_old_and_new_costs(self):
        with tempfile.TemporaryDirectory() as folder, self.context(), patch.object(resume, "run_checked", return_value=SOURCE_COMMIT):
            args, packet = fixture(Path(folder)); calls = []
            def fresh(**kwargs):
                calls.append((kwargs["phase"], kwargs["group"]))
                return {"status": "completed", "session": {"observed": {"model_requests": 1, "tool_calls": 2, "input_tokens": 50}}}
            def fake_execute(options, *, session_runner):
                options.output.mkdir()
                def candidate(group):
                    return session_runner(group=group, phase="candidate", packet=packet, source_root=options.source_root,
                        image=options.image, output=options.output / "candidate" / group / "batch-000", timeout=1200)
                with ThreadPoolExecutor(max_workers=3) as executor: list(executor.map(candidate, gt.GROUPS))
                for group in gt.GROUPS:
                    session_runner(group=group, phase="review", packet={"queries": [], "proposals": []},
                        source_root=options.source_root, image=options.image, output=options.output / "review" / group / "batch-000", timeout=1200)
                return {"status": "frozen", "query_count": 2, "labels_sha256": "fixture", "scorable_queries": 0, "unknown_queries": 2}
            with patch.object(gt, "execute", side_effect=fake_execute):
                result = resume.execute_resume(args, fresh_runner=fresh)
            self.assertEqual(sorted(calls), sorted([("candidate", gt.GROUPS[2]), *[("review", g) for g in gt.GROUPS]]))
            audit = json.loads((args.output / "candidate-resume-audit.json").read_text())
            self.assertEqual(audit["reused_candidate_sessions"], 2)
            self.assertEqual(audit["fresh_candidate_sessions"], 1)
            self.assertEqual(audit["fresh_review_sessions"], 3)
            self.assertEqual(audit["current_annotation_cost"]["input_tokens"], 200)
            self.assertEqual(audit["historical_annotation_cost"]["input_tokens_observed_sum"], 1800)
            self.assertTrue(audit["original_candidate_artifacts_unchanged"])
            self.assertEqual(result["candidate_resume"]["current_annotation_cost"]["input_tokens"], 200)

    def test_cli_routes_current_gt_arguments_and_old_sources_without_direct_model_calls(self):
        with patch.object(resume, "execute_resume", return_value={"status": "frozen", "query_count": 13,
            "scorable_queries": 0, "unknown_queries": 13, "labels_sha256": "hash", "candidate_resume": {}}) as execute, redirect_stdout(io.StringIO()):
            code = resume.main(["--analysis", "analysis.json", "--case", "case.json", "--entries", "entries.json",
                "--source-root", "prepared/source", "--output", "new-labels", "--resume-candidates-dir", "prior/ground-truth",
                "--resume-identity", "prior/resume-identity.json"])
        self.assertEqual(code, 0)
        self.assertEqual(execute.call_args.args[0].batch_size, 8)
        self.assertEqual(execute.call_args.args[0].resume_candidates_dir, Path("prior/ground-truth"))


if __name__ == "__main__":
    unittest.main()
