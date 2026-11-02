from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from zg_bench.case_query_report import export_navigation


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


class QueryNavigationTest(unittest.TestCase):
    def fixture(self, root):
        source, evidence = root / "source", root / "raw"
        source.mkdir()
        (source / "test.py").write_text("line one\nline two\nline three\n")
        for command in (["git", "init", "-q"], ["git", "add", "test.py"],
                        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                         "commit", "-qm", "fixture"]):
            subprocess.run(command, cwd=source, check=True, capture_output=True)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        prior = root / "prior.json"
        write(prior, {"entities": {"test.py:1-2": {"path": "test.py", "start": 1, "end": 2,
              "source_sha256": hashlib.sha256(b"line one\nline two").hexdigest(),
              "source_url": f"https://github.com/example/repo/blob/{commit}/test.py#L1",
              "relevance": "relevant", "reason": "static note"}}})
        prefix = "jobs/formal/trial/agent/wire/mcp/wire-id"
        links = {"pre_query_model_request": "jobs/formal/trial/agent/wire/model/pre.request.bin",
                 "first_visible_model_request": "jobs/formal/trial/agent/wire/model/post.request.bin",
                 "call_id": "call-1", "tool_body_equal": True, "model_body_equal": True}
        query = {"trial": "trial", "wire_id": "wire-id", "links": links,
                 "request": {"id": 1, "method": "tools/call", "params": {
                     "name": "zvec_grep_search", "arguments": {"query": "full\nquery", "limit": 5}}},
                 "hits": [{"rank": 1, "path": "test.py", "start": 1, "end": 2,
                           "text": "original preview\n", "source_mismatches": []}]}
        for path in (prefix + ".request.bin", prefix + ".response.bin", *list(links.values())[:2]):
            write(evidence / path, {})
        write(evidence / "jobs/formal/trial/agent/full-sessions.json", [])
        (evidence / "requests.jsonl").write_text(json.dumps({"trial": "trial", "wire_id": "wire-id",
                                                            "raw_prefix": prefix}) + "\n")
        audit = root / "audit.json"
        write(audit, {"queries": [query]})
        return source, evidence, prior, audit, query

    def test_reuses_only_hash_verified_static_annotation_without_completing_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, evidence, prior, audit, _ = self.fixture(root)
            before = hashes(evidence)
            result = export_navigation(audit, evidence, root / "out", prior, source)
            self.assertEqual(result["source_annotation_reused_occurrences"], 1)
            self.assertFalse(result["current_request_review_complete"])
            self.assertEqual(result["current_request_reviews_completed"], 0)
            index = json.loads((root / "out/index.json").read_text())
            request = index["requests"][0]
            self.assertEqual(request["query"], "full\nquery")
            self.assertTrue(all(v["exists"] for v in request["raw_links"].values()))
            self.assertEqual((root / "out" / request["hits"][0]["preview"]).read_text(), "original preview\n")
            self.assertEqual(request["hits"][0]["source_annotation"]["reason"], "static note")
            self.assertEqual(hashes(evidence), before)

    def test_whole_file_change_blocks_reuse_even_when_fragment_hash_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, evidence, prior, audit, _ = self.fixture(root)
            (source / "test.py").write_text("line one\nline two\nCHANGED OUTSIDE FRAGMENT\n")
            result = export_navigation(audit, evidence, root / "out", prior, source)
            self.assertEqual(result["source_annotation_reused_occurrences"], 0)
            hit = json.loads((root / "out/index.json").read_text())["requests"][0]["hits"][0]
            self.assertEqual(hit["source_annotation"]["reason"], "prior_source_file_hash_mismatch")

    def test_new_fragment_stays_pending_and_full_parameters_define_unique_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, evidence, prior, audit, query = self.fixture(root)
            duplicate = copy.deepcopy(query)
            duplicate["request"]["id"] = 2
            different = copy.deepcopy(query)
            different["request"]["params"]["arguments"]["limit"] = 10
            different["hits"][0].update(start=2, end=3)
            write(audit, {"queries": [query, duplicate, different]})
            result = export_navigation(audit, evidence, root / "out", prior, source)
            self.assertEqual((result["actual_calls"], result["unique_params"], result["hit_occurrences"],
                              result["unique_path_ranges"]), (3, 2, 3, 2))
            self.assertEqual(result["source_annotation_pending_occurrences"], 1)

    def test_empty_input_is_not_complete_and_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "raw"
            evidence.mkdir()
            write(root / "audit.json", {"queries": []})
            result = export_navigation(root / "audit.json", evidence, root / "out")
            self.assertFalse(result["current_request_review_complete"])
            self.assertEqual(result["actual_calls"], 0)
            with self.assertRaises(FileExistsError):
                export_navigation(root / "audit.json", evidence, root / "out")
            with self.assertRaises(ValueError):
                export_navigation(root / "audit.json", evidence, evidence / "out")


if __name__ == "__main__":
    unittest.main()
