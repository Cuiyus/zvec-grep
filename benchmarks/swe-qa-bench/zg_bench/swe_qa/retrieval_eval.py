"""Offline entry-point localization diagnostics from PUBLIC retrieval text only.

The answer's original evidence certificate is never redefined here. Targets
identify useful places to continue reading, not sufficient answer evidence.
Repeated executions estimate stability; repetition one supplies each query's
quality observation. No model, production query, or tokenizer is invoked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any

LEVELS = ("file", "class", "function")
KS = (1, 5, 10)
HEADER = re.compile(r"^#(?P<rank>[1-9]\d*)[^\n]*? (?P<path>[^\s:]+):(?P<start>\d+)-(?P<end>\d+)\s*$", re.M)
NUMBERED = re.compile(r"^\s*(\d+)(?:\t|: ?)(.*)$")


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _path(value: str) -> str:
    return value.removeprefix("/app/").removeprefix("./")


def _relative(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and not Path(value).is_absolute() and ".." not in Path(value).parts


def load_manifest(path: Path, source_root: Path | None = None) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported entry manifest schema")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest.get("repo", {}).get("commit", "")):
        raise ValueError("A pinned source commit is required")
    protocol = manifest["protocol"]
    if protocol.get("quality_repetition") != 1 or protocol.get("limit") != 10:
        raise ValueError("This protocol uses repetition one and the original top ten ranks")
    if protocol.get("modes") != ["fts", "vector", "hybrid"]:
        raise ValueError("Expected fts/vector/hybrid modes")
    if type(protocol.get("repetitions")) is not int or protocol["repetitions"] < 1:
        raise ValueError("Invalid repetition count")
    budgets = protocol.get("byte_budgets", [])
    if not budgets or len(set(budgets)) != len(budgets) or any(type(b) is not int or b < 1 for b in budgets):
        raise ValueError("Byte budgets must be unique positive integers")
    query_ids: set[str] = set()
    query_texts: set[str] = set()
    for query in manifest["queries"]:
        if not query.get("query_id") or query["query_id"] in query_ids or not query.get("text") or query["text"] in query_texts:
            raise ValueError("Query IDs and exact query texts must be unique and nonempty")
        if query.get("intent_id") != manifest["intent_id"]:
            raise ValueError("This development manifest has one underlying intent")
        query_ids.add(query["query_id"])
        query_texts.add(query["text"])
    if protocol.get("primary_query_id") not in query_ids:
        raise ValueError("A primary query is required")
    files = {item["path"]: item["sha256"] for item in manifest["source_files"]}
    for filename, expected in files.items():
        if not _relative(filename) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid source file identity")
        if source_root is not None and digest((source_root / filename).read_bytes()) != expected:
            raise ValueError(f"Frozen source file hash mismatch: {filename}")
    ids: set[str] = set()
    for target in manifest["targets"]:
        ident = target.get("target_id")
        if not ident or ident in ids or target.get("level") not in LEVELS or target.get("path") not in files:
            raise ValueError("Invalid or duplicate entry target")
        ids.add(ident)
        if type(target.get("primary")) is not bool:
            raise ValueError("Target primary flag must be explicit")
        if target["level"] == "file":
            continue
        start, line, end = (target.get(k) for k in ("entry_start_line", "definition_line", "entry_end_line"))
        if any(type(n) is not int for n in (start, line, end)) or not 1 <= start <= line <= end:
            raise ValueError(f"Invalid source location: {ident}")
        definition = target.get("definition", "")
        prefix = "class " if target["level"] == "class" else "def "
        name = target.get("symbol", "").split(".")[-1]
        if not definition.strip().startswith(prefix + name) or len(definition.splitlines()) != 1 or digest(definition) != target.get("definition_sha256"):
            raise ValueError(f"Invalid definition anchor: {ident}")
        if source_root is not None:
            actual = (source_root / target["path"]).read_text(encoding="utf-8").splitlines(keepends=True)
            if line > len(actual) or actual[line - 1] != definition:
                raise ValueError(f"Definition anchor differs from frozen source: {ident}")
    group_ids: set[str] = set()
    for group in manifest["groups"]:
        if not group.get("group_id") or group["group_id"] in group_ids or not group.get("target_ids") or not set(group["target_ids"]) <= ids:
            raise ValueError("Invalid entry alternative group")
        group_ids.add(group["group_id"])
        if any(t["level"] != group["level"] for t in manifest["targets"] if t["target_id"] in group["target_ids"]):
            raise ValueError("Alternative groups cannot mix file/class/function levels")
    return manifest


def public_items(text: str) -> list[dict[str, Any]]:
    """Keep native slots and byte prefixes, including duplicate retrieval items."""
    headers = [m for m in HEADER.finditer(text) if m.end() < len(text) and text[m.end()] == "\n"]
    items = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        items.append({"rank": int(header["rank"]), "path": _path(header["path"]),
                      "start_line": int(header["start"]), "end_line": int(header["end"]),
                      "text": text[header.end() + 1:end], "prefix_bytes": len(text[:end].encode("utf-8"))})
    return items


def _block_matches(block: dict[str, Any], manifest: dict[str, Any], *, file_address_visible: bool) -> list[dict[str, Any]]:
    matches = []
    # A cut in the middle of a definition line never creates a visible anchor.
    complete_lines = [line.rstrip("\r\n") for line in block["text"].splitlines(keepends=True) if line.endswith(("\n", "\r"))]
    numbered = [(int(m[1]), m[2].strip()) for line in complete_lines if (m := NUMBERED.match(line))]
    outline = block["text"].split("outline:\n", 1)[1].split("source:\n", 1)[0] if "outline:\n" in block["text"] else ""
    outline_lines = {line.strip() for line in outline.splitlines(keepends=True) if line.endswith(("\n", "\r"))}
    for target in manifest["targets"]:
        if target["path"] != block["path"]:
            continue
        kind = None
        if target["level"] == "file" and file_address_visible:
            kind = "visible_file_address"
        elif target["level"] != "file":
            anchor = target["definition"].strip()
            if (target["definition_line"], anchor) in numbered:
                kind = "numbered_definition"
            elif (anchor in outline_lines and block.get("start_line") == target["entry_start_line"]
                  and block.get("end_line") == target["entry_end_line"]):
                # An exact function/class outline at its own address is useful;
                # a parent range merely containing that symbol is not a hit.
                kind = "definition_outline_at_exact_entry"
        if kind:
            matches.append({"target_id": target["target_id"], "level": target["level"],
                            "path": target["path"], "symbol": target.get("symbol"),
                            "primary": target["primary"], "match_kind": kind,
                            "rank": block.get("rank"), "prefix_bytes": block.get("prefix_bytes")})
    return matches


def match_visible_entries(text: str, manifest: dict[str, Any], *, path_hint: str | None = None) -> list[dict[str, Any]]:
    """Shared retrieval/E2E detector. path_hint may only be the actual read path.

    Numbered read observations and public context entries are supported. Hidden
    result.items, hit ranges, function-name mentions, and answer prose are not.
    Duplicates retain their native rank; consumers may deduplicate target IDs.
    """
    if not isinstance(text, str):
        return []
    items = public_items(text)
    if items:
        return [match for item in items for match in _block_matches(item, manifest, file_address_visible=True)]
    if not isinstance(path_hint, str):
        path_hint = None
    # Native OpenCode grep names a file, then prints `Line N: source`. Keep
    # each file block separate; a symbol mention elsewhere remains a miss.
    headers = list(re.finditer(r"^([^\s:]+\.py):[ \t]*$", text, re.M))
    grep_matches = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        body = re.sub(r"^[ \t]*Line (\d+): ?", r"\1: ", text[header.end():end], flags=re.M)
        grep_matches.extend(_block_matches({"path": _path(header[1]), "text": body}, manifest, file_address_visible=True))
    if headers:
        return grep_matches
    visible_path = re.search(r"<path>([^<]+)</path>", text)
    path = _path(visible_path[1]) if visible_path else _path(path_hint) if path_hint else None
    if path is None:
        return []
    # A conflicting read argument cannot confer the wrong source identity.
    if path_hint and visible_path and _path(path_hint) != path:
        return []
    block = {"path": path, "text": text}
    return _block_matches(block, manifest, file_address_visible=bool(visible_path or path_hint))


def _unknown(reason: str) -> dict[str, Any]:
    return {"status": "unknown", "reason": reason, "visible_bytes": None, "matches": None,
            "levels": {level: {"first_hit_rank": None, "rr_at_10": None, "bytes_through_first_hit": None,
                                **{f"hit_at_{k}": None for k in KS}} for level in LEVELS}, "groups": None}


def score_public_text(text: str, manifest: dict[str, Any], budget: int | None = None) -> dict[str, Any]:
    native_items = public_items(text)
    ranks = [item["rank"] for item in native_items]
    if ranks != list(range(1, len(ranks) + 1)) or len(ranks) > manifest["protocol"]["limit"]:
        return _unknown("Malformed/nonsequential public result ranks")
    empty = text.strip() in {"freshness: fresh", "freshness: fresh\nNo results.", "freshness: fresh\nNo matches."}
    if not native_items and not empty:
        return _unknown("Public result format is not recognized; hidden results are not a fallback")
    raw = text.encode("utf-8")
    visible = raw[:budget].decode("utf-8", errors="ignore") if budget is not None else text
    items = public_items(visible)
    # Parse each public slot directly. Budget-cut text is never treated as a
    # standalone read observation, even if its content includes path tags.
    matches = [m for item in items for m in _block_matches(item, manifest, file_address_visible=True)]
    levels = {}
    for level in LEVELS:
        hits = [m for m in matches if m["level"] == level and m["primary"]]
        first = min(hits, key=lambda m: m["rank"]) if hits else None
        rank = first["rank"] if first else None
        levels[level] = {"first_hit_rank": rank, "rr_at_10": 1 / rank if rank is not None and rank <= 10 else 0.0,
                         "bytes_through_first_hit": first["prefix_bytes"] if first else None,
                         **{f"hit_at_{k}": rank is not None and rank <= k for k in KS}}
    groups = {}
    for group in manifest["groups"]:
        group_ranks = [m["rank"] for m in matches if m["target_id"] in group["target_ids"]]
        rank = min(group_ranks) if group_ranks else None
        groups[group["group_id"]] = {"role": group["role"], "level": group["level"], "first_hit_rank": rank,
                                     **{f"hit_at_{k}": rank is not None and rank <= k for k in KS}}
    return {"status": "scored", "budget_bytes": budget, "native_bytes": len(raw),
            "visible_bytes": len(visible.encode("utf-8")), "truncated": visible != text,
            "public_slots_visible": len(items), "public_text_sha256": digest(visible),
            "matches": matches, "levels": levels, "groups": groups,
            "optional_function_group_coverage": {
                f"at_{k}": sum(g[f"hit_at_{k}"] for g in groups.values() if g["role"] == "optional" and g["level"] == "function") /
                max(1, sum(g["role"] == "optional" and g["level"] == "function" for g in groups.values())) for k in KS}}


def load_events(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events, errors, packages = [], [], {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("not an object")
        except ValueError:
            errors.append({"line": number, "error": "invalid JSON object"})
            continue
        if event.get("event") == "start":
            packages[event.get("run_id")] = event.get("package")
        if event.get("event") == "search":
            event["_package"] = event.get("package") or packages.get(event.get("run_id"))
            event["_line"] = number
            events.append(event)
    return events, errors


def _query_text(event: dict[str, Any]) -> str | None:
    request = event.get("request", {})
    if not isinstance(request, dict):
        return None
    if isinstance(request.get("query"), str):
        return request["query"]
    routes = request.get("routes", [])
    if isinstance(routes, list) and len(routes) == 1 and isinstance(routes[0], dict):
        return routes[0].get("query") if isinstance(routes[0].get("query"), str) else None
    return None


def _event_error(event: dict[str, Any], manifest: dict[str, Any]) -> str | None:
    if event.get("status") != "success":
        return "Query execution did not succeed"
    source = event.get("source_identity")
    if not isinstance(source, dict) or source.get("git_commit") != manifest["repo"]["commit"]:
        return "Missing or different source commit"
    package = event.get("_package") or event.get("package") or {}
    if not isinstance(package, dict) or f"{package.get('name')}@{package.get('version')}" != manifest["protocol"]["package"]:
        return "Missing or different production package identity"
    index = event.get("index_identity")
    embedding = index.get("embedding") if isinstance(index, dict) else None
    if not isinstance(embedding, dict) or f"{embedding.get('provider')}/{embedding.get('model')}" != manifest["protocol"]["embedding_model"]:
        return "Missing or different embedding identity"
    request = event.get("request", {})
    if request.get("limit") != manifest["protocol"]["limit"] or request.get("autoUpdate") is not False:
        return "Query limit/autoUpdate differs from frozen protocol"
    routes = request.get("routes")
    if routes and (not isinstance(routes, list) or len(routes) != 1 or not isinstance(routes[0], dict) or routes[0].get("mode") != event.get("mode")):
        return "Route mode does not match event mode"
    if not isinstance(event.get("text"), str):
        return "Public text is missing"
    if event.get("text_sha256") and digest(event["text"]) != event["text_sha256"]:
        return "Public text hash mismatch"
    return None


def evaluate(events: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    protocol = manifest["protocol"]
    queries = {q["text"]: q for q in manifest["queries"]}
    slots: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    unassigned = []
    for event in events:
        query = queries.get(_query_text(event))
        mode, repetition = event.get("mode"), event.get("repetition")
        if (query is None or mode not in protocol["modes"] or type(repetition) is not int
                or not 1 <= repetition <= protocol["repetitions"] or event.get("query_id", query["query_id"]) != query["query_id"]):
            unassigned.append({"line": event.get("_line"), "reason": "Unknown query, conflicting query ID, mode, or repetition"})
            continue
        slots.setdefault((query["query_id"], mode, repetition), []).append(event)
    budgets = {"native": None, **{f"bytes_{b}": b for b in protocol["byte_budgets"]}}
    runs = []
    for query in manifest["queries"]:
        for mode in protocol["modes"]:
            for repetition in range(1, protocol["repetitions"] + 1):
                candidates = slots.get((query["query_id"], mode, repetition), [])
                event = candidates[0] if len(candidates) == 1 else None
                error = "Missing planned query execution" if not candidates else "Duplicate events for the same planned slot" if len(candidates) > 1 else _event_error(event, manifest)
                runs.append({"query_id": query["query_id"], "intent_id": query["intent_id"], "stratum": query.get("stratum"),
                             "mode": mode, "repetition": repetition, "quality_observation": repetition == 1,
                             "event_line": event.get("_line") if event else None,
                             "execution_status": event.get("status") if event else "missing" if not candidates else "ambiguous",
                             "duration_ms": event.get("duration_ms") if event and not error else None,
                             "context_duration_ms": event.get("context_duration_ms") if event and not error else None,
                             "scores": {key: _unknown(error) if error else score_public_text(event["text"], manifest, budget)
                                        for key, budget in budgets.items()}})
    quality, stability = [], []
    for query in manifest["queries"]:
        for mode in protocol["modes"]:
            repeated = [r for r in runs if r["query_id"] == query["query_id"] and r["mode"] == mode]
            first = repeated[0]
            quality.append({"query_id": query["query_id"], "stratum": query.get("stratum"), "mode": mode,
                            "primary_query": query["query_id"] == protocol["primary_query_id"],
                            "quality_samples": 1, "scores": first["scores"]})
            known = [r for r in repeated if r["scores"]["native"]["status"] == "scored"]
            latencies = [r["duration_ms"] for r in known if isinstance(r["duration_ms"], (int, float)) and not isinstance(r["duration_ms"], bool)]
            stability.append({"query_id": query["query_id"], "mode": mode, "planned_repeats": protocol["repetitions"],
                              "scored_repeats": len(known), "unknown_repeats": len(repeated) - len(known),
                              "distinct_public_texts": len({r["scores"]["native"]["public_text_sha256"] for r in known}),
                              "public_text_identical_all_repeats": len({r["scores"]["native"]["public_text_sha256"] for r in known}) == 1 if len(known) == len(repeated) else None,
                              "entry_rank_signatures": {level: [r["scores"]["native"]["levels"][level]["first_hit_rank"] for r in known] for level in LEVELS},
                              "first_duration_ms": first["duration_ms"],
                              "later_duration_ms": [r["duration_ms"] for r in repeated[1:]],
                              "latency_median_ms": statistics.median(latencies) if latencies else None,
                              "latency_min_ms": min(latencies) if latencies else None,
                              "latency_max_ms": max(latencies) if latencies else None,
                              "latency_policy": "First query includes process cold-load work; later queries may be warm. Repeats are not quality cases."})
    aggregates = []
    for mode in protocol["modes"]:
        for budget in budgets:
            for level in LEVELS:
                selected = [q["scores"][budget] for q in quality if q["mode"] == mode]
                known = [s["levels"][level] for s in selected if s["status"] == "scored"]
                aggregates.append({"mode": mode, "budget": budget, "level": level,
                                   "scope": "Descriptive mean across correlated formulations of one intent; not an independent-case estimate",
                                   "planned_formulations": len(selected), "scored_formulations": len(known), "independent_intents": 1,
                                   "mrr_at_10": statistics.mean(m["rr_at_10"] for m in known) if len(known) == len(selected) else None,
                                   "observed_formulations_mrr_at_10": statistics.mean(m["rr_at_10"] for m in known) if known else None,
                                   **{f"hit_rate_at_{k}": statistics.mean(m[f"hit_at_{k}"] for m in known) if len(known) == len(selected) else None for k in KS}})
    return {"schema_version": 1, "case_id": manifest["case_id"], "manifest_id": manifest["manifest_id"],
            "repo": manifest["repo"], "protocol": protocol, "annotation_provenance": manifest["annotation_provenance"],
            "independent_intents": 1, "planned_query_formulations": len(manifest["queries"]),
            "planned_executions": len(runs), "primary_query_id": protocol["primary_query_id"],
            "query_quality": quality, "descriptive_formulation_aggregates": aggregates,
            "repeat_stability": stability, "runs": runs, "unassigned_events": unassigned,
            "limitations": manifest["limitations"]}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Entry localization diagnostic: {report['case_id']}", "",
             "One underlying QA intent. Original question is primary; other formulations are related subintent probes.",
             "Targets were authored after inspecting the old artifact. Its rescoring is post-hoc; this is not a held-out quality estimate.", "",
             "Function is the primary entry level. File/class hits are separate and never upgraded to function hits.", "",
             "| Query | Mode | Output budget | Function Hit@1 / 5 / 10 | RR@10 | First function rank | Bytes through hit | File / class rank |",
             "|---|---|---|---|---:|---:|---:|---|"]
    def display(value: Any) -> str:
        if value is None:
            return "unknown / no hit"
        return f"{value:.3f}" if isinstance(value, float) else str(value)
    for query in report["query_quality"]:
        for budget, score in query["scores"].items():
            f = score["levels"]["function"]
            hit = "unknown" if score["status"] != "scored" else " / ".join("1" if f[f"hit_at_{k}"] else "0" for k in KS)
            ranks = " / ".join(display(score["levels"][level]["first_hit_rank"]) for level in ("file", "class"))
            lines.append(f"| {query['query_id']} | {query['mode']} | {budget} | {hit} | {display(f['rr_at_10'])} | {display(f['first_hit_rank'])} | {display(f['bytes_through_first_hit'])} | {ranks} |")
    lines += ["", "RR/Hit use repetition 1 only; repeats do not increase the quality denominator. Missing or invalid observations remain unknown.",
              "Native text is reported separately. Byte budgets cut the UTF-8 public prefix without reranking or dropping duplicate slots.",
              "Bytes through the first hit include the entire visible hit item; a miss has no such value. Bytes are not model tokens.",
              "Optional OR-group coverage and all per-repeat observations are in the JSON; completing every group is not a QA requirement.", "",
              "| Query | Mode | Scored repeats | Identical public text across all repeats | First duration ms | Later durations ms |",
              "|---|---|---:|---|---:|---|"]
    for row in report["repeat_stability"]:
        lines.append(f"| {row['query_id']} | {row['mode']} | {row['scored_repeats']}/{row['planned_repeats']} | {display(row['public_text_identical_all_repeats'])} | {display(row['first_duration_ms'])} | {', '.join(display(n) for n in row['later_duration_ms'])} |")
    lines += ["", *[f"- {item}" for item in report["limitations"]], ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve() in {args.events.resolve(), args.manifest.resolve()}:
        parser.error("Output cannot replace an input")
    manifest = load_manifest(args.manifest, args.source_root)
    events, parse_errors = load_events(args.events)
    report = evaluate(events, manifest)
    report.update(manifest_sha256=digest(args.manifest.read_bytes()), events_sha256=digest(args.events.read_bytes()),
                  event_parse_errors=parse_errors, source_files_verified=args.source_root is not None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "planned_executions": report["planned_executions"],
                      "independent_intents": 1, "parse_errors": len(parse_errors)}))


if __name__ == "__main__":
    main()
