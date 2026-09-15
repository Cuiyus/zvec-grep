"""Native-wire fixed-state next-decision prompt experiments.

This runner sends a single captured Chat Completions request per observation.
It does not run an agent, execute a tool, repair arguments, or follow a rollout.
Only exact guidance text and tool descriptions may differ from the source body.
Credentials are resolved at execution time and never included in the plan.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import re
import tempfile
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request


VARIANTS = ("P00", "P10", "P01", "P11")
CATEGORIES = ("original_question", "exact_search_no_result", "relevant_zg_entry",
              "parameter_error", "sufficient_evidence")
REPETITIONS = 5


def render_candidate_prompts(*, search_tool: str, rg_tool: str = "native grep",
                             include_zg_rg: bool = False,
                             prompt_dir: Path | None = None) -> dict[str, Any]:
    """Return native-installer and wire-diagnostic compatible plain prompt text."""
    directory = prompt_dir or Path(__file__).resolve().parents[2] / "prompts" / "stability-v1"
    guidance = (directory / "candidate-guidance.txt").read_text().format(search_tool=search_tool, rg_tool=rg_tool)
    search = (directory / "candidate-search-description.txt").read_text().strip()
    rg = (directory / "candidate-rg-description.txt").read_text().strip()
    native = {"zvec_grep_search": search}
    wire = {search_tool: search}
    if include_zg_rg:
        native["zvec_grep_rg"], wire[rg_tool] = rg, rg
    return {"guidance_override": guidance, "description_overrides": native,
            "wire_description_overrides": wire, "zg_tool_names": list(wire)}


def default_prompt_config(capture_group_dirs: dict[str, Path]) -> dict[str, Any]:
    """Derive installed text and real tool names from each native capture directory.

    Directory must contain native-install.json (or install-manifest.json) and
    agent/wire-requests. The helper never assumes an rg MCP tool exists.
    """
    groups = {}
    for group_id, directory_value in capture_group_dirs.items():
        directory = Path(directory_value)
        manifests = []
        for name in ("native-install.json", "native-install-manifest.json", "install-manifest.json"):
            matches = [p for p in (directory / name, directory / "agent" / name) if p.is_file()]
            if matches:
                manifests = matches
                break
        if not manifests:
            manifests = sorted(directory.rglob("*install*manifest*.json"))
        if len(manifests) != 1:
            raise ValueError(f"Expected one native install manifest for {group_id}")
        manifest = _read(manifests[0])
        original = manifest.get("installed_guidance_text") or manifest.get("guidance_text")
        if not isinstance(original, str) or not original:
            raise ValueError("Install manifest lacks exact original guidance text")
        body_paths = sorted(directory.rglob("wire-requests/request-*.json"))
        bodies = [_read(path) for path in body_paths if ".raw." not in path.name]
        body = next((item for item in bodies if item.get("tools")), None)
        if body is None:
            raise ValueError("Native capture lacks a tool-bearing model request")
        # Native CLIs may trim the file's final newline before insertion. Use
        # only a form actually occurring exactly once, never fuzzy replacement.
        instruction_texts = []
        for message in body["messages"]:
            if message.get("role") not in {"system", "developer", "user"}:
                continue
            content = message.get("content")
            if isinstance(content, str):
                instruction_texts.append((message["role"], content))
            elif isinstance(content, list):
                instruction_texts.extend((message["role"], block["text"]) for block in content if isinstance(block, dict) and isinstance(block.get("text"), str))
        matches = [(candidate, [(role, text) for role, text in instruction_texts if candidate in text]) for candidate in (original, original.strip())]
        matching = next(((candidate, locations) for candidate, locations in matches if sum(text.count(candidate) for _, text in locations) == 1), None)
        if matching is None:
            raise ValueError("Installed guidance does not occur exactly once in native request")
        original, locations = matching
        names = [t["function"]["name"] for t in body["tools"]]
        search = [name for name in names if name.endswith("zvec_grep_search")]
        rg = [name for name in names if name.endswith("zvec_grep_rg")]
        if len(search) != 1 or len(rg) > 1:
            raise ValueError("Unexpected native zg tool names")
        rendered = render_candidate_prompts(search_tool=search[0], rg_tool=rg[0] if rg else "native grep", include_zg_rg=bool(rg))
        groups[group_id] = {"original_guidance": original, "candidate_guidance": rendered["guidance_override"],
                            "description_overrides": rendered["wire_description_overrides"],
                            "zg_tool_names": rendered["zg_tool_names"],
                            "guidance_in_native_user_message": locations[0][0] == "user"}
    return {"groups": groups}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


def _scrub(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    # Error bodies occasionally echo authentication headers.
    return re.sub(r"(?i)(Bearer\s+)[^\s\"'<>]+", r"\1[REDACTED]", text)


def _write(path: Path, value: Any, secrets: tuple[str, ...] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = _scrub(json.dumps(value, ensure_ascii=False, indent=2), secrets) + "\n"
    # Parallel groups can analyze each other's completed results. Publish each
    # whole JSON atomically; never expose a half-written response or report.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + "-", delete=False) as output:
            temporary = Path(output.name)
            output.write(serialized)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _endpoint(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Endpoint must be HTTP(S), without credentials, query, or fragment")
    return value.rstrip("/").removesuffix("/chat/completions")


def extract_states(trace_specs: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    """Extract first eligible real wire state per group/category, never synthesize history.

    Each spec has group_id, trial_id, agent_dir, agent and optional model.
    Normal endpoint provenance comes from agent/session-spec.json:tap_upstream.
    Capture-only calibration additionally requires intended_endpoint and explicit
    native_request_builder_verified=true; its provenance is reported separately.
    Later categories require state_annotations keyed by request ID, with category,
    source_verified=true and nonempty source_refs. No keyword-based gold inference.
    """
    output_dir = Path(output_dir)
    candidates: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unavailable: list[dict[str, Any]] = []
    groups = sorted({str(spec["group_id"]) for spec in trace_specs})
    for spec in sorted(trace_specs, key=lambda s: (s["group_id"], s["trial_id"])):
        base = Path(spec["agent_dir"])
        session_path = next((base / name for name in ("session-spec.json", "native-runtime-spec.json") if (base / name).is_file()), None)
        reason = None
        if spec.get("agent") != "opencode":
            reason = "Native full request/context and same endpoint unavailable; no pasted-history fallback"
        elif not (base / "wire.jsonl").is_file() or session_path is None:
            reason = "Missing wire trace or endpoint provenance"
        if reason:
            unavailable.append({"group_id": spec["group_id"], "trial_id": spec["trial_id"], "reason": reason})
            continue
        session = _read(session_path)
        endpoint = session.get("tap_upstream")
        capture_only = spec.get("capture_only") is True
        if capture_only:
            if not spec.get("native_request_builder_verified") or not spec.get("intended_endpoint"):
                raise ValueError("Capture-only states require intended endpoint and verified native request construction")
            endpoint = spec["intended_endpoint"]
            capture_path = base / "capture-manifest.json"
            if capture_path.is_file():
                capture = _read(capture_path)
                if not (capture.get("capture_only") is True and capture.get("installed_guidance_verified") is True
                        and capture.get("intended_endpoint") == endpoint and capture.get("paid_model_calls") == 0):
                    raise ValueError("Native capture manifest does not verify intended endpoint and initial request construction")
        endpoint = _endpoint(endpoint or "")
        first_with_tools = True
        for line, text in enumerate((base / "wire.jsonl").read_text().splitlines(), 1):
            event = json.loads(text)
            if event.get("event") != "request" or not event.get("tool_names"):
                continue
            rid = event.get("request_id")
            if type(rid) is not int:
                continue
            body_path = base / "wire-requests" / f"request-{rid:03d}.json"
            if not body_path.is_file():
                unavailable.append({"group_id": spec["group_id"], "trial_id": spec["trial_id"], "request_id": rid, "reason": "Missing captured body"})
                continue
            body = _read(body_path)
            compact_hash = hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            raw_path = base / "wire-requests" / f"request-{rid:03d}.raw.json"
            raw_verified = False
            if raw_path.is_file() and not event.get("raw_body_redacted"):
                raw_bytes = raw_path.read_bytes()
                raw_verified = hashlib.sha256(raw_bytes).hexdigest() == event.get("request_sha256") and json.loads(raw_bytes) == body
            if not raw_verified and compact_hash != event.get("request_sha256"):
                unavailable.append({"group_id": spec["group_id"], "trial_id": spec["trial_id"], "request_id": rid, "reason": "Captured body does not reproduce recorded wire SHA"})
                continue
            names = [t.get("function", {}).get("name") for t in body.get("tools", [])]
            if names != event["tool_names"] or body.get("model") != event.get("model") or not isinstance(body.get("messages"), list):
                raise ValueError("Wire metadata does not match full body")
            if spec.get("model") and spec["model"] != body["model"]:
                raise ValueError("Captured model differs from requested diagnostic model")
            annotation = spec.get("state_annotations", {}).get(str(rid), {})
            category = annotation.get("category")
            history = any(m.get("role") in {"assistant", "tool"} for m in body["messages"])
            if first_with_tools and not history:
                category = "original_question"
            first_with_tools = False
            if category not in CATEGORIES:
                continue
            if category != "original_question" and not (annotation.get("source_verified") is True and annotation.get("source_refs")):
                continue
            if category != "original_question" and capture_only:
                raise ValueError("Capture-only construction cannot establish a real later-turn state")
            state = {"group_id": spec["group_id"], "trial_id": spec["trial_id"], "category": category,
                     "agent": spec["agent"], "model": body["model"], "endpoint": endpoint,
                     "request_id": rid, "request": body, "request_sha256": sha(body),
                     "wire_sha256": event["request_sha256"], "raw_wire_verified": raw_verified,
                     "source": {"path": str(body_path.resolve()),
                     "file_sha256": hashlib.sha256(body_path.read_bytes()).hexdigest(),
                     "wire_path": str((base / "wire.jsonl").resolve()), "line": line},
                     "provenance": "native_initial_request_capture_only" if capture_only else "historical_native_wire",
                     "endpoint_provenance": {"path": str(session_path.resolve()),
                     "captured_upstream": session.get("tap_upstream"), "intended_endpoint": endpoint,
                     "native_request_builder_verified": spec.get("native_request_builder_verified") if capture_only else None},
                     "annotation": annotation}
            candidates[(spec["group_id"], category)].append(state)
    states = []
    for key in sorted(candidates):
        state = sorted(candidates[key], key=lambda s: (s["trial_id"], s["request_id"]))[0]
        state["state_id"] = f"{state['group_id']}-{state['category']}-{state['request_sha256'][:12]}"
        states.append(state)
    result = {"schema_version": 1, "states": states, "unavailable": unavailable,
              "missing_categories": [{"group_id": g, "category": c} for g in groups for c in CATEGORIES if (g, c) not in candidates],
              "selection_rule": "First SHA-verified eligible trial/request in frozen lexical trial order per group/category"}
    _write(output_dir / "states.json", result)
    return result


def _patch_guidance(body: dict[str, Any], before: str, after: str, *, allow_user: bool = False) -> None:
    if not before or not after:
        raise ValueError("Both exact original and replacement guidance are required")
    locations: list[tuple[dict[str, Any], str]] = []
    for message in body["messages"]:
        if message.get("role") not in ({"system", "developer", "user"} if allow_user else {"system", "developer"}):
            continue
        content = message.get("content")
        if isinstance(content, str) and before in content:
            locations.append((message, "content"))
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str) and before in block["text"]:
                    locations.append((block, "text"))
    count = sum(item[key].count(before) for item, key in locations)
    if count != 1:
        raise ValueError(f"Expected one exact guidance fragment, observed {count}")
    item, key = locations[0]
    item[key] = item[key].replace(before, after, 1)


def variant_request(state: dict[str, Any], config: dict[str, Any], variant: str) -> dict[str, Any]:
    """Text-only intervention. The current P00 body is never reconstructed."""
    if variant not in VARIANTS:
        raise ValueError("Unknown prompt variant")
    original = state["request"]
    if sha(original) != state["request_sha256"]:
        raise ValueError("State body was modified after extraction")
    body = copy.deepcopy(original)
    if variant in {"P10", "P11"}:
        _patch_guidance(body, config["original_guidance"], config["candidate_guidance"], allow_user=config.get("guidance_in_native_user_message", False))
    if variant in {"P01", "P11"}:
        replacements = config.get("description_overrides", {})
        if not replacements:
            raise ValueError("Description variants require explicit actual tool names")
        found = set()
        for tool in body["tools"]:
            function = tool["function"]
            if function["name"] in replacements:
                value = replacements[function["name"]]
                if not isinstance(value, str) or not value:
                    raise ValueError("Description override must be nonempty text")
                function["description"] = value
                found.add(function["name"])
        if found != set(replacements):
            raise ValueError("Description override does not match actual tool catalog")
    # All non-description tool fields, schema, defaults, order remain identical.
    strip = lambda tools: [{**t, "function": {k: v for k, v in t["function"].items() if k != "description"}} for t in tools]
    if strip(body["tools"]) != strip(original["tools"]):
        raise ValueError("Prompt intervention changed a tool contract")
    return body


def build_plan(states: dict[str, Any], prompt_config: dict[str, Any], output_dir: Path,
               *, repeats: int = REPETITIONS, order_seed: int = 1729) -> dict[str, Any]:
    """Freeze four variants and exactly five next-decision draws per state.

    prompt_config may be a common config or {groups: {group_id: config}}.
    This order seed never becomes a model seed.
    """
    if repeats != REPETITIONS:
        raise ValueError("Protocol requires exactly five observations per variant/state")
    output_dir = Path(output_dir)
    requests, samples = {}, []
    rng = random.Random(order_seed)
    for state in states["states"]:
        config = prompt_config.get("groups", {}).get(state["group_id"], prompt_config)
        for variant in VARIANTS:
            body = variant_request(state, config, variant)
            key = f"{state['state_id']}-{variant}"
            requests[key] = {"request": body, "request_sha256": sha(body), "state_id": state["state_id"],
                             "group_id": state["group_id"], "variant": variant, "model": state["model"],
                             "endpoint": state["endpoint"], "zg_tool_names": config["zg_tool_names"]}
        for repetition in range(1, repeats + 1):
            order = list(VARIANTS)
            rng.shuffle(order)
            for variant in order:
                key = f"{state['state_id']}-{variant}"
                samples.append({"sample_id": f"{key}-r{repetition:02d}", "request_key": key,
                                "state_id": state["state_id"], "group_id": state["group_id"],
                                "variant": variant, "repetition": repetition})
    if not samples:
        raise ValueError("No faithful states available; cannot create a model experiment")
    payload = {"schema_version": 1, "kind": "native-next-decision-prompt-diagnostic", "repeats": repeats,
               "order_seed": order_seed, "model_seed": "unchanged_from_source_request",
               "states": states, "requests": requests, "samples": samples,
               "prompt_config": prompt_config, "tool_execution": False, "automatic_retries": 0}
    payload["plan_sha256"] = sha(payload)
    _write(output_dir / "plan.json", payload)
    return payload


def load_plan(path: Path) -> dict[str, Any]:
    plan = _read(Path(path))
    expected = plan.pop("plan_sha256", None)
    if expected != sha(plan):
        raise ValueError("Frozen plan hash mismatch")
    plan["plan_sha256"] = expected
    return plan


def parse_response(raw: str, content_type: str) -> dict[str, Any]:
    """Assemble every tool delta, including parallel calls; preserve raw elsewhere."""
    events, done = [], False
    if "text/event-stream" in content_type or raw.lstrip().startswith("data:"):
        for block in re.split(r"\r?\n\r?\n", raw):
            fields = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
            if not fields:
                continue
            data = "\n".join(fields)
            if data == "[DONE]":
                done = True
                continue
            events.append(json.loads(data))
    else:
        events = [json.loads(raw)]
        done = True
    choices: dict[int, dict[str, Any]] = {}
    metadata = {"usage": None, "model": None, "response_id": None, "system_fingerprint": None}
    for event in events:
        if event.get("error"):
            raise ValueError("Provider returned an error event")
        for target, source in (("usage", "usage"), ("model", "model"), ("response_id", "id"), ("system_fingerprint", "system_fingerprint")):
            if event.get(source) is not None:
                metadata[target] = event[source]
        for choice in event.get("choices", []):
            index = choice.get("index", 0)
            result = choices.setdefault(index, {"index": index, "content": "", "reasoning_content": "", "tool_calls": {}, "finish_reason": None})
            message = choice.get("delta") if "delta" in choice else choice.get("message", {})
            for key in ("content", "reasoning_content"):
                if isinstance(message.get(key), str):
                    result[key] += message[key]
            for offset, call in enumerate(message.get("tool_calls") or []):
                call_index = call.get("index", offset)
                record = result["tool_calls"].setdefault(call_index, {"index": call_index, "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if call.get("id"):
                    record["id"] += call["id"]
                if call.get("type"):
                    record["type"] = call["type"]
                for key in ("name", "arguments"):
                    value = (call.get("function") or {}).get(key)
                    if isinstance(value, str):
                        record["function"][key] += value
            if choice.get("finish_reason") is not None:
                result["finish_reason"] = choice["finish_reason"]
    output = []
    for index in sorted(choices):
        result = choices[index]
        result["tool_calls"] = [result["tool_calls"][key] for key in sorted(result["tool_calls"])]
        output.append(result)
    complete = bool(output) and all(choice["finish_reason"] in {"stop", "tool_calls", "function_call"} for choice in output)
    return {**metadata, "choices": output, "stream_done_observed": done, "decision_complete": complete,
            "event_count": len(events)}


def validate_calls(response: dict[str, Any], request: dict[str, Any], zg_tool_names: list[str]) -> dict[str, Any]:
    """Validate actual raw arguments against captured schemas; never fix them."""
    try:
        import jsonschema
    except ImportError:
        return {"status": "unavailable", "reason": "jsonschema package required", "calls": []}
    schemas = {t["function"]["name"]: t["function"].get("parameters", {}) for t in request["tools"]}
    rows = []
    for choice in response["choices"]:
        for call in choice["tool_calls"]:
            function = call["function"]
            name, raw = function["name"], function["arguments"]
            errors, arguments = [], None
            try:
                arguments = json.loads(raw)
            except (ValueError, TypeError):
                errors.append("arguments_not_json")
            if name not in schemas:
                errors.append("tool_not_in_captured_catalog")
            elif not errors:
                try:
                    jsonschema.validate(arguments, schemas[name])
                except jsonschema.ValidationError as error:
                    errors.append("schema:" + "/".join(str(p) for p in error.absolute_path) + ":" + str(error.validator))
                except jsonschema.SchemaError:
                    errors.append("captured_schema_unvalidated")
            rows.append({"choice_index": choice["index"], "call_id": call["id"], "name": name,
                         "raw_arguments": raw, "arguments": arguments, "valid": not errors,
                         "errors": errors, "is_zg": name in zg_tool_names,
                         "request_identity": sha({"name": name, "arguments": arguments}) if not errors else None})
    return {"status": "complete", "calls": rows, "attempted_calls": len(rows),
            "valid_calls": sum(row["valid"] for row in rows),
            "zg_attempted_calls": sum(row["is_zg"] for row in rows),
            "agent_repaired_calls": None, "repair_scope": "No agent execution; raw provider decision only"}


class PartialTransportError(Exception):
    """Keep partial wire output when an already-started stream is interrupted."""
    def __init__(self, status: int, headers: dict[str, str], raw: bytes, error: Exception):
        super().__init__(str(error))
        self.status, self.headers, self.raw = status, headers, raw
        self.original_error_type = type(error).__name__


def _transport(endpoint: str, key: str, body: dict[str, Any], timeout: float) -> tuple[int, dict[str, str], bytes]:
    wire = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    request = urllib.request.Request(endpoint + "/chat/completions", wire,
        {"Content-Type": "application/json", "Accept": "text/event-stream" if body.get("stream") else "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as result:
            chunks = []
            try:
                while True:
                    chunk = result.readline() if body.get("stream") else result.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except Exception as error:
                raise PartialTransportError(result.status, dict(result.headers), b"".join(chunks), error) from error
            return result.status, dict(result.headers), b"".join(chunks)
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def run_plan(plan_path: Path, *, credential_env: str, endpoint: str, timeout: float = 180,
             transport: Callable[..., tuple[int, dict[str, str], bytes]] | None = None,
             group_id: str | None = None) -> dict[str, Any]:
    """Execute each planned sample once. Existing/error/interrupted attempts never rerun.

    An atomic attempt marker is written before network I/O. This protects against
    accidental duplicate spend after an uncertain interruption. Remaining samples
    may resume, while the interrupted sample remains explicitly incomplete.
    """
    plan_path = Path(plan_path)
    plan = load_plan(plan_path)
    endpoint = _endpoint(endpoint)
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", credential_env):
        raise ValueError("Credential must be an environment-variable name")
    key = os.environ.get(credential_env)
    if not key:
        raise ValueError("Required credential environment variable is absent")
    selected = [s for s in plan["samples"] if group_id is None or s["group_id"] == group_id]
    if not selected:
        raise ValueError("No planned samples for selected group")
    if any(plan["requests"][s["request_key"]]["endpoint"] != endpoint for s in selected):
        raise ValueError("Execution endpoint differs from frozen native route; select a single endpoint group")
    for sample in selected:
        directory = plan_path.parent / "samples" / sample["sample_id"]
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / "attempt.json"
        result_path = directory / "result.json"
        if marker.exists() or result_path.exists():
            continue
        definition = plan["requests"][sample["request_key"]]
        body = definition["request"]
        if sha(body) != definition["request_sha256"]:
            raise ValueError("Frozen request changed")
        with marker.open("x") as output:
            output.write(json.dumps({"sample_id": sample["sample_id"], "plan_sha256": plan["plan_sha256"], "started_at": time.time()}))
        _write(directory / "request.json", body, (key,))
        started = time.monotonic()
        result = {**sample, "plan_sha256": plan["plan_sha256"], "request_sha256": sha(body),
                  "status": "unknown", "automatic_retries": 0, "tool_execution": False}
        try:
            status, headers, raw = (transport or _transport)(endpoint, key, body, timeout)
            lowered = {name.lower(): value for name, value in headers.items()}
            text = raw.decode("utf-8", errors="replace")
            (directory / "response.txt").write_text(_scrub(text, (key,)))
            result.update({"http_status": status, "response_sha256": hashlib.sha256(raw).hexdigest(),
                           "response_headers": {name: lowered[name] for name in ("content-type", "x-request-id", "request-id", "date") if name in lowered}})
            if not 200 <= status < 300:
                result["status"] = "http_error"
            else:
                response = parse_response(text, lowered.get("content-type", ""))
                result["response"] = response
                result["validation"] = validate_calls(response, body, definition["zg_tool_names"])
                result["status"] = "completed" if response["decision_complete"] else "incomplete_response"
                result["observed_model_matches_request"] = response["model"] == body["model"] if response["model"] else None
                if result["observed_model_matches_request"] is False:
                    result["status"] = "model_identity_mismatch"
        except PartialTransportError as error:
            text = error.raw.decode("utf-8", errors="replace")
            (directory / "response.txt").write_text(_scrub(text, (key,)))
            result.update({"status": "incomplete_transport", "http_status": error.status,
                           "response_sha256": hashlib.sha256(error.raw).hexdigest(),
                           "error_type": error.original_error_type,
                           "error": _scrub(str(error), (key,))[:1000]})
            try:
                lowered = {name.lower(): value for name, value in error.headers.items()}
                result["response"] = parse_response(text, lowered.get("content-type", ""))
            except (ValueError, KeyError, TypeError):
                pass
        except Exception as error:
            result["status"] = "transport_or_parse_error"
            result["error_type"] = type(error).__name__
            result["error"] = _scrub(str(error), (key,))[:1000]
        result["wall_seconds"] = time.monotonic() - started
        _write(result_path, result, (key,))
    return analyze(plan_path)


def analyze(plan_path: Path, evidence_path: Path | None = None) -> dict[str, Any]:
    """Report observable consistency; no efficacy winner without audited evidence."""
    plan_path = Path(plan_path)
    plan = load_plan(plan_path)
    rows = []
    for sample in plan["samples"]:
        path = plan_path.parent / "samples" / sample["sample_id"] / "result.json"
        if path.is_file():
            row = _read(path)
            if row.get("plan_sha256") != plan["plan_sha256"]:
                raise ValueError("Sample belongs to a different frozen plan")
        else:
            attempted = (path.parent / "attempt.json").exists()
            row = {**sample, "status": "interrupted_or_unknown" if attempted else "not_run"}
        rows.append(row)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["state_id"], row["variant"])].append(row)
    summaries = []
    for (state_id, variant), observed in sorted(grouped.items()):
        calls = [call for row in observed for call in row.get("validation", {}).get("calls", [])]
        complete = [r for r in observed if r["status"] == "completed"]
        identities = [c["request_identity"] for c in calls if c["is_zg"] and c["valid"]]
        decision_identities = [sha([{ "name": c["name"], "arguments": c["arguments"] if c["valid"] else c["raw_arguments"]} for c in row.get("validation", {}).get("calls", [])]) for row in complete]
        summaries.append({"state_id": state_id, "group_id": observed[0]["group_id"], "variant": variant,
                          "planned": len(observed), "completed": len(complete), "status_counts": dict(Counter(r["status"] for r in observed)),
                          "attempted_calls": len(calls), "valid_calls": sum(c["valid"] for c in calls),
                          "invalid_calls": sum(not c["valid"] for c in calls),
                          "tool_choice_counts": dict(Counter(c["name"] for c in calls)),
                          "zg_adopting_decisions": sum(any(c["is_zg"] for c in r.get("validation", {}).get("calls", [])) for r in complete),
                          "unique_valid_zg_requests": len(set(identities)),
                          "zg_request_frequencies": dict(Counter(identities)),
                          "tool_call_batch_frequencies": dict(Counter(decision_identities)),
                          "usage_missing": sum(not r.get("response", {}).get("usage") for r in complete)})
    selection = {"variant": "P00", "status": "retain_current_insufficient_evidence",
                 "reason": "Adoption/repetition alone cannot establish correctness or retrieval benefit"}
    if evidence_path is not None:
        evidence = _read(Path(evidence_path))
        selection = select_candidate(plan, rows, evidence)
    result = {"schema_version": 1, "plan_sha256": plan["plan_sha256"], "planned": len(rows),
              "completed": sum(row["status"] == "completed" for row in rows), "groups": summaries,
              "samples": rows, "selection": selection, "state_availability": plan["states"].get("missing_categories", []),
              "limitations": ["A local next-decision experiment, not full E2E cost or quality evidence",
                              "Exact query repetition measures observable consistency, not semantic correctness",
                              "No tool execution or native Agent repair occurs inside this diagnostic"]}
    _write(plan_path.parent / "analysis.json", result)
    return result


def select_candidate(plan: dict[str, Any], rows: list[dict[str, Any]], evidence: dict[str, Any]) -> dict[str, Any]:
    """Predeclared conservative screen across ALL available group/state strata.

    Evidence reviews every sample (including appropriate non-search decisions).
    Retrieval is assessed on returned queries outside this module. Source refs
    and review rationale are mandatory; source_verified is a reviewer assertion,
    never inferred merely from a file's existence. No self-graded model score.
    """
    fallback = {"variant": "P00", "status": "retain_current_insufficient_evidence"}
    if evidence.get("plan_sha256") != plan["plan_sha256"] or evidence.get("assessment_frozen") is not True:
        return {**fallback, "reason": "Evidence is not frozen for this plan"}
    evidence_rows = evidence.get("rows", [])
    reviewed = {r["sample_id"]: r for r in evidence_rows}
    if len(reviewed) != len(evidence_rows):
        raise ValueError("Duplicate evidence sample ID")
    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        strata[(row["state_id"], row["variant"])].append(row)
    state_ids = sorted({r["state_id"] for r in rows})
    eligible, reasons = [], {}

    def quality(sample: dict[str, Any]) -> tuple[int, float | None, float | None] | None:
        review = reviewed.get(sample["sample_id"], {})
        validation = sample.get("validation", {})
        if sample["status"] != "completed" or validation.get("status") != "complete":
            return None
        if not (review.get("source_verified") is True and review.get("source_refs") and review.get("rationale") and type(review.get("goal_correct")) is bool):
            return None
        retrieval = review.get("retrieval", {})
        if retrieval.get("status") == "not_applicable":
            if any(c["is_zg"] and c["valid"] for c in validation.get("calls", [])):
                return None
            return int(review["goal_correct"]), None, None
        hit, rr = retrieval.get("hit_at_10"), retrieval.get("rr_at_10")
        if retrieval.get("status") != "complete" or type(hit) is not bool or type(rr) not in {float, int} or not 0 <= rr <= 1:
            return None
        if hit != (rr > 0):
            return None
        return int(review["goal_correct"]), float(hit), float(rr)

    def metrics(samples: list[dict[str, Any]]) -> tuple[float, float, float | None, float | None] | None:
        values = [quality(s) for s in samples]
        if len(samples) != REPETITIONS or any(value is None for value in values):
            return None
        calls = [c for sample in samples for c in sample["validation"]["calls"]]
        valid = sum(c["valid"] for c in calls) / len(calls) if calls else 1.0
        scored = [value for value in values if value is not None]
        retrieved = [v for v in scored if v[1] is not None]
        return (sum(v[0] for v in scored) / REPETITIONS, valid,
                sum(v[1] for v in retrieved) / len(retrieved) if retrieved else None,
                sum(v[2] for v in retrieved) / len(retrieved) if retrieved else None)

    for variant in VARIANTS[1:]:
        errors, strict = [], False
        for state_id in state_ids:
            base = metrics(strata[(state_id, "P00")])
            candidate = metrics(strata[(state_id, variant)])
            if base is None or candidate is None:
                errors.append(f"{state_id}: incomplete/error/unreviewed observations")
                continue
            if candidate[0] != 1.0 or candidate[1] != 1.0:
                errors.append(f"{state_id}: candidate contains goal or call correctness failures")
            for index, name in enumerate(("goal_correct", "valid_call_fraction", "hit_at_10", "rr_at_10")):
                left, right = base[index], candidate[index]
                if left is None or right is None:
                    # Different retrieval subsets do not establish ranking improvement.
                    if left != right:
                        errors.append(f"{state_id}: {name} evaluated on incomparable action subsets")
                    continue
                if right < left:
                    errors.append(f"{state_id}: {name} decreased")
                strict = strict or right > left
        if not strict:
            errors.append("No measured correctness or retrieval improvement")
        reasons[variant] = errors
        if not errors:
            eligible.append(variant)
    if not eligible:
        return {**fallback, "reason": "No candidate met all predeclared screening requirements", "candidate_findings": reasons}
    # One changed factor precedes two; guidance precedes description only as an
    # explicit deterministic tie rule, never as a claim of stronger efficacy.
    chosen = next(v for v in ("P10", "P01", "P11") if v in eligible)
    return {"variant": chosen, "status": "screened_candidate_requires_fresh_e2e", "eligible": eligible,
            "tie_rule": "Fewest changed factors, then P10/P01/P11 fixed order", "candidate_findings": reasons,
            "reason": "No observed correctness/ranking decrease; at least one screening improvement per candidate across available strata"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("--trace-specs", type=Path, required=True)
    extract.add_argument("--output", type=Path, required=True)
    build = sub.add_parser("plan")
    build.add_argument("--states", type=Path, required=True)
    build.add_argument("--prompt-config", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--order-seed", type=int, default=1729)
    run = sub.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--credential-env", required=True)
    run.add_argument("--endpoint", required=True)
    run.add_argument("--group-id")
    run.add_argument("--timeout", type=float, default=180)
    report = sub.add_parser("analyze")
    report.add_argument("--plan", type=Path, required=True)
    report.add_argument("--evidence", type=Path)
    args = parser.parse_args(argv)
    if args.command == "extract":
        value = extract_states(_read(args.trace_specs), args.output)
        print(json.dumps({"states": len(value["states"]), "unavailable": len(value["unavailable"])}))
    elif args.command == "plan":
        value = build_plan(_read(args.states), _read(args.prompt_config), args.output, order_seed=args.order_seed)
        print(json.dumps({"planned": len(value["samples"]), "plan_sha256": value["plan_sha256"]}))
    else:
        value = run_plan(args.plan, credential_env=args.credential_env, endpoint=args.endpoint, timeout=args.timeout, group_id=args.group_id) if args.command == "run" else analyze(args.plan, args.evidence)
        print(json.dumps({"planned": value["planned"], "completed": value["completed"], "selection": value["selection"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
