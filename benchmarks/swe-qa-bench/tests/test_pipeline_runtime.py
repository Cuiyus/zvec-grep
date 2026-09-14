"""Same-run provenance, portable seed identity, and contextual replay safeguards."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout

from zg_bench.swe_qa import pipeline_runtime as runtime
from zg_bench.swe_qa import readonly_run as runner
from zg_bench.swe_qa.retrieval_replay import evaluate_replays


def prepared_fixture(root: Path):
    case = root / "case.json"
    runner.write_json(case, {"case_id": "reflex-6", "question": "Question",
        "repo": {"url": "https://example.invalid/reflex", "commit": "frozen"}})
    prepared = root / "prepared"
    for name in ("source", "index", "model-cache", "runtime"):
        (prepared / name).mkdir(parents=True)
    (prepared / "source" / "core.py").write_text("def target(): pass\n")
    (prepared / "source" / ".git").mkdir()
    (prepared / "source" / ".git" / "HEAD").write_text("frozen")
    (prepared / "index" / "vectors").write_bytes(b"all frozen vectors")
    (prepared / "model-cache" / "weights.onnx").write_bytes(b"frozen model")
    snapshot = {"source": {"git_commit": "frozen", "sha256": "source-semantic"},
                "package": {"name": "@zvec/zvec-grep", "version": "0.2.2"},
                "index": {"documents": {"sha256": "docs-semantic"}}}
    runner.write_json(prepared / "runtime" / "snapshot.json", snapshot)
    manifest = {"schema_version": 1, "protocol": runtime.PROTOCOL, "case_id": "reflex-6",
        "case_sha256": runner.sha256(case), "repo": json.loads(case.read_text())["repo"],
        "ci_identity": {"GITHUB_RUN_ID": "run-new", "GITHUB_SHA": "commit-new"},
        "package": runner.PACKAGE, "embedding_model": runner.EMBEDDING,
        "image_id": "pinned-image",
        "snapshot_sha256": runner.sha256(prepared / "runtime" / "snapshot.json"),
        "file_identities": {name: runner.directory_identity(prepared / name, skip_git=name == "source")
                            for name in ("source", "index", "model-cache")}}
    runner.write_json(prepared / runtime.PREPARED_MANIFEST, manifest)
    return case, prepared, manifest, snapshot


def analysis_fixture():
    request = {"root": "/app", "queries": ["getter", "dependencies"], "limit": 20,
               "autoUpdate": False, "trace": True}
    original = {"root": "/app", "query": "Question", "limit": 10, "autoUpdate": False, "trace": True}
    occurrences = [{"group": "g", "trial_id": "t", "call_id": "c1", "context_id": "context-first"},
                   {"group": "g", "trial_id": "t", "call_id": "c2", "context_id": "context-later"}]
    labels = [{"kind": "faithful", "request_id": "r", "request": request, "annotation_id": "a" + str(i),
               "context_id": occurrence["context_id"], "occurrences": [occurrence],
               "prior_turn_feedback": [] if i == 1 else [{"visible_text": "Earlier search was empty"}]}
              for i, occurrence in enumerate(occurrences, 1)]
    labels.append({"kind": "original", "request_id": "original", "request": original,
                   "annotation_id": "ao", "context_id": "co", "occurrences": []})
    return {"request_catalog": [{"request_id": "r", "request": request, "occurrences": occurrences}],
            "annotation_catalog": labels, "unreplayable_occurrences": [{"call_id": "invalid", "reason": "unmatched"}]}


class PreparedRuntimeTests(unittest.TestCase):
    def test_validate_cli_is_read_only_and_requires_no_image_or_output(self):
        output = io.StringIO()
        with patch.object(runtime, "validate_group_manifests", return_value={"validated": True}) as validate, \
                patch.object(runtime, "prepare") as prepare, patch.object(runtime, "execute_from_prepared") as replay, \
                redirect_stdout(output):
            code = runtime.main(["validate", "--recorded-runs", "groups", "--prepared-dir", "prepared",
                                 "--case", "case.json", "--run-id", "run-new", "--commit", "commit-new"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), {"validated": True})
        validate.assert_called_once_with(Path("groups"), Path("case.json"), Path("prepared"),
                                         run_id="run-new", commit="commit-new")
        prepare.assert_not_called()
        replay.assert_not_called()

    def test_e2e_consumer_uses_prepared_seed_and_snapshot_without_rebuilding_or_probing(self):
        import test_readonly_run as runner_tests
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            helper = runner_tests.ControlledRunTest()
            args = helper.args(root)
            case, prepared, shared, _ = prepared_fixture(root)
            shared["image_id"] = "sha256:fixed"
            runner.write_json(prepared / runtime.PREPARED_MANIFEST, shared)
            args.seed_dir = None; args.prepared_dir = prepared; args.e2e_only = True
            consumed = []
            def verify(bundle, **kwargs):
                consumed.append(runner.sha256(bundle / "runtime" / "snapshot.json"))
                self.assertEqual(runner.directory_identity(bundle / "index"), shared["file_identities"]["index"])
            with patch.object(runtime, "verify_prepared_semantics", side_effect=verify):
                code, checked, commands = helper.run_mocked(args,
                    on_launch=lambda command, index: helper.native_artifacts(args, command, index),
                    environment={"GLM_API_KEY": "mock", "GITHUB_RUN_ID": "run-new", "GITHUB_SHA": "commit-new"})
            self.assertEqual(code, 0)
            self.assertFalse(any("retrieve" in command or runner.PREPARE_INDEX in command for command in commands + checked))
            self.assertFalse(any(command[:2] == ["git", "init"] for command in checked))
            self.assertEqual(consumed, [shared["snapshot_sha256"]])
            manifest = json.loads((args.output / "manifest.json").read_text())
            self.assertEqual(manifest["prepared_manifest_sha256"], runner.sha256(prepared / runtime.PREPARED_MANIFEST))
            self.assertEqual(manifest["source_files"], shared["file_identities"]["source"])
            self.assertEqual(len(json.loads((args.output / "plan.json").read_text())["trials"]), 10)

    def test_portable_copy_preserves_identity_without_host_paths(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder); case, prepared, manifest, _ = prepared_fixture(root)
            copied = root / "another" / "job" / "runtime"
            result = runtime.copy_prepared(prepared, copied, case, run_id="run-new", commit="commit-new")
            self.assertEqual(result, manifest)
            self.assertEqual(runtime.validate_prepared(copied, case, run_id="run-new", commit="commit-new"), manifest)
            self.assertEqual(runner.sha256(prepared / runtime.PREPARED_MANIFEST), runner.sha256(copied / runtime.PREPARED_MANIFEST))
            self.assertNotIn(str(root), json.dumps(manifest))

    def test_wrong_run_commit_case_and_mutated_source_index_weights_snapshot_are_rejected(self):
        for changed in ("run", "commit", "case", "source", "index", "model-cache", "snapshot"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
                root = Path(folder); case, prepared, _, _ = prepared_fixture(root)
                run_id = "old-run" if changed == "run" else "run-new"
                commit = "old-commit" if changed == "commit" else "commit-new"
                if changed == "case":
                    case.write_text(case.read_text() + " ")
                elif changed in ("source", "index", "model-cache"):
                    (prepared / changed / "unexpected-file").write_text("mutation")
                elif changed == "snapshot":
                    (prepared / "runtime" / "snapshot.json").write_text("{}")
                with self.assertRaises(ValueError):
                    runtime.validate_prepared(prepared, case, run_id=run_id, commit=commit)

    def test_explicit_ci_identity_cannot_override_actual_current_job(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "actual-run", "GITHUB_SHA": "actual-commit"}, clear=True):
            with self.assertRaisesRegex(ValueError, "current GITHUB_RUN_ID"):
                runtime._identity("forged-run", "actual-commit")
            with self.assertRaisesRegex(ValueError, "nonempty"):
                runtime._identity(None, "actual-commit")

    def test_same_semantic_package_is_insufficient_when_consumer_image_differs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); _, prepared, _, _ = prepared_fixture(root)
            with patch.object(runtime, "run_checked", return_value='[{"Id":"different-image"}]') as execute:
                with self.assertRaisesRegex(ValueError, "Consumer image differs"):
                    runtime.verify_prepared_semantics(prepared, image="same-tag", logs=root / "logs", working=root / "working")
            self.assertEqual(execute.call_count, 1)
            self.assertFalse((root / "working").exists())

    def test_all_three_groups_must_share_current_preparation_and_integrity(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder); case, prepared, shared, _ = prepared_fixture(root)
            runs = root / "groups"
            for i, (agent, model) in enumerate(sorted(runtime.COMBINATIONS)):
                manifest = {"protocol": runtime.PROTOCOL, "e2e_only": True, "agent": agent, "model": model,
                    "prepared_manifest_sha256": runner.sha256(prepared / runtime.PREPARED_MANIFEST),
                    **{k: shared[k] for k in ("case_sha256", "case_id", "repo", "package", "embedding_model", "ci_identity")},
                    "source_files": shared["file_identities"]["source"], "index_files": shared["file_identities"]["index"],
                    "embedding_weight_files_before_e2e": shared["file_identities"]["model-cache"],
                    "embedding_weights_unchanged_during_e2e": True, "repetitions_per_profile": 5}
                manifest["image_id"] = shared["image_id"]
                runner.write_json(runs / str(i) / "manifest.json", manifest)
                runner.write_json(runs / str(i) / "plan.json", runner.make_plan("reflex-6"))
            self.assertTrue(runtime.validate_group_manifests(runs, case, prepared, run_id="run-new", commit="commit-new")["validated"])
            path = runs / "1" / "manifest.json"
            manifest = json.loads(path.read_text()); manifest["prepared_manifest_sha256"] = "separately-rebuilt-index"
            runner.write_json(path, manifest)
            with self.assertRaisesRegex(ValueError, "frozen runtime"):
                runtime.validate_group_manifests(runs, case, prepared, run_id="run-new", commit="commit-new")

    def test_preparation_builds_once_and_has_no_retrieval_probes_or_model_credentials(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder); case = root / "case.json"
            runner.write_json(case, {"case_id": "reflex-6", "question": "Question",
                "repo": {"url": "https://example.invalid/reflex", "commit": "frozen"}})
            output = root / "prepared"; calls = []
            def checked(command, **kwargs):
                calls.append(command)
                if command[:2] == ["git", "init"]:
                    (output / "source").mkdir(); (output / "source" / "core.py").write_text("source")
                if "rev-parse" in command:
                    return "frozen"
                if command[:3] == ["docker", "image", "inspect"]:
                    return '[{"Id":"image-id"}]'
                if runner.PREPARE_INDEX in command:
                    (output / "index" / "vectors").write_bytes(b"vectors")
                    (output / "model-cache" / "weights").write_bytes(b"model")
                if "preflight" in command:
                    runner.write_json(output / "runtime" / "snapshot.json", {
                        "source": {"sha256": "s", "git_commit": "frozen"},
                        "package": {"name": "@zvec/zvec-grep", "version": "0.2.2"},
                        "index": {"documents": {"sha256": "d"}}})
                return "{}"
            with patch.object(runtime, "run_checked", side_effect=checked), patch.object(runtime.subprocess, "run"):
                manifest = runtime.prepare(case, output, image="pinned", run_id="run-new", commit="commit-new")
            self.assertEqual(sum(runner.PREPARE_INDEX in c for c in calls), 1)
            self.assertFalse(any("retrieve" in c or "serve" in c for c in calls))
            self.assertEqual(manifest["retrieval_probes_before_e2e"], 0)
            self.assertFalse((output / "preflight-working-index").exists())
            runtime.validate_prepared(output, case, run_id="run-new", commit="commit-new")


class ContextualPlanTests(unittest.TestCase):
    def plan(self, analysis):
        return runtime.build_v6_plan(analysis, "Question", source_run="run-new", source_commit="commit-new",
                                      prepared_manifest_sha256="shared")

    def test_all_turns_preserve_context_but_do_not_duplicate_request_executions(self):
        analysis = analysis_fixture(); plan = self.plan(analysis)
        faithful = plan["units"][0]
        self.assertEqual(plan["planned_executions"], 10)
        self.assertEqual(faithful["request"], analysis["request_catalog"][0]["request"])
        self.assertEqual(len(faithful["annotations"]), 2)
        self.assertEqual(len(faithful["occurrences"]), 2)
        self.assertEqual(faithful["annotations"][1]["prior_turn_feedback"], [{"visible_text": "Earlier search was empty"}])
        self.assertEqual(plan["unreplayable_planned_trials_or_calls"], analysis["unreplayable_occurrences"])
        self.assertEqual(plan["source_run"], "run-new")
        self.assertEqual(plan["quality_repetition"], 1)

    def test_actual_original_request_remains_separate_from_protocol_reference(self):
        analysis = analysis_fixture(); original = analysis["annotation_catalog"][-1]
        original.update(also_faithful_request=True, occurrences=[{"call_id": "original-actual"}])
        analysis["request_catalog"] = [{"request_id": original["request_id"], "request": original["request"],
                                         "occurrences": original["occurrences"]}]
        plan = self.plan(analysis)
        self.assertEqual([u["kind"] for u in plan["units"]], ["faithful", "original"])
        self.assertEqual(plan["units"][0]["request"], plan["units"][1]["request"])
        self.assertEqual(plan["units"][1]["occurrences"], [])
        self.assertEqual(plan["units"][0]["annotations"][0]["annotation_id"], original["annotation_id"])
        self.assertEqual(plan["units"][1]["execution_unit_id"], plan["units"][0]["unit_id"])
        self.assertEqual(plan["planned_executions"], 5)
        self.assertEqual(plan["unique_execution_units"], 1)

    def test_original_defaults_cannot_silently_change(self):
        analysis = analysis_fixture(); analysis["annotation_catalog"][-1]["request"]["limit"] = 20
        with self.assertRaisesRegex(ValueError, "protocol-fixed"):
            self.plan(analysis)

    def test_observed_scores_are_from_native_all_round_outputs_and_contexts(self):
        analysis = analysis_fixture()
        analysis["groups"] = [{"group": "g", "trials": [{"trial_id": "t", "profile": "zvec-grep",
            "zg_decision_rounds": [{"model_turn_index": i, "zg_decision_round_index": i,
                "zg_calls": [{"call_id": "scoped-" + str(i), "id": "reused-native-id", "message_id": "m" + str(i),
                    "annotation_id": "a" + str(i), "visible_text": "actual output " + str(i), "result_source": {"path": "native"}}]}
                for i in (1, 2)]}]}]
        with patch.object(runtime, "score_request", side_effect=lambda text, request, labels, entries, **kw: {"native": [text, kw["context_id"]]}) as score:
            rows = runtime.observed_scores_v6(analysis, {}, {})
        self.assertEqual(len(rows), 2)
        self.assertEqual(score.call_count, 2)
        self.assertEqual(rows[1]["request_scores"]["native"], ["actual output 2", "context-later"])
        self.assertEqual(rows[0]["native_call_id"], rows[1]["native_call_id"])
        self.assertNotEqual(rows[0]["message_id"], rows[1]["message_id"])

    def test_replay_quality_scores_every_context_at_fixed_first_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); plan = self.plan(analysis_fixture()); snapshot = {"source": {"sha256": "s"}, "index": {"documents": {"sha256": "d"}}}
            for unit in plan["units"]:
                dest = root / unit["unit_id"]; dest.mkdir()
                runner.write_json(dest / "status.json", {"returncode": 0})
                events = [{"event": "search", "unit_id": unit["unit_id"], "repetition": i, "status": "success",
                    "request": unit["request"], "source_identity": snapshot["source"], "index_identity": snapshot["index"],
                    "text": "different output " + str(i), "text_sha256": hashlib.sha256(("different output " + str(i)).encode()).hexdigest()}
                    for i in range(1, 6)]
                audit = [{"event": "start", "source_identity": snapshot["source"], "index_identity": snapshot["index"]},
                    {"event": "integrity", "stage": "start", "unchanged": True}, *events,
                    {"event": "integrity", "stage": "end", "unchanged": True}, {"event": "end", "integrity": "semantic_unchanged"}]
                (dest / "stdout.jsonl").write_text("\n".join(map(json.dumps, events)))
                (dest / "events.jsonl").write_text("\n".join(map(json.dumps, audit)))
            with patch("zg_bench.swe_qa.retrieval_replay.score_output", return_value={}), \
                    patch("zg_bench.swe_qa.retrieval_replay.score_request", side_effect=lambda text, *args, **kw: {"native": [text, kw.get("context_id")] }):
                report = evaluate_replays(plan, root, {}, {}, snapshot)
            self.assertEqual(report["scored_executions"], 10)
            contexts = report["units"][0]["quality_observation"]["context_scores"]
            self.assertEqual(len(contexts), 2)
            self.assertEqual(contexts[1]["request_scores"]["native"], ["different output 1", "context-later"])
            self.assertIs(report["units"][0]["stability"]["all_public_outputs_identical"], False)


if __name__ == "__main__":
    unittest.main()
