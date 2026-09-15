from __future__ import annotations
import json
from contextlib import redirect_stdout
import io
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_task as module
from native_fixtures import PROTOCOL, dump, installation_stub, write_installation, write_native, write_probe


class SmokeGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.proof = patch("qoder_probe.installation_evidence", side_effect=installation_stub)
        self.proof.start()
        self.addCleanup(self.proof.stop)

    def fixture(self, statuses, measured=None):
        trial_id = "3-r01-with-zg"
        agent = self.runs / trial_id / "agent"
        installation = write_installation(agent)
        write_native(agent, statuses)
        write_native(self.runs / "3-r01-baseline/agent", ())
        dump(self.runs / "trial-results.json", {"protocol": PROTOCOL, "task_id": "3", "repetitions_per_profile": 1, "trials": [
            {"trial_id": "3-r01-baseline", "task_id": "3", "profile": "baseline", "repetition": 1,
             "status": "completed", "input_tokens": 374316, "tool_calls": 43, "wall_seconds": 409.309},
            {"trial_id": trial_id, "task_id": "3", "profile": "with-zg", "repetition": 1,
             "status": "completed", "zg_tool_calls_successful": sum(value is True for value in statuses) if measured is None else measured,
             "zg_tool_calls": len(statuses), "source_unchanged": True, "installation": installation,
             "working_index_semantic_unchanged": False,
             "input_tokens": 246933, "tool_calls": 31, "wall_seconds": 318.544}]})
        return agent

    def probe_fixture(self, **kwargs):
        path = self.root / "sdk-preflight/qoder"
        write_probe(path, **kwargs)
        return path

    def test_fresh_native_probe_and_natural_non_use_preserve_zero_qa_calls(self):
        self.fixture([])
        self.probe_fixture()
        before = (self.runs / "trial-results.json").read_bytes()
        result = module.smoke_validation(self.runs, "smoke")
        self.assertEqual(result["schema_version"], 3)
        self.assertEqual(result["protocol"], PROTOCOL)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["verified_successful_searches"], 0)
        self.assertTrue(result["trials"][0]["natural_non_use"])
        self.assertEqual(result["setup_probe"]["verified_successful_vector_searches"], 1)
        self.assertNotIn("bridge_sha256", result["setup_probe"])
        self.assertEqual((self.runs / "trial-results.json").read_bytes(), before)

    def test_native_success_needs_no_private_bridge_trace_and_allows_index_refresh(self):
        agent = self.fixture([False, True])
        self.probe_fixture()
        result = module.smoke_validation(self.runs, "smoke")
        self.assertFalse((agent / "zg-trace.jsonl").exists())
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["verified_successful_searches"], 1)
        self.assertEqual(result["trials"][0]["native_errors"], 1)

    def test_even_successful_qa_requires_fresh_same_run_native_probe(self):
        self.fixture([True])
        self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")

    def test_setup_success_does_not_hide_all_failed_calls_missing_results_or_fabricated_metrics(self):
        self.probe_fixture()
        for outcomes, measured in (([False], None), ([None], None), ([False], 1)):
            with self.subTest(outcomes=outcomes, measured=measured):
                self.fixture(outcomes, measured)
                self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")

    def test_wrong_fixture_or_exact_only_probe_does_not_establish_vector_path(self):
        self.fixture([])
        for kwargs in ({"marker": "wrong"}, {"vector": False}, {"outcomes": [False]}):
            with self.subTest(kwargs=kwargs):
                self.probe_fixture(**kwargs)
                self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")

    def test_old_protocol_missing_installation_and_changed_source_fail(self):
        self.probe_fixture()
        for mutation in ("old_protocol", "missing_install", "changed_source", "stale_hash"):
            with self.subTest(mutation=mutation):
                agent = self.fixture([True])
                path = self.runs / "trial-results.json"
                ledger = json.loads(path.read_text())
                if mutation == "old_protocol":
                    ledger["protocol"] = "workspace-qa-qoder-v1"
                elif mutation == "missing_install":
                    (agent / "install-manifest.json").unlink()
                elif mutation == "changed_source":
                    ledger["trials"][1]["source_unchanged"] = False
                else:
                    ledger["trials"][1]["installation"]["manifest_sha256"] = "stale"
                dump(path, ledger)
                self.assertEqual(module.smoke_validation(self.runs, "smoke")["status"], "invalid")

    def test_security_startup_failure_in_either_profile_invalidates_smoke(self):
        self.probe_fixture()
        for profile in ("baseline", "with-zg"):
            with self.subTest(profile=profile):
                self.fixture([True])
                path = self.runs / f"3-r01-{profile}/agent/qodercli-stream.jsonl"
                event = {"type": "system", "subtype": "hook_response", "hook_id": "security",
                    "hook_name": "Initializing Qoder Security", "hook_event": "SessionStart",
                    "exit_code": 127, "outcome": "error"}
                path.write_text(json.dumps(event) + "\n" + path.read_text())
                result = module.smoke_validation(self.runs, "smoke")
                self.assertEqual(result["status"], "invalid")
                check = next(row for row in result["startup_checks"] if row["profile"] == profile)
                self.assertEqual(check["status"], "failed")
                self.assertEqual(check["security_hooks"][0]["responses"][0]["exit_code"], 127)

    def test_batch_never_filters_tool_choice(self):
        self.fixture([])
        before = (self.runs / "trial-results.json").read_bytes()
        self.assertEqual(module.smoke_validation(self.runs, "batch")["status"], "not_applicable")
        self.assertEqual((self.runs / "trial-results.json").read_bytes(), before)

    def run_main(self, phase, statuses):
        self.fixture(statuses)
        self.probe_fixture()
        code = self.root / "fake-code"
        dump(code / "data/lock.json", {"experiment": {"protocol": PROTOCOL}, "tasks": [{"task_id": "3", "answer_filename": "answer.md"}]})
        def subprocess_run(command, **kwargs):
            if str(command[1]).endswith("report.py"):
                output = self.root / "report"
                dump(output / "summary.json", {"efficacy_claim_ready": True, "summary": {"complete": True},
                                                "scores": {"baseline": 19 / 21, "with-zg": 18 / 21}})
                (output / "summary.md").write_text("Coverage: COMPLETE\nRubric baseline 19/21, with zg 18/21\n")
                (output / "rows.json").write_text("original observations")
            return CompletedProcess(command, 0)
        before = (self.runs / "trial-results.json").read_bytes()
        with patch.object(module, "HERE", code), patch.object(module, "embedding_preflight"), patch.object(module, "sdk_preflight"), \
                patch.object(module.subprocess, "run", side_effect=subprocess_run), \
                patch.dict("os.environ", {"QODER_PERSONAL_ACCESS_TOKEN": "fixture", "GLM_API_KEY": "fixture", "QWEN_API_KEY": "fixture"}):
            code = module.main(["--task-id", "3", "--repetitions", "1", "--phase", phase,
                                "--output", str(self.root), "--upstream", str(self.root / "upstream")])
        self.assertEqual((self.runs / "trial-results.json").read_bytes(), before)
        self.assertEqual((self.root / "report/rows.json").read_text(), "original observations")
        return code

    def test_invalid_smoke_nonzero_preserves_metrics_and_judgements(self):
        self.assertEqual(self.run_main("smoke", [False]), 1)
        summary = json.loads((self.root / "report/summary.json").read_text())
        self.assertFalse(summary["pipeline_validation_complete"])
        self.assertFalse(summary["efficacy_claim_ready"])
        self.assertEqual(summary["scores"], {"baseline": 19 / 21, "with-zg": 18 / 21})

    def test_valid_smoke_is_not_formal_efficacy(self):
        self.assertEqual(self.run_main("smoke", [True]), 0)
        summary = json.loads((self.root / "report/summary.json").read_text())
        self.assertTrue(summary["pipeline_validation_complete"])
        self.assertFalse(summary["efficacy_claim_ready"])
        self.assertIn("excluded from formal efficacy estimates", (self.root / "report/summary.md").read_text())

    def test_incomplete_judge_remains_incomplete_after_valid_smoke(self):
        self.fixture([])
        self.probe_fixture()
        output = self.root / "report"
        dump(output / "summary.json", {"summary": {"complete": False}, "efficacy_claim_ready": True})
        module.annotate_smoke_report(output, module.smoke_validation(self.runs, "smoke"))
        summary = json.loads((output / "summary.json").read_text())
        self.assertFalse(summary["pipeline_validation_complete"])
        self.assertFalse(summary["efficacy_claim_ready"])

    def test_formal_batch_keeps_zero_zg_usage(self):
        self.assertEqual(self.run_main("batch", []), 0)
        summary = json.loads((self.root / "report/summary.json").read_text())
        self.assertTrue(summary["efficacy_claim_ready"])
        self.assertNotIn("smoke_validation", summary)


class ContinuationTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.code = self.root / "code"
        self.output = self.root / "output"
        self.prior = self.root / "original-artifact"
        self.review = self.root / "code-review.json"
        self.commands = []
        dump(self.code / "data/lock.json", {"experiment": {"protocol": PROTOCOL},
            "tasks": [{"task_id": "3", "answer_filename": "answer.md"}]})
        dump(self.review, {"status": "verified"})

    def run_main(self, *, runner_status=1, judge_status=1, report_status=0, dataset_failure=False):
        from runner import collect_results, make_plan
        def invoke(command, **kwargs):
            name = Path(command[1]).name
            self.commands.append((name, list(command), kwargs))
            if name == "dataset.py" and dataset_failure:
                raise CalledProcessError(1, command)
            if name == "runner.py":
                runs = self.output / "runs"
                runs.mkdir()
                plan = make_plan("3", 10)
                for i, trial in enumerate(plan["trials"]):
                    trial["status"] = "contract_failure" if i == 3 else "completed"
                collect_results(runs, plan)
                return CompletedProcess(command, runner_status)
            if name == "judge.py":
                return CompletedProcess(command, judge_status)
            if name == "report.py":
                output = self.output / "report"
                dump(output / "summary.json", {"summary": {"complete": False,
                    "execution_complete": report_status == 0}})
                (output / "summary.md").write_text("Original failure retained.\n")
                return CompletedProcess(command, report_status)
            return CompletedProcess(command, 0)
        with patch.object(module, "HERE", self.code), patch.object(module, "embedding_preflight"), \
                patch.object(module, "sdk_preflight"), patch.object(module.subprocess, "run", side_effect=invoke), \
                patch.dict("os.environ", {"QODER_PERSONAL_ACCESS_TOKEN": "fixture", "GLM_API_KEY": "fixture", "QWEN_API_KEY": "fixture"}), \
                redirect_stdout(io.StringIO()):
            return module.main(["--task-id", "3", "--repetitions", "10", "--phase", "batch",
                "--output", str(self.output), "--upstream", str(self.root / "upstream"),
                "--continue-from", str(self.prior), "--continuation-code-review", str(self.review)])

    def test_report_execution_gate_decides_completion_with_preserved_runner_and_judge_failure(self):
        self.assertEqual(self.run_main(), 0)
        commands = {name: (command, kwargs) for name, command, kwargs in self.commands}
        self.assertEqual(list(commands), ["dataset.py", "runner.py", "judge.py", "report.py"])
        runner_command = commands["runner.py"][0]
        self.assertEqual(runner_command[runner_command.index("--continue-from") + 1], str(self.prior))
        self.assertEqual(runner_command[runner_command.index("--continuation-code-review") + 1], str(self.review))
        self.assertEqual(runner_command[runner_command.index("--repetitions") + 1], "10")
        self.assertEqual(runner_command[runner_command.index("--timeout") + 1], "900")
        judge = commands["judge.py"][0]
        for option, name in (("--continue-from-ledger", "prior-ledger.json"),
                             ("--continue-from-judgements", "prior-judgements.json")):
            self.assertEqual(judge[judge.index(option) + 1], str(self.output.resolve() / "runs/continuation-evidence" / name))
        self.assertIn("--require-executed", commands["report.py"][0])
        self.assertNotIn("--require-complete", commands["report.py"][0])
        ledger = json.loads((self.output / "runs/trial-results.json").read_text())
        self.assertEqual(len(ledger["trials"]), 20)
        self.assertEqual(ledger["trials"][3]["status"], "contract_failure")
        validation = json.loads((self.output / "smoke_validation.json").read_text())
        self.assertEqual(validation["status"], "not_applicable")

    def test_incomplete_report_fails_even_if_all_subprocesses_return_zero(self):
        self.assertEqual(self.run_main(runner_status=0, judge_status=0, report_status=1), 1)

    def test_abnormal_process_exit_cannot_be_masked_by_successful_report(self):
        for runner_status, judge_status in ((-15, 1), (2, 1), (1, -9), (1, 2)):
            with self.subTest(runner_status=runner_status, judge_status=judge_status):
                self.output = self.root / f"output-{runner_status}-{judge_status}"
                self.commands.clear()
                with self.assertRaisesRegex(RuntimeError, "Continuation process failed abnormally"):
                    self.run_main(runner_status=runner_status, judge_status=judge_status)
                self.assertEqual(self.commands[-1][0], "report.py")
                self.assertTrue((self.output / "setup-failure.json").is_file())
                self.assertEqual(json.loads((self.output / "runs/trial-results.json").read_text())["trials"][3]["status"], "contract_failure")

    def test_setup_exception_keeps_all_planned_slots_and_cannot_be_masked_by_report(self):
        with self.assertRaises(CalledProcessError):
            self.run_main(dataset_failure=True)
        self.assertEqual([name for name, _, _ in self.commands], ["dataset.py", "report.py"])
        rows = json.loads((self.output / "runs/trial-results.json").read_text())["trials"]
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(row["status"] == "planned" for row in rows))

    def test_continuation_rejects_changed_repetition_count_before_setup(self):
        with patch.object(module, "embedding_preflight") as preflight, \
                self.assertRaisesRegex(ValueError, "original 10 repetitions"):
            module.main(["--task-id", "3", "--repetitions", "1", "--phase", "batch",
                "--output", str(self.output), "--upstream", str(self.root / "upstream"),
                "--continue-from", str(self.prior), "--continuation-code-review", str(self.review)])
        preflight.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
