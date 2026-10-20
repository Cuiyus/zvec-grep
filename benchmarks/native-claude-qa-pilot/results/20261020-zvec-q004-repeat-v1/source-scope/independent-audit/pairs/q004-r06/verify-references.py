"""Rehash references reachable from report; only parse auditor-generated local JSON."""
from pathlib import Path
import hashlib
import json
O = Path(__file__).parent
refs = {}
parsed = set()
errors = []
def sha(b):
    return hashlib.sha256(b).hexdigest()
def walk(value, origin):
    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and Path(value["path"]).is_absolute() and "bytes" in value and "sha256" in value:
            p = Path(value["path"])
            identity = (str(p), value["bytes"], value["sha256"])
            if identity not in refs:
                data = p.read_bytes()
                item = {"path": str(p), "bytes": len(data), "sha256": sha(data), "referenced_by": origin}
                item["matches"] = len(data) == value["bytes"] and sha(data) == value["sha256"]
                refs[identity] = item
                if not item["matches"]:
                    errors.append(item)
                if p.is_relative_to(O) and p.suffix == ".json" and p not in parsed:
                    parsed.add(p)
                    walk(json.loads(data), str(p))
        for v in value.values():
            walk(v, origin)
    elif isinstance(value, list):
        for v in value:
            walk(v, origin)
report = O / "report.json"
data = report.read_bytes()
walk({"path": str(report), "bytes": len(data), "sha256": sha(data)}, "verification root")
result = {
    "report": {"path": str(report), "bytes": len(data), "sha256": sha(data)},
    "all_references_match": not errors, "reference_count": len(refs),
    "checks": list(refs.values()), "errors": errors,
    "parsed_json_scope": "Only auditor-generated JSON under this pair output; external/raw references rehashed without parsing non-tool content.",
}
out = O / "reference-verification.json"
with out.open("x") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(json.dumps({"all_match": not errors, "references": len(refs), "verification_sha256": sha(out.read_bytes()), "report_sha256": sha(data)}))
assert not errors
