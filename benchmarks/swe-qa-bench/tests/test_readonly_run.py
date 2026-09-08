from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from zg_bench.swe_qa import readonly_run as runner
from zg_bench.swe_qa import readonly_agents as adapters


def seed_fixture(root: Path, name="seed", **overrides):
    target = root / name
    workspace = target / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "index.bin").write_bytes(b"frozen index")
    identity = {"repo_commit": "frozen", "embedding_model": runner.EMBEDDING,
                "workdir": "/app", "zvec_grep_package": "@zvec/zvec-grep@0.2.1",
                **overrides}
    (target / "identity.json").write_text(json.dumps(identity))
    digest = hashlib.sha256(json.dumps(identity, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    (target / "complete").write_text(digest)
    return target, identity


class FakeProcess:
    def __init__(self, timeout=False, returncode=0):
        self.timeout = timeout
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("docker", timeout)
        return self.returncode

    def kill(self):
        self.killed = True


class ReadonlyRunTest(unittest.TestCase):
    def args(self, root: Path, *, dry_run=False):
        case = root / "case.json"
        case.write_text(json.dumps({"case_id": "reflex-6", "question": "Question without hints",
                                    "repo": {"url": "https://example.invalid/source.git", "commit": "frozen"},
                                    "evidence": [{"text": "HOST_ONLY_GOLD"}]}))
        return argparse.Namespace(case=case, output=root / "output", seed_dir=root / "seeds",
                                  repetitions=5, order_seed=1729, dry_run=dry_run,
                                  image="pinned:test", model="custom-openai/glm-5.2", timeout=1)

    def run_mocked(self, args, *, process_factory=None, convert=None, retrieval_timeout=False,
                   on_launch=None, on_retrieval=None, on_verify=None, on_build=None,
                   operation_events=None, on_cleanup=None, controlled_convert=None,
                   environment=None, on_launch_kwargs=None):
        checked = []
        commands = []
        processes = []
        launch_count = 0

        def checked_command(command, **kwargs):
            checked.append(command)
            if operation_events is not None:
                operation_events.append(("checked", command))
            if command[:2] == ["git", "init"]:
                source = Path(command[2])
                source.mkdir(parents=True)
                (source / "core.py").write_text("frozen source\n")
                (source / ".git").mkdir()
            if "rev-parse" in command:
                return "frozen"
            if command[:3] == ["docker", "image", "inspect"]:
                return json.dumps([{"Id": "sha256:fixed", "RepoDigests": []}])
            if runner.PREPARE_INDEX in command:
                target = self.mount_source(command, "/app/.zvec-grep")
                (target / "index.bin").write_bytes(b"freshly built index")
                if on_build:
                    on_build(command, kwargs)
                return '{"status":"completed","indexed_files":1}'
            if "preflight" in command:
                output = args.output / "preparation" / "runtime"
                output.mkdir(parents=True, exist_ok=True)
                (output / "snapshot.json").write_text("{}")
            if "verify" in command and on_verify:
                on_verify(command)
            return ""

        def run(command, **kwargs):
            commands.append(command)
            if operation_events is not None:
                operation_events.append(("run", command))
            if command[:3] == ["docker", "rm", "--force"] and on_cleanup:
                on_cleanup(command)
            if retrieval_timeout and "retrieve" in command and "fts" in command:
                raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))
            if "retrieve" in command and on_retrieval:
                on_retrieval(command)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        def popen(command, **kwargs):
            nonlocal launch_count
            commands.append(command)
            if operation_events is not None:
                operation_events.append(("popen", command))
            index = launch_count
            launch_count += 1
            if on_launch:
                on_launch(command, index)
            if on_launch_kwargs:
                on_launch_kwargs(command, index, kwargs)
            process = process_factory(index) if process_factory else FakeProcess()
            processes.append(process)
            return process

        def convert_default(*args, **kwargs):
            return {"event_count": 1, "error_event_count": 0,
                    "has_final_answer": True,
                    "final_metrics": {"total_prompt_tokens": 50, "total_completion_tokens": 5}}

        with (patch.dict("os.environ", environment or {"GLM_API_KEY": "mock-secret"}, clear=True),
              patch.object(runner, "run_checked", side_effect=checked_command),
              patch.object(runner.subprocess, "run", side_effect=run),
              patch.object(runner.subprocess, "Popen", side_effect=popen),
              patch.object(runner, "convert_trace", side_effect=convert or convert_default),
              patch.object(adapters, "convert_agent_trace", side_effect=controlled_convert or convert_default),
              redirect_stdout(io.StringIO())):
            code = runner.execute_experiment(args)
        return code, checked, commands

    def test_five_by_five_reproducible_paired_plan(self):
        plan = runner.make_plan("reflex-6")
        self.assertEqual(plan, runner.make_plan("reflex-6"))
        self.assertEqual(len({t["trial_id"] for t in plan["trials"]}), 10)
        for repetition in range(1, 6):
            block = [t for t in plan["trials"] if t["repetition"] == repetition]
            self.assertEqual({t["profile"] for t in block}, set(runner.PROFILES))
        self.assertTrue(all(t["status"] == "planned" for t in plan["trials"]))
        with self.assertRaises(ValueError):
            runner.make_plan("reflex-6", repetitions=3)

    def test_dry_plan_does_not_overwrite_and_never_calls_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory), dry_run=True)
            with patch.object(runner, "run_checked") as execute, redirect_stdout(io.StringIO()):
                self.assertEqual(runner.execute_experiment(args), 0)
                original = (args.output / "plan.json").read_bytes()
                with self.assertRaises(ValueError):
                    runner.execute_experiment(args)
                execute.assert_not_called()
            self.assertEqual(original, (args.output / "plan.json").read_bytes())

    def test_seed_uses_matching_complete_content_identity_not_old_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed_fixture(root, "wrong-commit", repo_commit="different")
            seed_fixture(root, "wrong-model", embedding_model="another/model")
            seed_fixture(root, "wrong-root", workdir="/different")
            incomplete, _ = seed_fixture(root, "incomplete")
            (incomplete / "complete").write_text("not the identity hash")
            correct, identity = seed_fixture(root, "valid")
            workspace, found = runner.choose_seed(root, "frozen")
            self.assertEqual(workspace, correct / "workspace")
            self.assertEqual(found, identity)
            self.assertEqual(runner.PACKAGE, "@zvec/zvec-grep@0.2.2")

    def test_missing_seed_never_builds_an_index_and_retains_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            with (patch.dict("os.environ", {"GLM_API_KEY": "mock-secret"}, clear=True),
                  patch.object(runner, "run_checked") as execute):
                with self.assertRaisesRegex(RuntimeError, "EXISTING"):
                    runner.execute_experiment(args)
                execute.assert_not_called()
            self.assertEqual(len(json.loads((args.output / "plan.json").read_text())["trials"]), 10)

    def test_default_without_seed_builds_once_before_all_measured_work_and_freezes_index(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            args.seed_dir = None
            events = []
            built_indexes_seen_by_queries = []

            def observe_query(command):
                copied = self.mount_source(command, "/app/.zvec-grep")
                self.assertEqual((copied / "index.bin").read_bytes(), b"freshly built index")
                built_indexes_seen_by_queries.append(copied)

            code, checked, commands = self.run_mocked(args, operation_events=events,
                on_retrieval=observe_query,
                on_launch=lambda command, index: observe_query(command) if self.mount_source(command, "/app/.zvec-grep") else None)
            self.assertEqual(code, 0)
            build_positions = [i for i, (_, command) in enumerate(events) if runner.PREPARE_INDEX in command]
            self.assertEqual(len(build_positions), 1)
            build_position = build_positions[0]
            measured_positions = [i for i, (kind, command) in enumerate(events)
                                  if "preflight" in command or "retrieve" in command or kind == "popen"]
            self.assertTrue(all(build_position < i for i in measured_positions))
            build = events[build_position][1]
            source_mount = next(build[i + 1] for i, value in enumerate(build)
                                if value == "--mount" and "target=/app," in build[i + 1])
            self.assertTrue(source_mount.endswith(",readonly"))
            original = (args.output / "preparation" / "index").resolve()
            self.assertEqual(self.mount_source(build, "/app/.zvec-grep"), original)
            for _, command in events[build_position + 1:]:
                self.assertNotEqual(self.mount_source(command, "/app/.zvec-grep"), original)
            self.assertNotIn("GLM_API_KEY", " ".join(build))
            self.assertNotIn("OPENAI_API_KEY", " ".join(build))
            self.assertNotIn("mock-secret", " ".join(build))
            self.assertNotIn("--env-file", build)
            self.assertNotIn("HOST_ONLY_GOLD", " ".join(build))
            self.assertNotIn("Question without hints", " ".join(build))
            self.assertEqual(len(set(built_indexes_seen_by_queries)), 8)
            self.assertEqual((original / "index.bin").read_bytes(), b"freshly built index")
            preparation = json.loads((args.output / "preparation" / "runtime" / "preparation.json").read_text())
            self.assertEqual(preparation["mode"], "build_once")
            self.assertEqual(preparation["status"], "completed")
            self.assertFalse(preparation["included_in_qa_tokens_or_toolcalls"])
            self.assertGreaterEqual(preparation["wall_seconds"], 0)
            manifest = json.loads((args.output / "manifest.json").read_text())
            self.assertEqual(manifest["index_files"]["index.bin"], hashlib.sha256(b"freshly built index").hexdigest())
            self.assertEqual(manifest["seed_identity"]["origin"], "built_once_in_this_experiment")
            self.assertEqual(manifest["index_build_scope"], "preparation_only")
            self.assertTrue(all(json.loads(p.read_text())["original_seed_unchanged"] for p in args.output.glob("reflex-6-*/result.json")))

    def test_build_failure_records_preparation_failure_and_retains_all_unrun_trials(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            args.seed_dir = None
            events = []

            def fail_build(command, kwargs):
                self.assertEqual(kwargs["diagnostic_path"].name, "index-build-failure.json")
                raise RuntimeError("index preparation failed")

            with self.assertRaisesRegex(RuntimeError, "index preparation failed"):
                self.run_mocked(args, on_build=fail_build, operation_events=events)
            self.assertEqual(sum(runner.PREPARE_INDEX in command for _, command in events), 1)
            self.assertFalse(any(kind == "popen" or "retrieve" in command or "preflight" in command for kind, command in events))
            self.assertTrue(any(command[:3] == ["docker", "rm", "--force"] for _, command in events))
            plan = json.loads((args.output / "plan.json").read_text())
            self.assertEqual(len(plan["trials"]), 10)
            self.assertTrue(all(trial["status"] == "planned" for trial in plan["trials"]))
            preparation = json.loads((args.output / "preparation" / "runtime" / "preparation.json").read_text())
            self.assertEqual(preparation["status"], "failed")
            self.assertFalse(preparation["included_in_qa_tokens_or_toolcalls"])
            self.assertGreaterEqual(preparation["wall_seconds"], 0)
            self.assertEqual(list(args.output.glob("reflex-6-*/result.json")), [])

    def test_source_mutation_during_build_is_not_frozen_as_the_new_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            args.seed_dir = None
            events = []

            def mutate_source(command, kwargs):
                source = self.mount_source(command, "/app")
                (source / "core.py").write_text("changed during setup\n")

            with self.assertRaisesRegex(RuntimeError, "Source changed"):
                self.run_mocked(args, on_build=mutate_source, operation_events=events)
            self.assertFalse(any(kind == "popen" or "retrieve" in command or "preflight" in command for kind, command in events))
            preparation = json.loads((args.output / "preparation" / "runtime" / "preparation.json").read_text())
            self.assertEqual(preparation["status"], "integrity_failure")
            plan = json.loads((args.output / "plan.json").read_text())
            self.assertTrue(all(trial["status"] == "planned" for trial in plan["trials"]))

    def test_build_cleanup_timeout_preserves_original_error_and_failed_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            args.seed_dir = None
            original_error = RuntimeError("original index preparation failure")

            def fail_build(command, kwargs):
                raise original_error

            def timeout_cleanup(command):
                preparation = json.loads((args.output / "preparation" / "runtime" / "preparation.json").read_text())
                self.assertEqual(preparation["status"], "failed")
                raise subprocess.TimeoutExpired(command, 30)

            with self.assertRaises(RuntimeError) as raised:
                self.run_mocked(args, on_build=fail_build, on_cleanup=timeout_cleanup)
            self.assertIs(raised.exception, original_error)
            preparation = json.loads((args.output / "preparation" / "runtime" / "preparation.json").read_text())
            self.assertEqual(preparation["status"], "failed")
            self.assertEqual(preparation["cleanup_error"], "TimeoutExpired")
            plan = json.loads((args.output / "plan.json").read_text())
            self.assertEqual(len(plan["trials"]), 10)
            self.assertTrue(all(trial["status"] == "planned" for trial in plan["trials"]))
            self.assertEqual(list(args.output.glob("reflex-6-*/result.json")), [])

    def test_newly_built_original_index_remains_frozen_after_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            args.seed_dir = None

            def corrupt_frozen_original(command, index):
                (args.output / "preparation" / "index" / "index.bin").write_bytes(b"unexpected incremental write")

            with self.assertRaisesRegex(RuntimeError, "integrity"):
                self.run_mocked(args, on_launch=corrupt_frozen_original)
            plan = json.loads((args.output / "plan.json").read_text())
            first = json.loads((args.output / plan["trials"][0]["trial_id"] / "result.json").read_text())
            self.assertEqual(first["status"], "integrity_failure")
            self.assertFalse(first["original_seed_unchanged"])
            self.assertTrue(all(trial["status"] == "planned" for trial in plan["trials"][1:]))

    def test_source_is_readonly_and_only_working_index_mount_is_writable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = runner.docker_command("image", root / "source", root / "agent-logs", root / "cache",
                                            index=root / "index", snapshot=root / "snapshot.json")
            mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
            for target in ("/app", "/run/qa/snapshot.json"):
                matching = [m for m in mounts if f"target={target}," in m]
                self.assertEqual(len(matching), 1)
                self.assertTrue(matching[0].endswith(",readonly"))
            index_mounts = [m for m in mounts if "target=/app/.zvec-grep" in m]
            self.assertEqual(len(index_mounts), 1)
            self.assertFalse(index_mounts[0].endswith(",readonly"))
            self.assertIn("/app/.zvec-grep/locks:rw,mode=1777", command)
            self.assertFalse(any(f"source={root}," in m for m in mounts))
            baseline = runner.common_config("custom-openai/glm-5.2", zg=False)
            treatment = runner.common_config("custom-openai/glm-5.2", zg=True)
            self.assertNotIn("mcp", baseline)
            self.assertIn("--working-copy", treatment["mcp"]["zvec_grep"]["command"])
            for config in (baseline, treatment):
                self.assertEqual(config["permission"]["*"], "deny")
                self.assertEqual(config["permission"]["read"], "allow")
                self.assertNotIn("bash", config["permission"])
                self.assertNotIn("HOST_ONLY_GOLD", json.dumps(config))

    @staticmethod
    def mount_source(command, target):
        for index, value in enumerate(command):
            if value == "--mount":
                fields = dict(part.split("=", 1) for part in command[index + 1].split(",") if "=" in part)
                if fields.get("target") == target:
                    return Path(fields["source"])
        return None

    def test_working_index_copies_do_not_share_files_or_change_seed_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed, _ = seed_fixture(root)
            source = seed / "workspace"
            (source / "index.bin").chmod(0o444)
            first = runner.working_index(source, root / "trial-one")
            second = runner.working_index(source, root / "trial-two")
            self.assertTrue((first / "locks").is_dir())
            self.assertTrue((first / "index.bin").stat().st_mode & 0o200)
            self.assertFalse((source / "index.bin").stat().st_mode & 0o200)
            (first / "index.bin").write_bytes(b"native storage header changed")
            (first / "new-lock-state").write_text("local only")
            self.assertEqual((source / "index.bin").read_bytes(), b"frozen index")
            self.assertEqual((second / "index.bin").read_bytes(), b"frozen index")
            self.assertFalse((second / "new-lock-state").exists())

    def test_fresh_copies_isolate_modes_and_trials_and_allow_verified_physical_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            seed, _ = seed_fixture(args.seed_dir)
            mounted_indexes = []
            trial_indexes = []

            def mutate_retrieval(command):
                copied = self.mount_source(command, "/app/.zvec-grep")
                self.assertIsNotNone(copied)
                self.assertEqual((copied / "index.bin").read_bytes(), b"frozen index")
                (copied / "index.bin").write_bytes(b"retrieval header drift")
                mounted_indexes.append(copied)
                self.assertIn("--working-copy", command)

            def mutate_trial(command, index):
                copied = self.mount_source(command, "/app/.zvec-grep")
                if copied:
                    self.assertEqual((copied / "index.bin").read_bytes(), b"frozen index")
                    (copied / "index.bin").write_bytes(f"trial {index} header drift".encode())
                    trial_indexes.append(copied)

            code, checked, commands = self.run_mocked(args, on_launch=mutate_trial, on_retrieval=mutate_retrieval)
            self.assertEqual(code, 0)
            self.assertEqual(len(set(mounted_indexes)), 3)
            self.assertEqual(len(set(trial_indexes)), 5)
            self.assertTrue(set(mounted_indexes).isdisjoint(trial_indexes))
            all_index_mounts = [self.mount_source(command, "/app/.zvec-grep") for command in checked + commands]
            self.assertNotIn((seed / "workspace").resolve(), all_index_mounts)
            self.assertNotIn((args.output / "preparation" / "index").resolve(), all_index_mounts)
            self.assertEqual((seed / "workspace" / "index.bin").read_bytes(), b"frozen index")
            self.assertEqual((args.output / "preparation" / "index" / "index.bin").read_bytes(), b"frozen index")
            verification = [command for command in checked if "verify" in command]
            self.assertEqual(len(verification), 5)
            self.assertTrue(all("--working-copy" in command for command in verification))
            for path in args.output.glob("reflex-6-*/result.json"):
                result = json.loads(path.read_text())
                self.assertEqual(result["status"], "completed")
                self.assertTrue(result["original_seed_unchanged"])
                self.assertTrue(result["index_unchanged"])
                if result["profile"] == "zvec-grep":
                    self.assertTrue(result["working_index_semantic_unchanged"])
                    self.assertEqual([change["path"] for change in result["working_index_physical_changes"]], ["index.bin"])
                else:
                    self.assertIsNone(result["working_index_semantic_unchanged"])

    def test_semantic_verification_failure_retains_plan_and_stops_later_trials(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            seed_fixture(args.seed_dir)

            def semantic_mismatch(command):
                raise RuntimeError("stored document/vector identity mismatch")

            with self.assertRaisesRegex(RuntimeError, "integrity"):
                self.run_mocked(args, on_verify=semantic_mismatch)
            plan = json.loads((args.output / "plan.json").read_text())
            first_zg = next(index for index, trial in enumerate(plan["trials"]) if trial["profile"] == "zvec-grep")
            self.assertEqual(plan["trials"][first_zg]["status"], "integrity_failure")
            self.assertTrue(all(t["status"] == "completed" for t in plan["trials"][:first_zg]))
            self.assertTrue(all(t["status"] == "planned" for t in plan["trials"][first_zg + 1:]))
            trial = plan["trials"][first_zg]
            result = json.loads((args.output / trial["trial_id"] / "result.json").read_text())
            self.assertTrue(result["original_seed_unchanged"])
            self.assertFalse(result["working_index_semantic_unchanged"])
            self.assertFalse(result["index_unchanged"])
            self.assertIn("identity mismatch", result["index_verification_error"])

    def test_original_cache_seed_mutation_fails_even_when_working_copy_verifies(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            seed, _ = seed_fixture(args.seed_dir)

            def mutate_seed(command, index):
                (seed / "workspace" / "index.bin").write_bytes(b"unexpected original seed mutation")

            with self.assertRaisesRegex(RuntimeError, "integrity"):
                self.run_mocked(args, on_launch=mutate_seed)
            plan = json.loads((args.output / "plan.json").read_text())
            result = json.loads((args.output / plan["trials"][0]["trial_id"] / "result.json").read_text())
            self.assertFalse(result["original_seed_unchanged"])
            self.assertFalse(result["index_unchanged"])
            self.assertEqual(result["status"], "integrity_failure")
            self.assertTrue(all(t["status"] == "planned" for t in plan["trials"][1:]))

    def test_run_checked_failure_diagnostics_and_exception_are_redacted(self):
        for error_type in (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            with self.subTest(error_type=error_type.__name__), tempfile.TemporaryDirectory() as directory:
                diagnostic = Path(directory) / "failure.json"
                stderr = b"provider error glm-test-secret and openai-test-secret; useful failure detail"
                error = (error_type(1, ["docker", "run"], stderr=stderr)
                         if error_type is subprocess.CalledProcessError
                         else error_type(["docker", "run"], 10, stderr=stderr))
                with (patch.dict("os.environ", {"GLM_API_KEY": "glm-test-secret", "OPENAI_API_KEY": "openai-test-secret"}, clear=True),
                      patch.object(runner.subprocess, "run", side_effect=error)):
                    with self.assertRaises(RuntimeError) as raised:
                        runner.run_checked(["docker", "run"], diagnostic_path=diagnostic)
                saved = diagnostic.read_text()
                for output in (str(raised.exception), saved):
                    self.assertNotIn("glm-test-secret", output)
                    self.assertNotIn("openai-test-secret", output)
                    self.assertIn("[REDACTED]", output)
                    self.assertIn("useful failure detail", output)
                self.assertEqual(json.loads(saved)["kind"], error_type.__name__)

    def test_directory_identity_detects_content_and_symlink_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file").write_text("first")
            (root / "link").symlink_to("file")
            (root / ".git").mkdir()
            (root / ".git" / "HEAD").write_text("initial")
            first = runner.directory_identity(root, skip_git=True)
            (root / ".git" / "HEAD").write_text("changed")
            self.assertEqual(first, runner.directory_identity(root, skip_git=True))
            (root / "file").write_text("second")
            self.assertNotEqual(first, runner.directory_identity(root, skip_git=True))
            second = runner.directory_identity(root, skip_git=True)
            (root / "link").unlink()
            (root / "link").symlink_to("different")
            self.assertNotEqual(second, runner.directory_identity(root, skip_git=True))

    def test_success_records_all_ten_source_and_index_immutability_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.args(root)
            seed_fixture(args.seed_dir)
            code, checked, commands = self.run_mocked(args)
            self.assertEqual(code, 0)
            plan = json.loads((args.output / "plan.json").read_text())
            self.assertTrue(all(t["status"] == "completed" for t in plan["trials"]))
            self.assertEqual(len(list(args.output.glob("reflex-6-*/result.json"))), 10)
            for path in args.output.glob("reflex-6-*/result.json"):
                result = json.loads(path.read_text())
                self.assertTrue(result["source_unchanged"])
                self.assertTrue(result["index_unchanged"])
            self.assertFalse(any("index" in command for command in checked + commands))
            self.assertFalse(any(runner.PREPARE_INDEX in command for command in checked + commands))
            preparation = json.loads((args.output / "preparation" / "runtime" / "preparation.json").read_text())
            self.assertEqual(preparation["mode"], "existing_seed")
            self.assertFalse(preparation["included_in_qa_tokens_or_toolcalls"])
            self.assertNotIn("mock-secret", (args.output / "manifest.json").read_text())
            self.assertNotIn("HOST_ONLY_GOLD", (args.output / "instruction.json").read_text())

    def test_timeout_preserved_even_when_partial_stream_contains_error(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            seed_fixture(args.seed_dir)
            code, _, _ = self.run_mocked(args, process_factory=lambda i: FakeProcess(timeout=(i == 0)),
                convert=lambda *a: {"event_count": 1, "error_event_count": 1, "final_metrics": {}})
            plan = json.loads((args.output / "plan.json").read_text())
            self.assertEqual(code, 1)
            self.assertEqual(plan["trials"][0]["status"], "timeout")
            self.assertEqual(len(list(args.output.glob("reflex-6-*/result.json"))), 10)

    def test_unconvertible_stream_is_failure_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            seed_fixture(args.seed_dir)

            def incomplete(*args):
                raise RuntimeError("terminal answer is missing")

            code, _, _ = self.run_mocked(args, convert=incomplete)
            self.assertEqual(code, 1)
            plan = json.loads((args.output / "plan.json").read_text())
            self.assertTrue(all(t["status"] in ("failed", "protocol_failure") for t in plan["trials"]))

    def test_retrieval_timeout_is_recorded_without_losing_planned_qa_trials(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            seed_fixture(args.seed_dir)
            self.run_mocked(args, retrieval_timeout=True)
            self.assertEqual(len(list(args.output.glob("reflex-6-*/result.json"))), 10)
            status = json.loads((args.output / "retrieval" / "fts.status.json").read_text())
            self.assertTrue(status.get("status") == "timeout" or "timeout" in str(status.get("error", "")))

    def test_launch_failure_is_recorded_and_later_trials_still_run(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            seed_fixture(args.seed_dir)

            def launch(index):
                if index == 0:
                    raise OSError("mock Docker launch failure")
                return FakeProcess()

            code, _, _ = self.run_mocked(args, process_factory=launch)
            plan = json.loads((args.output / "plan.json").read_text())
            self.assertEqual(code, 1)
            self.assertIn(plan["trials"][0]["status"], ("failed", "launch_failure"))
            self.assertEqual(len(list(args.output.glob("reflex-6-*/result.json"))), 10)
            self.assertTrue(all(t["status"] == "completed" for t in plan["trials"][1:]))

    def test_converter_identifies_trajectory_without_terminal_answer(self):
        class FakeAdapter:
            def __init__(self, **kwargs):
                pass

            def _convert_events_to_trajectory(self, events):
                return SimpleNamespace(
                    model_dump=lambda **kw: {"steps": [{"source": "agent", "message": "I will search",
                                                         "tool_calls": [{"function_name": "grep"}]}]},
                    final_metrics=SimpleNamespace(model_dump=lambda **kw: {"total_prompt_tokens": 25}),
                )

        module = ModuleType("harbor.agents.installed.opencode")
        module.OpenCode = FakeAdapter
        with tempfile.TemporaryDirectory() as directory:
            agent = Path(directory)
            (agent / "opencode.txt").write_text('{"type":"step_start"}\n')
            with patch.dict(sys.modules, {"harbor.agents.installed.opencode": module}):
                result = runner.convert_trace(agent, "custom-openai/glm-5.2", "question")
            self.assertFalse(result["has_final_answer"])

    def test_zero_exit_without_terminal_answer_is_protocol_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            seed_fixture(args.seed_dir)
            code, _, _ = self.run_mocked(args, convert=lambda *a: {
                "event_count": 1, "error_event_count": 0, "has_final_answer": False, "final_metrics": {}})
            self.assertEqual(code, 1)
            plan = json.loads((args.output / "plan.json").read_text())
            self.assertTrue(all(t["status"] == "protocol_failure" for t in plan["trials"]))

    def test_integrity_failure_retains_unrun_plan_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            seed_fixture(args.seed_dir)

            def mutate(*unused):
                (args.output / "preparation" / "index" / "index.bin").write_bytes(b"changed")
                return {"event_count": 1, "error_event_count": 0, "final_metrics": {}}

            with self.assertRaisesRegex(RuntimeError, "integrity"):
                self.run_mocked(args, convert=mutate)
            plan = json.loads((args.output / "plan.json").read_text())
            self.assertEqual(plan["trials"][0]["status"], "integrity_failure")
            self.assertTrue(all(t["status"] == "planned" for t in plan["trials"][1:]))


class ControlledRunTest(unittest.TestCase):
    """Exercise the complete v3 orchestration with native execution mocked."""

    run_mocked = ReadonlyRunTest.run_mocked
    mount_source = staticmethod(ReadonlyRunTest.mount_source)

    def args(self, root, *, agent="opencode"):
        args = ReadonlyRunTest.args(self, root)
        args.controlled = True
        args.agent = agent
        if agent == "qodercli":
            args.model = "custom-openai/qwen3.8-max"
        seed_fixture(args.seed_dir)
        return args

    def native_artifacts(self, args, command, index):
        agent_dir = self.mount_source(command, "/logs")
        session_spec = json.loads((agent_dir / "session-spec.json").read_text())
        session = {"status": "completed", "observed": {"model_requests": 2, "tool_calls": 1, "input_tokens": 50}}
        (agent_dir / "session.json").write_text(json.dumps(session))
        if args.agent == "opencode":
            spec = adapters.agent_spec(args.agent, args.model, base_url=runner.OPENCODE_CUSTOM_BASE_URL)
            zg = self.mount_source(command, "/app/.zvec-grep") is not None
            # Non-task metadata generation is retained; its temperature does
            # not replace the actual task's sampling-parameter evidence.
            wire = [
                {"event": "request", "request_id": "title", "model": spec.provider_model, "temperature": 0.8, "tool_names": []},
                {"event": "response", "request_id": "title", "model": spec.provider_model, "status": 200},
                {"event": "request", "request_id": "task", "model": spec.provider_model, "temperature": 0,
                 "tool_names": adapters.expected_tools(spec, zg=zg)},
                {"event": "response", "request_id": "task", "model": spec.provider_model, "status": 200},
            ]
            (agent_dir / "wire.jsonl").write_text("\n".join(map(json.dumps, wire)) + "\n")
        return agent_dir, session_spec

    def execute(self, args, *, on_launch=None, controlled_convert=None, **kwargs):
        def launch(command, index):
            agent_dir, session = self.native_artifacts(args, command, index)
            if on_launch:
                on_launch(command, index, agent_dir, session)

        return self.run_mocked(args, on_launch=launch, controlled_convert=controlled_convert,
                               environment={"GLM_API_KEY": "mock-secret", "QODER_PERSONAL_ACCESS_TOKEN": "mock-qoder-secret"},
                               **kwargs)

    def plan(self, args):
        return json.loads((args.output / "plan.json").read_text())

    def test_five_pairs_keep_exact_tools_budgets_private_env_and_config_mounts(self):
        for agent in ("opencode", "qodercli"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as directory:
                args = self.args(Path(directory), agent=agent)
                seen = []
                spec = adapters.agent_spec(agent, args.model, base_url=runner.OPENCODE_CUSTOM_BASE_URL if agent == "opencode" else None)

                def inspect(command, index, agent_dir, session):
                    zg = self.mount_source(command, "/app/.zvec-grep") is not None
                    config_path = self.mount_source(command, "/run/qa/" + spec.config_filename)
                    config = json.loads(config_path.read_text())
                    config_mount = next(command[i + 1] for i, value in enumerate(command)
                                        if value == "--mount" and f"target=/run/qa/{spec.config_filename}" in command[i + 1])
                    self.assertTrue(config_mount.endswith(",readonly"))
                    self.assertEqual(command[-4:], ["python3", "/opt/qa/qa-session.py", "--spec", "/logs/session-spec.json"])
                    self.assertIn(spec.credential_env, command)
                    self.assertEqual(session["limits"], {"model_requests": 30, "tool_calls": 60, "input_tokens": 300000, "wall_seconds": args.timeout})
                    self.assertEqual(session["native_name"], spec.stream_filename)
                    prompt = session["command"][-1]
                    expected = ", ".join(adapters.expected_tools(spec, zg=zg))
                    self.assertIn("Tools registered for this session: " + expected + ".", prompt)
                    self.assertTrue(prompt.startswith("Question without hints\n\n"))
                    self.assertEqual(json.loads((agent_dir.parent / "instruction.json").read_text())["text"], prompt)
                    for value in (json.dumps(command), json.dumps(session), json.dumps(config)):
                        self.assertNotIn("mock-secret", value)
                        self.assertNotIn("mock-qoder-secret", value)
                        self.assertNotIn("HOST_ONLY_GOLD", value)
                    if agent == "opencode":
                        self.assertEqual(config["agent"]["build"], {"temperature": 0, "steps": 30})
                        self.assertIs(config["provider"]["custom-openai"]["models"][spec.provider_model]["temperature"], True)
                        self.assertEqual(session["tap_upstream"], spec.base_url)
                        self.assertEqual("mcp" in config, zg)
                    else:
                        self.assertEqual(session["command"][session["command"].index("--max-turns") + 1], "30")
                        self.assertEqual(session["command"][session["command"].index("--permission-mode") + 1], "dont_ask")
                        self.assertIsNone(session["tap_upstream"])
                        self.assertEqual(bool(config["mcpServers"]), zg)
                    seen.append((zg, prompt))

                def inspect_env(command, index, kwargs):
                    expected_secret = "mock-secret" if agent == "opencode" else "mock-qoder-secret"
                    self.assertEqual(kwargs["env"][spec.credential_env], expected_secret)
                    self.assertNotIn(expected_secret, json.dumps(command))

                code, checked, commands = self.execute(args, on_launch=inspect, on_launch_kwargs=inspect_env)
                self.assertEqual(code, 0)
                plan = self.plan(args)
                self.assertEqual(len(seen), 10)
                self.assertEqual(sum(zg for zg, _ in seen), 5)
                self.assertTrue(all(t["status"] == "completed" for t in plan["trials"]))
                self.assertEqual({t["block_id"] for t in plan["trials"]}, {1, 2, 3, 4, 5})
                self.assertEqual(len(list(args.output.glob("reflex-6-*/result.json"))), 10)
                self.assertEqual(sum("verify" in c for c in checked), 5)
                manifest = json.loads((args.output / "manifest.json").read_text())
                self.assertEqual(manifest["protocol"], "readonly-qa-v3")
                self.assertEqual(manifest["agent_spec"]["credential_env"], spec.credential_env)
                self.assertEqual(manifest["run_limits"], plan["run_limits"])
                self.assertFalse((args.output / "preflight.json").exists())

    def test_model_catalog_and_temperature_mismatch_stop_after_first_keep_nine_planned(self):
        for mismatch in ("model", "catalog", "temperature", "missing_temperature"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as directory:
                args = self.args(Path(directory))

                def corrupt(command, index, agent_dir, session):
                    self.assertEqual(index, 0)
                    path = agent_dir / "wire.jsonl"
                    wire = [json.loads(line) for line in path.read_text().splitlines()]
                    if mismatch == "model":
                        wire[-1]["model"] = "different-model"
                    elif mismatch == "catalog":
                        wire[-2]["tool_names"].append("bash")
                    elif mismatch == "temperature":
                        wire[-2]["temperature"] = 0.8
                    else:
                        wire[-2].pop("temperature")
                    path.write_text("\n".join(map(json.dumps, wire)) + "\n")

                code, _, _ = self.execute(args, on_launch=corrupt)
                self.assertEqual(code, 1)
                plan = self.plan(args)
                self.assertEqual(plan["trials"][0]["status"], "contract_failure")
                self.assertEqual([t["status"] for t in plan["trials"][1:]], ["planned"] * 9)
                self.assertEqual(len(list(args.output.glob("reflex-6-*/result.json"))), 1)
                result = json.loads((args.output / plan["trials"][0]["trial_id"] / "result.json").read_text())
                self.assertTrue(result["wire_contract"]["configuration_mismatch"])
                self.assertEqual(json.loads((args.output / "preflight.json").read_text())["reason"], "contract_failure")

    def test_missing_response_and_budget_stop_fail_one_trial_without_stopping_combination(self):
        for failure in ("missing_response", "budget_exhausted", "missing_wire", "transport_error"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                args = self.args(Path(directory))

                def interrupt(command, index, agent_dir, session):
                    if index:
                        return
                    wire_path = agent_dir / "wire.jsonl"
                    if failure == "missing_wire":
                        wire_path.unlink()
                    elif failure == "budget_exhausted":
                        (agent_dir / "session.json").write_text(json.dumps({"status": "budget_exhausted", "limit_reason": "tool_calls"}))
                    else:
                        wire = [json.loads(line) for line in wire_path.read_text().splitlines()]
                        if failure == "missing_response":
                            wire.pop()
                        else:
                            wire[-1] = {"event": "response", "request_id": "task", "status": 502}
                        wire_path.write_text("\n".join(map(json.dumps, wire)) + "\n")

                code, _, _ = self.execute(args, on_launch=interrupt)
                self.assertEqual(code, 1)
                plan = self.plan(args)
                expected_status = "budget_exhausted" if failure == "budget_exhausted" else "measurement_failure"
                self.assertEqual(plan["trials"][0]["status"], expected_status)
                self.assertEqual([t["status"] for t in plan["trials"][1:]], ["completed"] * 9)
                self.assertEqual(len(list(args.output.glob("reflex-6-*/result.json"))), 10)
                self.assertFalse((args.output / "preflight.json").exists())

    def test_qoder_converter_contract_error_stops_with_nine_planned(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory), agent="qodercli")
            calls = []

            def converted(agent_dir, spec, instruction, *, zg):
                calls.append(agent_dir)
                return {"event_count": 3, "error_event_count": 1, "contract_error_count": 1,
                        "has_final_answer": True, "final_metrics": {"total_prompt_tokens": 50},
                        "model_identity": {"valid": False, "observed": ["auto"]}}

            code, _, _ = self.execute(args, controlled_convert=converted)
            self.assertEqual(code, 1)
            self.assertEqual(len(calls), 1)
            plan = self.plan(args)
            self.assertEqual(plan["trials"][0]["status"], "contract_failure")
            self.assertEqual([t["status"] for t in plan["trials"][1:]], ["planned"] * 9)

    def test_qoder_tool_errors_without_contract_failure_do_not_abort_remaining_trials(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory), agent="qodercli")

            def converted(agent_dir, spec, instruction, *, zg):
                return {"event_count": 4, "error_event_count": 0, "contract_error_count": 0,
                        "has_final_answer": True, "final_metrics": {"total_prompt_tokens": 50},
                        "tool_error_count": 1, "tool_contract": {"valid": True, "unexpected_tool_calls": ["wrong_name"]}}

            code, _, _ = self.execute(args, controlled_convert=converted)
            self.assertEqual(code, 0)
            plan = self.plan(args)
            self.assertEqual([t["status"] for t in plan["trials"]], ["completed"] * 10)
            for path in args.output.glob("reflex-6-*/result.json"):
                self.assertEqual(json.loads(path.read_text())["tool_error_count"], 1)


if __name__ == "__main__":
    unittest.main()
