"""Evidence-only helpers. No transcript execution, approval, grading, or network."""
from pathlib import Path, PurePosixPath
import collections
import hashlib
import json
import re


def sha(value):
    return hashlib.sha256(value.read_bytes() if isinstance(value, Path) else value).hexdigest()


def ref(path):
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}


def load(path):
    return json.loads(path.read_text())


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def safe_source_path(path, manifest):
    if not isinstance(path, str):
        return None
    rel = path.removeprefix("/workspace/")
    if path.startswith("/") and not path.startswith("/workspace/"):
        return None
    parsed = PurePosixPath(rel)
    if ".." in parsed.parts or parsed.is_absolute() or str(parsed) != rel:
        return None
    return rel if rel in manifest else None


def read_check(args, result, source_lines, manifest):
    """Retain failed/outside/malformed Read evidence; never assume missing == absent."""
    rel = safe_source_path(args.get("file_path"), manifest)
    check = {"source_path": rel, "path_in_frozen_manifest": rel is not None,
             "all_returned_lines_exact": None, "metadata_exact": None,
             "returned_line_checks": [], "diagnostics": []}
    if rel is None:
        check["diagnostics"].append("Read path is outside or not a frozen file; manually resolve and assess the attempt.")
        return check
    lines = source_lines[rel]
    metadata = result.get("metadata")
    file = metadata.get("file") if isinstance(metadata, dict) else None
    if isinstance(file, dict):
        start, count = file.get("startLine"), file.get("numLines")
        if type(start) is int and type(count) is int and start >= 1 and count >= 0:
            check["metadata_exact"] = (
                file.get("filePath") == args.get("file_path")
                and file.get("totalLines") == len(lines)
                and start == args.get("offset", 1)
                and file.get("content") == "\n".join(lines[start - 1:start - 1 + count]))
    content = result.get("content")
    if isinstance(content, str):
        for n, row in enumerate(content.splitlines(), 1):
            match = re.fullmatch(r"(\d+)\t(.*)", row)
            number = int(match[1]) if match else None
            actual = lines[number - 1] if number and number <= len(lines) else None
            check["returned_line_checks"].append({
                "output_line": n, "source_line": number, "returned_text": row,
                "source_text": actual,
                "exact": bool(match and actual is not None and match[2] == actual)})
        rows = check["returned_line_checks"]
        check["all_returned_lines_exact"] = (
            bool(rows) and all(row["exact"] for row in rows)
            and isinstance(file, dict) and len(rows) == file.get("numLines"))
    if check["metadata_exact"] is not True or check["all_returned_lines_exact"] is not True:
        check["diagnostics"].append("Read return needs manual explanation, including errors or empty returns.")
    return check


SPECIFIC_STRUCT_SPANS = {
    ("src/include/zvec/db/options.h", 56, 58),
    ("src/db/index/column/vector_column/vector_column_params.h", 72, 75),
    ("src/include/zvec/core/interface/index_param.h", 54, 57),
}


