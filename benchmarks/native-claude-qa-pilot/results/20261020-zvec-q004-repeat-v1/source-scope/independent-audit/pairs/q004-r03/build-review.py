"""Publish independently reviewed q004-r03 source/tool scope only; no answer grading."""
from pathlib import Path
import collections
import hashlib
import json
import re

O = Path(__file__).parent
M = O.parent.parent
def load(p):
    return json.loads(p.read_text())
def sha(b):
    return hashlib.sha256(b).hexdigest()
def ref(p):
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p.read_bytes())}
def put(p, data):
    with p.open("x") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
cfg = load(M / "frozen-method-inputs.json")
S = Path(cfg["source_root"])
assert sha(Path(cfg["policy"]["path"]).read_bytes()) == "2233de3a9413bbad6c8af4a977c4da406213ac25674bb7b38e90b08fb4ac8d06"
bs = load(O / "bash-source-checks.json")
bash = {(r["profile"], r["ordinal"]): r for r in bs["reviews"]}
baseline_notes = {
3: "Two semicolon-separated read-only grep pipelines. The second /workspace/src/core/quantizer/pq_int8_quantizer* has no frozen match and stderr is discarded. Preserve this source-local unsuccessful probe; no external operand or hidden success inferred.",
4: "Recursive source grep for reducer and pool setter names followed by head; returned filenames are frozen.",
6: "Recursive source grep over two explicit source subtrees; discarded diagnostics are preserved as part of the command. Returned numbered lines match those operands.",
8: "Source header glob grep and separate index_builder.h grep. Both pipelines only read the declared source headers.",
11: "Recursive source grep over src and python, with --include=* and filename output; no command substitution or mutation.",
13: "Single source quantizer file grep; train/chunk/thread substrings are search patterns, not source execution.",
14: "Read-only grep across DiskANN .cc/.h globs. Returned lines match those files.",
15: "Two source grep pipelines over Python binding and Python API file; no Python interpreter is executed.",
18: "Recursive source search for the borrowed pool wrapper, constrained to .h/.cc files.",
22: "Lists two explicit source directories, then head. Each heading and returned entry belongs to those directories.",
23: "pwd && ls -la executes in /workspace according to persisted event cwd and stdout. Dot and parent-dot rows are incidental source-local ls metadata allowed explicitly by policy; no parent directory listing/read occurs.",
27: "Read-only grep of the frozen thread-pool header; searched std::thread and worker strings are not executed.",
28: "Source reducer grep then wc -l of two explicit source files. Counts 783 and 119, total 902, independently recomputed from frozen bytes.",
29: "find enumerates only directories below /workspace/src and /workspace/python, then head; no -exec, outside traversal operand or source execution.",
30: "Source DiskANN directory ls gates source file grep through &&. Either branch outcome remains within supplied source; no need to infer a hidden status.",
31: "cat reads only frozen global_resource.h, then a separate recursive source TrainAndTransform search. Complete cat text and numbered grep lines match source.",
32: "Two grep stages plus head all process source data. Native stdout and stderr are empty, content is the native '(Bash completed with no output)' wrapper. Preserve wrapper; it is not external file content. No individual grep exit status inferred.",
35: "Four sequential source inspections: grep of zvec.py, sed python_collection.cc:236-248, grep def init with context passed to sed of that stream, and sed zvec.py:155-175. Source output bound per line; first search has no returned match.",
36: "Recursive source optimize_thread_count grep. Exported-symbol text and linker source files are part of the frozen manifest, not outside binaries.",
42: "Two source binding grep pipelines, one filenames-only and one numbered; both frozen.",
43: "Grep of two explicit frozen Python/config-binding source files.",
44: "Command substitution runs grep/cut on the same frozen thread_pool.cc, resolving sed start 93. Range 93,+30 ends at blank line123; native output omits trailing newline/blank only and ends at122. No eval, source execution or outside read. v1 checker diagnostic retained.",
46: "Source grep plus grep/head/cut command substitution selecting first SafeWorkerCount line85, then sed 85,+12 (through97) from the same file. Returned tail exactly matches; no eval/source execution.",
47: "Source interface grep then sed of index_builder.h:30-80. EOF-limited displayed code is still bound to the file; stderr redirection is not a source write.",
48: "Single frozen DiskANN file grep for make_group and threads->; patterns contain no shell execution.",
50: "Recursive source db grep filtered by a second grep and head. Filtering test-named text does not execute tests.",
52: "Read-only grep of two explicit frozen interface headers; all returned source lines checked.",
}
zg_notes = {
1: "Three source grep pipelines separated by literal echo ===. Last /workspace/src/db/collection/*.cc has no frozen match and diagnostics are discarded; source-local failed probe retained, no external read or project execution.",
4: "Recursive grep at /workspace with explicit .py/.cc/.cpp/.h/.hpp include globs. Returned filenames are frozen source. Predeclared generated-index files present in before/after snapshots have none of these suffixes; source-local traversal metadata is allowed. No observed generated-file content is returned.",
5: "Source reducer grep, source db/common directory ls, MergeOptions search and borrowed wrapper search. Three echo --- separators are authored literal output; all real paths/lines are frozen.",
8: "Source setter search, a source-local index_streamer_reducer.h probe with no frozen match and suppressed diagnostics, and source include-directory find by name. Literal echo === retained; no find execution action.",
14: "The exact command has a conditional outside-home ls fallback. Source names and pwd are observed, but no exit status for the initial ls, expanded home path or branch trace is recorded. Whole Bash success/final pwd is not proof fallback was skipped. Strict scope remains unknown; no external file content or external code/answer fact is observed.",
17: "Source grep of four config/API files and separate converter search filtered with grep -v test; literal echo === only. No test execution.",
19: "Grep of explicit collection, segment_manager and segment source files. All three files, including segment_manager.cc, are frozen and returned lines align.",
21: "Three source grep pipelines separated by echo ===. Middle filtered MergeWithOptionalReuse search has no returned rows. Retain empty output without inferring independent grep status.",
25: "Source find of reducer names, grep filtered to exclude .cc rows (empty), then explicit reducer.cc search. All reads are source-local; echo is literal metadata.",
27: "Read-only source segment.cc grep for merge/concurrency names.",
31: "Source reducer-header grep returns no rows, then vector-column source glob grep and explicit index.cc grep, separated by echo ===. Empty first probe and each literal separator retained; no project execution.",
}
assert set(baseline_notes) == {n for p, n in bash if p == "baseline"}
assert set(zg_notes) == {n for p, n in bash if p == "zvec-grep"}

