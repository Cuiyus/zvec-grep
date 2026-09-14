"""A replay cannot replace past observations or inflate the E2E sample count."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from zg_bench.swe_qa.query_trajectory import analyze
from zg_bench.swe_qa.joint_report import build_report, main
from test_first_query_analysis import dump, events, experiment, oc, tool, side


def score(rank=1):
    return {"native": {"query_relevance": {"target": {"status": "scored", "first_hit_rank": rank,
        "matches": [{"target_id": "getter", "rank": rank}] if rank is not None else [],
        "hit_at_1": rank == 1, "hit_at_5": rank is not None and rank <= 5, "hit_at_10": rank is not None and rank <= 10,
        "rr_at_10": 1 / rank if rank and rank <= 10 else 0}}}}


class JointReportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        experiment(self.root, [("r", "zvec-grep", "completed"), ("failed", "baseline", "timeout")])
        events(self.root / "r/agent/opencode.txt", [oc("step_start", "m1"),
            tool("m1", "z", {"query": "getter"}, "original output"),
            tool("m1", "same-read", {"filePath": "/app/a.py"}, "10: def getter(self):", "read"),
            oc("step_finish", "m1", reason="tool-calls", tokens={"input": 5, "cache": {"read": 0}}),
            oc("step_start", "m2"), tool("m2", "later-read", {"filePath": "/app/a.py"}, "10: def getter(self):", "read"),
            tool("m2", "later-search", {"pattern": "getter"}, "no matches", "grep"),
            oc("step_finish", "m2", reason="tool-calls", tokens={"input": 8, "cache": {"read": 2}}),
            oc("step_start", "m3"), oc("text", "m3", text="The getter is at a.py:10."),
            oc("step_finish", "m3", reason="stop", tokens={"input": 12, "cache": {"read": 3}})])
        events(self.root / "r/agent/zg-trace.jsonl", [side("getter", "original output")])
        dump(self.root / "r/result.json", {"status": "completed", "final_metrics": {"total_prompt_tokens": 30}})
        self.analysis = analyze(self.root)
        self.call = self.analysis["groups"][0]["trials"][0]["first_zg_call"]
        self.labels = {"targets": [{"target_id": "getter", "path": "a.py", "definition_line": 10, "definition": "def getter(self):\n"}]}
        self.observed = [{"group": self.root.name, "trial_id": "r", "call_id": "z", "annotation_id": self.call["annotation_id"],
            "output_sha256": hashlib.sha256(b"original output").hexdigest(), "request_scores": score()}]
        self.unit = {"unit_id": "replay", "kind": "faithful", "request": self.call["backend"]["request"],
            "quality_observation": {"status": "scored", "repetition": 1, "output_sha256": hashlib.sha256(b"replayed output").hexdigest(),
                "context_scores": [{"annotation_id": self.call["annotation_id"], "context_id": self.call["context_id"], "request_scores": score(5)}]},
            "repeats": [{"repetition": i} for i in range(1, 6)], "stability": {"all_public_outputs_identical": True}}

    def report(self, **kwargs):
        return build_report(self.root, self.analysis, {"quality_repetition": 1}, {"units": [self.unit], "observed_e2e_calls": self.observed}, self.labels, {"status": "frozen"}, **kwargs)

    def test_actual_replay_separated_followup_excludes_same_message_and_samples_fixed(self):
        report = self.report()
        self.assertEqual(report["planned_e2e_trials"], 2)
        self.assertEqual(report["independent_qa_tasks"], 1)
        chain = report["groups"][0]["trials"][0]["query_chains"][0]
        self.assertFalse(chain["actual_and_replay_output_identical"])
        self.assertEqual(chain["actual_observation"]["request_scores"], score())
        self.assertEqual(chain["replay_observation"]["request_scores"], score(5))
        following = chain["following_observed_actions"]
        self.assertEqual(following["tool_calls"], 2)
        self.assertEqual(following["search_calls"], 1)
        self.assertEqual(following["native_input_tokens"], 25)
        self.assertEqual([r["call_id"] for r in following["read_of_returned_target_definition"]["evidence"]], ["later-read"])
        self.assertEqual(following["explicit_later_target_line_citation"]["status"], "observed")

    def test_unknown_scores_not_zero_failed_trials_remain(self):
        self.observed[0]["request_scores"] = None
        report = self.report()
        trials = report["groups"][0]["trials"]
        self.assertEqual(trials[1]["status"], "timeout")
        self.assertIsNone(trials[1]["metrics"]["input_tokens"])
        self.assertEqual(trials[0]["query_chains"][0]["actual_observation"]["status"], "unknown")
        self.assertEqual(trials[0]["query_chains"][0]["following_observed_actions"]["read_of_returned_target_definition"]["status"], "unknown")
        self.assertEqual(report["query_observation_summary"]["actual_calls_with_scorable_target_labels"], 0)

    def test_stale_actual_score_hash_cannot_substitute_past_return(self):
        self.observed[0]["output_sha256"] = "incorrect"
        report = self.report()
        self.assertIsNone(report["groups"][0]["trials"][0]["query_chains"][0]["actual_observation"]["request_scores"])

    def test_changed_source_rejected(self):
        with (self.root / "r/agent/opencode.txt").open("a") as output:
            output.write("{}\n")
        with self.assertRaisesRegex(ValueError, "source missing or changed"):
            self.report()

    def test_missing_stages_still_render_full_ledger(self):
        report = build_report(self.root, self.analysis)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["planned_e2e_trials"], 2)
        self.assertIn("shared_labels", report["missing_stages"])
        self.assertIsNone(report["groups"][0]["trials"][0]["query_chains"][0]["actual_and_replay_output_identical"])

    def test_cli_accepts_runtime_observed_list_and_annotation_manifest(self):
        dump(self.root / "query-analysis.json", self.analysis)
        dump(self.root / "replay/replay-plan.json", {"quality_repetition": 1})
        dump(self.root / "replay/replay-report.json", {"units": [self.unit]})
        dump(self.root / "replay/observed-query-scores.json", self.observed)
        dump(self.root / "gt/query-intents.json", self.labels)
        dump(self.root / "gt/annotation-manifest.json", {"status": "frozen"})
        main(["--runs-dir", str(self.root), "--analysis", str(self.root / "query-analysis.json"), "--replay-dir", str(self.root / "replay"),
            "--labels", str(self.root / "gt/query-intents.json"), "--annotation-dir", str(self.root / "gt"), "--output", str(self.root / "joint.json")])
        report = json.loads((self.root / "joint.json").read_text())
        self.assertEqual(report["annotation_audit"]["status"], "frozen")
        self.assertEqual(report["query_observation_summary"]["actual_calls_with_scorable_target_labels"], 1)
        self.assertTrue((self.root / "joint.md").is_file())

    def test_incomplete_native_trace_keeps_followup_counts_as_lower_bounds(self):
        path = self.root / "r/agent/opencode.txt"
        native = path.read_text().splitlines()
        path.write_text("\n".join(native[:-1]) + "\n")
        self.analysis = analyze(self.root)
        report = self.report()
        following = report["groups"][0]["trials"][0]["query_chains"][0]["following_observed_actions"]
        self.assertIsNone(following["tool_calls"])
        self.assertIsNone(following["native_input_tokens"])
        self.assertEqual(following["observed_lower_bounds"]["tool_calls"], 2)

    def test_cli_rejects_ground_truth_for_another_query_analysis(self):
        dump(self.root / "query-analysis.json", self.analysis)
        dump(self.root / "gt/query-intents.json", {**self.labels, "analysis_sha256": "wrong"})
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            main(["--runs-dir", str(self.root), "--analysis", str(self.root / "query-analysis.json"), "--replay-dir", str(self.root / "missing"),
                "--labels", str(self.root / "gt/query-intents.json"), "--annotation-dir", str(self.root / "gt"), "--output", str(self.root / "joint.json")])
