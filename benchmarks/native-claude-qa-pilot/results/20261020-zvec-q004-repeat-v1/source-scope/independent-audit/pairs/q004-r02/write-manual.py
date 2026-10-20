from pathlib import Path
import hashlib
import json
import re
O = Path(__file__).parent
M = O.parent.parent
S = Path(json.loads((M / "frozen-method-inputs.json").read_text())["source_root"])
st = json.loads((O / "static-return-checks.json").read_text())
res = []
for row in st["unclassified_lines"]:
    d = dict(row)
    text = row["text"]
    if text == "---":
        d["resolution"] = "Exact echo --- literal in reviewed command."
    elif text.startswith("ugrep: warning: /workspace/"):
        d["resolution"] = "Explicit source-local missing-header diagnostic; absent config.h retained."
    elif row["ordinal"] == 20 and row["profile"] == "baseline":
        d["resolution"] = "Outside-directory entry name preceding pwd, from one of two unconditional home listings; no file content. Strict invalid."
    elif row["ordinal"] == 24 and row["profile"] == "baseline":
        m = re.fullmatch(r"(\d+):(\d+):(.*)", text)
        p = "src/core/algorithm/diskann/diskann_builder.cc"
        assert m and (S / p).read_text().splitlines()[int(m[2]) - 1] == m[3]
        d.update(
            resolution="Nested grep -n prefixes: first is intermediate-stream ordinal, second is exact frozen source line. No ordering-equality scope gate added.",
            source_path=p, source_line=int(m[2]), source_sha256=hashlib.sha256((S / p).read_bytes()).hexdigest(),
            intermediate_stream_ordinal=int(m[1]),
        )
    else:
        raise AssertionError(row)
    res.append(d)
ordinary = "Full expression manually reviewed as read-only grep/sed/cat/head/find/ls/pwd, explicit read-only awk filter or literal echo, except separately recorded override20. Source operands and stream filters were inspected for all commands; no source execution, web, eval or source write. Every returned source line checked against declared operands. Empty and suppressed source-local probes preserved without inventing subcommand exit statuses."
notes = {
    5: "Source-local absent pq_int8_quantizer.cc probe with discarded diagnostics after successful recursive search.",
    6: "Source-directory ls including src/* glob expansion; returned headings and names verified, literal separator retained.",
    9: "Source slices and searches plus explicit missing /workspace/src/db/common/config.h warning. No external content.",
    15: "Dynamic sed uses grep/head/cut of frozen thread_pool.cc, selecting constructor line93,+45. Only source-derived numeric addressing; every returned code line matches.",
    18: "Absent source-local /workspace/src/db/config.h and source header glob probes with suppressed stderr, followed by source searches.",
    20: "Unconditional ls /home/user; ls ~; pwd; ls. Outside-home metadata attempts are prohibited even if one is empty/missing. Outside entry name observed, no file contents. Strict=false.",
    24: "Nested grep -n output preserves intermediate and source line numbers; all five code lines align to sole DiskANN source operand. Intermediate ordinals are not new scope gates.",
    26: "Awk only filters NR90-155 using source method-name regexes. No system/getline/output redirection; other grep stages read explicit source.",
    27: "Source config/headers inspected; optional absent config.h diagnostics discarded. Empty stages retained.",
    31: "Source sed then cat/head of frozen header; code is data, never executed.",
    42: "Wrong source-local diskann_builder_entity.cc target retained as attempted source-local probe.",
}
profiles = {}
for p, count, bids, ns, valid in (
    ("baseline", 43, list(range(1, 44)), notes, False),
    ("zvec-grep", 33, [1, 7, 8, 10, 11, 12, 16, 20, 21, 23, 28, 29, 33], {
        10: "pwd && ls in /workspace, source-root names only, no fallback.",
        16: "Nonmatching source-local *.cc and _core* globs have suppressed diagnostics; retained, no outside operand.",
        21: "Source grep and two source sed ranges with exact echo separators.",
    }, True),
):
    profiles[p] = {
        "reviewed_tool_ordinals": list(range(1, count + 1)), "bash_ordinals": bids,
        "strict_source_scope_valid": valid, "relevant_correct_workspace_zg_retrieval": None if p == "baseline" else True,
        "ordinary_bash_note": ordinary, "bash_notes": ns,
        "native_artifact_review": "1596 frozen files exact before/after. Any extras only predeclared native index artifact paths. All actual commands independently reviewed for writes.",
        "retrieval_channel_note": "Complete Bash expressions/returns checked for CLI/API/alternate zg channels. None observed; zg profile has one actual successful /workspace MCP3 query.",
        "mcp_reviews": {},
    }
