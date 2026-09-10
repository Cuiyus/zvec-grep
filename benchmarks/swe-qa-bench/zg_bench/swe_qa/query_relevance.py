"""Frozen query-conditioned entry diagnostics, independent of answer grading.

No model is called. An unmatched entry is unreviewed, not proven irrelevant.
The legacy task entry targets remain a separate output and are never replaced.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any

from .retrieval_eval import digest, public_items, score_public_text


CLASSIFICATIONS = {"original", "equivalent_rewrite", "legitimate_subgoal", "ambiguous", "off_task"}
SCORABLE = {"original", "equivalent_rewrite", "legitimate_subgoal"}
KS = (1, 5, 10)


def canonical_request_key(request: dict[str, Any]) -> str:
    """Canonicalize object ordering only. Never parse/rewrite string values."""
    if not isinstance(request, dict):
        raise ValueError("Request must be a JSON object")
    return json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _relative(path: Any) -> bool:
    return isinstance(path, str) and bool(path) and not Path(path).is_absolute() and ".." not in Path(path).parts


def load_labels(path: Path, source_root: Path | None = None) -> dict[str, Any]:
    labels = json.loads(path.read_text(encoding="utf-8"))
    if labels.get("schema_version") != 1 or not re.fullmatch(r"[0-9a-f]{40}", labels.get("repo", {}).get("commit", "")):
        raise ValueError("Invalid query-label schema or pinned source commit")
    files = {f["path"]: f["sha256"] for f in labels["source_files"]}
    for filename, expected in files.items():
        if not _relative(filename) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid source identity")
        if source_root is not None and digest((source_root / filename).read_bytes()) != expected:
            raise ValueError(f"Source file hash mismatch: {filename}")
    evidence_ids: set[str] = set()
    for e in labels["source_evidence"]:
        if e["id"] in evidence_ids or e["path"] not in files or digest(e["text"]) != e["sha256"]:
            raise ValueError("Invalid source evidence identity")
        evidence_ids.add(e["id"])
        if not 1 <= e["start_line"] <= e["end_line"]:
            raise ValueError("Invalid evidence source range")
        if source_root is not None:
            lines = (source_root / e["path"]).read_text(encoding="utf-8").splitlines(keepends=True)
            if "".join(lines[e["start_line"] - 1:e["end_line"]]) != e["text"]:
                raise ValueError("Source evidence text differs from frozen source")
    facts = {f["fact_id"] for f in labels["task_facts"]}
    if len(facts) != 3 or any(not set(f["evidence_ids"]) <= evidence_ids for f in labels["task_facts"]):
        raise ValueError("Expected three source-backed task facts")
    targets: set[str] = set()
    for t in labels["targets"]:
        if t["target_id"] in targets or t["path"] not in files or t["level"] not in {"class", "function"}:
            raise ValueError("Invalid or duplicate query target")
        targets.add(t["target_id"])
        if not set(t["task_fact_ids"]) <= facts or not t["task_fact_ids"]:
            raise ValueError("Query target must connect to a task fact")
        definition = t["definition"]
        prefix = "class " if t["level"] == "class" else "def "
        if (len(definition.splitlines()) != 1 or not definition.strip().startswith(prefix + t["symbol"].split(".")[-1])
                or digest(definition) != t["definition_sha256"]
                or not 1 <= t["entry_start_line"] <= t["definition_line"] <= t["entry_end_line"]):
            raise ValueError("Invalid definition anchor")
        if source_root is not None:
            lines = (source_root / t["path"]).read_text(encoding="utf-8").splitlines(keepends=True)
            if t["definition_line"] > len(lines) or lines[t["definition_line"] - 1] != definition:
                raise ValueError("Definition anchor differs from frozen source")
    query_ids: set[str] = set()
    texts: set[str] = set()
    for q in labels["queries"]:
        if (not isinstance(q["text"], str) or not q["text"] or q["text"] in texts or q["query_id"] in query_ids
                or q["classification"] not in CLASSIFICATIONS):
            raise ValueError("Invalid or duplicate query label")
        query_ids.add(q["query_id"])
        texts.add(q["text"])
        accepted, bridges = set(q["accepted_target_ids"]), set(q["bridge_target_ids"])
        if not (accepted | bridges) <= targets or accepted & bridges:
            raise ValueError("Invalid or overlapping query target roles")
        if q["classification"] in SCORABLE and not accepted:
            raise ValueError("Scorable query requires an accepted OR target")
        if not set(q["task_fact_ids"]) <= facts:
            raise ValueError("Unknown task fact")
    bindings: set[str] = set()
    for b in labels.get("request_bindings", []):
        key = canonical_request_key(b["request"])
        if key in bindings or b["query_id"] not in query_ids:
            raise ValueError("Duplicate request binding or unknown label")
        bindings.add(key)
    original = [q for q in labels["queries"] if q["classification"] == "original"]
    if len(original) != 1 or original[0]["text"] != labels["original_question"]:
        raise ValueError("Original question must be preserved exactly")
    return labels


def _request_texts(request: dict[str, Any]) -> list[str]:
    values = [request.get(k) for k in ("query", "fts", "vector")]
    queries = request.get("queries", [])
    if isinstance(queries, list):
        values += queries
    routes = request.get("routes", [])
    if isinstance(routes, list):
        values += [r.get("query") for r in routes if isinstance(r, dict)]
    return list(dict.fromkeys(v for v in values if isinstance(v, str) and v))


def resolve_label(labels: dict[str, Any], *, query: str | None = None,
                  request: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
    """Explicit query selects controlled-text view; request-only selects faithful view."""
    by_text = {q["text"]: q for q in labels["queries"]}
    if query is not None:
        if not isinstance(query, str):
            return None, "non_string_query"
        return by_text.get(query), "exact_query" if query in by_text else "unknown_exact_query"
    if isinstance(request, dict):
        key = canonical_request_key(request)
        binding = next((b for b in labels.get("request_bindings", []) if canonical_request_key(b["request"]) == key), None)
        if binding:
            return next(q for q in labels["queries"] if q["query_id"] == binding["query_id"]), "exact_canonical_request"
        texts = _request_texts(request)
        if len(texts) == 1:
            return by_text.get(texts[0]), "single_exact_request_text" if texts[0] in by_text else "unknown_request_text"
        return None, "unreviewed_multi_text_request"
    return None, "missing_query_or_request"


def _native_score(text: str, manifest: dict[str, Any], budget: int | None) -> dict[str, Any]:
    # Faithful calls can return 15/20 items. Expand only the parser ceiling;
    # preserve all native ranks, duplicates, and @1/@5/@10 definitions.
    view = copy.deepcopy(manifest)
    view["protocol"]["limit"] = max(10, len(public_items(text)))
    score = score_public_text(text, view, budget)
    score["ranking_view"] = "all native slots retained; Hit@1/5/10 and RR@10 remain fixed"
    score["native_parser_slot_limit"] = view["protocol"]["limit"]
    return score


def _role_metrics(matches: list[dict[str, Any]], target_ids: list[str], known: bool) -> dict[str, Any]:
    hits = [m for m in matches if m["target_id"] in target_ids]
    first = min(hits, key=lambda m: m["rank"]) if hits else None
    rank = first["rank"] if first else None
    return {"status": "scored" if known else "unknown", "matches": hits,
            "first_hit_rank": rank if known else None,
            "bytes_through_first_hit": first["prefix_bytes"] if known and first else None,
            "rr_at_10": (1 / rank if rank is not None and rank <= 10 else 0.0) if known else None,
            **{f"hit_at_{k}": rank is not None and rank <= k if known else None for k in KS}}


def score_query_text(public_text: str, labels: dict[str, Any], task_entries: dict[str, Any], *,
                     query: str | None = None, request: dict[str, Any] | None = None,
                     budget: int | None = None) -> dict[str, Any]:
    """Score only frozen positive targets; never infer new query intent from results.

    Use query= for normalized controlled text replay, request= alone for a faithful
    request. Unknown/ambiguous/off-task queries retain the separate task score.
    A scored miss means no annotated positive target, not all items irrelevant.
    """
    if not isinstance(public_text, str):
        raise ValueError("public_text must be a string")
    if budget is not None and (type(budget) is not int or budget < 1):
        raise ValueError("budget must be a positive UTF-8 byte count")
    if labels["repo"] != task_entries["repo"]:
        raise ValueError("Task and query labels must use the same pinned repository")
    task_score = _native_score(public_text, task_entries, budget)
    label, method = resolve_label(labels, query=query, request=request)
    view = {"protocol": {"limit": 10}, "targets": [dict(t, primary=True) for t in labels["targets"]], "groups": []}
    observations = _native_score(public_text, view, budget)
    matches = observations.get("matches") or []
    known_format = observations["status"] == "scored"
    known_goal = label is not None and label["classification"] in SCORABLE
    accepted = label["accepted_target_ids"] if label else []
    bridges = label["bridge_target_ids"] if label else []
    matched_ranks = {m["rank"] for m in matches if m["target_id"] in accepted + bridges} if known_goal else set()
    visible = public_text.encode()[:budget].decode("utf-8", errors="ignore") if budget is not None else public_text
    unreviewed = [item["rank"] for item in public_items(visible) if item["rank"] not in matched_ranks]
    relevance = {"status": "scored" if known_format and known_goal else "unknown",
                 "label_status": "matched" if label else "unknown", "resolution_method": method,
                 "query_id": label["query_id"] if label else None,
                 "classification": label["classification"] if label else "unknown",
                 "goal": label["goal"] if label else None,
                 "intent_is_annotation_inference": True,
                 "task_fact_ids": label["task_fact_ids"] if label else [],
                 "target": _role_metrics(matches, accepted, known_format and known_goal),
                 "bridge": _role_metrics(matches, bridges, known_format and known_goal),
                 "unreviewed_public_ranks": unreviewed if known_format else None,
                 "exhaustive_relevance_judgments": False,
                 "miss_interpretation": "No annotated positive target observed; unreviewed items are not judged irrelevant.",
                 "native_bytes": observations.get("native_bytes"), "visible_bytes": observations.get("visible_bytes"),
                 "public_text_sha256": observations.get("public_text_sha256"),
                 "ranking_view": observations["ranking_view"]}
    if not known_goal:
        relevance["reason"] = "No reviewed scorable query intent; task-level entry observations remain separate."
    if not known_format:
        relevance["format_reason"] = observations.get("reason")
    return {"schema_version": 1, "labels_id": labels["labels_id"], "budget_bytes": budget,
            "task_entry_score": task_score, "query_relevance": relevance}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--task-entries", type=Path, required=True)
    parser.add_argument("--public-text", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--query")
    source.add_argument("--request-json", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--budget", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from .retrieval_eval import load_manifest
    inputs = [args.labels, args.task_entries, args.public_text] + ([args.request_json] if args.request_json else [])
    if args.output.exists() or args.output.resolve() in {p.resolve() for p in inputs}:
        parser.error("Output must be new and cannot replace an input")
    labels = load_labels(args.labels, args.source_root)
    task = load_manifest(args.task_entries, args.source_root)
    report = score_query_text(args.public_text.read_text(encoding="utf-8"), labels, task, query=args.query,
                              request=json.loads(args.request_json.read_text()) if args.request_json else None,
                              budget=args.budget)
    report["input_sha256"] = {str(p): digest(p.read_bytes()) for p in inputs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
