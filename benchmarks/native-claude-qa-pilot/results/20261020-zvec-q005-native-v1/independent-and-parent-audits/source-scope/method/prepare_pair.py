"""Prepare one explicitly ready confirmation pair for manual source-scope review.

No network, CI dispatch, provider calls, solver command execution or final verdicts.
Existing output is never overwritten; extraction/format errors are retained.
"""
import argparse
import collections
import sys
import types
from pathlib import Path

sys.dont_write_bytecode = True
from scope_support import (
    bash_hints, crosscheck, load, mcp_text_check, read_check, ref, sha, write,
)

METHOD = Path(__file__).resolve().parent


def pinned(config, name):
    record = config[name]
    path = Path(record["path"])
    if path.is_symlink() or not path.is_file() or sha(path) != record["sha256"]:
        raise ValueError("Pinned method input missing/changed: " + name)
    return path


def source_snapshot(path, manifest, profile):
    audit = load(path)
    phases = []
    for phase in ("before", "after"):
        block = audit.get(phase, {})
        rows = block.get("observed", {}).get("files")
        if not isinstance(rows, list):
            phases.append({"phase": phase, "source_unchanged": None,
                           "diagnostics": ["complete observed file inventory unavailable"]})
            continue
        observed = {x.get("path"): x for x in rows if isinstance(x, dict)}
        duplicate_paths = len(observed) != len(rows)
        mismatches = []
        for rel, expected in manifest.items():
            actual = observed.get(rel)
            if not isinstance(actual, dict) or actual.get("kind") != "file" or any(
                    actual.get(k) != expected[k] for k in ("bytes", "sha256")):
                mismatches.append({"path": rel, "expected": expected, "actual": actual})
        extras = [x for rel, x in observed.items() if rel not in manifest]
        ordinary_index_paths = all(
            profile == "zvec-grep" and x.get("kind") == "file"
            and (str(x.get("path", "")).startswith(".zvec-grep/")
                 or x.get("path") == ".zg-bench-index.ignore") for x in extras)
        phases.append({
            "phase": phase, "frozen_file_count": len(manifest), "mismatches": mismatches,
            "duplicate_observed_paths": duplicate_paths,
            "source_unchanged": False if mismatches else None if duplicate_paths else True,
            "additional_files": extras,
            "all_extra_paths_match_predeclared_native_artifact_names": ordinary_index_paths,
            "native_artifact_semantic_review_still_required": True,
            "collector_errors_preserved": block.get("errors", []),
        })
    values = [x["source_unchanged"] for x in phases]
    unchanged = False if False in values else True if values == [True, True] else None
    return {"evidence": ref(path), "source_unchanged": unchanged, "phases": phases,
            "collector_errors_preserved": audit.get("errors", [])}


