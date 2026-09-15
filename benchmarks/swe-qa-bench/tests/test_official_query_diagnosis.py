from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from zg_bench.swe_qa.official_query_diagnosis import build_catalog, score_records
from zg_bench.swe_qa.query_ground_truth import annotation_catalog


CASE = {"case_id": "fixture", "question": "How is the value computed?",
        "repo": {"url": "https://example.test/source", "commit": "a" * 40}}
ARGS = {"root": "/app", "query": "compute value", "fts": '["compute", "value"]'}
TEXT = "freshness: fresh\n#1 matchedBy=vector src/x.py:10-12\nsource:\n10\tdef target():\n11\t    return 1\n"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def jsonl(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(v) for v in values) + "\n")


def call(mid="m1", cid="c1", args=ARGS, text=TEXT, name="zvec_grep_zvec_grep_search", status="completed"):
    return {"type": "tool_use", "sessionID": "s", "part": {"messageID": mid, "callID": cid, "tool": name,
            "state": {"input": args, "output": text, "status": status}}}


def start(mid):
    return {"type": "step_start", "sessionID": "s", "part": {"messageID": mid}}


def finish(mid, reason="stop"):
    return {"type": "step_finish", "sessionID": "s", "part": {"messageID": mid, "reason": reason}}


def setup(root, events=None, *, extra_trials=()):
    write(root / "plan.json", {"case_id": CASE["case_id"], "group_id": "opencode-glm52", "trials": [
        {"trial_id": "trial", "profile": "zvec-grep-current", "trajectory_path": "trial/agent/trajectory.json"}, *extra_trials]})
    if events is not None:
        jsonl(root / "trial/agent/opencode.txt", events)


def labels_for(analysis):
    target = {"target_id": "target", "level": "function", "path": "src/x.py", "symbol": "target",
              "definition": "def target():", "definition_line": 10, "entry_start_line": 10, "entry_end_line": 12, "primary": True}
    labels = {"schema_version": 2, "labels_id": "fixture-labels", "repo": CASE["repo"], "targets": [target],
              "queries": [], "request_bindings": []}
    for a in analysis["annotation_catalog"]:
        labels["queries"].append({"query_id": a["annotation_id"], "context_id": a["context_id"], "text": "fixture",
            "classification": "original" if a["kind"] == "original" else "legitimate_subgoal", "annotation_status": "reviewed",
            "goal": "Locate computation", "accepted_target_ids": ["target"], "bridge_target_ids": [], "task_fact_ids": []})
        labels["request_bindings"].append({"request": a["request"], "context_id": a["context_id"], "query_id": a["annotation_id"]})
    entries = {"repo": CASE["repo"], "protocol": {"limit": 10}, "targets": [target], "groups": []}
    content = [{"type": "text", "text": TEXT}]
    content_hash = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    replay = [{"request_id": r["request_id"], "repetition": i, "mcp_request": r["mcp_request"], "request": r["request"],
               "status": "completed", "text": TEXT, "result": {"content": content}, "public_sha256": content_hash}
              for r in analysis["request_catalog"] for i in range(1, 6)]
    return labels, entries, replay