# Static output check intentionally leaves authored separators unclassified.
# Resolve only the twelve exact literal outputs whose echo expressions are present.
literal_resolutions = []
for issue in bs["issues"]:
    record = bash[issue["profile"], issue["ordinal"]]
    assert issue["text"] in ("===", "---") and ("echo " + issue["text"]) in record["command"]
    literal_resolutions.append({
        **issue, "resolution": "exact literal echo operand in the reviewed command",
        "external_content": False, "command": record["command"],
        "tool_evidence": record["tool_evidence"],
    })
assert len(literal_resolutions) == 12
put(O / "bash-literal-resolutions.json", {
    "static_check": ref(O / "bash-source-checks.json"), "resolutions": literal_resolutions,
    "unresolved_stdout_lines": [], "policy_exceptions_added": False,
})

zg14 = load(O / "zvec-grep/extracted/tools/tool-0014.json")
fallback = {
    "id": "zg-bash14-home-fallback-branch",
    "profile": "zvec-grep", "ordinal": 14, "tool_id": zg14["id"],
    "command": zg14["call_variants"][0]["input"]["command"],
    "input_sha256": zg14["call_variants"][0]["input_sha256"],
    "result_sha256": zg14["result_variants"][0]["block_sha256"],
    "raw_call_locations": zg14["call_variants"][0]["locations"],
    "raw_result_locations": zg14["result_variants"][0]["locations"],
    "full_input_and_result": ref(O / "zvec-grep/extracted/tools/tool-0014.json"),
    "native_stdout": zg14["result_variants"][0]["metadata"]["stdout"],
    "native_stderr": zg14["result_variants"][0]["metadata"]["stderr"],
    "observed_root_names": zg14["result_variants"][0]["metadata"]["stdout"].splitlines()[:-1],
    "observed_names_equal_frozen_nonhidden_root_names": sorted(
        zg14["result_variants"][0]["metadata"]["stdout"].splitlines()[:-1]
    ) == sorted(p.name for p in S.iterdir() if not p.name.startswith(".")),
    "policy": cfg["policy"],
    "policy_basis": [
        "Names-only enumeration outside supplied source is a strict path deviation.",
        "Inspect actual shell operators, resolved paths and returned evidence.",
        "Distinguish confirmed probes from unresolved action; no hidden successful access inferred.",
        "Unresolved evidence blocks favorable conclusions rather than proving absence.",
    ],
    "missing_evidence": [
        "Independent exit status of the first `ls /workspace 2>/dev/null`.",
        "Branch trace showing whether `ls ~ 2>/dev/null` was invoked.",
        "Runtime home/tilde expansion, if the fallback was invoked.",
    ],
    "interpretation": "All observed stdout matches source-root names and /workspace. This supports observed factual provenance, but does not establish the execution or nonexecution of the outside-home branch. is_error=false and the final pwd are whole-command observations only.",
    "strict_source_scope_valid": None, "strict_scope_gate_passed": False,
    "external_directory_enumeration_attempt_confirmed": None,
    "external_content_observed": False, "external_code_or_answer_facts_observed": False,
    "factual_evidence_scope_valid": True, "not_claiming_confirmed_external_access": True,
}
assert fallback["observed_names_equal_frozen_nonhidden_root_names"]
put(O / "unresolved-home-fallback.json", fallback)

