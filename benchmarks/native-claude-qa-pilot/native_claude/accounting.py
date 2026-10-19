"""Strict accounting of retained Claude Code messages, not provider billing.

Input is one task's Harbor agent log directory: ``claude-code.txt`` and
``sessions/projects/**/*.jsonl``. Never inspect the user's Claude home directory.
The stdout envelope was observed in prior task-generated Claude traces; persisted
message splitting/last-usage behavior follows Harbor 0.18's Claude parser. Child
linkage is deliberately explicit and fail-closed; the native Docker contract must
validate it against fresh task-generated Agent/Task artifacts before live use.

Only terminal per-message usage in retained transcripts is summed. Root result
usage may already include children/background requests and is diagnostic only.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
SCOPE = "claude-persisted-session-tree-v1"


def _hash(value: Any) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(x["text"] for x in content if isinstance(x, dict) and x.get("type") == "text" and isinstance(x.get("text"), str))
    return ""


def _totals(messages: list[dict], tools: list[dict]) -> dict:
    valid = [m for m in messages if m["usage_valid"]]
    sums = {f: sum(m["usage"][f] for m in valid) for f in FIELDS}
    return dict(input_tokens=sums["input_tokens"] + sums["cache_read_input_tokens"] + sums["cache_creation_input_tokens"],
                uncached_input_tokens=sums["input_tokens"], cache_read_tokens=sums["cache_read_input_tokens"],
                cache_write_tokens=sums["cache_creation_input_tokens"], output_tokens=sums["output_tokens"],
                llm_calls=len(messages), tool_calls=len(tools), tool_errors=sum(t["is_error"] for t in tools),
                counted_usage_messages=len(valid), unknown_usage_messages=len(messages)-len(valid), cost_usd=None)


def analyze(agent_logs: Path, expected_model: str = "qwen3.8-max") -> dict:
    """Return useful partial evidence on any invalid/missing retained input.

    ``complete`` means the captured root plus explicitly linked recursive child
    tree passed these checks. ``root_only`` distinguishes a complete tree that
    never spawned an Agent from an incomplete root-only capture of spawned work.
    Terminal failed tools count as completed tools, not missing usage.
    """
    logs = Path(agent_logs).resolve()
    errors: list[dict] = []
    files: list[dict] = []

    def error(code: str, **details: Any) -> None:
        item = dict(code=code, **details)
        if item not in errors:
            errors.append(item)

    def read(path: Path) -> list[dict]:
        relative = str(path.relative_to(logs))
        if path.is_symlink() or logs not in path.resolve().parents:
            error("unsafe_evidence_path", file=relative)
            return []
        try:
            data = path.read_bytes()
            files.append(dict(path=relative, sha256=_hash(data), bytes=len(data)))
            lines = data.decode("utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            error("evidence_unreadable", file=relative, exception=type(exc).__name__)
            return []
        events = []
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line, parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
                if not isinstance(event, dict):
                    raise ValueError("not an object")
            except (ValueError, TypeError):
                error("malformed_jsonl", file=relative, line=line_number)
                continue
            events.append(dict(event=event, file=relative, line=line_number))
        return events

    def usage(value: Any, location: dict) -> bool:
        if not isinstance(value, dict):
            error("missing_usage", **location)
            return False
        invalid = [f for f in FIELDS if f not in value or not _number(value[f])]
        if invalid:
            error("invalid_usage_fields", fields=invalid, **location)
            return False
        return True

    stream = read(logs / "claude-code.txt")
    results = []
    seen_result = set()
    for row in stream:
        e = row["event"]
        if e.get("type") == "result":
            signature = _hash(e)
            if signature not in seen_result:
                results.append(row); seen_result.add(signature)
    if len(results) != 1:
        error("expected_one_root_result", found=len(results))
    result = results[-1]["event"] if results else {}
    root_id = result.get("session_id")
    if not isinstance(root_id, str) or not root_id:
        error("missing_root_session_id"); root_id = None
    result_success = (result.get("subtype") == "success" and result.get("is_error") is False
                      and isinstance(result.get("result"), str) and bool(result["result"]))
    if not result_success:
        error("root_result_not_success")
    if result.get("terminal_reason") not in (None, "completed"):
        error("root_terminal_reason", terminal_reason=result.get("terminal_reason"))
    if result.get("stop_reason") not in (None, "end_turn"):
        error("root_stop_reason", stop_reason=result.get("stop_reason"))
    result_usage_valid = usage(result.get("usage"), {"source": "root_result"})
    inits = [r["event"] for r in stream if r["event"].get("type") == "system" and r["event"].get("subtype") == "init"]
    if not inits:
        error("missing_root_init")
    for init in inits:
        if init.get("session_id") != root_id or init.get("model") != expected_model:
            error("root_init_identity_mismatch")

    # A child transcript has a distinct agentId even when its sessionId is the
    # root UUID. Its filesystem name supplies an identity, never parent linkage.
    nodes: dict[str, dict] = {}
    jsonl_files = sorted((logs / "sessions" / "projects").rglob("*.jsonl"))
    if not jsonl_files:
        error("missing_persisted_sessions")
    for path in jsonl_files:
        child_match = re.fullmatch(r"agent-(.+)\.jsonl", path.name) if "subagents" in path.parts else None
        file_child = child_match[1] if child_match else None
        for row in read(path):
            e = row["event"]
            if e.get("type") not in ("assistant", "user"):
                continue
            agent_id = e.get("agentId")
            if agent_id is not None and (not isinstance(agent_id, str) or not agent_id):
                error("invalid_agent_id", file=row["file"], line=row["line"]); continue
            if file_child and agent_id and file_child != agent_id:
                error("child_filename_identity_mismatch", file=row["file"])
            child_id = agent_id or file_child
            if e.get("isSidechain") is True and not child_id:
                error("sidechain_without_agent_id", file=row["file"], line=row["line"])
            node_id = "agent:" + child_id if child_id else "root"
            if not child_id and e.get("sessionId") != root_id:
                error("foreign_root_session", file=row["file"], session_id=e.get("sessionId"))
            node = nodes.setdefault(node_id, dict(id=node_id, agent_id=child_id, events=[], files=set()))
            node["events"].append(row); node["files"].add(row["file"])
    if "root" not in nodes:
        error("missing_persisted_root")

    all_messages: list[dict] = []
    all_tools: list[dict] = []
    message_owner: dict[str, str] = {}
    tool_owner: dict[str, str] = {}
    internal: dict[str, dict] = {}
    for node_id, node in nodes.items():
        messages: dict[str, dict] = {}
        tools: dict[str, dict] = {}
        tool_results: dict[str, dict] = {}
        seen_uuid: dict[str, str] = {}
        seen_events: set[str] = set()
        for row in node["events"]:
            e = row["event"]; m = e.get("message")
            loc = dict(session=node_id, file=row["file"], line=row["line"])
            event_signature = _hash(e)
            if event_signature in seen_events:
                continue
            seen_events.add(event_signature)
            if not isinstance(m, dict):
                error("malformed_message", **loc); continue
            uuid = e.get("uuid")
            if not isinstance(uuid, str) or not uuid:
                error("missing_event_uuid", **loc)
            else:
                # Same event can carry a terminal usage/stop update. Other
                # conflicting changes to the same identity are not deduplication.
                shape = dict(e, message={k:v for k,v in m.items() if k not in ("usage", "stop_reason", "stop_sequence")})
                signature = _hash(shape)
                if uuid in seen_uuid and seen_uuid[uuid] != signature:
                    error("conflicting_event_uuid", uuid=uuid, **loc)
                seen_uuid[uuid] = signature
            content = m.get("content")
            if e["type"] == "assistant":
                mid = m.get("id")
                if not isinstance(mid, str) or not mid:
                    error("missing_message_id", **loc); continue
                if m.get("role") != "assistant" or m.get("model") != expected_model:
                    error("assistant_identity_mismatch", message_id=mid, **loc)
                if mid in message_owner and message_owner[mid] != node_id:
                    error("message_id_in_multiple_sessions", message_id=mid)
                message_owner[mid] = node_id
                rec = messages.setdefault(mid, dict(message_id=mid, session=node_id, model=m.get("model"),
                    usage=None, usage_high_water={}, stop_reason=None, blocks=[], block_keys=set(), event_uuids=[], session_files=set(), source_locations=[]))
                if uuid not in rec["event_uuids"]:
                    rec["event_uuids"].append(uuid)
                rec["session_files"].add(row["file"]); rec["source_locations"].append(dict(file=row["file"], line=row["line"]))
                current = m.get("usage")
                if isinstance(current, dict):
                    for f in FIELDS:
                        if f in current and not _number(current[f]):
                            error("invalid_usage_update", message_id=mid, field=f, **loc)
                        old = rec["usage_high_water"].get(f)
                        if _number(old) and _number(current.get(f)) and current[f] < old:
                            error("usage_regression", message_id=mid, field=f, **loc)
                        if _number(current.get(f)):
                            rec["usage_high_water"][f] = max(current[f], old if _number(old) else 0)
                # Never merge fields from earlier usage into a malformed final
                # object, and never sum each streamed content block as a call.
                rec["usage"] = current
                rec["stop_reason"] = m.get("stop_reason")
                if not isinstance(content, list):
                    error("malformed_assistant_content", message_id=mid, **loc); continue
                for block in content:
                    if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                        error("malformed_content_block", message_id=mid, **loc); continue
                    bkey = _hash(block)
                    if bkey not in rec["block_keys"]:
                        rec["blocks"].append(block); rec["block_keys"].add(bkey)
                    if block["type"] != "tool_use":
                        continue
                    tid = block.get("id")
                    if not isinstance(tid, str) or not tid or not isinstance(block.get("name"), str) or not isinstance(block.get("input"), dict):
                        error("malformed_tool_use", message_id=mid, **loc); continue
                    spec = dict(id=tid, session=node_id, message_id=mid, name=block["name"], input_sha256=_hash(block["input"]))
                    if tid in tools and tools[tid] != spec:
                        error("conflicting_tool_use", tool_id=tid, **loc)
                    if tid in tool_owner and tool_owner[tid] != node_id:
                        error("tool_id_in_multiple_sessions", tool_id=tid)
                    tool_owner[tid] = node_id
                    tools[tid] = spec
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    tid = block.get("tool_use_id")
                    if not isinstance(tid, str) or not tid:
                        error("malformed_tool_result", **loc); continue
                    if "is_error" in block and type(block["is_error"]) is not bool:
                        error("malformed_tool_error_flag", tool_id=tid, **loc)
                    metadata = e.get("toolUseResult", e.get("tool_use_result", {}))
                    item = dict(block=block, metadata=metadata, file=row["file"], line=row["line"])
                    if tid in tool_results and (tool_results[tid]["block"] != block or tool_results[tid]["metadata"] != metadata):
                        error("conflicting_tool_result", tool_id=tid, **loc)
                    tool_results[tid] = item
        for mid, rec in messages.items():
            rec["usage_valid"] = usage(rec["usage"], dict(session=node_id, message_id=mid))
            if rec["usage_valid"]:
                rec["usage"] = {f:rec["usage"][f] for f in FIELDS}
            rec["session_files"] = sorted(rec["session_files"])
            all_messages.append({k:v for k,v in rec.items() if k not in ("blocks", "block_keys", "usage_high_water")})
        for tid, tool in tools.items():
            completed = tid in tool_results
            if not completed:
                error("unfinished_tool", session=node_id, tool_id=tid)
            result_row = tool_results.get(tid, {})
            block = result_row.get("block", {})
            metadata = result_row.get("metadata", {})
            if isinstance(metadata, dict) and (metadata.get("interrupted") is True or metadata.get("status") in ("running", "in_progress", "async_launched")):
                error("unfinished_tool_metadata", session=node_id, tool_id=tid)
            tool.update(completed=completed, is_error=block.get("is_error") is True,
                result_sha256=_hash(block) if completed else None)
            all_tools.append(tool)
        for tid in tool_results.keys()-tools.keys():
            error("orphan_tool_result", session=node_id, tool_id=tid)
        final = list(messages.values())[-1] if messages else None
        final_text = _text(final["blocks"]) if final else ""
        if not final or not final_text or any(b["type"] == "tool_use" for b in final["blocks"]):
            error("missing_terminal_assistant_text", session=node_id)
        if final and final["stop_reason"] not in (None, "end_turn"):
            error("nonterminal_assistant_stop", session=node_id, stop_reason=final["stop_reason"])
        internal[node_id] = dict(messages=messages, tools=tools, results=tool_results, final=final, final_text=final_text)

    # Establish every edge from the completed native Agent/Task tool result.
    # A filename alone cannot establish that an orphan child belongs to the run.
    parents: dict[str, tuple[str,str]] = {}
    for node_id, data in internal.items():
        for tid, tool in data["tools"].items():
            if tool["name"].lower() not in ("agent", "task"):
                continue
            item = data["results"].get(tid, {})
            metadata = item.get("metadata", {})
            child_id = metadata.get("agentId") if isinstance(metadata, dict) else None
            if not child_id:
                if not tool["is_error"]:
                    error("missing_child_link", session=node_id, tool_id=tid)
                continue
            if not isinstance(child_id, str):
                error("invalid_child_link", session=node_id, tool_id=tid); continue
            child = "agent:" + child_id
            if child in parents and parents[child] != (node_id,tid):
                error("child_has_multiple_parents", child=child)
            parents[child] = (node_id,tid)
            if child not in internal:
                error("missing_child_transcript", child=child, tool_id=tid)
            elif not tool["is_error"]:
                child_final = internal[child]["final_text"]
                returned = _text(metadata.get("content")) or _text(item.get("block", {}).get("content"))
                if child_final and child_final not in returned:
                    error("child_final_not_in_parent_result", child=child, tool_id=tid)
    reachable = {"root"} if "root" in internal else set()
    for _ in range(len(internal)):
        reachable.update(c for c,(p,_) in parents.items() if p in reachable)
    for node_id in internal:
        if node_id not in reachable:
            error("unlinked_or_cyclic_child", session=node_id)

    root = internal.get("root", {})
    persisted_final = root.get("final_text", "")
    stream_blocks: dict[str, list] = {}
    stream_seen: dict[str, set] = {}
    for row in stream:
        e=row["event"]
        if e.get("type") != "assistant":
            continue
        m=e.get("message", {})
        if not isinstance(m,dict):
            error("malformed_stream_message", file=row["file"], line=row["line"]); continue
        mid=m.get("id")
        if not isinstance(mid,str) or not mid:
            error("malformed_stream_message_id", file=row["file"], line=row["line"]); continue
        if mid not in message_owner:
            error("stream_message_not_retained", message_id=mid); continue
        owner=message_owner[mid]
        parent=e.get("parent_tool_use_id")
        if owner=="root" and parent is not None:
            error("root_stream_has_child_parent", message_id=mid)
        if owner=="root" and e.get("session_id") != root_id:
            error("root_stream_session_mismatch", message_id=mid)
        if owner!="root" and (owner not in parents or parent!=parents[owner][1]):
            error("stream_child_link_mismatch", message_id=mid)
        if m.get("model")!=expected_model:
            error("stream_model_mismatch", message_id=mid)
        for block in m.get("content",[]) if isinstance(m.get("content"),list) else []:
            key=_hash(block)
            if key not in stream_seen.setdefault(mid,set()):
                stream_blocks.setdefault(mid,[]).append(block); stream_seen[mid].add(key)
            if block not in internal[owner]["messages"][mid]["blocks"]:
                error("stream_block_not_retained", message_id=mid)
    root_mid=root.get("final",{}).get("message_id") if root.get("final") else None
    stream_final=_text(stream_blocks.get(root_mid,[]))
    equality=bool(persisted_final) and persisted_final==result.get("result")==stream_final
    if not equality:
        error("final_text_mismatch")

    session_reports=[]
    for node_id,node in nodes.items():
        messages=[m for m in all_messages if m["session"]==node_id]
        tools=[t for t in all_tools if t["session"]==node_id]
        session_reports.append(dict(id=node_id,agent_id=node["agent_id"],
            parent_session=parents.get(node_id,(None,None))[0],parent_tool_use_id=parents.get(node_id,(None,None))[1],
            files=sorted(node["files"]),message_ids=[m["message_id"] for m in messages],
            usage=_totals(messages,tools),tool_count=len(tools),reachable=node_id in reachable))
    child_count=sum(s["id"]!="root" for s in session_reports)
    return dict(schema_version=1,scope=SCOPE,complete=not errors,errors=errors,expected_model=expected_model,
        root_session_id=root_id,root_count=int("root" in nodes),child_count=child_count,session_count=len(nodes),
        root_only=child_count==0,complete_tree=not errors,
        final=dict(text=persisted_final,sha256=_hash(persisted_final.encode()),message_id=root_mid,
            root_result_success=result_success,persisted_equals_stream_result=equality),
        usage=_totals(all_messages,all_tools),usage_partial=bool(errors),messages=all_messages,tools=all_tools,
        root=next((s for s in session_reports if s["id"]=="root"),None),
        children=[s for s in session_reports if s["id"]!="root"],sessions=session_reports,files=files,
        root_result_usage=result.get("usage"),root_result_usage_valid=result_usage_valid,
        root_result_usage_counted=False,
        limitations=["Persisted linked session messages only; not whole-provider billing or unpersisted background usage.",
                     "Root result totals are diagnostic and may already include child usage; never added to persisted totals.",
                     "Unknown or missing child linkage fails closed; native Docker evidence must validate the supported transcript shape."])
