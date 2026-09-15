from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from zg_bench.swe_qa.native_query_diagnosis import build_catalog, build_decision_catalog, score_records, screening_evidence
from zg_bench.swe_qa.prompt_diagnostics import sha
from zg_bench.swe_qa.query_ground_truth import annotation_catalog


CASE = {"case_id": "fixture", "question": "How is the target computed?", "repo": {"commit": "a" * 40, "url": "https://example.test/fixture"}}
ARGS = {"root": "/app", "query": "target computation", "fts": ["symbol", "fallback"], "limit": 10}
TEXT = "freshness: fresh\n#1 [group_coverage: q1] matchedBy=vector src/x.py:10-12\ngroups: q1#1 (vector)\nsource:\n10\tdef target():\n11\t    pass\n"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def jsonl(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(v) for v in values) + "\n")


def native_call(mid, cid, args=ARGS, text=TEXT, name="zvec_grep_zvec_grep_search"):
    return {"type": "tool_use", "part": {"messageID": mid, "callID": cid, "tool": name,
            "state": {"input": args, "output": text, "status": "completed"}}}


def setup_runs(root, *, duplicate=False, parallel=False, text=TEXT):
    write(root / "plan.json", {"case_id": "fixture", "group_id": "opencode-glm52", "trials": [
        {"trial_id": "trial", "profile": "zvec-grep-current", "arm": "C", "prompt_version": "P00", "trajectory_path": "trial/agent/trajectory.json"}]})
    events = [{"type": "step_start", "part": {"messageID": "m0"}}, native_call("m0", "grep", {"pattern": "target"}, "No matches.", "grep"),
              {"type": "step_finish", "part": {"messageID": "m0", "reason": "tool-calls"}},
              {"type": "step_start", "part": {"messageID": "m1"}}, native_call("m1", "search1", text=text)]
    count = 2 if duplicate else 1
    if duplicate:
        if not parallel:
            events.append({"type": "step_start", "part": {"messageID": "m2"}})
        events.append(native_call("m1" if parallel else "m2", "search2", text=text))
    events.append({"type": "step_finish", "part": {"messageID": "m2" if duplicate and not parallel else "m1", "reason": "stop"}})
    jsonl(root / "trial/agent/opencode.txt", events)
    rpc = []
    for index in range(count):
        rpc.extend([{"direction": "agent_to_zg", "message": {"jsonrpc": "2.0", "id": index + 1, "method": "tools/call", "params": {"name": "zvec_grep_search", "arguments": ARGS}}},
                    {"direction": "zg_to_agent", "message": {"jsonrpc": "2.0", "id": index + 1, "result": {"content": [{"type": "text", "text": TEXT}]}}}])
    if parallel:
        rpc = [rpc[0], rpc[2], rpc[1], rpc[3]]
    jsonl(root / "trial/agent/native-mcp.jsonl", rpc)


def labels_for(analysis):
    target = {"target_id": "target", "level": "function", "path": "src/x.py", "symbol": "target", "definition": "def target():",
              "definition_line": 10, "entry_start_line": 10, "entry_end_line": 12, "primary": True}
    labels = {"schema_version": 2, "labels_id": "fixture-labels", "repo": CASE["repo"], "targets": [target], "queries": [], "request_bindings": [],
              "annotation_provenance": {"kind": "model_assisted_source_verified_cross_review"}}
    for annotation in analysis["annotation_catalog"]:
        labels["queries"].append({"query_id": annotation["annotation_id"], "context_id": annotation["context_id"], "text": "fixture",
            "classification": "original" if annotation["kind"] == "original" else "legitimate_subgoal", "annotation_status": "reviewed",
            "goal": "Locate the target computation", "accepted_target_ids": ["target"], "bridge_target_ids": [], "task_fact_ids": []})
        labels["request_bindings"].append({"request": annotation["request"], "context_id": annotation["context_id"], "query_id": annotation["annotation_id"]})
    entries = {"repo": CASE["repo"], "protocol": {"limit": 10}, "targets": [target], "groups": []}
    replays = [{"request_id": request["request_id"], "repetition": repetition, "status": "completed", "text": TEXT,
                "result": {"content": [{"type": "text", "text": TEXT}]}}
               for request in analysis["request_catalog"] for repetition in range(1, 6)]
    return labels, entries, replays


def setup_decisions(root, *, multiple=False):
    samples, requests = [], {}
    for version in ("P00", "P10"):
        sample = {"sample_id": version + "-r01", "request_key": version, "state_id": "same-source-state", "group_id": "opencode-glm52", "variant": version, "repetition": 1}
        samples.append(sample)
        requests[version] = {"request": {"messages": [{"role": "system", "content": version}, {"role": "user", "content": CASE["question"]}], "tools": []}}
    plan = {"requests": requests, "samples": samples}
    plan["plan_sha256"] = sha(plan)
    write(root / "plan.json", plan)
    for sample in samples:
        call = {"name": "zvec_grep_zvec_grep_search", "arguments": ARGS, "raw_arguments": json.dumps(ARGS), "is_zg": True, "valid": True, "call_id": "call1"}
        calls = [call, {**call, "call_id": "call2"}] if multiple else [call]
        write(root / "samples" / sample["sample_id"] / "result.json", {"status": "completed", "plan_sha256": plan["plan_sha256"], "validation": {"calls": calls}})
    return root / "plan.json"


