"""Replay original MCP searches on the archived index, without an agent/model."""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import requests

from .case_analysis import rpc_messages, sha256, write_json


def stable(value):
    # Raw replies remain archived. Exclude only transient index status and timers.
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in {"timings"}}
    if isinstance(value, list):
        return [stable(v) for v in value]
    if isinstance(value, str) and value.startswith("freshness: "):
        lines = value.splitlines()
        while lines and lines[0].startswith(("freshness: ", "results: ", "background_refresh: ")):
            lines.pop(0)
        return "\n".join(lines)
    return value


def key(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def control_params(original: dict, changes: dict) -> dict:
    params = copy.deepcopy(original)
    for name, value in changes.items():
        if value is None:
            params["arguments"].pop(name, None)
        else:
            params["arguments"][name] = value
    return params


class Mcp:
    def __init__(self):
        self.headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        self.next_id = 1
        self.call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "sweqa-case-replay", "version": "1"}})
        self.call("notifications/initialized", {}, notification=True)

    def call(self, method, params, notification=False):
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = self.next_id
            self.next_id += 1
        response = requests.post("http://127.0.0.1:7999/mcp", json=payload, headers=self.headers, timeout=(10, 600))
        response.raise_for_status()
        if session := response.headers.get("Mcp-Session-Id"):
            self.headers["Mcp-Session-Id"] = session
        if notification:
            return None
        messages = rpc_messages(response.text)
        matches = [m for m in messages if m.get("id") == payload["id"]]
        if len(matches) != 1:
            raise RuntimeError("missing MCP response")
        if "error" in matches[0]:
            raise RuntimeError("MCP error: " + json.dumps(matches[0]["error"]))
        if method == "initialize":
            self.headers["Mcp-Protocol-Version"] = matches[0]["result"]["protocolVersion"]
        return {"request": payload, "response": matches[0], "raw_response": response.text}


