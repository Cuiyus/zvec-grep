"""Offline, narrowly evidenced correction of Qoder 1.1.45 settings false alarms.

No original artifact, model judgment, retrieval score, or CI status is changed.
All ten trials must pass the allow rule before a separate derived view is made.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .first_query_analysis import _Sources, _native
from .official_install_benchmark import LIMITS, PROTOCOL as E2E_PROTOCOL, instruction, summarize
from .quality_review import DECISIONS, consensus
from .readonly_agents import _qoder_identity, agent_spec, qoder_contract
from .readonly_judge import extract_final_answer

PROTOCOL = "qoder-execution-correction-v1"
GROUP = "qoder-qwen38max"
ERROR = "Agent changed installed configuration or guidance"
DEFAULTS = {"l1StaticCheck": True, "l2LightweightScan": True, "l3DeepScan": True}
BUNDLE_SHA256 = "86565469a3a0dd2dcede554c6056678cb7423f12e1929b4b23b9d6c340559418"
DENIED = ["Bash", "Edit", "Write", "NotebookEdit", "Agent", "Task", "Skill", "WebFetch", "WebSearch", "ImageGen", "ImageSearch", "Workflow"]
ALLOW_REASON = "qoder-1.1.45-securityScan-defaults-exact-final-bytes"


def digest(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def require(condition: Any, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def reconstructed_settings(installed: dict) -> bytes:
    require("securityScan" not in installed, "Pre-existing securityScan is outside the allow rule")
    # 1.1.45 materializes these defaults at the end, preserving existing order.
    # Its JSON formatter writes two-space indentation and no trailing newline.
    return json.dumps({**installed, "securityScan": DEFAULTS}, ensure_ascii=False, indent=2).encode()


class Evidence:
    def __init__(self) -> None:
        self.hashes: dict[str, str] = {}

    def bytes(self, path: Path) -> bytes:
        require(path.is_file() and not path.is_symlink(), f"Missing or linked input: {path}")
        value = path.read_bytes()
        self.hashes[str(path.resolve())] = digest(value)
        return value

    def json(self, path: Path) -> dict:
        value = json.loads(self.bytes(path))
        require(isinstance(value, dict), f"Expected JSON object: {path}")
        return value

    def unchanged(self) -> None:
        for path, expected in self.hashes.items():
            require(digest(Path(path).read_bytes()) == expected, f"Input changed during correction: {path}")


def _base_config() -> dict:
    return {"model": {"name": "Qwen3.8-Max"}, "tools": {"core": ["Read", "Grep", "Glob"], "useRipgrep": True},
        "general": {"defaultPermissionMode": "dont_ask", "enableAutoUpdate": False, "enableAutoUpdateNotification": False},
        "disableAllHooks": True, "permissions": {"allow": ["Read", "Grep", "Glob"], "deny": DENIED},
        "security": {"disableYoloMode": True, "environmentVariableRedaction": {"enabled": True}},
        "mcp": {"lazyLoad": False}, "mcpServers": {}}


def _controls(spec: dict, native_spec: dict, before: dict, installed: dict, profile: str, case: dict) -> None:
    expected = {"agent": "qodercli", "model": "qwen3.8-max", "profile": profile, "root": "/app", "log_dir": "/logs",
                "instruction": instruction(case), "base_url": None, "limits": LIMITS}
    require(spec == expected, "Session spec differs from the frozen official controls")
    command = ["qodercli", "--print", "--output-format", "stream-json", "--no-session-persistence", "--permission-mode",
        "dont_ask", "--tools", "Read,Grep,Glob", "--disallowed-tools", ",".join(DENIED), "--max-model-request-retries",
        "0", "--max-turns", "30", "--model", "Qwen3.8-Max", "--", instruction(case)]
    require(native_spec.get("command") == command and native_spec.get("limits") == LIMITS
            and native_spec.get("config_path") == "/home/qa/.qoder/settings.json"
            and native_spec.get("native_name") == "qodercli-stream.jsonl" and native_spec.get("tap_upstream") is None,
            "Native command/config/budgets differ")
    require(before == _base_config(), "Pre-install controls differ")
    restored = copy.deepcopy(installed)
    if profile == "zvec-grep":
        require(set(restored.get("mcpServers", {})) == {"zvec_grep"}, "Unexpected MCP server")
        server = restored["mcpServers"]["zvec_grep"]
        require(set(server) == {"command", "args", "timeout", "trust", "description", "alwaysAllow"}
                and server["command"] == "zg" and server["args"] == ["server", "--stdio"]
                and server["timeout"] == 600000 and server["trust"] is True
                and set(server["alwaysAllow"]) == {"zvec_grep_search", "zvec_grep_rg"}, "Official MCP fields differ")
        restored["mcpServers"] = {}
        rules = ["mcp__zvec_grep__zvec_grep_search", "mcp__zvec_grep__zvec_grep_rg"]
        require(set(restored["permissions"]["allow"]) == set(before["permissions"]["allow"]) | set(rules), "Unexpected tool permissions")
        restored["permissions"]["allow"] = [v for v in restored["permissions"]["allow"] if v not in rules]
    require(restored == before, "Installer changed other controls")


def _trial(root: Path, planned: dict, quality: dict, case: dict, ev: Evidence) -> tuple[dict, dict, bytes]:
    tid, profile = planned["trial_id"], planned["profile"]
    require(Path(tid).name == tid and planned.get("trajectory_path") == f"{tid}/agent/trajectory.json", "Unsafe or unexpected trial path")
    folder = root / tid; agent = folder / "agent"
    result = ev.json(folder / "result.json"); session = ev.json(agent / "session.json")
    install = ev.json(agent / "install-manifest.json"); failure = ev.json(agent / "official-failure.json")
    trajectory_bytes = ev.bytes(agent / "trajectory.json"); trajectory = json.loads(trajectory_bytes)
    installed_bytes = ev.bytes(agent / "agent-config-installed.json"); installed = json.loads(installed_bytes)
    _controls(ev.json(agent / "session-spec.json"), ev.json(agent / "native-session-spec.json"),
              ev.json(agent / "agent-config-before.json"), installed, profile, case)
    require(planned["status"] == result.get("status") == install.get("status") == "failed"
            and result.get("returncode") == 5 and result.get("trial_id") == tid and result.get("profile") == profile,
            f"{tid}: not the known exit-5 wrapper failure")
    require(failure == {"error": ERROR, "error_type": "ValueError"}
            and install.get("error") == ERROR and install.get("error_type") == "ValueError"
            and install.get("agent_config_unchanged") is False and install.get("guidance_unchanged") is True,
            f"{tid}: different failure or changed guidance")
    require(result.get("session") == session and result.get("installation") == install, f"{tid}: result snapshots disagree")
    require(session.get("status") == "completed" and session.get("returncode") == 0 and session.get("limit_reason") is None
            and session.get("limits") == LIMITS and session.get("wall_seconds", float("inf")) <= LIMITS["wall_seconds"],
            f"{tid}: native session incomplete or exceeded limits")
    require(result.get("source_unchanged") is True and result.get("usage_complete") is True
            and install.get("installation_verified") is True and install.get("agent_model_calls_started") is True
            and install.get("prepare_only") is False and install.get("agent") == "qodercli"
            and install.get("model") == "qwen3.8-max" and install.get("prompt_variant") == "P00"
            and install.get("instruction_sha256") == digest(instruction(case)), f"{tid}: source, usage or install controls unverified")
    expected_versions = {"qodercli": "1.1.45", **({"zg": "0.2.2"} if profile == "zvec-grep" else {})}
    require(install.get("versions") == expected_versions and ev.bytes(agent / "version-qodercli.stdout.txt").decode().strip() == "1.1.45",
            f"{tid}: wrong agent version")
    require(digest(installed_bytes) == install.get("installed_agent_config_sha256"), f"{tid}: installed config hash differs")
    reconstructed = reconstructed_settings(installed)
    require(digest(reconstructed) == install.get("final_agent_config_sha256"), f"{tid}: final config has unproven changes")
    if profile == "zvec-grep":
        guide = ev.bytes(agent / "AGENTS-installed.md")
        require(digest(guide) == install.get("installed_guidance_sha256")
                and install.get("native_mcp_command") == ["zg", "server", "--stdio"]
                and install.get("install_command") == ["zg", "install", "--target", "qoder", "--yes"]
                and install.get("new_index_builds") == 1 and install.get("index_build", {}).get("status") == "completed"
                and ev.bytes(agent / "version-zg.stdout.txt").decode().strip() == "0.2.2", f"{tid}: zg installation not verified")
    else:
        require(install.get("install_command") is None and install.get("native_mcp_command") is None
                and install.get("new_index_builds") == 0 and not (agent / "AGENTS-installed.md").exists(), f"{tid}: baseline contaminated")
    trace = ev.bytes(agent / "qodercli-stream.jsonl").decode()
    events = [json.loads(line) for line in trace.splitlines() if line.strip()]
    require(all(isinstance(e, dict) for e in events), f"{tid}: invalid native event")
    require(not any(e.get("type") == "error" or e.get("parent_tool_use_id") for e in events), f"{tid}: native error or unexpected subagent")
    native = _native(agent / "qodercli-stream.jsonl", _Sources(root))
    require(native["complete_observed_trace"] and not native["parse_diagnostics"], f"{tid}: incomplete parser evidence")
    contract = qoder_contract(events, zg=profile == "zvec-grep")
    identity = _qoder_identity(events, agent_spec("qodercli", "qwen3.8-max"))
    require(contract["valid"] and not contract["unexpected_tool_calls"] and identity["valid"], f"{tid}: native model/tool contract differs")
    conversion = result.get("conversion", {})
    require(conversion.get("error_event_count") == 0 and conversion.get("contract_error_count") == 0
            and conversion.get("has_final_answer") is True and conversion.get("parse") == {"invalid_json_lines": [], "last_line_incomplete": False}
            and conversion.get("model_identity", {}).get("valid") is True and conversion.get("tool_contract", {}).get("valid") is True,
            f"{tid}: original conversion not clean")
    final = [e for e in events if e.get("type") == "result"]
    require(len(final) == 1 and final[0].get("subtype") == "success" and final[0].get("is_error") is False
            and isinstance(final[0].get("result"), str) and final[0]["result"].strip(), f"{tid}: no successful native final answer")
    turns = {}
    for e in events:
        if e.get("type") != "assistant":
            continue
        message = e["message"]; key = (e.get("session_id"), message["id"])
        turns.setdefault(key, None); usage = message.get("usage", {})
        if any(type(usage.get(k)) in (int, float) and usage[k] > 0 for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
            turns[key] = usage
    require(bool(turns) and all(u is not None and type(u.get("input_tokens")) is int and u["input_tokens"] >= 0 for u in turns.values()),
            f"{tid}: missing native input usage")
    count = sum(u["input_tokens"] for u in turns.values()); calls = len(native["calls"])
    observed = session["observed"]
    require(count == result["input_tokens"] == observed["input_tokens"] == final[0]["usage"]["input_tokens"]
            == sum(u["inputTokens"] for u in final[0]["modelUsage"].values())
            and calls == result["tool_calls"] == observed["tool_calls"]
            and len(turns) == observed["model_requests"] == final[0]["num_turns"]
            and observed.get("input_usage_missing_turns") == 0 and observed.get("invalid_usage_events") == 0,
            f"{tid}: native measurement differs")
    require(all(observed[k] <= LIMITS[k] for k in ("model_requests", "tool_calls", "input_tokens")), f"{tid}: native budget overshoot")
    require(all(c["status"] in {"completed", "error"} for c in native["calls"]), f"{tid}: unfinished tool call")
    # Recovered ordinary tool errors remain part of the trial; do not erase them.
    tool_errors = sum(c["status"] == "error" for c in native["calls"])
    require(tool_errors == conversion.get("tool_error_count"), f"{tid}: native tool errors differ")
    answer = extract_final_answer(trajectory)
    require(answer == final[0]["result"] == quality.get("answer") and digest(answer) == quality.get("answer_sha256")
            and digest(trajectory_bytes) == quality.get("trajectory_sha256") and quality.get("execution_status") == "failed"
            and quality.get("profile") == profile, f"{tid}: quality votes belong to another answer")
    proof = {"trial_id": tid, "profile": profile, "allow_reason": ALLOW_REASON, "raw_execution_status": "failed",
             "derived_execution_status": "completed", "raw_wrapper_returncode": 5, "native_returncode": 0,
             "installed_config_sha256": digest(installed_bytes), "reconstructed_final_config_sha256": digest(reconstructed),
             "recorded_final_config_sha256": install["final_agent_config_sha256"], "native_tool_errors_retained": tool_errors,
             "native_input_tokens": count, "native_tool_calls": calls, "answer_sha256": digest(answer)}
    derived_result = {**copy.deepcopy(result), "status": "completed", "raw_execution_status": "failed", "execution_correction": proof}
    return proof, derived_result, reconstructed


def apply_joint(joint: dict, correction: dict, derived_report: dict, raw_report: dict) -> dict:
    """Replace only execution-gated E2E views; retain all native retrieval data."""
    require("execution_correction" not in joint and "raw_e2e_groups" not in joint, "Joint report has already been corrected")
    require(joint.get("e2e_ci_identity") == correction["e2e_ci_identity"], "Joint report refers to another CI cohort")
    require(joint.get("e2e_groups", {}).get(GROUP) == raw_report, "Joint Qoder report differs from verified raw report")
    out = copy.deepcopy(joint); out["raw_e2e_groups"] = {GROUP: copy.deepcopy(raw_report)}
    out["e2e_groups"][GROUP] = copy.deepcopy(derived_report)
    results = {r["trial_id"]: r for r in derived_report["trials"]}
    qualities = {q["trial_id"]: q for q in derived_report["quality"]["trials"]}
    rows = [r for r in out.get("joined_trials", []) if r.get("group") == GROUP]
    require(len(rows) == 10 and {r["trial_id"] for r in rows} == set(results), "Joint report lacks the complete Qoder cohort")
    for row in rows:
        tid = row["trial_id"]
        require(row.get("e2e_result") == next(r for r in raw_report["trials"] if r["trial_id"] == tid), "Joint trial result differs")
        require(row.get("quality") == next(q for q in raw_report["quality"]["trials"] if q["trial_id"] == tid), "Joint quality differs")
        row["raw_e2e_result"] = copy.deepcopy(row["e2e_result"]); row["raw_quality"] = copy.deepcopy(row["quality"])
        row["e2e_result"] = copy.deepcopy(results[tid]); row["quality"] = copy.deepcopy(qualities[tid])
        # behavior.execution_status records the historical wrapper and stays raw.
        row["derived_execution_status"] = "completed"
    out["execution_correction"] = copy.deepcopy(correction)
    return out


def derive(*, runs_dir: Path, config_audit: Path, case_path: Path, output: Path, joint_report: Path | None = None) -> dict:
    root, output = runs_dir.resolve(), output.resolve()
    require(not output.exists() and not output.is_relative_to(root) and not root.is_relative_to(output), "Derived output must be new and outside raw artifacts")
    ev = Evidence(); case = ev.json(case_path); manifest = ev.json(root / "manifest.json"); plan = ev.json(root / "plan.json")
    raw_quality = ev.json(root / "quality-review.json"); raw_report = ev.json(root / "official-report.json")
    audit = ev.json(config_audit)
    require(manifest.get("protocol") == E2E_PROTOCOL and manifest.get("group") == GROUP and manifest.get("package") == "@zvec/zvec-grep@0.2.2"
            and manifest.get("source") == case["repo"] and manifest.get("case_sha256") == digest(ev.bytes(case_path))
            and manifest.get("agent", {}).get("name") == "qodercli" and manifest["agent"].get("version") == "1.1.45", "Wrong official experiment identity")
    require(audit.get("audit_status") == "passed" and audit.get("ci_run_id") == manifest["ci_identity"]["GITHUB_RUN_ID"]
            and audit.get("group") == GROUP and audit.get("source_bundle", {}).get("package_version") == "1.1.45"
            and audit["source_bundle"].get("sha256") == BUNDLE_SHA256
            and audit.get("verified_config_semantic_delta") == {"added": {"securityScan": DEFAULTS}, "removed": [], "changed_existing_fields": []},
            "Independent configuration audit does not establish the known migration")
    require(plan.get("case_id") == case["case_id"] and plan.get("group") == GROUP and plan.get("protocol") == E2E_PROTOCOL, "Plan identity differs")
    trials = plan.get("trials", []); ids = [t["trial_id"] for t in trials]
    require(len(ids) == len(set(ids)) == 10 and Counter(t["profile"] for t in trials) == {"baseline": 5, "zvec-grep": 5}
            and {(t["profile"], t.get("repetition")) for t in trials} == {(p, r) for p in ("baseline", "zvec-grep") for r in range(1, 6)}, "Expected all ten paired planned trials")
    audit_root = config_audit.resolve().parent
    inventory = audit.get("original_artifacts_inventory", {})
    original_files = {str(p.relative_to(audit_root)): p for p in root.rglob("*") if p.is_file()}
    require(set(inventory) == set(original_files), "Raw artifact inventory differs from independent audit")
    for relative, path in original_files.items():
        require(digest(ev.bytes(path)) == inventory[relative], f"Raw artifact changed: {relative}")
    require(raw_quality.get("case_id") == case["case_id"] and raw_quality.get("plan_sha256") == digest(ev.bytes(root / "plan.json"))
            and raw_report.get("quality") == raw_quality, "Original quality report identity differs")
    qualities = {q["trial_id"]: q for q in raw_quality["trials"]}
    require(len(raw_quality["trials"]) == 10 and set(qualities) == set(ids), "Incomplete original quality votes")
    proofs, results, reconstructed = [], [], {}
    derived_quality = copy.deepcopy(raw_quality)
    for t in trials:
        proof, result, data = _trial(root, t, qualities[t["trial_id"]], case, ev)
        proofs.append(proof); results.append(result); reconstructed[t["trial_id"]] = data
    raw_results = [ev.json(root / t["trial_id"] / "result.json") for t in trials]
    require(raw_report == summarize(plan, raw_results, raw_quality), "Original E2E summary does not reproduce from all ten raw trials")
    for q in derived_quality["trials"]:
        old = consensus(q["judgments"], raw_quality["calibration"], "failed")
        require(all(q.get(k) == v for k, v in old.items()), f"{q['trial_id']}: original consensus cannot be reproduced")
        q["raw_execution_status"] = q["execution_status"]; q["raw_effective_quality"] = q["quality"]
        q["execution_status"] = "completed"
        q.update(consensus(q["judgments"], raw_quality["calibration"], "completed"))
    derived_quality["summary"] = {p: {"planned": 5, **{s: sum(q["profile"] == p and q["quality"] == s for q in derived_quality["trials"]) for s in DECISIONS}} for p in ("baseline", "zvec-grep")}
    derived_quality["quality_gate"]["all_planned_answers_pass"] = all(q["quality"] == "pass" for q in derived_quality["trials"])
    correction = {"protocol": PROTOCOL, "kind": "offline_derived_execution_view", "e2e_ci_identity": manifest["ci_identity"],
        "case_id": case["case_id"], "group": GROUP, "allow_reason": ALLOW_REASON, "corrected_trials": proofs,
        "config_semantic_delta": {"securityScan": DEFAULTS}, "input_file_sha256": ev.hashes,
        "judgments_sha256": digest(canonical([q["judgments"] for q in raw_quality["trials"]])),
        "calibration_sha256": digest(canonical(raw_quality["calibration"])), "model_calls": 0, "retrieval_calls": 0,
        "ci_success_asserted": False, "limitations": ["Raw exit codes, failure artifacts, CI outcomes and retrieval observations are unchanged.",
            "Exact reconstructed final bytes match the recorded hash; final files were not captured and transient writes cannot be excluded.",
            "The independent audit identifies the released 1.1.45 bundle; that inspected bundle was not exported from the original CI runtime.",
            "Only the known 1.1.45 securityScan default persistence is allowed, not arbitrary configuration changes.",
            "Recovered tool errors and original judge disagreements remain; no model judgment was rerun.",
            "Qoder system guidance delivery and effective temperature remain unverified by wire evidence."]}
    derived_quality["execution_correction"] = {k: correction[k] for k in ("protocol", "kind", "allow_reason", "ci_success_asserted")}
    derived_plan = copy.deepcopy(plan)
    for t in derived_plan["trials"]: t["status"] = "completed"
    derived_report = summarize(derived_plan, results, derived_quality)
    derived_report["execution_correction"] = derived_quality["execution_correction"]
    derived_joint = apply_joint(ev.json(joint_report), correction, derived_report, raw_report) if joint_report else None
    ev.unchanged()
    output.mkdir(parents=True)
    documents = {"execution-corrections.json": correction, "derived-quality-review.json": derived_quality, "derived-official-report.json": derived_report}
    if derived_joint is not None: documents["derived-joint-report.json"] = derived_joint
    for name, value in documents.items():
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    for tid, data in reconstructed.items():
        path = output / "reconstructed-settings" / f"{tid}.json"; path.parent.mkdir(exist_ok=True); path.write_bytes(data)
    (output / "README.md").write_text("# Qoder 离线执行门槛更正\n\n原始 10 条 wrapper failed 和 exit 5 不变。仅在独立派生视图中，将经过精确配置重建、原生完成和控制验证的 execution gating 设为 completed。\n\n复用原有 judgments 和 calibration 调用 consensus；没有模型、检索或重新判卷。原始分歧保留，未宣称 CI 成功。详细 allow reason、逐项输入哈希和原/派生状态见 execution-corrections.json。\n")
    ev.unchanged()
    return correction


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("runs-dir", "config-audit", "case", "output"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--joint-report", type=Path)
    args = parser.parse_args(argv)
    report = derive(runs_dir=args.runs_dir, config_audit=args.config_audit, case_path=args.case, output=args.output, joint_report=args.joint_report)
    print(json.dumps({"protocol": PROTOCOL, "corrected_trials": len(report["corrected_trials"]), "model_calls": 0, "ci_success_asserted": False}))


if __name__ == "__main__":
    main()
