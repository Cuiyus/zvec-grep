"""Read recorded CI artifacts and print source-linked, human-readable audit rows.

No model requests, credentials, raw request bodies, or artifact mutation. This
post-processing workflow is separate from the paid benchmark workflow.
"""
import argparse
import hashlib
import json
from pathlib import Path

from zg_bench.swe_qa.e2e_analysis import analyze_runs
from zg_bench.swe_qa.retrieval_eval import load_manifest

SOURCE_RUN = "34255587426"
SOURCE_COMMIT = "dc0c2f2a7c52cbeb127e90e9a8cfcac2b8ab81a7"


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(root, case, entries):
    before = {str(p.relative_to(root)): digest(p) for p in root.rglob("*") if p.is_file()}
    manifest = read(root / "manifest.json")
    if str(manifest["ci_identity"]["GITHUB_RUN_ID"]) != SOURCE_RUN or manifest["ci_identity"]["GITHUB_SHA"] != SOURCE_COMMIT:
        raise ValueError("Source artifact CI identity mismatch")
    fresh = analyze_runs(root, case, entries)
    original = read(root / "e2e-analysis.json")
    quality = read(root / "quality-review.json")
    original_trials = {t["trial_id"]: t for t in original["trials"]}
    reports = []
    for trial in fresh["trials"]:
        trial_id = trial["trial_id"]
        if trial["metrics"] != original_trials[trial_id]["metrics"]:
            raise ValueError("Regenerated metrics differ for " + trial_id)
        result = read(root / trial_id / "result.json")
        qa = next(t for t in quality["trials"] if t["trial_id"] == trial_id)
        wire_path = root / trial_id / "agent" / "wire.jsonl"
        wire = [json.loads(x) for x in wire_path.read_text().splitlines()] if wire_path.exists() else []
        requests = {e["request_id"]: e for e in wire if e.get("event") == "request"}
        responses = {e["request_id"]: e for e in wire if e.get("event") == "response"}
        broken = [e for e in wire if e.get("event") == "transport_error"]
        broken += [e for e in responses.values() if e.get("status") != 200 or not e.get("model")]
        missing = [i for i in requests if i not in responses]
        row = {
            "kind": "trial", "group": root.name, "trial_id": trial_id, "profile": trial["profile"],
            "status": trial["status"], "metrics": trial["metrics"], "session": trial["session"],
            "phases": trial["trace"]["phases"], "first_entry": trial["trace"]["first_useful_entry"],
            "usage_reconciliation": trial["usage_reconciliation"], "readonly_integrity": trial["readonly_integrity"],
            "wire_contract": result.get("wire_contract"), "tool_contract": result.get("tool_contract"),
            "model_identity": result.get("model_identity"), "error": result.get("error"),
            "conversion_error": result.get("conversion_error"),
            "provider_wire_issues": {"request_count": len(requests), "response_count": len(responses),
                                     "missing_response_ids": missing, "errors": broken},
            "quality": qa,
            "artifact_file_hashes": {key: before[key] for key in before if key.startswith(trial_id + "/")},
        }
        reports.append(row)
    after = {str(p.relative_to(root)): digest(p) for p in root.rglob("*") if p.is_file()}
    if before != after:
        raise ValueError("Audit modified original artifact")
    summary = {
        "kind": "group", "group": root.name, "agent": manifest["agent"], "model": manifest["model"],
        "source_run": SOURCE_RUN, "source_commit": SOURCE_COMMIT, "file_count": len(before),
        "source_files_verified_by_original_ci": manifest.get("source_files"),
        "metrics_recomputed_identically": True, "artifacts_unchanged": True,
        "root_file_hashes": {k: v for k, v in before.items() if "/" not in k},
        "configurations": fresh["configurations"], "quality_gate": quality["quality_gate"],
        "quality_summary": quality["summary"], "calibration": quality["calibration"],
        "retrieval": read(root / "entry-report.json"),
    }
    return [summary, *reports]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--entries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    roots = sorted(p.parent for p in args.artifacts.glob("*/plan.json"))
    if len(roots) != 3:
        raise ValueError("All three existing artifacts are required")
    rows = []
    case, entries = read(args.case), load_manifest(args.entries)
    for root in roots:
        rows.extend(audit(root, case, entries))
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    # Emit normal readable JSON, split into bounded records so the Actions log
    # cannot truncate a large single line. No encoding or binary transport.
    for row in rows:
        group, kind = row["group"], row["kind"]
        trial = row.get("trial_id")
        def emit(value, path):
            if len(json.dumps(value, ensure_ascii=False).encode()) > 18000:
                if isinstance(value, dict):
                    for k, v in value.items():
                        emit(v, [*path, k])
                    return
                if isinstance(value, list):
                    for i, v in enumerate(value):
                        emit(v, [*path, i])
                    return
            print("QA-AUDIT " + json.dumps({"group": group, "kind": kind, "trial_id": trial, "path": path, "value": value}, ensure_ascii=False), flush=True)
        for key, value in row.items():
            if key in {"group", "kind", "trial_id"}:
                continue
            if key == "retrieval":
                value = {k: value[k] for k in ("manifest_id", "planned_executions", "source_files_verified", "query_quality", "repeat_stability", "event_parse_errors")}
            if key == "source_files_verified_by_original_ci":
                continue  # Full corpus identity stays in the original artifact.
            emit(value, [key])


if __name__ == "__main__":
    main()
