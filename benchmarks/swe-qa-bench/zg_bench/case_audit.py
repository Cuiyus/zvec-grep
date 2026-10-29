"""Link every MCP search to its session, pre-query context and visible model input."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from .case_analysis import decode_response, write_json


def audit(root: Path, source: Path) -> dict:
    rows = [json.loads(line) for line in (root / "requests.jsonl").read_text().splitlines()]
    output = []
    for row in rows:
        prefix = root / row["raw_prefix"]
        agent = prefix.parent.parent.parent
        sessions = json.loads((agent / "full-sessions.json").read_text())
        parts = []
        for record in sessions:
            data = record["data"]
            data = json.loads(data) if isinstance(data, str) else data
            if record["kind"] == "part" and data["content"].get("type") == "tool":
                parts.append(data)
        args = row["request"]["params"].get("arguments", {})
        matches = [p for p in parts if p["content"]["state"].get("input") == args
                   and p["content"].get("tool", "").endswith(row["request"]["params"]["name"])]
        matches.sort(key=lambda p: abs(p["content"]["state"].get("time", {}).get("start", 0) - row["started_ms"]))
        record = {"trial": row["trial"], "wire_id": row["wire_id"], "request": row["request"], "links": {}, "hits": []}
        body = "\n".join(item.get("text", "") for item in row["responses"][0].get("result", {}).get("content", []))
        if matches:
            part = matches[0]
            call_id = part["content"]["callID"]
            record["links"].update(session_id=part["session_id"], message_id=part["message_id"], part_id=part["id"], call_id=call_id,
                                   tool_body_equal=part["content"]["state"].get("output") == body)
            for meta_path in sorted((agent / "wire/model").glob("*.metadata.json")):
                model_prefix = str(meta_path).removesuffix(".metadata.json")
                completion = json.loads(Path(model_prefix + ".completion.json").read_text())
                response = decode_response(Path(model_prefix + ".response.bin"), completion)
                if call_id in response:
                    record["links"]["pre_query_model_request"] = str(Path(model_prefix + ".request.bin").relative_to(root))
                request = json.loads(Path(model_prefix + ".request.bin").read_text())
                for message in request.get("messages", []):
                    if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
                        record["links"].setdefault("first_visible_model_request", str(Path(model_prefix + ".request.bin").relative_to(root)))
                        record["links"].setdefault("model_body_equal", message.get("content") == body)
        blocks = re.split(r"(?m)(?=^#\d+ )", body)
        for block in blocks:
            header = re.match(r"#(\d+).*?matchedBy=\S+ (.+):(\d+)-(\d+)", block)
            if not header:
                continue
            rank, path, start, end = header.groups()
            hit = {"rank": int(rank), "path": path, "start": int(start), "end": int(end),
                   "text": block, "source_mismatches": []}
            source_file = source / path
            if source_file.is_file():
                lines = source_file.read_text(errors="replace").splitlines()
                for match in re.finditer(r"(?m)^(\d+)\t(.*)$", block):
                    number, visible = int(match[1]), match[2]
                    actual = lines[number - 1] if 0 < number <= len(lines) else None
                    if actual != visible:
                        hit["source_mismatches"].append({"line": number, "visible": visible, "source": actual})
            else:
                hit["source_missing"] = True
            record["hits"].append(hit)
        record["analysis"] = "pending_human_source_review"
        output.append(record)
    report = {"queries": output, "total_queries": len(output),
              "all_linked": all(all(k in r["links"] for k in ("pre_query_model_request", "first_visible_model_request")) for r in output),
              "all_tool_bodies_equal": all(r["links"].get("tool_body_equal") and r["links"].get("model_body_equal") for r in output)}
    write_json(root / "query-audit.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.evidence, args.source)
    print(json.dumps({k: v for k, v in result.items() if k != "queries"}))
    return 0 if result["all_linked"] and result["all_tool_bodies_equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
