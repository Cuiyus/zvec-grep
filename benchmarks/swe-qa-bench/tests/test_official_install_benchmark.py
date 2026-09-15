"""Official repeat experiment retains failures and never invents missing cost."""
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from zg_bench.swe_qa import official_install_benchmark as bench


class OfficialExperimentTests(unittest.TestCase):
    def test_changed_controls_override_native_completion_but_not_archived_byte_mismatch(self):
        spec, _ = bench.session_spec("qoder-qwen38max", {"question": "Explain."}, "baseline")
        trial = {"trial_id": "baseline-r01", "profile": "baseline", "repetition": 1}
        converted = {"contract_error_count": 0, "error_event_count": 0, "has_final_answer": True}
        with tempfile.TemporaryDirectory() as temporary:
            logs = Path(temporary)
            (logs / "session.json").write_text(json.dumps({"status": "completed", "observed": {
                "input_tokens": 123, "tool_calls": 3}}))
            for installation, code, expected in [
                ({"agent_config_contract_valid": False}, 5, "contract_failure"),
                ({"guidance_unchanged": False}, 5, "contract_failure"),
                ({"status": "contract_failure"}, 4, "contract_failure"),
                ({"agent_config_unchanged": False, "agent_config_contract_valid": True,
                  "guidance_unchanged": True}, 0, "completed"),
                ({"agent_config_unchanged": False, "guidance_unchanged": True}, 5, "failed"),
            ]:
                with self.subTest(installation=installation):
                    (logs / "install-manifest.json").write_text(json.dumps(installation))
                    with patch.object(bench, "convert_agent_trace", return_value=converted):
                        result = bench.collect_result(trial, logs, spec, code, True, "Explain.")
                    self.assertEqual(result["status"], expected)
                    self.assertEqual(result["returncode"], code)
                    self.assertEqual(result["input_tokens"], 123)

    def fixture(self):
        plan = bench.make_plan("reflex-6")
        plan["group"] = "opencode-glm52"
        rows = [{"trial_id": t["trial_id"], "profile": t["profile"], "status": "completed",
                 "input_tokens": 100 if t["profile"] == "baseline" else 80,
                 "tool_calls": 10 if t["profile"] == "baseline" else 8, "usage_complete": True}
                for t in plan["trials"]]
        return plan, rows

    def test_five_paired_trials_include_over_budget_and_no_adoption_results(self):
        plan, rows = self.fixture()
        baseline = next(r for r in rows if r["profile"] == "baseline")
        baseline.update(status="budget_exhausted", input_tokens=400)
        zg = next(r for r in rows if r["profile"] == "zvec-grep")
        zg["zg_adopted"] = False
        result = bench.summarize(plan, rows)
        self.assertEqual(result["groups"]["baseline"]["completed"], 4)
        self.assertEqual(result["groups"]["baseline"]["metrics"]["input_tokens"]["mean"], 160)
        self.assertEqual(result["mean_relative_change_percent"]["input_tokens"], -50)
        self.assertEqual(len(result["pairs"]), 5)
        self.assertEqual(len(result["trials"]), 10)
        self.assertFalse(result["quality_noninferiority_established"])

    def test_missing_trial_or_usage_is_unknown_not_a_saving(self):
        for mode in ("absent", "unknown"):
            plan, rows = self.fixture()
            if mode == "absent":
                rows.pop()
            else:
                rows[-1].update(input_tokens=None, usage_complete=False)
            result = bench.summarize(plan, rows)
            self.assertIsNone(result["mean_relative_change_percent"]["input_tokens"])
            self.assertTrue(any(p["input_tokens"] is None for p in result["pairs"]))

    def test_default_runtime_has_no_prompt_override_or_synthetic_mcp(self):
        case = {"question": "Explain the repository."}
        for group in bench.GROUPS:
            for profile in ("baseline", "zvec-grep"):
                spec, session = bench.session_spec(group, case, profile)
                self.assertEqual(session["profile"], profile)
                self.assertEqual(session["limits"], bench.LIMITS)
                self.assertEqual(session["root"], "/app")
                self.assertEqual(session["instruction"], bench.instruction(case))
                self.assertFalse({"guidance_override", "description_overrides", "mcp_command", "instructions"} & set(session))
                self.assertEqual(spec.version, "1.18.4" if spec.name == "opencode" else "1.1.45")

    def test_invalid_numeric_usage_does_not_produce_paired_savings(self):
        plan, rows = self.fixture()
        rows[0]["usage_complete"] = False
        result = bench.summarize(plan, rows)
        self.assertIsNone(result["pairs"][0]["input_tokens"])
        self.assertIsNone(result["pairs"][0]["tool_calls"])

    def test_execution_completion_is_separate_from_errors_and_scorability(self):
        scores = {"replays": [{"observations": [{"status": "error"}] * 5,
                                "context_assessments": [{"assessment": {"status": "unknown"}}]}],
                  "unplanned_replay_rows": []}
        status = bench.replay_stage_status(scores, 0)
        self.assertTrue(status["execution_complete"])
        self.assertEqual(status["status"], "complete_with_errors_or_unknown")
        self.assertEqual(status["scorable_contexts"], 0)
        self.assertFalse(status["quality_or_efficacy_pass"])
        scores["replays"][0]["observations"][0] = {"status": "ambiguous_duplicate"}
        self.assertEqual(bench.replay_stage_status(scores, 0)["status"], "incomplete")
