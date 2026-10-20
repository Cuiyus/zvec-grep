from scope_support import load, ref

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


