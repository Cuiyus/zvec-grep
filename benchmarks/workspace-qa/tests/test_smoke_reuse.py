from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import report
import run_task
import smoke_reuse


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class SmokeReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.here = self.repository / "benchmarks/workspace-qa"
        self.record_path = self.here / "data/smoke-validation.json"
        self.source, self.output = self.root / "artifact", self.root / "reused"
        for name in smoke_reuse.EVALUATION_FILES:
            path = self.repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("frozen evaluation fixture " + name)
        self.task = {"task_id": "3", "slice": "fixture", "answer_filename": "answer.md"}
        self.lock = {"tasks": [self.task], "dataset": {"revision": "frozen"}}
        dump(self.here / "data/lock.json", self.lock)
        self.make_artifact()
        self.record = {"phase": "smoke", "task_id": "3", "qa_trials": 2,
                       "pipeline_validation_complete": True, "included_in_formal_benchmark": False,
                       "efficacy_claim_ready": False, "qa_run_id": 123, "qa_commit": "qa-commit",
                       "recovery_run_id": 456, "recovery_commit": "recovery-commit",
                       "recovery_artifact_name": "workspace-qa-rejudge-456-1",
                       "evaluation_files": {name: smoke_reuse.digest(self.repository / name)
                                            for name in smoke_reuse.EVALUATION_FILES},
                       "evidence_sha256": {name: smoke_reuse.digest(self.source / name)
                                           for name in smoke_reuse.EVIDENCE_FILES}}
        dump(self.record_path, self.record)

    def make_artifact(self):
        trials, judgments = [], []
        for profile in ("baseline", "with-zg"):
            trial_id = "3-r01-" + profile
            answer = "original " + profile + " answer"
            candidate = trial_id + "/candidate/answer.md"
            trials.append({"trial_id": trial_id, "task_id": "3", "profile": profile, "repetition": 1,
                           "status": "completed", "answer": answer, "candidate_output_path": candidate,
                           "input_tokens": 100, "output_tokens": 10, "cached_input_tokens": 20,
                           "tool_calls": 2, "zg_tool_calls": 0, "zg_tool_calls_successful": 0, "wall_seconds": 1,
                           "source_unchanged": True, "original_seed_unchanged": True,
                           "working_index_semantic_unchanged": profile == "with-zg"})
            path = self.source / "runs" / candidate
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(answer)
            judgments.append({"trial_id": trial_id, "task_id": "3", "profile": profile, "repetition": 1,
                              "status": "judged", "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                              "score": 1, "criteria": [{"id": 0, "score": True, "reason": "fixture"}],
                              "judge_latency_seconds": 0.1})
        ledger = self.source / "runs/trial-results.json"
        dump(ledger, {"task_id": "3", "repetitions_per_profile": 1, "trials": trials})
        dump(self.source / "runs/judgements.json", {"task_id": "3", "trials": judgments,
             "judge_model": "glm-5.2", "rubrics": ["fixture"], "trial_results_sha256": smoke_reuse.digest(ledger)})
        dump(self.source / "selection.json", {"tasks": [self.task], "dataset": self.lock["dataset"], "repetitions": 1})
        init = {"type": "system", "subtype": "init", "qodercli_version": "1.1.45", "model": "Qwen3.8-Max",
                "tools": [run_task.ZG_SEARCH_TOOL], "mcp_servers": [{"name": "zvec_grep", "status": "connected"}]}
        dump(self.source / "runs/3-r01-with-zg/agent/qodercli-stream.jsonl", init)
        dump(self.source / "runs/3-r01-with-zg/agent/zg-trace.jsonl", {"event": "start"})
        probe = self.source / "sdk-preflight/qoder"
        dump(probe / "result.json", {"status": "completed", "phase": "setup_qoder_mcp_probe",
             "included_in_benchmark": False, "embedding_model": "qwen/qwen3.7-text-embedding",
             "model": "qwen3.8-max", "model_identity": {"valid": True},
             "zg_tool_calls_successful": 1, "successful_vector_searches": 1})
        events = [init, {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "probe",
                  "name": run_task.ZG_SEARCH_TOOL, "input": {"vector": "fixture source"}}]}},
                  {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "probe", "is_error": False}]}}]
        path = probe / "agent/qodercli-stream.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("\n".join(json.dumps(event) for event in events))
        dump(probe / "agent/zg-trace.jsonl", {"event": "search", "origin": "agent-mcp", "status": "success",
             "request": {"routes": [{"mode": "vector", "query": "fixture source"}]}, "text": "probe.md:1 fixture"})
        validation = run_task.smoke_validation(self.source / "runs", "smoke")
        self.assertEqual(validation["status"], "valid")
        dump(self.source / "smoke_validation.json", validation)
        report.write_report(runs_dir=self.source / "runs", output=self.source / "report", manifest_path=self.source / "selection.json")
        run_task.annotate_smoke_report(self.source / "report", validation)
        dump(self.source / "rejudge-provenance.json", {"status": "completed", "no_new_qa_trials": True,
             "new_qa_trials": 0, "original_qa_evidence_unchanged": True, "source_run_id": 123,
             "ci_identity": {"run_id": "456", "commit": "recovery-commit"}})

    def context(self):
        stack = ExitStack()
        stack.enter_context(patch.object(smoke_reuse, "RECORD_PATH", self.record_path))
        stack.enter_context(patch.object(smoke_reuse, "REPOSITORY", self.repository))
        stack.enter_context(patch.object(smoke_reuse, "HERE", self.here))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return stack

    def test_compatibility_checks_exact_dependency_list_and_hashes(self):
        with self.context():
            self.assertTrue(smoke_reuse.compatibility()["reuse_eligible"])
            for change in ("missing", "extra", "malformed"):
                with self.subTest(change=change):
                    record = deepcopy(self.record)
                    if change == "missing":
                        record["evaluation_files"].pop(smoke_reuse.EVALUATION_FILES[0])
                    elif change == "extra":
                        record["evaluation_files"]["README.md"] = "0" * 64
                    else:
                        record["evaluation_files"] = []
                    dump(self.record_path, record)
                    self.assertFalse(smoke_reuse.compatibility()["reuse_eligible"])
            dump(self.record_path, self.record)
            for name in smoke_reuse.EVALUATION_FILES:
                path = self.repository / name
                before = path.read_bytes()
                path.write_bytes(before + b" changed")
                result = smoke_reuse.compatibility()
                self.assertFalse(result["reuse_eligible"], name)
                self.assertEqual(result["incompatible_files"], [name])
                path.write_bytes(before)

    def test_check_mode_is_offline_and_miss_outputs_false_without_error(self):
        output = self.root / "github-output"
        with self.context(), patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), \
                patch("subprocess.Popen", side_effect=AssertionError("process forbidden")):
            self.assertEqual(smoke_reuse.main(["--check-compatibility", "--github-output", str(output)]), 0)
            self.assertEqual(output.read_text(), "reuse_eligible=true\nrecovery_run_id=456\nartifact_name=workspace-qa-rejudge-456-1\n")
            self.record_path.unlink()
            output.unlink()
            self.assertEqual(smoke_reuse.main(["--check-compatibility", "--github-output", str(output)]), 0)
            self.assertTrue(output.read_text().startswith("reuse_eligible=false\n"))

    def test_full_reuse_recomputes_current_gate_and_report_without_execution(self):
        marker = self.root / "must-not-exist"
        payload = self.source / "payload.py"
        payload.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        dump(self.source / "runs/3-r01-with-zg/agent/session-spec.json", {"command": [sys.executable, str(payload)]})
        original = smoke_reuse.inventory(self.source)
        with self.context(), patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), \
                patch("subprocess.Popen", side_effect=AssertionError("process forbidden")), \
                patch.object(run_task, "smoke_validation", wraps=run_task.smoke_validation) as gate, \
                patch.object(report, "write_report", wraps=report.write_report) as summarize:
            self.assertEqual(smoke_reuse.main(["--artifact-root", str(self.source), "--output", str(self.output)]), 0)
            gate.assert_called_once()
            summarize.assert_called_once()
        self.assertFalse(marker.exists())
        self.assertEqual(smoke_reuse.inventory(self.source), original)
        for name in ("runs/trial-results.json", "runs/judgements.json"):
            self.assertEqual(smoke_reuse.digest(self.output / name), original[name])
        provenance = smoke_reuse.read_object(self.output / "smoke-reuse-provenance.json")
        self.assertEqual(provenance["status"], "completed")
        self.assertEqual(provenance["new_qa_trials"], 0)
        self.assertTrue(provenance["no_model_or_agent_calls"])
        self.assertTrue(provenance["pipeline_validation_complete"])
        self.assertFalse(provenance["included_in_formal_benchmark"])
        self.assertIn("No new QA trials", (self.output / "summary.md").read_text())

    def test_any_pinned_core_file_tampering_fails_before_revalidation(self):
        for index, name in enumerate(smoke_reuse.EVIDENCE_FILES):
            with self.subTest(name=name):
                path = self.source / name
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                with self.context(), patch.object(run_task, "smoke_validation", side_effect=AssertionError("must fail before gate")):
                    self.assertEqual(smoke_reuse.reuse(self.source, self.root / f"bad-core-{index}"), 1)
                path.write_bytes(original)

    def test_raw_logs_and_candidate_files_must_match_anchored_hashes(self):
        for index, name in enumerate(("sdk-preflight/qoder/agent/zg-trace.jsonl",
                "sdk-preflight/qoder/result.json", "runs/3-r01-with-zg/agent/qodercli-stream.jsonl",
                "runs/3-r01-baseline/candidate/answer.md")):
            with self.subTest(name=name):
                path = self.source / name
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                with self.context():
                    self.assertEqual(smoke_reuse.reuse(self.source, self.root / f"bad-raw-{index}"), 1)
                path.write_bytes(original)

    def test_old_success_does_not_override_current_checker_or_missing_judge(self):
        with self.context(), patch.object(run_task, "smoke_validation", return_value={"status": "invalid"}):
            self.assertEqual(smoke_reuse.reuse(self.source, self.output), 1)
        with self.context(), patch.object(report, "write_report", return_value={"summary": {"complete": False}}):
            self.assertEqual(smoke_reuse.reuse(self.source, self.root / "missing-judge"), 1)

    def test_symlinks_and_unsafe_paths_are_rejected_without_following_them(self):
        (self.source / "outside-link").symlink_to(self.root)
        with self.context():
            self.assertEqual(smoke_reuse.reuse(self.source, self.output), 1)
        for name in ("../outside", "/absolute", "x/../outside", "x//y", "x\\y"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                smoke_reuse.relative_path(name)

    def test_changed_configuration_cannot_be_reused_or_bypassed_by_a_full_run(self):
        (self.repository / smoke_reuse.EVALUATION_FILES[0]).write_text("changed limits")
        with self.context(), patch.object(run_task, "smoke_validation", side_effect=AssertionError("must not run")):
            self.assertEqual(smoke_reuse.reuse(self.source, self.output), 1)
        provenance = smoke_reuse.read_object(self.output / "smoke-reuse-provenance.json")
        self.assertFalse(provenance["compatibility"]["reuse_eligible"])


if __name__ == "__main__":
    unittest.main()
