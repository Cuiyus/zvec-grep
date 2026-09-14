from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("workspace_qa_ci_plan_test", ROOT / "ci_plan.py")
ci_plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_plan)


class ScopeTests(unittest.TestCase):
    def test_push_requires_explicit_head_commit_marker(self):
        for message, expected in (("fix tests", "validate"), ("[workspace-qa-smoke] fix", "smoke"),
                                  ("run [workspace-qa-full]", "full"),
                                  ("[workspace-qa-smoke] [workspace-qa-full]", "full")):
            with self.subTest(message=message):
                self.assertEqual(ci_plan.execution_scope({"head_commit": {"message": message}}, "push"), expected)
        self.assertEqual(ci_plan.execution_scope({"head_commit": {"message": "ordinary"},
                                                 "commits": [{"message": "[workspace-qa-full]"}]}, "push"), "validate")
        self.assertEqual(ci_plan.execution_scope({"head_commit": None}, "push"), "validate")

    def test_dispatch_is_exact_and_other_events_cannot_opt_in(self):
        for scope in ("validate", "smoke", "full"):
            self.assertEqual(ci_plan.execution_scope({"inputs": {"scope": scope}}, "workflow_dispatch"), scope)
        for inputs in ({}, {"scope": "full\nsecond=value"}, {"scope": "ALL"}):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                ci_plan.execution_scope({"inputs": inputs}, "workflow_dispatch")
        self.assertEqual(ci_plan.execution_scope({"head_commit": {"message": "[workspace-qa-full]"}}, "pull_request"), "validate")

    def test_commit_text_is_data_and_github_output_contains_only_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "must-not-exist"
            event = root / "event.json"
            event.write_text(json.dumps({"head_commit": {"message": f"$(touch {marker}) `touch {marker}`\n[workspace-qa-smoke]\nmalicious=value"}}))
            output = root / "github-output"
            with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event), "GITHUB_EVENT_NAME": "push", "GITHUB_OUTPUT": str(output)}, clear=True):
                self.assertEqual(ci_plan.main([]), 0)
            self.assertFalse(marker.exists())
            self.assertEqual(output.read_text(), "scope=smoke\n")


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository = self.root / "repository"
        for relative in ci_plan.INDEX_DEPENDENCIES:
            path = self.repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test construction dependency: " + relative)
        self.lock = {"workspace": {"repo": "workspace/repo", "revision": "frozen-revision", "archive": "all.zip",
                                   "sha256": "a" * 64, "size_bytes": 18861940415},
                     "experiment": {"zg_version": "0.2.2", "embedding": {"type": "remote", "model": "qwen/embedding", "allow_local_fallback": False},
                                    "index": {"max_file_size_bytes": 1048576}, "corpus_policy": "full-persona"},
                     "tasks": [{"task_id": "128", "persona": "Researcher"}, {"task_id": "127", "persona": "Researcher"},
                               {"task_id": "160", "persona": "Operations Manager"}]}
        self.roots = {"Researcher": "Research_Workdir", "Operations Manager": "OperationsManager_Workdir"}

    def plan(self, lock=None, task="128", **kwargs):
        options = {"repository": self.repository, "persona_roots": self.roots, "runner_os": "Linux", "runner_arch": "X64",
                   "embedding_endpoint": "https://example.invalid/v1/embeddings"}
        options.update(kwargs)
        return ci_plan.cache_plan(lock or self.lock, task, self.root / "cache", **options)

    def test_exact_same_persona_keys_and_distinct_range_index_directories(self):
        first, second, other = self.plan(), self.plan(task="127"), self.plan(task="160")
        for kind in ("range", "index"):
            self.assertEqual(first[kind + "_cache_key"], second[kind + "_cache_key"])
            self.assertEqual(first[kind + "_cache_path"], second[kind + "_cache_path"])
            self.assertNotEqual(first[kind + "_cache_key"], other[kind + "_cache_key"])
            self.assertTrue(Path(first[kind + "_cache_path"]).is_relative_to((self.root / "cache" / kind).resolve()))
            self.assertEqual(len(first[kind + "_cache_key"].rsplit("-", 1)[1]), 64)
        self.assertNotEqual(first["range_cache_path"], first["index_cache_path"])
        self.assertEqual(first["range_cache_bytes"], 4294967296)
        self.assertFalse((self.root / "cache").exists(), "Planning must not create or modify caches")

    def test_archive_identity_os_arch_block_format_and_budget_change_both_keys(self):
        baseline = self.plan()
        variants = []
        for field, value in (("revision", "new-revision"), ("sha256", "b" * 64), ("size_bytes", 20000000000), ("archive", "new.zip")):
            lock = deepcopy(self.lock)
            lock["workspace"][field] = value
            variants.append(self.plan(lock))
        variants.extend([self.plan(runner_os="Windows"), self.plan(runner_arch="ARM64")])
        for name, value in (("RANGE_BLOCK_BYTES", 8 * 1024 * 1024), ("RANGE_CACHE_BYTES", 2 * 1024**3), ("RANGE_FORMAT_VERSION", 2)):
            with patch.object(ci_plan, name, value):
                variants.append(self.plan())
        for variant in variants:
            for kind in ("range", "index"):
                self.assertNotEqual(baseline[kind + "_cache_key"], variant[kind + "_cache_key"])

    def test_index_binding_to_embedding_and_every_build_dependency(self):
        baseline = self.plan()
        lock = deepcopy(self.lock)
        lock["experiment"]["embedding"]["model"] = "qwen/another-embedding"
        variants = [self.plan(lock), self.plan(embedding_endpoint="https://different.invalid/embeddings")]
        for relative in ci_plan.INDEX_DEPENDENCIES:
            path = self.repository / relative
            previous = path.read_bytes()
            path.write_bytes(previous + b" changed")
            variants.append(self.plan())
            path.write_bytes(previous)
        for variant in variants:
            self.assertEqual(baseline["range_cache_key"], variant["range_cache_key"])
            self.assertNotEqual(baseline["index_cache_key"], variant["index_cache_key"])

    def test_commit_task_query_and_unrelated_file_do_not_invalidate_cache(self):
        baseline = self.plan()
        (self.repository / "unrelated.md").write_text("unrelated change")
        lock = deepcopy(self.lock)
        lock["tasks"][0].update(question="different task query", rubric="different grading")
        lock["git_commit"] = "changed-head"
        with patch.dict(os.environ, {"GITHUB_SHA": "another-head", "GITHUB_RUN_ID": "99"}):
            self.assertEqual(baseline, self.plan(lock))

    def test_actual_node_version_invalidates_index_but_image_creation_digest_does_not(self):
        runtime = {"installed_versions": {"zg": "0.2.2", "qoder": "1.1.45", "node": "v24.1.0"},
                   "os": "linux", "architecture": "amd64", "image_id": "volatile-image-id"}
        first = self.plan(runtime_identity=runtime)
        self.assertEqual(first["index_runtime_bound"], "true")
        runtime["image_id"] = "different-image-build"
        self.assertEqual(first, self.plan(runtime_identity=runtime))
        runtime["installed_versions"]["node"] = "v24.1.1"
        second = self.plan(runtime_identity=runtime)
        self.assertNotEqual(first["index_cache_key"], second["index_cache_key"])
        self.assertEqual(first["range_cache_key"], second["range_cache_key"])

    def test_invalid_configuration_or_output_injection_fails_closed(self):
        for field, value in (("zg_version", "0.2.3"), ("index", {"max_file_size_bytes": 2 * 1024**2})):
            lock = deepcopy(self.lock)
            lock["experiment"][field] = value
            with self.assertRaises(ValueError):
                self.plan(lock)
        for endpoint in ("https://secret@example.invalid/v1", "https://example.invalid/v1?token=secret", "file:///tmp/vector"):
            with self.assertRaises(ValueError):
                self.plan(embedding_endpoint=endpoint)
        with self.assertRaises(ValueError):
            self.plan(task="unknown")
        with self.assertRaises(ValueError):
            ci_plan.write_outputs(self.root / "output", {"index_cache_path": "path\nunauthorized=value"})
        self.assertFalse((self.root / "output").exists())

    def test_real_lock_uses_dataset_persona_mapping_without_loading_agent_runtime(self):
        lock = ci_plan.read_object(ROOT / "data/lock.json")
        output = ci_plan.cache_plan(lock, "128", self.root / "cache", runner_os="Linux", runner_arch="X64")
        self.assertEqual(output["persona_root"], "Research_Workdir")
        self.assertEqual(output["persona"], "Researcher")


if __name__ == "__main__":
    unittest.main()
