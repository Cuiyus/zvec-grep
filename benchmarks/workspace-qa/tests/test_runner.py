from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "runner.py"
sys.path.insert(0, str(MODULE.parent))
import runner


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
            self.assertEqual(endpoint, "https://proxy.example/v1/embeddings")
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

    def test_cache_identity_changes_with_source_runtime_endpoint_model_cap_and_build_code(self):
        runtime = {"installed_versions": {"zg": "0.2.2", "qoder": "1.1.45", "node": "v24.0.0"},
                   "os": "linux", "architecture": "amd64"}
        kwargs = dict(protocol=runner.PROTOCOL, embedding_model=runner.EMBEDDING,
                      endpoint="https://example.invalid/embeddings", max_file_size_bytes=1048576)
        original = runner.seed_cache.make_identity({"资料.md": "content"}, runtime, **kwargs)
        variants = [runner.seed_cache.make_identity({"资料.md": "changed"}, runtime, **kwargs)]
        for key, value in (("embedding_model", "qwen/changed"), ("endpoint", "https://other.invalid/embeddings"),
                           ("max_file_size_bytes", 100)):
            variants.append(runner.seed_cache.make_identity({"资料.md": "content"}, runtime, **{**kwargs, key: value}))
        variants.append(runner.seed_cache.make_identity({"资料.md": "content"},
            {**runtime, "installed_versions": {**runtime["installed_versions"], "node": "v24.1.0"}}, **kwargs))
        with patch.object(runner.seed_cache, "file_hash", return_value="changed-build-code"):
            variants.append(runner.seed_cache.make_identity({"资料.md": "content"}, runtime, **kwargs))
        self.assertTrue(all(runner.seed_cache.digest(v) != runner.seed_cache.digest(original) for v in variants))

    def test_cache_rejects_missing_completion_marker_and_never_publishes_incomplete_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "seed"
            seed.mkdir()
            (seed / "data").write_text("index")
            identity = {"source_fingerprint": "frozen"}
            build = {"status": "completed", "fresh": True, "source_unchanged": True}
            self.assertEqual(runner.seed_cache.publish(root / "cache", seed, identity, build,
                preflight_passed=False)["status"], "not_saved")
            self.assertEqual(runner.seed_cache.publish(root / "cache", seed, identity, build,
                preflight_passed=True)["status"], "saved")
            (root / "cache" / runner.seed_cache.digest(identity) / "COMPLETE").unlink()
            restored = root / "restored"
            result = runner.seed_cache.restore(root / "cache", restored, identity)
            self.assertEqual(result["status"], "miss")
            self.assertEqual({p.name for p in restored.iterdir()}, {"locks"})

    def test_wrong_json_metadata_shape_is_a_cache_miss(self):
        for metadata in ([], "string", 3, None, {"schema_version": 2}):
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                identity = {"source_fingerprint": "frozen"}
                entry = root / "cache" / runner.seed_cache.digest(identity)
                entry.mkdir(parents=True)
                (entry / "metadata.json").write_text(json.dumps(metadata))
                (entry / "COMPLETE").write_text(runner.seed_cache.digest(metadata))
                result = runner.seed_cache.restore(root / "cache", root / "restored", identity)
                self.assertEqual(result["status"], "miss")
                self.assertIn("supported object", result["reason"])

    def test_index_stream_is_visible_before_exit_and_redacts_split_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = root / "release"
            observed = threading.Event()

            class Observer(io.StringIO):
                def write(self, text):
                    result = super().write(text)
                    if "progress=" in text:
                        observed.set()
                    return result

            console = Observer()
            outcome = {}
            script = (
                "import os,sys,time,pathlib\n"
                "secret=os.environ['QWEN_API_KEY']\n"
                "sys.stderr.write('progress='+secret[:5]);sys.stderr.flush()\n"
                "time.sleep(.05)\n"
                "sys.stderr.write(secret[5:]+' 中文\\n');sys.stderr.flush()\n"
                "while not pathlib.Path(sys.argv[1]).exists(): time.sleep(.01)\n"
                "print('summary='+secret)\n")

            def run():
                try:
                    outcome["stdout"] = runner.run_streamed(
                        [sys.executable, "-u", "-c", script, str(release)], timeout=5,
                        diagnostic_path=root / "failure.json", output_prefix=root / "index-build")
                except Exception as error:
                    outcome["error"] = error

            with patch.dict("os.environ", {"QWEN_API_KEY": "secret-split-across-pipe-writes"}), redirect_stderr(console):
                worker = threading.Thread(target=run)
                worker.start()
                try:
                    self.assertTrue(observed.wait(3), "Progress remained buffered until process exit")
                    self.assertTrue(worker.is_alive())
                    saved = (root / "index-build.stderr.txt").read_text()
                    self.assertEqual(saved, "progress=[REDACTED] 中文\n")
                    self.assertEqual(console.getvalue(), saved)
                finally:
                    release.touch()
                    worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertNotIn("error", outcome)
            self.assertEqual(outcome["stdout"], "summary=[REDACTED]")
            self.assertEqual((root / "index-build.stdout.txt").read_text(), "summary=[REDACTED]\n")
            self.assertFalse((root / "failure.json").exists())

    def test_index_timeout_and_exit_failure_preserve_redacted_partial_logs(self):
        for ending, timeout, expected_kind in (("time.sleep(5)", .2, "TimeoutExpired"),
                                               ("sys.exit(7)", 5, "CalledProcessError")):
            with self.subTest(kind=expected_kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                script = ("import os,sys,time\n"
                          "print('partial='+os.environ['QWEN_API_KEY'],flush=True)\n"
                          "print('progress='+os.environ['QWEN_API_KEY'],file=sys.stderr,flush=True)\n"
                          + ending)
                with patch.dict("os.environ", {"QWEN_API_KEY": "offline-private-value"}), redirect_stderr(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, expected_kind):
                        runner.run_streamed([sys.executable, "-u", "-c", script], timeout=timeout,
                            diagnostic_path=root / "failure.json", output_prefix=root / "index-build")
                report = json.loads((root / "failure.json").read_text())
                self.assertEqual(report["kind"], expected_kind)
                self.assertTrue(report["logs_redacted"])
                self.assertEqual(Path(report["stdout_path"]).read_text(), "partial=[REDACTED]\n")
                self.assertEqual(Path(report["stderr_path"]).read_text(), "progress=[REDACTED]\n")
                self.assertNotIn("offline-private-value", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
