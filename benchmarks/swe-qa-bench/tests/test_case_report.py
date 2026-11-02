from __future__ import annotations

import copy
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from zg_bench.case_report import export_report
from zg_bench.core.protocol import PROFILE_NAMES, SCORE_KEYS
from zg_bench.metrics.usage import SESSION_USAGE_METRICS, SESSION_USAGE_SCOPE


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def file_hashes(root: Path):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def fixture(root: Path, count=10):
    evidence, judge = root / "raw/case-analysis", root / "raw/judge"
    pair = {"valid": True, "task_id": "pylint-10", "task_slug": "pylint-10",
            "expected_trials": count, "actual_trials": count, "profiles": {}}
    case = {"task_id": "pylint:10", "profiles": {}}
    report = {"cases": [case], "judge": {"model": "qwen3.8-max", "self_judge": True},
              "aggregate": {"filter": {"included_count": 0, "excluded_count": 1}}}
    write(evidence / "run-lock.json", {"attempts": count, "case": {"task_slug": "pylint-10"},
          "zg_commit": "candidate", "harness_commit": "harness", "profiles": list(PROFILE_NAMES)})
    for profile in PROFILE_NAMES:
        pair["profiles"][profile] = {"trials": []}
        case["profiles"][profile] = {"trials": []}
        for index in range(1, count + 1):
            name = f"pylint-10__{profile}-{index}"
            directory = evidence / "jobs" / f"formal-{profile}" / name
            agent = directory / "agent"
            session = f"ses-{profile}-{index}"
            tools = ["bash"] if profile == "baseline" else ["bash", "zvec_grep_zvec_grep_search"]
            total = {key: 0 for key in SESSION_USAGE_METRICS}
            total.update(input_tokens=100 + index + (100 if profile == "zvec-grep" else 0),
                         output_tokens=20, reasoning_tokens=5, text_output_tokens=15,
                         cache_read_tokens=50, tool_calls=len(tools), llm_calls=2, cost_usd=None)
            total["uncached_input_tokens"] = total["input_tokens"] - total["cache_read_tokens"]
            usage = {"schema_version": 1, "scope": SESSION_USAGE_SCOPE, "complete": True, "errors": [],
                     "root_session_id": session, "sessions": [{"session_id": session}],
                     "root": copy.deepcopy(total), "total": copy.deepcopy(total),
                     "descendants": {key: 0 for key in SESSION_USAGE_METRICS}, "collection_wall_seconds": 1.0}
            write(agent / "session-usage.json", usage)
            answer = f"Answer for {name}."
            write(agent / "trajectory.json", {"steps": [{"source": "agent", "message": answer}]})
            adapter = "CaseEvidenceBaselineOpenCode" if profile == "baseline" else "CaseEvidenceOpenCode"
            write(directory / "config.json", {"agent": {"name": "zg_bench.agents.case_evidence:" + adapter}})
            start = datetime(2026, 11, 2, tzinfo=timezone.utc) + timedelta(minutes=index)
            write(directory / "result.json", {"trial_name": name, "exception_info": None,
                  "started_at": start.isoformat(), "finished_at": (start + timedelta(seconds=40 + index)).isoformat(),
                  "agent_execution": {"started_at": start.isoformat(),
                                      "finished_at": (start + timedelta(seconds=21 + index)).isoformat()}})
            records = []
            for number, tool in enumerate(tools):
                records.append({"kind": "part", "data": json.dumps({"id": f"part-{number}", "session_id": session,
                    "message_id": "message", "content": {"type": "tool", "tool": tool, "callID": f"call-{number}",
                    "state": {"status": "completed", "input": {"query": "exact request"}, "output": "full output " * 50,
                              "time": {"start": 1000 + number, "end": 2000 + number}}}})})
            records.reverse()
            # An auxiliary session must not inflate the task-session metrics.
            records.append({"kind": "part", "data": {"id": "aux-part", "session_id": "auxiliary-title-session",
                "content": {"type": "tool", "tool": "not-in-task-tree"}}})
            write(agent / "full-sessions.json", records)
            for number in range(3):
                write(agent / f"wire/model/{number}.metadata.json", {"route": "model"})
            trial = {"profile": profile, "trial_name": name, "trial_index": index, "job_name": f"formal-{profile}",
                     "answer": answer, "session_usage": usage, "agent_wall_seconds": 20 + index, **copy.deepcopy(total)}
            pair["profiles"][profile]["trials"].append(trial)
            scores = {key: index + (1 if profile == "baseline" else 4) for key in SCORE_KEYS}
            case["profiles"][profile]["trials"].append({"trial_name": name, "trial_index": index,
                "judge": {"model": "qwen3.8-max", "scores": scores, "total": sum(scores.values())},
                "metrics": {"session_usage": usage, "agent_wall_seconds": 20 + index, **copy.deepcopy(total)}})
        # Different list ordering must never alter which answer receives a score.
        case["profiles"][profile]["trials"].reverse()
    write(judge / "pair.json", pair)
    write(judge / "report.json", report)
    return evidence, judge


