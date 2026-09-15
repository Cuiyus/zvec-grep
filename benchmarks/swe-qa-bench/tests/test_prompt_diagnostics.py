"""Fixed native context, text-only interventions, failure accounting and screening."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from zg_bench.swe_qa.prompt_diagnostics import (
    VARIANTS, PartialTransportError, analyze, build_plan, default_prompt_config, extract_states, load_plan,
    parse_response, render_candidate_prompts, run_plan, select_candidate, sha,
    validate_calls, variant_request,
)

NAME = "zvec_grep_zvec_grep_search"
ENDPOINT = "https://provider.example/v1"


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def request():
    return {"model": "model", "temperature": 0, "stream": True,
            "messages": [{"role": "system", "content": "prefix\nORIGINAL GUIDANCE\nsuffix"},
                         {"role": "user", "content": "Question unrelated to prompt text"}],
            "tools": [{"type": "function", "function": {"name": NAME, "description": "original description",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "default": 10}},
                               "required": ["query"], "additionalProperties": False}}}]}


def state(body=None):
    body = body or request()
    return {"state_id": "group-original", "group_id": "group", "request": body,
            "request_sha256": sha(body), "endpoint": ENDPOINT, "model": "model"}


def config():
    return {"original_guidance": "ORIGINAL GUIDANCE", "candidate_guidance": "NEW GUIDANCE",
            "description_overrides": {NAME: "new description"}, "zg_tool_names": [NAME]}


def response(arguments='{"query":"concept"}', name=NAME):
    return {"id": "answer", "model": "model", "usage": {"prompt_tokens": 13, "completion_tokens": 5},
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"tool_calls": [
                {"id": "call1", "type": "function", "function": {"name": name, "arguments": arguments}}]}}]}


class PromptDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def make_plan(self):
        return build_plan({"states": [state()], "missing_categories": []}, config(), self.root)

    def write_trace(self, body=None, raw=None):
        body = body or request()
        base = self.root / "agent"
        wire = raw if raw is not None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        dump(base / "wire-requests/request-001.json", body)
        if raw is not None:
            (base / "wire-requests/request-001.raw.json").write_bytes(raw)
        event = {"event": "request", "request_id": 1, "model": body["model"],
                 "tool_names": [NAME], "request_sha256": hashlib.sha256(wire).hexdigest()}
        (base / "wire.jsonl").write_text(json.dumps(event) + "\n")
        dump(base / "session-spec.json", {"tap_upstream": ENDPOINT})
        return {"group_id": "group", "trial_id": "r01", "agent": "opencode", "agent_dir": str(base), "model": "model"}

    def test_extract_exact_wire_and_missing_categories_and_qoder(self):
        spec = self.write_trace()
        value = extract_states([spec, {**spec, "group_id": "qoder", "agent": "qodercli"}], self.root / "states")
        self.assertEqual(len(value["states"]), 1)
        self.assertEqual(value["states"][0]["category"], "original_question")
        self.assertEqual(value["states"][0]["request"], request())
        self.assertEqual(len(value["missing_categories"]), 9)
        self.assertIn("no pasted-history", value["unavailable"][0]["reason"])

    def test_raw_wire_allows_noncanonical_serialization_but_tamper_rejected(self):
        raw = json.dumps(request(), indent=1).encode()
        spec = self.write_trace(raw=raw)
        value = extract_states([spec], self.root / "states")
        self.assertTrue(value["states"][0]["raw_wire_verified"])
        body = request()
        body["temperature"] = 0.5
        dump(Path(spec["agent_dir"]) / "wire-requests/request-001.json", body)
        self.assertFalse(extract_states([spec], self.root / "states2")["states"])

    def test_capture_only_requires_native_construction_and_marks_provenance(self):
        spec = self.write_trace()
        spec.update(capture_only=True, intended_endpoint=ENDPOINT)
        with self.assertRaises(ValueError):
            extract_states([spec], self.root / "states")
        spec["native_request_builder_verified"] = True
        value = extract_states([spec], self.root / "states")
        self.assertEqual(value["states"][0]["provenance"], "native_initial_request_capture_only")

    def test_later_feedback_not_inferred_to_be_source_verified(self):
        body = request()
        body["messages"] += [{"role": "assistant", "content": "Search found source"}, {"role": "tool", "content": "great match", "tool_call_id": "id"}]
        spec = self.write_trace(body)
        spec["state_annotations"] = {"1": {"category": "relevant_zg_entry"}}
        self.assertFalse(extract_states([spec], self.root / "states")["states"])
        spec["state_annotations"]["1"].update(source_verified=True, source_refs=[{"path": "source.py", "start_line": 1}])
        self.assertEqual(extract_states([spec], self.root / "states2")["states"][0]["category"], "relevant_zg_entry")

    def test_prompt_factor_interventions_preserve_messages_and_schema(self):
        original = request()
        for variant in VARIANTS:
            value = variant_request(state(original), config(), variant)
            self.assertEqual(value["tools"][0]["function"]["parameters"], original["tools"][0]["function"]["parameters"])
            self.assertEqual(value["messages"][1:], original["messages"][1:])
            self.assertEqual(value["temperature"], 0)
            self.assertEqual("NEW GUIDANCE" in value["messages"][0]["content"], variant in {"P10", "P11"})
            self.assertEqual(value["tools"][0]["function"]["description"] == "new description", variant in {"P01", "P11"})
        self.assertEqual(original, request())

    def test_missing_guidance_or_tool_override_never_silent(self):
        with self.assertRaises(ValueError):
            variant_request(state(), {**config(), "original_guidance": "missing"}, "P10")
        with self.assertRaises(ValueError):
            variant_request(state(), {**config(), "description_overrides": {"unavailable_rg": "desc"}}, "P01")

    def test_plan_exactly_ten_each_immutable_and_fixed_model_seed(self):
        plan = self.make_plan()
        self.assertEqual(len(plan["samples"]), 40)
        for variant in VARIANTS:
            self.assertEqual(sum(s["variant"] == variant for s in plan["samples"]), 10)
        self.assertNotIn("seed", plan["requests"]["group-original-P00"]["request"])
        with self.assertRaises(ValueError):
            build_plan({"states": [state()]}, config(), self.root, repeats=4)
        plan["samples"].pop()
        dump(self.root / "plan.json", plan)
        with self.assertRaises(ValueError):
            load_plan(self.root / "plan.json")

    def test_parallel_streamed_tool_arguments_usage_and_final_text(self):
        events = [
            {"id": "r", "model": "model", "choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": NAME, "arguments": '{"query":'}},
                {"index": 1, "id": "c2", "function": {"name": NAME, "arguments": '{"query":"other"}'}}]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"concept"}'}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}]
        wire = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
        parsed = parse_response(wire, "text/event-stream")
        self.assertTrue(parsed["decision_complete"])
        self.assertEqual(len(parsed["choices"][0]["tool_calls"]), 2)
        self.assertEqual(parsed["usage"]["prompt_tokens"], 100)
        self.assertEqual(validate_calls(parsed, request(), [NAME])["valid_calls"], 2)

    def test_validation_uses_actual_schema_without_argument_repair(self):
        parsed = parse_response(json.dumps(response('{"query":["wrong"],"limit":0}')), "application/json")
        validation = validate_calls(parsed, request(), [NAME])
        self.assertFalse(validation["calls"][0]["valid"])
        self.assertEqual(validation["calls"][0]["arguments"]["query"], ["wrong"])
        self.assertIsNone(validation["agent_repaired_calls"])

    def test_token_limit_finish_is_incomplete_even_with_valid_arguments(self):
        body = response()
        body["choices"][0]["finish_reason"] = "length"
        self.assertFalse(parse_response(json.dumps(body), "application/json")["decision_complete"])

    def test_run_preserves_failed_attempt_denominator_and_never_retries(self):
        self.make_plan()
        count = []
        def fake(endpoint, key, body, timeout):
            count.append(1)
            if len(count) == 1:
                return 500, {"Content-Type": "application/json"}, b'{"error":"secret-token"}'
            return 200, {"Content-Type": "application/json"}, json.dumps(response()).encode()
        with patch.dict(os.environ, {"DIAGNOSTIC_KEY": "secret-token"}):
            report = run_plan(self.root / "plan.json", credential_env="DIAGNOSTIC_KEY", endpoint=ENDPOINT, transport=fake)
            self.assertEqual(report["completed"], 39)
            self.assertEqual(report["planned"], 40)
            run_plan(self.root / "plan.json", credential_env="DIAGNOSTIC_KEY", endpoint=ENDPOINT, transport=fake)
            self.assertEqual(len(count), 40)
        files = list((self.root / "samples").rglob("*.json")) + list((self.root / "samples").rglob("*.txt"))
        self.assertFalse(any("secret-token" in p.read_text() for p in files))
        self.assertEqual(report["selection"]["variant"], "P00")

    def test_parallel_run_bounds_concurrency_and_preserves_all_samples(self):
        self.make_plan()
        lock = threading.Lock()
        active = maximum = calls = 0
        def fake(endpoint, key, body, timeout):
            nonlocal active, maximum, calls
            with lock:
                active += 1
                calls += 1
                maximum = max(maximum, active)
            time.sleep(0.005)
            with lock:
                active -= 1
            return 200, {"Content-Type": "application/json"}, json.dumps(response()).encode()
        with patch.dict(os.environ, {"DIAGNOSTIC_KEY": "key"}):
            report = run_plan(self.root / "plan.json", credential_env="DIAGNOSTIC_KEY",
                              endpoint=ENDPOINT, transport=fake, max_workers=2)
        self.assertEqual(calls, 40)
        self.assertEqual(report["completed"], 40)
        self.assertEqual(maximum, 2)

    def test_run_refuses_changed_endpoint_before_sending(self):
        self.make_plan()
        with patch.dict(os.environ, {"DIAGNOSTIC_KEY": "key"}):
            with self.assertRaises(ValueError):
                run_plan(self.root / "plan.json", credential_env="DIAGNOSTIC_KEY", endpoint="https://another.example/v1")
        self.assertFalse((self.root / "samples").exists())

    def test_partial_stream_error_keeps_raw_feedback_without_retry(self):
        self.make_plan()
        partial = b'data: {"choices":[],"usage":{"prompt_tokens":10}}\n\n'
        def fake(*args):
            raise PartialTransportError(200, {"content-type": "text/event-stream"}, partial, TimeoutError("partial secret-token"))
        with patch.dict(os.environ, {"DIAGNOSTIC_KEY": "secret-token"}):
            report = run_plan(self.root / "plan.json", credential_env="DIAGNOSTIC_KEY", endpoint=ENDPOINT, transport=fake)
        self.assertEqual(report["completed"], 0)
        self.assertEqual(report["samples"][0]["status"], "incomplete_transport")
        self.assertEqual(report["samples"][0]["response"]["usage"]["prompt_tokens"], 10)
        self.assertEqual(len(list((self.root / "samples").rglob("response.txt"))), 40)

    def test_interrupted_sample_not_resubmitted(self):
        plan = self.make_plan()
        sample = plan["samples"][0]
        dump(self.root / "samples" / sample["sample_id"] / "attempt.json", {"status": "unknown"})
        count = []
        def fake(*args):
            count.append(1)
            return 200, {}, json.dumps(response()).encode()
        with patch.dict(os.environ, {"DIAGNOSTIC_KEY": "key"}):
            report = run_plan(self.root / "plan.json", credential_env="DIAGNOSTIC_KEY", endpoint=ENDPOINT, transport=fake)
        self.assertEqual(len(count), 39)
        self.assertEqual(report["samples"][0]["status"], "interrupted_or_unknown")

    def test_selection_requires_reviewed_improvement_not_adoption_or_repetition(self):
        plan = self.make_plan()
        parsed = parse_response(json.dumps(response()), "application/json")
        rows = [{**s, "status": "completed", "validation": validate_calls(parsed, request(), [NAME])} for s in plan["samples"]]
        evidence = {"plan_sha256": plan["plan_sha256"], "assessment_frozen": True, "rows": [
            {"sample_id": s["sample_id"], "source_verified": True, "source_refs": [{"path": "code.py", "start_line": 10}],
             "goal_correct": True, "retrieval": {"status": "complete", "hit_at_10": True, "rr_at_10": 0.5}, "rationale": "Reviewed goal and supporting source"} for s in rows]}
        self.assertEqual(select_candidate(plan, rows, evidence)["variant"], "P00")
        for sample, review in zip(rows, evidence["rows"]):
            if sample["variant"] == "P10":
                review["retrieval"]["rr_at_10"] = 1.0
        self.assertEqual(select_candidate(plan, rows, evidence)["variant"], "P10")
        evidence["rows"][0]["source_verified"] = False
        # If the withheld row is not P00/P10 it need not invalidate P10; choose
        # one P10 review explicitly to test the conservative promotion gate.
        next(r for r in evidence["rows"] if "-P10-" in r["sample_id"])["source_verified"] = False
        self.assertEqual(select_candidate(plan, rows, evidence)["variant"], "P00")

    def test_default_prompt_config_uses_release_catalog_search_only(self):
        self.write_trace()
        dump(self.root / "native-install.json", {"installed_guidance_text": "ORIGINAL GUIDANCE\n"})
        dump(self.root / "install-manifest.json", {"installed_guidance_text": "ORIGINAL GUIDANCE\n"})
        cfg = default_prompt_config({"group": self.root})["groups"]["group"]
        self.assertEqual(cfg["zg_tool_names"], [NAME])
        self.assertEqual(list(cfg["description_overrides"]), [NAME])
        self.assertNotIn("{search_tool}", cfg["candidate_guidance"])
        self.assertEqual(set(render_candidate_prompts(search_tool=NAME)["description_overrides"]), {"zvec_grep_search"})


if __name__ == "__main__":
    unittest.main()
