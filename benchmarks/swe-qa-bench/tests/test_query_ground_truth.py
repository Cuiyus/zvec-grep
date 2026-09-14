from __future__ import annotations

import copy
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from zg_bench.swe_qa.query_ground_truth import (
    GROUPS, MAX_INLINE_INSTRUCTION_BYTES, annotation_catalog, annotation_config, annotation_instruction,
    digest, execute, freeze_labels, parse_response, print_phase_result, reconcile, run_session,
    source_anchor, split_annotation_packet, verify_candidates, verify_review_sources,
)
from zg_bench.swe_qa.query_relevance import load_labels, resolve_label, score_query_text
from zg_bench.swe_qa.readonly_agents import agent_environment, agent_spec, build_agent_command, build_agent_config


class QueryGroundTruthTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        (self.source / "state.py").write_text(
            "class Computed:\n"
            "    @property\n"
            "    def getter(self):\n"
            "        return self._getter\n"
            "\n"
            "    def dependencies(self):\n"
            "        return inspect_bytecode(self.getter)\n")
        self.case = {"question": "How are dependencies identified?", "repo": {"url": "https://example.org/repo.git", "commit": "a" * 40}}
        self.request = {"root": "/app", "query": self.case["question"], "limit": 10}
        self.catalog = [{"annotation_id": "annotation-original", "kind": "original", "context_id": "context-original",
                         "request": self.request, "original_question": self.case["question"], "occurrences": []},
                        {"annotation_id": "annotation-followup", "kind": "faithful", "context_id": "context-followup",
                         "request": self.request, "original_question": self.case["question"], "occurrences": [{"trial_id": "trial-1"}]}]

    def nomination(self, symbol="Computed.dependencies", role="accepted"):
        return {"path": "state.py", "symbol": symbol, "role": role,
                "reason": "The method directly analyzes the getter bytecode to find dependencies."}

    def candidate(self, annotation_id="annotation-original", symbol="Computed.dependencies", role="accepted"):
        return {"annotation_id": annotation_id, "classification": "original" if annotation_id == "annotation-original" else "legitimate_subgoal",
                "goal": "Locate dependency discovery", "targets": [self.nomination(symbol, role)]}

    def proposals(self):
        return verify_candidates(self.catalog, {GROUPS[0]: {"annotations": [self.candidate()]}}, self.source)

    def reviews(self, proposals):
        return verify_review_sources({group: {"annotations": [{"proposal_id": p["proposal_id"], "decision": "accept",
                    "reason": "The cited code performs bytecode inspection rather than executing the getter; accepted is a direct entry.",
                    "source_checks": [{"path": "state.py", "start_line": 6, "end_line": 7,
                                       "claim": "The dependency routine inspects the getter bytecode."}]}
                for p in proposals if p["proposer_group"] != group]} for group in GROUPS}, self.source)

    def labels(self, proposals=None, reviews=None):
        proposals = self.proposals() if proposals is None else proposals
        reviews = self.reviews(proposals) if reviews is None else reviews
        reviews = verify_review_sources(reviews, self.source)
        return freeze_labels(self.catalog, proposals, reconcile(self.catalog, proposals, reviews), self.case,
                             analysis_sha256="b" * 64, case_sha256="c" * 64, entries_sha256="d" * 64)

    def test_actual_source_definition_evidence_and_hashes_are_generated_locally(self):
        target, evidence = source_anchor(self.source, self.nomination())
        self.assertEqual(target["definition_line"], 6)
        self.assertEqual(target["entry_end_line"], 7)
        self.assertEqual(target["symbol"], "Computed.dependencies")
        self.assertEqual(evidence["sha256"], digest(evidence["text"]))
        getter, _ = source_anchor(self.source, self.nomination("Computed.getter"))
        self.assertEqual(getter["entry_start_line"], 2)
        self.assertEqual(getter["definition_line"], 3)

    def test_source_validation_rejects_traversal_missing_and_wrong_definition(self):
        for changes in [{"path": "../outside.py"}, {"symbol": "Computed.missing"},
                        {"definition_line": 3}, {"evidence_start_line": 1}, {"evidence_end_line": 99}]:
            with self.subTest(changes=changes), self.assertRaises((ValueError, OSError)):
                source_anchor(self.source, {**self.nomination(), **changes})

    def test_exact_file_module_prefix_resolves_to_same_canonical_target(self):
        original, _ = source_anchor(self.source, self.nomination())
        prefixed, _ = source_anchor(self.source, {**self.nomination(), "symbol": "state.Computed.dependencies"})
        self.assertEqual(prefixed["symbol"], "Computed.dependencies")
        self.assertEqual(prefixed["original_symbol"], "state.Computed.dependencies")
        self.assertEqual(prefixed["target_id"], original["target_id"])
        path = self.source / "reflex/vars/base.py"
        path.parent.mkdir(parents=True)
        path.write_text((self.source / "state.py").read_text().replace("Computed:", "ComputedVar:"))
        target, _ = source_anchor(self.source, {"path": "reflex/vars/base.py", "symbol": "reflex.vars.base.ComputedVar.getter", "definition_line": 3})
        self.assertEqual(target["symbol"], "ComputedVar.getter")
        self.assertEqual(target["original_symbol"], "reflex.vars.base.ComputedVar.getter")

    def test_module_compatibility_never_uses_wrong_prefix_or_arbitrary_suffix(self):
        for symbol in ("other.Computed.dependencies", "other.state.Computed.dependencies",
                       "state.extra.Computed.dependencies", "dependencies", "Computed.dependencies.extra"):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                source_anchor(self.source, {**self.nomination(), "symbol": symbol})

    def test_symlink_escape_is_not_a_source_anchor(self):
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / "outside.py"
            path.write_text("def outside():\n    pass\n")
            (self.source / "linked.py").symlink_to(path)
            with self.assertRaises(ValueError):
                source_anchor(self.source, {"path": "linked.py", "symbol": "outside"})

    def test_source_verified_nomination_is_not_yet_semantically_accepted(self):
        proposals = self.proposals()
        self.assertEqual(proposals[0]["source_status"], "verified")
        decisions = reconcile(self.catalog, proposals, {})
        self.assertEqual(decisions[0]["status"], "unknown")
        self.assertEqual(self.labels(proposals, {})["queries"][0]["annotation_status"], "unknown")

    def test_self_endorsement_and_one_other_group_do_not_form_a_majority_gold(self):
        proposals = self.proposals()
        reviews = self.reviews(proposals)
        reviews[GROUPS[0]]["annotations"] = copy.deepcopy(reviews[GROUPS[1]]["annotations"])
        reviews[GROUPS[2]]["annotations"] = []
        self.assertEqual(reconcile(self.catalog, proposals, reviews)[0]["status"], "unknown")

    def test_both_other_groups_must_accept_with_nonempty_evidence(self):
        proposals = self.proposals()
        reviews = self.reviews(proposals)
        self.assertEqual(reconcile(self.catalog, proposals, reviews)[0]["status"], "accepted")
        for decision in ["reject", "unknown"]:
            changed = copy.deepcopy(reviews)
            changed[GROUPS[2]]["annotations"][0]["decision"] = decision
            self.assertEqual(reconcile(self.catalog, proposals, changed)[0]["status"], "unknown")
        reviews[GROUPS[2]]["annotations"][0]["source_checks"] = []
        self.assertEqual(reconcile(self.catalog, proposals, reviews)[0]["status"], "unknown")

    def test_false_review_citations_invalidate_semantic_endorsement(self):
        proposals = self.proposals()
        reviews = self.reviews(proposals)
        reviews[GROUPS[2]]["annotations"][0]["source_checks"][0]["end_line"] = 999
        checked = verify_review_sources(reviews, self.source)
        self.assertEqual(checked[GROUPS[2]]["annotations"][0]["decision"], "unknown")
        self.assertEqual(checked[GROUPS[2]]["annotations"][0]["original_decision"], "accept")
        self.assertEqual(reconcile(self.catalog, proposals, checked)[0]["status"], "unknown")

    def test_valid_alternative_targets_can_coexist_without_generator_consensus(self):
        outputs = {GROUPS[0]: {"annotations": [self.candidate()]},
                   GROUPS[1]: {"annotations": [self.candidate(symbol="Computed.getter")]}}
        proposals = verify_candidates(self.catalog, outputs, self.source)
        labels = self.labels(proposals)
        self.assertEqual(len(labels["queries"][0]["accepted_target_ids"]), 2)
        self.assertEqual(labels["queries"][0]["annotation_status"], "reviewed")

    def test_one_unresolved_proposal_does_not_erase_another_verified_entry(self):
        outputs = {GROUPS[0]: {"annotations": [self.candidate()]},
                   GROUPS[1]: {"annotations": [self.candidate(symbol="Computed.getter")]}}
        proposals = verify_candidates(self.catalog, outputs, self.source)
        reviews = self.reviews(proposals)
        for row in reviews[GROUPS[2]]["annotations"]:
            if row["proposal_id"] == proposals[1]["proposal_id"]:
                row["decision"] = "unknown"
        labels = self.labels(proposals, reviews)
        self.assertEqual(len(labels["queries"][0]["accepted_target_ids"]), 1)
        self.assertEqual(len(labels["queries"][0]["unresolved_proposal_ids"]), 1)

    def test_accepted_bridge_role_disagreement_remains_unknown_for_that_target(self):
        outputs = {GROUPS[0]: {"annotations": [self.candidate()]},
                   GROUPS[1]: {"annotations": [self.candidate(role="bridge")]}}
        proposals = verify_candidates(self.catalog, outputs, self.source)
        label = self.labels(proposals)["queries"][0]
        self.assertEqual(label["annotation_status"], "unknown")
        self.assertEqual(len(label["role_disagreements"]), 1)

    def test_context_specific_labels_share_request_but_never_silently_merge_intent(self):
        labels = self.labels()
        path = self.source / "labels.json"
        path.write_text(json.dumps(labels))
        loaded = load_labels(path, self.source)
        self.assertEqual(loaded["schema_version"], 2)
        self.assertIsNone(resolve_label(loaded, request=self.request)[0])
        self.assertEqual(resolve_label(loaded, request=self.request)[1], "ambiguous_context")
        original, _ = resolve_label(loaded, request=self.request, context_id="context-original")
        followup, _ = resolve_label(loaded, request=self.request, context_id="context-followup")
        self.assertEqual(original["annotation_status"], "reviewed")
        self.assertEqual(followup["annotation_status"], "unknown")
        self.assertIsNone(resolve_label(loaded, request=self.request, context_id="missing")[0])

    def test_unknown_original_has_no_fake_zero_and_can_still_load(self):
        labels = self.labels([], {})
        path = self.source / "unknown.json"
        path.write_text(json.dumps(labels))
        loaded = load_labels(path, self.source)
        task = {"repo": self.case["repo"], "protocol": {"limit": 10}, "targets": [], "groups": []}
        public = "freshness: fresh\n#1 matchedBy=vector state.py:6-7\nsource:\n6\t    def dependencies(self):\n"
        score = score_query_text(public, loaded, task, request=self.request, context_id="context-original")
        self.assertEqual(score["query_relevance"]["status"], "unknown")
        self.assertIsNone(score["query_relevance"]["target"]["rr_at_10"])

    def test_labels_verify_real_source_hash_on_reload(self):
        path = self.source / "labels.json"
        path.write_text(json.dumps(self.labels()))
        load_labels(path, self.source)
        with (self.source / "state.py").open("a") as f:
            f.write("# changed\n")
        with self.assertRaises(ValueError):
            load_labels(path, self.source)

    def test_duplicate_candidate_and_review_ids_are_not_cherry_picked(self):
        duplicate = verify_candidates(self.catalog, {GROUPS[0]: {"annotations": [self.candidate(), self.candidate()]}}, self.source)
        self.assertTrue(all(p["source_status"] == "invalid" for p in duplicate))
        proposals = self.proposals()
        reviews = self.reviews(proposals)
        reviews[GROUPS[1]]["annotations"] *= 2
        self.assertEqual(reconcile(self.catalog, proposals, reviews)[0]["status"], "unknown")

    def test_catalog_rejects_missing_original_cross_case_and_duplicate_identity(self):
        self.assertEqual(annotation_catalog({"annotation_catalog": self.catalog}, self.case), self.catalog)
        for catalog in [self.catalog[1:], self.catalog + self.catalog[:1], [{**self.catalog[0], "original_question": "other"}]]:
            with self.assertRaises(ValueError):
                annotation_catalog({"annotation_catalog": catalog}, self.case)

    def test_parse_requires_whole_structured_answer_not_a_fragment(self):
        self.assertEqual(parse_response('```json\n{"annotations": []}\n```'), {"annotations": []})
        for text in ['explanation {"annotations": []}', '[]', '{"annotations": {}}']:
            with self.assertRaises(ValueError):
                parse_response(text)

    def test_single_complete_fence_accepts_real_glm_prose_without_repairing_json(self):
        answer = 'Now I have all the information needed. Here is my final annotation:\n\n```json\n{"annotations": [{"annotation_id": "one"}]}\n```\nThis concludes my annotation.'
        self.assertEqual(parse_response(answer), {"annotations": [{"annotation_id": "one"}]})
        self.assertEqual(parse_response('{"annotations": [], "note": "``` is literal source text"}')["annotations"], [])
        for text in ('```json\n{"annotations": []}\n```\n```json\n{"annotations": []}\n```',
                     '{"other": 1}\n```json\n{"annotations": []}\n```',
                     '```json\n{"annotations": []}\n```\n{"other":',
                     '```json\n{"annotations": []} {"annotations": []}\n```',
                     '```json\n{"annotations": [}\n```',
                     '```json\n{"annotations": []}',
                     '```python\n{"annotations": []}\n```'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_response(text)

    def test_qoder_receives_exact_inline_packet_without_changing_its_readonly_permissions(self):
        spec = agent_spec("qodercli", "qwen3.8-max")
        packet = {"annotations": [{"annotation_id": "fixture", "query": "中文 query with quotes \\\" and `$(unsafe)`"}]}
        instruction = annotation_instruction(spec, "candidate", packet)
        self.assertNotIn("/annotation/input.json", instruction)
        data = instruction.split("\nANNOTATION_PACKET_JSON_START\n", 1)[1].rsplit("\nANNOTATION_PACKET_JSON_END", 1)[0]
        self.assertEqual(json.loads(data), packet)
        command = build_agent_command(spec, instruction, config_path="/run/qa/qoder.json")
        self.assertEqual(command[-2:], ["--", instruction])
        self.assertEqual(annotation_config(spec), build_agent_config(spec, zg=False, max_model_turns=40))
        opencode = agent_spec("opencode", "glm-5.2", base_url="http://localhost/v1")
        self.assertIn("/annotation/input.json", annotation_instruction(opencode, "candidate", packet))
        self.assertNotIn("ANNOTATION_PACKET_JSON_START\n", annotation_instruction(opencode, "candidate", packet))

    def test_oversize_qoder_candidate_and_review_split_preserves_full_context(self):
        spec = agent_spec("qodercli", "qwen3.8-max")
        units = [{"annotation_id": "a" + str(i), "prior_turn_feedback": "源" * 20000} for i in range(2)]
        parts = split_annotation_packet(GROUPS[2], "candidate", {"annotations": units})
        self.assertEqual([u for part in parts for u in part["annotations"]], units)
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(len(annotation_instruction(spec, "candidate", p).encode()) + 1 <= MAX_INLINE_INSTRUCTION_BYTES for p in parts))
        proposals = [{"proposal_id": str(i), "annotation_id": "a0", "evidence": "文" * 20000} for i in range(2)]
        packet = {"queries": [{"annotation_id": "a0", "context": "full unchanged context"}], "proposals": proposals}
        parts = split_annotation_packet(GROUPS[2], "review", packet)
        self.assertEqual([p for part in parts for p in part["proposals"]], proposals)
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(part["queries"] == packet["queries"] for part in parts))
        self.assertEqual(split_annotation_packet(GROUPS[0], "candidate", {"annotations": units}), [{"annotations": units}])

    def test_indivisible_large_qoder_packet_is_preserved_and_never_launches_agent(self):
        packet = {"annotations": [{"annotation_id": "a", "context": "界" * MAX_INLINE_INSTRUCTION_BYTES}]}
        self.assertEqual(split_annotation_packet(GROUPS[2], "candidate", packet), [packet])
        with tempfile.TemporaryDirectory() as directory, patch("zg_bench.swe_qa.query_ground_truth.subprocess.Popen") as launch:
            output = Path(directory) / "oversized"
            result = run_session(group=GROUPS[2], phase="candidate", packet=packet, source_root=self.source, image="fixture", output=output)
            self.assertEqual(result["status"], "packet_too_large")
            self.assertEqual(json.loads((output / "packet/input.json").read_text()), packet)
            launch.assert_not_called()

    def test_failure_progress_explains_reason_without_exposing_credentials(self):
        capture = io.StringIO()
        with patch.dict(os.environ, {"GLM_API_KEY": "fixture-secret"}), contextlib.redirect_stdout(capture):
            print_phase_result("candidate", GROUPS[0], 0, {"status": "failed", "error_kind": "JSONDecodeError", "error": "Cannot decode fixture-secret"})
        row = json.loads(capture.getvalue())
        self.assertEqual(row["error_kind"], "JSONDecodeError")
        self.assertIn("[REDACTED]", row["error"])
        self.assertNotIn("fixture-secret", capture.getvalue())

    def test_native_session_wrapper_passes_qoder_inline_argument_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = root / "record.py"
            received = root / "received.json"
            recorder.write_text("import json, os, sys\nfrom pathlib import Path\nPath(os.environ['ANNOTATION_CAPTURE']).write_text(json.dumps(sys.argv[1:], ensure_ascii=False))\nprint(json.dumps({'type':'result','subtype':'success','result':'done'}))\n")
            spec = agent_spec("qodercli", "qwen3.8-max")
            instruction = annotation_instruction(spec, "candidate", {"annotations": [{"query": "带换行\n和引号 \\\" 和$(literal)", "context": "源" * 1000}]})
            command = build_agent_command(spec, instruction, config_path="/run/qa/qoder.json")
            session = {"command": [sys.executable, str(recorder), *command[1:]],
                       "env": {"ANNOTATION_CAPTURE": str(received)}, "log_dir": str(root / "logs"),
                       "native_name": spec.stream_filename,
                       "limits": {"wall_seconds": 5, "tool_calls": 100, "model_requests": 40, "input_tokens": 600000}}
            session_path = root / "session-spec.json"
            session_path.write_text(json.dumps(session))
            script = Path(__file__).resolve().parents[1] / "scripts/qa-session.py"
            result = subprocess.run([sys.executable, str(script), "--spec", str(session_path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(received.read_text()), command[1:])
            self.assertEqual(json.loads(received.read_text())[-1].encode(), instruction.encode())

    def test_whole_annotation_pipeline_uses_fresh_cross_review_packets_then_freezes_labels(self):
        with tempfile.TemporaryDirectory() as output_directory:
            root = Path(output_directory)
            case_path, analysis_path, entries_path = root / "case.json", root / "analysis.json", root / "entries.json"
            # These gold/final-answer fields must never enter annotation inputs.
            case_path.write_text(json.dumps({**self.case, "reference_answer": "HIDDEN_REFERENCE", "evidence": ["HIDDEN_GOLD"]}))
            analysis_path.write_text(json.dumps({"annotation_catalog": self.catalog, "final_answer": "HIDDEN_FINAL", "current_output": "HIDDEN_CURRENT_OUTPUT"}))
            entries = {"repo": self.case["repo"], "source_case_sha256": digest(case_path.read_bytes())}
            entries_path.write_text(json.dumps(entries))
            calls = []
            call_lock = threading.Lock()
            candidate_barrier = threading.Barrier(3)
            candidate_finished = set()

            def runner(**kwargs):
                packet = kwargs["packet"]
                with call_lock:
                    calls.append(kwargs)
                for forbidden in ("HIDDEN_REFERENCE", "HIDDEN_GOLD", "HIDDEN_FINAL", "HIDDEN_CURRENT_OUTPUT"):
                    self.assertNotIn(forbidden, json.dumps(packet))
                if kwargs["phase"] == "candidate":
                    candidate_barrier.wait(timeout=3)
                    if kwargs["group"] == GROUPS[0]:
                        time.sleep(0.03)
                    rows = [self.candidate(u["annotation_id"]) for u in packet["annotations"]]
                    self.assertEqual(len(packet["annotations"]), 2)
                    with call_lock:
                        candidate_finished.add(kwargs["group"])
                else:
                    with call_lock:
                        self.assertEqual(candidate_finished, set(GROUPS))
                    self.assertTrue(all("proposer_group" not in p for p in packet["proposals"]))
                    rows = [{"proposal_id": p["proposal_id"], "decision": "accept", "reason": "The definition directly discovers dependencies by inspecting getter bytecode.",
                             "source_checks": [{"path": "state.py", "start_line": 6, "end_line": 7,
                                                "claim": "The method inspects getter bytecode."}]} for p in packet["proposals"]]
                return {"status": "completed", "group": kwargs["group"], "phase": kwargs["phase"],
                        "parsed": {"annotations": rows}, "session": {"observed": {"input_tokens": 123, "tool_calls": 2, "model_requests": 3}}}

            args = argparse.Namespace(case=case_path, analysis=analysis_path, entries=entries_path,
                                      source_root=self.source, output=root / "annotation", batch_size=8, image="fixture", timeout=30)
            with patch("zg_bench.swe_qa.query_ground_truth.run_checked", return_value=self.case["repo"]["commit"]), \
                 patch("zg_bench.swe_qa.retrieval_eval.load_manifest", return_value=entries), contextlib.redirect_stdout(io.StringIO()):
                report = execute(args, session_runner=runner)
            self.assertEqual(len(calls), 6)
            self.assertEqual([c["phase"] for c in calls], ["candidate"] * 3 + ["review"] * 3)
            self.assertEqual(len({c["output"] for c in calls}), 6)
            self.assertEqual(report["scorable_queries"], 2)
            self.assertEqual(report["unknown_queries"], 0)
            self.assertFalse(report["annotation_cost_included_in_e2e"])
            self.assertEqual(report["annotation_cost"]["input_tokens"], 738)
            self.assertEqual(report["execution"]["max_parallel_groups"], 3)
            self.assertEqual([s["group"] for s in report["sessions"]], list(GROUPS) * 2)
            labels = load_labels(args.output / "query-intents.json", self.source)
            self.assertEqual(report["labels_sha256"], digest((args.output / "query-intents.json").read_bytes()))
            self.assertEqual(len(labels["request_bindings"]), 2)
            self.assertTrue((args.output / "candidate-outputs.json").is_file())
            self.assertTrue((args.output / "review-outputs.json").is_file())
            self.assertTrue((args.output / "decisions.json").is_file())
            with self.assertRaises(ValueError):
                execute(args, session_runner=runner)


@unittest.skipUnless(os.environ.get("OPENCODE_READONLY_TEST_BINARY"), "requires pinned OpenCode; only a local fake provider is used")
class OpenCodeAnnotationPacketContractTests(unittest.TestCase):
    def test_packet_external_read_is_allowed_and_unrelated_external_read_is_denied(self):
        binary = os.environ["OPENCODE_READONLY_TEST_BINARY"]
        self.assertEqual(subprocess.check_output([binary, "--version"], text=True, timeout=10).strip(), "1.18.4")
        with tempfile.TemporaryDirectory(prefix="annotation-packet-contract-") as directory:
            root = Path(directory).resolve()
            corpus, packet = root / "corpus", root / "annotation"
            corpus.mkdir()
            packet.mkdir()
            (packet / "input.json").write_text('{"fixture": "ANNOTATION_PACKET_READ_CONFIRMED"}\n')
            (root / "unrelated.txt").write_text("UNRELATED_EXTERNAL_FILE_MUST_STAY_UNREAD\n")
            requests = []

            class FakeProvider(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    requests.append(body)
                    task = bool(body.get("tools"))
                    results = [m for m in body.get("messages", []) if m.get("role") == "tool"]
                    if task and len(results) < 2:
                        path = packet / "input.json" if not results else root / "unrelated.txt"
                        delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "fixture-read-" + str(len(results)),
                                 "type": "function", "function": {"name": "read", "arguments": json.dumps({"filePath": str(path)})}}]}
                        finish = "tool_calls"
                    else:
                        delta, finish = {"role": "assistant", "content": '{"annotations": []}'}, "stop"
                    chunks = [{"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": body["model"],
                               "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                              {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": body["model"],
                               "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}]
                    payload = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(payload.encode())))
                    self.end_headers()
                    self.wfile.write(payload.encode())

            server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                spec = agent_spec("opencode", "glm-5.2", base_url=f"http://127.0.0.1:{server.server_port}/v1")
                config = root / "opencode.json"
                config.write_text(json.dumps(annotation_config(spec, packet_directory=str(packet))))
                env = {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8",
                       "OPENAI_API_KEY": "offline-fixture-not-a-real-key", "OPENCODE_DISABLE_MODELS_FETCH": "true",
                       "XDG_CONFIG_HOME": str(root / "config"), "XDG_DATA_HOME": str(root / "data"),
                       "XDG_STATE_HOME": str(root / "state"), "XDG_CACHE_HOME": str(root / "cache"),
                       **agent_environment(spec, config_path=str(config))}
                command = build_agent_command(spec, "Read the annotation packet; return JSON.", config_path=str(config), max_model_turns=4)
                command[0] = binary
                result = subprocess.run(command, env=env, cwd=corpus, capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stderr)
                observations = [m.get("content") for r in requests if r.get("tools")
                                for m in r.get("messages", []) if m.get("role") == "tool"]
                content = json.dumps(observations)
                self.assertIn("ANNOTATION_PACKET_READ_CONFIRMED", content)
                self.assertNotIn("UNRELATED_EXTERNAL_FILE_MUST_STAY_UNREAD", content)
                self.assertGreaterEqual(len(observations), 2)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
