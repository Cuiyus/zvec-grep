from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import continuation
import continue_ci
import native_session
import runner
from native_fixtures import dump, installation_stub, write_installation
from test_continuation import fixture as single_fixture, OLD_COMMIT, NEW_COMMIT, QUESTION


SOURCE_RUN = "34919707888"
NEW_RUN = "900001"


def collection_fixture(root, *, pending=("3",)):
    template_root = root / "template"
    prior, _, _, _ = single_fixture(template_root)
    template = continuation.read_object(prior / "runs/manifest.json")
    shutil.rmtree(template_root)
    lock = continuation.read_object(continuation.LOCK_PATH)
    task_ids = [t["task_id"] for t in lock["tasks"]]
    config = {"protocol": continuation.PROTOCOL, "source_run_id": SOURCE_RUN, "source_run_attempt": 1,
        "source_commit": OLD_COMMIT, "branch": "dev/benchmark-qoder-qwen38-qa", "task_ids": task_ids,
        "workflow_path": ".github/workflows/workspace-qa-qoder.yml"}
    source_api = {"id": int(SOURCE_RUN), "run_attempt": 1, "status": "completed", "head_sha": OLD_COMMIT,
        "head_branch": config["branch"], "path": config["workflow_path"], "conclusion": "failure"}
    dump(root / "config.json", config)
    dump(root / "source-run.json", source_api)
    artifacts = root / "original"
    for task in lock["tasks"]:
        task_id, filename = task["task_id"], task["answer_filename"]
        artifact = artifacts / f"workspace-qa-batch-{task_id}-{SOURCE_RUN}-1"
        selection = {**lock, "tasks": [task], "repetitions": 10}
        dump(artifact / "selection.json", selection)
        manifest = copy.deepcopy(template)
        manifest.update(task_id=task_id, answer_filename=filename,
                        ci_identity={"GITHUB_RUN_ID": SOURCE_RUN, "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": OLD_COMMIT})
        dump(artifact / "runs/manifest.json", manifest)
        plan = runner.make_plan(task_id, 10)
        ledger = {"schema_version": 1, "protocol": continuation.PROTOCOL, "task_id": task_id,
                  "repetitions_per_profile": 10, "trials": []}
        for index, planned in enumerate(plan["trials"]):
            row = {**planned, **dict.fromkeys(("input_tokens", "output_tokens", "cached_input_tokens", "tool_calls",
                "zg_tool_calls", "wall_seconds", "answer", "candidate_output_path"))}
            if index < 4 or task_id not in pending:
                row.update(status="completed" if index < 3 else "failed", input_tokens=15, tool_calls=1, wall_seconds=2.0,
                    provenance={"manifest_path": "manifest.json", "source_git_commit": manifest["source_git_commit"],
                        "question_sha256": manifest["question_sha256"], "image_id": manifest["image_id"]})
                trial = artifact / "runs" / row["trial_id"]
                prompt = runner.instruction(QUESTION, filename, zg=row["profile"] == "with-zg")
                dump(trial / "instruction.json", {"text": prompt, "sha256": hashlib.sha256(prompt.encode()).hexdigest()})
                spec = {"protocol": continuation.PROTOCOL, "profile": row["profile"], "prompt": prompt,
                    "model": native_session.MODEL, "embedding_model": runner.EMBEDDING, "root": "/app", "limits": manifest["run_limits"]}
                dump(trial / "agent/native-spec.json", spec)
                dump(trial / "agent/session-spec.json", native_session.session_spec(spec, Path("/logs")))
                write_installation(trial / "agent", row["profile"])
                if index < 3:
                    row["answer"] = "Original answer\n"
                    row["candidate_output_path"] = f"{row['trial_id']}/candidate/{filename}"
                    candidate = artifact / "runs" / row["candidate_output_path"]
                    candidate.parent.mkdir(parents=True)
                    candidate.write_text(row["answer"])
                dump(trial / "result.json", row)
                planned["status"] = row["status"]
            ledger["trials"].append(row)
        dump(artifact / "runs/plan.json", plan)
        dump(artifact / "runs/trial-results.json", ledger)
        dump(artifact / "runs/judgements.json", {"schema_version": 1, "task_id": task_id, "expected_trials": 20,
            "repetitions_per_profile": 10, "trial_results_sha256": continuation.digest(artifact / "runs/trial-results.json"),
            "trials": [{"trial_id": row["trial_id"], "status": "judged" if i < 3 else "execution_not_completed",
                        "score": 0.5 if i < 3 else None} for i, row in enumerate(ledger["trials"])]})
    return artifacts, root / "config.json", root / "source-run.json"


