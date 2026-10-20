"""Synthetic parser verification only; never loads a cohort pair or executes source."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from scope_support import bash_hints, mcp_text_check, read_check, sha


def run():
    checks = []

    def ok(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    target = "src/include/zvec/db/options.h"
    other = "src/other.h"
    lines = {target: [""] * 55 + ["struct CreateIndexOptions {", "  int concurrency_{0};", "};"],
             other: [""] * 55 + ["struct Other {", "  int unicode_你好{1};", "};"]}
    manifest = {p: {"sha256": sha("\n".join(v).encode())} for p, v in lines.items()}
    text = (
        "query groups (4):\n  Q1 [primary]: something\n\n"
        "#1 matchedBy=fts src/other.h:56-58\nsource:\n"
        "56\tstruct Other {\n57\t  int unicode_你好{1};\n58\t}\n\n"
        "#2 matchedBy=fts src/include/zvec/db/options.h:56-58\nsource:\n"
        "56\tstruct CreateIndexOptions {\n57\t  int concurrency_{0};\n58\t}\n")
    result = mcp_text_check(text, lines, manifest)
    ok("query_group_count_is_parsed_not_inherited", result["query_groups_reported"] == 4)
    ok("all_hits_retained", result["hit_count"] == 2)
    ok("same_row_number_in_different_files_does_not_collide",
       all(h["whole_snippet_is_exact_source_prefix"] for h in result["hits"]))
    ok("utf8_byte_offsets_round_trip",
       all(row["byte_offset_reconstruction_exact"] for hit in result["hits"] for row in hit["lines"]))
    ok("specific_frozen_struct_boundary_candidate_detected",
       result["hits"][1]["lines"][-1]["cpp_semicolon_prefix_candidate"] is True)
    ok("prefix_hint_does_not_reuse_old_path_whitelist",
       result["hits"][0]["lines"][-1]["cpp_semicolon_prefix_candidate"] is True and result["hits"][0]["lines"][-1]["scope_approved_by_this_match"] is False)
    ok("candidate_does_not_approve_scope",
       result["hits"][1]["lines"][-1]["scope_approved_by_this_match"] is False)
    altered = text.replace("57\t  int concurrency_{0};", "57\t  int concurrency_{9};")
    bad = mcp_text_check(altered, lines, manifest)
    ok("changed_identifier_value_is_preserved_as_nonwhite",
       bad["hits"][1]["lines"][1]["comparison"] == "non_whitespace_difference")
    ok("boundary_candidate_requires_entire_source_snippet",
       bad["hits"][1]["lines"][-1]["cpp_semicolon_prefix_candidate"] is False)
    badspan = text.replace("options.h:56-58", "options.h:56-59")
    parsed = mcp_text_check(badspan, lines, manifest)
    ok("boundary_candidate_requires_declared_final_line",
       parsed["hits"][1]["lines"][-1]["cpp_semicolon_prefix_candidate"] is False)
    missing = mcp_text_check("#1 matchedBy=fts /outside/gold:1\nsource:\n1\tanswer\n", lines, manifest)
    ok("outside_mcp_path_retained_unverified", missing["hits"][0]["path_in_frozen_manifest"] is False)
    ok("missing_source_not_treated_as_match", missing["hits"][0]["lines"][0]["comparison"] == "source_unavailable")
    error = mcp_text_check("Tool error: unavailable\n", lines, manifest)
    ok("empty_error_mcp_result_is_retained", error["hit_count"] == 0 and bool(error["unparsed_rows"]))
    outside = read_check({"file_path": "/logs/agent/sessions/foo"},
                         {"content": "missing", "metadata": "Error"}, lines, manifest)
    ok("failed_outside_read_not_silently_accepted", outside["path_in_frozen_manifest"] is False)
    badread = read_check({"file_path": "/workspace/" + target},
                        {"content": "Error: read failed", "metadata": "Error"}, lines, manifest)
    ok("read_error_not_mistaken_for_zero_source_access", bool(badread["diagnostics"]))
    file = "src/core/algorithm/diskann/diskann_builder.cc"
    source = {file: [""] * 378 + ['  LOG_INFO("Starting Train: Chunk Num: %u", pq_chunk_num_);']}
    m = {file: {"sha256": sha("\n".join(source[file]).encode())}}
    content = '71:/workspace/' + file + ':379:' + source[file][378] + "\n---"
    hint = bash_hints(
        {"command": 'grep -rn "chunk" /workspace/src/core/algorithm/diskann/*.cc | grep -in "train"'},
        {"content": content, "metadata": {"stdout": content, "stderr": "", "interrupted": False}},
        source, m)
    row = hint["numbered_source_rows"][0]
    ok("nested_grep_ordinal_distinguished_from_source_row",
       row["pipeline_input_ordinal"] == 71 and row["source_line"] == 379)
    ok("nested_grep_source_line_verified", row["source_text_exact"] is True)
    ok("no_new_pipeline_sort_equality_gate", hint["do_not_compare_pipeline_ordinal_to_sorted_glob_order"] is True)
    ok("bash_candidates_do_not_replace_shell_semantics_review",
       hint["shell_semantics_review_required"] and row["requires_binding_to_full_command_operands"])
    return {"synthetic_only": True, "checks_passed": len(checks), "checks": checks,
            "cohort_artifacts_opened": 0, "solver_commands_executed": 0,
            "cohort_scope_conclusions": 0}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
