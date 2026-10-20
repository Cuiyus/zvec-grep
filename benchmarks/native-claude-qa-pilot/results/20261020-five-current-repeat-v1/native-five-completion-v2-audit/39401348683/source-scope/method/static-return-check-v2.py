"""Complete static returned-text provenance map; not a shell interpreter or scope approval."""
from pathlib import Path, PurePosixPath
import collections
import hashlib
import json
import re
import shlex
O = Path(__file__).parent
M = O
cfg = json.loads((M / "pair-inputs.json").read_text())
S = Path(cfg["source_root"])
manifest = json.loads(Path(cfg["source_manifest"]["path"]).read_text())
sm = {r["path"]: r for r in manifest["files"]}
ls = {p: (S / p).read_text(errors="replace").splitlines() for p in sm}
def sha(b):
    return hashlib.sha256(b).hexdigest()
def ref(p):
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p.read_bytes())}
def put(p, obj):
    with p.open("x") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
reviews, unknown = [], []
for profile in ("baseline", "zvec-grep"):
    q = json.loads((O / profile / "extracted/tool-audit.json").read_text())
    for t in q["tools"]:
        if t["name"] != "Bash":
            continue
        c, r = t["call_variants"][0], t["result_variants"][0]
        cmd, meta = c["input"]["command"], r["metadata"]
        assert len(t["call_variants"]) == len(t["result_variants"]) == 1
        expressions = re.findall(r"/workspace(?:/[A-Za-z0-9_.?*+/-]+)?", cmd)
        relative = re.findall(r"(?<![A-Za-z0-9_./])(?:src|tests|python)(?:/[A-Za-z0-9_.?*+/-]+)+", cmd)
        expressions += ["/workspace/" + p for p in relative]
        if re.search(r"(?:^|[;&|]\s*)\s*(?:ls(?:\s+-[al]+)?|pwd)\s*(?:$|[;&|])", cmd):
            expressions.append("/workspace")
        requested, files, names = [], set(), set()
        for e in expressions:
            rel = e.removeprefix("/workspace").lstrip("/")
            if ".." in PurePosixPath(rel).parts:
                requested.append({"expression": e, "parent_traversal_requires_review": True})
                continue
            matches = list(S.glob(rel)) if "*" in rel or "?" in rel else [S / rel]
            matches = [p for p in matches if p.exists()]
            requested.append({"expression": e, "matched_source_paths": [p.relative_to(S).as_posix() for p in matches], "missing_in_frozen_tree": not matches})
            for p in matches:
                if p.is_file():
                    files.add(p.relative_to(S).as_posix())
                elif p.is_dir():
                    names.update(x.name for x in p.iterdir())
                    files.update(x.relative_to(S).as_posix() for x in p.rglob("*") if x.is_file())
        assert files.issubset(sm)
        echoes = []
        for m in re.finditer(r"(?:^|[;&])\s*echo\s+([^;|&]+)", cmd):
            try:
                tokens = shlex.split(m[1])
                if tokens and not tokens[0].startswith("-"):
                    echoes.append(" ".join(tokens))
            except ValueError:
                pass
        text_to_matches = collections.defaultdict(list)
        for p in sorted(files):
            for no, text in enumerate(ls[p], 1):
                if text:
                    text_to_matches[text].append({"path": p, "line": no})
        rows, classes = [], collections.Counter()
        streams = {k: meta.get(k, "") for k in ("stdout", "stderr")} if isinstance(meta, dict) else {"rendered_error_content": r["content"]}
        for stream, stream_text in streams.items():
            offset = 0
            for n, rawline in enumerate(stream_text.splitlines(keepends=True), 1):
                text = rawline.rstrip("\r\n")
                row = {"stream": stream, "output_line": n, "text": text, "text_sha256": sha(text.encode()), "byte_offset": offset, "byte_length": len(rawline.encode())}
                offset += len(rawline.encode())
                path = text.removeprefix("/workspace/").rstrip(":")
                numbered = re.fullmatch(r"(?:(/workspace/.*?|(?:src|tests|python)/.*?)[:-])?(\d+)[:-](.*)", text)
                nl = re.fullmatch(r"\s*(\d+)\t(.*)", text)
                nested = re.fullmatch(r"(\d+):/workspace/(.*?):(\d+):(.*)", text)
                if re.fullmatch(r"Exit code \d+", text) and r["is_error"] and not isinstance(meta, dict):
                    kind = "native_error_exit_status_header"
                elif not text:
                    kind = "blank_line"
                elif text in echoes:
                    kind = "literal_echo"
                elif text == "--" and ("grep " in cmd or "rg " in cmd):
                    kind = "grep_context_separator_candidate"
                elif text == "/workspace":
                    kind = "workspace_pwd_or_directory"
                elif text.startswith("/workspace/") and (S / path).exists():
                    kind = "frozen_tree_path_or_heading"
                    row["source_path"] = path
                elif text in names:
                    kind = "frozen_directory_entry_name"
                elif text in text_to_matches:
                    kind = "exact_source_line"
                    row["source_matches"] = text_to_matches[text]
                elif nested:
                    p, nsrc = nested[2], int(nested[3])
                    if p in files and 0 < nsrc <= len(ls[p]) and ls[p][nsrc - 1] == nested[4]:
                        kind = "exact_source_with_intermediate_stream_ordinal"
                        row["source_matches"] = [{"path": p, "line": nsrc}]
                        row["intermediate_stream_ordinal"] = int(nested[1])
                        row["intermediate_order_not_reconstructed_or_scope_gate"] = True
                    else:
                        kind = "unclassified"
                elif numbered or nl:
                    number = int(numbered[2] if numbered else nl[1])
                    source_text = numbered[3] if numbered else nl[2]
                    candidates = [numbered[1].removeprefix("/workspace/")] if numbered and numbered[1] else files
                    matches = [{"path": p, "line": number} for p in candidates if p in files and 0 < number <= len(ls[p]) and ls[p][number - 1] == source_text]
                    if matches:
                        kind = "exact_numbered_source_line"
                        row["source_matches"] = matches
                    else:
                        kind = "unclassified"
                elif re.fullmatch(r"(?:grep|sed|cat|ls|find|rg): .*", text):
                    kind = "command_diagnostic_requires_semantic_review"
                elif re.match(r"^(?:total \d+|[dl-][rwx-]{9}\s)", text):
                    kind = "ls_metadata_requires_semantic_review"
                elif re.fullmatch(r"\s*\d+\s+(?:/workspace/\S+|total)", text):
                    kind = "wc_metadata_requires_semantic_review"
                else:
                    kind = "unclassified"
                row["classification"] = kind
                classes[kind] += 1
                if kind == "unclassified":
                    unknown.append({"profile": profile, "ordinal": t["ordinal"], **row})
                rows.append(row)
        reviews.append({
            "profile": profile, "ordinal": t["ordinal"], "tool_id": t["id"],
            "command": cmd, "input_sha256": c["input_sha256"], "result_block_sha256": r["block_sha256"],
            "full_native_result_content": r["content"], "full_native_metadata": meta,
            "stdout_sha256": sha(meta.get("stdout", "").encode()) if isinstance(meta, dict) else None, "stderr_sha256": sha(meta.get("stderr", "").encode()) if isinstance(meta, dict) else None,
            "content_matches_stdout": r["content"] == meta.get("stdout") if isinstance(meta, dict) else None,
            "is_error": r["is_error"], "declared_source_operands": requested,
            "tool_evidence": ref(O / profile / "extracted" / t["evidence_path"]),
            "all_returned_lines": rows, "line_classes": dict(classes),
            "matched_source_files": [ref(S / p) for p in sorted({m["path"] for row in rows for m in row.get("source_matches", [])})],
            "semantic_approval": "pending",
        })
result = {"pair": O.name, "script": ref(Path(__file__)), "all_bash_results_read_completely": True,
          "solver_commands_executed": False, "reviews": reviews, "unclassified_lines": unknown,
          "tool_count": len(reviews), "returned_lines": sum(len(r["all_returned_lines"]) for r in reviews)}
put(O / "static-return-checks.json", result)
print(json.dumps({"pair": O.name, "bash": len(reviews), "lines": result["returned_lines"], "unclassified": unknown,
 "special": [{"profile": r["profile"], "ordinal": r["ordinal"], "classes": r["line_classes"], "content_matches_stdout": r["content_matches_stdout"]}
             for r in reviews if not r["content_matches_stdout"] or any("requires" in k or "candidate" in k for k in r["line_classes"])]}, ensure_ascii=False))
