"""Source-faithful first-decision extraction, including absence/ambiguity cases."""
import hashlib
import json
from pathlib import Path

import tempfile
import unittest

from zg_bench.swe_qa.first_query_analysis import analyze, main, write_report

ZG = "zvec_grep_zvec_grep_search"


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def events(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(v) for v in values) + "\n")


def experiment(root, specs):
    dump(root / "plan.json", {"case_id": "case", "trials": [
        {"trial_id": tid, "profile": profile, "status": status,
         "trajectory_path": f"{tid}/agent/trajectory.json"} for tid, profile, status in specs]})
    dump(root / "manifest.json", {"agent": "opencode", "model": "model"})
    dump(root / "case.json", {"question": "Where is the getter?"})


def oc(kind, mid, **kwargs):
    return {"type": kind, "sessionID": "session", "part": {"messageID": mid, **kwargs}}


def tool(mid, cid, args, text="visible", name=ZG, start=10, end=20, status="completed"):
    return oc("tool_use", mid, callID=cid, tool=name,
              state={"input": args, "output": text, "status": status, "time": {"start": start, "end": end}})


def side(query, text, **kwargs):
    return {"event": "search", "sequence": 1, "status": "success", "text": text,
            "request": {"queries": [query], "routes": [], **kwargs},
            "result": {"diagnostics": {"index": {"routes": [
                {"mode": "fts", "query": query}, {"mode": "vector", "query": query}]}}}}




def qassistant(mid, blocks, usage=None):
    return {"type": "assistant", "session_id": "s", "message": {"id": mid, "content": blocks, "usage": usage or {"input_tokens": 0}}}


def qcall(cid, args, name=ZG):
    return {"type": "tool_use", "id": cid, "name": name, "input": args}


def qresult(cid, text="visible"):
    return {"type": "user", "session_id": "s", "message": {"content": [{"type": "tool_result", "tool_use_id": cid, "content": text}]}}















class FirstQueryAnalysisTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_same_round_parallel_full_batch_exact_output_and_catalog(self):
        tmp_path = self.root
        experiment(tmp_path, [("r1", "zvec-grep", "completed")])
        agent = tmp_path / "r1/agent"
        events(agent / "opencode.txt", [oc("step_start", "m1"),
            tool("m1", "read", {"filePath": "/app"}, "directory", "read", 1, 2),
            tool("m1", "a", {"query": "first"}, "out1", start=10, end=30),
            tool("m1", "b", {"query": "second", "limit": 15}, "out2", start=20, end=40),
            oc("step_finish", "m1", reason="tool-calls", tokens={"input": 4}),
            oc("step_start", "m2"), oc("step_finish", "m2", reason="stop")])
        events(agent / "zg-trace.jsonl", [side("first", "out1"), side("second", "out2", limit=15)])
        r = analyze(tmp_path)
        trial = r["groups"][0]["trials"][0]
        batch = trial["first_zg_decision_round"]
        assert len(batch["all_tool_calls"]) == 3
        assert len(batch["zg_calls"]) == 2
        assert batch["has_prior_turn_feedback"] is False
        assert len(batch["same_message_feedback_returned_before_first_zg_native_event"]) == 1
        assert batch["overlapping_zg_execution_pairs"] == [["a", "b"]]
        first = trial["first_zg_call"]
        assert first["source"]["line"] == 3
        assert first["visible_text"] == "out1"
        assert "limit" not in first["raw_arguments"]
        assert first["backend"]["source"]["line"] == 1
        assert first["backend"]["executed_routes"][1]["mode"] == "vector"
        assert first["source"]["sha256"] == hashlib.sha256((agent / "opencode.txt").read_bytes()).hexdigest()
        q = next(q for q in r["query_catalog"] if q["text"] == "first")
        assert q["occurrence_count"] == q["trial_count"] == 1
        assert len(q["occurrences"][0]["locations"]) == 4  # raw + request + 2 routes
        assert r["groups"][0]["original_question"] == "Where is the getter?"


    def test_qoder_prior_message_vs_streamed_same_message_array_literal(self):
        tmp_path = self.root
        experiment(tmp_path, [("r1", "zvec-grep", "completed")])
        agent = tmp_path / "r1/agent"
        literal = '["two concepts", "other intent"]'
        args = {"query": "getter", "vector": literal, "routes": [{"mode": "fts", "query": "symbol"}]}
        events(agent / "qodercli-stream.jsonl", [
            qassistant("m1", [qcall("g1", {"pattern": "name"}, "Grep")]), qresult("g1"),
            qassistant("m2", [{"type": "thinking", "thinking": "reasoning"}]),
            qassistant("m2", [qcall("g2", {"pattern": "**/*"}, "Glob")]), qresult("g2"),
            qassistant("m2", [qcall("z", args)]),
            qassistant("m2", [qcall("z", args)], {"input_tokens": 42}), qresult("z", "zg output"),
            {"type": "result"}])
        events(agent / "zg-trace.jsonl", [side("getter", "zg output", routes=[{"mode": "vector", "query": literal}, {"mode": "fts", "query": "symbol"}])])
        r = analyze(tmp_path)
        trial = r["groups"][0]["trials"][0]
        batch = trial["first_zg_decision_round"]
        assert trial["zg_tool_calls_attempted"] == 1  # repeated complete block deduplicated
        assert batch["model_turn_index"] == 2
        assert [x["call_id"] for x in batch["prior_turn_feedback"]] == ["g1"]
        assert [x["call_id"] for x in batch["same_message_feedback_returned_before_first_zg_native_event"]] == ["g2"]
        texts = {x["text"] for x in r["query_catalog"]}
        assert literal in texts and "two concepts" not in texts
        assert "symbol" in texts
        assert len(batch["usage_snapshots"]) == 4


    def test_missing_failed_truncated_and_no_call_not_collapsed(self):
        tmp_path = self.root
        specs = [("missing", "zvec-grep", "failed"), ("no-call", "zvec-grep", "completed"),
                 ("truncated", "zvec-grep", "timeout"), ("error", "zvec-grep", "failed")]
        experiment(tmp_path, specs)
        events(tmp_path / "no-call/agent/opencode.txt", [oc("step_start", "m"), oc("step_finish", "m", reason="stop")])
        events(tmp_path / "truncated/agent/opencode.txt", [oc("step_start", "m")])
        events(tmp_path / "error/agent/opencode.txt", [oc("step_start", "m"), tool("m", "z", {"query": "q"}, "unknown tool", status="error")])
        r = analyze(tmp_path)
        rows = {x["trial_id"]: x for x in r["groups"][0]["trials"]}
        assert rows["missing"]["zg_adoption_observed"] is None
        assert rows["no-call"]["zg_adoption_observed"] is False
        assert rows["no-call"]["zg_tool_calls_attempted"] == 0
        assert rows["truncated"]["zg_tool_calls_attempted"] is None
        assert rows["error"]["zg_adoption_observed"] is True
        assert rows["error"]["first_zg_call"]["status"] == "error"
        assert rows["error"]["execution_status"] == "failed"
        assert r["groups"][0]["adoption_summary"] == {"planned_treatment_trials": 4, "adopted_trials": 1, "no_call_trials": 1, "unknown_trials": 2}


    def test_ci_subset_preserves_known_adoption_and_measurement_failure(self):
        tmp_path = self.root
        dump(tmp_path / "ci-audit.json", {"records": [{"kind": "group", "agent": "opencode", "model": "qwen"}] + [
            {"trial_id": f"r{i}", "profile": "zvec-grep", "status": "measurement_failure" if i == 2 else "completed",
             "metrics": {"zg_tool_calls_attempted": 1, "zg_tool_calls_successful": 1}} for i in range(1, 6)]})
        r = analyze(tmp_path)
        g = r["groups"][0]
        assert g["adoption_summary"]["adopted_trials"] == 5
        assert g["trials"][1]["execution_status"] == "measurement_failure"
        assert all(x["first_zg_call"] is None for x in g["trials"])
        assert g["first_main_query_text_consistency"]["observed_trials"] == 0
        assert r["query_catalog"] == []


    def test_ambiguous_equal_outputs_not_arbitrarily_paired(self):
        tmp_path = self.root
        experiment(tmp_path, [("r", "zvec-grep", "completed")])
        events(tmp_path / "r/agent/opencode.txt", [oc("step_start", "m"), tool("m", "z", {"query": "same"}), oc("step_finish", "m", reason="stop")])
        events(tmp_path / "r/agent/zg-trace.jsonl", [side("same", "visible"), side("same", "visible")])
        first = analyze(tmp_path)["groups"][0]["trials"][0]["first_zg_call"]
        assert first["backend"] is None
        assert first["backend_link"]["status"] == "ambiguous"


    def test_exact_parameter_frequency_distinguishes_omission_and_explicit_default(self):
        tmp_path = self.root
        experiment(tmp_path, [("a", "zvec-grep", "completed"), ("b", "zvec-grep", "completed")])
        for tid, args in [("a", {"query": "same"}), ("b", {"query": "same", "limit": 10})]:
            events(tmp_path / tid / "agent/opencode.txt", [oc("step_start", "m"), tool("m", "z", args), oc("step_finish", "m", reason="stop")])
        r = analyze(tmp_path)
        g = r["groups"][0]
        assert g["first_main_query_text_consistency"]["modal_count"] == 2
        assert g["first_raw_arguments_consistency"]["unique_values"] == 2
        assert r["query_catalog"][0]["occurrence_count"] == 2
        assert r["query_catalog"][0]["trial_count"] == 2


    def test_wire_initial_request_excludes_title_and_verifies_body(self):
        tmp_path = self.root
        experiment(tmp_path, [("r", "zvec-grep", "completed")])
        agent = tmp_path / "r/agent"
        body = {"model": "qwen", "temperature": 0, "top_p": 1, "messages": [{"content": "question"}],
                "tools": [{"type": "function", "function": {"name": ZG}}]}
        digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        schema_digest = hashlib.sha256(json.dumps(body["tools"], sort_keys=True).encode()).hexdigest()
        dump(agent / "wire-requests/request-002.json", body)
        events(agent / "wire.jsonl", [{"event": "request", "request_id": 1, "tool_names": [], "temperature": .5},
            {"event": "request", "request_id": 2, "model": "qwen", "temperature": 0, "top_p": 1,
             "tool_names": [ZG], "request_sha256": digest, "schema_sha256": schema_digest}])
        identity = analyze(tmp_path)["groups"][0]["trials"][0]["initial_request_identity"]
        assert identity["request_id"] == 2
        assert identity["temperature"] == 0 and identity["parameter_presence"]["top_p"]
        assert identity["request_sha256_verified"] is True
        assert identity["schema_sha256_verified"] is True
        assert identity["source"]["line"] == 2


    def test_cli_derived_files_and_source_protection(self):
        tmp_path = self.root
        experiment(tmp_path, [("r", "zvec-grep", "planned")])
        before = (tmp_path / "plan.json").read_bytes()
        output = tmp_path / "derived.json"
        main(["analyze", "--runs-dir", str(tmp_path), "--output", str(output)])
        report = json.loads(output.read_text())
        assert report["scope"]["query_replay_performed"] is False
        assert output.with_suffix(".md").exists()
        assert (tmp_path / "plan.json").read_bytes() == before
        with self.assertRaisesRegex(ValueError, "input artifact"):
            write_report(report, tmp_path / "plan.json")


    def test_parse_gaps_and_later_unfinished_turn_never_prove_zero(self):
        experiment(self.root, [("broken", "zvec-grep", "failed"), ("unfinished", "zvec-grep", "timeout")])
        events(self.root / "broken/agent/opencode.txt", [oc("step_start", "m"), oc("step_finish", "m", reason="stop")])
        with (self.root / "broken/agent/opencode.txt").open("a") as handle:
            handle.write("malformed event\n")
        events(self.root / "unfinished/agent/opencode.txt", [oc("step_start", "m"), oc("step_finish", "m", reason="stop"), oc("step_start", "m2")])
        trials = analyze(self.root)["groups"][0]["trials"]
        assert all(t["zg_adoption_observed"] is None for t in trials)
        assert all(t["zg_tool_calls_attempted"] is None for t in trials)

    def test_full_artifact_preferred_to_subset_and_group_isolation(self):
        for group in ("a", "b"):
            directory = self.root / group
            experiment(directory, [("same-trial-id", "zvec-grep", "completed")])
            events(directory / "same-trial-id/agent/opencode.txt", [oc("step_start", "m"), tool("m", "z", {"query": "shared"}), oc("step_finish", "m", reason="stop")])
            dump(directory / "ci-audit.json", {"records": []})
        result = analyze(self.root)
        assert len(result["groups"]) == 2
        assert all(g["source_kind"] == "native_artifact" for g in result["groups"])
        assert result["query_catalog"][0]["trial_count"] == 2
        assert {x["group"] for x in result["query_catalog"][0]["occurrences"]} == {"a", "b"}


if __name__ == "__main__":
    unittest.main()
