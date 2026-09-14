from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

MODULE = Path(__file__).resolve().parents[1] / "dataset.py"
spec = importlib.util.spec_from_file_location("workspace_qa_dataset", MODULE)
dataset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dataset)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_bytes(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


class MemoryArchive(io.BytesIO):
    fetched = 0

    def metrics(self):
        return {"enabled": False, "downloaded_bytes": 0, "cache_hit_bytes": 0,
                "persisted_bytes": 0, "cache_stored_bytes": 0}


class DatasetTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"WORKSPACE_QA_RANGE_CACHE": "",
                                               "WORKSPACE_QA_RANGE_CACHE_BYTES": "4294967296"})
        environment.start()
        self.addCleanup(environment.stop)

    @staticmethod
    def mock_http(payload, requests):
        def fetch(argv, **kwargs):
            low, high = map(int, argv[argv.index("--range") + 1].split("-"))
            requests.append((low, high))
            Path(argv[argv.index("--dump-header") + 1]).write_text(
                f"HTTP/2 206\nContent-Range: bytes {low}-{high}/{len(payload)}\n")
            Path(argv[argv.index("--output") + 1]).write_bytes(payload[low:high + 1])
        return fetch

    def test_dataset_paths_cannot_escape_destination(self):
        for value in ("", "/tmp/out", "../out", "data/../../out", "data\\out", "a\x00b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dataset.safe_relative(value)
        self.assertEqual(dataset.safe_relative("资料/脚本.py").as_posix(), "资料/脚本.py")

    def test_full_persona_preserves_distractors_and_chinese_names(self):
        data = archive_bytes({
            "filesys_cn/Research_Workdir/项目/脚本.py": b"import numpy\n",
            "filesys_cn/Research_Workdir/无关/空文件.md": b"",
            "filesys_cn/Research_Workdir/无关/其他资料.txt": "干扰资料".encode(),
            "filesys_cn/BackendDeveloper_Workdir/other.py": b"other persona",
            "task_lite_clean_cn/128/metadata.json": b"PRIVATE_RUBRIC",
        })
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(data)) as archive:
            dest = Path(tmp) / "source"
            with patch.object(dataset.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
                result = dataset.extract_persona(archive, "Researcher", dest)
            self.assertEqual(result["file_count"], 3)
            self.assertEqual(result["archive_prefix"], "filesys_cn/Research_Workdir/")
            self.assertEqual((dest / "项目/脚本.py").read_bytes(), b"import numpy\n")
            self.assertEqual((dest / "无关/空文件.md").read_bytes(), b"")
            self.assertEqual((dest / "无关/其他资料.txt").read_text(), "干扰资料")
            self.assertFalse((dest / "other.py").exists())
            self.assertFalse(any(p.name == "metadata.json" for p in dest.rglob("*")))
            for item in result["files"]:
                self.assertEqual(item["sha256"], sha((dest / item["path"]).read_bytes()))

    def test_ambiguous_persona_root_is_not_silently_selected(self):
        data = archive_bytes({"a/Research_Workdir/a.txt": b"a", "b/Research_Workdir/b.txt": b"b"})
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(data)) as archive:
            with self.assertRaisesRegex(ValueError, "one original persona root"):
                dataset.extract_persona(archive, "Researcher", Path(tmp) / "source")

    def test_nested_git_metadata_is_omitted_but_all_working_files_remain(self):
        data = archive_bytes({
            "Research_Workdir/project/.git/HEAD": b"ref: refs/heads/main\n",
            "Research_Workdir/project/.git/objects/pack/data.pack": b"git database",
            "Research_Workdir/project/.github/workflows/build.yml": b"name: build\n",
            "Research_Workdir/project/.gitignore": b"build/\n",
            "Research_Workdir/project/src/main.py": b"print('keep')\n",
            "Research_Workdir/unrelated.txt": b"not a gold dependency",
        })
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(data)) as archive:
            dest = Path(tmp) / "source"
            with patch.object(dataset.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
                result = dataset.extract_persona(archive, "Researcher", dest)
            self.assertEqual(result["file_count"], 4)
            self.assertEqual(result["excluded_vcs_metadata_files"], 2)
            self.assertEqual(result["excluded_vcs_metadata_bytes"],
                             len(b"ref: refs/heads/main\n") + len(b"git database"))
            self.assertFalse((dest / "project/.git").exists())
            self.assertEqual((dest / "project/.gitignore").read_bytes(), b"build/\n")
            self.assertEqual((dest / "project/.github/workflows/build.yml").read_bytes(), b"name: build\n")
            self.assertEqual((dest / "project/src/main.py").read_bytes(), b"print('keep')\n")
            self.assertTrue((dest / "unrelated.txt").is_file())

    def test_zip_traversal_and_symlinks_cannot_write_outside_corpus(self):
        for malicious in ("Research_Workdir/../../escape.txt", "Research_Workdir/a\\escape.txt"):
            data = archive_bytes({malicious: b"not allowed"})
            with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(data)) as archive:
                dest = Path(tmp) / "source"
                with patch.object(dataset.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
                    with self.assertRaises(ValueError):
                        dataset.extract_persona(archive, "Researcher", dest)
                self.assertFalse((Path(tmp) / "escape.txt").exists())
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            entry = zipfile.ZipInfo("Research_Workdir/link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "../../outside")
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(stream.getvalue())) as archive:
            with patch.object(dataset.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    dataset.extract_persona(archive, "Researcher", Path(tmp) / "source")

    def test_member_crc_mismatch_fails_extraction(self):
        raw = bytearray(archive_bytes({"Research_Workdir/script.py": b"original contents"}))
        # Change one stored member byte, retaining the central-directory CRC.
        filename_length, extra_length = struct.unpack_from("<HH", raw, 26)
        raw[30 + filename_length + extra_length] ^= 1
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(raw)) as archive:
            with patch.object(dataset.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
                with self.assertRaises(zipfile.BadZipFile):
                    dataset.extract_persona(archive, "Researcher", Path(tmp) / "source")

    def fixture(self, root: Path, *, corpus_input=b"import numpy\n"):
        source_input = b"import numpy\n"
        metadata = {"task": "分析代码并生成 report.md", "output_files": ["report.md"],
                    "rubrics": ["PRIVATE_RUBRIC_MARKER"], "data_manifest": [{
                        "filename": "script.py", "stored_relpath": "data/hash_script.py"}]}
        raw_metadata = json.dumps(metadata, ensure_ascii=False).encode()
        task = {"task_id": "128", "slice": "code_qa", "persona": "Researcher",
                "metadata_sha256": sha(raw_metadata), "answer_filename": "report.md",
                "inputs": [{"filename": "script.py", "stored_relpath": "data/hash_script.py",
                            "sha256": sha(source_input), "size_bytes": len(source_input)}]}
        lock = {"tasks": [task], "upstream": {"repo": "upstream/repo", "commit": "fixed-commit"},
                "dataset": {"repo": "dataset/repo", "revision": "fixed-dataset"},
                "workspace": {"repo": "workspace/repo", "revision": "fixed-workspace",
                              "archive": "filesys_cn.zip", "size_bytes": 100}}
        upstream = root / "upstream"
        module = upstream / "evaluation/src/task_patches.py"
        module.parent.mkdir(parents=True)
        module.write_text("def apply_task_patches(*args, **kwargs):\n    return []\n")
        source_entries = {"Research_Workdir/project/script.py": corpus_input,
                          "Research_Workdir/unrelated/notes.txt": b"retained distractor"}
        raw_zip = archive_bytes(source_entries)

        def fetch(url, dest, expected=None):
            data = raw_metadata if url.endswith("metadata.json") else source_input
            self.assertEqual(sha(data), expected)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        return lock, upstream, raw_zip, fetch, raw_metadata

    def test_prepare_keeps_gold_and_question_outside_complete_source_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, upstream, raw_zip, fetch, raw_metadata = self.fixture(root)
            output = root / "prepared"
            with (patch.object(dataset, "download", side_effect=fetch),
                  patch.object(dataset, "RangeArchive", side_effect=lambda *a, **kw: MemoryArchive(raw_zip)),
                  patch.object(dataset.subprocess, "check_output", return_value="fixed-commit\n"),
                  patch.object(dataset.subprocess, "run"),
                  patch.object(dataset.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12))):
                result = dataset.prepare(lock, "128", output, upstream)
            self.assertEqual(result["file_count"], 2)
            self.assertTrue((output / "source/unrelated/notes.txt").is_file())
            self.assertEqual((output / "tasks/128/metadata.json").read_bytes(), raw_metadata)
            self.assertEqual((output / "question.txt").read_text(), "分析代码并生成 report.md")
            self.assertEqual(result["source_metadata_sha256"], sha(raw_metadata))
            self.assertEqual(result["effective_metadata_sha256"], sha(raw_metadata))
            self.assertEqual(result["matched_inputs"][0]["paths"], ["project/script.py"])
            for path in (output / "source").rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"PRIVATE_RUBRIC_MARKER", path.read_bytes())
                    self.assertNotIn(path.name, ("metadata.json", "question.txt", "report.md"))

    def test_mismatched_full_workspace_input_is_not_replaced_using_gold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, upstream, raw_zip, fetch, _ = self.fixture(root, corpus_input=b"different original contents")
            output = root / "prepared"
            with (patch.object(dataset, "download", side_effect=fetch),
                  patch.object(dataset, "RangeArchive", side_effect=lambda *a, **kw: MemoryArchive(raw_zip)),
                  patch.object(dataset.subprocess, "check_output", return_value="fixed-commit\n"),
                  patch.object(dataset.subprocess, "run") as execute,
                  patch.object(dataset.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12))):
                with self.assertRaisesRegex(RuntimeError, "lacks matching input"):
                    dataset.prepare(lock, "128", output, upstream)
            self.assertEqual((output / "source/project/script.py").read_bytes(), b"different original contents")
            execute.assert_not_called()
            self.assertFalse((output / "dataset-manifest.json").exists())

    def test_upstream_mismatch_stops_before_workspace_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, upstream, _, fetch, _ = self.fixture(root)
            with (patch.object(dataset, "download", side_effect=fetch),
                  patch.object(dataset, "RangeArchive") as archive,
                  patch.object(dataset.subprocess, "check_output", return_value="other-commit\n")):
                with self.assertRaisesRegex(ValueError, "revision mismatch"):
                    dataset.prepare(lock, "128", root / "prepared", upstream)
            archive.assert_not_called()

    def test_range_reader_validates_exact_response_and_crosses_block_boundaries(self):
        payload = b"abcdefghijk"
        requested = []

        def fetch(argv, **kwargs):
            low, high = map(int, argv[argv.index("--range") + 1].split("-"))
            requested.append((low, high))
            Path(argv[argv.index("--dump-header") + 1]).write_text(
                f"HTTP/2 206\nContent-Range: bytes {low}-{high}/{len(payload)}\n")
            Path(argv[argv.index("--output") + 1]).write_bytes(payload[low:high + 1])

        with patch.object(dataset.subprocess, "run", side_effect=fetch):
            reader = dataset.RangeArchive("https://example.test/archive.zip", len(payload), block_size=4)
            reader.seek(2)
            self.assertEqual(reader.read(5), b"cdefg")
            reader.seek(-2, 2)
            self.assertEqual(reader.read(), b"jk")
            self.assertEqual(reader.read(1), b"")
        self.assertEqual(requested, [(0, 3), (4, 7), (8, 10)])
        self.assertEqual(reader.fetched, len(payload))

    def test_ignored_or_truncated_http_range_is_not_treated_as_archive_bytes(self):
        for wrong_range, body in (("0-3/12", b"abcd"), ("0-3/11", b"ab")):
            def fetch(argv, **kwargs):
                Path(argv[argv.index("--dump-header") + 1]).write_text(
                    f"Content-Range: bytes {wrong_range}\n")
                Path(argv[argv.index("--output") + 1]).write_bytes(body)
            with self.subTest(response=wrong_range, body=body), patch.object(dataset.subprocess, "run", side_effect=fetch):
                reader = dataset.RangeArchive("https://example.test/archive.zip", 11, block_size=4)
                with self.assertRaises(RuntimeError):
                    reader.read(1)
                self.assertEqual(reader.fetched, 0)

    def test_persistent_cache_second_reader_needs_no_http_or_credentials_on_disk(self):
        payload, requests = b"abcdefghijk", []
        url = "https://example.test/archive-v1.zip?token=DO_NOT_CACHE_CREDENTIAL"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"WORKSPACE_QA_RANGE_CACHE": tmp}):
            with patch.object(dataset.subprocess, "run", side_effect=self.mock_http(payload, requests)):
                first = dataset.RangeArchive(url, len(payload), block_size=4)
                self.assertEqual(first.read(), payload)
            self.assertEqual(first.fetched, len(payload))
            self.assertEqual(first.persisted_bytes, len(payload))
            self.assertEqual(first.cache_limit, 4294967296)
            self.assertLessEqual(len(first.cache), 2)
            with patch.object(dataset.subprocess, "run") as fetch:
                second = dataset.RangeArchive(url, len(payload), block_size=4)
                self.assertEqual(second.read(), payload)
                fetch.assert_not_called()
            self.assertEqual(second.cache_hit_bytes, len(payload))
            self.assertEqual(second.fetched, 0)
            self.assertEqual(second.persisted_bytes, 0)
            self.assertLessEqual(len(second.cache), 2)
            for path in Path(tmp).iterdir():
                self.assertIn(path.suffix, (".block", ".json"))
                self.assertNotIn("DO_NOT_CACHE_CREDENTIAL", path.name)
                self.assertNotIn(b"DO_NOT_CACHE_CREDENTIAL", path.read_bytes())

    def test_corrupt_persistent_blocks_are_downloaded_again(self):
        payload = b"abcdefghijk"
        for corruption in ("checksum", "length", "identity", "missing_sidecar"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as tmp:
                with patch.dict(os.environ, {"WORKSPACE_QA_RANGE_CACHE": tmp}):
                    with patch.object(dataset.subprocess, "run", side_effect=self.mock_http(payload, [])):
                        first = dataset.RangeArchive("https://example.test/v1.zip", len(payload), block_size=4)
                        first.read()
                    key = first._cache_key(0)
                    body, sidecar = Path(tmp) / f"{key}.block", Path(tmp) / f"{key}.json"
                    if corruption == "checksum":
                        body.write_bytes(b"xxxx")
                    elif corruption == "length":
                        body.write_bytes(b"x")
                    elif corruption == "identity":
                        metadata = json.loads(sidecar.read_text())
                        metadata["key"] = "another-cache-entry"
                        sidecar.write_text(json.dumps(metadata))
                    else:
                        sidecar.unlink()
                    requested = []
                    with patch.object(dataset.subprocess, "run", side_effect=self.mock_http(payload, requested)):
                        second = dataset.RangeArchive("https://example.test/v1.zip", len(payload), block_size=4)
                        self.assertEqual(second.read(), payload)
                    self.assertEqual(requested, [(0, 3)])
                    self.assertEqual(second.fetched, 4)
                    self.assertEqual(second.cache_hit_bytes, 7)
                    self.assertEqual(second.persisted_bytes, 4)
                    self.assertEqual(body.read_bytes(), b"abcd")

    def test_cache_identity_separates_archive_url_size_block_size_and_offset(self):
        payload = b"abcdefghijk"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"WORKSPACE_QA_RANGE_CACHE": tmp}):
            identities = [("v1", 11, 4), ("v2", 11, 4), ("v1", 10, 4), ("v1", 11, 5)]
            keys = set()
            for version, size, block_size in identities:
                calls = []
                with patch.object(dataset.subprocess, "run", side_effect=self.mock_http(payload[:size], calls)):
                    reader = dataset.RangeArchive(f"https://example.test/{version}.zip", size, block_size=block_size)
                    self.assertEqual(reader.read(1), payload[:1])
                    keys.add(reader._cache_key(0))
                    keys.add(reader._cache_key(block_size))
                self.assertEqual(len(calls), 1)
                self.assertEqual(reader.cache_hit_bytes, 0)
            self.assertEqual(len(keys), 8)

    def test_cache_budget_exhaustion_does_not_truncate_remote_content(self):
        payload = b"abcdefghijk"
        for budget in (0, 200):
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as tmp:
                with (patch.dict(os.environ, {"WORKSPACE_QA_RANGE_CACHE": tmp,
                                               "WORKSPACE_QA_RANGE_CACHE_BYTES": str(budget)}),
                      patch.object(dataset.subprocess, "run", side_effect=self.mock_http(payload, []))):
                    reader = dataset.RangeArchive("https://example.test/archive.zip", len(payload), block_size=4)
                    self.assertEqual(reader.read(), payload)
                    self.assertEqual(reader.fetched, len(payload))
                    self.assertLessEqual(reader.metrics()["cache_stored_bytes"], budget)
                    self.assertLess(reader.persisted_bytes, len(payload))
                    if budget:
                        self.assertGreater(reader.persisted_bytes, 0)
                    self.assertLessEqual(len(reader.cache), 2)

    def test_chinese_zip_extraction_is_identical_when_all_ranges_are_cached(self):
        payload = archive_bytes({"Research_Workdir/项目/说明.md": "中文资料".encode(),
                                 "Research_Workdir/无关/空文件.txt": b"",
                                 "Research_Workdir/其他.csv": b"kind,count\nother,3\n"})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (patch.dict(os.environ, {"WORKSPACE_QA_RANGE_CACHE": str(root / "cache")}),
                  patch.object(dataset.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12))):
                manifests = []
                for repeat in range(2):
                    with patch.object(dataset.subprocess, "run", side_effect=self.mock_http(payload, [])) as fetch:
                        with dataset.RangeArchive("https://example.test/cn.zip", len(payload), block_size=64) as reader:
                            with zipfile.ZipFile(reader) as archive:
                                manifests.append(dataset.extract_persona(archive, "Researcher", root / str(repeat)))
                        if repeat:
                            fetch.assert_not_called()
                            self.assertEqual(reader.fetched, 0)
                            self.assertGreater(reader.cache_hit_bytes, 0)
                self.assertEqual(manifests[0], manifests[1])
                self.assertEqual((root / "1/项目/说明.md").read_text(), "中文资料")

    def test_failed_prepare_retains_downloaded_blocks_and_cache_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, upstream, _, fetch_metadata, _ = self.fixture(root)
            # A valid ZIP with the wrong persona fails after remote ZIP inspection.
            payload = archive_bytes({"BackendDeveloper_Workdir/file.txt": b"working file"})
            lock["workspace"]["size_bytes"] = len(payload)
            output = root / "prepared"
            with (patch.dict(os.environ, {"WORKSPACE_QA_RANGE_CACHE": str(root / "cache")}),
                  patch.object(dataset, "download", side_effect=fetch_metadata),
                  patch.object(dataset.subprocess, "check_output", return_value="fixed-commit\n"),
                  patch.object(dataset.subprocess, "run", side_effect=self.mock_http(payload, []))):
                with self.assertRaisesRegex(ValueError, "one original persona root"):
                    dataset.prepare(lock, "128", output, upstream)
            metrics = json.loads((output / "range-cache-metrics.json").read_text())
            self.assertEqual(metrics["downloaded_bytes"], len(payload))
            self.assertEqual(metrics["persisted_bytes"], len(payload))
            self.assertGreater(metrics["cache_stored_bytes"], len(payload))
            self.assertEqual(len(list((root / "cache").glob("*.block"))), 1)
            self.assertFalse((output / "dataset-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
