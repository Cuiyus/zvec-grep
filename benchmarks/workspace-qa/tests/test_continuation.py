from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import continuation
import native_session
import runner
from native_fixtures import dump, installation_stub, write_installation


QUESTION = "请整理项目依赖。\n"
FILENAME = "project_dependencies_unique_list.md"
OLD_COMMIT = "a" * 40
NEW_COMMIT = "b" * 40


def fixture(root):
    """Small offline artifact with the real 20-slot order and four observations."""
    prior = root / "prior"
    plan = runner.make_plan("3", 10)
    old_plan = copy.deepcopy(plan)
    manifest = {"schema_version": 2, "protocol": native_session.PROTOCOL,
        "integration_method": "zg_install", "install_command": native_session.INSTALL_COMMAND,
        "task_id": "3", "package": "@zvec/zvec-grep@0.2.2", "embedding_model": runner.EMBEDDING,
        "embedding_endpoint": runner.ZVEC_GREP_EMBEDDING_ENDPOINT, "agent": "qodercli",
        "agent_version": "1.1.45", "model": runner.MODEL, "agent_spec": runner.SPEC.to_dict(),
        "source_files": {"资料.md": hashlib.sha256(b"frozen").hexdigest()},
        "question_sha256": hashlib.sha256(QUESTION.encode()).hexdigest(), "answer_filename": FILENAME,
        "run_limits": {"model_requests": 60, "tool_calls": 120, "input_tokens": 600000, "wall_seconds": 900},
        "repetitions_per_profile": 10, "order_seed": 1729, "gold_visible_to_agent": False,
        "corpus_readonly_mount": True, "index_options": {"root": "/app", "maxFileSizeBytes": 1048576},
        "index_policy": "native writable copies", "wall_seconds_scope": "qa-session interval",
        "answer_delivery": "verbatim", "installed_versions": {"zg": "0.2.2", "qoder": "1.1.45", "node": "v24.21.0"},
        "os": "linux", "architecture": "amd64", "source_git_commit": "c" * 40,
        "image_id": "sha256:original", "image_repo_digests": [],
        "ci_identity": {"GITHUB_RUN_ID": "34919707888", "GITHUB_SHA": OLD_COMMIT}}
    selection = continuation.read_object(continuation.LOCK_PATH)
    selection["tasks"] = [t for t in selection["tasks"] if t["task_id"] == "3"]
    selection["repetitions"] = 10
    dump(prior / "selection.json", selection)
    dump(prior / "runs/manifest.json", manifest)
    rows = []
    for index, original in enumerate(plan["trials"]):
        row = {**original, **dict.fromkeys(("input_tokens", "output_tokens", "cached_input_tokens", "tool_calls",
            "zg_tool_calls", "wall_seconds", "answer", "candidate_output_path"))}
        if index < 4:
            row.update(status="completed" if index < 3 else "contract_failure", input_tokens=9104,
                       wall_seconds=84.08, tool_calls=1, zg_tool_calls=int(row["profile"] == "with-zg"),
                       provenance={"manifest_path": "manifest.json", "source_git_commit": manifest["source_git_commit"],
                         "question_sha256": manifest["question_sha256"], "image_id": manifest["image_id"]})
            trial = prior / "runs" / row["trial_id"]
            instruction = runner.instruction(QUESTION, FILENAME, zg=row["profile"] == "with-zg")
            dump(trial / "instruction.json", {"text": instruction, "sha256": hashlib.sha256(instruction.encode()).hexdigest()})
            spec = {"protocol": native_session.PROTOCOL, "profile": row["profile"], "prompt": instruction,
                "model": native_session.MODEL, "embedding_model": runner.EMBEDDING, "root": "/app", "limits": manifest["run_limits"]}
            dump(trial / "agent/native-spec.json", spec)
            dump(trial / "agent/session-spec.json", native_session.session_spec(spec, Path("/logs")))
            write_installation(trial / "agent", row["profile"])
            (trial / "agent/qodercli-stream.jsonl").write_text('{"original":"native observation"}\n')
            if index < 3:
                row["answer"] = "\n原始回答。\n"
                row["candidate_output_path"] = f"{row['trial_id']}/candidate/{FILENAME}"
                candidate = prior / "runs" / row["candidate_output_path"]
                candidate.parent.mkdir()
                candidate.write_text(row["answer"])
            dump(trial / "result.json", row)
            old_plan["trials"][index]["status"] = row["status"]
        rows.append(row)
    ledger = {"schema_version": 1, "protocol": native_session.PROTOCOL, "task_id": "3", "repetitions_per_profile": 10, "trials": rows}
    dump(prior / "runs/trial-results.json", ledger)
    dump(prior / "runs/plan.json", old_plan)
    dump(prior / "runs/judgements.json", {"schema_version": 1, "task_id": "3", "expected_trials": 20,
        "repetitions_per_profile": 10, "trial_results_sha256": continuation.digest(prior / "runs/trial-results.json"),
        "trials": [{"trial_id": row["trial_id"], "status": "judged" if i < 3 else "execution_not_completed",
                    "score": 0.5 if i < 3 else None} for i, row in enumerate(rows)]})
    current = copy.deepcopy(manifest)
    current.update(image_id="sha256:rebuilt", source_git_commit="d" * 40,
        ci_identity={"GITHUB_RUN_ID": "new-run", "GITHUB_SHA": NEW_COMMIT},
        continuation_code_review={"status": "verified", "base_commit": OLD_COMMIT, "head_commit": NEW_COMMIT,
            "changes": {"diagnostic.py": {"before_sha256": "1" * 64, "after_sha256": "2" * 64}}})
    return prior, plan, ledger, current


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.validator = patch.object(continuation, "validate_installation", side_effect=installation_stub)
        self.validator.start()
        self.addCleanup(self.validator.stop)

    def imported(self, root):
        prior, plan, ledger, current = fixture(root)
        bundle = continuation.load_prior(prior, plan)
        continuation.validate_runtime(bundle, current, QUESTION, FILENAME)
        runs = root / "new/runs"
        provenance = continuation.import_prior(bundle, runs, plan)
        current["continuation"] = provenance
        dump(runs / "manifest.json", current)
        dump(runs / "trial-results.json", ledger)
        return prior, runs, bundle, plan, current

    def test_import_preserves_all_four_observations_and_original_evidence_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            prior, runs, bundle, plan, current = self.imported(Path(tmp))
            self.assertEqual(len(bundle["preserved_trial_ids"]), 4)
            self.assertEqual(len(bundle["pending_trial_ids"]), 16)
            self.assertEqual(plan["trials"][3]["status"], "contract_failure")
            self.assertTrue(all(row["status"] == "planned" for row in plan["trials"][4:]))
            for name, source in (("ledger", "trial-results.json"), ("manifest", "manifest.json"), ("judgements", "judgements.json")):
                self.assertEqual((runs / continuation.PRIOR_PATHS[name]).read_bytes(), (prior / "runs" / source).read_bytes())
            for name in current["continuation"]["preserved_files_sha256"]:
                self.assertEqual((runs / name).read_bytes(), (prior / "runs" / name).read_bytes())
            proof = continuation.validate_continuation_evidence(runs)
            self.assertEqual(proof["preserved_trial_ids"], bundle["preserved_trial_ids"])
            self.assertNotEqual(proof["compatibility"]["source_image_id"], proof["compatibility"]["current_image_id"])
            self.assertFalse(any((runs / name).exists() for name in bundle["pending_trial_ids"]))

    def test_planned_trial_directory_or_hidden_usage_cannot_be_resampled(self):
        for evidence in ("directory", "usage", "started_at"):
            with self.subTest(evidence=evidence), tempfile.TemporaryDirectory() as tmp:
                prior, plan, ledger, _ = fixture(Path(tmp))
                row = ledger["trials"][4]
                if evidence == "directory":
                    (prior / "runs" / row["trial_id"]).mkdir()
                else:
                    row["input_tokens" if evidence == "usage" else "started_at"] = 0 if evidence == "usage" else "attempted"
                    dump(prior / "runs/trial-results.json", ledger)
                with self.assertRaisesRegex(ValueError, "evidence"):
                    continuation.load_prior(prior, plan)

    def test_running_trial_remains_attempted_even_without_terminal_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            prior, plan, ledger, _ = fixture(Path(tmp))
            row = ledger["trials"][4]
            row["status"] = "running"
            trial = prior / "runs" / row["trial_id"]
            trial.mkdir()
            (trial / "launching.txt").write_text("started")
            old_plan = continuation.read_object(prior / "runs/plan.json")
            old_plan["trials"][4]["status"] = "running"
            dump(prior / "runs/plan.json", old_plan)
            dump(prior / "runs/trial-results.json", ledger)
            judged = continuation.read_object(prior / "runs/judgements.json")
            judged["trial_results_sha256"] = continuation.digest(prior / "runs/trial-results.json")
            dump(prior / "runs/judgements.json", judged)
            bundle = continuation.load_prior(prior, plan)
            self.assertEqual(len(bundle["preserved_trial_ids"]), 5)
            self.assertNotIn(row["trial_id"], bundle["pending_trial_ids"])

    def test_bridge_or_changed_selection_or_changed_order_is_rejected(self):
        for change in ("bridge", "selection", "order"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                prior, plan, _, _ = fixture(Path(tmp))
                if change == "bridge":
                    manifest = continuation.read_object(prior / "runs/manifest.json")
                    manifest["protocol"] = "workspace-qa-qoder-v2"
                    dump(prior / "runs/manifest.json", manifest)
                elif change == "selection":
                    dump(prior / "selection.json", {})
                else:
                    plan["trials"][4:6] = list(reversed(plan["trials"][4:6]))
                with self.assertRaises(ValueError):
                    continuation.load_prior(prior, plan)

    def test_runtime_rejects_changed_source_model_node_budget_endpoint_or_index_cap(self):
        changes = {"source_files": {"资料.md": "0" * 64}, "model": "other", "installed_versions": {
            "zg": "0.2.2", "qoder": "1.1.45", "node": "v24.22.0"}, "run_limits": {"wall_seconds": 1200},
            "embedding_endpoint": "https://other.invalid/embeddings", "index_options": {"maxFileSizeBytes": 2097152}}
        for field, value in changes.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                prior, plan, _, current = fixture(Path(tmp))
                bundle = continuation.load_prior(prior, plan)
                current[field] = value
                with self.assertRaisesRegex(ValueError, field):
                    continuation.validate_runtime(bundle, current, QUESTION, FILENAME)

    def test_runtime_requires_exact_prompt_and_native_command_and_verified_code_review(self):
        for change in ("question", "instruction", "native_command", "unreviewed", "wrong_base"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                prior, plan, _, current = fixture(Path(tmp))
                bundle = continuation.load_prior(prior, plan)
                trial = prior / "runs" / plan["trials"][0]["trial_id"]
                question = QUESTION
                if change == "question": question += "changed"
                elif change == "instruction": dump(trial / "instruction.json", {"text": "changed", "sha256": "0" * 64})
                elif change == "native_command":
                    path = trial / "agent/session-spec.json"
                    spec = continuation.read_object(path)
                    spec["command"].insert(1, "--dangerously-skip-permissions")
                    dump(path, spec)
                elif change == "unreviewed": del current["continuation_code_review"]
                else: current["continuation_code_review"]["base_commit"] = NEW_COMMIT
                with self.assertRaises(ValueError):
                    continuation.validate_runtime(bundle, current, question, FILENAME)

    def test_symlink_path_escape_and_tampering_between_validation_and_import_are_rejected(self):
        for change in ("symlink", "name", "after_validation"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prior, plan, _, current = fixture(root)
                if change == "symlink":
                    (prior / "escape").symlink_to(root / "outside")
                    with self.assertRaisesRegex(ValueError, "symlinks"):
                        continuation.load_prior(prior, plan)
                elif change == "name":
                    for name in ("../secret", "/tmp/secret", "a\\b", "a//b"):
                        with self.assertRaises(ValueError): continuation.relative_path(name)
                else:
                    bundle = continuation.load_prior(prior, plan)
                    continuation.validate_runtime(bundle, current, QUESTION, FILENAME)
                    (prior / "tampered.txt").write_text("changed")
                    with self.assertRaisesRegex(ValueError, "changed after"):
                        continuation.import_prior(bundle, root / "new", plan)

    def test_transition_keeps_failed_row_exact_and_only_allows_original_pending_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, ledger, _ = fixture(Path(tmp))
            new = copy.deepcopy(ledger)
            new["trials"][4].update(status="failed", input_tokens=10)
            classification = continuation.validate_transition(ledger, new)
            self.assertEqual(len(classification["pending_trial_ids"]), 16)
            for change in ("retry_failure", "edit_scoreless_answer", "reorder", "remove"):
                altered = copy.deepcopy(new)
                if change == "retry_failure": altered["trials"][3]["status"] = "completed"
                elif change == "edit_scoreless_answer": altered["trials"][0]["input_tokens"] += 1
                elif change == "reorder": altered["trials"][4:6] = list(reversed(altered["trials"][4:6]))
                else: altered["trials"].pop()
                with self.subTest(change=change), self.assertRaises(ValueError):
                    continuation.validate_transition(ledger, altered)

    def test_final_gate_rejects_old_file_extra_file_ledger_or_runtime_changes(self):
        for change in ("old_file", "extra_file", "old_ledger", "runtime", "review"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                _, runs, bundle, _, current = self.imported(Path(tmp))
                trial = runs / bundle["preserved_trial_ids"][0]
                if change == "old_file": (trial / "agent/qodercli-stream.jsonl").write_text("changed")
                elif change == "extra_file": (trial / "added.txt").write_text("extra")
                elif change == "old_ledger": (runs / continuation.PRIOR_PATHS["ledger"]).write_text("{}")
                else:
                    if change == "runtime": current["model"] = "different"
                    else: current["continuation_code_review"]["status"] = "unverified"
                    dump(runs / "manifest.json", current)
                with self.assertRaises(ValueError):
                    continuation.validate_continuation_evidence(runs)

    def test_final_gate_does_not_read_new_trials_or_preparation_vectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, runs, _, _, _ = self.imported(Path(tmp))
            vector = runs / "preparation/index/vectors.bin"
            vector.parent.mkdir(parents=True)
            vector.write_bytes(b"large unneeded seed")
            real_digest = continuation.digest

            def guarded(path):
                self.assertFalse(path.is_relative_to(runs / "preparation"))
                return real_digest(path)

            with patch.object(continuation, "digest", side_effect=guarded):
                continuation.validate_continuation_evidence(runs)

    def test_import_requires_prior_runtime_validation_and_never_overwrites_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prior, plan, _, current = fixture(root)
            bundle = continuation.load_prior(prior, plan)
            with self.assertRaisesRegex(ValueError, "Runtime compatibility"):
                continuation.import_prior(bundle, root / "new", plan)
            continuation.validate_runtime(bundle, current, QUESTION, FILENAME)
            target = root / "new" / bundle["preserved_trial_ids"][0]
            target.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "overwrite"):
                continuation.import_prior(bundle, root / "new", plan)


if __name__ == "__main__":
    unittest.main()
