from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zg_bench.swe_qa import qoder_execution_correction as correction
from zg_bench.swe_qa.official_install_benchmark import summarize
from zg_bench.swe_qa.quality_review import CRITERIA, MODELS, consensus


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read(path):
    return json.loads(path.read_bytes())


class Fixture:
    """Small complete native traces, not mocks of the execution verification."""
    def __init__(self, base):
        self.base = Path(base)
        self.root = self.base / "recorded" / correction.GROUP
        self.case_path = self.base / "case.json"
        self.audit_path = self.base / "independent-qoder-config-audit.json"
        self.output = self.base / "derived"
        self.case = {"case_id": "fixture", "question": "How does computation work?",
                     "repo": {"url": "https://example.test/source", "commit": "a" * 40}}
        write(self.case_path, self.case)
        self.ci = {"GITHUB_RUN_ID": "42", "GITHUB_SHA": "b" * 40}
        write(self.root / "manifest.json", {"protocol": correction.E2E_PROTOCOL, "group": correction.GROUP,
            "package": "@zvec/zvec-grep@0.2.2", "source": self.case["repo"],
            "case_sha256": correction.digest(self.case_path.read_bytes()), "ci_identity": self.ci,
            "agent": {"name": "qodercli", "version": "1.1.45"}})
        self.plan = {"case_id": "fixture", "group": correction.GROUP, "protocol": correction.E2E_PROTOCOL, "trials": []}
        self.qualities = []
        self.calibration = {m: {"status": "passed"} for m in MODELS}
        for profile in ("baseline", "zvec-grep"):
            for repetition in range(1, 6):
                self.trial(profile, repetition)
        write(self.root / "plan.json", self.plan)
        self.quality = {"case_id": "fixture", "plan_sha256": correction.digest((self.root / "plan.json").read_bytes()),
            "trials": self.qualities, "calibration": self.calibration, "summary": {},
            "quality_gate": {"all_planned_answers_pass": False}}
        write(self.root / "quality-review.json", self.quality)
        self.refresh()

    def trial(self, profile, repetition):
        tid = f"{profile}-r{repetition:02d}"
        planned = {"trial_id": tid, "profile": profile, "repetition": repetition, "status": "failed",
                   "trajectory_path": f"{tid}/agent/trajectory.json"}
        self.plan["trials"].append(planned)
        folder = self.root / tid
        agent = folder / "agent"
        base = correction._base_config()
        installed = copy.deepcopy(base)
        if profile == "zvec-grep":
            installed["mcpServers"] = {"zvec_grep": {"command": "zg", "args": ["server", "--stdio"],
                "timeout": 600000, "trust": True, "description": "Official search",
                "alwaysAllow": ["zvec_grep_search", "zvec_grep_rg"]}}
            installed["permissions"]["allow"] += ["mcp__zvec_grep__zvec_grep_search", "mcp__zvec_grep__zvec_grep_rg"]
            (agent).mkdir(parents=True)
            (agent / "AGENTS-installed.md").write_text("Official guidance\n")
            (agent / "version-zg.stdout.txt").write_text("0.2.2\n")
        write(agent / "agent-config-before.json", base)
        write(agent / "agent-config-installed.json", installed)
        expected_final = {**installed, "securityScan": {"l1StaticCheck": True, "l2LightweightScan": True, "l3DeepScan": True}}
        final_hash = correction.digest(json.dumps(expected_final, ensure_ascii=False, indent=2).encode())
        text = correction.instruction(self.case)
        write(agent / "session-spec.json", {"agent": "qodercli", "model": "qwen3.8-max", "profile": profile,
            "root": "/app", "log_dir": "/logs", "instruction": text, "base_url": None, "limits": correction.LIMITS})
        write(agent / "native-session-spec.json", {"command": ["qodercli", "--print", "--output-format", "stream-json",
            "--no-session-persistence", "--permission-mode", "dont_ask", "--tools", "Read,Grep,Glob", "--disallowed-tools",
            ",".join(correction.DENIED), "--max-model-request-retries", "0", "--max-turns", "30", "--model", "Qwen3.8-Max", "--", text],
            "limits": correction.LIMITS, "config_path": "/home/qa/.qoder/settings.json", "native_name": "qodercli-stream.jsonl", "tap_upstream": None})
        (agent / "version-qodercli.stdout.txt").write_text("1.1.45\n")
        answer = "The repository computes the value statically."
        recovered_error = profile == "baseline" and repetition == 1
        content = [{"type": "tool_use", "id": "c1", "name": "Read", "input": {"file_path": "/app/x.py"}}]
        assistant = {"type": "assistant", "session_id": "s", "message": {"id": "m1", "model": "Qwen3.8-Max",
            "content": content, "usage": {"input_tokens": 100, "output_tokens": 2, "cache_read_input_tokens": 40}}}
        tools = ["Read", "Grep", "Glob"] + (["mcp__zvec_grep__zvec_grep_search"] if profile == "zvec-grep" else [])
        events = [{"type": "system", "subtype": "init", "session_id": "s", "tools": tools, "permissionMode": "dontAsk",
            "mcp_servers": [{"name": "zvec_grep", "status": "connected"}] if profile == "zvec-grep" else []},
            assistant, copy.deepcopy(assistant),  # repeated native snapshots must count once
            {"type": "user", "session_id": "s", "message": {"content": [{"type": "tool_result", "tool_use_id": "c1",
             "content": "missing file" if recovered_error else "source", "is_error": recovered_error}]}},
            {"type": "assistant", "session_id": "s", "message": {"id": "m2", "model": "Qwen3.8-Max", "content": [{"type": "text", "text": answer}],
             "usage": {"input_tokens": 110, "output_tokens": 5, "cache_read_input_tokens": 50}}},
            {"type": "result", "session_id": "s", "subtype": "success", "is_error": False, "result": answer, "num_turns": 2,
             "usage": {"input_tokens": 210}, "modelUsage": {"qmodel_38max": {"inputTokens": 210}}}]
        (agent / "qodercli-stream.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        write(agent / "trajectory.json", {"steps": [{"source": "agent", "message": answer}]})
        session = {"status": "completed", "returncode": 0, "limit_reason": None, "limits": correction.LIMITS,
            "wall_seconds": 3, "observed": {"input_tokens": 210, "tool_calls": 1, "model_requests": 2,
            "input_usage_missing_turns": 0, "invalid_usage_events": 0}}
        install = {"status": "failed", "error": correction.ERROR, "error_type": "ValueError", "agent_config_unchanged": False,
            "guidance_unchanged": True, "installation_verified": True, "agent_model_calls_started": True, "prepare_only": False,
            "agent": "qodercli", "model": "qwen3.8-max", "prompt_variant": "P00", "instruction_sha256": correction.digest(text),
            "versions": {"qodercli": "1.1.45", **({"zg": "0.2.2"} if profile == "zvec-grep" else {})},
            "installed_agent_config_sha256": correction.digest((agent / "agent-config-installed.json").read_bytes()),
            "final_agent_config_sha256": final_hash, "install_command": None, "native_mcp_command": None, "new_index_builds": 0}
        if profile == "zvec-grep":
            install.update(installed_guidance_sha256=correction.digest((agent / "AGENTS-installed.md").read_bytes()),
                install_command=["zg", "install", "--target", "qoder", "--yes"], native_mcp_command=["zg", "server", "--stdio"],
                new_index_builds=1, index_build={"status": "completed"})
        write(agent / "session.json", session)
        write(agent / "install-manifest.json", install)
        write(agent / "official-failure.json", {"error": correction.ERROR, "error_type": "ValueError"})
        write(folder / "result.json", {"status": "failed", "returncode": 5, "trial_id": tid, "profile": profile,
            "session": session, "installation": install, "source_unchanged": True, "usage_complete": True,
            "input_tokens": 210, "tool_calls": 1, "conversion": {"error_event_count": 0, "contract_error_count": 0,
            "has_final_answer": True, "parse": {"invalid_json_lines": [], "last_line_incomplete": False},
            "model_identity": {"valid": True}, "tool_contract": {"valid": True}, "tool_error_count": int(recovered_error)}})
        disagreement = profile == "zvec-grep" and repetition == 2
        votes = {m: {"status": "judged", "quality": "fail" if disagreement and i else "pass",
            "assessment": {c: {"score": 0 if disagreement and i else 2} for c in CRITERIA}} for i, m in enumerate(MODELS)}
        self.qualities.append({"trial_id": tid, "profile": profile, "execution_status": "failed", "judgments": votes,
            "answer": answer, "answer_sha256": correction.digest(answer),
            "trajectory_sha256": correction.digest((agent / "trajectory.json").read_bytes()),
            **consensus(votes, self.calibration, "failed")})

    def refresh(self):
        """Refresh the independent inventory so mutations test inner invariants."""
        plan, quality = read(self.root / "plan.json"), read(self.root / "quality-review.json")
        write(self.root / "official-report.json", summarize(plan, [read(self.root / t["trial_id"] / "result.json") for t in plan["trials"]], quality))
        inventory = {str(p.relative_to(self.base)): correction.digest(p.read_bytes()) for p in self.root.rglob("*") if p.is_file()}
        write(self.audit_path, {"audit_status": "passed", "ci_run_id": "42", "group": correction.GROUP,
            "source_bundle": {"package_version": "1.1.45", "sha256": correction.BUNDLE_SHA256},
            "verified_config_semantic_delta": {"added": {"securityScan": correction.DEFAULTS}, "removed": [], "changed_existing_fields": []},
            "original_artifacts_inventory": inventory})

    def derive(self, **kwargs):
        return correction.derive(runs_dir=self.root, config_audit=self.audit_path, case_path=self.case_path, output=self.output, **kwargs)

    def mutate(self, relative, field, value, *, sync_result=False):
        path = self.root / relative
        doc = read(path); doc[field] = value; write(path, doc)
        if sync_result:
            result_path = path.parent.parent / "result.json"
            result = read(result_path)
            result["session" if path.name == "session.json" else "installation"] = doc
            write(result_path, result)
        self.refresh()


class QoderExecutionCorrectionTests(unittest.TestCase):
    def test_all_ten_offline_native_dedup_and_original_votes_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            before = read(f.audit_path)["original_artifacts_inventory"]
            with patch("subprocess.run", side_effect=AssertionError("No subprocess allowed")), patch("socket.socket", side_effect=AssertionError("No network allowed")):
                result = f.derive()
            quality = read(f.output / "derived-quality-review.json")
            report = read(f.output / "derived-official-report.json")
            self.assertEqual(len(result["corrected_trials"]), 10)
            self.assertFalse(result["ci_success_asserted"])
            self.assertEqual(quality["calibration"], f.calibration)
            self.assertEqual([q["judgments"] for q in quality["trials"]], [q["judgments"] for q in f.qualities])
            self.assertEqual(quality["summary"]["baseline"]["pass"], 5)
            self.assertEqual(quality["summary"]["zvec-grep"]["pass"], 4)
            self.assertEqual(quality["summary"]["zvec-grep"]["disagreement"], 1)
            self.assertTrue(all(t["returncode"] == 5 and t["session"]["returncode"] == 0 for t in report["trials"]))
            self.assertEqual(result["corrected_trials"][0]["native_tool_errors_retained"], 1)
            self.assertEqual(report["groups"]["baseline"]["metrics"]["input_tokens"]["mean"], 210)
            self.assertEqual(before, {str(p.relative_to(f.base)): correction.digest(p.read_bytes()) for p in f.root.rglob("*") if p.is_file()})
            with self.assertRaisesRegex(ValueError, "new and outside"):
                f.derive()

    def test_fail_closed_for_other_failure_migration_or_controls(self):
        mutations = [
            ("baseline-r01/agent/install-manifest.json", "final_agent_config_sha256", "0" * 64, True, "unproven changes"),
            ("baseline-r01/agent/official-failure.json", "error", "Other exception", False, "different failure"),
            ("baseline-r01/agent/install-manifest.json", "guidance_unchanged", False, True, "changed guidance"),
            ("baseline-r01/agent/session.json", "status", "budget_exhausted", True, "native session incomplete"),
            ("baseline-r01/result.json", "source_unchanged", False, False, "source, usage"),
            ("baseline-r01/result.json", "input_tokens", None, False, "native measurement differs"),
            ("baseline-r01/agent/agent-config-before.json", "disableAllHooks", False, False, "controls differ"),
            ("baseline-r01/agent/agent-config-installed.json", "disableAllHooks", False, False, "other controls"),
        ]
        for path, field, value, sync, message in mutations:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                f = Fixture(directory); f.mutate(path, field, value, sync_result=sync)
                with self.assertRaisesRegex(ValueError, message): f.derive()
                self.assertFalse(f.output.exists())

    def test_native_error_and_wrong_answer_are_rejected_even_if_conversion_claims_clean(self):
        for mode in ("error", "answer"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                f = Fixture(directory)
                trace = f.root / "zvec-grep-r05/agent/qodercli-stream.jsonl"
                events = [json.loads(s) for s in trace.read_text().splitlines()]
                if mode == "error": events.insert(1, {"type": "error", "error": "backend failure"})
                else: events[-1]["result"] = "Another answer"
                trace.write_text("\n".join(json.dumps(e) for e in events) + "\n")
                f.refresh()
                with self.assertRaisesRegex(ValueError, "native error|votes belong to another answer"): f.derive()
                self.assertFalse(f.output.exists(), "The first nine valid trials must not create partial corrections")

    def test_stale_inventory_and_missing_trial_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            (f.root / "baseline-r01/agent/version-qodercli.stdout.txt").write_text("1.1.46\n")
            with self.assertRaisesRegex(ValueError, "Raw artifact changed"): f.derive()
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            plan = read(f.root / "plan.json"); plan["trials"].pop(); write(f.root / "plan.json", plan)
            with self.assertRaisesRegex(ValueError, "all ten paired"): f.derive()

    def test_original_consensus_must_reproduce_including_disagreement(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            quality = read(f.root / "quality-review.json")
            quality["trials"][6]["judgments"][MODELS[1]]["quality"] = "pass"
            write(f.root / "quality-review.json", quality); f.refresh()
            with self.assertRaisesRegex(ValueError, "original consensus cannot be reproduced"): f.derive()
            self.assertFalse(f.output.exists())

    def test_joint_derivation_preserves_native_retrieval_and_raw_status(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            raw = read(f.root / "official-report.json")
            joint = {"e2e_ci_identity": f.ci, "status": "partial", "e2e_groups": {correction.GROUP: raw, "other": {"unchanged": True}},
                "retrieval_scores": {"actual": [{"rank": 4}], "replay": [{"hash": "abc"}]}, "joined_trials": [
                {"group": correction.GROUP, "trial_id": t["trial_id"], "e2e_result": t, "quality": q,
                 "behavior": {"execution_status": "failed"}, "observed_queries": [{"score": 0.25}]} for t, q in zip(raw["trials"], raw["quality"]["trials"])]}
            path = f.base / "joint.json"; write(path, joint)
            f.derive(joint_report=path)
            derived = read(f.output / "derived-joint-report.json")
            self.assertEqual(read(path), joint)
            self.assertEqual(derived["status"], "partial")
            self.assertEqual(derived["retrieval_scores"], joint["retrieval_scores"])
            self.assertEqual(derived["e2e_groups"]["other"], joint["e2e_groups"]["other"])
            for old, new in zip(joint["joined_trials"], derived["joined_trials"]):
                self.assertEqual(old["observed_queries"], new["observed_queries"])
                self.assertEqual(new["behavior"]["execution_status"], "failed")
                self.assertEqual(new["raw_e2e_result"], old["e2e_result"])
                self.assertEqual(new["derived_execution_status"], "completed")
            with self.assertRaisesRegex(ValueError, "already been corrected"):
                correction.apply_joint(derived, read(f.output / "execution-corrections.json"), read(f.output / "derived-official-report.json"), raw)

    def test_migration_requires_exact_bytes_and_no_prior_security_defaults(self):
        value = correction.reconstructed_settings({"unicode": "值"})
        self.assertFalse(value.endswith(b"\n"))
        self.assertIn("值".encode(), value)
        with self.assertRaisesRegex(ValueError, "Pre-existing"):
            correction.reconstructed_settings({"securityScan": correction.DEFAULTS})


if __name__ == "__main__":
    unittest.main()