def merged_fixture(root, originals, task_id="3"):
    original = originals / f"workspace-qa-batch-{task_id}-{SOURCE_RUN}-1"
    plan = runner.make_plan(task_id, 10)
    bundle = continuation.load_prior(original, plan)
    manifest = copy.deepcopy(bundle["manifest"])
    manifest.update(image_id="sha256:rebuilt", source_git_commit="d" * 40,
        ci_identity={"GITHUB_SHA": NEW_COMMIT, "GITHUB_RUN_ID": NEW_RUN, "GITHUB_RUN_ATTEMPT": "1"},
        continuation_code_review={"status": "verified", "base_commit": OLD_COMMIT, "head_commit": NEW_COMMIT, "changes": {}})
    continuation.validate_runtime(bundle, manifest, QUESTION, manifest["answer_filename"])
    artifact = root / "continued" / f"workspace-qa-continuation-batch-{task_id}-{NEW_RUN}-1"
    runs = artifact / "runs"
    proof = continuation.import_prior(bundle, runs, plan)
    manifest["continuation"] = proof
    dump(runs / "manifest.json", manifest)
    ledger = copy.deepcopy(bundle["ledger"])
    for row, planned in zip(ledger["trials"], plan["trials"], strict=True):
        if row["trial_id"] in bundle["pending_trial_ids"]:
            row.update(status="failed", input_tokens=1, wall_seconds=1.0, tool_calls=0,
                provenance={"manifest_path": "manifest.json", "source_git_commit": manifest["source_git_commit"],
                    "question_sha256": manifest["question_sha256"], "image_id": manifest["image_id"]})
            dump(runs / row["trial_id"] / "result.json", row)
            planned["status"] = row["status"]
    dump(runs / "trial-results.json", ledger)
    dump(runs / "plan.json", plan)
    dump(artifact / "selection.json", bundle["selection"])
    judged = copy.deepcopy(bundle["judgements"])
    judged["trial_results_sha256"] = continuation.digest(runs / "trial-results.json")
    judged["judgement_continuation"] = {"schema_version": 1, **{key: proof[key] for key in (
        "prior_ledger_path", "prior_ledger_sha256", "prior_manifest_path", "prior_manifest_sha256",
        "prior_judgements_path", "prior_judgements_sha256", "preserved_trial_ids")},
        "imported_judged_trial_ids": sorted(row["trial_id"] for row in judged["trials"]
            if row["trial_id"] in proof["preserved_trial_ids"] and row["status"] == "judged")}
    dump(runs / "judgements.json", judged)
    return artifact


