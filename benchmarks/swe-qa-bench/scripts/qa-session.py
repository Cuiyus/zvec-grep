#!/usr/bin/env python3
"""Run one fresh agent session with observable limits and an optional HTTP tap.

The tap preserves request/response bytes; it never repairs tool calls or retries
the model. Only the explicitly configured OpenAI-compatible endpoint is used.
Limits stop after an observed threshold, so an in-flight turn may overshoot.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import math
import os
import queue
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def scrub(text):
    for key in ("OPENAI_API_KEY", "GLM_API_KEY", "QODER_PERSONAL_ACCESS_TOKEN", "QWEN_API_KEY"):
        secret = os.environ.get(key)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scrub(json.dumps(value, ensure_ascii=False, indent=2)) + "\n")


class WireTap:
    def __init__(self, root, upstream):
        self.root, self.upstream = root, upstream.rstrip("/")
        self.lock = threading.Lock()
        self.sequence = 0
        self.server = None

    def record(self, value):
        with self.lock:
            with (self.root / "wire.jsonl").open("a") as out:
                out.write(scrub(json.dumps(value, ensure_ascii=False)) + "\n")

    def start(self):
        tap = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path.split("?", 1)[0] != "/v1/chat/completions":
                    self.send_error(404)
                    return
                with tap.lock:
                    tap.sequence += 1
                    request_id = tap.sequence
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                started = time.monotonic()
                try:
                    data = json.loads(body)
                    schema = data.get("tools", [])
                    save(tap.root / "wire-requests" / f"request-{request_id:03d}.json", data)
                    tap.record({"event": "request", "request_id": request_id, "timestamp": time.time(),
                                "model": data.get("model"), "temperature": data.get("temperature"),
                                "top_p": data.get("top_p"), "max_tokens": data.get("max_tokens"),
                                "tool_names": [t.get("function", {}).get("name") for t in schema],
                                "schema_sha256": hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(),
                                "request_sha256": hashlib.sha256(body).hexdigest()})
                    headers = {"Content-Type": "application/json", "Accept": self.headers.get("Accept", "*/*")}
                    if self.headers.get("Authorization"):
                        headers["Authorization"] = self.headers["Authorization"]
                    request = urllib.request.Request(tap.upstream + "/chat/completions", body, headers)
                    with urllib.request.urlopen(request, timeout=180) as upstream:
                        self.send_response(upstream.status)
                        self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.close_connection = True
                        model, usage, response_id, fingerprint = None, None, None, None
                        response_chunks = []
                        is_sse = "text/event-stream" in upstream.headers.get("Content-Type", "")
                        while True:
                            chunk = upstream.readline() if is_sse else upstream.read()
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                            response_chunks.append(chunk)
                            payload = chunk[5:].strip() if chunk.startswith(b"data:") else chunk.strip()
                            if payload.startswith(b"{"):
                                try:
                                    response = json.loads(payload)
                                    model = response.get("model") or model
                                    response_id = response.get("id") or response_id
                                    fingerprint = response.get("system_fingerprint") or fingerprint
                                    usage = response.get("usage") or usage
                                except ValueError:
                                    pass
                        (tap.root / "wire-requests" / f"response-{request_id:03d}.txt").write_text(
                            scrub(b"".join(response_chunks).decode("utf-8", errors="replace")))
                        tap.record({"event": "response", "request_id": request_id, "status": upstream.status,
                                    "model": model, "response_id": response_id, "system_fingerprint": fingerprint,
                                    "usage": usage, "duration_seconds": time.monotonic() - started})
                except urllib.error.HTTPError as error:
                    detail = error.read()
                    tap.record({"event": "response", "request_id": request_id, "status": error.code,
                                "error": scrub(detail.decode(errors="replace"))[:4000],
                                "duration_seconds": time.monotonic() - started})
                    self.send_response(error.code)
                    self.end_headers()
                    self.wfile.write(detail)
                except Exception as error:
                    tap.record({"event": "transport_error", "request_id": request_id,
                                "error_type": type(error).__name__, "duration_seconds": time.monotonic() - started})
                    try:
                        self.send_error(502)
                    except OSError:
                        pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_port}/v1"


class Counters:
    def __init__(self):
        self.requests, self.calls, self.step_ids, self.message_ids = 0, set(), set(), set()
        self.usage_by_turn = {}
        self.cache_write_by_turn = {}
        self.native_models = set()
        self.invalid_usage_events = 0

    @staticmethod
    def number(value):
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 and int(value) == value else None

    @staticmethod
    def scope(event):
        return (str(event.get("session_id") or event.get("sessionID") or ""), str(event.get("parent_tool_use_id") or ""))

    def observe(self, event):
        part = event.get("part") or {}
        if event.get("type") == "step_finish":
            identity = ("opencode", *self.scope(event), part.get("messageID") or part.get("id"))
            if identity not in self.step_ids:
                self.step_ids.add(identity)
                self.requests += 1
                self.usage_by_turn[identity] = None
            tokens = part.get("tokens") or {}
            cache = tokens.get("cache") or {}
            native, cached = self.number(tokens.get("input")), self.number(cache.get("read"))
            if native is not None and cached is not None:
                # Match the Harbor adapter's established inclusive prompt counter.
                self.usage_by_turn[identity] = native + cached
            elif tokens:
                self.invalid_usage_events += 1
            self.cache_write_by_turn[identity] = self.number(cache.get("write"))
        if event.get("type") == "tool_use" and part.get("callID"):
            self.calls.add((*self.scope(event), part["callID"]))
        if event.get("type") == "assistant":
            message = event.get("message") or {}
            message_id = message.get("id") or event.get("uuid")
            identity = ("qoder", *self.scope(event), message_id)
            if message_id and identity not in self.message_ids:
                self.message_ids.add(identity)
                self.requests += 1
                self.usage_by_turn[identity] = None
            usage = message.get("usage") or {}
            numeric = {key: self.number(usage.get(key)) for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
            if message_id and any(v is not None and v > 0 for v in numeric.values()):
                # Full thinking/tool blocks repeat one message's usage. Later
                # nonzero snapshots replace earlier ones; masked zeros never do.
                if numeric["input_tokens"] is not None:
                    self.usage_by_turn[identity] = numeric["input_tokens"]
                self.cache_write_by_turn[identity] = numeric["cache_creation_input_tokens"]
            if any(usage.get(key) is not None and numeric[key] is None for key in numeric):
                self.invalid_usage_events += 1
            if isinstance(message.get("model"), str) and message["model"]:
                self.native_models.add(message["model"])
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                    self.calls.add((*self.scope(event), block["id"]))

    def snapshot(self):
        values = list(self.usage_by_turn.values())
        missing = sum(value is None for value in values)
        lower_bound = sum(value for value in values if value is not None)
        writes = list(self.cache_write_by_turn.values())
        return {"model_requests": self.requests, "tool_calls": len(self.calls),
                "input_tokens": lower_bound if values and not missing else None,
                "input_tokens_observed_lower_bound": lower_bound,
                "input_usage_missing_turns": missing, "invalid_usage_events": self.invalid_usage_events,
                "cache_write_tokens": sum(writes) if writes and all(value is not None for value in writes) else None,
                "native_models": sorted(self.native_models),
                "input_convention": "OpenCode native input + cache.read; Qoder native inclusive input. Repeated message snapshots replace, not sum. Cache writes are not added."}

    def exceeded(self, limits):
        snapshot = self.snapshot()
        # Known lower-bound spend can safely stop a run even when another turn's
        # provider usage is missing. Unknown overall usage never becomes zero.
        snapshot["input_tokens"] = snapshot["input_tokens_observed_lower_bound"]
        for key, value in snapshot.items():
            if key in limits and value is not None and value >= limits[key]:
                return key
        return None


def run(spec):
    root = Path(spec.get("log_dir", "/logs"))
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    tap = None
    env = dict(os.environ)
    env.update(spec.get("env", {}))
    if spec.get("tap_upstream"):
        tap = WireTap(root, spec["tap_upstream"])
        local_url = tap.start()
        config = json.loads(Path(spec["config_path"]).read_text())
        config["provider"]["custom-openai"]["options"]["baseURL"] = local_url
        effective = root / "opencode.effective.json"
        save(effective, config)
        env["OPENCODE_CONFIG"] = str(effective)
    counters, lines = Counters(), queue.Queue()
    native_name = spec.get("native_name", "opencode.txt")
    try:
        process = subprocess.Popen(spec["command"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, bufsize=1, env=env, start_new_session=True)
    except OSError as error:
        if tap:
            tap.server.shutdown()
            tap.server.server_close()
        save(root / "session.json", {"status": "launch_failure", "returncode": None,
             "error_type": type(error).__name__, "limits": spec["limits"], "observed": counters.snapshot(),
             "wall_seconds": time.monotonic() - started, "wire_available": tap is not None})
        return 127

    def reader(stream, name, observe):
        with (root / name).open("w") as out:
            for line in stream:
                out.write(scrub(line))
                out.flush()
                if observe:
                    lines.put(line)

    workers = [threading.Thread(target=reader, args=(process.stdout, native_name, True), daemon=True),
               threading.Thread(target=reader, args=(process.stderr, "stderr.txt", False), daemon=True)]
    for worker in workers:
        worker.start()
    limit_reason = None
    while True:
        try:
            line = lines.get(timeout=0.1)
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    counters.observe(event)
            except ValueError:
                pass
        except queue.Empty:
            pass
        if process.poll() is not None and lines.empty():
            break
        if time.monotonic() - started > spec["limits"]["wall_seconds"]:
            limit_reason = "wall_seconds"
        else:
            limit_reason = counters.exceeded(spec["limits"])
        if limit_reason:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            break
    returncode = process.wait()
    for worker in workers:
        worker.join(timeout=5)
    process.stdout.close()
    process.stderr.close()
    # Reconcile final counters from the persisted stream, including events that
    # arrived while a threshold was stopping the child or after its last poll.
    counters = Counters()
    for line in (root / native_name).read_text().splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                counters.observe(event)
        except ValueError:
            pass
    # A fast-exiting child can finish before the queue loop observes its final
    # events. Record actual overshoot even when there is no process left to stop.
    if limit_reason is None:
        observed = counters.snapshot()
        observed["input_tokens"] = observed["input_tokens_observed_lower_bound"]
        limit_reason = next((key for key in ("model_requests", "tool_calls", "input_tokens")
                             if key in spec["limits"] and observed[key] > spec["limits"][key]), None)
    if tap:
        tap.server.shutdown()
        tap.server.server_close()
    result = {"status": "budget_exhausted" if limit_reason else "completed" if returncode == 0 else "failed",
              "returncode": returncode, "limit_reason": limit_reason, "limits": spec["limits"],
              "observed": counters.snapshot(), "wall_seconds": time.monotonic() - started,
              "limit_semantics": "Stop after a native threshold is observed; in-flight/batched work may overshoot.",
              "wire_available": tap is not None}
    save(root / "session.json", result)
    print(json.dumps(result), flush=True)
    return 3 if limit_reason else returncode


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(json.loads(args.spec.read_text())))
