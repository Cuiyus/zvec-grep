"""Offline contract tests; QA_ORIGINAL_TGZ selects the original tgz.

No provider calls, npm installation, or learned QA scoring occurs here. The
separate Linux Harbor gate must still prove the real installation boundary.
"""

from __future__ import annotations

import copy
import binascii
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("repair_v2_package_under_test", Path(__file__).with_name("package.py"))
package = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(package)

DEFAULT_SOURCE = Path(
    "/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/"
    "main-agent-pilot/runtime/zg-native.tgz"
)


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ.get("QA_ORIGINAL_TGZ", os.environ.get("ZG_PACKAGE_TEST_SOURCE", str(DEFAULT_SOURCE))))
        if not cls.source.is_file():
            raise RuntimeError("Set QA_ORIGINAL_TGZ to the original pinned tgz; these tests never skip the payload contract")
        cls.temporary = tempfile.TemporaryDirectory(prefix="zg-package-tests-")
        cls.directory = Path(cls.temporary.name)
        cls.first = cls.directory / "first.tgz"
        cls.first_manifest = cls.directory / "first.json"
        cls.second = cls.directory / "second.tgz"
        cls.second_manifest = cls.directory / "second.json"
        cls.result = package.derive_package(cls.source, cls.first, cls.first_manifest)
        cls.second_result = package.derive_package(cls.source, cls.second, cls.second_manifest)
        with tarfile.open(cls.source) as archive:
            cls.source_headers = archive.getmembers()
            cls.source_payloads = {m.name: archive.extractfile(m).read() for m in cls.source_headers}
        with tarfile.open(cls.first) as archive:
            cls.derived_headers = archive.getmembers()
            cls.derived_payloads = {m.name: archive.extractfile(m).read() for m in cls.derived_headers}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def make_archive(self, names_and_types):
        """Small malformed archives test structure before any payload is read."""
        destination = self.directory / f"unsafe-{len(list(self.directory.glob('unsafe-*')))}.tgz"
        with tarfile.open(destination, mode="w:gz") as archive:
            for name, kind in names_and_types:
                member = tarfile.TarInfo(name)
                member.type = kind
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    member.linkname = "/tmp/forbidden-target"
                member.size = 1 if kind == tarfile.REGTYPE else 0
                archive.addfile(member, io.BytesIO(b"x") if member.size else None)
        return destination

    def checksum_payloads(self, mutate):
        changed = dict(self.source_payloads)
        manifest = json.loads(changed["package/checksums.json"])
        mutate(manifest)
        changed["package/checksums.json"] = json.dumps(manifest).encode()
        return changed

    def test_pinned_original_identity_and_binary(self):
        self.assertEqual(package._file_sha256(self.source), package.UPSTREAM_ARCHIVE_SHA256)
        self.assertEqual(hashlib.sha256(self.source_payloads["package/bin/zg"]).hexdigest(), package.BINARY_SHA256)
        self.assertEqual(package._read_validated_members(self.source), self.source_payloads)

    def test_archive_and_manifest_are_deterministic(self):
        self.assertEqual(package._file_sha256(self.first), package._file_sha256(self.second))
        with self.first.open("rb") as first, self.second.open("rb") as second:
            while chunk := first.read(1024 * 1024):
                self.assertEqual(chunk, second.read(len(chunk)))
            self.assertEqual(second.read(), b"")
        self.assertEqual(self.first_manifest.read_bytes(), self.second_manifest.read_bytes())
        self.assertEqual(self.result, self.second_result)

    def test_canonical_gzip_blocks_crc_and_length(self):
        crc, size, blocks = 0, 0, 0
        with self.first.open("rb") as handle:
            self.assertEqual(handle.read(10), bytes.fromhex("1f8b08000000000000ff"))
            while True:
                control = handle.read(1)
                self.assertIn(control, (b"\x00", b"\x01"))
                length, complement = struct.unpack("<HH", handle.read(4))
                self.assertEqual(length ^ complement, 0xffff)
                if control == b"\x00":
                    self.assertEqual(length, 65535)
                else:
                    self.assertGreater(length, 0)
                data = handle.read(length)
                self.assertEqual(len(data), length)
                crc = binascii.crc32(data, crc)
                size += length
                blocks += 1
                if control == b"\x01":
                    break
            self.assertEqual(struct.unpack("<II", handle.read(8)), (crc & 0xffffffff, size & 0xffffffff))
            self.assertEqual(handle.read(), b"")
        self.assertEqual(blocks, (size + 65534) // 65535)
        self.assertEqual(size % tarfile.RECORDSIZE, 0)
        self.assertEqual(self.first.stat().st_size, 10 + size + 5 * blocks + 8)
        self.assertEqual(self.result["normalization"]["compression_library"], None)
        self.assertFalse(any("zlib" in key for key in self.result["normalization"]))

    def test_gzip_decompress_roundtrip_and_write_chunk_independence(self):
        for size in (0, 1, 65535, 65536, 131070, 131071):
            payload = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
            outputs = []
            for chunk_size in (17, 32768, max(1, size)):
                raw = io.BytesIO()
                writer = package._StoredGzipWriter(raw)
                for start in range(0, size, chunk_size):
                    writer.write(payload[start:start + chunk_size])
                writer.finish()
                self.assertEqual(gzip.decompress(raw.getvalue()), payload)
                outputs.append(raw.getvalue())
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[1], outputs[2])

    def test_only_package_json_changes_and_semantic_diff_is_only_bin(self):
        self.assertEqual(set(self.derived_payloads), set(self.source_payloads))
        changed = [name for name in self.source_payloads if self.source_payloads[name] != self.derived_payloads[name]]
        self.assertEqual(changed, [package.PACKAGE_JSON])
        before = json.loads(self.source_payloads[package.PACKAGE_JSON])
        after = json.loads(self.derived_payloads[package.PACKAGE_JSON])
        self.assertEqual(after.pop("bin"), {"zg": "bin/zg"})
        self.assertEqual(before, after)
        self.assertEqual(self.result["package_json"]["semantic_diff"], [{"op": "add", "path": "/bin", "value": {"zg": "bin/zg"}}])
        self.assertNotIn("scripts", after)
        self.assertEqual(package._validate_checksums(self.derived_payloads), package._validate_checksums(self.source_payloads))

    def test_normalized_metadata_and_preserved_executable_modes(self):
        self.assertEqual([m.name for m in self.derived_headers], sorted(package.EXPECTED_MEMBERS))
        for member in self.derived_headers:
            self.assertEqual(member.type, tarfile.REGTYPE)
            self.assertEqual(member.mode, package.EXPECTED_MEMBERS[member.name][2])
            self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))
            self.assertEqual((member.uname, member.gname, member.linkname), ("", "", ""))
            self.assertEqual(member.pax_headers, {})
        with self.first.open("rb") as handle:
            header = handle.read(10)
        self.assertEqual(header[:4], b"\x1f\x8b\x08\x00")  # No filename or optional fields.
        self.assertEqual(header[4:8], b"\x00" * 4)
        self.assertEqual(header[9], 255)  # Portable gzip OS marker.

    def test_manifest_covers_every_file_and_builder(self):
        self.assertEqual(json.loads(self.first_manifest.read_text()), self.result)
        self.assertEqual(self.result["builder"]["sha256"], package._file_sha256(Path(package.__file__)))
        self.assertEqual(self.result["install_package_sha256"], package._file_sha256(self.first))
        self.assertEqual(self.result["install_package_bytes"], self.first.stat().st_size)
        self.assertFalse(self.result["install_package_is_unmodified_upstream_archive"])
        self.assertNotEqual(self.result["install_package_sha256"], self.result["upstream_archive_sha256"])
        self.assertEqual(len(self.result["members"]), 7)
        self.assertEqual(len(self.result["runtime_payloads"]), 4)
        for entry in self.result["members"]:
            name = entry["path"]
            self.assertEqual(entry["source_sha256"], package._sha256(self.source_payloads[name]))
            self.assertEqual(entry["derived_sha256"], package._sha256(self.derived_payloads[name]))
            self.assertEqual(entry["unchanged"], name != package.PACKAGE_JSON)

    def test_tampered_source_rejected_before_output_creation(self):
        tampered = self.directory / "tampered.tgz"
        data = bytearray(self.source.read_bytes())
        data[-1] ^= 1
        tampered.write_bytes(data)
        output = self.directory / "never-created" / "output.tgz"
        with self.assertRaisesRegex(package.PackageValidationError, "pinned upstream"):
            package.derive_package(tampered, output, output.with_suffix(".json"))
        self.assertFalse(output.parent.exists())

    def test_malformed_archive_rejected(self):
        malformed = self.directory / "malformed.tgz"
        malformed.write_bytes(b"not gzip or tar")
        with self.assertRaisesRegex(package.PackageValidationError, "malformed platform archive"):
            package._read_validated_members(malformed)

    def test_unsafe_and_unexpected_archive_paths_rejected(self):
        for path in ("/package/bin/zg", "package/../bin/zg", "package//bin/zg", "package\\bin\\zg", "package/unexpected"):
            with self.subTest(path=path):
                unsafe = self.make_archive([(path, tarfile.REGTYPE)])
                with self.assertRaisesRegex(package.PackageValidationError, "unsafe or unexpected"):
                    package._read_validated_members(unsafe)

    def test_nonregular_archive_members_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind):
                unsafe = self.make_archive([("package/bin/zg", kind)])
                with self.assertRaisesRegex(package.PackageValidationError, "plain regular file"):
                    package._read_validated_members(unsafe)

    def test_duplicate_and_missing_archive_members_rejected(self):
        duplicate = self.make_archive([("package/bin/zg", tarfile.REGTYPE)] * 2)
        with self.assertRaisesRegex(package.PackageValidationError, "duplicate archive member"):
            package._read_validated_members(duplicate)
        missing = self.make_archive([("package/bin/zg", tarfile.REGTYPE)])
        with self.assertRaisesRegex(package.PackageValidationError, "exactly the seven"):
            package._read_validated_members(missing)

    def test_member_size_mode_and_pax_headers_rejected(self):
        for field, value, expected in (("size", 1, "size mismatch"), ("mode", 0o777, "mode mismatch"), ("pax_headers", {"comment": "unexpected"}, "plain regular file")):
            with self.subTest(field=field):
                headers = copy.deepcopy(self.source_headers)
                setattr(headers[0], field, value)
                with self.assertRaisesRegex(package.PackageValidationError, expected):
                    package._validate_headers(headers)

    def test_runtime_checksum_hash_and_length_rejected(self):
        for field, value in (("bytes", 1), ("sha256", "0" * 64)):
            with self.subTest(field=field):
                payloads = self.checksum_payloads(lambda m: m["files"]["bin/zg"].__setitem__(field, value))
                with self.assertRaisesRegex(package.PackageValidationError, "runtime checksum mismatch"):
                    package._validate_checksums(payloads)

    def test_checksum_coverage_and_schema_rejected(self):
        mutations = [
            (lambda m: m["files"].pop("bin/zg"), "exactly the four"),
            (lambda m: m["files"].__setitem__("../other", {"bytes": 0, "sha256": "0" * 64}), "exactly the four"),
            (lambda m: m.__setitem__("target", "wrong"), "target"),
            (lambda m: m.__setitem__("schemaVersion", True), "schema version"),
            (lambda m: m["files"]["bin/zg"].__setitem__("bytes", True), "checksum length"),
            (lambda m: m["files"]["bin/zg"].__setitem__("sha256", "invalid"), "checksum hash"),
        ]
        for mutation, expected in mutations:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(package.PackageValidationError, expected):
                    package._validate_checksums(self.checksum_payloads(mutation))

    def test_duplicate_or_malformed_json_rejected(self):
        for data, expected in ((b'{"name":"a","name":"b"}', "duplicate JSON key"), (b'{', "invalid package.json"), (b'[]', "JSON object")):
            with self.subTest(data=data):
                with self.assertRaisesRegex(package.PackageValidationError, expected):
                    package._derive_package_json(data)

    def test_existing_bin_or_lifecycle_declaration_rejected(self):
        for key, value in (("bin", {"zg": "elsewhere"}), ("scripts", {"postinstall": "false"}), ("directories", {"bin": "bin"})):
            before = json.loads(self.source_payloads[package.PACKAGE_JSON])
            before[key] = value
            with self.subTest(key=key):
                with self.assertRaisesRegex(package.PackageValidationError, "installation declaration"):
                    package._derive_package_json(json.dumps(before).encode())

    def test_refuses_output_or_manifest_overwrite_and_path_alias(self):
        for output, manifest in ((self.first, self.directory / "absent.json"), (self.directory / "absent.tgz", self.first_manifest)):
            with self.subTest(output=output):
                with self.assertRaises(FileExistsError):
                    package.derive_package(self.source, output, manifest)
        with self.assertRaisesRegex(ValueError, "different paths"):
            package.derive_package(self.source, self.source, self.directory / "absent.json")
        dangling = self.directory / "dangling.tgz"
        dangling.symlink_to(self.directory / "missing-target")
        with self.assertRaises(FileExistsError):
            package.derive_package(self.source, dangling, self.directory / "absent.json")

    def test_publication_failure_preserves_existing_file_and_cleans_new_archive(self):
        output = self.directory / "publication-failure.tgz"
        manifest = self.directory / "publication-failure.json"
        real_link = package.os.link

        def race_manifest(source, destination):
            if destination == manifest:
                manifest.write_bytes(b"existing manifest must survive")
            real_link(source, destination)

        # Copy an already produced deterministic tgz to avoid spending another
        # compression pass on this filesystem-publication failure test.
        def copy_archive(destination, payloads):
            destination.write_bytes(self.first.read_bytes())

        with mock.patch.object(package.os, "link", side_effect=race_manifest), mock.patch.object(package, "_write_deterministic_archive", side_effect=copy_archive):
            with self.assertRaises(FileExistsError):
                package.derive_package(self.source, output, manifest)
        self.assertFalse(output.exists())
        self.assertEqual(manifest.read_bytes(), b"existing manifest must survive")
        self.assertFalse(list(self.directory.glob(".zg-package-*")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