def mcp_text_check(text, source_lines, manifest):
    """Compare by hit identity and UTF-8 byte offsets, never a global line-number lookup."""
    data = text.encode()
    groups = re.search(r"^query groups \((\d+)\):$", text, re.M)
    out = {"query_groups_reported": int(groups[1]) if groups else None,
           "text_sha256": sha(data), "text_bytes": len(data),
           "hits": [], "unparsed_rows": [], "manual_relevance_required": True}
    current = None
    offset = 0
    for raw in text.splitlines(keepends=True):
        row = raw.removesuffix("\n")
        header = re.match(r"^#(\d+).*? ([^\s:]+):(\d+)(?:-(\d+))?$", row)
        numbered = re.match(r"^(\d+)(?:[:-])?\t(.*)$", row)
        if header:
            path = header[2]
            current = {"rank": int(header[1]), "path": path,
                       "start_line": int(header[3]), "end_line": int(header[4] or header[3]),
                       "header": row, "header_byte_offset": offset,
                       "path_in_frozen_manifest": safe_source_path(path, manifest) is not None,
                       "source_file_sha256": manifest.get(path, {}).get("sha256"),
                       "lines": [], "preview_ellipsis": False,
                       "material_relevance": None}
            out["hits"].append(current)
        elif numbered:
            if current is None:
                out["unparsed_rows"].append({"byte_offset": offset, "text": row, "reason": "numbered row without hit"})
            else:
                num, returned = int(numbered[1]), numbered[2]
                lines = source_lines.get(current["path"], [])
                actual = lines[num - 1] if 0 < num <= len(lines) else None
                kind = ("source_unavailable" if actual is None else
                        "exact" if returned == actual else
                        "leading_indentation_omitted" if returned == actual.lstrip() else
                        "non_whitespace_difference")
                current["lines"].append({
                    "line": num, "comparison": kind, "returned_text": returned,
                    "source_text": actual, "returned_text_sha256": sha(returned.encode()),
                    "source_text_sha256": sha(actual.encode()) if actual is not None else None,
                    "result_text_byte_offset": offset, "result_text_byte_length": len(raw.encode()),
                    "within_declared_span": current["start_line"] <= num <= current["end_line"]})
        elif row == "..." and current is not None:
            current["preview_ellipsis"] = True
        elif row and row != "source:" and not row.startswith((
                "freshness:", "results:", "background_refresh:", "query groups (", "  Q",
                "selection:", "groups:", "heading:", "heading_level:")):
            out["unparsed_rows"].append({"byte_offset": offset, "text": row, "reason": "unclassified format"})
        offset += len(raw.encode())
    for hit in out["hits"]:
        values = []
        for row in hit["lines"]:
            raw = data[row["result_text_byte_offset"]:
                       row["result_text_byte_offset"] + row["result_text_byte_length"]]
            parsed = re.match(rb"^(\d+)(?:[:-])?\t(.*?)(?:\n)?$", raw, re.S)
            row["byte_offset_reconstruction_exact"] = bool(
                parsed and int(parsed[1]) == row["line"]
                and parsed[2].decode() == row["returned_text"])
            values.append(row["returned_text"])
        lines = source_lines.get(hit["path"], [])
        span = "\n".join(lines[hit["start_line"] - 1:hit["end_line"]])
        snippet = "\n".join(values)
        hit["complete_displayed_snippet"] = snippet
        hit["whole_snippet_is_exact_source_prefix"] = bool(lines and span.startswith(snippet))
        for row in hit["lines"]:
            if row["comparison"] == "non_whitespace_difference":
                row["at_declared_hit_end"] = row["line"] == hit["end_line"]
                row["last_displayed_line"] = row is hit["lines"][-1]
                row["returned_is_exact_source_prefix"] = row["source_text"].startswith(row["returned_text"])
                row["specific_fixed_cpp_boundary_candidate"] = bool(
                    (hit["path"], hit["start_line"], hit["end_line"]) in SPECIFIC_STRUCT_SPANS
                    and row["source_text"] == "};" and row["returned_text"] == "}"
                    and row["at_declared_hit_end"] and row["last_displayed_line"]
                    and snippet == span.removesuffix(";")
                    and hit["whole_snippet_is_exact_source_prefix"])
                row["scope_approved_by_this_match"] = False
                row["manual_fixed_implementation_and_span_check_required"] = True
    out["line_comparison_counts"] = dict(collections.Counter(
        row["comparison"] for hit in out["hits"] for row in hit["lines"]))
    out["hit_count"] = len(out["hits"])
    return out


