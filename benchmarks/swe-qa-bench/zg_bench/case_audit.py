"""Link every MCP search to its session, pre-query context and visible model input."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from .case_analysis import decode_response, rpc_messages, write_json


def emitted_tool_ids(body: str) -> set[str]:
    ids = {}
    for event in rpc_messages(body):
        for choice in event.get("choices", []):
            for tool in choice.get("delta", choice.get("message", {})).get("tool_calls", []):
                if tool.get("id"):
                    index = (choice.get("index", 0), tool.get("index", 0))
                    ids[index] = ids.get(index, "") + tool["id"]
    return set(ids.values())


def _display_match(actual: str | None, visible: str, *, first: bool, short: bool) -> str:
    if actual == visible:
        return "exact"
    if actual is None:
        return "source_line_missing"
    # Keep these differences in the report; do not erase them by stripping text.
    if visible.endswith("\r") and visible[:-1] == actual:
        return "line_ending"
    if first and visible and actual.endswith(visible):
        removed = actual[:-len(visible)]
        return "leading_indentation_removed" if removed.isspace() else "fragment_prefix_clipped"
    if short and len(visible) == 163 and visible.endswith("..."):
        prefix = visible[:-3]
        if len(actual) > 160 and actual.startswith(prefix):
            return "short_line_truncation"
        # A fragment can begin inside a source line and then hit the short limit.
        offset = actual.find(prefix) if first else -1
        if offset > 0 and len(actual) - offset > 160:
            return "fragment_prefix_clipped_and_short_line_truncation"
    return "unexplained_difference"


def parse_hits(body: str, source: Path, *, preview: str = "short") -> list[dict]:
    """Parse legacy/detail and current compact hits, preserving query groups."""
    markers = list(re.finditer(r"(?m)^(?:#\d+\b[^\n]*|-- query [^\n]*)$", body))
    group = {"index": 1, "total": 1, "query": None}
    hits = []
    for index, marker in enumerate(markers):
        heading = marker.group().rstrip("\r")
        if heading.startswith("-- query "):
            match = re.fullmatch(r"-- query (\d+)/(\d+): (.*) --", heading)
            if not match:
                raise ValueError("unrecognized query group header: " + heading)
            group = {"index": int(match[1]), "total": int(match[2]), "query": match[3]}
            if not 1 <= group["index"] <= group["total"]:
                raise ValueError("invalid query group numbering")
            continue
        match = re.fullmatch(r"#(\d+)\s+(?:.*?matchedBy=\S+\s+)?(.+):(\d+)-(\d+)", heading)
        if not match:
            # A changed format must fail visibly instead of reporting zero hits.
            raise ValueError("unrecognized search hit header: " + heading)
        rank, path, start, end = match.groups()
        stop = markers[index + 1].start() if index + 1 < len(markers) else len(body)
        block = body[marker.start():stop]
        hit = {"rank": int(rank), "group": dict(group), "path": path,
               "start": int(start), "end": int(end), "text": block,
               "source_mismatches": [], "source_display_differences": []}
        source_file = source / path
        if source_file.is_file():
            lines = source_file.read_text(errors="replace").splitlines()
            numbered = list(re.finditer(r"(?m)^(?:(\d+)\t|  (\d+): )(.*)$", block))
            for position, line in enumerate(numbered):
                number, visible = int(line[1] or line[2]), line[3]
                actual = lines[number - 1] if 0 < number <= len(lines) else None
                kind = _display_match(actual, visible, first=position == 0, short=preview == "short")
                if kind != "exact":
                    difference = {"line": number, "visible": visible, "source": actual,
                                  "expected": actual, "match_type": kind}
                    hit["source_display_differences"].append(difference)
                    if kind in {"unexplained_difference", "source_line_missing"}:
                        hit["source_mismatches"].append(difference)
            hit["source_lines_checked"] = len(numbered)
        else:
            hit["source_missing"] = True
        hits.append(hit)
    return hits


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
                if call_id in emitted_tool_ids(response):
                    record["links"]["pre_query_model_request"] = str(Path(model_prefix + ".request.bin").relative_to(root))
                request = json.loads(Path(model_prefix + ".request.bin").read_text())
                for message in request.get("messages", []):
                    if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
                        record["links"].setdefault("first_visible_model_request", str(Path(model_prefix + ".request.bin").relative_to(root)))
                        record["links"].setdefault("model_body_equal", message.get("content") == body)
        record["hits"] = parse_hits(body, source, preview=args.get("preview", "short"))
        record["analysis"] = "pending_human_source_review"
        output.append(record)
    report = {"queries": output, "total_queries": len(output),
              "total_hits": sum(len(r["hits"]) for r in output),
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
    return 0 if result["all_linked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
