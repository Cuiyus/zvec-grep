"""Offline integration checks against actual Git archives, with no solver calls."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("prepare_corpus", Path(__file__).with_name("prepare_corpus.py"))
corpus = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(corpus)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(files):
    return [{"path": path, "bytes": len(data), "sha256": digest(data)}
            for path, data in sorted(files.items())]


def inventory_digest(files):
    return digest(json.dumps(inventory(files), sort_keys=True, separators=(",", ":")).encode())


class PrepareCorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repository"
        self.repo.mkdir()
        self.benchmark = self.root / "benchmark"
        self.benchmark.mkdir()
        self.dataset_path = self.benchmark / "dataset.json"
        self.git("init", "-q")
        self.first_files = {"src/example.cc": b"int value = 1;\nreturn value;\n"}
        first = self.commit(self.first_files)
        self.second_files = {"src/example.cc": b"int value = 2;\nreturn value;\n",
                             "extra.txt": b"Only in the second revision.\n"}
        second = self.commit(self.second_files)
        self.dataset = {"tasks": [self.task("q001", first, self.first_files),
                                  self.task("q002", second, self.second_files)]}
        self.save_dataset()
        self.counter = 0

    def git(self, *args):
        return subprocess.check_output([
            "git", "-C", str(self.repo), "-c", "core.hooksPath=/dev/null",
            "-c", "commit.gpgsign=false", "-c", "user.name=Corpus Test",
            "-c", "user.email=corpus-test@example.invalid", *args,
        ], text=True).strip()

    def commit(self, files):
        for relative, content in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.git("add", "--all")
        self.git("commit", "-qm", "Fixture revision")
        return self.git("rev-parse", "HEAD")

    def task(self, identifier, revision, files):
        instruction = self.benchmark / "tasks" / identifier / "instruction.md"
        instruction.parent.mkdir(parents=True)
        instruction.write_text(f"Explain the behavior in task {identifier}.\n")
        rubric_path = self.benchmark / "evaluator" / f"{identifier}.json"
        rubric_path.parent.mkdir(exist_ok=True)
        content = files["src/example.cc"]
        rubric = {"task_id": identifier, "source_commit": revision, "evidence_spans": [{
            "id": "E01", "path": "src/example.cc", "start_line": 1, "end_line": 2,
            "file_sha256": digest(content), "span_sha256": digest(content),
        }]}
        rubric_path.write_text(json.dumps(rubric))
        return {"id": identifier, "source_repository": "https://fixture.invalid/source.git",
                "source_commit": revision,
                "instruction": instruction.relative_to(self.benchmark).as_posix(),
                "instruction_sha256": digest(instruction.read_bytes()),
                "evaluator": rubric_path.relative_to(self.benchmark).as_posix(),
                "evaluator_sha256": digest(rubric_path.read_bytes()),
                "forbidden_corpus_markers": ["PRIVATE_GOLD_MARKER"],
                "source_file_count": len(files), "source_inventory_sha256": inventory_digest(files)}

    def save_dataset(self):
        self.dataset_path.write_text(json.dumps(self.dataset))

    def change_rubric(self, mutate):
        task = self.dataset["tasks"][0]
        path = self.benchmark / task["evaluator"]
        rubric = json.loads(path.read_text())
        mutate(rubric)
        path.write_text(json.dumps(rubric))
        task["evaluator_sha256"] = digest(path.read_bytes())
        self.save_dataset()

    def export(self, task="q001", *, default_dataset=False, output=None, manifest=None):
        self.counter += 1
        output = output or self.root / f"source-{self.counter}"
        manifest = manifest or self.root / "controller" / f"manifest-{self.counter}.json"
        args = ["--repo", str(self.repo), "--output", str(output), "--manifest", str(manifest)]
        if not default_dataset:
            args += ["--dataset", str(self.dataset_path)]
        if task is not None:
            args += ["--task", task]
        with contextlib.redirect_stdout(io.StringIO()):
            corpus.main(args)
        return output, manifest

    def test_explicit_tasks_export_their_own_commits_and_complete_inventories(self):
        for identifier, files in (("q001", self.first_files), ("q002", self.second_files)):
            with self.subTest(task=identifier):
                output, manifest_path = self.export(identifier)
                exported = {p.relative_to(output).as_posix(): p.read_bytes()
                            for p in output.rglob("*") if p.is_file()}
                self.assertEqual(exported, files)
                manifest = json.loads(manifest_path.read_text())
                task = next(t for t in self.dataset["tasks"] if t["id"] == identifier)
                self.assertEqual(manifest, {
                    "source_repository": task["source_repository"], "source_commit": task["source_commit"],
                    "source_file_count": len(files), "inventory_sha256": inventory_digest(files),
                    "git_history_present": False, "evidence_spans_verified": 1,
                    "known_leakage_markers_found": False, "files": inventory(files),
                })

    def test_single_task_default_dataset_and_cli_preserve_manifest_bytes(self):
        # Mirrors the original invocation: no --dataset or --task, relative task paths.
        self.dataset["tasks"] = self.dataset["tasks"][:1]
        self.save_dataset()
        frozen = {p: p.read_bytes() for p in self.benchmark.rglob("*") if p.is_file()}
        _, explicit = self.export()
        with patch.object(corpus, "HERE", self.benchmark):
            _, implicit = self.export(None, default_dataset=True)
        self.assertEqual(implicit.read_bytes(), explicit.read_bytes())
        self.assertEqual({p: p.read_bytes() for p in frozen}, frozen)

    def test_existing_q001_default_dataset_paths_and_frozen_hashes_are_valid(self):
        dataset = json.loads((corpus.HERE / "dataset.json").read_text())
        task = corpus.select_task(dataset, None)
        self.assertEqual(task["id"], "q001")
        for key in ("instruction", "evaluator"):
            path = corpus.relative_file(corpus.HERE, task[key], key)
            self.assertEqual(digest(path.read_bytes()), task[f"{key}_sha256"])

    def test_multitask_default_and_unknown_ids_fail_before_export(self):
        for task, message in ((None, "explicit --task"), ("absent", "Unknown task ID")):
            with self.subTest(task=task), self.assertRaisesRegex(SystemExit, message):
                self.export(task)
        self.assertFalse(any(self.root.glob("source-*")))

    def test_duplicate_ids_rejected_even_when_selecting_another_task(self):
        self.dataset["tasks"].append(copy.deepcopy(self.dataset["tasks"][1]))
        self.save_dataset()
        with self.assertRaisesRegex(SystemExit, "Duplicate task ID: q002"):
            self.export("q001")
        self.assertFalse(any(self.root.glob("source-*")))

    def test_empty_dataset_and_invalid_ids_fail(self):
        for tasks in ([], [{"id": ""}], [{"id": 1}], [None]):
            with self.subTest(tasks=tasks), self.assertRaises(SystemExit):
                corpus.select_task({"tasks": tasks}, None)

    def test_instruction_and_evaluator_hash_drift_rejected(self):
        for key in ("instruction", "evaluator"):
            path = self.benchmark / self.dataset["tasks"][0][key]
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            with self.subTest(key=key), self.assertRaisesRegex(SystemExit, f"Frozen {key} hash mismatch"):
                self.export()
            path.write_bytes(original)

    def test_task_input_paths_cannot_escape_dataset_directory(self):
        task = self.dataset["tasks"][0]
        original = task["instruction"]
        outside = self.root / "outside-instruction.md"
        outside.write_bytes((self.benchmark / original).read_bytes())
        link = self.benchmark / "linked-instruction.md"
        link.symlink_to(outside)
        for value in (str(outside), "../outside-instruction.md", link.name):
            task["instruction"] = value
            self.save_dataset()
            with self.subTest(path=value), self.assertRaisesRegex(SystemExit, "instruction.*(relative|inside)"):
                self.export()
        self.assertFalse(any(self.root.glob("source-*")))

    def test_evaluator_identity_must_match_selected_task_and_source(self):
        for key, value in (("task_id", "q002"), ("source_commit", "0" * 40)):
            task = self.dataset["tasks"][0]
            path = self.benchmark / task["evaluator"]
            original = path.read_text()
            self.change_rubric(lambda rubric: rubric.update({key: value}))
            with self.subTest(key=key), self.assertRaisesRegex(SystemExit, "Evaluator task/source identity"):
                self.export()
            path.write_text(original)
            task["evaluator_sha256"] = digest(path.read_bytes())
            self.save_dataset()

    def test_source_revision_must_be_exact_commit_not_branch(self):
        self.dataset["tasks"][0]["source_commit"] = "HEAD"
        self.save_dataset()
        with self.assertRaisesRegex(SystemExit, "Source revision mismatch"):
            self.export()
        self.assertFalse(any(self.root.glob("source-*")))

    def test_invalid_ranges_cannot_pass_by_hashing_empty_or_truncated_slice(self):
        content = self.first_files["src/example.cc"]
        for start, end in ((0, 0), (-2, -1), (2, 1), (3, 3), (1, 99), (True, 2), (1, 2.0)):
            def mutate(rubric):
                span = rubric["evidence_spans"][0]
                span.update(start_line=start, end_line=end)
                # Preserve the hash that the old unchecked Python slice would accept.
                if type(start) is int and type(end) is int:
                    span["span_sha256"] = digest(b"".join(content.splitlines(keepends=True)[start - 1:end]))
            self.change_rubric(mutate)
            with self.subTest(start=start, end=end), self.assertRaisesRegex(SystemExit, "Invalid evidence line range"):
                self.export()

    def test_evidence_paths_cannot_read_outside_exported_source(self):
        outside = self.root / "outside.cc"
        outside.write_bytes(self.first_files["src/example.cc"])
        for value in (str(outside), "../outside.cc"):
            self.change_rubric(lambda rubric: rubric["evidence_spans"][0].update(path=value))
            with self.subTest(path=value), self.assertRaisesRegex(SystemExit, "Evidence path must be relative"):
                self.export()

    def test_file_and_span_hash_mismatches_rejected(self):
        for key, error in (("file_sha256", "Evidence file mismatch"), ("span_sha256", "Evidence span mismatch")):
            def mutate(rubric):
                span = rubric["evidence_spans"][0]
                span["file_sha256"] = digest(self.first_files["src/example.cc"])
                span[key] = "0" * 64
            self.change_rubric(mutate)
            with self.subTest(key=key), self.assertRaisesRegex(SystemExit, error):
                self.export()

    def test_full_inventory_detects_non_evidence_files_and_hash_drift(self):
        task = self.dataset["tasks"][1]
        task["source_file_count"] = 1
        self.save_dataset()
        with self.assertRaisesRegex(SystemExit, "source file count differs"):
            self.export("q002")
        task["source_file_count"] = 2
        task["source_inventory_sha256"] = "0" * 64
        self.save_dataset()
        with self.assertRaisesRegex(SystemExit, "source differs from the frozen inventory"):
            self.export("q002")

    def test_leak_scan_includes_non_evidence_files_even_when_inventory_matches(self):
        files = dict(self.second_files, **{"private.txt": b"PRIVATE_GOLD_MARKER"})
        revision = self.commit(files)
        self.dataset["tasks"] = [self.task("q003", revision, files)]
        self.save_dataset()
        with self.assertRaisesRegex(SystemExit, "Potential answer leakage in private.txt"):
            self.export("q003")
        self.assertFalse((self.root / "controller").exists())

    def test_archive_symlinks_are_rejected(self):
        (self.repo / "outside-link").symlink_to("../../private")
        self.git("add", "--all")
        self.git("commit", "-qm", "Symlink fixture")
        self.dataset["tasks"][0]["source_commit"] = self.git("rev-parse", "HEAD")
        self.save_dataset()
        with self.assertRaisesRegex(SystemExit, "(Unsafe|Unsupported) archive member: outside-link"):
            self.export()

    def test_output_and_manifest_stay_outside_benchmark_and_repository(self):
        for protected in (self.benchmark, self.repo, corpus.HERE):
            with self.subTest(protected=protected, role="source"), \
                    self.assertRaisesRegex(SystemExit, "Source directory must be separate"):
                self.export(output=protected / "must-not-be-created")
            with self.subTest(protected=protected, role="manifest"), \
                    self.assertRaisesRegex(SystemExit, "Manifest must be outside benchmark and Git"):
                self.export(manifest=protected / "must-not-be-created.json")
            self.assertFalse((protected / "must-not-be-created").exists())
            self.assertFalse((protected / "must-not-be-created.json").exists())

    def test_manifest_cannot_replace_gold_source_or_existing_controller_files(self):
        existing = self.root / "existing.json"
        existing.write_text("Retain previous evidence.")
        for path in (self.dataset_path, self.benchmark / "evaluator/q001.json",
                     self.repo / "src/example.cc", existing):
            original = path.read_bytes()
            with self.subTest(path=path), self.assertRaisesRegex(SystemExit, "Manifest must not exist"):
                self.export(manifest=path)
            self.assertEqual(path.read_bytes(), original)

    def test_manifest_cannot_be_inside_or_ancestor_of_source(self):
        output = self.root / "new-source"
        for manifest in (output, output / "manifest.json"):
            with self.subTest(manifest=manifest), self.assertRaisesRegex(SystemExit, "outside the solver source"):
                self.export(output=output, manifest=manifest)
        with self.assertRaisesRegex(SystemExit, "outside the solver source"):
            self.export(output=output / "nested", manifest=output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
