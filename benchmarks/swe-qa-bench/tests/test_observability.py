from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from zg_bench.swe_qa.observability import (
    analyze_runs,
    analyze_retrieval,
    analyze_trajectory,
    classify_call,
    main,
    score_retrieval_event,
    native_tool_states,
    validate_case,
    visible_evidence,
)


def _case() -> dict:
    evidence = []
    for evidence_id, path, text in (
        ("entry", "src/api.py", "def answer():\n    return resolve()"),
        ("implementation", "src/core.py", "def resolve():\n    return 42"),
        ("alternative", "docs/design.md", "The answer API delegates to resolve, which returns 42."),
    ):
        evidence.append({"id": evidence_id, "path": path, "start_line": 1,
                         "end_line": len(text.splitlines()), "text": text,
                         "sha256": hashlib.sha256(text.encode()).hexdigest()})
    return {"case_id": "demo:1", "question": "How is the answer produced?",
            "repo": {"url": "https://example.test/demo.git", "commit": "a" * 40},
            "evidence": evidence, "sufficient_sets": [["entry", "implementation"], ["alternative"]]}


def _step(index: int, path: str, text: str, *, prompt: int | None = 100) -> dict:
    value = {"step_id": index, "source": "agent", "llm_call_count": 1,
             "tool_calls": [{"tool_call_id": f"call-{index}", "function_name": "read",
                             "arguments": {"filePath": path}}],
             "observation": {"results": [{"source_call_id": f"call-{index}", "content": text}]}}
    if prompt is not None:
        value["metrics"] = {"prompt_tokens": prompt}
    return value


