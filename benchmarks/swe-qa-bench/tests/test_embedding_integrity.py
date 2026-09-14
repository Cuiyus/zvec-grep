from __future__ import annotations

import copy
import hashlib
import unittest

from zg_bench.swe_qa.embedding_integrity import (
    MUTABLE_COMPLETION_MARKER, REQUIRED_MODEL_ARTIFACTS, compare_embedding_cache,
)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def inventory():
    return {**{path: sha(path) for path in REQUIRED_MODEL_ARTIFACTS}, MUTABLE_COMPLETION_MARKER: sha("old marker")}


class EmbeddingIntegrityTests(unittest.TestCase):
    def test_exact_unchanged_nonempty_inventory_is_accepted_without_marker_exception(self):
        data = {"weights.onnx": sha("fixture weights")}
        result = compare_embedding_cache(data, data.copy())
        self.assertTrue(result["valid"])
        self.assertTrue(result["artifact_files_unchanged"])
        self.assertTrue(result["cache_directory_unchanged"])
        self.assertFalse(result["metadata_exception_applied"])

    def test_only_exact_marker_rewrite_with_complete_unchanged_artifacts_is_allowed_and_audited(self):
        before = inventory(); after = copy.deepcopy(before)
        after[MUTABLE_COMPLETION_MARKER] = sha("new marker stat metadata")
        original = copy.deepcopy((before, after))
        result = compare_embedding_cache(before, after)
        self.assertTrue(result["valid"])
        self.assertTrue(result["artifact_files_unchanged"])
        self.assertFalse(result["cache_directory_unchanged"])
        self.assertTrue(result["metadata_exception_applied"])
        self.assertEqual(result["mutable_metadata_changes"], [{"path": MUTABLE_COMPLETION_MARKER,
            "before": before[MUTABLE_COMPLETION_MARKER], "after": after[MUTABLE_COMPLETION_MARKER]}])
        self.assertEqual((before, after), original)

    def test_real_model_tokenizer_and_config_changes_cannot_be_hidden_by_marker_rewrite(self):
        for path in REQUIRED_MODEL_ARTIFACTS:
            for action in ("change", "delete"):
                with self.subTest(path=path, action=action):
                    before = inventory(); after = before.copy()
                    after[MUTABLE_COMPLETION_MARKER] = sha("rewritten metadata")
                    if action == "delete":
                        del after[path]
                    else:
                        after[path] = sha("different real content")
                    result = compare_embedding_cache(before, after)
                    self.assertFalse(result["valid"])
                    self.assertFalse(result["artifact_files_unchanged"])
                    self.assertIn(path, [c["path"] for c in result["unexpected_changes"]])

    def test_new_real_or_hidden_files_and_unknown_complete_markers_are_rejected(self):
        for path in ("weights.onnx", ".other-metadata", ".zvec-grep-artifacts-unknown.complete",
                     MUTABLE_COMPLETION_MARKER.replace("7db565dd", "aaaaaaaa"),
                     "another-model/" + MUTABLE_COMPLETION_MARKER.rsplit("/", 1)[1]):
            with self.subTest(path=path):
                before = inventory(); after = before.copy(); after[path] = sha("new")
                self.assertFalse(compare_embedding_cache(before, after)["valid"])
                before[path] = sha("before"); after[path] = sha("after")
                self.assertFalse(compare_embedding_cache(before, after)["valid"])

    def test_marker_exception_needs_all_fixed_artifacts_and_marker_before_and_after(self):
        for missing in (*REQUIRED_MODEL_ARTIFACTS, MUTABLE_COMPLETION_MARKER):
            for side in ("before", "after"):
                with self.subTest(missing=missing, side=side):
                    before = inventory(); after = before.copy(); after[MUTABLE_COMPLETION_MARKER] = sha("new")
                    del (before if side == "before" else after)[missing]
                    result = compare_embedding_cache(before, after)
                    self.assertFalse(result["valid"])
                    self.assertFalse(result["metadata_exception_applied"])

    def test_missing_empty_or_malformed_inventories_do_not_pass(self):
        for before, after in ((None, inventory()), (inventory(), None), ({}, {}), ({}, inventory()),
                              (inventory(), {}), (True, True), ({"weights.onnx": "unknown"}, {"weights.onnx": "unknown"})):
            with self.subTest(before=before, after=after):
                self.assertFalse(compare_embedding_cache(before, after)["valid"])

    def test_marker_symlink_is_not_an_accepted_sha256_metadata_change(self):
        before = inventory(); after = before.copy()
        after[MUTABLE_COMPLETION_MARKER] = "symlink:/another/model/weights"
        self.assertFalse(compare_embedding_cache(before, after)["valid"])


if __name__ == "__main__":
    unittest.main()
