"""Continue scoring only new QA, retaining all original verdicts and evidence."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import continuation
import judge
import report
import runner
from native_fixtures import dump, installation_stub, write_installation


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class JudgeContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prior = self.root / "prior/runs"
        self.runs = self.root / "current/runs"
        self.task = self.root / "task"
        (self.task / "data").mkdir(parents=True)
        (self.task / "data/source.md").write_text("Original source evidence")
        self.metadata = self.task / "metadata.json"
        dump(self.metadata, {"task": "Explain the source in answer.md", "rubrics": ["原始要求"],
            "rubric_types": ["结果评估"], "data_manifest": [{"stored_relpath": "data/source.md", "filename": "source.md"}]})
        plan = runner.make_plan("3", 10)
        self.rows = copy.deepcopy(plan["trials"])
        old_manifest = {field: "fixture" for field in continuation.RUNTIME_FIELDS}
        old_manifest.update(schema_version=2, protocol=continuation.PROTOCOL, integration_method="zg_install",
            model=runner.MODEL, agent_spec=runner.SPEC.to_dict(),
            install_command=continuation.INSTALL_COMMAND, task_id="3", gold_visible_to_agent=False,
            corpus_readonly_mount=True, source_files={"source.md": digest(self.task / "data/source.md")},
            source_git_commit="source-old", question_sha256=hashlib.sha256(b"question").hexdigest(),
            image_id="image-old", ci_identity={"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "1"})
        self.old_manifest = old_manifest
        for index, row in enumerate(self.rows):
            if index < 3:
                self.complete(self.prior, row, "old " + row["trial_id"])
            elif index == 3:
                row.update(status="contract_failure", answer=None, input_tokens=None, tool_calls=5,
                    wall_seconds=42, error="original provider timeout", provenance=self.provenance())
                dump(self.prior / row["trial_id"] / "result.json", row)
        self.old_ledger = {"schema_version": 1, "protocol": continuation.PROTOCOL, "task_id": "3",
                           "repetitions_per_profile": 10, "trials": self.rows}
        dump(self.prior / "trial-results.json", self.old_ledger)
        dump(self.prior / "manifest.json", old_manifest)
        env = patch.dict("os.environ", {"GLM_API_KEY": "offline-only-fixture"})
        env.start()
        self.addCleanup(env.stop)
        old_calls = []
        self.old_judgements = judge.judge_runs(metadata_path=self.metadata, task_dir=self.task, runs_dir=self.prior,
            completion_fn=lambda **args: old_calls.append(args) or self.response(False), sleep_fn=lambda _: None)
        self.assertEqual(len(old_calls), 3)
        shutil.copytree(self.prior, self.runs)
        self.old_ids = [row["trial_id"] for row in self.rows[:4]]
        self.new_ids = [row["trial_id"] for row in self.rows[4:]]
        provenance = {"schema_version": 1, "no_resampling": True, "source_run_id": "1", "source_commit": "a" * 40,
            "preserved_trial_ids": self.old_ids, "pending_trial_ids": self.new_ids,
            "preserved_files_sha256": {f"{trial_id}/{name}": value for trial_id in self.old_ids
                for name, value in continuation.file_hashes(self.runs / trial_id).items()}}
        for name, relative in continuation.PRIOR_PATHS.items():
            filename = {"ledger": "trial-results.json", "manifest": "manifest.json", "judgements": "judgements.json"}[name]
            path = self.runs / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.prior / filename, path)
            provenance[f"prior_{name}_path"] = relative
            provenance[f"prior_{name}_sha256"] = digest(path)
        review = {"status": "verified", "base_commit": "a" * 40, "head_commit": "b" * 40}
        provenance["compatibility"] = {"status": "verified", "code_review": review,
            "source_files_unchanged": True, "original_instructions_unchanged": True, "runtime_parameters_unchanged": True,
            "source_image_id": "image-old", "current_image_id": "image-new",
            "source_git_commit": "source-old", "current_source_git_commit": "source-new"}
        current_manifest = {**old_manifest, "source_git_commit": "source-new", "image_id": "image-new",
            "ci_identity": {"GITHUB_SHA": "b" * 40, "GITHUB_RUN_ID": "2"},
            "continuation_code_review": review, "continuation": provenance}
        dump(self.runs / "manifest.json", current_manifest)
        new_rows = copy.deepcopy(self.rows)
        for row in new_rows[4:]:
            self.complete(self.runs, row, "new " + row["trial_id"])
        dump(self.runs / "trial-results.json", {**self.old_ledger, "trials": new_rows})
        self.prior_bytes = {name: (self.runs / relative).read_bytes() for name, relative in continuation.PRIOR_PATHS.items()}

    def provenance(self):
        return {"manifest_path": "manifest.json", "source_git_commit": "source-old",
                "question_sha256": self.old_manifest["question_sha256"], "image_id": "image-old"}

    def complete(self, directory, row, answer):
        candidate = f"{row['trial_id']}/candidate/answer.md"
        row.update(status="completed", answer=answer, candidate_output_path=candidate,
            input_tokens=100, tool_calls=2, wall_seconds=3, source_unchanged=True,
            provenance=self.provenance(), installation=write_installation(directory / row["trial_id"] / "agent", row["profile"]))
        path = directory / candidate
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(answer)
        dump(directory / row["trial_id"] / "result.json", row)

    def response(self, score=True):
        return {"model": "glm-5.2", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "criteria": [{"id": 0, "score": score, "reason": "preserved fixture reasoning"}]})}}]}

    def invoke(self, completion=None, **kwargs):
        return judge.judge_runs(metadata_path=self.metadata, task_dir=self.task, runs_dir=self.runs,
            continue_from_ledger=self.runs / continuation.PRIOR_PATHS["ledger"],
            continue_from_judgements=self.runs / continuation.PRIOR_PATHS["judgements"],
            completion_fn=completion or (lambda **_: self.response()), sleep_fn=lambda _: None, **kwargs)

    def test_only_sixteen_new_answers_are_judged_and_original_rows_and_bytes_are_unchanged(self):
        calls = []
        result = self.invoke(lambda **args: calls.append(args) or self.response())
        self.assertEqual(len(calls), 16)
        old = {row["trial_id"]: row for row in self.old_judgements["trials"]}
        current = {row["trial_id"]: row for row in result["trials"]}
        for trial_id in self.old_ids:
            self.assertEqual(current[trial_id], old[trial_id])
        self.assertEqual(sum(row["status"] == "judged" for row in result["trials"]), 19)
        self.assertEqual(current[self.old_ids[0]]["score"], 0)
        self.assertEqual(current[self.old_ids[3]]["status"], "execution_not_completed")
        self.assertEqual(result["trial_results_sha256"], digest(self.runs / "trial-results.json"))
        self.assertEqual(len(result["judgement_continuation"]["imported_judged_trial_ids"]), 3)
        for name, relative in continuation.PRIOR_PATHS.items():
            self.assertEqual((self.runs / relative).read_bytes(), self.prior_bytes[name])
        for call in calls:
            self.assertTrue(json.loads(call["messages"][1]["content"])["candidate_answer"].startswith("new "))

    def test_changing_any_attempted_trial_including_failure_is_rejected_before_call_or_write(self):
        path = self.runs / "trial-results.json"
        value = json.loads(path.read_text())
        value["trials"][3]["status"] = "completed"
        dump(path, value)
        before = (self.runs / "judgements.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "previously attempted"), \
                patch.object(judge, "http_completion", side_effect=AssertionError("No model call")):
            self.invoke(lambda **_: self.fail("No model call"))
        self.assertEqual((self.runs / "judgements.json").read_bytes(), before)

    def test_model_source_and_parameter_drift_fail_closed(self):
        before = (self.runs / "judgements.json").read_bytes()
        for options in ({"model": "another-model"}, {"attempts": 2}, {"max_prompt_bytes": 1000000}):
            with self.subTest(options=options), self.assertRaises(judge.JudgeError):
                self.invoke(lambda **_: self.fail("No model call"), **options)
        (self.task / "data/source.md").write_text("changed source")
        with self.assertRaises(judge.JudgeError):
            self.invoke(lambda **_: self.fail("No model call"))
        self.assertEqual((self.runs / "judgements.json").read_bytes(), before)

    def test_resume_still_requires_exact_current_ledger_and_does_not_rejudge_imported_scores(self):
        self.invoke()
        resumed = judge.judge_runs(metadata_path=self.metadata, task_dir=self.task, runs_dir=self.runs,
            resume=True, completion_fn=lambda **_: self.fail("No model call"))
        self.assertEqual(judge.validate_judgement_continuation(resumed, self.runs), set(self.old_ids))
        ledger = json.loads((self.runs / "trial-results.json").read_text())
        ledger["extra"] = "changed"
        dump(self.runs / "trial-results.json", ledger)
        with self.assertRaisesRegex(judge.JudgeError, "trial_results_sha256"):
            judge.judge_runs(metadata_path=self.metadata, task_dir=self.task, runs_dir=self.runs,
                resume=True, completion_fn=lambda **_: self.fail("No model call"))

    def test_reimport_never_overwrites_new_judging_progress_or_original_evidence(self):
        self.invoke()
        before = (self.runs / "judgements.json").read_bytes()
        with self.assertRaisesRegex(judge.JudgeError, "already has progress"):
            self.invoke(lambda **_: self.fail("No repeated judge call"))
        self.assertEqual((self.runs / "judgements.json").read_bytes(), before)
        with self.assertRaisesRegex(judge.JudgeError, "cannot overwrite original"):
            self.invoke(output=self.runs / continuation.PRIOR_PATHS["judgements"])
        self.assertEqual((self.runs / continuation.PRIOR_PATHS["judgements"]).read_bytes(), self.prior_bytes["judgements"])

    def test_import_and_resume_flags_are_mutually_exclusive(self):
        with self.assertRaisesRegex(judge.JudgeError, "mutually exclusive"):
            self.invoke(resume=True)
        with self.assertRaisesRegex(judge.JudgeError, "both"):
            judge.judge_runs(metadata_path=self.metadata, task_dir=self.task, runs_dir=self.runs,
                            continue_from_ledger=self.runs / continuation.PRIOR_PATHS["ledger"])

    def test_report_verifies_original_manifest_mapping_and_rejects_changed_imported_score(self):
        result = self.invoke()
        selection = self.root / "selection.json"
        dump(selection, {"experiment": {"protocol": continuation.PROTOCOL}, "tasks": [{"task_id": "3", "slice": "code_qa"}], "repetitions": 10})
        with patch("qoder_probe.installation_evidence", side_effect=installation_stub):
            rows, plan = report.load_rows(self.runs, manifest_path=selection)
        preserved = [row for row in rows if row["preserved_from_original"]]
        self.assertEqual(len(preserved), 4)
        self.assertTrue(all(row["effective_manifest_path"] == continuation.PRIOR_PATHS["manifest"] for row in preserved))
        self.assertTrue(all(row["provenance"]["manifest_path"] == "manifest.json" for row in preserved))
        self.assertTrue(report.summarize(rows)["execution"]["execution_complete"])
        self.assertFalse(report.summarize(rows)["complete"])
        changed = next(row for row in result["trials"] if row["trial_id"] == self.old_ids[0])
        changed["score"] = 1
        changed["criteria"][0]["score"] = True
        dump(self.runs / "judgements.json", result)
        with patch("qoder_probe.installation_evidence", side_effect=installation_stub), self.assertRaisesRegex(report.ReportError, "imported judgements"):
            report.load_rows(self.runs, manifest_path=selection)


if __name__ == "__main__":
    unittest.main()
