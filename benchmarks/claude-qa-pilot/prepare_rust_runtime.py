#!/usr/bin/env python3
"""Verify the existing current-main Rust artifact and prepare Docker inputs.

Download rust-native-linux-x64-gnu from upstream run 39221698769 using a
read-only artifact request, then pass its directory. This never triggers CI.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile

COMMIT = "a424e0ce06868dc9ad8f142f57de59b8f9b58699"
PACKAGE = "zvec-zvec-grep-linux-x64-gnu-0.0.1.tgz"
PACKAGE_SHA = "470c2a11b8b19a9e6458c977918260ee6f535595488ed8b44233db5aa032ad4d"
BINARY_SHA = "645ef8b62d62ff604c06f3c8aa34790c3c28bc65c946714ecdbf7175131b61dd"
MODEL_FILES = {
    "model.safetensors": {"bytes": 32490072, "sha256": "75cf7a6c2171b230ad19b1e7d8e0b1aee86da5a02af8e7cacedd9921d227623c",
                          "cache_path": "model.safetensors"},
    "tokenizer.json": {"bytes": 1024340, "sha256": "107bbdcbad4bff1d299b7a4c3a2fb17c52890688b7dd0e4c9deab79d3c4f3d45",
                       "cache_path": "tokenizer/tokenizer.json"},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--runtime-dir", required=True, type=Path)
    args = parser.parse_args()
    source = args.artifact_dir / PACKAGE
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != PACKAGE_SHA:
        raise ValueError("Rust package SHA256 does not match the pinned current-main artifact")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
        if any(not member.isfile() or not member.name.startswith("package/") or
               ".." in Path(member.name).parts for member in members.values()):
            raise ValueError("Unexpected native package entry")
        checksums = json.load(archive.extractfile("package/checksums.json"))
        package = json.load(archive.extractfile("package/package.json"))
        if package["name"] != "@zvec/zvec-grep-linux-x64-gnu" or package["version"] != "0.0.1":
            raise ValueError("Unexpected native package identity")
        if checksums["target"] != "linux-x64-gnu":
            raise ValueError("Unexpected native target")
        for relative, expected in checksums["files"].items():
            contents = archive.extractfile("package/" + relative).read()
            if len(contents) != expected["bytes"] or hashlib.sha256(contents).hexdigest() != expected["sha256"]:
                raise ValueError("Native package checksum mismatch: " + relative)
        if checksums["files"]["bin/zg"]["sha256"] != BINARY_SHA:
            raise ValueError("Unexpected native executable")
    provenance = {
        "schema_version": 1, "implementation": "Rust", "commit": COMMIT,
        "repository": "zvec-ai/zvec-grep", "workflow_run_id": 39221698769,
        "workflow_name": "Publish Rust packages", "workflow_conclusion": "success",
        "workflow_artifact": {"id": 12176409568, "name": "rust-native-linux-x64-gnu",
                              "archive_sha256": "00873b7a5505bb4f628c5d33336c058750cb09dcccccc8ae2718fa8d0cfb0e03"},
        "package_name": package["name"], "package_version": package["version"],
        "package_sha256": PACKAGE_SHA, "binary_sha256": BINARY_SHA,
        "native_files": checksums["files"],
        "embedding": {"reference": "local/potion-code-16m-v2", "repo": "minishlab/potion-code-16M-v2",
                      "revision": "e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b",
                      "model_scope_revision": "3e922fde18f43b8db69f3381b6d468738d3dd2d7",
                      "files": MODEL_FILES},
    }
    args.runtime_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, args.runtime_dir / "zg-native.tgz")
    (args.runtime_dir / "zg-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"commit": COMMIT, "package_sha256": PACKAGE_SHA, "binary_sha256": BINARY_SHA}))


if __name__ == "__main__":
    main()