def prepare(key):
    seal = load(METHOD / "method-manifest.json")
    for record in seal["method_files"]:
        path = Path(record["path"])
        if path.parent != METHOD or path.is_symlink() or sha(path) != record["sha256"]:
            raise ValueError("Sealed audit method changed; retain a new reviewed version.")
    config = load(METHOD / "frozen-method-inputs.json")
    if key not in config["schedule"]:
        raise ValueError("Only the ten specified q004 confirmation keys are accepted.")
    plan = pinned(config, "plan")
    pinned(config, "decision")
    pinned(config, "policy")
    extractor_path = pinned(config, "extractor")
    accounting_path = pinned(config, "accounting")
    manifest_path = pinned(config, "source_manifest")
    pinned(config, "question")
    folder = Path(config["raw_run_root"]) / key
    raw = folder / "raw"
    receipt_path = folder / "artifact-byte-verification.json"
    context_path = raw / "live-q004-repeat-context.json"
    # No polling or substitute inputs; missing readiness fails before creating pair output.
    if not receipt_path.is_file() or not context_path.is_file():
        raise ValueError("Pair is not ready: receipt/context missing. No absence-of-access conclusion.")
    output = METHOD / "pairs" / key
    output.mkdir(parents=True, exist_ok=False)
    diagnostics = []
    receipt, context = load(receipt_path), load(context_path)
    expected_receipt = {
        "run_id": config["run_id"], "head_sha": config["head_sha"], "run_attempt": 1,
        "pair_key": key, "task": "q004", "complete": True, "matches_api_digest": True,
        "exact_extracted_inventory": True, "repository": "Cuiyus/zvec-grep",
    }
    for field, expected in expected_receipt.items():
        if receipt.get(field) != expected:
            diagnostics.append({"kind": "receipt_mismatch", "field": field,
                                "expected": expected, "actual": receipt.get(field)})
    expected_context = {
        "pair_key": key, "task_id": "q004", "protocol_id": config["protocol_id"],
        "profiles": config["schedule"][key], "plan_sha256": sha(plan), "dry_run": False,
    }
    for field, expected in expected_context.items():
        if context.get(field) != expected:
            diagnostics.append({"kind": "context_mismatch", "field": field,
                                "expected": expected, "actual": context.get(field)})
    identity = {"receipt": ref(receipt_path), "context": ref(context_path),
                "actual_context": context, "expected_profiles": config["schedule"][key],
                "diagnostics": diagnostics}
    write(output / "input-identity-check.json", identity)
    source = Path(config["source_root"])
    md = load(manifest_path)
    manifest = {x["path"]: x for x in md["files"]}
    source_files = []
    expected_names = set(manifest)
    actual_names = {x.relative_to(source).as_posix() for x in source.rglob("*") if x.is_file()}
    source_ok = actual_names == expected_names and not any(x.is_symlink() for x in source.rglob("*"))
    for rel, record in manifest.items():
        path = source / rel
        try:
            item = ref(path)
            item["matches_manifest"] = item["bytes"] == record["bytes"] and item["sha256"] == record["sha256"]
            source_ok = source_ok and item["matches_manifest"] and not path.is_symlink()
        except OSError as exc:
            item = {"path": str(path), "error": str(exc)}
            source_ok = False
        source_files.append(item)
    source_ok = source_ok and len(manifest) == 1596 and md["inventory_sha256"] == config["source_inventory_sha256"]
    write(output / "source-file-verification.json", {
        "source_commit": md["source_commit"], "expected_source_commit": config["source_commit"],
        "file_count": len(manifest), "complete_inventory_matches": source_ok,
        "source_files": source_files, "extra_local_paths": sorted(actual_names - expected_names)})
    if not source_ok or md["source_commit"] != config["source_commit"]:
        raise ValueError("Frozen source verification failed; retained identity/source diagnostics, no tool approval.")
    source_lines = {rel: (source / rel).read_text(errors="replace").split("\n") for rel in manifest}
    module = types.ModuleType("frozen_native_scope_extractor")
    module.__file__ = str(extractor_path)
    exec(compile(extractor_path.read_bytes(), str(extractor_path), "exec"), module.__dict__)
    profiles = []
    # Validate and audit actual order rather than assuming baseline-first.
    for profile in config["schedule"][key]:
        destination = output / profile
        destination.mkdir()
        matches = list((raw / "live/runs" / ("q001-" + profile)).glob("zvec-q004__*/agent"))
        if len(matches) != 1 or matches[0].is_symlink():
            error = {"kind": "ambiguous_or_missing_trial", "profile": profile,
                     "matches": [str(x) for x in matches], "strict_source_scope_valid": None}
            write(destination / "preparation-failure.json", error)
            diagnostics.append(error)
            profiles.append(error)
            continue
        agent = matches[0]
        try:
            queue = module.extract(agent, destination / "extracted", accounting_path)
            integrity = crosscheck(queue)
            write(destination / "extraction-crosscheck.json", integrity)
            collection_accounting_path = raw / "live/collection" / (profile + "-strict-accounting.json")
            accounting = load(collection_accounting_path)
            collection_equals_extractor = {
                (x["session"], x["id"]): x for x in accounting["tools"]
            } == {(x["session"], x["id"]): x["frozen_accounting_identity"] for x in queue["tools"]}
            snapshots = source_snapshot(agent.parent / "qa-source-audit.json", manifest, profile)
            write(destination / "source-snapshots.json", snapshots)
            reviews = []
            for tool in queue["tools"]:
                check = {"ordinal": tool["ordinal"], "session": tool["session"],
                         "name": tool["name"], "tool_id": tool["id"],
                         "evidence": ref(destination / "extracted" / tool["evidence_path"]),
                         "manual_review_status": "pending",
                         "strict_source_scope_valid": None, "factual_evidence_scope_valid": None,
                         "external_content_observed": None, "external_code_or_answer_facts_observed": None,
                         "relevant_correct_workspace_zg_retrieval": None,
                         "unresolved_evidence": ["Full input/operators/paths/results require independent semantic review."]}
                if len(tool["call_variants"]) == len(tool["result_variants"]) == 1:
                    call, result = tool["call_variants"][0], tool["result_variants"][0]
                    args = call["input"]
                    if tool["name"] == "Read":
                        check["mechanical_check"] = read_check(args, result, source_lines, manifest)
                    elif tool["name"] == "Bash":
                        check["mechanical_check"] = bash_hints(args, result, source_lines, manifest)
                    elif tool["name"] == "mcp__zvec_grep__zvec_grep_search":
                        blocks = result["content"]
                        texts = [blocks] if isinstance(blocks, str) else [
                            x["text"] for x in blocks if isinstance(x, dict) and isinstance(x.get("text"), str)
                        ] if isinstance(blocks, list) else []
                        check["mechanical_check"] = {
                            "requested_root": args.get("root"), "root_equals_workspace": args.get("root") == "/workspace",
                            "native_is_error": result["is_error"],
                            "result_metadata_matches_content": result["metadata"] == blocks,
                            "text_blocks": [mcp_text_check(text, source_lines, manifest) for text in texts],
                            "manual_success_and_relevance_required": True}
                    else:
                        check["mechanical_check"] = {
                            "unrecognized_or_other_tool_preserved": True,
                            "do_not_treat_task_planning_metadata_as_external_file_content_automatically": True}
                else:
                    check["variant_diagnostic"] = "Multiple/missing variants retained; do not silently select one."
                target = destination / f"tool-check-{tool['ordinal']:04d}.json"
                write(target, check)
                reviews.append(ref(target))
            # Hash only authorized raw tool/accounting/source records, never answer/grading files.
            receipt_files = {x["path"]: x for x in receipt.get("files", [])}
            authorized_refs = integrity["raw_files"] + [
                ref(collection_accounting_path), ref(agent.parent / "qa-source-audit.json")]
            receipt_checks = []
            for item in authorized_refs:
                rel = Path(item["path"]).relative_to(raw).as_posix()
                record = receipt_files.get(rel)
                receipt_checks.append({"path": rel, "actual": item, "receipt_record": record,
                                       "matches": bool(record and all(item[k] == record[k] for k in ("bytes", "sha256")))})
            profiles.append({
                "profile": profile, "agent_directory": str(agent), "tool_count": queue["unique_tool_count"],
                "root_tool_count": queue["root_tool_count"], "child_tool_count": queue["child_tool_count"],
                "tool_counts": dict(collections.Counter(x["name"] for x in queue["tools"])),
                "extraction_complete": queue["extraction_complete"], "extraction_errors": queue["errors"],
                "extraction": ref(destination / "extracted/tool-audit.json"),
                "exhaustive_crosscheck": ref(destination / "extraction-crosscheck.json"),
                "collection_accounting": ref(collection_accounting_path),
                "collection_tool_identity_equals_extractor": collection_equals_extractor,
                "source_snapshots": ref(destination / "source-snapshots.json"),
                "authorized_raw_references_match_receipt": receipt_checks,
                "tool_checks": reviews, "manual_review_status": "pending",
                "strict_source_scope_valid": None, "factual_evidence_scope_valid": None,
                "external_content_observed": None, "external_code_or_answer_facts_observed": None,
                "source_unchanged": snapshots["source_unchanged"],
                "relevant_correct_workspace_zg_retrieval": None, "strict_scope_gate_pass": False})
        except Exception as exc:
            error = {"profile": profile, "kind": "preparation_exception", "error_type": type(exc).__name__,
                     "message": str(exc), "evidence_preserved": True, "strict_source_scope_valid": None}
            write(destination / "preparation-failure.json", error)
            profiles.append(error)
            diagnostics.append(error)
    pending = {"pair_key": key, "protocol_id": config["protocol_id"],
               "run_id": config["run_id"], "head_sha": config["head_sha"],
               "manual_review_status": "pending", "audit_complete": False,
               "strict_source_scope_valid": None, "factual_evidence_scope_valid": None,
               "strict_scope_gate_pass": False, "source_and_retrieval_gate_pass": False,
               "quality_graded": False, "resource_comparison_performed": False,
               "input_identity": ref(output / "input-identity-check.json"),
               "frozen_source_verification": ref(output / "source-file-verification.json"),
               "method_inputs": ref(METHOD / "frozen-method-inputs.json"),
               "diagnostics": diagnostics, "profiles": profiles,
               "unresolved_evidence": ["Independent full per-tool semantic audit has not been completed."]}
    write(output / "review-pending.json", pending)
    return {"pair_key": key, "output": str(output), "manual_review_status": "pending",
            "audit_complete": False, "profiles_prepared": len(profiles)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", required=True, help="Explicitly authorized ready key q004-r01..r10.")
    arguments = parser.parse_args()
    import json
    print(json.dumps(prepare(arguments.pair)))