mcp = load(O / "zvec-grep/tool-check-0012.json")["mechanical_check"]
assert mcp["root_equals_workspace"] and mcp["native_is_error"] is False
assert len(mcp["text_blocks"]) == 1
block = mcp["text_blocks"][0]
assert not block["unparsed_rows"] and block["hit_count"] == 12
assert block["line_comparison_counts"] == {"exact": 91, "leading_indentation_omitted": 4, "non_whitespace_difference": 1}
relevance = {
1: ("direct", "OptimizeOption documentation explicitly identifies optimization concurrency; source docs are not proof of all runtime details."),
2: ("relevant_location", "Core merge entry and MixedStreamerReducer selection; short preview ends before full pool handoff."),
3: ("material", "ThreadPool constructor applies SafeWorkerCount and clamps requested size, relevant to worker capacity."),
4: ("direct", "Python collection.optimize wrapper forwards OptimizeOption to native Optimize."),
5: ("direct", "DiskANN train_quantized_data enters PqInt8Quantizer creation; preview locates the requested path but does not include later outer-pool formula."),
6: ("material", "Reducer constructs BorrowedSingleQueueIndexThreads from its selected pool and passes it to optional conversion training."),
7: ("material", "GlobalResource optimization-pool accessor initializes and returns the configured pool."),
8: ("material", "MergeWithOptionalReuse concurrency==0 selects GlobalResource optimization pool and derives worker count from it."),
9: ("supporting_source", "Frozen reducer test source demonstrates a converter/pool arrangement; read only, never executed."),
10: ("adjacent_not_authoritative", "CreateIndexOptions is adjacent configuration source, not itself the queried OptimizeOption runtime branch."),
11: ("tangential", "Generic IndexRunner.optimize method is not the collection optimization pool-selection path."),
12: ("direct", "OptimizeOption constructor docs identify concurrency argument/default; docs alone do not prove runtime worker use."),
}
for h in block["hits"]:
    h["source_reference"] = ref(S / h["path"])
    h["scope_source_valid"] = True
    h["material_relevance_class"], h["relevance_note"] = relevance[h["rank"]]
    h["material_relevance"] = h["rank"] in (3, 6, 7, 8)
    for line in h["lines"]:
        assert line["within_declared_span"] and line["byte_offset_reconstruction_exact"]
        if line["comparison"] == "leading_indentation_omitted":
            assert line["returned_text"] == line["source_text"].lstrip()
            line["manual_disposition"] = "Only initial indentation omitted; non-whitespace text exactly matches the frozen source. The byte-sliced entity begins at its parser start column in the fixed implementation."
        elif line["comparison"] == "non_whitespace_difference":
            assert h["rank"] == 10 and h["path"] == "src/include/zvec/db/options.h"
            assert (h["start_line"], h["end_line"], line["line"]) == (56, 58, 58)
            assert line["source_text"] == "};" and line["returned_text"] == "}"
            assert h["whole_snippet_is_exact_source_prefix"]
            assert line["at_declared_hit_end"] and line["last_displayed_line"]
            line["manual_disposition"] = "Specific C++ struct entity boundary: full displayed 56-58 snippet is the frozen source prefix ending at closing brace; one following semicolon excluded. Fixed adapter includes struct_specifier, fixed extraction slices parser byte coordinates, and formatter does not remove a short-line semicolon. Evidence supports exact source-slice attribution, not an arbitrary punctuation waiver."
            line["attribution_limit"] = "Static source+implementation attribution; raw tool does not expose underlying parser node byte columns, and no parser/source execution was performed."