class NativeQueryDiagnosisTests(unittest.TestCase):
    def test_real_mcp_request_links_to_prior_round_without_current_output_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup_runs(root)
            report = build_catalog([root], CASE)
            self.assertEqual(len(annotation_catalog(report, CASE)), 2)
            occurrence = report["occurrences"][0]
            self.assertEqual(occurrence["native_link_status"], "matched")
            self.assertEqual(occurrence["native_call_id"], "search1")
            annotation = next(a for a in report["annotation_catalog"] if a["kind"] == "faithful")
            self.assertEqual(annotation["prior_turn_feedback"][0]["visible_text"], "No matches.")
            self.assertNotIn(TEXT, json.dumps({k: v for k, v in annotation.items() if k != "occurrences"}))
            request = next(r for r in report["request_catalog"] if r["kind"] == "faithful")
            self.assertEqual(request["mcp_request"], {"name": "zvec_grep_search", "arguments": ARGS})
            self.assertEqual(request["request"], ARGS)
            self.assertNotIn("autoUpdate", request["request"])

    def test_sequential_duplicates_can_link_but_parallel_duplicates_remain_ambiguous(self):
        for parallel in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                setup_runs(root, duplicate=True, parallel=parallel)
                report = build_catalog([root], CASE)
                self.assertEqual(len(report["occurrences"]), 2)
                expected = "ambiguous" if parallel else "matched"
                self.assertEqual({o["native_link_status"] for o in report["occurrences"]}, {expected})
                faithful = [a for a in report["annotation_catalog"] if a["kind"] == "faithful"]
                self.assertEqual(len(faithful), 2)
                if parallel:
                    self.assertTrue(all(a["context_capture_status"] == "unknown" for a in faithful))

    def test_agent_visible_mismatch_is_not_silently_linked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup_runs(root, text="Agent truncated the output")
            report = build_catalog([root], CASE)
            self.assertEqual(report["occurrences"][0]["native_link_status"], "unknown")
            labels, entries, rows = labels_for(report)
            scored = score_records(report, labels, entries, rows)
            self.assertFalse(scored["actual"][0]["agent_visible_link_verified"])

    def test_release_ranked_format_scores_with_unchanged_request_and_five_repeats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup_runs(root)
            analysis = build_catalog([root], CASE)
            labels, entries, rows = labels_for(analysis)
            scored = score_records(analysis, labels, entries, rows)
            self.assertTrue(scored["actual"][0]["assessment"]["target"]["hit_at_1"])
            self.assertEqual(scored["actual"][0]["assessment"]["target"]["rr_at_10"], 1)
            self.assertTrue(all(r["stability"]["identical_all_five"] for r in scored["replays"]))
            self.assertTrue(scored["actual_vs_replay"][0]["original_vs_replay_text_identical"])
            self.assertNotIn("bytes_through_first_hit", json.dumps(scored))

    def test_first_failed_replay_is_not_replaced_by_later_hits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup_runs(root)
            analysis = build_catalog([root], CASE)
            labels, entries, rows = labels_for(analysis)
            rows[0]["status"] = "failed"
            scored = score_records(analysis, labels, entries, rows)
            self.assertEqual(scored["replays"][0]["context_assessments"][0]["assessment"]["status"], "unknown")
            self.assertIsNone(scored["replays"][0]["stability"]["identical_all_five"])

    def test_unknown_output_duplicate_or_missing_repeats_stay_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup_runs(root)
            analysis = build_catalog([root], CASE)
            labels, entries, rows = labels_for(analysis)
            rows[0].update(text="unsupported native output", result=None)
            rows.append(copy.deepcopy(rows[1]))
            rows.pop(4)
            scored = score_records(analysis, labels, entries, rows)
            replay = scored["replays"][0]
            self.assertEqual(replay["context_assessments"][0]["assessment"]["status"], "unknown")
            self.assertEqual(replay["observations"][1]["status"], "ambiguous_duplicate")
            self.assertEqual(replay["observations"][4]["status"], "missing")

    def test_decision_variants_share_state_labels_and_use_actual_response_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = setup_decisions(Path(directory))
            analysis = build_decision_catalog(plan, CASE)
            self.assertEqual(len(analysis["occurrences"]), 2)
            self.assertEqual(len(analysis["annotation_catalog"]), 2)
            self.assertEqual(len(analysis["request_catalog"]), 2)
            self.assertEqual({o["native_link_status"] for o in analysis["occurrences"]}, {"provider_decision"})
            self.assertEqual(next(a for a in analysis["annotation_catalog"] if a["kind"] == "faithful")["prior_turn_feedback"], [])
            labels, entries, replays = labels_for(analysis)
            evidence = screening_evidence(plan, analysis, labels, entries, replays)
            self.assertTrue(all(r["goal_correct"] is True and r["source_verified"] is True for r in evidence["rows"]))
            self.assertTrue(all(r["retrieval"]["hit_at_10"] for r in evidence["rows"]))

    def test_unreviewed_labels_and_multiple_query_decisions_cannot_auto_pass_screen(self):
        for multiple in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                plan = setup_decisions(Path(directory), multiple=multiple)
                analysis = build_decision_catalog(plan, CASE)
                labels, entries, replays = labels_for(analysis)
                if not multiple:
                    for query in labels["queries"]:
                        query["annotation_status"] = "unknown"
                evidence = screening_evidence(plan, analysis, labels, entries, replays)
                self.assertTrue(all(r["goal_correct"] is None and r["source_verified"] is False for r in evidence["rows"]))

    def test_case_and_source_revision_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup_runs(root)
            with self.assertRaisesRegex(ValueError, "case differ"):
                build_catalog([root], {**CASE, "case_id": "other"})
            analysis = build_catalog([root], CASE)
            labels, entries, rows = labels_for(analysis)
            entries["repo"] = {}
            with self.assertRaisesRegex(ValueError, "source revision"):
                score_records(analysis, labels, entries, rows)


if __name__ == "__main__":
    unittest.main()
