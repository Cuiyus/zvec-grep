from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_fixtures import PROTOCOL, installation_stub, write_installation


def load(name):
    spec = importlib.util.spec_from_file_location("workspace_qa_" + name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


judge, report = load("judge"), load("report")


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = self.root / "task"
        self.task.mkdir()
        (self.task / "data").mkdir()
        (self.task / "data/a.py").write_text("# 原始代码\ndef add(a, b):\n    return a + b\n", encoding="utf-8")
        self.metadata = {"task": "请分析 add 并输出 answer.md", "rubrics": ["正确解释返回值", "不存在的原始要求也要保留"],
                         "rubric_types": ["结果评估", "结果评估"],
                         "data_manifest": [{"stored_relpath": "data/a.py", "filename": "a.py", "target_path": "src"}],
                         "agent_trace": "NEVER SEND TRACE", "profile": "NEVER SEND PROFILE"}
        self.metadata_path = self.task / "metadata.json"
        dump(self.metadata_path, self.metadata)
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.trial = {"trial_id": "128-baseline-1", "task_id": "128", "profile": "baseline", "repetition": 1,
                      "status": "completed", "answer": "相加", "candidate_output_path": "128-baseline-1/candidate/answer.md"}
        candidate = self.runs / self.trial["candidate_output_path"]
        candidate.parent.mkdir(parents=True)
        candidate.write_text(self.trial["answer"], encoding="utf-8")
        dump(self.runs / "trial-results.json", {"task_id": "128", "repetitions_per_profile": 1, "trials": [self.trial]})

    def response(self, scores=(True, False)):
        return {"id": "response-1", "model": "glm-5.2", "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"criteria": [
                    {"id": i, "score": value, "reason": "原文中找到依据"} for i, value in enumerate(scores)]})}}]}

    def run_judge(self, completion=None, **kwargs):
        with patch.dict("os.environ", {"GLM_API_KEY": "test-private-key"}):
            return judge.judge_runs(metadata_path=self.metadata_path, task_dir=self.task, runs_dir=self.runs,
                                    completion_fn=completion or (lambda **_: self.response()), sleep_fn=lambda _: None, **kwargs)

    def test_prompt_is_blind_and_preserves_every_original_rubric(self):
        evidence = judge.load_evidence(self.metadata_path, self.task)
        messages = judge.build_messages(evidence, "answer")
        payload = json.loads(messages[1]["content"])
        self.assertEqual([r["text"] for r in payload["rubrics"]], self.metadata["rubrics"])
        self.assertEqual([r["type"] for r in payload["rubrics"]], self.metadata["rubric_types"])
        self.assertEqual(payload["task"], self.metadata["task"])
        self.assertEqual(payload["source_files"][0]["text"], (self.task / "data/a.py").read_text())
        self.assertNotIn("NEVER SEND", json.dumps(messages))
        self.assertEqual(set(payload), {"task", "rubrics", "source_files", "candidate_answer", "candidate_outputs"})

    def test_official_docx_and_pptx_sources_are_extracted_without_skipping(self):
        docx = self.task / "data/report.docx"
        pptx = self.task / "data/slides.pptx"
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml",
                '<w:document xmlns:w="urn:w"><w:body><w:p><w:r><w:t>行政部 172 人</w:t></w:r></w:p></w:body></w:document>')
        with zipfile.ZipFile(pptx, "w") as archive:
            archive.writestr("ppt/slides/slide2.xml",
                '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>招聘周期 19 天</a:t></p:sld>')
            archive.writestr("ppt/slides/slide1.xml",
                '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>前台文员</a:t></p:sld>')
        self.metadata["data_manifest"] = [
            {"stored_relpath": "data/report.docx", "filename": "report.docx"},
            {"stored_relpath": "data/slides.pptx", "filename": "slides.pptx"},
        ]
        dump(self.metadata_path, self.metadata)
        evidence = judge.load_evidence(self.metadata_path, self.task)
        self.assertEqual([source["filename"] for source in evidence["sources"]], ["report.docx", "slides.pptx"])
        self.assertIn("行政部 172 人", evidence["sources"][0]["text"])
        self.assertLess(evidence["sources"][1]["text"].index("前台文员"),
                        evidence["sources"][1]["text"].index("招聘周期 19 天"))

    def test_full_boolean_mean_hashes_raw_model_latency_and_harness_output(self):
        captured = []
        result = self.run_judge(lambda **kwargs: captured.append(kwargs) or self.response())
        self.assertEqual(result["expected_trials"], 1)
        row = result["trials"][0]
        self.assertEqual(row["score"], 0.5)
        self.assertEqual(row["status"], "judged")
        self.assertEqual(result["rubrics"], self.metadata["rubrics"])
        self.assertEqual(len(row["prompt_sha256"]), 64)
        self.assertEqual(len(result["source_hashes"][0]["sha256"]), 64)
        self.assertGreaterEqual(row["judge_latency_seconds"], 0)
        self.assertEqual(row["attempts"][0]["raw_response"], self.response())
        self.assertEqual(json.loads(captured[0]["messages"][1]["content"])["candidate_outputs"][0]["filename"], "answer.md")
        self.assertEqual(captured[0]["model"], "glm-5.2")
        self.assertNotIn("test-private-key", (self.runs / "judgements.json").read_text())

    def test_sharded_ledger_is_complete_when_its_selected_pair_is_judged(self):
        second = dict(self.trial, trial_id="128-with-zg-1", profile="with-zg",
                      candidate_output_path="128-with-zg-1/candidate/answer.md")
        candidate = self.runs / second["candidate_output_path"]
        candidate.parent.mkdir(parents=True)
        candidate.write_text(second["answer"], encoding="utf-8")
        dump(self.runs / "trial-results.json", {"task_id": "128", "repetitions_per_profile": 5,
             "shard_repetitions": [1], "trials": [self.trial, second]})
        result = self.run_judge()
        self.assertEqual(result["repetitions_per_profile"], 5)
        self.assertEqual(result["expected_trials"], 2)
        self.assertEqual(sum(row["status"] == "judged" for row in result["trials"]), 2)

    def test_model_output_schema_rejects_omission_duplicate_ids_integer_scores_extra_keys(self):
        valid = [{"id": 0, "score": True, "reason": "yes"}, {"id": 1, "score": False, "reason": "no"}]
        bad = [valid[:1], [valid[0], valid[0]], [dict(valid[0], score=1), valid[1]],
               [dict(valid[0], id=True), valid[1]], [dict(valid[0], reason=""), valid[1]],
               [dict(valid[0], confidence=1), valid[1]]]
        for criteria in bad:
            with self.subTest(criteria=criteria), self.assertRaises(judge.JudgeError):
                judge.parse_assessment(json.dumps({"criteria": criteria}), 2)
        self.assertEqual(judge.parse_assessment(json.dumps({"criteria": valid[::-1]}), 2), valid)

    def test_invalid_assessment_retries_are_bounded_and_never_zero_scored(self):
        calls = []
        result = self.run_judge(lambda **_: calls.append(1) or self.response((True,)), attempts=3)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["trials"][0]["status"], "judge_error")
        self.assertIsNone(result["trials"][0]["score"])
        self.assertEqual(len(result["trials"][0]["attempts"]), 3)

    def test_truncated_repeated_response_retries_same_request_and_retains_raw_attempt(self):
        raw = self.response()
        raw["choices"][0].update(finish_reason="length", message={"content": '{"criteria":[{"id":0,"reason":"' * 20})
        captured = []
        def completion(**kwargs):
            captured.append(kwargs)
            return raw if len(captured) == 1 else self.response((False, False))
        result = self.run_judge(completion)
        row = result["trials"][0]
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0], captured[1])
        self.assertEqual(row["score"], 0)
        self.assertEqual(row["attempts"][0]["raw_response"], raw)
        self.assertEqual(row["attempts"][0]["finish_reason"], "length")
        self.assertEqual(row["attempts"][0]["status"], "invalid_assessment")
        self.assertEqual(row["attempts"][1]["status"], "judged")
        self.assertEqual(row["attempts"][0]["prompt_sha256"], row["attempts"][1]["prompt_sha256"])

    def test_valid_zero_score_is_never_retried(self):
        calls = []
        result = self.run_judge(lambda **_: calls.append(1) or self.response((False, False)))
        self.assertEqual(result["trials"][0]["score"], 0)
        self.assertEqual(len(calls), 1)

    def test_duplicate_json_keys_are_invalid_even_if_json_decoder_would_accept(self):
        raw = '{"criteria":[{"id":0,"score":false,"score":true,"reason":"duplicate"}]}'
        with self.assertRaisesRegex(judge.InvalidAssessmentError, "duplicate JSON keys"):
            judge.parse_assessment(raw, 1)

    def test_content_filter_and_different_model_do_not_retry(self):
        for kind in ("content_filter", "different_model"):
            response = self.response()
            if kind == "content_filter":
                response["choices"][0]["finish_reason"] = "content_filter"
            else:
                response["model"] = "another-model"
            calls = []
            result = self.run_judge(lambda **_: calls.append(1) or response)
            self.assertEqual(len(calls), 1)
            self.assertIsNone(result["trials"][0]["score"])

    def test_resume_skips_existing_valid_low_score_without_altering_raw_attempt(self):
        original = self.run_judge(lambda **_: self.response((False, False)))
        calls = []
        resumed = self.run_judge(lambda **_: calls.append(1) or self.response(), resume=True)
        self.assertEqual(calls, [])
        self.assertEqual(original["trials"], resumed["trials"])

    def test_resume_preserves_failed_attempt_and_uses_remaining_total_budget(self):
        invalid = self.response()
        invalid["choices"][0]["finish_reason"] = "length"
        original = self.run_judge(lambda **_: invalid)
        # An interrupted old run had saved one failed attempt, as in the real smoke.
        row = original["trials"][0]
        row["attempts"] = row["attempts"][:1]
        row["judge_latency_seconds"] = row["attempts"][0]["latency_seconds"]
        # Exercise backward compatibility with the pre-retry-policy artifact.
        original.pop("retry_policy")
        dump(self.runs / "judgements.json", original)
        prior_attempt = json.loads(json.dumps(row["attempts"][0]))
        calls = []
        resumed = self.run_judge(lambda **_: calls.append(1) or self.response(), resume=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(resumed["trials"][0]["attempts"][0], prior_attempt)
        self.assertEqual(resumed["trials"][0]["attempts"][1]["attempt"], 2)
        self.assertEqual(resumed["trials"][0]["status"], "judged")

    def test_resume_does_not_reset_exhausted_budget(self):
        original = self.run_judge(lambda **_: self.response((True,)))
        calls = []
        resumed = self.run_judge(lambda **_: calls.append(1) or self.response(), resume=True)
        self.assertEqual(calls, [])
        self.assertEqual(resumed["trials"], original["trials"])
        self.assertIsNone(resumed["trials"][0]["score"])

    def test_resume_identity_mismatch_fails_before_call_or_artifact_change(self):
        original = self.run_judge()
        for key, value in (("judge_model", "different-model"), ("answer_sha256", "bad-answer"),
                           ("prompt_sha256", "bad-prompt"), ("source_hashes", []),
                           ("trial_results_sha256", "bad-ledger"), ("metadata_sha256", "bad-metadata")):
            modified = json.loads(json.dumps(original))
            (modified["trials"][0] if key in ("answer_sha256", "prompt_sha256") else modified)[key] = value
            path = self.runs / "judgements.json"
            dump(path, modified)
            before = path.read_bytes()
            calls = []
            with self.subTest(key=key), self.assertRaises(judge.JudgeError):
                self.run_judge(lambda **_: calls.append(1) or self.response(), resume=True)
            self.assertEqual(calls, [])
            self.assertEqual(path.read_bytes(), before)

    def test_resume_cannot_increase_original_attempt_budget(self):
        self.run_judge()
        before = (self.runs / "judgements.json").read_bytes()
        with self.assertRaisesRegex(judge.JudgeError, "attempt budget"):
            self.run_judge(resume=True, attempts=4)
        self.assertEqual((self.runs / "judgements.json").read_bytes(), before)

    def test_resume_rejects_limit_and_attempt_parameter_drift_before_writing(self):
        original = self.run_judge()
        for location, key, value in (("limits", "max_completion_tokens", 4096),
                                     ("limits", "max_source_bytes", 512001),
                                     ("limits", "max_prompt_bytes", 750001),
                                     ("attempt", "temperature", 0.5),
                                     ("attempt", "max_completion_tokens", 4096)):
            modified = json.loads(json.dumps(original))
            target = modified["limits"] if location == "limits" else modified["trials"][0]["attempts"][0]
            target[key] = value
            path = self.runs / "judgements.json"
            dump(path, modified)
            before = path.read_bytes()
            calls = []
            with self.subTest(location=location, key=key), self.assertRaises(judge.JudgeError):
                self.run_judge(lambda **_: calls.append(1) or self.response(), resume=True)
            self.assertEqual(calls, [])
            self.assertEqual(path.read_bytes(), before)

    def test_resume_authentication_failure_and_content_filter_are_not_retryable(self):
        for cause in ("unauthorized", "content_filter", "different_model"):
            response = self.response()
            if cause == "content_filter":
                response["choices"][0]["finish_reason"] = "content_filter"
            if cause == "different_model":
                response["model"] = "another-model"
            def failure(**_):
                if cause == "unauthorized":
                    raise HTTPError("https://example.invalid", 401, "unauthorized", {}, None)
                return response
            original = self.run_judge(failure)
            calls = []
            resumed = self.run_judge(lambda **_: calls.append(1) or self.response(), resume=True)
            self.assertEqual(calls, [])
            self.assertEqual(original["trials"], resumed["trials"])

    def test_bounded_retry_only_for_operational_error(self):
        calls = []
        def completion(**_):
            calls.append(1)
            if len(calls) == 1:
                raise HTTPError("https://example.invalid", 429, "limited", {}, None)
            return self.response()
        result = self.run_judge(completion)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["trials"][0]["status"], "judged")
        calls.clear()
        def unauthorized(**_):
            calls.append(1)
            raise HTTPError("https://example.invalid", 401, "unauthorized", {}, None)
        result = self.run_judge(unauthorized)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(result["trials"][0]["score"])

    def test_no_silent_source_or_prompt_truncation(self):
        with self.assertRaisesRegex(judge.JudgeError, "no truncation"):
            judge.load_evidence(self.metadata_path, self.task, max_source_bytes=1)
        evidence = judge.load_evidence(self.metadata_path, self.task)
        with self.assertRaisesRegex(judge.JudgeError, "no truncation"):
            judge.build_messages(evidence, "answer", max_prompt_bytes=1)
        result = self.run_judge(max_source_bytes=1)
        self.assertEqual(result["trials"][0]["status"], "evidence_error")
        self.assertIsNone(result["trials"][0]["score"])

    def test_source_escape_hash_mismatch_binary_and_missing_are_errors(self):
        for relative in ("../outside.py", "/tmp/outside.py", "data/missing.py"):
            self.metadata["data_manifest"][0]["stored_relpath"] = relative
            dump(self.metadata_path, self.metadata)
            with self.subTest(relative=relative), self.assertRaises((judge.JudgeError, OSError)):
                judge.load_evidence(self.metadata_path, self.task)
        self.metadata["data_manifest"][0].update(stored_relpath="data/a.py", sha256="bad")
        dump(self.metadata_path, self.metadata)
        with self.assertRaisesRegex(judge.JudgeError, "SHA-256"):
            judge.load_evidence(self.metadata_path, self.task)

    def test_missing_answer_or_failed_execution_never_calls_judge(self):
        for status, answer, expected in (("failed", "partial", "execution_not_completed"), ("completed", "", "missing_answer")):
            self.trial.update(status=status, answer=answer)
            dump(self.runs / "trial-results.json", {"task_id": "128", "repetitions_per_profile": 1, "trials": [self.trial]})
            def never(**_):
                self.fail("unscorable trial reached judge")
            result = self.run_judge(never)
            self.assertEqual(result["trials"][0]["status"], expected)
            self.assertIsNone(result["trials"][0]["score"])

    def test_changed_materialized_output_is_not_judged(self):
        (self.runs / self.trial["candidate_output_path"]).write_text("changed")
        result = self.run_judge()
        self.assertEqual(result["trials"][0]["status"], "invalid_judge_input")
        self.assertIsNone(result["trials"][0]["score"])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        proof = patch("qoder_probe.installation_evidence", side_effect=installation_stub)
        proof.start()
        self.addCleanup(proof.stop)
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.manifest = self.root / "selected.json"
        dump(self.manifest, {"experiment": {"protocol": PROTOCOL}, "tasks": [{"task_id": "128", "slice": "code_qa"}, {"task_id": "76", "slice": "document_qa"}], "repetitions": 2})

    def write_task(self, task_id, values=(100, 80), repetitions=2, missing=(), score=(True, False)):
        trials, judgments = [], []
        for profile, tokens, passed in zip(report.PROFILES, values, score):
            for repetition in range(1, repetitions + 1):
                if (profile, repetition) in missing:
                    continue
                trial_id = f"{task_id}-{profile}-{repetition}"
                installation = write_installation(self.runs / task_id / trial_id / "agent", profile)
                trials.append({"protocol": PROTOCOL, "source_unchanged": True, "installation": installation, "trial_id": trial_id, "task_id": task_id, "profile": profile, "repetition": repetition,
                    "status": "completed", "answer": "answer", "input_tokens": tokens,
                    "output_tokens": 20, "tool_calls": tokens / 10, "wall_seconds": tokens / 20, "zg_tool_calls": 0})
                judgments.append({"trial_id": trial_id, "task_id": task_id, "profile": profile, "repetition": repetition,
                    "status": "judged", "score": float(passed), "criteria": [{"id": 0, "score": passed, "reason": "evidence"}],
                    "answer_sha256": hashlib.sha256(b"answer").hexdigest(), "judge_latency_seconds": 1})
        directory = self.runs / task_id
        dump(directory / "manifest.json", {"protocol": PROTOCOL, "task_id": task_id})
        dump(directory / "trial-results.json", {"protocol": PROTOCOL, "task_id": task_id, "repetitions_per_profile": repetitions, "trials": trials})
        dump(directory / "judgements.json", {"task_id": task_id, "judge_model": "glm-5.2", "rubrics": ["original"], "trials": judgments})

    def test_rejects_old_or_mixed_protocol_even_when_all_answers_are_scored(self):
        self.write_task("128")
        self.write_task("76")
        path = self.runs / "76/trial-results.json"
        ledger = report.read_object(path)
        ledger["protocol"] = "workspace-qa-qoder-v1"
        dump(path, ledger)
        with self.assertRaisesRegex(report.ReportError, "old or mixed"):
            report.load_rows(self.runs, manifest_path=self.manifest)

    def test_rejects_old_selection_and_runtime_manifest(self):
        self.write_task("128")
        old = report.read_object(self.manifest)
        old["experiment"]["protocol"] = "old-bridge"
        dump(self.manifest, old)
        with self.assertRaisesRegex(report.ReportError, "manifest requires"):
            report.load_rows(self.runs, manifest_path=self.manifest)
        dump(self.runs / "128/manifest.json", {"protocol": "old-bridge", "task_id": "128"})
        with self.assertRaisesRegex(report.ReportError, "runtime manifest protocol"):
            report.load_rows(self.runs)

    def test_completed_trial_requires_original_installation_and_source_evidence(self):
        for mutation in ("missing", "hash", "source", "manifest"):
            with self.subTest(mutation=mutation):
                self.write_task("128")
                path = self.runs / "128/trial-results.json"
                ledger = report.read_object(path)
                trial = ledger["trials"][0]
                if mutation == "missing":
                    (path.parent / trial["trial_id"] / "agent/install-manifest.json").unlink()
                elif mutation == "hash":
                    trial["installation"]["manifest_sha256"] = "stale"
                elif mutation == "source":
                    trial["source_unchanged"] = False
                else:
                    (path.parent / "manifest.json").unlink()
                dump(path, ledger)
                with self.assertRaises(report.ReportError):
                    report.load_rows(self.runs)

    def test_failed_installation_keeps_null_observation_and_planned_denominator(self):
        self.write_task("128")
        path = self.runs / "128/trial-results.json"
        ledger = report.read_object(path)
        trial = ledger["trials"][0]
        trial.update(status="launch_failure", installation=None, source_unchanged=None, input_tokens=None)
        (path.parent / trial["trial_id"] / "agent/install-manifest.json").unlink()
        dump(path, ledger)
        judgments = report.read_object(path.parent / "judgements.json")
        judgments["trials"][0].update(status="execution_not_completed", score=None, criteria=[])
        dump(path.parent / "judgements.json", judgments)
        rows, plan = report.load_rows(self.runs, manifest_path=self.manifest)
        self.assertEqual(plan["expected_trials"], 8)
        self.assertEqual(rows[0]["execution_status"], "launch_failure")
        self.assertIsNone(rows[0]["input_tokens"])
        self.assertFalse(report.summarize(rows)["complete"])

    def test_entire_missing_task_and_missing_trial_keep_expected_denominator(self):
        self.write_task("128", missing=(("with-zg", 2),))
        rows, plan = report.load_rows(self.runs, manifest_path=self.manifest)
        self.assertEqual(plan["expected_trials"], 8)
        self.assertEqual(len(rows), 8)
        self.assertEqual(sum(r["execution_status"] == "missing_trial" for r in rows), 5)
        summary = report.summarize(rows)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["paired_metrics"]["input_tokens"]["matched_pairs"], 1)
        self.assertEqual(summary["paired_metrics"]["input_tokens"]["expected_pairs"], 4)
        self.assertEqual(summary["profiles"]["with-zg"]["judged"], 1)

    def test_task_equal_weighted_means_and_pairing_do_not_weight_by_available_repetitions(self):
        self.write_task("128", values=(100, 50))
        self.write_task("76", values=(1000, 800), missing=(("baseline", 2),))
        rows, _ = report.load_rows(self.runs, manifest_path=self.manifest)
        summary = report.summarize(rows)
        metric = summary["paired_metrics"]["input_tokens"]
        self.assertEqual(metric["baseline_mean"], 550)
        self.assertEqual(metric["with_zg_mean"], 425)
        self.assertAlmostEqual(metric["percent_savings"], 125 / 550 * 100)
        self.assertEqual(metric["matched_pairs"], 3)
        self.assertEqual(summary["paired_metrics"]["rubric_score"]["quality_delta_percentage_points"], -100)
        self.assertIsNone(summary["paired_metrics"]["rubric_score"]["percent_savings"])

    def test_complete_report_writes_reviewable_json_csv_markdown_and_separate_slices(self):
        self.write_task("128")
        self.write_task("76", values=(1000, 900), score=(True, True))
        result = report.write_report(runs_dir=self.runs, output=self.root / "report", manifest_path=self.manifest)
        self.assertTrue(result["efficacy_claim_ready"])
        self.assertEqual(result["summary"]["profiles"]["baseline"]["metrics"]["input_tokens"]["mean"], 550)
        self.assertEqual(set(result["by_slice"]), {"code_qa", "document_qa"})
        self.assertEqual({p.name for p in (self.root / "report").iterdir()}, {"summary.json", "summary.md", "rows.json", "rows.csv", "rows.md"})
        self.assertEqual(set(result["by_qa_group"]), {"code_qa", "other_readonly_qa"})
        self.assertIn("original rubrics retained in full", (self.root / "report/summary.md").read_text())
        self.assertEqual(result["summary"]["profiles"]["baseline"]["metrics"]["cached_input_tokens"]["observations"], 0)

    def test_no_manifest_cannot_establish_complete_benchmark_claim(self):
        self.write_task("128")
        result = report.write_report(runs_dir=self.runs, output=self.root / "report")
        self.assertTrue(result["summary"]["complete"])
        self.assertFalse(result["efficacy_claim_ready"])

    def test_zero_baseline_has_no_percentage_savings(self):
        self.write_task("128", values=(0, 10))
        rows, _ = report.load_rows(self.runs)
        self.assertIsNone(report.summarize(rows)["paired_metrics"]["input_tokens"]["percent_savings"])

    def test_stale_answer_hash_and_missing_criterion_are_rejected(self):
        self.write_task("128")
        path = self.runs / "128/judgements.json"
        judgments = report.read_object(path)
        judgments["trials"][0]["answer_sha256"] = "stale"
        dump(path, judgments)
        with self.assertRaisesRegex(report.ReportError, "answer hash"):
            report.load_rows(self.runs)
        judgments["trials"][0]["answer_sha256"] = hashlib.sha256(b"answer").hexdigest()
        judgments["trials"][0]["criteria"] = []
        dump(path, judgments)
        with self.assertRaisesRegex(report.ReportError, "every original rubric"):
            report.load_rows(self.runs)

    def test_duplicate_task_artifact_and_repetition_mismatch_are_rejected(self):
        self.write_task("128")
        ledger = report.read_object(self.runs / "128/trial-results.json")
        dump(self.runs / "duplicate/trial-results.json", ledger)
        with self.assertRaisesRegex(report.ReportError, "duplicate task ledger"):
            report.load_rows(self.runs)
        (self.runs / "duplicate/trial-results.json").unlink()
        with self.assertRaisesRegex(report.ReportError, "repetitions"):
            report.load_rows(self.runs, repetitions=10)

    def test_disjoint_pair_shards_merge_into_one_complete_task(self):
        self.write_task("128")
        source = self.runs / "128"
        ledger = report.read_object(source / "trial-results.json")
        judgments = report.read_object(source / "judgements.json")
        for repetition in (1, 2):
            target = self.runs / f"128-r{repetition:02d}"
            target.mkdir()
            shutil.copy2(source / "manifest.json", target / "manifest.json")
            selected = [row for row in ledger["trials"] if row["repetition"] == repetition]
            for row in selected:
                shutil.copytree(source / row["trial_id"], target / row["trial_id"])
            dump(target / "trial-results.json", {"protocol": PROTOCOL, "task_id": "128",
                 "repetitions_per_profile": 2, "shard_repetitions": [repetition], "trials": selected})
            dump(target / "judgements.json", {**judgments,
                 "trials": [row for row in judgments["trials"] if row["repetition"] == repetition]})
        shutil.rmtree(source)
        rows, plan = report.load_rows(self.runs, manifest_path=self.manifest)
        self.assertEqual(len(rows), 8)
        self.assertEqual(plan["ledger_files"], 2)
        task_rows = [row for row in rows if row["task_id"] == "128"]
        self.assertTrue(all(row["execution_status"] == "completed" for row in task_rows))
        duplicate = self.runs / "128-r03"
        shutil.copytree(self.runs / "128-r01", duplicate)
        with self.assertRaisesRegex(report.ReportError, "overlapping task shard"):
            report.load_rows(self.runs, manifest_path=self.manifest)

    def test_execution_complete_preserves_failed_outcome_and_does_not_claim_full_quality(self):
        self.write_task("128")
        self.write_task("76")
        path = self.runs / "128/trial-results.json"
        ledger = report.read_object(path)
        failed = ledger["trials"][0]
        failed.update(status="contract_failure", input_tokens=None, error="Original provider timeout")
        dump(path, ledger)
        old_bytes = path.read_bytes()
        judgments = report.read_object(path.parent / "judgements.json")
        judgments["trials"][0].update(status="execution_not_completed", score=None, criteria=[])
        dump(path.parent / "judgements.json", judgments)
        result = report.write_report(runs_dir=self.runs, manifest_path=self.manifest, output=self.root / "report")
        execution = result["summary"]["execution"]
        self.assertEqual((execution["planned"], execution["attempted"], execution["terminal_recorded"],
                          execution["qa_completed"], execution["qa_judged"]), (8, 8, 8, 7, 7))
        self.assertTrue(result["execution_complete"])
        self.assertFalse(result["efficacy_claim_ready"])
        self.assertFalse(result["summary"]["complete"])
        args = ["--runs-dir", str(self.runs), "--manifest", str(self.manifest), "--output", str(self.root / "report")]
        self.assertEqual(report.main([*args, "--require-executed"]), 0)
        self.assertEqual(report.main([*args, "--require-complete"]), 1)
        self.assertEqual(path.read_bytes(), old_bytes)
        rows = json.loads((self.root / "report/rows.json").read_text())
        self.assertEqual(rows[0]["execution_status"], "contract_failure")
        self.assertEqual(rows[0]["error"], "Original provider timeout")
        self.assertIsNone(rows[0]["input_tokens"])
        self.assertIn("does not mean every answer succeeded", (self.root / "report/summary.md").read_text())

    def test_require_executed_rejects_unstarted_unknown_and_unjudged_completed_answers(self):
        self.write_task("128")
        args = ["--runs-dir", str(self.runs), "--manifest", str(self.manifest), "--output", str(self.root / "report")]
        self.assertEqual(report.main([*args, "--require-executed"]), 1)
        summary = json.loads((self.root / "report/summary.json").read_text())["summary"]["execution"]
        self.assertEqual(summary["attempt_status_unknown"], 4)
        self.write_task("76")
        path = self.runs / "76/trial-results.json"
        ledger = report.read_object(path)
        original = ledger["trials"][0].copy()
        ledger["trials"][0].update(status="planned", answer=None, input_tokens=None, tool_calls=None, wall_seconds=None)
        dump(path, ledger)
        judgments = report.read_object(path.parent / "judgements.json")
        judgments["trials"][0].update(status="execution_not_completed", score=None, criteria=[])
        dump(path.parent / "judgements.json", judgments)
        self.assertEqual(report.main([*args, "--require-executed"]), 1)
        ledger["trials"][0] = original
        dump(path, ledger)
        judgments["trials"][0]["status"] = "judge_error"
        dump(path.parent / "judgements.json", judgments)
        self.assertEqual(report.main([*args, "--require-executed"]), 1)
        summary = json.loads((self.root / "report/summary.json").read_text())["summary"]["execution"]
        self.assertTrue(summary["all_trials_attempted"])
        self.assertFalse(summary["all_successful_answers_judged"])

    def test_failed_judge_is_unknown_instead_of_zero_and_raw_execution_metrics_are_retained(self):
        self.write_task("128")
        path = self.runs / "128/judgements.json"
        judgments = report.read_object(path)
        judgments["trials"][0].update(status="judge_error", score=None, criteria=[])
        dump(path, judgments)
        rows, _ = report.load_rows(self.runs)
        self.assertIsNone(rows[0]["rubric_score"])
        self.assertEqual(rows[0]["judge_status"], "judge_error")
        self.assertEqual(rows[0]["observed_metrics"]["input_tokens"], 100)
        self.assertFalse(report.summarize(rows)["complete"])


if __name__ == "__main__":
    unittest.main()