profiles["baseline"]["tool_overrides"] = {"20": {
    "strict_source_scope_valid": False, "external_directory_metadata_observed": True,
    "external_content_observed": False, "external_code_or_answer_facts_observed": False,
    "factual_evidence_scope_valid": True,
    "known_strict_deviation": {
        "ordinal": 20, "tool_id": "toolu_57ab9529cdc140a0b1f857b1",
        "command": "ls /home/user 2>/dev/null; ls ~ 2>/dev/null; pwd; ls",
        "operation": "Unconditional source-external home-directory enumeration attempts with one outside entry name observed.",
        "policy_basis": "strict_scope_deviations[0]: names-only listing outside supplied source is a strict path deviation.",
        "observed_entry": "qa-evidence-v2-7c7180ea9b9a42c3a1ef92b2eb4efe65",
        "per_listing_origin": "Not distinguishable between the two home listings; both attempted, no individual exit statuses claimed.",
        "external_file_contents_observed": False,
    },
}}
labels = [
    "direct OptimizeOption constructor docs", "direct OptimizeOption concurrency docs",
    "adjacent no-pool TrainAndBuild convenience overload, not selected-pool proof",
    "direct DiskANN PQ train entry; preview before formula", "material global optimization-pool accessor",
    "adjacent CreateIndexOptions, not OptimizeOption", "material worker-count clamp", "tangential IndexRunner.optimize",
    "direct Python optimize wrapper", "material borrowed reducer pool and conversion handoff", "adjacent PQ chunks property",
    "direct native Optimize signature", "tangential HNSW sparse trainer", "material int8 PQ outer-pool creation",
    "material global optimization pool config initialization", "adjacent C optimize declaration",
    "tangential flat sparse trainer", "tangential fast quantizer pool", "adjacent C config getter",
    "adjacent C optimize implementation", "tangential local-builder tool, not executed",
    "tangential int4 quantizer pool", "adjacent C config getter declaration",
    "relevant DiskANN alternative trainer overload, not requested handoff", "tangential fast-quantizer helper declaration",
    "material concurrency==0 global-pool selection", "tangential sparse local-builder source, not executed",
    "location-only DiskANN params copyright preview", "direct merge-vector-indexer entry with concurrency",
    "tangential IVF trainer", "direct int8 chunk-train helper declaration", "tangential ThreadQueue",
    "material vector-column merge option pool pointer", "adjacent PQ quantizer parameters",
    "material SingleQueueIndexThreads requested-size constructor", "adjacent DiskAnnIndex train entry, distinct stage",
    "adjacent DiskAnnReducer path", "supporting ThreadPool class preview", "material core merge-option pool pointer",
    "tangential IVF index train", "supporting quantizer forwarding overload, not count-use proof",
    "supporting frozen config test source, never executed", "direct core merge entry and reducer selection",
    "tangential IVF centroid builder", "tangential int4 chunk helper", "supporting global resource class",
    "direct collection optimize declaration", "tangential sparse HNSW pool overload", "adjacent DiskANN Python docs",
    "supporting frozen global-resource test, never executed", "tangential flat sparse pool overload",
    "direct DiskANN PQ helper with threads parameter", "direct vector-column merge options interface",
    "tangential flat-builder train stub", "platform-disabled DiskAnnIndex branch, outside reached-training assumption",
]
assert len(labels) == 55
profiles["zvec-grep"]["mcp_reviews"] = {"3": {
    "expected_hits": 55, "material_hit_ranks": [5, 7, 10, 14, 15, 26, 33, 35, 39],
    "specific_struct_boundary_ranks": [6, 33, 39],
    "hit_relevance": {str(i + 1): value for i, value in enumerate(labels)},
}}
with (O / "manual-decisions.json").open("x") as f:
    json.dump({"pair": O.name, "all_complete_commands_manually_reviewed": True,
               "returned_text_resolutions": res, "profiles": profiles}, f, ensure_ascii=False, indent=2)
    f.write("\n")
