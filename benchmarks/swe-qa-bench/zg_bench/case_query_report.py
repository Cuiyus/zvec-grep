"""Create local, source-verified navigation for captured ZG search requests.

This exports evidence and reusable static source notes, not completed reviews of
the current requests. It never writes to the source checkout or raw evidence.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlparse


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def contained(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Path escapes input root: {name}")
    return path


def md_link(label: str, path: Path, parent: Path) -> str:
    return f"[{label}](<{os.path.relpath(path, parent)}>)"


def fence(text: str, language: str = "") -> str:
    marker = "`" * max(3, max((len(s) + 1 for s in re.findall(r"`+", text)), default=3))
    return f"{marker}{language}\n{text}\n{marker}"


class SourceNotes:
    def __init__(self, source: Path | None, prior: dict):
        self.source = source
        self.prior = prior.get("entities", {})
        self.files: dict[str, bytes | None] = {}
        self.historical: dict[tuple[str, str], bytes | None] = {}

    def inspect(self, path: str, start: int, end: int) -> tuple[dict, str | None]:
        note = {"status": "pending", "reason": "no_matching_prior_source_annotation"}
        if self.source is None:
            note["reason"] = "source_not_supplied"
            return note, None
        if path not in self.files:
            file = contained(self.source, path)
            self.files[path] = file.read_bytes() if file.is_file() else None
        data = self.files[path]
        if data is None:
            note["reason"] = "source_file_missing"
            return note, None
        lines = data.decode("utf-8", errors="replace").splitlines()
        if not (1 <= start <= end <= len(lines)):
            note["reason"] = "source_range_invalid"
            return note, None
        snippet = "\n".join(lines[start - 1:end])
        note.update(source_file_sha256=digest(data), source_snippet_sha256=digest(snippet.encode()))
        entity = f"{path}:{start}-{end}"
        prior = self.prior.get(entity)
        if not prior:
            return note, snippet
        if any(prior.get(k) != v for k, v in (("path", path), ("start", start), ("end", end))):
            note["reason"] = "prior_entity_identity_mismatch"
            return note, snippet
        # The original review's source_sha256 hashes the line slice without a
        # trailing newline, NOT the whole source file. Validate both separately.
        expected_snippet = prior.get("source_snippet_sha256", prior.get("source_sha256"))
        if expected_snippet != note["source_snippet_sha256"]:
            note["reason"] = "prior_source_snippet_hash_mismatch_or_missing"
            return note, snippet
        expected_file = prior.get("source_file_sha256")
        if not expected_file:
            url = urlparse(prior.get("source_url", ""))
            match = re.fullmatch(r"/[^/]+/[^/]+/blob/([0-9a-f]{40})/(.+)", unquote(url.path))
            if not match or match[2] != path:
                note["reason"] = "prior_source_file_hash_unverifiable"
                return note, snippet
            commit = match[1]
            cache_key = (commit, path)
            if cache_key not in self.historical:
                result = subprocess.run(
                    ["git", "-C", str(self.source), "show", f"{commit}:{path}"],
                    capture_output=True, check=False,
                )
                self.historical[cache_key] = result.stdout if result.returncode == 0 else None
            historical = self.historical[cache_key]
            if historical is None:
                note["reason"] = "prior_source_git_object_unavailable"
                return note, snippet
            expected_file = digest(historical)
            note["prior_source_commit"] = commit
        note["prior_source_file_sha256"] = expected_file
        if expected_file != note["source_file_sha256"]:
            note["reason"] = "prior_source_file_hash_mismatch"
            return note, snippet
        if not prior.get("relevance") or not prior.get("reason"):
            note["reason"] = "prior_static_annotation_empty"
            return note, snippet
        note.update(status="source_annotation_reused", reason=prior["reason"],
                    relevance=prior["relevance"], prior_entity=entity,
                    source_url=prior.get("source_url"),
                    reuse_scope="static_source_fragment_only; current_query_relevance_pending")
        return note, snippet


def export_navigation(audit: Path, evidence: Path, output: Path,
                      prior_source_review: Path | None = None, source: Path | None = None) -> dict:
    audit, evidence, output = audit.resolve(), evidence.resolve(), output.resolve()
    source = source.resolve() if source else None
    if output.is_relative_to(evidence) or (source and output.is_relative_to(source)):
        raise ValueError("Output must be outside raw evidence and source checkout")
    data = json.loads(audit.read_text())
    queries = data["queries"]
    prior = json.loads(prior_source_review.read_text()) if prior_source_review else {}
    notes = SourceNotes(source, prior)
    raw_index = {}
    requests_file = evidence / "requests.jsonl"
    if requests_file.is_file():
        for line in requests_file.read_text().splitlines():
            row = json.loads(line)
            raw_index[(row["trial"], row["wire_id"])] = row
    # Do not overwrite a previous navigation directory with hand-written notes.
    output.mkdir(parents=True, exist_ok=False)
    rows, hit_rows, entities, params_seen = [], [], set(), set()
    for query in queries:
        request = query["request"]
        params = request["params"]
        if params.get("name") != "zvec_grep_search":
            continue
        number = len(rows) + 1
        canonical = json.dumps(params, sort_keys=True, ensure_ascii=False)
        query_id = digest(canonical.encode())[:16]
        params_seen.add(canonical)
        directory = output / "requests" / f"{number:03d}-{query_id}"
        directory.mkdir(parents=True)
        write_json(directory / "request.json", request)
        links = dict(query.get("links", {}))
        references = {}
        for name in ("pre_query_model_request", "first_visible_model_request"):
            if links.get(name):
                references[name] = contained(evidence, links[name])
        raw = raw_index.get((query["trial"], query["wire_id"]))
        if raw:
            prefix = contained(evidence, raw["raw_prefix"])
            references.update(mcp_request=Path(str(prefix) + ".request.bin"),
                              mcp_response=Path(str(prefix) + ".response.bin"),
                              full_sessions=prefix.parent.parent.parent / "full-sessions.json")
        raw_links = {k: {"path": str(v), "exists": v.is_file()} for k, v in references.items()}
        row = {"number": number, "trial": query["trial"], "wire_id": query["wire_id"],
               "query_id": query_id, "query": params.get("arguments", {}).get("query"),
               "request": request, "links": links, "raw_links": raw_links,
               "request_review_status": "pending", "hits": [],
               "report": str((directory / "README.md").relative_to(output))}
        text = [f"# 请求 {number} · {query['trial']}",
                "当前请求审阅：**pending**。复用注释仅描述相同源码片段，不表示本次检索已审阅。",
                f"Wire ID: `{query['wire_id']}` · 参数 ID: `{query_id}`",
                "## 查询与完整 MCP 参数", fence(json.dumps(params, ensure_ascii=False, indent=2), "json"),
                "## 原始证据", fence(json.dumps(links, ensure_ascii=False, indent=2), "json")]
        for label, info in raw_links.items():
            text.append(f"- {md_link(label, Path(info['path']), directory)}"
                        + ("" if info["exists"] else " **缺失**"))
        if not raw:
            text.append("MCP 原始文件定位缺失：requests.jsonl 没有匹配记录。")
        text.extend(["## 返回项", "全文预览保留原返回块；源码摘录是本地辅助证据，不代表模型看过。"])
        for position, hit in enumerate(query.get("hits", []), 1):
            path, start, end = hit["path"], hit["start"], hit["end"]
            entity = f"{path}:{start}-{end}"
            entities.add((path, start, end))
            note, snippet = notes.inspect(path, start, end)
            preview_file = directory / f"hit-{position:03d}-preview.txt"
            preview_file.write_text(hit.get("text", ""))
            item = {**hit, "entity": entity, "source_annotation": note,
                    "current_query_review_status": "pending",
                    "preview": str(preview_file.relative_to(output))}
            item.pop("text", None)
            if snippet is not None:
                source_file = directory / f"hit-{position:03d}-source.txt"
                source_file.write_text(snippet + "\n")
                item["source_excerpt"] = str(source_file.relative_to(output))
            row["hits"].append(item)
            hit_rows.append({"request_number": number, "trial": query["trial"], "query_id": query_id,
                             "rank": hit["rank"], "path": path, "start": start, "end": end,
                             "annotation_status": note["status"], "relevance": note.get("relevance", ""),
                             "reason": note["reason"], "current_query_review_status": "pending",
                             "preview": item["preview"]})
            text.extend([f"### #{hit['rank']} · `{entity}`",
                         md_link("全文预览（原始返回块）", preview_file, directory),
                         f"静态源码注释：**{note['status']}** · {note.get('relevance', '')}",
                         note["reason"]])
            if snippet is not None:
                text.append(md_link("固定源码完整片段（辅助证据）", source_file, directory))
            text.append(fence(json.dumps(note, ensure_ascii=False, indent=2), "json"))
        row["hit_count"] = len(row["hits"])
        row["source_annotations_reused"] = sum(
            hit["source_annotation"]["status"] == "source_annotation_reused" for hit in row["hits"])
        row["source_annotations_pending"] = row["hit_count"] - row["source_annotations_reused"]
        (directory / "README.md").write_text("\n\n".join(text) + "\n")
        write_json(directory / "request-review.json", row)
        rows.append(row)
    summary = {"actual_calls": len(rows), "unique_params": len(params_seen),
               "hit_occurrences": len(hit_rows), "unique_path_ranges": len(entities),
               "source_annotation_reused_occurrences": sum(r["source_annotations_reused"] for r in rows),
               "source_annotation_pending_occurrences": sum(r["source_annotations_pending"] for r in rows),
               "current_request_reviews_completed": 0, "current_request_reviews_pending": len(rows),
               "current_request_review_complete": False,
               "skipped_non_search_audit_rows": len(queries) - len(rows)}
    report = {"schema_version": 1, "audit": str(audit), "audit_sha256": digest(audit.read_bytes()),
              "evidence": str(evidence), "source": str(source) if source else None,
              "prior_source_review": str(prior_source_review.resolve()) if prior_source_review else None,
              "summary": summary,
              "scope": "navigation_only; no new request relevance or causal judgments",
              "unique_params_definition": "full request.params, including MCP _meta; excludes JSON-RPC id",
              "requests": rows}
    write_json(output / "index.json", report)
    fields = ["number", "trial", "wire_id", "query_id", "query", "hit_count",
              "source_annotations_reused", "source_annotations_pending", "request_review_status", "report"]
    with (output / "index.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    with (output / "hits.csv").open("w", newline="") as stream:
        fields = ["request_number", "trial", "query_id", "rank", "path", "start", "end",
                  "annotation_status", "relevance", "reason", "current_query_review_status", "preview"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(hit_rows)
    readme = ["# ZG 检索审阅导航", "本目录是待审导航，不是已完成的检索质量分析。",
              fence(json.dumps(summary, ensure_ascii=False, indent=2), "json"),
              "参数去重包含完整 MCP params（包括 _meta），与回放参数 ID 口径一致。",
              "`source_annotation_reused` 仅复用整文件和片段 hash 均通过的旧静态源码注释。"
              "当前查询的相关性、预览问题、模型利用和因果关系均待独立审阅。",
              "| 请求 | Run ID | 返回项 | 复用静态注释 | 待补静态注释 |", "|---|---|---:|---:|---:|"]
    for row in rows:
        link = md_link(str(row["number"]), output / row["report"], output)
        readme.append(f"| {link} | {row['trial']} | {row['hit_count']} | "
                      f"{row['source_annotations_reused']} | {row['source_annotations_pending']} |")
    (output / "README.md").write_text("\n\n".join(readme[:5]) + "\n\n" + "\n".join(readme[5:]) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prior-source-review", type=Path)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    print(json.dumps(export_navigation(args.audit, args.evidence, args.output,
                                      args.prior_source_review, args.source), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
