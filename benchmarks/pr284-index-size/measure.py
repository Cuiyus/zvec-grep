"""Measure a restored, immutable SWE-QA index seed without opening the engine."""
import argparse
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--expected", type=Path, required=True)
parser.add_argument("--task", required=True)
parser.add_argument("--variant", choices=("fp32", "fp16"), required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
expected = json.loads(args.expected.read_text())[args.task][args.variant]
root = args.root / expected["seed_key"]
assert not root.is_symlink() and root.is_dir(), "Expected seed missing"
assert (root / "complete").read_text().strip() == expected["seed_key"]
identity_bytes = (root / "identity.json").read_bytes()
identity = json.loads(identity_bytes)
assert identity == expected["identity"], "Seed identity differs from the E2E trials"
assert hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == expected["seed_key"]
workspace = root / "workspace"
assert workspace.is_dir() and not workspace.is_symlink()
entries = []
directory_bytes = 0
directory_blocks = 0
metadata = {}
for path in sorted(workspace.rglob("*")):
    s = path.lstat()
    relative = path.relative_to(workspace).as_posix()
    assert not stat.S_ISLNK(s.st_mode), "Unexpected symlink"
    if stat.S_ISDIR(s.st_mode):
        directory_bytes += s.st_size
        directory_blocks += s.st_blocks * 512
        continue
    assert stat.S_ISREG(s.st_mode), "Unexpected special file"
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    entries.append({"path": relative, "bytes": s.st_size, "allocated_bytes": s.st_blocks * 512,
                    "sha256": digest.hexdigest(), "links": s.st_nlink})
    if path.suffix == ".json" and s.st_size < 2 * 1024 * 1024:
        try:
            metadata[relative] = json.loads(path.read_text())
        except (ValueError, UnicodeError):
            pass
assert entries, "Empty index"
storage_files = [entry for entry in entries if "storage" in Path(entry["path"]).parts]
assert storage_files, "No storage directory in the original seed"
storage_groups = collections.defaultdict(lambda: {"bytes": 0, "allocated_bytes": 0, "files": 0})
for entry in storage_files:
    parts = Path(entry["path"]).parts
    i = parts.index("storage")
    group = parts[i + 1] if i + 1 < len(parts) else "(root)"
    target = storage_groups[group]
    for metric in ("bytes", "allocated_bytes"):
        target[metric] += entry[metric]
    target["files"] += 1
result = {
    "task": args.task, "variant": args.variant,
    "measured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "scope": "Original cached .zvec-grep workspace, before Agent execution; index not opened or rebuilt",
    "cache_key": expected["cache_key"], "compressed_cache_bytes": expected["compressed_cache_bytes"],
    "cache_id": expected["cache_id"], "seed_key": expected["seed_key"],
    "identity": identity, "identity_file_sha256": hashlib.sha256(identity_bytes).hexdigest(),
    "expected_source_stats": expected["source_stats"],
    "workspace_file_bytes": sum(entry["bytes"] for entry in entries),
    "workspace_allocated_file_bytes": sum(entry["allocated_bytes"] for entry in entries),
    "directory_apparent_bytes": directory_bytes + workspace.lstat().st_size,
    "directory_allocated_bytes": directory_blocks + workspace.lstat().st_blocks * 512,
    "storage_file_bytes": sum(entry["bytes"] for entry in storage_files),
    "storage_allocated_file_bytes": sum(entry["allocated_bytes"] for entry in storage_files),
    "file_count": len(entries), "storage_groups": dict(storage_groups),
    "files": entries, "small_json_metadata": metadata,
    "filesystem": subprocess.check_output(["df", "--output=fstype", str(workspace)], text=True).strip().splitlines()[-1],
    "run_id": os.environ.get("GITHUB_RUN_ID"),
    "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    "measurement_commit": os.environ.get("GITHUB_SHA"),
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: result[key] for key in ("task", "variant", "workspace_file_bytes", "storage_file_bytes", "file_count")}))
