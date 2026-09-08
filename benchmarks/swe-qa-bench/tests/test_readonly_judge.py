from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zg_bench.swe_qa import SweQaError
from zg_bench.swe_qa.readonly_judge import (
    CRITERIA,
    extract_final_answer,
    judge_runs,
    load_case,
    parse_assessment,
)


def assessment(score=1):
    return {key: {"score": score, "reason": "The frozen source supports this conclusion.",
                  "evidence_ids": ["getter"]} for key in CRITERIA}


def response(content=None):
    return {
        "id": "response-1", "model": "glm-5.2",
        "choices": [{"message": {"content": json.dumps(assessment()) if content is None else content,
                                    "reasoning_content": "JUDGE_HIDDEN_REASONING"}}],
        "usage": {"prompt_tokens": 101, "completion_tokens": 42},
    }


class ReadonlyJudgeTest(unittest.TestCase):
    def fixture(self, root: Path, missing=False):
        source = "    return self._fget\n"
        case = {
            "case_id": "reflex-6", "question": "What does the getter do?",
            "repo": {"commit": "frozen"},
            "reference_answer": "Corrected reference: getter analysis supplies dependencies.",
            "required_facts": ["Identify getter and dependency analysis."],
            "evidence": [{"id": "getter", "path": "src/base.py", "start_line": 1,
                          "end_line": 1, "text": source,
                          "sha256": hashlib.sha256(source.encode()).hexdigest()}],
        }
        case_path = root / "case.json"
        case_path.write_text(json.dumps(case))
        trials = []
        for profile in ("baseline", "zvec-grep"):
            trial_id = f"reflex-6-r01-{profile}"
            trajectory_path = f"{trial_id}/agent/trajectory.json"
            trials.append({"trial_id": trial_id, "profile": profile,
                           "trajectory_path": trajectory_path, "status": "planned"})
            if missing and profile == "zvec-grep":
                continue
            path = root / trajectory_path
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"schema_version": "ATIF-v1.7", "steps": [
                {"source": "user", "message": "USER_CONTEXT_NOT_FOR_JUDGE"},
                {"source": "agent", "message": "PROGRESS_NOT_FOR_JUDGE",
                 "reasoning_content": "CANDIDATE_HIDDEN_REASONING",
                 "tool_calls": [{"function_name": "read"}],
                 "observation": {"results": [{"content": "TOOL_OBSERVATION_NOT_FOR_JUDGE"}]}},
                {"source": "agent", "message": "The getter is analyzed for dependencies.",
                 "reasoning_content": "FINAL_HIDDEN_REASONING"},
            ]}))
        (root / "plan.json").write_text(json.dumps({"case_id": "reflex-6", "trials": trials}))
        return case_path

    def test_all_planned_rows_blinded_only_final_answers_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_path = self.fixture(root, missing=True)
            calls = []

            def complete(**kwargs):
                calls.append(kwargs)
                return response()

            with patch.dict("os.environ", {"GLM_API_KEY": "private-test-token"}, clear=True):
                report = judge_runs(runs_dir=root, case_path=case_path, output=root / "judgment.json",
                                    expected_per_profile=1, completion_fn=complete)
            self.assertEqual(report["planned_trials"], 2)
            self.assertEqual(len(calls), 1)
            payload = json.loads(calls[0]["messages"][1]["content"])
            self.assertEqual(payload["candidate_answer"], "The getter is analyzed for dependencies.")
            self.assertNotIn("profile", payload)
            self.assertNotIn("trial_id", payload)
            self.assertIn("Corrected reference", payload["corrected_reference_answer"])
            self.assertEqual(calls[0]["temperature"], 0)
            self.assertEqual(calls[0]["model"], "openai/glm-5.2")
            self.assertEqual(report["summary"]["baseline"]["pass"], 1)
            self.assertEqual(report["summary"]["zvec-grep"]["unscored"], 1)
            saved = (root / "judgment.json").read_text()
            for forbidden in ("private-test-token", "HIDDEN_REASONING", "PROGRESS_NOT_FOR_JUDGE",
                              "USER_CONTEXT_NOT_FOR_JUDGE", "TOOL_OBSERVATION_NOT_FOR_JUDGE"):
                self.assertNotIn(forbidden, saved)
            self.assertTrue((root / "judgment.md").is_file())
            attempt = report["trials"][0]["attempts"][0]
            self.assertEqual(attempt["resolved_model"], "glm-5.2")
            self.assertEqual(attempt["usage"]["input_tokens"], 101)

    def test_retry_preserves_failed_attempts_unknowns_and_no_exception_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_path = self.fixture(root, missing=True)
            calls = 0

            def complete(**kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("provider echoed private-test-token")
                if calls == 2:
                    return response("not-json")
                return response(json.dumps(assessment("?")))

            with patch.dict("os.environ", {"GLM_API_KEY": "private-test-token"}, clear=True):
                report = judge_runs(runs_dir=root, case_path=case_path, output=root / "judgment.json",
                                    attempts=3, completion_fn=complete)
            judged = next(r for r in report["trials"] if r["profile"] == "baseline")
            self.assertEqual([a["status"] for a in judged["attempts"]],
                             ["transport_error", "invalid_assessment", "judged"])
            self.assertEqual(judged["attempts"][1]["content"], "not-json")
            self.assertEqual(judged["quality"], "uncertain")
            saved = (root / "judgment.json").read_text()
            self.assertNotIn("provider echoed", saved)
            self.assertNotIn("private-test-token", saved)

    def test_exhausted_judge_is_unscored_not_wrong_or_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_path = self.fixture(root)
            with patch.dict("os.environ", {"GLM_API_KEY": "private-test-token"}, clear=True):
                report = judge_runs(runs_dir=root, case_path=case_path, output=root / "judgment.json",
                                    attempts=1, completion_fn=lambda **kw: response("{}"))
            self.assertEqual(len(report["trials"]), 2)
            self.assertTrue(all(r["status"] == "judge_failed" for r in report["trials"]))
            self.assertEqual(report["summary"]["baseline"]["unscored"], 1)
            self.assertEqual(report["summary"]["baseline"]["fail"], 0)

    def test_incomplete_trajectory_does_not_reuse_progress_or_reasoning(self):
        for final in ({"source": "agent", "message": "I will inspect the source", "tool_calls": [{}]},
                      {"source": "agent", "message": "", "reasoning_content": "an answer"}):
            self.assertIsNone(extract_final_answer({"steps": [
                {"source": "agent", "message": "earlier partial answer"}, final]}))
        self.assertIsNone(extract_final_answer({"steps": [
            {"source": "agent", "message": "progress"}, {"source": "tool", "message": "output"}]}))

    def test_score_schema_rejects_boolean_and_fabricated_citations(self):
        for score in (True, 0.5, "1", None):
            with self.assertRaises(SweQaError):
                parse_assessment(json.dumps(assessment(score)), {"getter"})
        invalid = assessment()
        invalid["evidence_support"]["evidence_ids"] = ["imaginary"]
        with self.assertRaises(SweQaError):
            parse_assessment(json.dumps(invalid), {"getter"})
        self.assertEqual(parse_assessment(json.dumps(assessment("?")), {"getter"})["factual_correctness"]["score"], "?")

    def test_bad_source_hash_and_plan_count_fail_before_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.fixture(root)
            with self.assertRaises(SweQaError):
                judge_runs(runs_dir=root, case_path=path, output=root / "out.json", expected_per_profile=5)
            case = json.loads(path.read_text())
            case["evidence"][0]["text"] = "modified"
            path.write_text(json.dumps(case))
            with self.assertRaises(SweQaError):
                load_case(path)

    def test_missing_credentials_preserves_all_planned_rows_without_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.fixture(root)
            with patch.dict("os.environ", {}, clear=True):
                report = judge_runs(runs_dir=root, case_path=path, output=root / "out.json",
                                    completion_fn=lambda **kw: self.fail("must not call"))
            self.assertTrue(all(r["status"] == "judge_unavailable" for r in report["trials"]))


if __name__ == "__main__":
    unittest.main()
