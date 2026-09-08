from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from zg_bench.swe_qa.e2e_analysis import (adapt_atif, adapt_opencode, adapt_qoder, analyze_runs,
                                         analyze_trace, describe, numbered_lines, paired_description,
                                         render_markdown)


def case():
    text = "def find_dep():\n    return tracker()\n"
    return {"case_id": "case", "question": "How are dependencies found?", "repo": {"url": "https://example.test/repo", "commit": "a" * 40},
            "evidence": [{"id": "proof", "path": "src/base.py", "start_line": 10, "end_line": 11, "text": text,
                          "sha256": hashlib.sha256(text.encode()).hexdigest()}], "sufficient_sets": [["proof"]]}


def entries():
    return {"case_id": "case", "repo": {"commit": "a" * 40}, "targets": [
        {"target_id": "entry", "level": "function", "path": "src/base.py", "symbol": "find_dep", "primary": True,
         "definition_line": 10, "entry_start_line": 10, "entry_end_line": 11, "definition": "def find_dep():\n"}]}


def start(message):
    return {"type": "step_start", "part": {"messageID": message}}


def finish(message, inp=10, cached=20, output=3):
    return {"type": "step_finish", "part": {"messageID": message, "reason": "tool-calls", "tokens":
                                             {"input": inp, "output": output, "reasoning": 2, "cache": {"read": cached, "write": 4}}}}


def tool(call_id, name="read", text="", args=None, message="m1", status="completed", error=None):
    state = {"status": status, "input": args or {}, "output": text}
    if error is not None:
        state.pop("output")
        state["error"] = error
    return {"type": "tool_use", "part": {"messageID": message, "callID": call_id, "tool": name, "state": state}}


