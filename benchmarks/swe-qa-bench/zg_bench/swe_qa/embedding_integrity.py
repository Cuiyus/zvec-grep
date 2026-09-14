"""Compare model artifact hashes while auditing one published cache marker.

Released @zvec/zvec-grep@0.2.2 dist/engine/models/artifact-downloader.js
lines 587–608 writes the completion marker from artifact size, mtimeMs and
ctimeMs. Lines 298–365 hash artifacts when that stat cache no longer matches;
lines 614–624 explicitly describe the marker as a hashing optimization.

The verified release dist SHA256 is
8efa0a4e60d65a644da145556ea88e0db399c1fa51fd53a7e493f7a5bd28f5ea.
The exception below is an exact path for the benchmark's fixed local model,
not a general rule for hidden files or files ending in .complete.
"""
from __future__ import annotations

import re
from typing import Any

MODEL_DIRECTORY = "model2vec/minishlab--potion-code-16M-v2/e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b"
MUTABLE_COMPLETION_MARKER = MODEL_DIRECTORY + "/.zvec-grep-artifacts-7db565dd4d3a5969b4c61568.complete"
REQUIRED_MODEL_ARTIFACTS = (
    MODEL_DIRECTORY + "/model.safetensors",
    MODEL_DIRECTORY + "/tokenizer/tokenizer.json",
    MODEL_DIRECTORY + "/tokenizer/tokenizer_config.json",
)
RELEASE_SOURCE = {
    "package": "@zvec/zvec-grep@0.2.2",
    "module": "dist/engine/models/artifact-downloader.js",
    "marker_writer_lines": [587, 608],
    "cache_validation_lines": [298, 365],
    "best_effort_marker_lines": [614, 624],
    "dist_sha256": "8efa0a4e60d65a644da145556ea88e0db399c1fa51fd53a7e493f7a5bd28f5ea",
    "marker_fields": {"version": "number", "fingerprint": "string",
                      "files": {"artifact_relative_path": ["size", "mtimeMs", "ctimeMs"]}},
}


def _hashes(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(path, str) and bool(path) and isinstance(sha, str)
        and re.fullmatch(r"[0-9a-f]{64}", sha) is not None for path, sha in value.items())


def compare_embedding_cache(before: Any, after: Any) -> dict[str, Any]:
    """Validate two path→SHA256 inventories without changing either inventory.

Identical nonempty inventories remain compatible with other model fixtures.
Only a changed known completion marker can take the exception path. That path
requires both marker inventories and all three real model artifacts, with all
non-marker files byte-identical. Missing inventories never inherit a prior
boolean claim that weights were unchanged.
"""
    result: dict[str, Any] = {
        "schema_version": 1, "profile": "embedding-artifacts-v1", "valid": False,
        "artifact_files_unchanged": False, "cache_directory_unchanged": False,
        "mutable_metadata_changes": [], "unexpected_changes": [], "validation_errors": [],
        "metadata_exception_applied": False,
        "comparison_scope": "File content SHA256 inventories; a marker exception does not claim the entire cache directory stayed unchanged.",
        "known_mutable_metadata_path": MUTABLE_COMPLETION_MARKER, "release_source": RELEASE_SOURCE,
    }
    if not _hashes(before) or not _hashes(after):
        result["validation_errors"].append("before_and_after_must_be_path_to_sha256_inventories")
        return result
    if not before or not after:
        result["validation_errors"].append("before_and_after_inventories_must_both_be_nonempty")
        return result
    changes = [{"path": path, "before": before.get(path), "after": after.get(path)}
               for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)]
    before_artifacts = {p: h for p, h in before.items() if p != MUTABLE_COMPLETION_MARKER}
    after_artifacts = {p: h for p, h in after.items() if p != MUTABLE_COMPLETION_MARKER}
    result.update(cache_directory_unchanged=not changes,
                  artifact_files_unchanged=before_artifacts == after_artifacts,
                  before_file_count=len(before), after_file_count=len(after),
                  before_artifact_count=len(before_artifacts), after_artifact_count=len(after_artifacts))
    if not changes:
        result["valid"] = True
        return result
    metadata = [change for change in changes if change["path"] == MUTABLE_COMPLETION_MARKER]
    unexpected = [change for change in changes if change["path"] != MUTABLE_COMPLETION_MARKER]
    result["mutable_metadata_changes"] = metadata
    result["unexpected_changes"] = unexpected
    if unexpected:
        result["validation_errors"].append("non_marker_file_added_removed_or_changed")
    if metadata:
        required = (*REQUIRED_MODEL_ARTIFACTS, MUTABLE_COMPLETION_MARKER)
        missing = {"before": [p for p in required if p not in before],
                   "after": [p for p in required if p not in after]}
        result["required_artifact_paths"] = list(REQUIRED_MODEL_ARTIFACTS)
        result["missing_required_paths"] = missing
        if any(missing.values()):
            result["validation_errors"].append("marker_exception_requires_fixed_model_artifacts_and_marker_in_both_inventories")
        result["metadata_exception_applied"] = not result["validation_errors"]
        result["metadata_interpretation"] = (
            "This exact release marker caches artifact size/mtime/ctime and can change after copying. "
            "Only marker hashes are available here; exact changed JSON fields are not inferred.")
    result["valid"] = not result["validation_errors"] and bool(metadata)
    return result
