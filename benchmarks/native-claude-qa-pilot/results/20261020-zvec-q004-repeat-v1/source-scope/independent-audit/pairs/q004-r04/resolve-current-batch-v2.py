"""Resolve only returned-text cases from manually reviewed r04-r10 commands; never run them."""
from pathlib import Path
import hashlib
import json
import re
import shlex
M = Path(__file__).parent.parent.parent
S = Path(json.loads((M / "frozen-method-inputs.json").read_text())["source_root"])
def sha(b):
    return hashlib.sha256(b).hexdigest()
def ref(p):
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p.read_bytes())}
def put(p, d):
    with p.open("x") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
for key in ("q004-r04", "q004-r05", "q004-r06", "q004-r07", "q004-r08", "q004-r09", "q004-r10"):
    O = M / "pairs" / key
    st = json.loads((O / "static-return-checks.json").read_text())
    toolmap = {}
    for profile in ("baseline", "zvec-grep"):
        q = json.loads((O / profile / "extracted/tool-audit.json").read_text())
        for t in q["tools"]:
            toolmap[profile, t["ordinal"]] = t
    resolutions, unresolved = [], []
    for row in st["unclassified_lines"]:
        profile, ordinal, text = row["profile"], row["ordinal"], row["text"]
        t = toolmap[profile, ordinal]
        c = t["call_variants"][0]
        cmd = c["input"]["command"]
        persisted = [x for x in c["locations"] if x["source_kind"] == "persisted-session-jsonl"]
        assert persisted and all(x["event_metadata"]["cwd"] == "/workspace" for x in persisted)
        d = dict(row)
        d["tool_evidence"] = ref(O / profile / "extracted" / t["evidence_path"])
        tokens = shlex.split(cmd)
        roots = []
        for token in tokens:
            token = token.rstrip(";")
            if token.startswith("/workspace"):
                roots.append(token.removeprefix("/workspace").lstrip("/").rstrip(";"))
            elif token == ".":
                roots.append("")
            elif re.match(r"^(?:src|tests|python)(?:/|$)", token) or token == "README.md":
                roots.append(token.rstrip(";"))
        if re.search(r"\bpwd\s*(?:&&|;)\s*ls", cmd):
            roots.append("")
        actual = set()
        for root in roots:
            if ".." in Path(root).parts:
                continue
            for p in (S.glob(root) if "*" in root or "?" in root else [S / root]):
                if p.exists():
                    actual.add(p)
        files = set()
        for p in actual:
            if p.is_file():
                files.add(p)
            else:
                files.update(x for x in p.rglob("*") if x.is_file())
        sl = {p.relative_to(S).as_posix(): p.read_text(errors="replace").splitlines() for p in files}
        def record(kind, matches=None, **kw):
            d["resolution"] = kind
            if matches:
                d["source_matches"] = matches
                d["source_references"] = [ref(S / p) for p in sorted({m["path"] for m in matches})]
            d.update(kw)
        # This outside listing is a known strict violation, never a source-name exception.
        if key == "q004-r04" and profile == "zvec-grep" and ordinal == 18:
            assert cmd == "ls /logs/agent/sessions/ 2>/dev/null; pwd; ls"
            assert row["output_line"] <= 9
            record("Observed outside-session-directory entry name; strict path deviation. No file contents returned.", external_directory_metadata_observed=True, external_content_observed=False)
        elif text in ("---", "----") and re.search(r"\becho\s+[\"']?" + re.escape(text) + r"(?:[\"']|[;& ]|$)", cmd):
            record("Exact authored echo literal, not file content.")
        elif text.startswith("ugrep: warning: "):
            target = text.removeprefix("ugrep: warning: ").split(": No such file", 1)[0]
            assert target in cmd and (target.startswith("/workspace/") or target.startswith("src/"))
            record("Native missing source-local path diagnostic; exact target is in command. Failure retained.")
        elif (key, profile, ordinal) in (("q004-r05", "zvec-grep", 15), ("q004-r06", "zvec-grep", 28)):
            m = re.fullmatch(r"/workspace/([^:]+):(.*)", text)
            i = next(i for i in range(len(tokens) - 2) if tokens[i:i + 2] == ["rg", "-rn"])
            pattern = tokens[i + 2]
            assert m and m[1] in sl
            matches = [{"path": m[1], "line": no, "original_source_text": value}
                       for no, value in enumerate(sl[m[1]], 1) if re.search(pattern, value) and re.sub(pattern, "n", value) == m[2]]
            assert matches
            record("Exact source transformed by the actual `rg -rn` short option: -r replacement is literal n, not a line-number flag. All regex substitutions independently reconstructed from frozen source without executing rg.", matches, replacement="n", regex=pattern)
        elif (key, profile, ordinal) in (
            ("q004-r08", "baseline", 6), ("q004-r08", "zvec-grep", 3),
            ("q004-r08", "zvec-grep", 23), ("q004-r09", "zvec-grep", 11),
        ):
            ranges = {
                ("q004-r08", "baseline", 6): ("src/core/algorithm/diskann/diskann_builder.cc", 640, 680),
                ("q004-r08", "zvec-grep", 3): ("src/db/index/segment/segment_helper.cc", 804, 812),
                ("q004-r08", "zvec-grep", 23): ("src/db/index/segment/segment_helper.cc", 640, 750),
                ("q004-r09", "zvec-grep", 11): ("src/db/index/segment/segment.cc", 1595, 1700),
            }
            m = re.fullmatch(r"\s*(\d+)(?::|\t)(.*)", text)
            p, start, end = ranges[key, profile, ordinal]
            assert m and p in sl
            nsrc = start + int(m[1]) - 1
            assert nsrc <= end and sl[p][nsrc - 1] == m[2]
            record("Line number is relative to the exact sed-produced stream (or cat -n), not the file. Explicit source range maps the returned bytes exactly.", [{"path": p, "line": nsrc}], stream_line=int(m[1]), source_sed_range=[start, end])
        else:
            path = text.removeprefix("./").rstrip(":")
            numbered = re.fullmatch(r"(?:(\d+):)?((?:src|python|tests)/.*?):(\d+)[:-]?(.*)", text)
            # Actual relative grep layout: path:line:text, with optional preceding intermediate count.
            if numbered and numbered[2] in sl:
                p, nsrc, value = numbered[2], int(numbered[3]), numbered[4]
                assert 0 < nsrc <= len(sl[p]) and sl[p][nsrc - 1] == value
                record("Relative frozen source path plus exact line/text; persisted cwd=/workspace. Any preceding count is intermediate stream metadata, not a new gate.", [{"path": p, "line": nsrc}], intermediate_stream_line=numbered[1])
            elif re.fullmatch(r"\d+:\d+:.*", text):
                m = re.fullmatch(r"(\d+):(\d+):(.*)", text)
                matches = [{"path": p, "line": int(m[2])} for p, values in sl.items()
                           if 0 < int(m[2]) <= len(values) and values[int(m[2]) - 1] == m[3]]
                assert matches
                record("Nested grep prefixes; second number and exact bytes map to declared frozen source. First is intermediate stream ordinal.", matches, intermediate_stream_line=int(m[1]))
            elif (path.startswith(("src/", "python/", "tests/")) or path in ("src", "python", "tests")) and (S / path).exists() and any((S / path) == root or (S / path).is_relative_to(root) for root in actual):
                p = S / path
                assert p.exists() and any(p == root or p.is_relative_to(root) for root in actual)
                record("Relative source path/directory heading; exact location within declared source operands and cwd=/workspace.", source_path=path)
            else:
                matches = [{"path": p, "line": no} for p, values in sl.items() for no, value in enumerate(values, 1) if value == text]
                if matches:
                    record("Exact source line from explicitly named relative operand (including README.md).", matches)
                elif text in (".", "..", ".zvec-grep", ".zg-bench-index.ignore") and key == "q004-r06" and profile == "zvec-grep" and ordinal == 20:
                    assert cmd == "pwd && ls -a"
                    record("Source-local ls metadata: incidental dot entries or explicitly predeclared native index artifact name. No external directory enumeration or content read.")
                else:
                    matches = [p.relative_to(S).as_posix() for root in actual if root.is_dir() for p in (root.rglob("*") if "ls -R " in cmd else root.iterdir()) if p.name == text]
                    if matches:
                        record("Source-directory entry name under the actual declared ls operand; recursive descendants considered only for explicit ls -R.", matched_source_entries=matches)
                    else:
                        unresolved.append(d)
                        continue
        resolutions.append(d)
    result = {"pair": key, "script": ref(Path(__file__)), "original_static_map": ref(O / "static-return-checks.json"),
              "resolutions": resolutions, "unresolved": unresolved, "actual_commands_executed": False,
              "scope_verdict_is_manual_and_separate": True}
    put(O / "returned-text-resolutions.json", result)
    print(json.dumps({"pair": key, "resolved": len(resolutions), "unresolved": unresolved}, ensure_ascii=False))
