from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("workspace_failure_audit_test", ROOT / "failure_audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class FailureAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / "lock.json"
        self.results = self.root / "results"
        write(self.manifest, {"tasks": [{"task_id": "3"}], "repetitions": 1})

    def trial(self, profile, status="completed", **fields):
        return {"trial_id": f"3-r01-{profile}", "task_id": "3", "profile": profile, "repetition": 1,
                "status": status, **fields}

    def ledger(self, trials):
        path = self.results / "artifact/runs/trial-results.json"
        write(path, {"task_id": "3", "repetitions_per_profile": 1, "trials": trials})
        return path

    def budget_session(self):
        return {"status": "budget_exhausted", "limit_reason": "input_tokens", "returncode": -15,
                "limits": {"input_tokens": 600000, "tool_calls": 120, "wall_seconds": 900},
                "observed": {"input_tokens": None, "input_tokens_observed_lower_bound": 615555,
                             "input_usage_missing_turns": 1, "invalid_usage_events": 0, "model_requests": 18, "tool_calls": 53},
                "wall_seconds": 160.5}

    def test_budget_failure_preserves_actual_spend_and_lower_bound_without_zero_imputation(self):
        self.ledger([self.trial("baseline", input_tokens=100, tool_calls=3, wall_seconds=10),
                     self.trial("with-zg", "budget_exhausted", input_tokens=None, tool_calls=53, wall_seconds=163,
                                session=self.budget_session())])
        result = audit.build_audit(self.results, self.manifest)
        self.assertTrue(result["execution_coverage_complete"])
        self.assertEqual(result["summary"]["terminal_record_coverage"], 1)
        self.assertEqual(result["summary"]["qa_completion_rate"], 0.5)
        row = result["failures"][0]
        self.assertEqual(row["limit_reason"], "input_tokens")
        self.assertEqual(row["limit_threshold"], 600000)
        self.assertEqual(row["input_tokens_observed_lower_bound"], 615555)
        self.assertEqual(row["input_usage_missing_turns"], 1)
        self.assertIsNone(row["input_tokens"])
        self.assertEqual(row["tool_calls"], 53)
        self.assertEqual(row["wall_seconds"], 163)

    def test_full_200_denominator_survives_wholly_absent_artifacts(self):
        write(self.manifest, {"tasks": [{"task_id": str(i)} for i in range(10)], "repetitions": 10})
        result = audit.build_audit(self.results, self.manifest)
        self.assertEqual(result["summary"]["planned"], 200)
        self.assertEqual(result["summary"]["attempt_status_unknown"], 200)
        self.assertEqual(result["summary"]["not_started"], 0)
        self.assertIsNone(result["failures"][0]["input_tokens_observed_lower_bound"])
        self.assertFalse(result["execution_coverage_complete"])

    def test_explicit_planned_is_not_started_but_missing_record_is_unknown(self):
        self.ledger([self.trial("baseline", "planned")])
        result = audit.build_audit(self.results, self.manifest)
        self.assertEqual(result["summary"]["not_started"], 1)
        self.assertEqual(result["summary"]["attempt_status_unknown"], 1)
        self.assertEqual(result["summary"]["attempted"], 0)

    def test_sidecar_keeps_actual_agent_terminal_evidence_when_ledger_not_finalized(self):
        ledger = self.ledger([self.trial("baseline", "planned"), self.trial("with-zg", "planned")])
        write(ledger.parent / "3-r01-with-zg/agent/session.json", self.budget_session())
        result = audit.build_audit(self.results, self.manifest)
        row = next(row for row in result["trials"] if row["profile"] == "with-zg")
        self.assertTrue(row["attempted"])
        self.assertFalse(row["terminal_recorded"])
        self.assertEqual(row["category"], "no_final_trial_record")
        self.assertEqual(row["session_status"], "budget_exhausted")
        self.assertEqual(row["input_tokens_observed_lower_bound"], 615555)
        self.assertEqual(row["session_evidence"], "session_file")

    def test_conflicting_sidecar_does_not_guess_lower_bound(self):
        ledger = self.ledger([self.trial("baseline"), self.trial("with-zg", "budget_exhausted", session=self.budget_session())])
        changed = self.budget_session()
        changed["observed"]["input_tokens_observed_lower_bound"] = 999999
        write(ledger.parent / "3-r01-with-zg/agent/session.json", changed)
        result = audit.build_audit(self.results, self.manifest)
        self.assertFalse(result["execution_coverage_complete"])
        self.assertEqual(len(result["artifact_anomalies"]), 1)
        self.assertIsNone(result["failures"][0]["input_tokens_observed_lower_bound"])
        self.assertEqual(result["failures"][0]["session_evidence"], "conflicting")

    def test_generic_failure_does_not_invent_model_or_infrastructure_cause(self):
        self.ledger([self.trial("baseline", "failed", returncode=1), self.trial("with-zg", "launch_failure", error_type="FileNotFoundError")])
        result = audit.build_audit(self.results, self.manifest)
        categories = result["summary"]["categories"]
        self.assertEqual(categories["failure_cause_unknown"], 1)
        self.assertEqual(categories["infrastructure_or_protocol_failure"], 1)
        self.assertIsNone(result["trials"][0]["limit_reason"])

    def test_setup_failure_is_retained_separately_from_unstarted_trials(self):
        ledger = self.ledger([self.trial("baseline", "planned"), self.trial("with-zg", "planned")])
        write(ledger.parent.parent / "setup-failure.json", {"status": "failed", "error_type": "HTTPError"})
        result = audit.build_audit(self.results, self.manifest)
        self.assertEqual(result["summary"]["not_started"], 2)
        self.assertEqual(result["failures"][0]["setup_failure"]["error_type"], "HTTPError")

    def test_unknown_status_and_malformed_ledger_are_audited_not_counted_as_zero_or_complete(self):
        self.ledger([self.trial("baseline", "future_status")])
        path = self.results / "invalid/trial-results.json"
        path.parent.mkdir()
        path.write_text("malformed")
        result = audit.build_audit(self.results, self.manifest)
        self.assertEqual(result["summary"]["attempt_status_unknown"], 2)
        self.assertEqual(len(result["artifact_anomalies"]), 1)
        self.assertFalse(result["execution_coverage_complete"])

    def test_cli_writes_audit_but_does_not_change_original_verdict_or_artifact(self):
        ledger = self.ledger([self.trial("baseline"), self.trial("with-zg", "budget_exhausted", session=self.budget_session())])
        before = ledger.read_bytes()
        output = self.root / "report/failure-audit"
        self.assertEqual(audit.main(["--runs-dir", str(self.results), "--manifest", str(self.manifest), "--output", str(output)]), 0)
        self.assertEqual(ledger.read_bytes(), before)
        self.assertEqual({path.name for path in output.iterdir()}, {"summary.json", "summary.md", "failures.json"})
        self.assertIn("no failed trial is retried", (output / "summary.md").read_text())
        result = json.loads((output / "summary.json").read_text())
        self.assertTrue(result["does_not_change_ci_verdict_or_retry_policy"])


if __name__ == "__main__":
    unittest.main()
