#!/usr/bin/env python3
"""Bake the exact model files named and hashed by current main into the image."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--record-cache", action="store_true")
args = parser.parse_args()
provenance_path = Path("/opt/qa/zg-provenance.json")
provenance = json.loads(provenance_path.read_text())
model = provenance["embedding"]
cache = Path(os.environ["ZVEC_GREP_MODEL_CACHE"])
root = cache / "model2vec" / model["repo"].replace("/", "--") / model["revision"]
if not args.record_cache:
    for remote_path, expected in model["files"].items():
        destination = root / expected["cache_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        urls = [f'https://huggingface.co/{model["repo"]}/resolve/{model["revision"]}/{remote_path}',
                f'https://modelscope.cn/models/{model["repo"]}/resolve/{model["model_scope_revision"]}/{remote_path}']
        failure = None
        for url in urls:
            try:
                with urllib.request.urlopen(url, timeout=120) as response:
                    data = response.read()
                if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
                    raise ValueError("Pinned model checksum mismatch: " + remote_path)
                destination.write_bytes(data)
                failure = None
                break
            except Exception as error:
                failure = error
        if failure is not None:
            raise failure
else:
    provenance["model_cache_files"] = {
        str(path.relative_to(cache)): {"bytes": path.stat().st_size,
                                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(cache.rglob("*")) if path.is_file()
    }
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n")