class ObservabilityTest(unittest.TestCase):
    def test_native_unknown_tool_error_survives_missing_atif_observation(self):
        # Minimal regression fixture taken from OpenCode's real state:error
        # shape; Harbor ATIF kept this tool call but omitted its error result.
        native = [
            {"type": "tool_use", "part": {"tool": "zvec_grep_search", "callID": "bad",
                "state": {"status": "error", "input": {"query": "find computation"},
                          "error": "Model tried to call unavailable tool 'invalid'. Available tools: read, zvec_grep_zvec_grep_search."}}},
            {"type": "tool_use", "part": {"tool": "zvec_grep_zvec_grep_search", "callID": "good",
                "state": {"status": "completed", "input": {"query": "find computation"}, "output": "source preview"}}},
        ]
        trajectory = {"steps": [{"source": "agent", "step_id": 1, "llm_call_count": 1,
            "tool_calls": [{"tool_call_id": "bad", "function_name": "zvec_grep_search", "arguments": {"query": "find computation"}},
                           {"tool_call_id": "good", "function_name": "zvec_grep_zvec_grep_search", "arguments": {"query": "find computation"}}],
            "observation": {"results": [{"source_call_id": "good", "content": "source preview"}]}}]}
        report = analyze_trajectory(trajectory, _case(), native_events=native)
        self.assertEqual(report["tool_calls"], 2)
        self.assertEqual(report["attempted_logical_queries"], 2)
        self.assertEqual(report["executed_logical_queries"], 1)
        self.assertEqual(report["zg_tool_calls_attempted"], 2)
        self.assertEqual(report["zg_tool_calls_executed"], 1)
        self.assertEqual(report["unavailable_tool_errors"], 1)
        self.assertTrue(report["calls"][0]["is_error"])
        self.assertFalse(report["calls"][0]["execution_confirmed"])
        self.assertIn("unavailable tool", report["calls"][0]["execution_error"])
        self.assertEqual(report["native_only_error_text_bytes"], len(native[0]["part"]["state"]["error"].encode()))
        self.assertEqual(report["returned_text_bytes"], len("source preview"))
        self.assertEqual(report["calls_without_observations"], 1)

    def test_ambiguous_native_error_does_not_invent_zero_executed_queries(self):
        event = {"type": "tool_use", "part": {"tool": "grep", "callID": "x", "state": {"status": "error", "error": "transport timeout"}}}
        trajectory = {"steps": [{"source": "agent", "tool_calls": [{"tool_call_id": "x", "function_name": "grep", "arguments": {"pattern": "thing"}}]}]}
        report = analyze_trajectory(trajectory, _case(), native_events=[event])
        self.assertTrue(report["calls"][0]["is_error"])
        self.assertIsNone(report["executed_logical_queries"])
        self.assertEqual(report["confirmed_executed_logical_queries_lower_bound"], 0)
        # A later partial stream update cannot erase a terminal error.
        pending = {"type": "tool_use", "part": {"tool": "grep", "callID": "x", "state": {"status": "running"}}}
        self.assertEqual(native_tool_states([event, pending])["x"]["status"], "error")

    def test_native_error_only_zg_trial_is_retained_with_zero_backend_searches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial_id = "demo-1-r05-zvec-grep"
            agent = root / trial_id / "agent"
            agent.mkdir(parents=True)
            (root / "plan.json").write_text(json.dumps({"case_id": "demo:1", "trials": [{"trial_id": trial_id, "profile": "zvec-grep", "trajectory_path": f"{trial_id}/agent/trajectory.json"}]}))
            (agent.parent / "result.json").write_text(json.dumps({"status": "completed", "final_metrics": {"total_prompt_tokens": 100}}))
            (agent / "trajectory.json").write_text(json.dumps({"steps": [{"source": "agent", "tool_calls": [{"tool_call_id": "bad", "function_name": "zvec_grep_search", "arguments": {"query": "thing"}}]}]}))
            (agent / "opencode.txt").write_text(json.dumps({"type": "tool_use", "part": {"tool": "zvec_grep_search", "callID": "bad", "state": {"status": "error", "error": "Model tried to call unavailable tool 'invalid'."}}}) + "\n")
            (agent / "zg-trace.jsonl").write_text(json.dumps({"event": "start"}) + "\n")
            report = analyze_runs(runs_dir=root, case=_case(), profile="zvec-grep", expected_trials=1)
            row = report["profiles"]["zvec-grep"]["trials"][0]
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["trajectory"]["tool_calls"], 1)
            self.assertEqual(row["trajectory"]["executed_logical_queries"], 0)
            self.assertEqual(row["successful_zg_backend_searches"], 0)
            self.assertTrue(row["zero_successful_zg_search_observed"])
            self.assertEqual(report["profiles"]["zvec-grep"]["trials_with_zero_successful_zg_search_observed"], [trial_id])

    def test_gold_rejects_corrupt_hash_and_inconsistent_span(self):
        case = _case()
        validate_case(case)
        case["evidence"][0]["sha256"] = "bad"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            validate_case(case)
        case = _case()
        case["evidence"][0]["end_line"] = 100
        with self.assertRaisesRegex(ValueError, "line range"):
            validate_case(case)

    def test_filename_and_partial_text_are_not_sufficient(self):
        case = _case()
        self.assertEqual(visible_evidence("src/api.py", {}, case), [])
        self.assertEqual(visible_evidence("def answer():", {"filePath": "src/api.py"}, case), [])
        self.assertEqual(visible_evidence(case["evidence"][0]["text"], {}, case), [])
        self.assertEqual(visible_evidence("1|def answer():\n2|    return resolve()", {"filePath": "src/api.py"}, case), ["entry"])
        self.assertEqual(visible_evidence("1\tdef answer():\n2\t    return resolve()", {"filePath": "src/api.py"}, case), ["entry"])

    def test_cumulative_or_of_and_and_first_sufficient_milestone(self):
        case = _case()
        steps = [_step(i + 1, e["path"], e["text"]) for i, e in enumerate(case["evidence"][:2])]
        first = analyze_trajectory({"steps": steps[:1]}, case)
        self.assertFalse(first["evidence_sufficient"])
        result = analyze_trajectory({"steps": steps}, case)
        self.assertTrue(result["evidence_sufficient"])
        self.assertEqual(result["first_sufficient_evidence"]["step_id"], 2)
        self.assertEqual(result["first_sufficient_evidence"]["step_prompt_tokens_through_request"], 200)
        alternate = case["evidence"][2]
        result = analyze_trajectory({"steps": [_step(1, alternate["path"], alternate["text"])]}, case)
        self.assertTrue(result["evidence_sufficient"])

    def test_missing_step_usage_is_na_not_zero(self):
        case = _case()
        evidence = case["evidence"][2]
        step = _step(1, evidence["path"], evidence["text"], prompt=None)
        result = analyze_trajectory({"steps": [step]}, case)
        self.assertIsNone(result["first_sufficient_evidence"]["step_prompt_tokens_through_request"])
        del step["llm_call_count"]
        self.assertIsNone(analyze_trajectory({"steps": [step]}, case)["model_requests"])

    def test_repeated_visible_text_does_not_invent_token_counts(self):
        case = _case()
        evidence = case["evidence"][0]
        result = analyze_trajectory({"steps": [_step(i, evidence["path"], evidence["text"]) for i in (1, 2)]}, case)
        size = len(evidence["text"].encode())
        self.assertEqual(result["returned_text_bytes"], size * 2)
        self.assertEqual(result["exact_repeated_observation_bytes"], size)
        self.assertIsNone(result["returned_text_tokens"])

    def test_batch_shell_counts_searches_not_mentions(self):
        result = classify_call("bash", {"command": "rg alpha src && rg beta lib"})
        self.assertEqual(result["logical_queries"], 2)
        self.assertTrue(result["query_count_complete"])
        self.assertEqual(classify_call("bash", {"command": "echo 'rg alpha src'"})["logical_queries"], 0)
        result = classify_call("bash", {"command": "python search.py"})
        self.assertEqual(result["category"], "unknown")
        self.assertIsNone(result["logical_queries"])
        self.assertEqual(classify_call("mcp__zg__context", {"queries": ["one", "two"]})["logical_queries"], 2)

    def test_failed_and_missing_trials_retained_cache_not_double_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial = root / "job-baseline" / "demo-1__trial"
            (trial / "agent").mkdir(parents=True)
            (trial / "result.json").write_text(json.dumps({
                "task_name": "demo-1", "finished_at": "2026-09-08T00:00:00Z",
                "exception_info": {"exception_type": "Timeout"},
                "agent_result": {"n_input_tokens": 150, "n_cache_tokens": 100},
            }))
            (trial / "agent" / "trajectory.json").write_text(json.dumps({"steps": []}))
            report = analyze_runs(runs_dir=root, case=_case(), profile="baseline", expected_trials=5)
            group = report["profiles"]["baseline"]
            self.assertEqual(group["status_counts"]["failed"], 1)
            self.assertEqual(group["status_counts"]["missing"], 4)
            self.assertEqual(group["trials"][0]["input_tokens"], 150)
            self.assertEqual(group["trials"][0]["cached_tokens"], 100)
            self.assertEqual(group["metrics"]["input_tokens"]["missing"], 4)
            self.assertEqual(report["quality_gate"]["status"], "not_evaluated")

    def test_qoder_masked_usage_is_na_and_raw_trace_is_not_visible_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial = root / "job-zvec-grep" / "demo-1__trial"
            (trial / "agent").mkdir(parents=True)
            (trial / "result.json").write_text(json.dumps({
                "task_name": "demo-1", "finished_at": "2026-09-08T00:00:00Z",
                "agent_result": {"n_input_tokens": 0, "n_output_tokens": 0,
                                 "metadata": {"token_usage_available": False}},
            }))
            (trial / "agent" / "trajectory.json").write_text(json.dumps({"steps": []}))
            alternate = _case()["evidence"][2]
            (trial / "agent" / "zg-trace.jsonl").write_text(json.dumps({
                "event": "search", "text": alternate["path"] + "\n" + alternate["text"],
                "result": {"items": [{"content": alternate["text"]}]},
            }) + "\n")
            report = analyze_runs(runs_dir=root, case=_case(), profile="zvec-grep", expected_trials=1)
            row = report["profiles"]["zvec-grep"]["trials"][0]
            self.assertIsNone(row["input_tokens"])
            self.assertFalse(row["trajectory"]["evidence_sufficient"])
            self.assertEqual(row["zg_trace"]["search_count"], 1)

    def test_cli_creates_machine_and_human_reports_without_running_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_path, output = root / "case.json", root / "out" / "report.json"
            case_path.write_text(json.dumps(_case()))
            self.assertEqual(main(["analyze", "--runs-dir", str(root), "--case", str(case_path), "--output", str(output)]), 0)
            report = json.loads(output.read_text())
            self.assertEqual(len(report["profiles"]["baseline"]["trials"]), 5)
            self.assertIsNone(report["comparison"]["input_tokens"]["reduction_pct"])
            self.assertIn("N/A", output.with_suffix(".md").read_text())

    def test_explicit_plan_discovers_flat_trials_and_retains_missing_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            planned = [{"trial_id": f"demo-1-r{i}-baseline", "profile": "baseline", "repetition": i,
                        "trajectory_path": f"demo-1-r{i}-baseline/agent/trajectory.json", "status": "planned"} for i in range(1, 6)]
            (root / "plan.json").write_text(json.dumps({"case_id": "demo:1", "trials": planned}))
            trajectory = root / planned[0]["trajectory_path"]
            trajectory.parent.mkdir(parents=True)
            trajectory.write_text(json.dumps({"steps": [], "final_metrics": {"total_prompt_tokens": 123}}))
            report = analyze_runs(runs_dir=root, case=_case(), profile="baseline")
            group = report["profiles"]["baseline"]
            self.assertEqual(group["actual_trials"], 1)
            self.assertEqual(group["status_counts"], {"completed": 0, "failed": 0, "unfinished": 1, "missing": 4})
            self.assertEqual(len(group["trials"]), 5)
            self.assertEqual(group["metrics"]["input_tokens"]["mean"], 123)

    def test_retrieval_hidden_source_does_not_become_visible_evidence_or_rank(self):
        case = _case()
        item = case["evidence"][2]
        event = {"status": "success", "text": "#1 matchedBy=vector docs/design.md:1-1\n...",
                 "result": {"items": [{"entityId": "x", "rank": 1, "file": {"relativePath": item["path"]}, "content": item["text"]}]}}
        row = score_retrieval_event(event, case)
        self.assertFalse(row["visible_evidence"]["sufficient"])
        self.assertTrue(row["raw_item_evidence_diagnostic"]["sufficient"])
        self.assertIsNone(row["first_visible_evidence_rank"])
        self.assertIsNone(row["first_sufficient_visible_rank"])
        event["text"] = "#2 matchedBy=vector docs/design.md:1-1\n1\t" + item["text"]
        row = score_retrieval_event(event, case)
        self.assertTrue(row["visible_evidence"]["sufficient"])
        self.assertEqual(row["first_visible_evidence_rank"], 2)
        self.assertEqual(row["first_sufficient_visible_rank"], 2)

    def test_retrieval_unranked_visible_evidence_rank_is_na(self):
        item = _case()["evidence"][2]
        event = {"status": "success", "text": item["path"] + "\n" + item["text"], "result": {"items": []}}
        row = score_retrieval_event(event, _case())
        self.assertTrue(row["visible_evidence"]["sufficient"])
        self.assertIsNone(row["first_visible_evidence_rank"])

    def test_retrieval_preserves_fifteen_planned_trials_including_errors(self):
        events = [{"event": "search", "origin": "retrieval-only", "mode": "hybrid", "repetition": 1,
                   "status": "error", "error": {"message": "timeout"}, "duration_ms": 500}]
        report = analyze_retrieval(events=events, case=_case())
        self.assertEqual(sum(len(group["trials"]) for group in report["profiles"].values()), 15)
        self.assertEqual(report["profiles"]["hybrid"]["status_counts"]["error"], 1)
        self.assertEqual(report["profiles"]["hybrid"]["status_counts"]["missing"], 4)
        self.assertIsNone(report["profiles"]["hybrid"]["trials"][0]["visible_evidence"])
        self.assertIsNone(report["llm_input_tokens"])

    def test_retrieval_agreement_checks_order_request_and_identity(self):
        events = []
        for repetition, ids in enumerate((["a", "b"], ["b", "a"]), 1):
            events.append({"event": "search", "origin": "retrieval-only", "mode": "hybrid", "repetition": repetition,
                           "status": "success", "text": "No source shown", "request": {"query": "same"},
                           "result": {"items": [{"entityId": value} for value in ids]}})
        report = analyze_retrieval(events=events, case=_case(), expected_trials=2)
        agreement = report["profiles"]["hybrid"]["result_order_agreement"]
        self.assertEqual(agreement["identical_order_fraction"], 0)
        self.assertEqual(agreement["identical_set_fraction"], 1)
        events[1]["request"]["query"] = "different"
        report = analyze_retrieval(events=events, case=_case(), expected_trials=2)
        agreement = report["profiles"]["hybrid"]["result_order_agreement"]
        self.assertFalse(agreement["same_request_and_identity"])
        self.assertIsNone(agreement["identical_order_fraction"])

    def test_retrieval_cli_reads_trace_and_writes_both_report_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_path, trace, output = root / "case.json", root / "trace.jsonl", root / "retrieval.json"
            case_path.write_text(json.dumps(_case()))
            trace.write_text(json.dumps({"event": "start"}) + "\n")
            self.assertEqual(main(["retrieval", "--events", str(trace), "--case", str(case_path), "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())["expected_total_trials"], 15)
            self.assertIn("retrieval-only", output.with_suffix(".md").read_text())

    def test_direct_runner_contract_preserves_status_usage_time_and_source_judge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            planned = []
            for repetition, status in enumerate(("completed", "timeout", "integrity_failure", "failed", "completed"), 1):
                trial_id = f"demo-1-r{repetition:02d}-baseline"
                trial = root / trial_id
                (trial / "agent").mkdir(parents=True)
                planned.append({"trial_id": trial_id, "profile": "baseline", "repetition": repetition,
                                "trajectory_path": f"{trial_id}/agent/trajectory.json", "status": status})
                (trial / "result.json").write_text(json.dumps({
                    "trial_id": trial_id, "profile": "baseline", "repetition": repetition,
                    "status": status, "wall_seconds": 12.5, "finished_at": "2026-09-08T00:00:00Z",
                    "source_unchanged": status != "integrity_failure", "index_unchanged": True,
                    "agent_info": {"name": "opencode", "version": "1.18.4", "model_name": "custom-openai/glm-5.2"},
                    "final_metrics": {"total_prompt_tokens": 100, "total_completion_tokens": 20, "total_cached_tokens": 50},
                }))
                # One missing converted trajectory must not erase direct usage.
                if repetition != 4:
                    (trial / "agent" / "trajectory.json").write_text(json.dumps({"steps": []}))
            (root / "plan.json").write_text(json.dumps({"case_id": "demo:1", "trials": planned}))
            judged = {"case_id": "demo:1", "summary": {"baseline": {"pass": 1}}, "trials": [
                {"trial_id": planned[0]["trial_id"], "profile": "baseline", "status": "judged", "quality": "pass", "assessment": {"factual_correctness": {"score": 1}}}
            ]}
            report = analyze_runs(runs_dir=root, case=_case(), profile="baseline", judged_report=judged)
            group = report["profiles"]["baseline"]
            self.assertEqual(group["status_counts"]["completed"], 2)
            self.assertEqual(group["status_counts"]["timeout"], 1)
            self.assertEqual(group["status_counts"]["integrity_failure"], 1)
            self.assertEqual(group["metrics"]["input_tokens"]["mean"], 100)
            self.assertEqual(group["metrics"]["agent_wall_seconds"]["mean"], 12.5)
            self.assertEqual(group["agent_model_configurations"], [("opencode", "custom-openai/glm-5.2")])
            self.assertEqual(group["trials"][0]["original_judge"]["quality"], "pass")
            self.assertEqual(report["source_judge_summary"], judged["summary"])

    def test_retrieval_missing_or_truncated_trace_keeps_planned_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_path, trace, output = root / "case.json", root / "partial.jsonl", root / "report.json"
            case_path.write_text(json.dumps(_case()))
            trace.write_text(json.dumps({"event": "search", "origin": "retrieval-only", "mode": "fts", "repetition": 1, "status": "error"}) + '\n{"event":')
            self.assertEqual(main(["retrieval", "--events", str(trace), str(root / "missing.jsonl"), "--case", str(case_path), "--output", str(output)]), 0)
            report = json.loads(output.read_text())
            self.assertEqual(report["profiles"]["fts"]["status_counts"]["error"], 1)
            self.assertEqual(sum(len(group["trials"]) for group in report["profiles"].values()), 15)
            self.assertEqual({event["stage"] for event in report["integrity_events"]}, {"trace_parse", "trace_load"})

    def test_five_by_five_descriptive_comparison_and_mixed_model_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for profile, tokens in (("baseline", 200), ("zvec-grep", 100)):
                for index in range(5):
                    trial = root / f"job-{profile}" / f"demo-1__{index}"
                    (trial / "agent").mkdir(parents=True)
                    (trial / "result.json").write_text(json.dumps({
                        "task_name": "demo-1", "finished_at": "2026-09-08T00:00:00Z",
                        "agent_info": {"name": "opencode", "model_info": {"name": "glm-5.2"}},
                        "agent_result": {"n_input_tokens": tokens},
                    }))
                    (trial / "agent" / "trajectory.json").write_text(json.dumps({"steps": []}))
            report = analyze_runs(runs_dir=root, case=_case())
            self.assertEqual(report["profiles"]["baseline"]["actual_trials"], 5)
            self.assertEqual(report["profiles"]["zvec-grep"]["actual_trials"], 5)
            self.assertEqual(report["comparison"]["input_tokens"]["reduction_pct"], 50)
            self.assertEqual(report["comparison"]["input_tokens"]["repeat_bootstrap_95_interval_pct"], [50, 50])
            changed = root / "job-zvec-grep" / "demo-1__0" / "result.json"
            value = json.loads(changed.read_text())
            value["agent_info"]["model_info"]["name"] = "qwen3.8-max"
            changed.write_text(json.dumps(value))
            report = analyze_runs(runs_dir=root, case=_case())
            self.assertFalse(report["profiles"]["zvec-grep"]["configuration_consistent"])
            self.assertIsNone(report["comparison"]["input_tokens"]["reduction_pct"])


if __name__ == "__main__":
    unittest.main()
