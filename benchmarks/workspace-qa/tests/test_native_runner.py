from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_runner
import runner
import seed_cache


RUNTIME = {"image_id": "sha256:fixture", "installed_versions": {
    "zg": "0.2.2", "qoder": "1.1.45", "node": "v24.0.0"}, "os": "linux", "architecture": "amd64"}
LIMITS = {"model_requests": 60, "tool_calls": 120, "input_tokens": 600000, "wall_seconds": 900}
ANSWER = "\n# 完整报告\n原始终态回答。\n"


class NativeRunnerTests(unittest.TestCase):
    def args(self, root, *, name="runs", repetitions=2):
        source = root / "source"
        source.mkdir(exist_ok=True)
        (source / ".git").mkdir(exist_ok=True)
        (source / ".zvec-grep").mkdir(exist_ok=True)
        (source / "资料.md").write_text("完整的业务资料\n")
        question = root / (name + "-question.txt")
        question.write_text("请读取工作区，生成分析报告。\n")
        return argparse.Namespace(source_root=source, question_file=question, task_id="3", output=root / name,
            repetitions=repetitions, image=native_runner.IMAGE, timeout=900, order_seed=1729,
            dry_run=False, answer_filename="报告/分析.md")

    def execute(self, args, *, cache=None, statuses=(), mutate=None, prepare_error=None,
                mutate_preparation=False, commit="fixture-commit", runtime=RUNTIME, extra_env=None):
        calls, builds = [], []

        def prepare(source, index, logs, model_cache, *, image, check_only=False):
            builds.append({"index": index, "check_only": check_only})
            logs.mkdir(parents=True, exist_ok=True)
            if prepare_error:
                raise RuntimeError(prepare_error)
            if not check_only:
                (index / "vectors.bin").write_bytes(b"native-index")
            # Native auth grant may legitimately refresh preparation metadata.
            (index / "grants.json").write_text('{"embedding": true}')
            if mutate_preparation:
                (source / "资料.md").write_text("changed during preparation")
            return {"status": "completed", "protocol": native_runner.PROTOCOL, "fresh": True,
                    "wall_seconds": 123.0, "method": "released_zg_cli"}

        def trial(source, agent, index, model_cache, **kwargs):
            calls.append({"source": source, "agent": agent, "index": index, **kwargs})
            agent.mkdir(parents=True)
            if index:
                (index / "native-refresh.json").write_text('{"refreshed": true}')
            if mutate:
                mutate(source, index, args.output / "preparation/index")
            status = statuses[len(calls) - 1] if len(calls) <= len(statuses) else "completed"
            return {"status": status, "answer": ANSWER, "input_tokens": 120, "output_tokens": 9,
                    "tool_calls": 2, "zg_tool_calls": int(index is not None), "wall_seconds": 7.5,
                    "installation_wall_seconds": 2.0, "container_total_wall_seconds": 11.0}

        env = {runner.SPEC.credential_env: "fake-qoder-secret", "QWEN_API_KEY": "fake-embedding-secret"}
        env.update(extra_env or {})
        if cache:
            env["WORKSPACE_QA_INDEX_CACHE"] = str(cache)
        with patch.dict("os.environ", env, clear=True), \
                patch.object(runner, "runtime_identity", return_value=runtime), \
                patch.object(runner, "run_checked", side_effect=lambda cmd, **kw: commit if cmd[-1] == "HEAD" else "资料.md"), \
                patch.object(seed_cache, "build_identity", return_value={"fixture_runtime": RUNTIME}), \
                patch.object(native_runner, "native_index", side_effect=prepare), \
                patch.object(native_runner, "run_native_trial", side_effect=trial), redirect_stdout(io.StringIO()):
            status = runner.execute(args)
        return status, calls, builds

    def rows(self, args):
        return json.loads((args.output / "trial-results.json").read_text())["trials"]

    def test_native_pairs_prepare_once_and_use_fresh_writable_copies_with_separate_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            status, calls, builds = self.execute(args)
            self.assertEqual(status, 0)
            self.assertEqual(len(builds), 1)
            self.assertFalse(builds[0]["check_only"])
            self.assertEqual(len(calls), 4)
            copies = [c["index"] for c in calls if c["profile"] == "with-zg"]
            self.assertEqual(len(set(copies)), 2)
            self.assertTrue(all(not p.exists() for p in copies))
            self.assertTrue(all(c["index"] is None for c in calls if c["profile"] == "baseline"))
            self.assertTrue(all(c["limits"] == LIMITS for c in calls))
            self.assertEqual(len({c["agent"] for c in calls}), 4)
            rows = self.rows(args)
            for row in rows:
                self.assertTrue(row["source_unchanged"])
                self.assertTrue(row["original_seed_unchanged"])
                self.assertEqual(row["wall_seconds"], 7.5)
                candidate = args.output / row["candidate_output_path"]
                self.assertFalse(candidate.is_relative_to(args.source_root))
                self.assertEqual(candidate.read_text(), ANSWER)
                if row["profile"] == "with-zg":
                    self.assertTrue(row["native_index_refresh_allowed"])
                    self.assertIsNone(row["working_index_semantic_unchanged"])
                    self.assertTrue(row["working_index_physical_changes"])
            manifest = json.loads((args.output / "manifest.json").read_text())
            self.assertEqual(manifest["protocol"], native_runner.PROTOCOL)
            self.assertEqual(manifest["integration_method"], "zg_install")
            self.assertEqual(manifest["install_command"], ["zg", "install", "--target", "qoder", "--yes"])
            self.assertEqual(manifest["run_limits"], LIMITS)
            self.assertEqual(manifest["index_preparation"]["native_index"]["wall_seconds"], 123.0)

    def test_model_failures_keep_the_original_sample_and_continue_without_resampling(self):
        for failure in ("budget_exhausted", "timeout", "failed"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                args = self.args(Path(tmp))
                status, calls, _ = self.execute(args, statuses=[failure])
                self.assertEqual(status, 1)
                expected_ids = [t["trial_id"] for t in runner.make_plan("3", 2)["trials"]]
                self.assertEqual([c["agent"].parent.name for c in calls], expected_ids)
                rows = self.rows(args)
                self.assertEqual(rows[0]["status"], failure)
                self.assertEqual(rows[0]["input_tokens"], 120)
                self.assertEqual([r["status"] for r in rows[1:]], ["completed"] * 3)

    def continuation_fixture(self, root):
        from test_continuation import fixture, QUESTION, FILENAME, NEW_COMMIT
        from native_fixtures import dump
        args = self.args(root, repetitions=10)
        (args.source_root / "资料.md").write_bytes(b"frozen")
        args.question_file.write_text(QUESTION)
        args.answer_filename = FILENAME
        prior, plan, ledger, current = fixture(root)
        # The helper's compact fixture abbreviates explanatory manifest text;
        # exercise the real runner's full runtime compatibility check too.
        manifest_path = prior / "runs/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(
            index_policy="native CLI seed; separate writable copy per with-zg trial; normal native refresh allowed",
            wall_seconds_scope="qa-session agent interval including native MCP startup/search; native install, index preparation and host integrity checks are recorded separately",
            answer_delivery="Harness saves terminal response verbatim outside corpus to requested report path")
        dump(manifest_path, manifest)
        args.continue_from = prior
        args.continuation_code_review = root / "code-review.json"
        dump(args.continuation_code_review, current["continuation_code_review"])
        runtime = {k: current[k] for k in ("image_id", "installed_versions", "os", "architecture")}
        return args, prior, plan, ledger, {"runtime": runtime,
            "extra_env": {"GITHUB_SHA": NEW_COMMIT, "GITHUB_RUN_ID": "continuation-run"}}

    def test_continuation_executes_only_original_sixteen_slots_and_preserves_four_observations(self):
        import continuation
        from native_fixtures import installation_stub
        with tempfile.TemporaryDirectory() as tmp:
            args, prior, plan, ledger, kwargs = self.continuation_fixture(Path(tmp))
            original_hashes = continuation.file_hashes(prior)
            with patch.object(continuation, "validate_installation", side_effect=installation_stub):
                status, calls, builds = self.execute(args, **kwargs)
            self.assertEqual(status, 1)  # The original failure is still a failure.
            expected = [t["trial_id"] for t in plan["trials"][4:]]
            self.assertEqual([call["agent"].parent.name for call in calls], expected)
            self.assertEqual(len(calls), 16)
            self.assertEqual(len(builds), 1)
            self.assertEqual(self.rows(args)[:4], ledger["trials"][:4])
            self.assertEqual(self.rows(args)[3]["status"], "contract_failure")
            self.assertTrue(all(row["status"] == "completed" for row in self.rows(args)[4:]))
            self.assertEqual(continuation.file_hashes(prior), original_hashes)
            manifest = json.loads((args.output / "manifest.json").read_text())
            self.assertEqual(manifest["continuation"]["pending_trial_ids"], expected)
            for relative, digest in manifest["continuation"]["preserved_files_sha256"].items():
                self.assertEqual(runner.sha256(args.output / relative), digest)
            proof = continuation.validate_continuation_evidence(args.output)
            self.assertTrue(proof["no_resampling"])
            self.assertEqual(len(proof["preserved_trial_ids"]), 4)

    def test_continuation_rejects_ambiguous_original_status_before_import_or_new_trials(self):
        import continuation
        from native_fixtures import dump, installation_stub
        for status in ("running", "unrecognized"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                args, prior, _, ledger, kwargs = self.continuation_fixture(Path(tmp))
                row = ledger["trials"][3]
                row.update(status=status, partial_usage_observed=9104)
                trial = prior / "runs" / row["trial_id"]
                if status == "running":
                    (trial / "result.json").unlink()
                else:
                    dump(trial / "result.json", row)
                dump(prior / "runs/trial-results.json", ledger)
                old_plan = json.loads((prior / "runs/plan.json").read_text())
                old_plan["trials"][3]["status"] = status
                dump(prior / "runs/plan.json", old_plan)
                judged = json.loads((prior / "runs/judgements.json").read_text())
                judged["trial_results_sha256"] = runner.sha256(prior / "runs/trial-results.json")
                dump(prior / "runs/judgements.json", judged)
                before = continuation.file_hashes(prior)
                with patch.object(continuation, "validate_installation", side_effect=installation_stub), \
                        self.assertRaisesRegex(ValueError, rf"terminal original result: {row['trial_id']}.*{status}"):
                    self.execute(args, prepare_error="Index must not start", **kwargs)
                self.assertEqual(continuation.file_hashes(prior), before)
                self.assertFalse((args.output / "continuation-evidence").exists())
                self.assertFalse(any(args.output.glob("3-r*")))
                self.assertFalse((args.output / "preparation/runtime/preparation.json").exists())
                self.assertTrue((args.output / "failure.json").is_file())

    def test_continuation_preparation_failure_retains_imported_old_rows_without_retry(self):
        import continuation
        from native_fixtures import installation_stub
        with tempfile.TemporaryDirectory() as tmp:
            args, prior, _, ledger, kwargs = self.continuation_fixture(Path(tmp))
            before = continuation.file_hashes(prior)
            with patch.object(continuation, "validate_installation", side_effect=installation_stub), \
                    self.assertRaisesRegex(RuntimeError, "native preparation failed"):
                self.execute(args, prepare_error="native preparation failed", **kwargs)
            self.assertEqual(self.rows(args)[:4], ledger["trials"][:4])
            self.assertTrue(all(row["status"] == "planned" for row in self.rows(args)[4:]))
            self.assertEqual(continuation.file_hashes(prior), before)
            self.assertEqual(len(list(args.output.glob("3-r*"))), 4)

    def test_contract_or_launch_failure_keeps_unexecuted_denominator(self):
        for failure in ("contract_failure", "launch_failure"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                args = self.args(Path(tmp), repetitions=10)
                status, calls, _ = self.execute(args, statuses=[failure])
                self.assertEqual(status, 1)
                self.assertEqual(len(calls), 1)
                rows = self.rows(args)
                self.assertEqual(len(rows), 20)
                self.assertEqual(rows[0]["status"], failure)
                self.assertTrue(all(r["status"] == "planned" and r["input_tokens"] is None for r in rows[1:]))

    def test_source_or_original_seed_mutation_stops_after_first_trial(self):
        for target in ("source", "seed"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                args = self.args(Path(tmp))

                def mutate(source, working, seed):
                    ((source / "资料.md") if target == "source" else seed / "vectors.bin").write_text("changed")

                status, calls, _ = self.execute(args, mutate=mutate)
                self.assertEqual(status, 1)
                self.assertEqual(len(calls), 1)
                rows = self.rows(args)
                self.assertEqual(rows[0]["status"], "integrity_failure")
                self.assertFalse(rows[0]["source_unchanged" if target == "source" else "original_seed_unchanged"])
                self.assertTrue(all(r["status"] == "planned" for r in rows[1:]))

    def test_preparation_source_change_fails_before_any_trial_and_retains_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "Source changed"):
                self.execute(args, mutate_preparation=True)
            self.assertTrue((args.output / "failure.json").is_file())
            preparation = json.loads((args.output / "preparation/runtime/preparation.json").read_text())
            self.assertEqual(preparation["status"], "failed")
            self.assertTrue(all(r["status"] == "planned" for r in self.rows(args)))

    def test_completed_native_seed_survives_qa_failure_and_hits_for_new_question_and_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            first = self.args(root, name="first")
            self.execute(first, cache=cache, statuses=["contract_failure"])
            second = self.args(root, name="second")
            second.question_file.write_text("不同的原始问题。")
            status, calls, builds = self.execute(second, cache=cache, commit="different-commit")
            self.assertEqual(status, 0)
            self.assertEqual(len(calls), 4)
            self.assertEqual([b["check_only"] for b in builds], [True])
            manifest = json.loads((second.output / "manifest.json").read_text())
            self.assertEqual(manifest["index_preparation"]["cache"]["status"], "hit")
            self.assertFalse(manifest["index_preparation"]["cache"]["published"])
            self.assertEqual(len(list(cache.glob("*/COMPLETE"))), 1)
            self.assertFalse(any(p.name in {"trial-results.json", "native-spec.json"} for p in cache.rglob("*")))

    def test_corrupt_cached_content_is_rebuilt_before_trials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            self.execute(self.args(root, name="first"), cache=cache)
            next(cache.glob("*/index/vectors.bin")).write_bytes(b"corrupt")
            second = self.args(root, name="second")
            status, _, builds = self.execute(second, cache=cache)
            self.assertEqual(status, 0)
            self.assertEqual([b["check_only"] for b in builds], [False])
            preparation = json.loads((second.output / "manifest.json").read_text())["index_preparation"]
            self.assertEqual(preparation["cache"]["status"], "miss")
            self.assertIn("hashes mismatch", preparation["cache"]["reason"])

    def test_cached_native_readiness_failure_is_preserved_without_launching_or_resampling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            self.execute(self.args(root, name="first"), cache=cache)
            second = self.args(root, name="second")
            with self.assertRaisesRegex(RuntimeError, "native status failed"):
                self.execute(second, cache=cache, prepare_error="native status failed")
            prep = json.loads((second.output / "preparation/runtime/preparation.json").read_text())
            self.assertEqual(prep["cache"]["status"], "hit")
            self.assertEqual(prep["status"], "failed")
            self.assertTrue(all(r["status"] == "planned" for r in self.rows(second)))

    def test_native_protocol_cannot_restore_an_old_bridge_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kwargs = dict(embedding_model=runner.EMBEDDING, endpoint=runner.ZVEC_GREP_EMBEDDING_ENDPOINT,
                          max_file_size_bytes=1048576)
            with patch.object(seed_cache, "build_identity", return_value={"fixture_runtime": RUNTIME}):
                old = seed_cache.make_identity({"资料.md": "hash"}, RUNTIME, protocol="workspace-qa-qoder-v2", **kwargs)
                new = seed_cache.make_identity({"资料.md": "hash"}, RUNTIME, protocol=native_runner.PROTOCOL, **kwargs)
            seed = root / "seed"
            seed.mkdir()
            (seed / "data").write_text("old SDK bridge index")
            saved = seed_cache.publish(root / "cache", seed, old,
                {"status": "completed", "fresh": True, "source_unchanged": True}, preflight_passed=True)
            self.assertEqual(saved["status"], "saved")
            restored = seed_cache.restore(root / "cache", root / "restored", new)
            self.assertEqual(restored["status"], "miss")
            self.assertNotEqual(seed_cache.digest(old), seed_cache.digest(new))
            self.assertFalse((root / "restored/data").exists())

    def test_native_index_container_uses_cli_entrypoint_and_named_remote_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, index, logs, cache = [root / name for name in ("source", "index", "logs", "cache")]
            source.mkdir()
            captured = []

            def run(command, name, **kwargs):
                captured.append((command, kwargs))
                runner.write_json(logs / "native-index.json", {"status": "completed", "protocol": native_runner.PROTOCOL})

            with patch.dict("os.environ", {"QWEN_API_KEY": "not-in-command"}, clear=True), \
                    patch.object(runner, "run_named", side_effect=run):
                result = native_runner.native_index(source, index, logs, cache, image=native_runner.IMAGE, check_only=True)
            command, options = captured[0]
            self.assertEqual(result["status"], "completed")
            self.assertIn("/opt/qa/native-index.py", command)
            self.assertEqual(command.count("--init"), 1)
            self.assertLess(command.index("--init"), command.index(native_runner.IMAGE))
            self.assertIn("--check-only", command)
            self.assertIn("QWEN_API_KEY", command)
            self.assertIn("ZVEC_GREP_EMBEDDING=" + runner.EMBEDDING, command)
            self.assertNotIn("not-in-command", " ".join(command))
            self.assertNotIn("ZG_QA_ALLOW_REMOTE_EMBEDDING", " ".join(command))
            self.assertNotIn(runner.SPEC.credential_env, command)
            self.assertEqual(options["timeout"], 1800)

    def trial(self, root, profile, *, invalid_proof=False, session_status="completed", startup_status="passed"):
        source, agent, cache = [root / name for name in ("source", "agent", "cache")]
        source.mkdir()
        index = root / "index" if profile == "with-zg" else None
        if index:
            index.mkdir()
        captured = []

        def launch(command, **kwargs):
            captured.append(command)
            runner.write_json(agent / "install-manifest.json", {"setup_wall_seconds": 9.0})
            runner.write_json(agent / "session.json", {"status": session_status, "wall_seconds": 7.5})
            return SimpleNamespace(wait=lambda **kw: int(session_status != "completed"))

        proof = {"verified": True, "profile": profile, "installed": index is not None, "setup_wall_seconds": 9.0}
        with patch.dict("os.environ", {"QWEN_API_KEY": "fake-embedding-secret"}, clear=True), \
                patch.object(native_runner.subprocess, "Popen", side_effect=launch), \
                patch.object(native_runner.time, "monotonic", side_effect=[100.0, 120.0]), \
                patch.object(runner, "cleanup_container"), \
                patch("qoder_probe.native_startup_evidence", return_value={"status": startup_status}) as startup, \
                patch.object(native_runner, "validate_installation", return_value=proof,
                    side_effect=ValueError("invalid install fake-embedding-secret") if invalid_proof else None) as validate, \
                patch.object(runner, "convert_agent_trace", return_value={"has_final_answer": True, "contract_error_count": 0}), \
                patch.object(runner, "trial_metrics", return_value={"input_tokens": 120, "tool_calls": 2, "answer": ANSWER}):
            result = native_runner.run_native_trial(source, agent, index, cache, prompt="原始提示", profile=profile, limits=LIMITS)
            validate.assert_called_once_with(agent, profile=profile)
            startup.assert_called_once_with(agent)
        return result, captured[0], json.loads((agent / "native-spec.json").read_text())

    def test_native_session_profiles_isolate_embedding_and_separate_installation_wall_time(self):
        for profile in ("baseline", "with-zg"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                result, command, spec = self.trial(Path(tmp), profile)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["wall_seconds"], 7.5)
                self.assertEqual(result["installation_wall_seconds"], 9.0)
                self.assertEqual(result["container_total_wall_seconds"], 20.0)
                self.assertEqual(result["installation"]["installed"], profile == "with-zg")
                self.assertEqual(spec["profile"], profile)
                self.assertEqual(spec["prompt"], "原始提示")
                self.assertEqual(spec["model"], "Qwen3.8-Max")
                self.assertEqual(spec["limits"], LIMITS)
                self.assertIn("/opt/qa/native-session.py", command)
                self.assertEqual(command.count("--init"), 1)
                self.assertLess(command.index("--init"), command.index(native_runner.IMAGE))
                self.assertIn(runner.SPEC.credential_env, command)
                self.assertEqual("QWEN_API_KEY" in command, profile == "with-zg")
                mounts = [v for v in command if v.startswith("type=bind,")]
                self.assertTrue(any(v.endswith("target=/app,readonly") for v in mounts))
                self.assertEqual(any("target=/app/.zvec-grep" in v for v in mounts), profile == "with-zg")
                self.assertNotIn("fake-embedding-secret", " ".join(command))

    def test_invalid_installation_proof_fails_an_otherwise_successful_trial(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _ = self.trial(Path(tmp), "with-zg", invalid_proof=True)
            self.assertEqual(result["status"], "contract_failure")
            self.assertIn("invalid install [REDACTED]", result["installation_error"])
            self.assertEqual(result["input_tokens"], 120)
            self.assertEqual(result["answer"], ANSWER)

    def test_native_session_budget_failure_is_not_overwritten_by_valid_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _ = self.trial(Path(tmp), "with-zg", session_status="budget_exhausted")
            self.assertEqual(result["status"], "budget_exhausted")
            self.assertEqual(result["wall_seconds"], 7.5)
            self.assertEqual(result["input_tokens"], 120)

    def test_security_startup_failure_keeps_observations_and_existing_budget_outcomes(self):
        for profile in ("baseline", "with-zg"):
            for startup in ("failed", "incomplete"):
                for session in ("completed", "budget_exhausted"):
                    with self.subTest(profile=profile, startup=startup, session=session), tempfile.TemporaryDirectory() as tmp:
                        result, _, _ = self.trial(Path(tmp), profile, session_status=session, startup_status=startup)
                        self.assertEqual(result["status"], "contract_failure" if session == "completed" else session)
                        self.assertEqual(result["startup_evidence"]["status"], startup)
                        self.assertEqual(result["input_tokens"], 120)
                        self.assertEqual(result["wall_seconds"], 7.5)
                        self.assertEqual(result["answer"], ANSWER)

    def test_profile_index_mismatch_fails_without_starting_a_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(native_runner.subprocess, "Popen") as launch:
                for profile, index in (("baseline", root / "index"), ("with-zg", None)):
                    with self.assertRaisesRegex(ValueError, "Only the with-zg"):
                        native_runner.run_native_trial(root, root / "agent", index, root / "cache",
                            prompt="question", profile=profile, limits=LIMITS)
                launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