put(O / "mcp-reviewed-source-and-relevance.json", {
    "ordinal": 12, "full_evidence": ref(O / "zvec-grep/extracted/tools/tool-0012.json"),
    "mechanical_evidence": ref(O / "zvec-grep/tool-check-0012.json"),
    "input_query_form": "one string value in query, whose text resembles a JSON array; no queries array argument",
    "query_groups_reported": block["query_groups_reported"],
    "observed_group_labels": ["Q1"], "hidden_query_decomposition_inferred": False,
    "root": "/workspace", "successful_materially_relevant_retrieval": True,
    "material_hit_ranks": [3, 6, 7, 8], "fixed_implementation": cfg["fixed_rust_implementation"],
    "fixed_implementation_line_spans": {"rust-fixed-code.rs": [209, 241], "rust-fixed-adapter.rs": [[26, 45], [172, 185]], "rust-fixed-search-format.rs": [193, 276]},
    "all_returned_snippets_reviewed": True, "text_block": block,
    "non_whitespace_difference_preserved": True, "generic_difference_waiver": False,
    "external_code_or_answer_facts_observed": False, "unresolved_scope_evidence": [],
})

profiles = []
review_refs = []
for profile in ("baseline", "zvec-grep"):
    q = load(O / profile / "extracted/tool-audit.json")
    cross = load(O / profile / "extraction-crosscheck.json")
    snapshots = load(O / profile / "source-snapshots.json")
    assert cross["all_match"] and not cross["errors"] and snapshots["source_unchanged"]
    reviews = []
    output_dir = O / profile / "reviewed-tools"
    output_dir.mkdir()
    for t in q["tools"]:
        n = t["ordinal"]
        check_path = O / profile / f"tool-check-{n:04d}.json"
        check = load(check_path)
        c, r = t["call_variants"][0], t["result_variants"][0]
        strict = None if profile == "zvec-grep" and n == 14 else True
        review = {
            "ordinal": n, "session": t["session"], "tool_id": t["id"], "name": t["name"],
            "full_input": c["input"], "input_sha256": c["input_sha256"],
            "input_block_sha256": c["block_sha256"], "result_block_sha256": r["block_sha256"],
            "raw_call_locations": c["locations"], "raw_result_locations": r["locations"],
            "original_complete_tool_evidence": ref(O / profile / "extracted" / t["evidence_path"]),
            "mechanical_check": ref(check_path), "native_is_error": r["is_error"],
            "all_call_and_result_variants_reviewed": True, "manual_review_status": "complete",
            "strict_source_scope_valid": strict, "strict_scope_gate_passed": strict is True,
            "factual_evidence_scope_valid": True, "external_content_observed": False,
            "external_code_or_answer_facts_observed": False,
            "solver_web_or_network_attempt_observed": False,
            "source_write_or_project_execution_attempt_observed": False,
            "unresolved_evidence": [ref(O / "unresolved-home-fallback.json")] if strict is None else [],
        }
        if t["name"] == "Read":
            mc = check["mechanical_check"]
            assert mc["path_in_frozen_manifest"] and mc["all_returned_lines_exact"] and mc["metadata_exact"]
            review["note"] = "Full raw Read input/result/metadata checked against the exact frozen source slice. Repository-source contents, including comments, examples and test source, are data only and were not executed."
            review["source_reference"] = ref(S / mc["source_path"])
            review["returned_source_lines"] = len(mc["returned_line_checks"])
        elif t["name"] == "Bash":
            review["note"] = (baseline_notes if profile == "baseline" else zg_notes)[n]
            review["complete_command_and_output_check"] = ref(O / "bash-source-checks.json")
            review["stdout_sha256"] = bash[profile, n]["stdout_sha256"]
            review["stdout_lines"] = bash[profile, n]["stdout_line_count"]
            review["literal_output_check"] = ref(O / "bash-literal-resolutions.json")
        else:
            assert t["name"] == "mcp__zvec_grep__zvec_grep_search" and profile == "zvec-grep" and n == 12
            review["note"] = "One successful /workspace zg query, all twelve hits and all 96 displayed source lines checked, with specific formatting/boundary differences retained. Four returned hits materially establish requested pool-selection/handoff/capacity locations."
            review["complete_retrieval_check"] = ref(O / "mcp-reviewed-source-and-relevance.json")
            review["relevant_correct_workspace_zg_retrieval"] = True
        p = output_dir / f"tool-{n:04d}.json"
        put(p, review)
        review_refs.append(ref(p))
        reviews.append(review)
    pstrict = True if profile == "baseline" else None
    profiles.append({
        "profile": profile, "strict_source_scope_valid": pstrict,
        "strict_scope_gate_passed": pstrict is True, "scope_and_retrieval_gate_passed": pstrict is True,
        "factual_evidence_scope_valid": True, "external_content_observed": False,
        "external_code_or_answer_facts_observed": False, "source_unchanged": True,
        "relevant_correct_workspace_zg_retrieval": None if profile == "baseline" else True,
        "retrieval_requirement_applies": profile == "zvec-grep",
        "tool_count": len(reviews), "root_tool_count": q["root_tool_count"], "child_tool_count": q["child_tool_count"],
        "tool_counts": dict(collections.Counter(t["name"] for t in q["tools"])),
        "raw_tool_occurrences": cross["actual_tool_occurrences"],
        "read_returned_source_lines": sum(x.get("returned_source_lines", 0) for x in reviews),
        "bash_stdout_lines": sum(x.get("stdout_lines", 0) for x in reviews),
        "mcp_hit_count": 0 if profile == "baseline" else 12,
        "mcp_returned_source_lines": 0 if profile == "baseline" else 96,
        "known_strict_deviations": [], "unresolved_evidence": [] if profile == "baseline" else [ref(O / "unresolved-home-fallback.json")],
        "source_snapshot_evidence": ref(O / profile / "source-snapshots.json"),
        "extraction_and_accounting": ref(O / profile / "extracted/tool-audit.json"),
        "independent_raw_occurrence_crosscheck": ref(O / profile / "extraction-crosscheck.json"),
        "source_file_count_before_after": [1596, 1596],
        "additional_predeclared_native_artifact_files_before_after": [len(p["additional_files"]) for p in snapshots["phases"]],
        "native_artifact_review": "All extras are regular files under predeclared .zvec-grep/ or .zg-bench-index.ignore. Complete trajectories show no solver writes to source; unchanged native MCP indexing artifacts are accounted separately. This does not infer no writes merely from source hashes.",
        "all_tool_paths_operators_and_returned_contents_reviewed": True,
        "all_bash_commands_checked_for_zg_cli_or_other_retrieval_channels": True,
        "zg_retrieval_channels_observed": [] if profile == "baseline" else ["mcp__zvec_grep__zvec_grep_search"],
        "quality_or_resource_comparison_performed": False,
    })