@contextmanager
def server(binary: Path, directory: Path, index: Path, diagnostic: bool):
    directory.mkdir(parents=True, exist_ok=True)
    destination = Path("/app/.zvec-grep")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(index, destination)
    environment = dict(os.environ, ZVEC_GREP_DEVICE="cpu")
    if diagnostic:
        environment["ZG_CASE_DIAGNOSTICS"] = str(directory / "stages.jsonl")
    with (directory / "server.log").open("w") as log:
        process = subprocess.Popen([str(binary), "--server", "run", "--listen", "127.0.0.1:7999",
                                    "--home", str(directory / "daemon")],
                                   cwd="/app", env=environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(180):
                if process.poll() is not None:
                    raise RuntimeError("replay server exited; inspect server.log")
                try:
                    with socket.create_connection(("127.0.0.1", 7999), timeout=.2):
                        break
                except OSError:
                    time.sleep(.5)
            else:
                raise RuntimeError("replay server readiness timeout")
            yield Mcp()
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--production", type=Path, required=True)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-commit", required=True)
    args = parser.parse_args()
    if not os.environ.get("GITHUB_ACTIONS") or Path("/app/.git").exists():
        parser.error("run only in a fresh CI runner, with /app absent")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    lock_path, = args.evidence.rglob("run-lock.json")
    lock = json.loads(lock_path.read_text())
    if lock["zg_commit"] != args.candidate_commit:
        raise RuntimeError("replay product commit differs from E2E lock")
    case = lock["case"]
    subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", case["repository_url"], "/app"], check=True)
    subprocess.run(["git", "-C", "/app", "checkout", "--detach", case["repository_commit"]], check=True)
    ignore_source = Path(__file__).parent / "swe_qa/data/index.ignore"
    shutil.copyfile(ignore_source, "/app/.zg-bench-index.ignore")
    with Path("/app/.git/info/exclude").open("a") as exclude:
        exclude.write("\n.zvec-grep/\n.zg-bench-index.ignore\n")
    requests_path = lock_path.parent / "requests.jsonl"
    rows = [json.loads(line) for line in requests_path.read_text().splitlines()]
    unique = {}
    for row in rows:
        params = row["request"]["params"]
        if params["name"] != "zvec_grep_search":
            continue
        group = unique.setdefault(key(params), {"params": params, "occurrences": []})
        group["occurrences"].append({"trial": row["trial"], "wire_id": row["wire_id"],
                                     "result": row["responses"][0].get("result")})
    if not unique:
        raise RuntimeError("no search calls to replay")
    seeds = [p.parent for p in args.evidence.rglob("identity.json") if (p.parent / "complete").exists()]
    if len(seeds) != 1:
        raise RuntimeError("expected exactly one immutable index seed")
    identity = json.loads((seeds[0] / "identity.json").read_text())
    if identity["index_ignore_sha256"] != sha256(ignore_source):
        raise RuntimeError("index ignore rules differ from recorded seed")
    workspace = seeds[0] / "workspace"
    index = workspace / ".zvec-grep" if (workspace / ".zvec-grep").is_dir() else workspace
    write_json(output / "replay-lock.json", {"source_lock": lock, "query_count": len(unique), "repeats": 10,
        "production_sha256": sha256(args.production), "diagnostic_sha256": sha256(args.diagnostic),
        "seed_identity": json.loads((seeds[0] / "identity.json").read_text()),
        "index_files": [{"path": str(p.relative_to(index)), "sha256": sha256(p)} for p in sorted(index.rglob("*")) if p.is_file()]})
    write_json(output / "queries.json", unique)
    summaries = []
    with server(args.production.resolve(), output / "production", index, False) as client:
        for query_id, group in unique.items():
            results = []
            for repeat in range(1, 11):
                reply = client.call("tools/call", group["params"])
                write_json(output / "production" / query_id / f"{repeat:02}.json", reply)
                results.append(stable(reply["response"].get("result")))
            summaries.append({"query_id": query_id, "stable_10": all(r == results[0] for r in results),
                              "matches_e2e": all(stable(o["result"]) == results[0] for o in group["occurrences"]),
                              "production_result": results[0]})
            print(f"production replay {query_id}: 10/10 captured", flush=True)
    with server(args.diagnostic.resolve(), output / "diagnostic", index, True) as client:
        for summary in summaries:
            query_id = summary["query_id"]
            stages = output / "diagnostic/stages.jsonl"
            start = stages.stat().st_size if stages.exists() else 0
            reply = client.call("tools/call", unique[query_id]["params"])
            write_json(output / "diagnostic" / query_id / "response.json", reply)
            end = stages.stat().st_size if stages.exists() else 0
            summary["diagnostic_parity"] = stable(reply["response"].get("result")) == summary.pop("production_result")
            summary["stage_byte_range"] = [start, end]
            summary["stage_capture"] = end > start
            print(f"diagnostic {query_id}: parity={summary['diagnostic_parity']}", flush=True)
            controls = {"full_preview": {"preview": "full"}, "limit_50": {"limit": 50}}
            arguments = unique[query_id]["params"]["arguments"]
            if isinstance(arguments.get("query"), str) and not any(k in arguments for k in ("queries", "fts", "vector")):
                controls["fts_only"] = {"query": None, "fts": arguments["query"]}
                controls["vector_only"] = {"query": None, "vector": arguments["query"]}
            for name, changes in controls.items():
                params = control_params(unique[query_id]["params"], changes)
                start = stages.stat().st_size if stages.exists() else 0
                reply = client.call("tools/call", params)
                end = stages.stat().st_size if stages.exists() else 0
                write_json(output / "diagnostic" / query_id / (name + ".json"),
                           {**reply, "stage_byte_range": [start, end], "control": name})
    from .case_audit import audit
    from .case_ranking import analyze
    links = audit(lock_path.parent, Path("/app"))
    write_json(output / "query-audit.json", links)
    evidence_ready = (links["all_linked"] and links["all_tool_bodies_equal"]
                      and all(r["diagnostic_parity"] and r["stage_capture"] for r in summaries))
    write_json(output / "summary.json", {"queries": summaries, "evidence_ready": evidence_ready,
        "ready": evidence_ready and all(r["stable_10"] and r["matches_e2e"] for r in summaries),
        "note": "CI success checks evidence/parity, not answer correctness. Replay instability or E2E differences remain findings requiring review."})
    analyze(output)
    return 0 if evidence_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
