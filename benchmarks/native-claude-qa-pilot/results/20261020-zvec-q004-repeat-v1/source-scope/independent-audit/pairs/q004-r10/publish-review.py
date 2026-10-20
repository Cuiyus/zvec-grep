"""Publish only explicit per-pair manual decisions, with complete static/raw evidence links."""
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
def put(p, value):
    with p.open("x") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")
def tri(values):
    assert all(v is None or type(v) is bool for v in values)
    return False if False in values else True if all(v is True for v in values) else None
cfg = load(M / "frozen-method-inputs.json")
S = Path(cfg["source_root"])
manual = load(O / "manual-decisions.json")
st = load(O / "static-return-checks.json")
assert manual["pair"] == O.name == st["pair"]
assert manual["all_complete_commands_manually_reviewed"]
assert sha(Path(cfg["policy"]["path"]).read_bytes()) == cfg["policy"]["sha256"] == "2233de3a9413bbad6c8af4a977c4da406213ac25674bb7b38e90b08fb4ac8d06"
return_checks = {(r["profile"], r["ordinal"]): r for r in st["reviews"]}
unclassified_keys = {(r["profile"], r["ordinal"], r["stream"], r["output_line"]) for r in st["unclassified_lines"]}
resolutions = manual["returned_text_resolutions"]
assert unclassified_keys == {(r["profile"], r["ordinal"], r["stream"], r["output_line"]) for r in resolutions}
profiles, reviewed_refs, mcp_refs = [], [], []
for profile in ("baseline", "zvec-grep"):
    q = load(O / profile / "extracted/tool-audit.json")
    cross = load(O / profile / "extraction-crosscheck.json")
    source = load(O / profile / "source-snapshots.json")
    dec = manual["profiles"][profile]
    assert cross["all_match"] and source["source_unchanged"]
    assert not q["errors"] and q["extraction_complete"]
    assert sorted(dec["reviewed_tool_ordinals"]) == sorted(t["ordinal"] for t in q["tools"])
    assert sorted(dec["bash_ordinals"]) == sorted(t["ordinal"] for t in q["tools"] if t["name"] == "Bash")
    reviews, hits_total, mcp_lines = [], 0, collections.Counter()
    out = O / profile / "reviewed-tools"
    out.mkdir()
    for t in q["tools"]:
        n, name = t["ordinal"], t["name"]
        cp = O / profile / f"tool-check-{n:04d}.json"
        check = load(cp)["mechanical_check"]
        c, r = t["call_variants"][0], t["result_variants"][0]
        assert len(t["call_variants"]) == len(t["result_variants"]) == 1
        assert t["identity_matches_frozen_accounting"] and not t["extraction_issues"]
        override = dec.get("tool_overrides", {}).get(str(n), {})
        review = {
            "ordinal": n, "session": t["session"], "tool_id": t["id"], "name": name,
            "full_input": c["input"], "input_sha256": c["input_sha256"],
            "input_block_sha256": c["block_sha256"], "result_block_sha256": r["block_sha256"],
            "raw_call_locations": c["locations"], "raw_result_locations": r["locations"],
            "full_original_input_result_metadata": ref(O / profile / "extracted" / t["evidence_path"]),
            "mechanical_check": ref(cp), "all_call_result_variants_reviewed": True,
            "manual_review_status": "complete", "native_is_error": r["is_error"],
            "strict_source_scope_valid": override.get("strict_source_scope_valid", True),
            "factual_evidence_scope_valid": override.get("factual_evidence_scope_valid", True),
            "external_content_observed": override.get("external_content_observed", False),
            "external_code_or_answer_facts_observed": override.get("external_code_or_answer_facts_observed", False),
            "unresolved_evidence": override.get("unresolved_evidence", []),
            "solver_web_or_network_attempt_observed": override.get("solver_web_or_network_attempt_observed", False),
            "source_write_or_project_execution_attempt_observed": override.get("source_write_or_project_execution_attempt_observed", False),
        }
        if name == "Read":
            assert check["metadata_exact"] and check["all_returned_lines_exact"] and check["path_in_frozen_manifest"]
            review.update(
                note="Reviewed explicit source path and full returned slice. Every numbered line and native Read metadata exactly match the frozen source; source code/comments/tests are data and were not executed.",
                source_reference=ref(S / check["source_path"]),
                source_lines=len(check["returned_line_checks"]),
            )
        elif name == "Bash":
            bc = return_checks[profile, n]
            review.update(
                note=dec["bash_notes"].get(str(n), dec["ordinary_bash_note"]),
                full_static_return_map=ref(O / "static-return-checks.json"),
                stdout_sha256=bc["stdout_sha256"], stderr_sha256=bc["stderr_sha256"],
                returned_lines=len(bc["all_returned_lines"]),
                no_output_or_diagnostic_status_inferred=False,
                native_stderr=bc["full_native_metadata"].get("stderr"),
                static_operand_matches=bc["declared_source_operands"],
                returned_text_resolutions=[x for x in resolutions if x["profile"] == profile and x["ordinal"] == n],
            )
        elif name == "mcp__zvec_grep__zvec_grep_search":
            md = dec["mcp_reviews"][str(n)]
            assert check["root_equals_workspace"] and check["native_is_error"] is False and check["result_metadata_matches_content"]
            all_hits = [h for block in check["text_blocks"] for h in block["hits"]]
            assert len(all_hits) == md["expected_hits"]
            assert len(check["text_blocks"]) == 1
            for block in check["text_blocks"]:
                assert not block["unparsed_rows"]
                mcp_lines.update(block["line_comparison_counts"])
                for h in block["hits"]:
                    rank = str(h["rank"])
                    assert rank in md["hit_relevance"]
                    h["manual_relevance"] = md["hit_relevance"][rank]
                    h["materially_relevant"] = h["rank"] in md["material_hit_ranks"]
                    h["source_reference"] = ref(S / h["path"])
                    for line in h["lines"]:
                        assert line["within_declared_span"] and line["byte_offset_reconstruction_exact"]
                        if line["comparison"] == "leading_indentation_omitted":
                            assert line["returned_text"] == line["source_text"].lstrip()
                            line["manual_disposition"] = "Initial indentation only; all non-whitespace bytes match frozen source. Fixed extraction uses entity start byte/column; no source content added."
                        elif line["comparison"] == "non_whitespace_difference":
                            assert h["rank"] in md["specific_struct_boundary_ranks"]
                            assert line["source_text"] == "};" and line["returned_text"] == "}"
                            assert line["at_declared_hit_end"] and line["last_displayed_line"]
                            assert line["returned_is_exact_source_prefix"] and h["whole_snippet_is_exact_source_prefix"]
                            line["manual_disposition"] = "This exact C++ struct snippet ends at closing brace within the frozen declared span; following semicolon is outside the displayed entity. Fixed Rust adapter includes struct_specifier, extractor slices node byte coordinates, formatter does not strip short-line punctuation. Attribution is specific to this span, not a generic difference waiver."
                            line["implementation_attribution_limit"] = "Static implementation plus exact source-prefix evidence; underlying parser byte columns are not returned. No source/parser command was executed."
            mcpdoc = {
                "ordinal": n, "full_original_tool": ref(O / profile / "extracted" / t["evidence_path"]),
                "mechanical_check": ref(cp), "complete_returned_text_comparison": check,
                "input": c["input"], "requested_root": c["input"].get("root"),
                "query_group_count": [b["query_groups_reported"] for b in check["text_blocks"]],
                "hidden_query_decomposition_inferred": False, "all_hits_reviewed": True,
                "successful_materially_relevant_correct_workspace_retrieval": bool(md["material_hit_ranks"]),
                "manual_relevance": md, "fixed_rust_implementation": cfg["fixed_rust_implementation"],
                "implementation_spans": {"code.rs": [209, 241], "adapter.rs": [[26, 45], [172, 185]], "search_format.rs": [193, 276]},
                "all_nonwhite_differences_retained": True, "arbitrary_difference_waiver": False,
            }
            mp = O / profile / f"mcp-reviewed-{n:04d}.json"
            put(mp, mcpdoc)
            mcp_refs.append(ref(mp))
            review.update(note="Complete native MCP input, root, results and every hit reviewed, with byte-positioned source checks and individual relevance labels.", mcp_review=ref(mp), relevant_correct_workspace_zg_retrieval=bool(md["material_hit_ranks"]))
            hits_total += len(all_hits)
        else:
            assert str(n) in dec.get("other_tool_notes", {})
            review["note"] = dec["other_tool_notes"][str(n)]
            review["non_file_tool_full_result"] = r["content"]
        if "note" in override:
            review["note"] = override["note"]
        review["strict_scope_gate_passed"] = review["strict_source_scope_valid"] is True
        p = out / f"tool-{n:04d}.json"
        put(p, review)
        reviewed_refs.append(ref(p))
        reviews.append(review)
    strict = tri([x["strict_source_scope_valid"] for x in reviews])
    assert strict is dec["strict_source_scope_valid"]
    retrieval = dec["relevant_correct_workspace_zg_retrieval"]
    if profile == "zvec-grep":
        assert retrieval == any(r.get("relevant_correct_workspace_zg_retrieval") for r in reviews)
    pdoc = {
        "profile": profile, "strict_source_scope_valid": strict, "strict_scope_gate_passed": strict is True,
        "factual_evidence_scope_valid": tri([r["factual_evidence_scope_valid"] for r in reviews]),
        "external_content_observed": any(r["external_content_observed"] for r in reviews),
        "external_code_or_answer_facts_observed": any(r["external_code_or_answer_facts_observed"] for r in reviews),
        "source_unchanged": source["source_unchanged"], "relevant_correct_workspace_zg_retrieval": retrieval,
        "retrieval_requirement_applies": profile == "zvec-grep",
        "scope_and_retrieval_gate_passed": strict is True and (profile == "baseline" or retrieval is True),
        "tools": len(reviews), "root_tools": q["root_tool_count"], "child_tools": q["child_tool_count"],
        "tool_counts": dict(collections.Counter(t["name"] for t in q["tools"])),
        "raw_tool_occurrences": cross["actual_tool_occurrences"],
        "read_source_lines": sum(r.get("source_lines", 0) for r in reviews),
        "bash_returned_lines": sum(r.get("returned_lines", 0) for r in reviews),
        "mcp_hits": hits_total, "mcp_line_comparisons": dict(mcp_lines),
        "source_snapshots": ref(O / profile / "source-snapshots.json"),
        "source_before_after": [1596, 1596],
        "predeclared_native_artifact_files_before_after": [len(p["additional_files"]) for p in source["phases"]],
        "native_artifact_review": dec["native_artifact_review"],
        "raw_occurrence_crosscheck": ref(O / profile / "extraction-crosscheck.json"),
        "extraction": ref(O / profile / "extracted/tool-audit.json"),
        "unresolved_evidence": [u for r in reviews for u in r["unresolved_evidence"]],
        "bash_cli_and_other_zg_channels_reviewed": True, "retrieval_channel_note": dec["retrieval_channel_note"],
    }
    profiles.append(pdoc)