class E2EAnalysisTests(unittest.TestCase):
    def test_native_tool_errors_are_cost_but_not_success_and_usage_is_not_double_counted(self):
        events = [start("m1"), tool("bad", "zvec_grep_search", args={"query": "dependencies"}, status="error",
                                      error="Model tried to call unavailable tool 'zvec_grep_search'."), finish("m1"),
                  start("m2"), tool("good", "zvec_grep_zvec_grep_search", "freshness: fresh\n", {"query": "dependencies"}, "m2"), finish("m2")]
        trace = analyze_trace(adapt_opencode(events), case())
        self.assertEqual(trace["metrics"]["tool_calls_attempted"], 2)
        self.assertEqual(trace["metrics"]["tool_calls_successful"], 1)
        self.assertEqual(trace["metrics"]["search_calls_successful"], 1)
        self.assertEqual(trace["metrics"]["unavailable_tool_errors"], 1)
        self.assertEqual(trace["native_turn_totals"]["input_tokens"], 60)
        self.assertEqual(trace["native_turn_totals"]["cache_write_tokens"], 8)
        bad = next(x for x in trace["timeline"] if x.get("call_id") == "bad")
        self.assertFalse(bad["execution_confirmed"])
        self.assertIn("unavailable tool", bad["error"])
        self.assertGreater(bad["visible_bytes"], 0)

    def test_terminal_tool_states_and_finish_events_count_once(self):
        events = [start("m1"), tool("one", status="running"), tool("one", text="1: foo\n"), finish("m1"), finish("m1")]
        trace = analyze_trace(adapt_opencode(events), case())
        self.assertEqual(trace["metrics"]["tool_calls_attempted"], 1)
        self.assertEqual(trace["metrics"]["model_turns"], 1)
        self.assertEqual(trace["native_turn_totals"]["input_tokens"], 30)

    def test_discovery_includes_entry_request_then_expansion_and_final_generation(self):
        events = [start("m1"), tool("locate", text="<path>/app/src/base.py</path>\n10: def find_dep():\n", args={"filePath": "/app/src/base.py"}), finish("m1"),
                  start("m2"), tool("expand", text="11:     return tracker()\n", args={"filePath": "/app/src/base.py"}, message="m2"), finish("m2"),
                  start("m3"), {"type": "text", "part": {"messageID": "m3", "text": "Answer"}}, finish("m3")]
        trace = analyze_trace(adapt_opencode(events), case(), entries())
        self.assertEqual(trace["first_useful_entry"]["call_id"], "locate")
        self.assertEqual([trace["phases"][x]["input_tokens"] for x in ("discovery", "expansion", "final_generation")], [30, 30, 30])
        self.assertEqual(trace["first_useful_entry"]["cost_through_entry"]["tool_calls_attempted"], 1)
        self.assertTrue(trace["source_line_union_diagnostic"]["by_evidence"][0]["all_nonblank_lines_observed_across_outputs"])

    def test_actual_numbered_lines_not_parent_range_drive_read_and_union_counts(self):
        text = "#1 score=1 matchedBy=fts src/base.py:1-1000\nsource:\n10: def find_dep():\n"
        self.assertEqual(numbered_lines(text), [("src/base.py", 10, "def find_dep():")])
        events = [start("m1"), tool("one", text="10: def find_dep():\n", args={"filePath": "/app/src/base.py", "offset": 1, "limit": 1000}),
                  tool("two", text="10: def find_dep():\n11:     return tracker()\n", args={"filePath": "/app/src/base.py"}),
                  tool("wrong", text="11:     return a_different_function()\n", args={"filePath": "/app/src/base.py"}), finish("m1")]
        trace = analyze_trace(adapt_opencode(events), case(), entries())
        self.assertEqual(trace["metrics"]["read_unique_lines"], 3)  # distinct content at line11 is another visible triple.
        self.assertEqual(trace["metrics"]["read_repeated_lines"], 1)
        self.assertEqual(trace["actual_read_ranges"], [{"path": "src/base.py", "start_line": 10, "end_line": 11}])
        self.assertEqual(trace["source_line_union_diagnostic"]["observed_nonblank_lines"], 2)

    def test_filename_comment_and_parent_outline_do_not_create_primary_entry(self):
        text = "#1 score=1 matchedBy=fts src/base.py:1-999\noutline:\ndef find_dep():\nsource:\n3: # def find_dep():\n"
        trace = analyze_trace(adapt_opencode([start("m1"), tool("s", "zvec_grep_search", text, {"query": "find_dep"}), finish("m1")]), case(), entries())
        self.assertIsNone(trace["first_useful_entry"])
        self.assertEqual(trace["source_line_union_diagnostic"]["observed_nonblank_lines"], 0)

    def test_repeated_attempt_and_success_counts_are_separate_and_empty_is_explicit(self):
        args = {"pattern": "needle", "path": "/app"}
        events = [start("m1"), tool("a", "grep", args=args, status="error", error="tool error"),
                  tool("b", "grep", "No matches found", args), tool("c", "grep", "No matches found", args), finish("m1")]
        trace = analyze_trace(adapt_opencode(events), case())
        self.assertEqual(trace["metrics"]["repeated_search_attempts"], 2)
        self.assertEqual(trace["metrics"]["repeated_successful_searches"], 1)
        self.assertEqual(trace["metrics"]["empty_searches_confirmed"], 2)
        self.assertEqual(trace["metrics"]["search_emptiness_unknown"], 1)

    def test_qoder_messages_deduplicate_usage_and_ignore_session_total(self):
        usage = {"input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 80, "cache_creation_input_tokens": 2}
        message = {"id": "m", "usage": usage, "content": [{"type": "tool_use", "id": "c", "name": "Grep", "input": {"pattern": "x"}}]}
        events = [{"type": "assistant", "message": message}, {"type": "assistant", "message": message},
                  {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c", "is_error": True, "content": "Unknown tool Grep"}]}},
                  {"type": "result", "usage": {"input_tokens": 99999}}]
        trace = analyze_trace(adapt_qoder(events), case())
        self.assertEqual(trace["metrics"]["model_turns"], 1)
        self.assertEqual(trace["metrics"]["tool_calls_attempted"], 1)
        self.assertEqual(trace["native_turn_totals"]["input_tokens"], 100)
        self.assertIsNone(trace["native_turn_totals"]["native_input_tokens"])
        self.assertEqual(trace["metrics"]["tool_calls_error"], 1)

    def test_qoder_masked_zero_and_missing_turn_usage_are_unknown(self):
        events = [{"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 0, "output_tokens": 0}, "content": []}},
                  {"type": "assistant", "message": {"id": "m2", "usage": {"input_tokens": 20, "output_tokens": 3}, "content": []}}]
        trace = analyze_trace(adapt_qoder(events), case(), entries())
        self.assertIsNone(trace["native_turn_totals"]["input_tokens"])
        self.assertEqual(trace["phases"]["discovery"]["input_tokens_missing_turns"], 1)
        self.assertEqual(trace["phases"]["discovery"]["input_tokens_observed_lower_bound"], 20)

    def test_qoder_scopes_prevent_parent_child_call_collision(self):
        events = []
        for scope, name in ((None, "Read"), ("child", "Grep")):
            events.extend([{"type": "assistant", "parent_tool_use_id": scope, "message": {"id": "m", "content": [{"type": "tool_use", "id": "c", "name": name}]}},
                           {"type": "user", "parent_tool_use_id": scope, "message": {"content": [{"type": "tool_result", "tool_use_id": "c", "content": "result"}]}}])
        trace = analyze_trace(adapt_qoder(events), case())
        self.assertEqual(trace["metrics"]["model_turns"], 2)
        self.assertEqual(trace["metrics"]["tool_calls_successful"], 2)

    def test_atif_output_without_native_state_is_not_fabricated_success(self):
        trajectory = {"steps": [{"source": "agent", "step_id": 1, "tool_calls": [{"tool_call_id": "c", "function_name": "read", "arguments": {}}],
                                "observation": {"results": [{"source_call_id": "c", "content": "text"}]}, "metrics": {"prompt_tokens": 40}}]}
        trace = analyze_trace(adapt_atif(trajectory), case())
        self.assertEqual(trace["metrics"]["tool_calls_unknown_status"], 1)
        self.assertEqual(trace["metrics"]["tool_calls_successful"], 0)
        self.assertEqual(trace["native_turn_totals"]["input_tokens"], 40)
        self.assertIsNone(trace["native_turn_totals"]["native_input_tokens"])

    def test_descriptive_pairs_keep_failed_arms_unknowns_and_do_not_mix_models(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            planned = [{"trial_id": f"{model}-{profile}", "profile": profile, "agent": "opencode", "model": model, "block_id": 1}
                       for model in ("glm", "qwen") for profile in ("baseline", "zvec-grep")]
            (root / "plan.json").write_text(json.dumps({"trials": planned}))
            trial = root / "glm-baseline"
            (trial / "agent").mkdir(parents=True)
            (trial / "result.json").write_text(json.dumps({"status": "timeout", "final_metrics": {"total_prompt_tokens": 30}, "wall_seconds": 9}))
            (trial / "agent" / "opencode.txt").write_text("\n".join(json.dumps(x) for x in [start("m1"), tool("c"), finish("m1")]))
            report = analyze_runs(root, case(), entries())
            self.assertEqual(len(report["trials"]), 4)
            self.assertEqual(len(report["configurations"]), 2)
            self.assertEqual(report["trials"][0]["status"], "timeout")
            self.assertEqual(report["trials"][1]["status"], "missing")
            self.assertIsNone(report["trials"][1]["metrics"]["tool_calls_attempted"])
            self.assertEqual(report["configurations"][0]["profiles"]["baseline"]["all_planned_metrics"]["input_tokens"]["values"], [30])
            pairs = report["configurations"][0]["within_block_cost_differences"]
            self.assertEqual(pairs["all_planned_pairs"]["input_tokens"]["missing"], 1)
            self.assertEqual(pairs["completed_pairs_only_secondary"]["input_tokens"]["known"], 0)
            self.assertIn("timeout", render_markdown(report))

    def test_pair_values_include_five_prescheduled_blocks_and_sd_cv(self):
        rows = []
        for i in range(1, 6):
            for profile, cost in (("baseline", 100 + i), ("zvec-grep", 100)):
                rows.append({"trial_id": f"{i}-{profile}", "profile": profile, "block_id": i, "status": "completed", "metrics": {"input_tokens": cost}})
        paired = paired_description(rows)
        summary = paired["all_planned_pairs"]["input_tokens"]
        self.assertEqual(summary["values"], [1, 2, 3, 4, 5])
        self.assertEqual(summary["zg_lower_cost_count"], 5)
        self.assertEqual(summary["mean"], 3)
        self.assertEqual(summary["median"], 3)
        self.assertGreater(summary["cv"], 0)
        self.assertEqual(describe([0, None])["missing"], 1)
        self.assertIsNone(describe([0, 0])["cv"])

    def test_integrity_failure_survives_completed_trajectory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "plan.json").write_text(json.dumps({"trials": [{"trial_id": "one", "profile": "zvec-grep"}]}))
            (root / "one" / "agent").mkdir(parents=True)
            (root / "one" / "result.json").write_text(json.dumps({"status": "completed", "original_seed_unchanged": False}))
            (root / "one" / "agent" / "trajectory.json").write_text(json.dumps({"steps": []}))
            self.assertEqual(analyze_runs(root, case())["trials"][0]["status"], "integrity_failure")

    def test_source_annotation_identity_mismatch_is_rejected(self):
        mismatched = entries()
        mismatched["repo"]["commit"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "pinned source"):
            analyze_runs(Path("unused"), case(), mismatched)

    def test_unnumbered_read_cost_is_not_zero_and_missing_annotations_do_not_invent_discovery(self):
        adapted = adapt_opencode([start("m1"), tool("read", text="def find_dep():\n", args={"filePath": "/app/src/base.py"}), finish("m1")])
        trace = analyze_trace(adapted, case())
        self.assertIsNone(trace["metrics"]["read_unique_lines"])
        self.assertEqual(trace["metrics"]["read_unique_lines_observed_lower_bound"], 0)
        self.assertIsNone(trace["phases"]["discovery"]["input_tokens"])
        self.assertEqual(trace["phases"]["unclassified"]["input_tokens"], 30)
        self.assertEqual(adapted["timeline"][1]["text"], "def find_dep():\n")

    def test_conflicting_visible_path_and_argument_do_not_prove_annotated_lines(self):
        text = "<path>/app/src/base.py</path>\n10: def find_dep():\n"
        self.assertEqual(numbered_lines(text, "/app/other.py"), [])
        self.assertEqual(numbered_lines("10→def find_dep():\n", "/app/src/base.py"), [("src/base.py", 10, "def find_dep():")])

    def test_native_grep_can_find_entry_before_any_read(self):
        text = "Found 1 matches\n/app/src/base.py:\n  Line 10: def find_dep():\n"
        trace = analyze_trace(adapt_opencode([start("m1"), tool("grep", "grep", text, {"pattern": "find_dep"}), finish("m1")]), case(), entries())
        self.assertEqual(trace["first_useful_entry"]["call_id"], "grep")

    def test_wire_totals_are_supplemental_and_incomplete_responses_stay_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "plan.json").write_text(json.dumps({"trials": [{"trial_id": "one", "profile": "baseline"}]}))
            agent = root / "one" / "agent"
            agent.mkdir(parents=True)
            (agent.parent / "result.json").write_text(json.dumps({"status": "completed", "final_metrics": {"total_prompt_tokens": 30}}))
            wire = [{"event": "request", "request_id": "one", "model": "glm", "temperature": 0.1},
                    {"event": "response", "request_id": "one", "usage": {"prompt_tokens": 90, "completion_tokens": 5}}]
            (agent / "wire.jsonl").write_text("\n".join(json.dumps(x) for x in wire))
            trial = analyze_runs(root, case())["trials"][0]
            self.assertEqual(trial["metrics"]["input_tokens"], 30)
            self.assertEqual(trial["provider_trace"]["provider_all_request_usage"]["prompt_tokens"], 90)
            with (agent / "wire.jsonl").open("a") as out:
                out.write('\n{"event":"request","request_id":"unfinished"}')
            trial = analyze_runs(root, case())["trials"][0]
            self.assertIsNone(trial["provider_trace"]["provider_all_request_usage"]["prompt_tokens"])

    def test_calibrated_quality_review_is_preferred_and_never_filters_planned_trials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = {"trials": [{"trial_id": name, "profile": "baseline"} for name in ("a", "b", "c")]}
            (root / "plan.json").write_text(json.dumps(plan))
            (root / "judged.json").write_text(json.dumps({"case_id": "case", "trials": [{"trial_id": "a", "quality": "pass"}]}))
            gate = {"both_judges_calibrated": False, "all_planned_answers_pass": False, "not_a_noninferiority_test": True}
            review = {"schema_version": 2, "case_id": "case", "quality_gate": gate, "trials": [
                {"trial_id": "a", "quality": "uncalibrated", "raw_consensus": "pass", "consensus_status": "uncalibrated", "judgments": {}},
                {"trial_id": "b", "quality": "disagreement", "consensus_status": "disagreement", "judgments": {}}]}
            (root / "quality-review.json").write_text(json.dumps(review))
            report = analyze_runs(root, case())
            self.assertEqual(len(report["trials"]), 3)
            self.assertEqual([x["quality_status"] for x in report["trials"]], ["uncalibrated", "disagreement", "unscored"])
            self.assertEqual(report["quality_gate"], gate)
            self.assertEqual(report["quality_review_source"]["protocol"], "readonly-source-qa-v2-calibrated")
            self.assertIn("uncalibrated=1", render_markdown(report))
            self.assertEqual(report["configurations"][0]["profiles"]["baseline"]["planned_count"], 3)

    def test_legacy_quality_fallback_and_wrong_review_plan_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "plan.json").write_text(json.dumps({"trials": [{"trial_id": "a", "profile": "baseline"}]}))
            (root / "judged.json").write_text(json.dumps({"case_id": "case", "trials": [{"trial_id": "a", "quality": "fail"}]}))
            report = analyze_runs(root, case())
            self.assertEqual(report["trials"][0]["quality_status"], "fail")
            self.assertEqual(report["quality_review_source"]["protocol"], "legacy-single-judge")
            (root / "quality-review.json").write_text(json.dumps({"case_id": "case", "plan_sha256": "wrong", "trials": []}))
            with self.assertRaisesRegex(ValueError, "plan hash"):
                analyze_runs(root, case())


if __name__ == "__main__":
    unittest.main()
