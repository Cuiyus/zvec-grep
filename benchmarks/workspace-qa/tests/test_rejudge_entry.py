import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run_rejudge as entry


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.source, self.output = self.base / "original", self.base / "recovered"
        body = "原始依赖列表\n".encode()
        source_item = {"stored_relpath": "data/source.md", "filename": "source.md",
                       "sha256": digest(body), "size_bytes": len(body)}
        metadata = {"task": "汇总依赖", "output_files": ["answer.md"],
                    "rubrics": ["完整"], "rubric_types": ["content"], "data_manifest": [source_item]}
        self.metadata = json.dumps(metadata, ensure_ascii=False).encode()
        self.task = {"task_id": "3", "slice": "code_qa", "answer_filename": "answer.md",
                     "metadata_sha256": digest(self.metadata), "inputs": [source_item]}
        self.lock = {"dataset": {"repo": "fixture/repo", "revision": "immutable"}, "tasks": [self.task]}
        self.lock_path = self.base / "lock.json"
        write_json(self.lock_path, self.lock)
        write_json(self.source / "selection.json", {**self.lock, "repetitions": 1})
        write_json(self.source / "runs/trial-results.json", {"task_id": "3", "repetitions_per_profile": 1,
                   "trials": [{"trial_id": "3-r01-" + profile, "task_id": "3", "profile": profile,
                               "repetition": 1, "status": "completed", "answer": "固定回答"}
                              for profile in ("baseline", "with-zg")]})
        write_json(self.source / "runs/judgements.json", {"original_attempts": ["invalid JSON"]})
        write_json(self.source / "runs/3-r01-with-zg/agent/session.json", {"status": "completed"})
        write_json(self.source / "report/summary.json", {"old": True})
        write_json(self.source / "smoke_validation.json", {"status": "invalid", "reason": "judge incomplete"})
        self.recovery = {"source_run_id": "123", "source_commit": "abc", "artifact_id": "456",
                         "artifact_name": "workspace-qa-smoke-3", "artifact_sha256": "a" * 64,
                         "task_id": "3", "phase": "smoke", "files": self.inventory(self.source)}
        self.recovery.update(source_trial_results_sha256=self.recovery["files"]["runs/trial-results.json"],
                             source_judgements_sha256=self.recovery["files"]["runs/judgements.json"])
        self.recovery_path = self.base / "recovery.json"
        write_json(self.recovery_path, self.recovery)
        self.downloads = []
        self.bodies = {"metadata.json": self.metadata, "data/source.md": body}

    def inventory(self, root):
        return {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in root.rglob("*") if p.is_file()}

    def download(self, url, target, expected):
        self.downloads.append(url)
        relative = url.split("/task_lite_clean_cn/3/", 1)[1]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.bodies[relative])

    def rebuild_report(self, *, runs_dir, output, manifest_path):
        self.assertEqual(runs_dir, self.output / "runs")
        self.assertEqual(manifest_path, self.output / "selection.json")
        result = {"summary": {"complete": True}, "efficacy_claim_ready": True}
        write_json(output / "summary.json", result)
        (output / "summary.md").write_text("Coverage: complete\n")
        return result

    def invoke(self, *, judge=None, validation="valid"):
        def default_judge(command, **kwargs):
            self.assertEqual(Path(command[1]), ROOT / "judge.py")
            self.assertEqual(kwargs["cwd"], ROOT)
            self.assertIn("--resume", command)
            self.assertNotIn(str(self.source), " ".join(command))
            write_json(self.output / "runs/judgements.json", {"original_attempts": ["invalid JSON"], "new": True})
            return subprocess.CompletedProcess(command, 0)
        gate = {"phase": "smoke", "status": validation, "verified_successful_searches": 1}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(entry, "RECOVERY_PATH", self.recovery_path))
            stack.enter_context(patch.object(entry, "LOCK_PATH", self.lock_path))
            stack.enter_context(patch.dict("os.environ", {"GLM_API_KEY": "fixture-secret"}, clear=True))
            stack.enter_context(patch.object(entry.dataset, "download", side_effect=self.download))
            stack.enter_context(patch.object(entry.dataset, "prepare", side_effect=AssertionError("No full corpus")))
            stack.enter_context(patch.object(entry.dataset, "RangeArchive", side_effect=AssertionError("No ZIP")))
            stack.enter_context(patch.object(entry.run_task, "sdk_preflight", side_effect=AssertionError("No SDK")))
            stack.enter_context(patch.object(entry.run_task, "embedding_preflight", side_effect=AssertionError("No embedding")))
            stack.enter_context(patch.object(entry.runner, "make_plan", side_effect=AssertionError("No QA plan")))
            command = stack.enter_context(patch.object(entry.subprocess, "run", side_effect=judge or default_judge))
            stack.enter_context(patch.object(entry.run_task, "smoke_validation", return_value=gate))
            stack.enter_context(patch.object(entry.report, "write_report", side_effect=self.rebuild_report))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            result = entry.main(["--artifact-root", str(self.source), "--output", str(self.output)])
        return result, json.loads((self.output / "rejudge-provenance.json").read_text()), command

    def test_recovery_uses_current_judge_only_and_preserves_original_qa_and_attempts(self):
        old = self.inventory(self.source)
        status, provenance, command = self.invoke()
        self.assertEqual(status, 0)
        command.assert_called_once()
        self.assertEqual(self.inventory(self.source), old)
        self.assertEqual(digest((self.output / "runs/trial-results.json").read_bytes()), old["runs/trial-results.json"])
        self.assertEqual(digest((self.output / "rejudge-original/judgements.json").read_bytes()), old["runs/judgements.json"])
        self.assertEqual(digest((self.output / "rejudge-original/smoke_validation.json").read_bytes()), old["smoke_validation.json"])
        self.assertEqual(digest((self.output / "rejudge-original/report/summary.json").read_bytes()), old["report/summary.json"])
        self.assertTrue(provenance["no_new_qa_trials"])
        self.assertTrue(provenance["original_qa_evidence_unchanged"])
        self.assertEqual(provenance["source_run_id"], "123")
        self.assertEqual(len(self.downloads), 2)
        self.assertTrue(all("/task_lite_clean_cn/3/" in url for url in self.downloads))
        self.assertFalse((self.output / "dataset/source").exists())
        self.assertEqual(json.loads((self.output / "report/summary.json").read_text())["smoke_validation"]["status"], "valid")

    def test_missing_extra_corrupt_and_symlink_artifacts_stop_before_any_download(self):
        target = self.source / "runs/3-r01-with-zg/agent/session.json"
        original = target.read_bytes()
        for mode in ("missing", "extra", "corrupt", "symlink", "extra-directory"):
            with self.subTest(mode=mode):
                if mode == "missing": target.unlink()
                elif mode == "extra": (self.source / "unexpected.py").write_text("raise RuntimeError('never execute')")
                elif mode == "corrupt": target.write_text("corrupt")
                elif mode == "symlink":
                    target.unlink()
                    target.symlink_to(self.source / "selection.json")
                else: (self.source / "extra-directory").mkdir()
                status, _, command = self.invoke()
                self.assertEqual(status, 1)
                command.assert_not_called()
                self.assertEqual(self.downloads, [])
                import shutil
                shutil.rmtree(self.output)
                if target.is_symlink() or target.exists(): target.unlink()
                target.write_bytes(original)
                (self.source / "unexpected.py").unlink(missing_ok=True)
                if (self.source / "extra-directory").exists(): (self.source / "extra-directory").rmdir()

    def test_manifest_escape_and_noncanonical_paths_are_rejected(self):
        for name in ("../escape", "/absolute", "runs//alias", "runs/./alias", "runs\\alias", "."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                entry.verify_artifact(self.source, {**self.recovery, "files": {name: "a" * 64}})

    def test_judge_failure_keeps_diagnostics_and_rebuilds_report(self):
        def failed(command, **kwargs):
            write_json(self.output / "runs/judgements.json", {"new_failed_attempt": True})
            return subprocess.CompletedProcess(command, 1)
        status, provenance, _ = self.invoke(judge=failed)
        self.assertEqual(status, 1)
        self.assertEqual(provenance["judge_exit_code"], 1)
        self.assertTrue((self.output / "report/summary.json").is_file())
        self.assertTrue(json.loads((self.output / "runs/judgements.json").read_text())["new_failed_attempt"])
        self.assertTrue((self.output / "rejudge-original/judgements.json").is_file())

    def test_invalid_smoke_cannot_be_marked_complete_by_successful_judge(self):
        status, provenance, _ = self.invoke(validation="invalid")
        self.assertEqual(status, 1)
        self.assertEqual(provenance["smoke_validation"], "invalid")

    def test_changed_ledger_is_detected_after_judge(self):
        def corrupt(command, **kwargs):
            (self.output / "runs/trial-results.json").write_text("{}")
            return subprocess.CompletedProcess(command, 0)
        status, provenance, _ = self.invoke(judge=corrupt)
        self.assertEqual(status, 1)
        self.assertIn("modified original QA evidence", provenance["finalization_error"])

    def test_source_hash_failure_stops_before_judge(self):
        self.bodies["data/source.md"] = b"changed"
        status, provenance, command = self.invoke()
        self.assertEqual(status, 1)
        command.assert_not_called()
        self.assertEqual(provenance["status"], "failed")

    def test_output_cannot_overlap_or_overwrite_artifact(self):
        for output in (self.source, self.source / "nested", self.base):
            with self.subTest(output=str(output)), self.assertRaises(ValueError):
                entry.main(["--artifact-root", str(self.source), "--output", str(output)])
        self.assertEqual(self.inventory(self.source), self.recovery["files"])


if __name__ == "__main__":
    unittest.main()
