from __future__ import annotations

import argparse
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "runner.py"
spec = importlib.util.spec_from_file_location("workspace_qa_runner", MODULE)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def args(self, root, repetitions=2, **overrides):
        source = root / "source"
        source.mkdir()
        (source / ".git").mkdir()
        (source / ".zvec-grep").mkdir()
        (source / "资料.md").write_text("完整的业务资料\n")
        question = root / "question.txt"
        question.write_text("请读取工作区，生成分析报告。\n")
        values = dict(source_root=source, question_file=question, task_id="task-3", output=root / "runs",
                      repetitions=repetitions, image="zg-readonly-qa:0.2.2", timeout=900,
                      order_seed=1729, dry_run=False, answer_filename="报告/分析.md")
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_ten_repeats_have_balanced_randomized_ab_ba_blocks(self):
        plan = runner.make_plan("3", 10)
        self.assertEqual(plan, runner.make_plan("3", 10))
        self.assertEqual(len(plan["trials"]), 20)
        self.assertEqual(sum(t["profile"] == "baseline" for t in plan["trials"][::2]), 5)
        for i in range(0, 20, 2):
            pair = plan["trials"][i:i + 2]
            self.assertEqual({t["profile"] for t in pair}, set(runner.PROFILES))
            self.assertEqual(pair[0]["repetition"], pair[1]["repetition"])
        self.assertEqual(len({t["trial_id"] for t in plan["trials"]}), 20)
        for invalid in (0, -1, 1.1, True):
            with self.assertRaises(ValueError):
                runner.make_plan("3", invalid)
        for invalid in ("../3", "/3", "", "3/4"):
            with self.assertRaises(ValueError):
                runner.make_plan(invalid, 10)

    def test_candidate_path_cannot_escape_and_source_suffix_is_symmetric(self):
        for invalid in ("../answer.md", "/answer.md", "a/../answer.md", "a//answer.md", "a\\answer.md", "answer.pdf", "./answer.md", "a\n.md"):
            with self.assertRaises(ValueError):
                runner.answer_filename(invalid)
        self.assertEqual(runner.answer_filename("报告/输出.md"), "报告/输出.md")
        q = "原始问题必须保留\n"
        a = runner.instruction(q, "输出.md", zg=False)
        b = runner.instruction(q, "输出.md", zg=True)
        self.assertTrue(a.startswith(q))
        self.assertEqual(a, b.replace(", " + runner.QODER_SEARCH_TOOL, ""))

    def test_dry_run_preserves_all_missing_trials_without_runtime_or_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp), repetitions=10, dry_run=True)
            with patch.object(runner, "runtime_identity") as runtime, redirect_stdout(io.StringIO()):
                self.assertEqual(runner.execute(args), 0)
                runtime.assert_not_called()
                with self.assertRaisesRegex(ValueError, "new/empty"):
                    runner.execute(args)
            rows = json.loads((args.output / "trial-results.json").read_text())["trials"]
            self.assertEqual(len(rows), 20)
            self.assertTrue(all(r["input_tokens"] is None and r["status"] == "planned" for r in rows))

    def test_runtime_tag_does_not_override_pinned_installed_versions(self):
        replies = [json.dumps([{"Id": "sha256:x"}]), json.dumps({"zg": "0.2.3", "qoder": "1.1.45"})]
        with patch.object(runner, "run_checked", side_effect=replies):
            with self.assertRaisesRegex(RuntimeError, "versions differ"):
                runner.runtime_identity("looks-pinned:0.2.2")

    def test_remote_embedding_endpoint_uses_production_environment_contract(self):
        self.assertEqual(runner.EMBEDDING, "qwen/qwen3.7-text-embedding")
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(runner.embedding_endpoint(), runner.ZVEC_GREP_EMBEDDING_ENDPOINT)
        with patch.dict("os.environ", {"QWEN_EMBEDDING_ENDPOINT": "https://proxy.example/v1/embeddings"}):
            endpoint = runner.embedding_endpoint()
            command = runner.with_embedding_environment(["docker", "run"], endpoint)
            self.assertEqual(command, ["docker", "run", "--env", "QWEN_API_KEY", "--env",
                                       "ZVEC_GREP_ENDPOINT=https://proxy.example/v1/embeddings", "--env",
                                       "ZG_QA_ALLOW_REMOTE_EMBEDDING=1"])
        for invalid in ("", "file:///tmp/endpoint", "https://secret@example.com/embeddings", "https://example.com/?key=secret"):
            with patch.dict("os.environ", {"QWEN_EMBEDDING_ENDPOINT": invalid}):
                with self.assertRaises(ValueError):
                    runner.embedding_endpoint()

    def test_missing_remote_embedding_credential_stops_before_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            with (patch.dict("os.environ", {runner.SPEC.credential_env: "fake-secret"}, clear=True),
                  patch.object(runner, "runtime_identity") as runtime):
                with self.assertRaisesRegex(RuntimeError, "QWEN_API_KEY is required"):
                    runner.execute(args)
                runtime.assert_not_called()
            rows = json.loads((args.output / "trial-results.json").read_text())["trials"]
            self.assertEqual(len(rows), 4)

    @staticmethod
    def stream():
        usage = {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 60}
        tool = {"type": "tool_use", "id": "call-1", "name": runner.QODER_SEARCH_TOOL, "input": {"query": "q"}}
        assistant = {"type": "assistant", "session_id": "s", "message": {"id": "m1", "usage": usage, "content": [tool]}}
        return [assistant, assistant,  # repeated complete blocks are one request/call
                {"type": "user", "session_id": "s", "message": {"content": [{"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": "error"}]}},
                {"type": "assistant", "session_id": "s", "message": {"id": "m1", "usage": {"input_tokens": 0, "output_tokens": 0}, "content": []}}]

    def test_metrics_do_not_double_count_cached_input_or_duplicate_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / runner.SPEC.stream_filename).write_text("\n".join(json.dumps(x) for x in self.stream()))
            metrics = runner.trial_metrics(root, {"final_metrics": {"total_prompt_tokens": 100, "total_cached_tokens": 60}})
            self.assertEqual(metrics["input_tokens"], 100)
            self.assertEqual(metrics["native_turn_input_tokens"], 100)
            self.assertEqual(metrics["tool_calls"], 1)
            self.assertEqual(metrics["zg_tool_calls"], 1)
            self.assertEqual(metrics["tool_calls_successful"], 0)
            self.assertEqual(metrics["model_requests"], 1)
            hidden = runner.trial_metrics(root, {"final_metrics": {"total_prompt_tokens": 0, "extra": {"token_usage_available": False}}})
            self.assertIsNone(hidden["input_tokens"])

    def test_report_preserves_terminal_native_answer_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            answer = "\n# 完整报告\n结论\n"
            event = {"type": "result", "subtype": "success", "result": answer}
            (root / runner.SPEC.stream_filename).write_text(json.dumps(event))
            metrics = runner.trial_metrics(root, {"has_final_answer": True})
            self.assertEqual(metrics["answer"], answer)

    @staticmethod
    def mounted(command, target):
        for i, item in enumerate(command):
            if item == "--mount":
                fields = command[i + 1].split(",")
                if "target=" + target in fields:
                    return Path(next(x.removeprefix("source=") for x in fields if x.startswith("source=")))
        return None

    def mocked_run(self, args, *, contract_failure=False, mutate_seed=False, verify_failure=False):
        commands, calls, indexes = [], [], []
        original_copy, original_remove = runner.working_index, shutil.rmtree

        def copy_one(seed, destination):
            if destination.parent.exists():
                self.assertEqual(list(destination.parent.iterdir()), [], "a previous mutable index copy was retained")
            return original_copy(seed, destination)

        def remove_after_record(path, *positional, **kwargs):
            if path.parent.name == "working-indexes" and path.name != "preflight":
                saved = json.loads((args.output / path.name / "result.json").read_text())
                self.assertIn("working_index_files_before_sha256", saved)
                self.assertIn("working_index_files_after_sha256", saved)
                self.assertIn("working_index_semantic_unchanged", saved)
            return original_remove(path, *positional, **kwargs)

        def checked(command, **kwargs):
            commands.append(command)
            if "rev-parse" in command:
                return "frozen-commit"
            if "ls-files" in command:
                return "资料.md"
            target = self.mounted(command, "/logs")
            if runner.PREPARE_INDEX in command:
                self.mounted(command, "/app/.zvec-grep").joinpath("data").write_text("fixed seed")
            if "preflight" in command:
                (target / "snapshot.json").write_text("{}")
            if "verify" in command and verify_failure:
                raise RuntimeError("simulated semantic index corruption")
            return "{}"

        def launch(command, **kwargs):
            calls.append(command)
            logs = self.mounted(command, "/logs")
            idx = self.mounted(command, "/app/.zvec-grep")
            if idx:
                indexes.append(idx)
                self.assertEqual((idx / "data").read_text(), "fixed seed")
                # Working storage can change; the seed must not.
                (idx / "header").write_text("opened")
            (logs / "session.json").write_text(json.dumps({"status": "completed"}))
            (logs / runner.SPEC.stream_filename).write_text("\n".join(json.dumps(x) for x in self.stream()))
            (logs / "trajectory.json").write_text(json.dumps({"steps": [{"source": "agent", "message": "最终完整报告"}]}))
            if mutate_seed:
                (args.output / "preparation" / "index" / "data").write_text("corrupted")
            return SimpleNamespace(wait=lambda timeout: 0)

        conversion = {"error_event_count": 0, "contract_error_count": int(contract_failure),
                      "has_final_answer": True, "model_identity": {"valid": True},
                      "final_metrics": {"total_prompt_tokens": 100, "total_completion_tokens": 10,
                                        "total_cached_tokens": 60}}
        with (patch.dict("os.environ", {runner.SPEC.credential_env: "fake-secret", "QWEN_API_KEY": "fake-embedding-secret"}),
              patch.object(runner, "runtime_identity", return_value={"image_id": "sha256:fixed"}),
              patch.object(runner, "working_index", side_effect=copy_one),
              patch.object(runner.shutil, "rmtree", side_effect=remove_after_record),
              patch.object(runner, "run_checked", side_effect=checked),
              patch.object(runner, "cleanup_container"),
              patch.object(runner.subprocess, "Popen", side_effect=launch),
              patch.object(runner, "convert_agent_trace", return_value=conversion),
              redirect_stdout(io.StringIO())):
            result = runner.execute(args)
        return result, commands, calls, indexes

    def test_one_index_build_fresh_sessions_and_deliverables_outside_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            result, commands, calls, indexes = self.mocked_run(args)
            self.assertEqual(result, 0)
            self.assertEqual(sum(runner.PREPARE_INDEX in c for c in commands), 1)
            self.assertFalse(any("retrieve" in c for c in commands))
            self.assertEqual(len(calls), 4)
            self.assertEqual(len(set(indexes)), 2)
            self.assertTrue(all(p != args.output / "preparation" / "index" for p in indexes))
            self.assertEqual((args.output / "preparation" / "index" / "data").read_text(), "fixed seed")
            self.assertEqual(list((args.output / "preparation" / "working-indexes").iterdir()), [])
            rows = json.loads((args.output / "trial-results.json").read_text())["trials"]
            self.assertEqual(len(rows), 4)
            for row in rows:
                candidate = args.output / row["candidate_output_path"]
                self.assertEqual(candidate.read_text(), "最终完整报告")
                self.assertFalse(candidate.is_relative_to(args.source_root))
                self.assertEqual(row["status"], "completed")
                self.assertGreaterEqual(row["post_trial_verification_wall_seconds"], 0)
                if row["profile"] == "with-zg":
                    self.assertEqual(row["working_index_disposal"], "removed_after_verification_and_provenance_saved")
            for command in commands + calls:
                self.assertNotIn("fake-secret", " ".join(command))
                self.assertNotIn("fake-embedding-secret", " ".join(command))
                self.assertNotIn("local/potion", " ".join(command))
                if "--mount" in command:
                    mounts = [command[i + 1] for i, v in enumerate(command) if v == "--mount"]
                    source_mount = next(m for m in mounts if "target=/app," in m)
                    self.assertTrue(source_mount.endswith(",readonly"))
                    self.assertFalse(any("question.txt" in m or "candidate" in m for m in mounts))
                    has_index = self.mounted(command, "/app/.zvec-grep") is not None
                    self.assertEqual("QWEN_API_KEY" in command, has_index)
                    self.assertEqual(any(x.startswith("ZVEC_GREP_ENDPOINT=") for x in command), has_index)
                    self.assertEqual("ZG_QA_ALLOW_REMOTE_EMBEDDING=1" in command, has_index)
                if "--embedding-model" in command:
                    self.assertEqual(command[command.index("--embedding-model") + 1], "qwen/qwen3.7-text-embedding")

    def test_contract_failure_retains_planned_denominator_and_does_not_launch_remaining(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp), repetitions=10)
            result, _, calls, _ = self.mocked_run(args, contract_failure=True)
            self.assertEqual(result, 1)
            self.assertEqual(len(calls), 1)
            rows = json.loads((args.output / "trial-results.json").read_text())["trials"]
            self.assertEqual(len(rows), 20)
            self.assertEqual(rows[0]["status"], "contract_failure")
            self.assertTrue(all(r["status"] == "planned" and r["input_tokens"] is None for r in rows[1:]))

    def test_seed_integrity_failure_stops_new_trials(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            result, _, calls, _ = self.mocked_run(args, mutate_seed=True)
            self.assertEqual(result, 1)
            self.assertEqual(len(calls), 1)
            rows = json.loads((args.output / "trial-results.json").read_text())["trials"]
            self.assertEqual(rows[0]["status"], "integrity_failure")
            self.assertEqual(list((args.output / "preparation" / "working-indexes").iterdir()), [])

    def test_failed_semantic_verification_keeps_hashes_and_discards_mutable_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            result, _, _, indexes = self.mocked_run(args, verify_failure=True)
            self.assertEqual(result, 1)
            self.assertEqual(len(indexes), 1)
            self.assertFalse(indexes[0].exists())
            rows = json.loads((args.output / "trial-results.json").read_text())["trials"]
            row = next(r for r in rows if r["status"] == "integrity_failure")
            self.assertFalse(row["working_index_semantic_unchanged"])
            self.assertIn("working_index_files_after_sha256", row)
            self.assertEqual(row["working_index_disposal"], "removed_after_verification_and_provenance_saved")
            self.assertEqual((args.output / "preparation" / "index" / "data").read_text(), "fixed seed")


if __name__ == "__main__":
    unittest.main()