report = {
    "schema_version": 2, "audit_kind": "independent_complete_source_tool_scope", "pair_key": O.name,
    "protocol_id": cfg["protocol_id"], "run_id": cfg["run_id"], "head_sha": cfg["head_sha"], "attempt": 1,
    "status": "review_complete",
    "strict_source_scope_valid": tri([p["strict_source_scope_valid"] for p in profiles]),
    "strict_scope_gate_passed": all(p["strict_scope_gate_passed"] for p in profiles),
    "scope_and_retrieval_gate_passed": all(p["scope_and_retrieval_gate_passed"] for p in profiles),
    "factual_evidence_scope_valid": tri([p["factual_evidence_scope_valid"] for p in profiles]),
    "external_content_observed": any(p["external_content_observed"] for p in profiles),
    "external_code_or_answer_facts_observed": any(p["external_code_or_answer_facts_observed"] for p in profiles),
    "source_unchanged": all(p["source_unchanged"] for p in profiles),
    "relevant_correct_workspace_zg_retrieval": profiles[1]["relevant_correct_workspace_zg_retrieval"],
    "tool_count": sum(p["tools"] for p in profiles), "root_tool_count": sum(p["root_tools"] for p in profiles),
    "child_tool_count": sum(p["child_tools"] for p in profiles),
    "raw_tool_occurrences": sum(p["raw_tool_occurrences"] for p in profiles),
    "read_source_lines": sum(p["read_source_lines"] for p in profiles),
    "bash_returned_lines": sum(p["bash_returned_lines"] for p in profiles),
    "mcp_hit_count": sum(p["mcp_hits"] for p in profiles),
    "source_commit": cfg["source_commit"], "source_inventory_sha256": cfg["source_inventory_sha256"],
    "source_file_count": 1596, "frozen_policy": cfg["policy"], "frozen_plan": cfg["plan"],
    "frozen_decision": cfg["decision"], "task_instruction": cfg["question"],
    "identity_and_receipt": ref(O / "input-identity-check.json"),
    "complete_local_source": ref(O / "source-file-verification.json"),
    "sealed_method_manifest": ref(M / "method-manifest.json"),
    "method_files": [ref(p) for p in M.iterdir() if p.is_file()],
    "manual_decisions": ref(O / "manual-decisions.json"),
    "all_bash_return_checks": ref(O / "static-return-checks.json"),
    "reviewed_tools": reviewed_refs, "complete_mcp_reviews": mcp_refs, "profiles": profiles,
    "unresolved_evidence": [u for p in profiles for u in p["unresolved_evidence"]],
    "scripts": [ref(O / name) for name in ("static-return-check.py", "publish-review.py")],
    "limits": [
        "Post-run complete native-tool evidence audit, not independent OS/network tracing.",
        "Raw command exit flags do not establish individual pipeline or conditional branch statuses; failed, empty and suppressed probes remain recorded.",
        "Source matches establish returned fact provenance, not answer correctness. All code was read as data.",
        "Any unknown strict operation remains null and cannot pass a gate. Known deviations remain false.",
        "Exact source-span C++ boundary attribution uses the pinned Rust implementation; no generic punctuation waiver or hidden query-group assumption.",
    ],
    "field_definition_factual": "Observed returned code/source facts are supported by frozen source. Outside-file contents and outside code/answer facts are separately reported. This does not claim hidden actions absent or final answers correct.",
    "final_answers_grades_or_private_mapping_read": False, "quality_or_resource_comparison_performed": False,
    "solver_project_commands_executed_by_auditor": False, "policy_exceptions_added": False, "historical_reports_modified": False,
}
put(O / "report.json", report)
print(json.dumps({"report": ref(O / "report.json"), "strict": report["strict_source_scope_valid"], "retrieval": report["relevant_correct_workspace_zg_retrieval"], "tools": report["tool_count"], "mcp_hits": report["mcp_hit_count"]}))