def bash_hints(args, result, source_lines, manifest):
    """Match literal returns for review; this is not a shell interpreter or scope verdict."""
    command = args.get("command")
    metadata = result.get("metadata")
    stdout = metadata.get("stdout") if isinstance(metadata, dict) else None
    content = result.get("content")
    text = stdout if isinstance(stdout, str) else content if isinstance(content, str) else None
    out = {"full_command": command, "shell_semantics_review_required": True,
           "metadata": metadata, "native_content": content,
           "stdout_equals_native_content": stdout == content if isinstance(stdout, str) else None,
           "source_matches_are_candidates_not_access_proof": True,
           "numbered_source_rows": [], "unclassified_output_rows": [],
           "do_not_compare_pipeline_ordinal_to_sorted_glob_order": True}
    if not isinstance(text, str):
        return out
    for number, row in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r"(?:(\d+):)?((?:/workspace/)?[^:\s]+):(\d+)[:-](.*)", row)
        if match:
            rel = safe_source_path(match[2], manifest)
            ln = int(match[3])
            actual = source_lines[rel][ln - 1] if rel and 0 < ln <= len(source_lines[rel]) else None
            out["numbered_source_rows"].append({
                "output_line": number, "text": row, "source_path": rel, "source_line": ln,
                "returned_text": match[4], "source_text": actual,
                "source_text_exact": actual is not None and actual == match[4],
                "pipeline_input_ordinal": int(match[1]) if match[1] else None,
                "pipeline_input_ordinal_not_a_source_line": bool(match[1]),
                "requires_binding_to_full_command_operands": True})
        else:
            out["unclassified_output_rows"].append({"output_line": number, "text": row})
    return out


def crosscheck(queue):
    """Enumerate all actual raw tool blocks; non-tool answer content is never emitted."""
    agent = Path(queue["agent_directory"])
    actual = [agent / "claude-code.txt", *sorted((agent / "sessions/projects").rglob("*.jsonl"))]
    observed, extracted = set(), set()
    errors, files = [], []
    if {p.relative_to(agent).as_posix() for p in actual} != {x["path"] for x in queue["files"]}:
        errors.append("native raw file inventory mismatch")
    for path in actual:
        if path.is_symlink():
            errors.append("raw symlink: " + str(path))
            continue
        data = path.read_bytes()
        relative = path.relative_to(agent).as_posix()
        files.append(ref(path))
        record = next((f for f in queue["files"] if f["path"] == relative), {})
        if len(data) != record.get("bytes") or sha(data) != record.get("sha256"):
            errors.append("raw byte/hash mismatch: " + relative)
        byte_offset = 0
        for line_no, line in enumerate(data.splitlines(keepends=True), 1):
            try:
                event = json.loads(line)
            except ValueError:
                errors.append(f"malformed JSONL: {relative}:{line_no}")
                byte_offset += len(line)
                continue
            if event.get("type") in ("assistant", "user"):
                blocks = event.get("message", {}).get("content", [])
                if isinstance(blocks, list):
                    for i, block in enumerate(blocks):
                        if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                            observed.add((relative, line_no, i, sha(line), byte_offset, len(line)))
            byte_offset += len(line)
    for tool in queue["tools"]:
        for field in ("call_variants", "result_variants"):
            for variant in tool[field]:
                for loc in variant["locations"]:
                    extracted.add((loc["file"], loc["line"], loc["block_index"],
                                   loc["raw_line_sha256"], loc["byte_offset"], loc["byte_length"]))
                    try:
                        raw = (agent / loc["file"]).read_bytes()
                        line = raw[loc["byte_offset"]:loc["byte_offset"] + loc["byte_length"]]
                        if sha(raw) != loc["file_sha256"] or sha(line) != loc["raw_line_sha256"]:
                            errors.append("location hash mismatch")
                        if json.loads(line)["message"]["content"][loc["block_index"]] != variant["block"]:
                            errors.append("location block mismatch")
                    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                        errors.append("location verification failure: " + str(exc))
    if observed != extracted:
        errors.append("raw/extracted tool occurrence sets differ")
    return {"all_match": not errors, "errors": errors, "raw_files": files,
            "actual_tool_occurrences": len(observed), "extracted_tool_occurrences": len(extracted),
            "unmatched_raw_occurrences": sorted(observed - extracted),
            "extra_extracted_occurrences": sorted(extracted - observed)}
