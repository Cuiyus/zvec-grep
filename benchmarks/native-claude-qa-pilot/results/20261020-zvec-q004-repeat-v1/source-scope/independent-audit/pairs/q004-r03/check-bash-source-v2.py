"""Static q004-r03 returned-source validation. Never executes trajectory commands."""
from pathlib import Path, PurePosixPath
import collections
import hashlib
import json
import re

O = Path(__file__).parent
M = O.parent.parent
config = json.loads((M / "frozen-method-inputs.json").read_text())
S = Path(config["source_root"])
manifest = json.loads(Path(config["source_manifest"]["path"]).read_text())
sm = {x["path"]: x for x in manifest["files"]}
assert len(sm) == 1596
lines = {p: (S / p).read_text(errors="replace").splitlines() for p in sm}
def sha(b):
    return hashlib.sha256(b).hexdigest()
def ref(p):
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p.read_bytes())}
def write(p, x):
    with p.open("x") as f:
        json.dump(x, f, ensure_ascii=False, indent=2)
        f.write("\n")
reviews = []
issues = []
for profile in ("baseline", "zvec-grep"):
    q = json.loads((O / profile / "extracted/tool-audit.json").read_text())
    for t in q["tools"]:
        if t["name"] != "Bash":
            continue
        assert len(t["call_variants"]) == len(t["result_variants"]) == 1
        n = t["ordinal"]
        c = t["call_variants"][0]
        r = t["result_variants"][0]
        cmd = c["input"]["command"]
        meta = r["metadata"]
        assert meta["stderr"] == "" and meta["interrupted"] is False
        wrapper = r["content"] != meta["stdout"]
        if wrapper:
            assert profile == "baseline" and n == 32
            assert meta["stdout"] == "" and r["content"] == "(Bash completed with no output)"
        expressions = re.findall(r"/workspace(?:/[A-Za-z0-9_.?*+/-]+)?", cmd)
        # The sole implicit operand is the source-local `pwd && ls -la`.
        if profile == "baseline" and n == 23:
            assert cmd == "pwd && ls -la"
            expressions.append("/workspace")
        targets, requested, entry_names = set(), [], set()
        for expression in expressions:
            assert ".." not in PurePosixPath(expression).parts
            rel = expression.removeprefix("/workspace").lstrip("/")
            matches = list(S.glob(rel)) if any(x in rel for x in "*?") else [S / rel]
            matches = [p for p in matches if p.exists()]
            requested.append({
                "expression": expression,
                "frozen_matches": [p.relative_to(S).as_posix() for p in matches],
                "no_frozen_match": not matches,
            })
            for p in matches:
                if p.is_file():
                    targets.add(p.relative_to(S).as_posix())
                else:
                    entry_names.update(v.name for v in p.iterdir())
                    targets.update(v.relative_to(S).as_posix() for v in p.rglob("*") if v.is_file())
        assert targets.issubset(sm)
        output_checks = []
        offset = 0
        counts = collections.Counter()
        for output_no, rawline in enumerate(meta["stdout"].splitlines(keepends=True), 1):
            text = rawline.rstrip("\r\n")
            item = {
                "output_line": output_no, "text": text, "text_sha256": sha(text.encode()),
                "stdout_byte_offset": offset, "stdout_byte_length": len(rawline.encode()),
            }
            offset += len(rawline.encode())
            relpath = text.removeprefix("/workspace/").rstrip(":")
            numbered = re.fullmatch(r"(?:(/workspace/.*?)[:-])?(\d+)[:-](.*)", text)
            wc = re.fullmatch(r"\s*(\d+)\s+(/workspace/\S+|total)", text)
            if not text:
                kind = "blank_line"
            elif text == "/workspace":
                kind = "source_workspace_pwd_or_directory"
            elif text.startswith("/workspace/") and (S / relpath).exists():
                kind = "frozen_source_path_or_directory_header"
                item["source_path"] = relpath
            elif wc:
                kind = "line_count_metadata"
                if wc[2] == "total":
                    assert profile == "baseline" and n == 28 and int(wc[1]) == 902
                    item["calculated_source_newlines"] = sum((S / p).read_bytes().count(b"\n") for p in targets)
                else:
                    p = wc[2].removeprefix("/workspace/")
                    assert p in targets
                    item["source_path"] = p
                    item["calculated_source_newlines"] = (S / p).read_bytes().count(b"\n")
                assert item["calculated_source_newlines"] == int(wc[1])
            elif profile == "baseline" and n == 23 and re.match(r"^(total \d+|[dl-][rwx-]{9}\s)", text):
                kind = "source_local_ls_permissions_size_time_metadata"
                if not text.startswith("total "):
                    name = text.split()[-1]
                    assert name in entry_names or name in (".", "..")
                    item["entry_name"] = name
                    item["parent_dot_is_incidental_metadata"] = name == ".."
            elif text in entry_names:
                kind = "frozen_directory_entry_name"
            elif numbered:
                candidates = [numbered[1].removeprefix("/workspace/")] if numbered[1] else sorted(targets)
                found = [
                    {"path": p, "line": int(numbered[2])}
                    for p in candidates if p in targets
                    and 0 < int(numbered[2]) <= len(lines[p])
                    and lines[p][int(numbered[2]) - 1] == numbered[3]
                ]
                if found:
                    kind = "exact_numbered_source_line"
                    item["source_matches"] = found
                else:
                    kind = "unclassified"
            else:
                found = [
                    {"path": p, "line": i + 1}
                    for p in sorted(targets) for i, value in enumerate(lines[p]) if value == text
                ]
                if found:
                    kind = "exact_source_line"
                    item["source_matches"] = found
                else:
                    kind = "unclassified"
            item["classification"] = kind
            counts[kind] += 1
            if kind == "unclassified":
                issues.append({"profile": profile, "ordinal": n, **item})
            output_checks.append(item)
        dynamic_range = None
        if profile == "baseline" and n in (44, 46):
            path = "src/ailego/parallel/thread_pool.cc"
            pattern = "ThreadPool::ThreadPool(uint32_t" if n == 44 else "SafeWorkerCount"
            locations = [i + 1 for i, value in enumerate(lines[path]) if pattern in value]
            start = locations[0]
            delta = 30 if n == 44 else 12
            expected = lines[path][start - 1:start + delta]
            displayed_expected = "\n".join(expected).rstrip("\n").splitlines()
            assert meta["stdout"].splitlines()[-len(displayed_expected):] == displayed_expected
            dynamic_range = {
                "source_path": path, "grep_pattern": pattern, "matching_lines": locations,
                "selected_first_line": start, "sed_end_inclusive": start + delta,
                "returned_tail_exact_after_omitted_trailing_newline_only": True,
                "trailing_blank_lines_not_present_in_native_stdout": len(expected) - len(displayed_expected),
                "command_substitution_only_grep_head_cut_source_metadata": True,
            }
        reviews.append({
            "profile": profile, "ordinal": n, "tool_id": t["id"], "command": cmd,
            "tool_evidence": ref(O / profile / "extracted" / t["evidence_path"]),
            "input_sha256": c["input_sha256"], "result_sha256": r["block_sha256"],
            "stdout_sha256": sha(meta["stdout"].encode()), "stdout": meta["stdout"],
            "native_content": r["content"], "native_no_output_wrapper": wrapper,
            "stderr": meta["stderr"], "is_error": r["is_error"],
            "all_command_operators_require_manual_review": True,
            "declared_source_operands": requested,
            "all_bound_files_in_manifest": True,
            "source_references": [ref(S / p) for p in sorted({
                m["path"] for row in output_checks for m in row.get("source_matches", [])
            })],
            "stdout_line_count": len(output_checks), "stdout_line_classes": dict(counts),
            "output_line_checks": output_checks, "dynamic_sed_range": dynamic_range,
        })
result = {
    "pair": "q004-r03", "static_only": True, "solver_commands_executed": False,
    "source_manifest": config["source_manifest"], "script": ref(Path(__file__)),
    "bash_tools": len(reviews), "stdout_lines": sum(r["stdout_line_count"] for r in reviews),
    "issues": issues, "reviews": reviews, "semantic_scope_verdict_not_automatic": True,
}
write(O / "bash-source-checks.json", result)
print(json.dumps({k: result[k] for k in ("bash_tools", "stdout_lines", "issues")}, ensure_ascii=False))