class CaseReportTests(unittest.TestCase):
    def test_exports_all_twenty_trials_by_identity_without_filtering_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, judge = fixture(root)
            (root / "README.zh-CN.md").write_text("Human introduction")
            before = file_hashes(root / "raw")
            result = export_report(evidence, judge, root)
            self.assertEqual(result["trials"], 20)
            self.assertEqual(result["task"], "pylint:10")
            self.assertFalse(result["filter_applied"])
            self.assertEqual(file_hashes(root / "raw"), before)
            self.assertEqual((root / "README.zh-CN.md").read_text(), "Human introduction")
            rows = json.loads((root / "trials.json").read_text())
            with (root / "trials.csv").open() as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 20)
            for row in rows:
                expected_score = row["trial_index"] + (1 if row["profile"] == "baseline" else 4)
                self.assertEqual(row["judge_correctness"], expected_score)
                self.assertEqual(row["judge_total"], expected_score * 5)
                self.assertEqual(row["agent_wall_seconds_raw"] - row["agent_wall_seconds_standard"], 1)
                self.assertEqual(row["wire_model_calls_including_auxiliary"], 3)
                self.assertEqual(row["llm_calls"], 2)
                self.assertEqual(row["zg_search_calls"], 0 if row["profile"] == "baseline" else 1)
                self.assertIn(row["trial_name"], (root / row["links"]["answer"]).read_text())
                self.assertTrue((root / row["links"]["full_sessions"]).exists())
                tools = json.loads((root / row["links"]["tool_sequence_json"]).read_text())
                self.assertEqual(len(tools), row["tool_calls"])
                self.assertEqual(tools[0]["tool"], "bash")
                self.assertTrue(tools[0]["output_preview_truncated"])
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual(summary["retained_trials"], 20)
            self.assertEqual(summary["differences"]["judge_total"]["zvec_minus_baseline"], 15)
            self.assertEqual(summary["profiles"]["baseline"]["judge_total"]["median"], 32.5)
            self.assertIsNone(summary["differences"]["zg_tool_calls"]["change_pct"])
            self.assertGreater(summary["profiles"]["baseline"]["judge_total"]["std_population"], 0)
            text = (root / "RESULTS.generated.zh-CN.md").read_text()
            self.assertIn("不是纯模型执行耗时", text)
            self.assertIn("不采用标准 Aggregate", text)

    def test_rejects_wrong_run_names_sessions_metrics_and_answers(self):
        for failure in ("name", "duplicate", "session", "metric", "answer", "tool_count"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                evidence, judge = fixture(root, 2)
                report_path = judge / "report.json"
                report = json.loads(report_path.read_text())
                trials = report["cases"][0]["profiles"]["baseline"]["trials"]
                if failure == "name":
                    trials[0]["trial_name"] = "pylint-10__other-run"
                elif failure == "duplicate":
                    trials[0]["trial_name"] = trials[1]["trial_name"]
                elif failure == "session":
                    trials[0]["metrics"]["session_usage"]["root_session_id"] = "another-run-session"
                elif failure == "metric":
                    trials[0]["metrics"]["input_tokens"] += 10
                elif failure == "answer":
                    path = judge / "pair.json"
                    pair = json.loads(path.read_text())
                    pair["profiles"]["baseline"]["trials"][0]["answer"] = "Wrong answer from another run"
                    write(path, pair)
                else:
                    path = next((evidence / "jobs/formal-baseline").glob("*/agent/full-sessions.json"))
                    write(path, [])
                write(report_path, report)
                with self.assertRaises(ValueError):
                    export_report(evidence, judge, root / "report")
                self.assertFalse((root / "report").exists())

    def test_rejects_hidden_extra_trials_and_generated_output_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, judge = fixture(root, 1)
            extra = evidence / "jobs/formal-baseline/extra/result.json"
            write(extra, {})
            with self.assertRaisesRegex(ValueError, "refusing to hide"):
                export_report(evidence, judge, root)
            extra.unlink()
            export_report(evidence, judge, root)
            with self.assertRaisesRegex(ValueError, "already exist"):
                export_report(evidence, judge, root)

    def test_rejects_output_inside_raw_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, judge = fixture(root, 1)
            with self.assertRaisesRegex(ValueError, "separate"):
                export_report(evidence, judge, evidence / "derived")


if __name__ == "__main__":
    unittest.main()
