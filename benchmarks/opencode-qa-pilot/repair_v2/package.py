#!/usr/bin/env python3
"""Derive a local npm-installable tgz without changing the pinned Rust payload.

This is deliberately not a general package rewriter. The public entry point
accepts only the original, SHA-pinned v1 platform artifact. It adds the missing
npm ``bin`` declaration and records that the resulting archive is derived.
It does not install, execute, publish, download, or rebuild anything.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import tarfile
import tempfile


UPSTREAM_COMMIT = "a424e0ce06868dc9ad8f142f57de59b8f9b58699"
UPSTREAM_ARCHIVE_SHA256 = "470c2a11b8b19a9e6458c977918260ee6f535595488ed8b44233db5aa032ad4d"
UPSTREAM_ARCHIVE_BYTES = 40228623
BINARY_SHA256 = "645ef8b62d62ff604c06f3c8aa34790c3c28bc65c946714ecdbf7175131b61dd"
PACKAGE_JSON = "package/package.json"
BIN_DECLARATION = {"zg": "bin/zg"}
BUILDER_REPOSITORY_PATH = "benchmarks/opencode-qa-pilot/repair_v2/package.py"
GZIP_HEADER = bytes.fromhex("1f8b08000000000000ff")
STORED_BLOCK_BYTES = 65535

# Path -> (length, SHA-256, permission bits). These pin *all* seven files;
# checksums.json itself covers only the four runtime payloads, by upstream design.
EXPECTED_MEMBERS = {
    "package/README.md": (110, "83dd5863fecb9e0713b0b252d7b6ba39f9c448c932bafe9dd9e25d6dd645ce94", 0o644),
    "package/bin/data/jieba_dict/hmm_model.utf8": (519739, "f17790586ac86dd048c8adffed052c4bd2b28ed0682972c1275e59040c0589a7", 0o644),
    "package/bin/data/jieba_dict/jieba.dict.utf8": (5071207, "6f7d4350e8861ef4139b2e3a6fad05430c19ae71f4b8378190edecac8aae2e6a", 0o644),
    "package/bin/libzvec_c_api.so": (38067280, "58381ac7b12afd5eeae3dc10325914a28fc3157061291bb693a9ed757d815b8a", 0o755),
    "package/bin/zg": (79479864, BINARY_SHA256, 0o755),
    "package/checksums.json": (663, "503c03928ef0886f6fec40587f8710228781f1e3f4594f8aeaaf62d23b67b953", 0o644),
    PACKAGE_JSON: (489, "ff0acbf3ea934d6b6709f011f7a0aa15fa440d2aeecd9aec11376bcfb3603062", 0o644),
}
RUNTIME_PATHS = frozenset(path for path in EXPECTED_MEMBERS if path.startswith("package/bin/"))


class PackageValidationError(ValueError):
    """An input does not satisfy the frozen platform-package contract."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackageValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(data: bytes, label: str) -> dict:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackageValidationError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackageValidationError(f"{label} must be a JSON object")
    return value


def _validate_headers(members: list[tarfile.TarInfo]) -> None:
    # Check every name/type before reading any member contents. No extraction is
    # used anywhere in this builder, including for a source that fails validation.
    names = []
    for member in members:
        path = PurePosixPath(member.name)
        if (path.is_absolute() or ".." in path.parts or "\\" in member.name
                or str(path) != member.name or member.name not in EXPECTED_MEMBERS):
            raise PackageValidationError(f"unsafe or unexpected archive member: {member.name!r}")
        if member.type != tarfile.REGTYPE or member.linkname or member.pax_headers or member.issparse():
            raise PackageValidationError(f"archive member must be a plain regular file: {member.name}")
        names.append(member.name)
    if len(names) != len(set(names)):
        raise PackageValidationError("duplicate archive member")
    if set(names) != set(EXPECTED_MEMBERS) or len(names) != 7:
        raise PackageValidationError("archive must contain exactly the seven allowlisted members")
    for member in members:
        expected_size, _, expected_mode = EXPECTED_MEMBERS[member.name]
        if member.size != expected_size:
            raise PackageValidationError(f"member size mismatch: {member.name}")
        if member.mode != expected_mode:
            raise PackageValidationError(f"member mode mismatch: {member.name}")


