import concurrent.futures
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = load("budget_hook", "tool_budget.py")
runner = load("budget_runner", "run.py")


def event(tool_id, name="Read"):
    return {"hook_event_name": "PreToolUse", "tool_use_id": tool_id, "tool_name": name}


class BudgetTests(unittest.TestCase):
    def test_budget_does_not_override_permissions_and_counts_failed_attempts(self):
        state = {"started_at_epoch": 100, "calls": {}}
        first = hook.decide(state, event("first"), 1, 600, 101)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", first)
        second = hook.decide(state, event("second", "mcp__zvec_grep__zvec_grep_search"), 1, 600, 102)["hookSpecificOutput"]
        self.assertEqual(second["permissionDecision"], "deny")
        self.assertIn("final answer", second["permissionDecisionReason"])
        self.assertEqual(sum(c["admitted"] for c in state["calls"].values()), 1)

    def test_duplicate_identity_is_idempotent(self):
        state = {"started_at_epoch": 100, "calls": {}}
        hook.decide(state, event("same"), 2, 600, 101)
        hook.decide(state, event("same"), 2, 600, 102)
        self.assertEqual(len(state["calls"]), 1)
        with self.assertRaises(ValueError):
            hook.decide(state, event("same", "Grep"), 2, 600, 103)

    def test_deadline_blocks_retrieval_even_with_calls_remaining(self):
        state = {"started_at_epoch": 100, "calls": {}}
        result = hook.decide(state, event("late"), 24, 600, 700)
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(state["calls"]["late"]["reason"], "time_limit")

    def test_parallel_processes_cannot_overspend_shared_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({"started_at_epoch": time.time(), "calls": {}}))
            command = [sys.executable, str(HERE / "tool_budget.py"), "--state", str(state),
                       "--limit", "3", "--research-seconds", "60"]

            def call(index):
                return subprocess.run(command, input=json.dumps(event(str(index))), text=True,
                                      capture_output=True, timeout=10)

            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                outputs = list(pool.map(call, range(12)))
            self.assertTrue(all(result.returncode == 0 for result in outputs))
            self.assertEqual(sum(json.loads(result.stdout)["hookSpecificOutput"].get("permissionDecision") == "deny"
                                 for result in outputs), 9)
            saved = json.loads(state.read_text())
            self.assertEqual(len(saved["calls"]), 12)
            self.assertEqual(sum(value["admitted"] for value in saved["calls"].values()), 3)

    def test_missing_state_fails_closed_on_pinned_hook_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(HERE / "tool_budget.py"), "--state",
                                     str(Path(directory) / "missing"), "--limit", "1", "--research-seconds", "60"],
                                    input=json.dumps(event("one")), text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn("BUDGET_HOOK_INFRASTRUCTURE_ERROR", result.stderr)

    def test_audit_detects_hook_not_running_and_deadline_bypass(self):
        stream = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "one", "name": "Read"}]}})
        missing = runner.audit_tool_budget({"calls": {}}, stream, 24, 600)
        self.assertFalse(missing["valid"])
        state = {"calls": {"one": {"name": "Read", "admitted": True, "elapsed_seconds": 601}}}
        self.assertIn("research_deadline_exceeded", runner.audit_tool_budget(state, stream, 24, 600)["errors"])
        state["calls"]["one"]["elapsed_seconds"] = 1
        self.assertTrue(runner.audit_tool_budget(state, stream, 24, 600)["valid"])

    def test_shared_guidance_is_opt_in_and_task_neutral(self):
        self.assertEqual(runner.common_instructions(), runner.COMMON_INSTRUCTIONS)
        text = runner.common_instructions(tool_budget=24, research_seconds=600)
        self.assertIn("24 repository tool calls", text)
        self.assertIn("600 elapsed seconds", text)
        for secret_hint in ["wal_file_", "SegmentImpl", "optimize", "recovery", "C1", "rubric"]:
            self.assertNotIn(secret_hint, text)

    def test_denied_call_cannot_be_reported_as_successful_execution(self):
        state = {"calls": {"denied": {"name": "Read", "admitted": False, "elapsed_seconds": 601}}}
        request = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "denied", "name": "Read"}]}}
        for is_error in (False, True):
            result = {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "denied", "is_error": is_error}]}}
            audit = runner.audit_tool_budget(state, json.dumps(request) + "\n" + json.dumps(result), 24, 600)
            self.assertEqual(audit["valid"], is_error)
            self.assertEqual(audit["denied_result_errors"], int(is_error))
        interrupted = runner.audit_tool_budget(state, json.dumps(request), 24, 600)
        self.assertEqual(interrupted["denied_result_unresolved"], 1)


if __name__ == "__main__":
    unittest.main()
