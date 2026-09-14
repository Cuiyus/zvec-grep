"""All-round query capture preserves feedback boundaries and sample identity."""
import json
from pathlib import Path
import tempfile
import unittest

from zg_bench.swe_qa.query_trajectory import analyze, main, write_report
from test_first_query_analysis import dump, events, experiment, oc, tool, side, qassistant, qcall, qresult


class QueryTrajectoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_all_rounds_and_same_message_parallel_feedback_boundary(self):
        experiment(self.root, [("r", "zvec-grep", "completed")])
        agent = self.root / "r/agent"
        events(agent / "opencode.txt", [oc("step_start", "m1"),
            tool("m1", "read", {"filePath": "/app"}, "directory", "read"),
            tool("m1", "a", {"query": "same"}, "out1", start=10, end=40),
            tool("m1", "b", {"query": "parallel"}, "out2", start=20, end=50),
            oc("step_finish", "m1", reason="tool-calls"), oc("step_start", "m2"),
            tool("m2", "c", {"query": "same"}, "out3"), oc("step_finish", "m2", reason="stop")])
        events(agent / "zg-trace.jsonl", [side("same", "out1"), side("parallel", "out2"), side("same", "out3")])
        report = analyze(self.root)
        trial = report["groups"][0]["trials"][0]
        first, later = trial["zg_decision_rounds"]
        self.assertEqual(len(first["zg_calls"]), 2)
        self.assertEqual(first["overlapping_zg_execution_pairs"], [["a", "b"]])
        self.assertEqual(first["prior_turn_feedback"], [])
        self.assertEqual([c["call_id"] for c in later["prior_turn_feedback"]], ["read", "a", "b"])
        self.assertNotEqual(first["context_id"], later["context_id"])
        same = next(q for q in report["query_catalog"] if q["text"] == "same")
        self.assertEqual((same["occurrence_count"], same["trial_count"]), (2, 1))
        request = next(r for r in report["request_catalog"] if r["request"]["queries"] == ["same"])
        self.assertEqual(request["occurrence_count"], 2)
        self.assertEqual(len({o["annotation_id"] for o in request["occurrences"]}), 2)
        self.assertEqual(trial["first_zg_call"]["call_id"], "a")
        for annotation in report["annotation_catalog"]:
            # Own return is excluded. Later context legitimately retains earlier outputs.
            if annotation.get("occurrences") and annotation["occurrences"][0]["call_id"] in {"a", "b"}:
                self.assertEqual(annotation["prior_turn_feedback"], [])

    def test_same_request_initial_context_shared_across_groups(self):
        for group in ("a", "b"):
            root = self.root / group
            experiment(root, [("r", "zvec-grep", "completed")])
            events(root / "r/agent/opencode.txt", [oc("step_start", "m"), tool("m", group, {"query": "same"}, "out"), oc("step_finish", "m", reason="stop")])
            events(root / "r/agent/zg-trace.jsonl", [side("same", "out")])
        report = analyze(self.root)
        self.assertEqual(len(report["request_catalog"]), 1)
        self.assertEqual(len(report["annotation_catalog"]), 2)  # shared observed + original
        self.assertEqual(report["request_catalog"][0]["trial_count"], 2)
        self.assertEqual(report["groups"][0]["first_main_query_text_consistency"]["observed_trials"], 1)

    def test_qoder_stream_snapshots_not_duplicate_late_calls(self):
        experiment(self.root, [("r", "zvec-grep", "completed")])
        agent = self.root / "r/agent"
        events(agent / "qodercli-stream.jsonl", [qassistant("m1", [qcall("a", {"query": "one"})]), qresult("a", "one out"),
            qassistant("m2", [{"type": "text", "text": "Need another entry"}]),
            qassistant("m2", [qcall("b", {"query": "two"})]),
            qassistant("m2", [qcall("b", {"query": "two"})]), qresult("b", "two out"), {"type": "result"}])
        events(agent / "zg-trace.jsonl", [side("one", "one out"), side("two", "two out")])
        report = analyze(self.root)
        trial = report["groups"][0]["trials"][0]
        self.assertEqual(len(trial["all_tool_calls"]), 2)
        self.assertEqual(len(trial["zg_decision_rounds"]), 2)
        self.assertEqual(sum(q["occurrence_count"] for q in report["query_catalog"]), 2)

    def test_missing_invalid_and_unlinked_requests_remain_visible(self):
        experiment(self.root, [("missing", "zvec-grep", "timeout"), ("none", "zvec-grep", "completed"), ("bad", "zvec-grep", "failed")])
        events(self.root / "none/agent/opencode.txt", [oc("step_start", "m"), oc("step_finish", "m", reason="stop")])
        events(self.root / "bad/agent/opencode.txt", [oc("step_start", "m"), tool("m", "bad", {"query": "q", "routes": 5}, "invalid", status="error")])
        report = analyze(self.root)
        rows = {t["trial_id"]: t for t in report["groups"][0]["trials"]}
        self.assertIsNone(rows["missing"]["zg_adoption_observed"])
        self.assertFalse(rows["none"]["zg_adoption_observed"])
        self.assertEqual(rows["bad"]["all_tool_calls"][0]["raw_arguments"]["routes"], 5)
        self.assertEqual(len(report["unreplayable_occurrences"]), 1)
        self.assertEqual(report["request_catalog"], [])

    def test_original_request_shares_label_without_becoming_extra_trial(self):
        question = "Where is the getter?"
        request = {"root": "/app", "query": question, "limit": 10, "autoUpdate": False, "trace": True}
        experiment(self.root, [("r", "zvec-grep", "completed")])
        events(self.root / "r/agent/opencode.txt", [oc("step_start", "m"), tool("m", "c", request, "out"), oc("step_finish", "m", reason="stop")])
        events(self.root / "r/agent/zg-trace.jsonl", [{"event": "search", "request": request, "status": "success", "text": "out"}])
        report = analyze(self.root)
        self.assertEqual(len(report["annotation_catalog"]), 1)
        annotation = report["annotation_catalog"][0]
        self.assertEqual(annotation["kind"], "original")
        self.assertTrue(annotation["also_faithful_request"])
        self.assertEqual(len(annotation["occurrences"]), 1)

    def test_cli_persists_input_hashes_and_cannot_overwrite_input(self):
        experiment(self.root, [("r", "zvec-grep", "planned")])
        output = self.root / "analysis.json"
        main(["--runs-dir", str(self.root), "--case", str(self.root / "case.json"), "--output", str(output)])
        report = json.loads(output.read_text())
        self.assertEqual(report["protocol"], "readonly-query-trajectory-v6")
        self.assertTrue(output.with_suffix(".md").is_file())
        with self.assertRaisesRegex(ValueError, "input artifact"):
            write_report(report, self.root / "plan.json")

    def test_unknown_native_tool_name_does_not_hide_remaining_trace(self):
        experiment(self.root, [("r", "zvec-grep", "completed")])
        events(self.root / "r/agent/opencode.txt", [oc("step_start", "m"), tool("m", "bad", {}, name=None), oc("step_finish", "m", reason="stop")])
        report = analyze(self.root)
        self.assertEqual(report["groups"][0]["trials"][0]["all_tool_calls"][0]["category"], "unknown")