def _validate_checksums(payloads: dict[str, bytes]) -> dict:
    checksums = _load_json(payloads["package/checksums.json"], "checksums.json")
    if set(checksums) != {"schemaVersion", "target", "files"}:
        raise PackageValidationError("unexpected checksums.json fields")
    if type(checksums["schemaVersion"]) is not int or checksums["schemaVersion"] != 1:
        raise PackageValidationError("unexpected checksums.json schema version")
    if checksums["target"] != "linux-x64-gnu":
        raise PackageValidationError("unexpected checksums.json target")
    files = checksums["files"]
    if not isinstance(files, dict) or {"package/" + path for path in files} != RUNTIME_PATHS:
        raise PackageValidationError("checksums.json must cover exactly the four runtime payloads")
    for relative, entry in files.items():
        if not isinstance(entry, dict) or set(entry) != {"bytes", "sha256"}:
            raise PackageValidationError(f"invalid checksum entry: {relative}")
        if type(entry["bytes"]) is not int or entry["bytes"] < 0:
            raise PackageValidationError(f"invalid checksum length: {relative}")
        if not isinstance(entry["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None:
            raise PackageValidationError(f"invalid checksum hash: {relative}")
        payload = payloads["package/" + relative]
        if len(payload) != entry["bytes"] or _sha256(payload) != entry["sha256"]:
            raise PackageValidationError(f"runtime checksum mismatch: {relative}")
    return checksums


def _read_validated_members(source: Path) -> dict[str, bytes]:
    try:
        with tarfile.open(source, mode="r:gz") as archive:
            members = archive.getmembers()
            if archive.pax_headers:
                raise PackageValidationError("global PAX headers are not allowed")
            _validate_headers(members)
            payloads = {}
            for member in members:
                handle = archive.extractfile(member)
                if handle is None:
                    raise PackageValidationError(f"unreadable archive member: {member.name}")
                with handle:
                    data = handle.read()
                expected_size, expected_sha256, _ = EXPECTED_MEMBERS[member.name]
                if len(data) != expected_size or _sha256(data) != expected_sha256:
                    raise PackageValidationError(f"pinned member identity mismatch: {member.name}")
                payloads[member.name] = data
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise PackageValidationError(f"malformed platform archive: {exc}") from exc
    _validate_checksums(payloads)
    return payloads


def _derive_package_json(original: bytes) -> tuple[bytes, dict, dict]:
    before = _load_json(original, "package.json")
    if before.get("name") != "@zvec/zvec-grep-linux-x64-gnu" or before.get("version") != "0.0.1":
        raise PackageValidationError("unexpected package identity")
    if before.get("os") != ["linux"] or before.get("cpu") != ["x64"] or before.get("libc") != ["glibc"]:
        raise PackageValidationError("unexpected package platform")
    if "bin" in before or "scripts" in before or "directories" in before:
        raise PackageValidationError("source package has an unexpected installation declaration")
    after = {**before, "bin": dict(BIN_DECLARATION)}
    # Keep upstream key order and indentation. This is the only changed file.
    encoded = (json.dumps(after, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return encoded, before, after


class _StoredGzipWriter:
    """Canonical RFC 1952 member containing only RFC 1951 STORED blocks.

    Block boundaries depend solely on the input length, not write chunking or a
    compression-library version. Keep one block pending so the final block is
    full when the stream length is an exact positive multiple of 65535.
    """

    def __init__(self, raw):
        self.raw = raw
        self.pending = bytearray()
        self.crc32 = 0
        self.length = 0
        self.finished = False
        self.raw.write(GZIP_HEADER)

    def tell(self) -> int:
        return self.length

    def write(self, data: bytes) -> int:
        if self.finished:
            raise ValueError("cannot write a finished gzip member")
        self.crc32 = binascii.crc32(data, self.crc32)
        self.length += len(data)
        self.pending.extend(data)
        while len(self.pending) > STORED_BLOCK_BYTES:
            self._block(bytes(self.pending[:STORED_BLOCK_BYTES]), final=False)
            del self.pending[:STORED_BLOCK_BYTES]
        return len(data)

    def _block(self, data: bytes, *, final: bool) -> None:
        # BTYPE=00 and all padding bits zero; every stored block starts aligned.
        self.raw.write(bytes([int(final)]))
        self.raw.write(struct.pack("<HH", len(data), len(data) ^ 0xffff))
        self.raw.write(data)

    def finish(self) -> None:
        if self.finished:
            raise ValueError("gzip member was already finished")
        self._block(bytes(self.pending), final=True)
        self.raw.write(struct.pack("<II", self.crc32 & 0xffffffff, self.length & 0xffffffff))
        self.pending.clear()
        self.finished = True


def _write_deterministic_archive(path: Path, payloads: dict[str, bytes]) -> None:
    with path.open("wb") as raw:
        compressed = _StoredGzipWriter(raw)
        with tarfile.open(mode="w", fileobj=compressed, format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted(payloads):
                member = tarfile.TarInfo(name)
                member.type = tarfile.REGTYPE
                member.size = len(payloads[name])
                member.mode = EXPECTED_MEMBERS[name][2]
                member.uid = member.gid = member.mtime = 0
                member.uname = member.gname = ""
                archive.addfile(member, io.BytesIO(payloads[name]))
        compressed.finish()


def derive_package(source: Path, output: Path, manifest: Path) -> dict:
    """Write a new tgz and derivation manifest; reject overwrite or non-pinned input.

    Both destinations must be absent. Their parent directories are created only
    after complete input validation. Archive and manifest publication are atomic
    per file, and an ordinary publication error removes this call's new archive.
    """
    source, output, manifest = Path(source), Path(output), Path(manifest)
    if len({source.resolve(), output.resolve(), manifest.resolve()}) != 3:
        raise ValueError("source, output, and manifest must be different paths")
    for destination in (output, manifest):
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"refusing to overwrite {destination}")
    if source.stat().st_size != UPSTREAM_ARCHIVE_BYTES or _file_sha256(source) != UPSTREAM_ARCHIVE_SHA256:
        raise PackageValidationError("source archive does not match the pinned upstream size and SHA-256")
    payloads = _read_validated_members(source)
    original_json = payloads[PACKAGE_JSON]
    changed_json, before_json, after_json = _derive_package_json(original_json)
    derived_payloads = {**payloads, PACKAGE_JSON: changed_json}
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    # Temp files live beside their destinations so publication by hard link is
    # atomic and refuses an existing destination, including a concurrent symlink.
    output_fd, output_tmp_name = tempfile.mkstemp(prefix=".zg-package-", suffix=".tgz", dir=output.parent)
    os.close(output_fd)
    output_tmp = Path(output_tmp_name)
    manifest_tmp = None
    output_published = False
    try:
        _write_deterministic_archive(output_tmp, derived_payloads)
        members = [
            {
                "path": name,
                "source_bytes": len(payloads[name]),
                "source_sha256": _sha256(payloads[name]),
                "derived_bytes": len(derived_payloads[name]),
                "derived_sha256": _sha256(derived_payloads[name]),
                "mode": f"{EXPECTED_MEMBERS[name][2]:04o}",
                "unchanged": payloads[name] == derived_payloads[name],
            }
            for name in sorted(payloads)
        ]
        result = {
            "schema_version": 1,
            "repair": "npm-bin-metadata-only-v2",
            "upstream_commit": UPSTREAM_COMMIT,
            "upstream_archive_sha256": UPSTREAM_ARCHIVE_SHA256,
            "upstream_archive_bytes": source.stat().st_size,
            "install_package_sha256": _file_sha256(output_tmp),
            "install_package_bytes": output_tmp.stat().st_size,
            "install_package_is_unmodified_upstream_archive": False,
            "builder": {"path": BUILDER_REPOSITORY_PATH, "sha256": _file_sha256(Path(__file__))},
            "binary_sha256": BINARY_SHA256,
            "runtime_payloads": [entry for entry in members if entry["path"] in RUNTIME_PATHS],
            "members": members,
            "package_json": {
                "source_sha256": _sha256(original_json),
                "derived_sha256": _sha256(changed_json),
                "before": before_json,
                "after": after_json,
                "semantic_diff": [{"op": "add", "path": "/bin", "value": dict(BIN_DECLARATION)}],
            },
            "normalization": {
                "tar_format": "USTAR", "member_order": "lexicographic",
                "uid": 0, "gid": 0, "uname": "", "gname": "", "mtime": 0,
                "gzip_filename": "", "gzip_mtime": 0,
                "gzip_header_hex": GZIP_HEADER.hex(),
                "gzip_deflate_block_type": "RFC1951 STORED (BTYPE=00)",
                "gzip_stored_block_bytes": STORED_BLOCK_BYTES,
                "gzip_final_block": "last nonempty block; one empty block only for empty input",
                "gzip_trailer": "RFC1952 CRC32 and ISIZE, little-endian unsigned 32-bit",
                "compression_library": None,
                "determinism_scope": "identical input and builder; independent of compression-library version and write chunking",
            },
            "checksums_json": {
                "unchanged": True,
                "coverage": "exactly four runtime payloads; package.json is separately recorded in this manifest",
                "validated": True,
            },
        }
        manifest_fd, manifest_tmp_name = tempfile.mkstemp(prefix=".zg-package-manifest-", suffix=".json", dir=manifest.parent)
        manifest_tmp = Path(manifest_tmp_name)
        with os.fdopen(manifest_fd, "wb") as handle:
            handle.write((json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        output_tmp.chmod(0o644)
        manifest_tmp.chmod(0o644)
        os.link(output_tmp, output)
        output_published = True
        os.link(manifest_tmp, manifest)
        return result
    except BaseException:
        if output_published:
            output.unlink(missing_ok=True)
        raise
    finally:
        output_tmp.unlink(missing_ok=True)
        if manifest_tmp is not None:
            manifest_tmp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="exact original platform tgz")
    parser.add_argument("--output", type=Path, required=True, help="new derived install tgz; must not exist")
    parser.add_argument("--manifest", type=Path, required=True, help="new derivation JSON; must not exist")
    args = parser.parse_args()
    result = derive_package(args.source, args.output, args.manifest)
    print(json.dumps({key: result[key] for key in (
        "upstream_archive_sha256", "install_package_sha256", "binary_sha256", "install_package_bytes",
    )}, sort_keys=True))


if __name__ == "__main__":
    main()
