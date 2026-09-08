from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zg_bench.swe_qa import SweQaError
from zg_bench.swe_qa.quality_review import (
    MODELS,
    consensus,
    load_review_case,
    review_messages,
    review_runs,
)
from zg_bench.swe_qa.readonly_judge import CRITERIA, judge_runs


BASE = Path(__file__).resolve().parents[1]
CASE = BASE / "cases/reflex-6.judge-v2.json"


def assessment(scores=None, reason="Supported by frozen source."):
    scores = scores or {}
    return {criterion: {"score": scores.get(criterion, 1), "reason": reason,
                        "evidence_ids": ["getter_accessor"]} for criterion in CRITERIA}


def response(model, scores=None, reason="Supported by frozen source."):
    return {"id": "response-test", "model": model.rsplit("/", 1)[-1],
            "choices": [{"message": {"content": json.dumps(assessment(scores, reason)),
                                      "reasoning_content": "HIDDEN_JUDGE_REASONING"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


class QualityReviewTest(unittest.TestCase):
    def fixture(self, root: Path, *, answers=True, execution_status="completed"):
        trials = []
        for profile in ("baseline", "zvec-grep"):
            ident = f"reflex-6-r01-{profile}"
            relative = f"{ident}/agent/trajectory.json"
            trials.append({"trial_id": ident, "profile": profile, "trajectory_path": relative,
                           "status": execution_status})
            if not answers:
                continue
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"steps": [
                {"source": "user", "message": "PRIVATE_USER_CONTEXT"},
                {"source": "agent", "message": "PROGRESS_NOT_AN_ANSWER", "tool_calls": [{"function_name": "read"}],
                 "observation": {"results": [{"content": "HIDDEN_TOOL_OBSERVATION"}]}},
                {"source": "agent", "message": "CANDIDATE_FINAL_ANSWER", "reasoning_content": "HIDDEN_AGENT_REASONING"},
            ]}))
        (root / "plan.json").write_text(json.dumps({"case_id": "reflex-6", "trials": trials}))
        return root / "quality-review.json"

    def oracle(self, *, candidate_scores=None, calibration_override=None, calls=None):
        examples = {example["answer"]: example for example in load_review_case(CASE)["calibration_examples"]}
        def complete(**kwargs):
            payload = json.loads(kwargs["messages"][1]["content"])
            answer, model = payload["candidate_answer"], kwargs["model"]
            sample = examples.get(answer)
            if calls is not None:
                calls.append({"kwargs": kwargs, "sample": sample["calibration_id"] if sample else None})
            if sample:
                scores = calibration_override(model, sample) if calibration_override else sample["expected_scores"]
            else:
                scores = candidate_scores(model) if candidate_scores else {}
            return response(model, scores)
        return complete

    def run_review(self, root, output, complete, **kwargs):
        with patch.dict("os.environ", {"GLM_API_KEY": "private-test-key"}, clear=True):
            return review_runs(runs_dir=root, case_path=CASE, output=output,
                               expected_per_profile=1, completion_fn=complete, **kwargs)

    def test_judge_v2_keeps_original_gold_and_adds_source_backed_calibration(self):
        case = load_review_case(CASE)
        original = json.loads((BASE / "cases/reflex-6.json").read_text())
        for key in ("case_id", "question", "repo", "evidence", "sufficient_sets"):
            self.assertEqual(case[key], original[key])
        self.assertEqual(len(case["calibration_examples"]), 4)
        self.assertEqual([s["expected_quality"] for s in case["calibration_examples"]].count("fail"), 3)
        self.assertTrue({"static_and_automatic_dependencies", "potentially_dirty_state_names"} <=
                        {e["id"] for e in case["judge_only_evidence"]})

    def test_calibrated_two_judge_pass_is_blinded_and_keeps_all_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.fixture(root)
            legacy = root / "judged.json"
            legacy.write_text("KEEP_EXISTING_JUDGMENT")
            calls = []
            report = self.run_review(root, output, self.oracle(calls=calls))
            self.assertEqual(len(calls), 12)  # Four calibration + two candidates, for each of two models.
            self.assertTrue(all(c["status"] == "passed" for c in report["calibration"].values()))
            self.assertTrue(all(r["quality"] == "pass" for r in report["trials"]))
            self.assertEqual(report["summary"]["baseline"]["planned"], 1)
            self.assertTrue(report["quality_gate"]["all_planned_answers_pass"])
            for call in calls:
                kwargs = call["kwargs"]
                payload = json.loads(kwargs["messages"][1]["content"])
                self.assertEqual(kwargs["temperature"], 0)
                self.assertEqual(set(payload), {"question", "corrected_reference_answer", "required_facts", "source_evidence", "candidate_answer"})
                for forbidden in ("expected_quality", "expected_scores", "calibration_examples", "trial_id", "profile", "input_tokens", "tool_calls"):
                    self.assertNotIn(forbidden, payload)
            saved = output.read_text()
            for forbidden in ("PRIVATE_USER_CONTEXT", "PROGRESS_NOT_AN_ANSWER", "HIDDEN_TOOL_OBSERVATION", "HIDDEN_AGENT_REASONING", "HIDDEN_JUDGE_REASONING", "private-test-key"):
                self.assertNotIn(forbidden, saved)
            self.assertEqual(legacy.read_text(), "KEEP_EXISTING_JUDGMENT")
            self.assertIn("not held-out", output.with_suffix(".md").read_text())
            attempt = report["trials"][0]["judgments"][MODELS[1]]["attempts"][0]
            self.assertEqual(attempt["resolved_model"], "qwen3.8-max")
            self.assertEqual(attempt["usage"]["input_tokens"], 100)

    def test_failed_calibration_blocks_pass_and_never_retries_valid_wrong_label(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = self.fixture(root)
            calls = []
            def wrong(model, sample):
                return {} if model == MODELS[0] and sample["calibration_id"] == "automatic-discards-static" else sample["expected_scores"]
            report = self.run_review(root, output, self.oracle(calibration_override=wrong, calls=calls))
            self.assertEqual(report["calibration"][MODELS[0]]["status"], "failed")
            self.assertEqual(report["calibration"][MODELS[0]]["matched_examples"], 3)
            self.assertTrue(all(r["raw_consensus"] == "pass" and r["quality"] == "uncalibrated" for r in report["trials"]))
            self.assertFalse(report["quality_gate"]["all_planned_answers_pass"])
            self.assertEqual(len([c for c in calls if c["kwargs"]["model"] == MODELS[0] and c["sample"] == "automatic-discards-static"]), 1)
            self.assertEqual(len(calls), 12)

    def test_disagreement_is_explicit_without_majority_or_fail_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = self.fixture(root)
            calls = []
            complete = self.oracle(candidate_scores=lambda model: {"factual_correctness": 0} if model == MODELS[1] else {}, calls=calls)
            report = self.run_review(root, output, complete)
            self.assertTrue(all(r["quality"] == "disagreement" for r in report["trials"]))
            self.assertTrue(all(r["flags"]["criterion_disagreement"] for r in report["trials"]))
            self.assertEqual(len(calls), 12)
            self.assertTrue(all(len(r["judgments"][MODELS[1]]["attempts"]) == 1 for r in report["trials"]))

    def test_transport_and_format_retries_are_bounded_and_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = self.fixture(root)
            oracle = self.oracle()
            keys = set()
            def complete(**kwargs):
                payload = json.loads(kwargs["messages"][1]["content"])
                key = (kwargs["model"], payload["candidate_answer"])
                # One retry on each calibration answer. Two candidate calls may
                # share wording; validity still stops at the first decision.
                if key not in keys:
                    keys.add(key)
                    if kwargs["model"] == MODELS[0]:
                        raise RuntimeError("provider echoed private-test-key")
                    return {"model": "qwen3.8-max", "choices": [{"message": {"content": "not JSON"}}]}
                return oracle(**kwargs)
            report = self.run_review(root, output, complete)
            examples = [e for c in report["calibration"].values() for e in c["examples"]]
            self.assertTrue(all(len(e["attempts"]) == 2 for e in examples))
            self.assertEqual(examples[0]["attempts"][0]["status"], "transport_error")
            self.assertEqual(examples[-1]["attempts"][0]["status"], "invalid_assessment")
            self.assertIn("not JSON", output.read_text())
            self.assertNotIn("provider echoed", output.read_text())
            self.assertNotIn("private-test-key", output.read_text())
            self.assertTrue(report["quality_gate"]["all_planned_answers_pass"])

    def test_unavailable_judge_keeps_denominator_and_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = self.fixture(root)
            oracle = self.oracle()
            calls = []
            def complete(**kwargs):
                calls.append(kwargs["model"])
                if kwargs["model"] == MODELS[1]:
                    raise RuntimeError("unavailable")
                return oracle(**kwargs)
            report = self.run_review(root, output, complete)
            self.assertEqual(report["calibration"][MODELS[1]]["status"], "unavailable")
            self.assertEqual(calls.count(MODELS[1]), 12)
            self.assertTrue(all(r["quality"] == "unscored" for r in report["trials"]))
            self.assertTrue(all(r["judgments"][MODELS[1]]["quality"] == "unscored" for r in report["trials"]))
            self.assertEqual(report["summary"]["zvec-grep"]["planned"], 1)
            self.assertEqual(report["summary"]["zvec-grep"]["unscored"], 1)

    def test_no_final_answers_means_no_calibration_or_candidate_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = self.fixture(root, answers=False)
            calls = []
            report = self.run_review(root, output, lambda **kwargs: calls.append(kwargs))
            self.assertEqual(calls, [])
            self.assertTrue(all(c["status"] == "not_run_no_candidates" for c in report["calibration"].values()))
            self.assertEqual(report["planned_trials"], 2)
            self.assertTrue(all(r["status"] == "missing_trajectory" for r in report["trials"]))

    def test_failed_execution_cannot_gain_reportable_quality_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = self.fixture(root, execution_status="timeout")
            report = self.run_review(root, output, self.oracle())
            self.assertTrue(all(r["raw_consensus"] == "pass" for r in report["trials"]))
            self.assertTrue(all(r["quality"] == "execution_incomplete" for r in report["trials"]))
            self.assertFalse(report["quality_gate"]["all_planned_answers_pass"])

    def test_response_model_aliases_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = self.fixture(root)
            oracle = self.oracle()
            def complete(**kwargs):
                result = oracle(**kwargs)
                result["model"] = "OpenAI/" + result["model"].upper()
                return result
            report = self.run_review(root, output, complete)
            self.assertTrue(report["quality_gate"]["all_planned_answers_pass"])
            self.assertTrue(all(j["attempts"][0]["identity_verified"] for r in report["trials"] for j in r["judgments"].values()))

    def test_missing_or_wrong_response_model_is_unscored_without_retry(self):
        for wrong_model, expected in [(None, "identity_unverified"), ("glm-5.2", "identity_mismatch")]:
            with self.subTest(wrong_model=wrong_model), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); output = self.fixture(root)
                calls = []
                oracle = self.oracle()
                def complete(**kwargs):
                    calls.append(kwargs["model"])
                    result = oracle(**kwargs)
                    if kwargs["model"] == MODELS[1]:
                        result["model"] = wrong_model
                    return result
                report = self.run_review(root, output, complete)
                self.assertEqual(calls.count(MODELS[1]), 6)  # No identity-shopping retry.
                self.assertEqual(report["calibration"][MODELS[1]]["status"], "unavailable")
                self.assertFalse(report["quality_gate"]["all_planned_answers_pass"])
                for row in report["trials"]:
                    judgment = row["judgments"][MODELS[1]]
                    self.assertEqual(judgment["status"], expected)
                    self.assertEqual(judgment["quality"], "unscored")
                    self.assertEqual(len(judgment["attempts"]), 1)
                    self.assertFalse(judgment["attempts"][0]["identity_verified"])

    def test_existing_output_cannot_be_overwritten_to_revote(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = self.fixture(root)
            output.write_text("KEEP")
            with self.assertRaisesRegex(SweQaError, "new .json output"):
                self.run_review(root, output, self.oracle())
            self.assertEqual(output.read_text(), "KEEP")

    def test_consensus_fail_uncertain_and_criterion_disagreement(self):
        calibration = {model: {"status": "passed"} for model in MODELS}
        for value, expected in [(0, "fail"), ("?", "uncertain")]:
            judgments = {model: {"status": "judged", "quality": expected,
                                  "assessment": assessment({"factual_correctness": value})} for model in MODELS}
            self.assertEqual(consensus(judgments, calibration, "completed")["quality"], expected)
        judgments = {MODELS[0]: {"status": "judged", "quality": "fail", "assessment": assessment({"factual_correctness": 0})},
                     MODELS[1]: {"status": "judged", "quality": "fail", "assessment": assessment({"necessary_completeness": 0})}}
        result = consensus(judgments, calibration, "completed")
        self.assertEqual(result["quality"], "fail")
        self.assertTrue(result["flags"]["criterion_disagreement"])

    def test_optional_legacy_judge_model_preserves_default_and_supports_qwen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root)
            calls = []
            with patch.dict("os.environ", {"GLM_API_KEY": "private-test-key"}, clear=True):
                report = judge_runs(runs_dir=root, case_path=BASE / "cases/reflex-6.json", output=root / "qwen.json",
                                    expected_per_profile=1, judge_model=MODELS[1],
                                    completion_fn=lambda **kwargs: (calls.append(kwargs), response(kwargs["model"]))[1])
            self.assertFalse(report["self_judge"])
            self.assertEqual(report["judge_model"], MODELS[1])
            self.assertTrue(all(call["model"] == MODELS[1] for call in calls))


if __name__ == "__main__":
    unittest.main()
