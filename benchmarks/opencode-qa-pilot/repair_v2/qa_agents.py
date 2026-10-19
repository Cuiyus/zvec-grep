"""Versioned evidence transport; model execution is unchanged upstream main."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import time

from qa_agents import INVENTORY_SCRIPT as _V1_INVENTORY_SCRIPT, compare_inventory
from zg_bench.agents.opencode import ResilientOpenCode
from zg_bench.agents.zvec_opencode import ZvecOpenCode
from .evidence import capture_json, download_json, safe_identifier


_RUNTIME_PAYLOAD_SCRIPT = r'''
if 'zg' in runtime:
    package_root=pathlib.Path(runtime['zg']['binary_path']).parent.parent
    payloads={}
    for relative in ('bin/zg','bin/libzvec_c_api.so','bin/data/jieba_dict/hmm_model.utf8','bin/data/jieba_dict/jieba.dict.utf8'):
        payload=package_root/relative
        if payload.is_symlink() or not payload.is_file() or not payload.resolve().is_relative_to(package_root):
            raise RuntimeError('installed_runtime_payload_not_regular:'+relative)
        payloads[relative]={'bytes':payload.stat().st_size,'sha256':hashlib.sha256(payload.read_bytes()).hexdigest(),
            'mode':format(payload.stat().st_mode & 0o777,'04o')}
    runtime['zg']['package_root']=str(package_root)
    runtime['zg']['payloads']=payloads
'''
# Reuse the frozen source/isolation inventory verbatim, inserting only the
# explicit non-secret installed runtime payload attestation before its output.
INVENTORY_SCRIPT = _V1_INVENTORY_SCRIPT.replace(
    "print(json.dumps(", _RUNTIME_PAYLOAD_SCRIPT + "\nprint(json.dumps(", 1)


def snapshot_tree(snapshot: list) -> tuple[str, dict]:
    """Require exactly one complete fresh session tree and exact counted IDs."""
    if not isinstance(snapshot, list):
        raise ValueError("invalid_usage_snapshot")
    rows = {"session": {}, "message": {}, "tool": {}}
    for row in snapshot:
        kind, data = row["kind"], row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        if kind not in rows or not isinstance(data, dict):
            raise ValueError("invalid_usage_snapshot_row")
        identifier = safe_identifier(data["id"])
        if identifier in rows[kind]:
            raise ValueError("duplicate_usage_snapshot_id")
        rows[kind][identifier] = data
    sessions = rows["session"]
    roots = [sid for sid, row in sessions.items() if row.get("parent_id") is None]
    if len(roots) != 1:
        raise ValueError("fresh_trial_requires_exactly_one_root_session")
    selected = {roots[0]}
    while True:
        expanded = selected | {sid for sid, row in sessions.items()
                               if row.get("parent_id") in selected}
        if expanded == selected:
            break
        selected = expanded
    if selected != set(sessions):
        raise ValueError("orphan_or_cyclic_session_tree")
    for message in rows["message"].values():
        if message.get("session_id") not in selected:
            raise ValueError("message_outside_session_tree")
    for tool in rows["tool"].values():
        owner = tool.get("session_id")
        message = rows["message"].get(tool.get("message_id"), {})
        if owner not in selected or message.get("session_id") != owner:
            raise ValueError("tool_message_identity_mismatch")
        child = tool.get("child_session_id")
        if child and (child not in selected or sessions[child].get("parent_id") != owner):
            raise ValueError("missing_or_mismatched_task_child")
        if tool.get("name") == "task" and tool.get("status") == "completed" and not child:
            raise ValueError("missing_task_child_reference")
    return roots[0], rows


def validate_export(data: dict, session_id: str, rows: dict) -> None:
    """Compare native export identities against the independently saved DB rows."""
    session = rows["session"][session_id]
    info = data.get("info", {})
    if info.get("id") != session_id or info.get("parentID") != session.get("parent_id"):
        raise ValueError("session_export_identity_mismatch")
    messages = data.get("messages")
    if not isinstance(messages, list):
        raise ValueError("session_export_missing_messages")
    assistant_ids, message_ids, parts, tools = set(), set(), set(), {}
    for message in messages:
        info = message.get("info", {})
        mid = safe_identifier(info.get("id"))
        if info.get("sessionID") != session_id or mid in message_ids:
            raise ValueError("export_message_identity_mismatch")
        message_ids.add(mid)
        if info.get("role") == "assistant":
            assistant_ids.add(mid)
        if not isinstance(message.get("parts"), list):
            raise ValueError("export_missing_message_parts")
        for part in message["parts"]:
            pid = safe_identifier(part.get("id"))
            if (pid in parts or part.get("messageID") != mid
                    or part.get("sessionID") != session_id):
                raise ValueError("export_part_identity_mismatch")
            parts.add(pid)
            if part.get("type") == "tool":
                tools[pid] = part
    counted_messages = {mid for mid, message in rows["message"].items()
                        if message.get("session_id") == session_id}
    counted_tools = {tid: tool for tid, tool in rows["tool"].items()
                     if tool.get("session_id") == session_id}
    if assistant_ids != counted_messages or set(tools) != set(counted_tools):
        raise ValueError("export_counted_message_or_tool_ids_mismatch")
    for tid, counted in counted_tools.items():
        part = tools[tid]
        state = part.get("state", {})
        if (part.get("messageID") != counted.get("message_id")
                or part.get("callID") != counted.get("call_id")
                or part.get("tool") != counted.get("name")
                or state.get("status") != counted.get("status")
                or state.get("metadata", {}).get("sessionId") != counted.get("child_session_id")):
            raise ValueError("export_tool_identity_or_state_mismatch")


class IntegrityAuditMixin:
    """Keep v1 collector schema; retain additional transport evidence separately."""

    def __init__(self, *args, qa_source_manifest: str, qa_profile: str, **kwargs):
        if qa_profile not in ("baseline", "zvec-grep"):
            raise ValueError("invalid QA profile")
        self._qa_manifest = json.loads(Path(qa_source_manifest).read_text())
        self._qa_profile = qa_profile
        self._qa_audit = {"schema_version": 1, "profile": qa_profile, "valid": False,
                          "before": None, "after": None, "errors": [],
                          "audit_wall_seconds": 0.0, "export_wall_seconds": 0.0,
                          "session_exports_complete": False,
                          "evidence_transport": "file-download-v2"}
        super().__init__(*args, **kwargs)

    @property
    def _qa_evidence_dir(self):
        return self.logs_dir.parent / "qa-evidence-v2"

    def _save_qa_audit(self):
        path = self.logs_dir.parent / "qa-source-audit.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._qa_audit, indent=2, sort_keys=True) + "\n")

    async def _audit_source(self, environment, phase: str):
        started = time.monotonic()
        try:
            captured = await capture_json(
                environment, self._qa_evidence_dir, "source-" + phase,
                "set -e; . ~/.nvm/nvm.sh; python3 -c " + shlex.quote(INVENTORY_SCRIPT), 120)
            return compare_inventory(captured.data, self._qa_manifest["files"], self._qa_profile)
        finally:
            self._qa_audit["audit_wall_seconds"] += time.monotonic() - started

    async def _export_sessions(self, environment):
        captured = await download_json(environment, self._qa_evidence_dir, "usage-snapshot",
                                       "/logs/agent/opencode-session-snapshot.json")
        root, rows = snapshot_tree(captured.data)
        self._qa_audit["exported_root_session_id"] = root
        self._qa_audit["expected_sessions"] = sorted(rows["session"])
        self._qa_audit["exported_sessions"] = []
        exports = self.logs_dir.parent / "qa-session-exports"
        exports.mkdir(exist_ok=False)
        errors = []
        # Retain every available session even if one child's export is missing.
        for session_id in sorted(rows["session"]):
            validation = {"session_id": session_id, "valid": False}
            try:
                evidence = await capture_json(
                    environment, self._qa_evidence_dir, "export-" + session_id,
                    "set -e; . ~/.nvm/nvm.sh; opencode export " + shlex.quote(session_id), 60)
                validate_export(evidence.data, session_id, rows)
                validation["valid"] = True
                (exports / (session_id + ".json")).write_bytes(evidence.path.read_bytes())
                self._qa_audit["exported_sessions"].append(session_id)
            except Exception as exc:
                validation["error_type"] = type(exc).__name__
                validation["error_code"] = str(exc)
                errors.append("session_export:" + session_id + ":" + type(exc).__name__)
            finally:
                directory = self._qa_evidence_dir / ("export-" + session_id)
                if directory.is_dir():
                    (directory / "identity-validation.json").write_text(
                        json.dumps(validation, indent=2, sort_keys=True) + "\n")
        self._qa_audit["errors"].extend(errors)
        self._qa_audit["session_exports_complete"] = not errors
        if not self._usage_capture_complete:
            self._qa_audit["errors"].append("native_usage_capture_incomplete")

    async def run(self, instruction, environment, context):
        try:
            self._qa_audit["before"] = await self._audit_source(environment, "before")
            if not self._qa_audit["before"]["valid"]:
                raise RuntimeError("pre_agent_source_or_isolation_audit_failed")
            await super().run(instruction, environment, context)
        finally:
            try:
                self._qa_audit["after"] = await self._audit_source(environment, "after")
            except Exception as exc:
                self._qa_audit["errors"].append("source_after:" + type(exc).__name__)
            started = time.monotonic()
            try:
                await self._export_sessions(environment)
            except Exception as exc:
                self._qa_audit["errors"].append("session_export:" + type(exc).__name__)
            finally:
                self._qa_audit["export_wall_seconds"] = time.monotonic() - started
                self._qa_audit["valid"] = (
                    not self._qa_audit["errors"]
                    and self._qa_audit["session_exports_complete"]
                    and all(isinstance(self._qa_audit[k], dict)
                            and self._qa_audit[k].get("valid") is True for k in ("before", "after")))
                self._save_qa_audit()


class QABaseline(IntegrityAuditMixin, ResilientOpenCode):
    pass


class QAZvec(IntegrityAuditMixin, ZvecOpenCode):
    pass
