#!/usr/bin/env python3
"""Capture a real installed OpenCode initial request using a local fake responder.

Only the request builder is exercised. The fake response is not a model result
and is never scored as an E2E sample. No model credentials are required.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import importlib.util
import json
import os
import threading
from pathlib import Path


def capture(spec: dict) -> int:
    if spec["agent"] != "opencode":
        raise ValueError("Qoder native full-request capture is not verified")
    destination = Path(spec.get("log_dir", "/logs"))
    destination.mkdir(parents=True, exist_ok=True)
    intended = spec.get("tap_upstream")

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            content = "Offline native request capture complete."
            common = {"id": "offline-capture", "object": "chat.completion.chunk", "created": 1,
                      "model": body["model"]}
            chunks = [dict(common, choices=[{"index": 0, "delta": {"role": "assistant", "content": content},
                                            "finish_reason": None}]),
                      dict(common, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}],
                           usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})]
            payload = ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime_path = Path(__file__).with_name("native-agent-session.py")
    module_spec = importlib.util.spec_from_file_location("native_capture_runtime", runtime_path)
    runtime = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(runtime)
    captured_spec = dict(spec, tap_upstream=f"http://127.0.0.1:{server.server_port}/v1")
    old_key = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "offline-native-capture"
    try:
        result = runtime.run(captured_spec)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if old_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_key
    if result != 0:
        raise ValueError(f"Native offline capture exited {result}; no valid capture produced")
    events = [json.loads(line) for line in (destination / "wire.jsonl").read_text().splitlines() if line]
    first = next((e for e in events if e.get("event") == "request" and e.get("tool_names")), None)
    if first is None or first.get("raw_body_redacted"):
        raise ValueError("Native initial request is missing or redacted")
    raw = (destination / first["raw_body_path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != first["request_sha256"]:
        raise ValueError("Native request bytes differ from transport capture")
    body = json.loads(raw)
    expected_temperature = spec.get("base_config", {}).get("agent", {}).get("build", {}).get("temperature")
    expected_seed = spec.get("model_seed")
    if body.get("temperature") != expected_temperature or body.get("seed") != expected_seed:
        raise ValueError("Native request did not preserve the configured temperature and model seed")
    installed = json.loads((destination / "install-manifest.json").read_text())
    guidance = installed.get("guidance_text") or installed.get("installed_guidance_text")
    if not guidance or not any(guidance.strip() in text for text in runtime.instruction_texts(body)):
        raise ValueError("Actual model request does not contain installed guidance")
    manifest = {"schema_version": 1, "status": "captured", "capture_only": True,
                "agent": "opencode", "model": body["model"], "intended_endpoint": intended,
                "actual_endpoint": captured_spec["tap_upstream"], "paid_model_calls": 0,
                "first_request": first, "installed_guidance_verified": True,
                "sampling_controls_verified": True,
                "temperature": body.get("temperature"), "model_seed": body.get("seed"),
                "not_an_e2e_sample": True, "native_exit_code": result,
                "limitation": "Real installed agent request construction; the responder was local and fake."}
    (destination / "capture-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(capture(json.loads(args.spec.read_text())))