method_files = [ref(p) for p in M.iterdir() if p.is_file()]
report = {
    "schema_version": 2, "audit_kind": "independent_complete_source_tool_scope", "pair_key": "q004-r03",
    "protocol_id": cfg["protocol_id"], "run_id": cfg["run_id"], "head_sha": cfg["head_sha"], "attempt": 1,
    "status": "review_complete_with_specific_unresolved_strict_scope",
    "strict_source_scope_valid": None, "strict_scope_gate_passed": False,
    "scope_and_retrieval_gate_passed": False, "factual_evidence_scope_valid": True,
    "external_content_observed": False, "external_code_or_answer_facts_observed": False,
    "source_unchanged": True, "relevant_correct_workspace_zg_retrieval": True,
    "tool_count": 85, "root_tool_count": 85, "child_tool_count": 0, "raw_tool_occurrences": 340,
    "read_tool_count": 46, "bash_tool_count": 38, "mcp_tool_count": 1, "mcp_hit_count": 12,
    "read_returned_source_lines": sum(p["read_returned_source_lines"] for p in profiles),
    "bash_stdout_lines": 790, "mcp_returned_source_lines": 96,
    "mcp_source_line_comparisons": block["line_comparison_counts"],
    "native_tool_error_count": 0,
    "native_error_count_limit": "Whole-tool is_error flags are not independent exit statuses for each shell subcommand. Empty and redirected probes are preserved.",
    "field_definitions": {
        "factual_evidence_scope_valid": "Observed returned source/code facts are traceable to the frozen supplied checkout. Native self-authored literals and source-local metadata are separately classified. This does not assert unobserved fallback absence or correctness of a final answer.",
        "external_content_observed": "Positive observation of content from an outside-source file; none observed. Unknown metadata branch execution remains separately blocking.",
        "strict_source_scope_valid": "Tri-state conjunction over actual source/metadata/tool operations under the unchanged policy. Unknown branch evidence stays null; it cannot pass the gate.",
        "source_unchanged": "All 1596 frozen source files match independently verified manifests both before and after, in addition to reviewing attempted writes.",
    },
    "source_commit": cfg["source_commit"], "source_inventory_sha256": cfg["source_inventory_sha256"],
    "source_file_count": 1596, "frozen_policy": cfg["policy"], "frozen_plan": cfg["plan"],
    "frozen_decision": cfg["decision"], "original_task_instruction": cfg["question"],
    "identity_and_receipt_check": ref(O / "input-identity-check.json"),
    "complete_local_source_verification": ref(O / "source-file-verification.json"),
    "sealed_method_manifest": ref(M / "method-manifest.json"), "method_files": method_files,
    "profiles": profiles, "reviewed_tools": review_refs,
    "bash_source_check": ref(O / "bash-source-checks.json"),
    "literal_output_resolutions": ref(O / "bash-literal-resolutions.json"),
    "mcp_complete_source_comparison": ref(O / "mcp-reviewed-source-and-relevance.json"),
    "unresolved_evidence": [ref(O / "unresolved-home-fallback.json")],
    "retained_checker_diagnostic": ref(O / "checker-diagnostic-v1.json"),
    "audit_scripts": [ref(O / x) for x in ("check-bash-source.py", "check-bash-source-v2.py", "build-review.py")],
    "limits": [
        "Post-run evidence audit, not OS/network isolation or observation beyond the supplied complete native trajectories.",
        "zg Bash14 contains an outside-home fallback whose actual execution is not independently recorded; strict null blocks gate.",
        "The sole MCP nonwhite difference is preserved and specifically attributed using its exact frozen source span and fixed Rust implementation; underlying parser byte columns are not present in the raw response.",
        "No exact ordering of incidental grep stream ordinals or hidden query group decomposition is imposed or inferred.",
    ],
    "final_answers_grades_private_assignments_or_packets_read": False,
    "solver_or_project_commands_executed_by_auditor": False,
    "policy_exceptions_added": False, "historical_reports_modified": False,
    "quality_or_resource_comparison_performed": False,
}
assert sum(p["tool_count"] for p in profiles) == 85
assert sum(p["raw_tool_occurrences"] for p in profiles) == 340
put(O / "report.json", report)
print(json.dumps({"report": ref(O / "report.json"), "profiles": [{k: p[k] for k in ("profile", "strict_source_scope_valid", "tool_count", "read_returned_source_lines", "bash_stdout_lines")} for p in profiles]}, ensure_ascii=False))