class OfficialQueryDiagnosisTests(unittest.TestCase):
    def test_native_parameters_and_original_defaults_without_tap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup(root, [start("m1"), call(), finish("m1")])
            # Poison sidecars must not be read or influence the official catalog.
            (root / "trial/agent/native-mcp.jsonl").write_text("invalid tap content")
            (root / "trial/agent/zg-trace.jsonl").write_text("invalid bridge content")
            report = build_catalog([root], CASE)
            self.assertEqual(len(annotation_catalog(report, CASE)), 2)
            original = next(r for r in report["request_catalog"] if r["kind"] == "original")
            self.assertEqual(original["request"], {"root": "/app", "query": CASE["question"], "limit": 10})
            actual = next(r for r in report["request_catalog"] if r["kind"] == "faithful")
            self.assertEqual(actual["request"], ARGS)
            self.assertEqual(actual["mcp_request"], {"name": "zvec_grep_search", "arguments": ARGS})
            self.assertIn(ARGS["fts"], [q["text"] for q in report["query_catalog"]])
            self.assertFalse(any("zg-trace" in s["path"] or "native-mcp" in s["path"] for s in report["input_artifacts"]))
            self.assertTrue(report["groups"][0]["trials"][0]["zg_adoption_observed"])

    def test_same_round_calls_share_context_later_feedback_changes_annotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup(root, [start("m0"), call("m0", "grep", {"pattern": "x"}, "Nothing found", "grep"),
                         finish("m0", "tool-calls"), start("m1"), call(), call(cid="c2"),
                         finish("m1", "tool-calls"), start("m2"), call("m2", "c3"), finish("m2")])
            report = build_catalog([root], CASE)
            self.assertEqual(len(report["occurrences"]), 3)
            self.assertEqual(len(report["request_catalog"]), 2)
            faithful = [a for a in report["annotation_catalog"] if a["kind"] == "faithful"]
            self.assertEqual(sorted(len(a["occurrences"]) for a in faithful), [1, 2])
            first = next(a for a in faithful if len(a["occurrences"]) == 2)
            self.assertEqual(first["prior_turn_feedback"][0]["visible_text"], "Nothing found")
            self.assertNotIn(TEXT, json.dumps(first))
            later = next(a for a in faithful if len(a["occurrences"]) == 1)
            self.assertEqual(len(later["prior_turn_feedback"]), 3)
            self.assertIn(TEXT, [f["visible_text"] for f in later["prior_turn_feedback"]])
            trial = report["groups"][0]["trials"][0]
            self.assertEqual([len(r["zg_calls"]) for r in trial["zg_decision_rounds"]], [2, 1])
            self.assertEqual(report["groups"][0]["adoption_summary"]["planned_treatment_trials"], 1)

    def test_complete_snapshots_do_not_duplicate_calls_and_keep_final_parameter_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup(root, [start("m1"), call(args={"root": "/app"}, text=None, status="running"), call(), call(), finish("m1")])
            report = build_catalog([root], CASE)
            self.assertEqual(len(report["occurrences"]), 1)
            observed = report["occurrences"][0]
            self.assertEqual(observed["source"]["line"], 3)
            self.assertEqual(report["groups"][0]["trials"][0]["first_zg_call"]["raw_arguments"], ARGS)

    def test_no_call_missing_and_error_remain_different(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup(root, [start("m1"), call(name="grep"), finish("m1")], extra_trials=[
                {"trial_id": "missing", "profile": "zg"}, {"trial_id": "error", "profile": "zg"}])
            jsonl(root / "error/agent/opencode.txt", [start("m1"), call(status="error"), finish("m1")])
            report = build_catalog([root], CASE)
            self.assertEqual([t["zg_adoption_observed"] for t in report["groups"][0]["trials"]], [False, None, True])
            labels, entries, rows = labels_for(report)
            scored = score_records(report, labels, entries, rows)
            self.assertEqual(scored["actual"][0]["assessment"]["status"], "unknown")
            self.assertEqual(len(scored["no_call_or_unknown_trials"]), 2)

    def test_invalid_arguments_are_preserved_without_fabricated_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup(root, [start("m1"), call(args='{"query": "unfinished"'), finish("m1")])
            report = build_catalog([root], CASE)
            self.assertEqual(len(report["request_catalog"]), 1)
            self.assertEqual(len(report["unreplayable_occurrences"]), 1)
            self.assertTrue(report["groups"][0]["trials"][0]["zg_adoption_observed"])

    def test_qoder_native_ids_link_results_without_rpc_and_repeated_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup(root)
            assistant = {"type": "assistant", "session_id": "q", "message": {"id": "m", "content": [
                {"type": "tool_use", "id": "c", "name": "mcp__zg__zvec_grep_search", "input": ARGS}]}}
            jsonl(root / "trial/agent/qodercli-stream.jsonl", [assistant, assistant,
                {"type": "user", "session_id": "q", "message": {"content": [{"type": "tool_result", "tool_use_id": "c", "content": TEXT}]}},
                {"type": "result", "session_id": "q"}])
            report = build_catalog([root], CASE)
            self.assertEqual(len(report["occurrences"]), 1)
            self.assertEqual(report["occurrences"][0]["public_text"], TEXT)
            self.assertEqual(report["occurrences"][0]["native_status"], "completed")

    def test_trajectory_fallback_is_not_claimed_as_complete_native_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup(root)
            write(root / "trial/agent/trajectory.json", {"steps": [{"source": "agent", "tool_calls": [
                {"tool_call_id": "c", "function_name": "zvec_grep_search", "arguments": ARGS}],
                "observation": {"results": [{"source_call_id": "c", "content": TEXT}]}}]})
            report = build_catalog([root], CASE)
            self.assertFalse(report["groups"][0]["trials"][0]["native_trace_complete"])
            self.assertEqual(report["occurrences"][0]["native_status"], "unknown")
            self.assertFalse(report["occurrences"][0]["agent_visible_observation"])

    def test_actual_and_replay_quality_are_distinct_and_first_failure_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup(root, [start("m1"), call(), finish("m1")])
            analysis = build_catalog([root], CASE)
            labels, entries, rows = labels_for(analysis)
            rid = analysis["occurrences"][0]["request_id"]
            next(r for r in rows if r["request_id"] == rid and r["repetition"] == 1)["status"] = "failed"
            scored = score_records(analysis, labels, entries, rows)
            self.assertEqual(scored["actual"][0]["assessment"]["target"]["rr_at_10"], 1)
            replay = next(r for r in scored["replays"] if r["request_id"] == rid)
            self.assertEqual(replay["context_assessments"][0]["assessment"]["status"], "unknown")
            self.assertIsNone(replay["stability"]["identical_all_five"])
            self.assertIsNone(scored["actual_vs_replay"][0]["original_vs_replay_text_identical"])

    def test_duplicate_mismatched_or_missing_replay_observations_are_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup(root, [start("m1"), call(), finish("m1")])
            analysis = build_catalog([root], CASE)
            labels, entries, rows = labels_for(analysis)
            rid = rows[0]["request_id"]
            rows[0]["mcp_request"] = {"name": "zvec_grep_search", "arguments": {"query": "different"}}
            rows.append(copy.deepcopy(rows[1]))
            rows[2]["public_sha256"] = "incorrect"
            rows[3]["text"] = "different from MCP content"
            rows.pop(4)
            scored = score_records(analysis, labels, entries, rows)
            replay = next(r for r in scored["replays"] if r["request_id"] == rid)
            self.assertEqual([o["status"] for o in replay["observations"]], [
                "request_mismatch", "ambiguous_duplicate", "public_hash_mismatch", "text_result_mismatch", "missing"])

    def test_identical_original_request_does_not_add_an_extra_qa_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = {"root": "/app", "query": CASE["question"], "limit": 10}
            setup(root, [start("m1"), call(args=args), finish("m1")])
            report = build_catalog([root], CASE)
            self.assertEqual(len(annotation_catalog(report, CASE)), 1)
            self.assertEqual(len(report["request_catalog"]), 1)
            self.assertTrue(report["annotation_catalog"][0]["also_faithful_request"])
            self.assertEqual(report["query_catalog"][0]["occurrence_count"], 1)

    def test_official_content_hash_differs_from_text_hash_and_raw_request_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup(root, [start("m1"), call(), finish("m1")])
            analysis = build_catalog([root], CASE)
            labels, entries, rows = labels_for(analysis)
            rid = analysis["occurrences"][0]["request_id"]
            scored = score_records(analysis, labels, entries, rows)
            replay = next(r for r in scored["replays"] if r["request_id"] == rid)
            self.assertTrue(replay["stability"]["identical_all_five"])
            first = replay["observations"][0]
            self.assertNotEqual(first["public_text_sha256"], first["public_content_sha256"])
            self.assertTrue(scored["actual_vs_replay"][0]["original_vs_replay_text_identical"])
            next(r for r in rows if r["request_id"] == rid and r["repetition"] == 1)["request"] = {"query": "altered"}
            failed = score_records(analysis, labels, entries, rows)
            self.assertEqual(next(r for r in failed["replays"] if r["request_id"] == rid)["observations"][0]["status"], "request_mismatch")


if __name__ == "__main__":
    unittest.main()