class ContinueCiTests(unittest.TestCase):
    def setUp(self):
        validator = patch.object(continuation, "validate_installation", side_effect=installation_stub)
        validator.start()
        self.addCleanup(validator.stop)

    def test_plan_uses_all_ten_artifacts_and_only_original_unstarted_trials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts, config, source = collection_fixture(root)
            with redirect_stdout(io.StringIO()):
                status = continue_ci.main(["plan", "--artifacts", str(artifacts), "--source-run-json", str(source),
                    "--source-config", str(config), "--output", str(root / "plan.json")])
            result = continuation.read_object(root / "plan.json")
            self.assertEqual(status, 0)
            self.assertEqual(result["matrix"], {"task": ["3"]})
            self.assertTrue(result["has_pending"])
            self.assertEqual(result["counts"], {"tasks": 10, "planned_trials": 200, "attempted_trials": 184, "pending_trials": 16, "pending_tasks": 1})
            record = next(task for task in result["tasks"] if task["task_id"] == "3")
            self.assertEqual(record["pending_trial_ids"], [row["trial_id"] for row in runner.make_plan("3", 10)["trials"]][4:])

    def test_api_must_be_completed_with_exact_run_sha_branch_workflow_and_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, config, source = collection_fixture(Path(tmp))
            original = continuation.read_object(source)
            changes = {"status": "in_progress", "head_sha": NEW_COMMIT, "head_branch": "other", "id": 123,
                       "run_attempt": 2, "path": ".github/workflows/other.yml"}
            for field, value in changes.items():
                dump(source, {**original, field: value})
                with self.subTest(field=field), patch.object(continue_ci, "load_originals") as load:
                    with self.assertRaisesRegex(ValueError, field): continue_ci.plan(artifacts, source, config)
                    load.assert_not_called()

    def test_missing_setup_artifact_and_another_run_attempt_are_not_inferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts, config, source = collection_fixture(root)
            task = artifacts / f"workspace-qa-batch-373-{SOURCE_RUN}-1"
            task.rename(root / "missing")
            with self.assertRaisesRegex(ValueError, "Missing.*373"):
                continue_ci.plan(artifacts, source, config)
            (root / "missing").rename(artifacts / f"workspace-qa-batch-373-{SOURCE_RUN}-2")
            with self.assertRaisesRegex(ValueError, "run attempt"):
                continue_ci.plan(artifacts, source, config)

    def test_partial_plan_and_running_or_unknown_rows_fail_closed(self):
        for status in ("running", "unknown", "partial_plan"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                artifacts, config, source = collection_fixture(Path(tmp))
                runs = artifacts / f"workspace-qa-batch-3-{SOURCE_RUN}-1/runs"
                ledger, plan = continuation.read_object(runs / "trial-results.json"), continuation.read_object(runs / "plan.json")
                if status == "partial_plan": plan["trials"].pop()
                else:
                    ledger["trials"][3]["status"] = status
                    plan["trials"][3]["status"] = status
                    dump(runs / ledger["trials"][3]["trial_id"] / "result.json", ledger["trials"][3])
                dump(runs / "plan.json", plan)
                dump(runs / "trial-results.json", ledger)
                judged = continuation.read_object(runs / "judgements.json")
                judged["trial_results_sha256"] = continuation.digest(runs / "trial-results.json")
                dump(runs / "judgements.json", judged)
                with self.assertRaises(ValueError): continue_ci.plan(artifacts, source, config)

    def test_artifact_ci_identity_and_symlink_cannot_substitute_another_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, config, source = collection_fixture(Path(tmp))
            path = artifacts / f"workspace-qa-batch-3-{SOURCE_RUN}-1/runs/manifest.json"
            manifest = continuation.read_object(path)
            manifest["ci_identity"]["GITHUB_SHA"] = NEW_COMMIT
            dump(path, manifest)
            with self.assertRaisesRegex(ValueError, "pinned source"):
                continue_ci.plan(artifacts, source, config)
            manifest["ci_identity"]["GITHUB_SHA"] = OLD_COMMIT
            dump(path, manifest)
            (artifacts / f"workspace-qa-batch-3-{SOURCE_RUN}-1/escape").symlink_to(Path(tmp) / "outside")
            with self.assertRaisesRegex(ValueError, "symlinks"):
                continue_ci.plan(artifacts, source, config)

    def test_assemble_selects_one_canonical_artifact_per_task_preserving_original_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            originals, config, _ = collection_fixture(root)
            merged = merged_fixture(root, originals)
            with redirect_stdout(io.StringIO()):
                status = continue_ci.main(["assemble", "--original", str(originals), "--continued", str(root / "continued"),
                    "--source-config", str(config), "--output", str(root / "assembled")])
            result = continuation.read_object(root / "assembled/assembly.json")
            self.assertEqual(status, 0)
            self.assertEqual(len(list((root / "assembled").rglob("trial-results.json"))), 10)
            self.assertEqual(result["counts"]["attempted_trials"], 200)
            self.assertEqual(result["counts"]["pending_trials"], 0)
            self.assertEqual(continuation.file_hashes(root / "assembled/task-3"), continuation.file_hashes(merged))
            original = originals / f"workspace-qa-batch-127-{SOURCE_RUN}-1"
            self.assertEqual(continuation.file_hashes(root / "assembled/task-127"), continuation.file_hashes(original))

    def test_no_pending_uses_only_originals_without_a_continued_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts, config, source = collection_fixture(root, pending=())
            result = continue_ci.plan(artifacts, source, config)
            self.assertEqual(result["matrix"], {"task": []})
            self.assertFalse(result["has_pending"])
            assembled = continue_ci.assemble(artifacts, root / "not-created", root / "out", config)
            self.assertTrue(all(task["kind"] == "original" for task in assembled["tasks"]))

    def test_missing_or_duplicate_continuation_and_extra_completed_task_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            originals, config, _ = collection_fixture(root)
            with self.assertRaisesRegex(ValueError, "missing=3"):
                continue_ci.assemble(originals, root / "continued", root / "out", config)
            merged = merged_fixture(root, originals)
            duplicate = merged.parent / f"workspace-qa-continuation-batch-3-{int(NEW_RUN)+1}-1"
            shutil.copytree(merged, duplicate)
            with self.assertRaisesRegex(ValueError, "Multiple artifacts"):
                continue_ci.assemble(originals, root / "continued", root / "out", config)
            shutil.rmtree(duplicate)
            (merged.parent / f"workspace-qa-continuation-batch-127-{NEW_RUN}-1").mkdir()
            with self.assertRaisesRegex(ValueError, "unexpected=127"):
                continue_ci.assemble(originals, root / "continued", root / "out", config)

    def test_assembly_checks_original_hash_binding_and_old_judgement_rows(self):
        for change in ("source_manifest", "old_score"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                originals, config, _ = collection_fixture(root)
                merged = merged_fixture(root, originals)
                if change == "source_manifest":
                    path = originals / f"workspace-qa-batch-3-{SOURCE_RUN}-1/runs/manifest.json"
                    value = continuation.read_object(path)
                    value["created_at"] = "modified original"
                    dump(path, value)
                else:
                    path = merged / "runs/judgements.json"
                    value = continuation.read_object(path)
                    value["trials"][0]["score"] = 0.9
                    dump(path, value)
                with self.assertRaises(ValueError):
                    continue_ci.assemble(originals, root / "continued", root / "out", config)
                self.assertFalse((root / "out").exists())

    def test_cli_failure_does_not_emit_an_eligible_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts, config, source = collection_fixture(root)
            source.write_text('{"status":"in_progress"}')
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                continue_ci.main(["plan", "--artifacts", str(artifacts), "--source-run-json", str(source),
                    "--source-config", str(config), "--output", str(root / "plan.json")])
            self.assertEqual(error.exception.code, 2)
            self.assertFalse((root / "plan.json").exists())


if __name__ == "__main__":
    unittest.main()
