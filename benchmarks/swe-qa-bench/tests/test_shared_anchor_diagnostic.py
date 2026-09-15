"""Partial-label coverage cannot masquerade as a query ranking improvement."""
import copy
import unittest

from zg_bench.swe_qa.shared_anchor_diagnostic import build_diagnostic


def score(matches):
    rank = min((m[1] for m in matches), default=None)
    return {"native": {"query_relevance": {"target": {"status": "scored", "first_hit_rank": rank,
        "matches": [{"target_id": t, "rank": r} for t, r in matches]}}}}


class SharedAnchorTests(unittest.TestCase):
    def setUp(self):
        self.labels = {"targets": [{"target_id": t, "symbol": t} for t in ("class", "deps", "getter")],
            "queries": [{"query_id": q, "classification": kind, "annotation_status": "reviewed", "accepted_target_ids": ts}
                for q, kind, ts in (("original", "original", ["deps"]),
                    ("rewrite-a", "equivalent_rewrite", ["class", "deps"]),
                    ("rewrite-b", "equivalent_rewrite", ["deps"]),
                    ("subgoal", "legitimate_subgoal", ["getter"]))],
            "request_bindings": [{"query_id": q, "context_id": "initial"} for q in ("original", "rewrite-a", "rewrite-b", "subgoal")]}
        self.replay = {"units": [{"unit_id": q, "kind": "original" if q == "original" else "faithful",
            "quality_observation": {"repetition": 1, "output_sha256": "same" if q.startswith("rewrite") else q,
                "context_scores": [{"annotation_id": q, "context_id": "initial", "request_scores": score(matches)}]}}
            for q, matches in (("original", [("deps", 6)]), ("rewrite-a", [("class", 3), ("deps", 9)]),
                               ("rewrite-b", [("deps", 9)]), ("subgoal", [("getter", 1)]))]}

    def test_same_return_different_primary_labels_get_same_common_rank(self):
        before = copy.deepcopy((self.labels, self.replay))
        result = build_diagnostic(self.labels, [], self.replay)
        context = result["contexts"][0]
        self.assertEqual(context["common_accepted_target_ids"], ["deps"])
        self.assertEqual(context["accepted_target_set_variants"], 2)
        rows = {r["annotation_id"]: r for r in context["observations"]}
        self.assertEqual([rows[q]["primary_first_hit_rank"] for q in ("rewrite-a", "rewrite-b")], [3, 9])
        self.assertEqual([rows[q]["common_anchor_score"]["first_hit_rank"] for q in ("rewrite-a", "rewrite-b")], [9, 9])
        self.assertEqual(rows["original"]["common_anchor_score"]["rr_at_10"], 1 / 6)
        self.assertEqual(result["excluded_subgoal_or_other_annotation_ids"], ["subgoal"])
        self.assertEqual((self.labels, self.replay), before)

    def test_unknown_annotation_does_not_disappear_to_enlarge_intersection(self):
        self.labels["queries"][2]["annotation_status"] = "unknown"
        context = build_diagnostic(self.labels, [], self.replay)["contexts"][0]
        self.assertEqual(context["unknown_annotation_ids"], ["rewrite-b"])
        self.assertEqual(context["common_accepted_target_ids"], [])
        self.assertIsNone(context["observations"][0]["common_anchor_score"]["rr_at_10"])

    def test_empty_intersection_is_unknown_not_a_retrieval_miss(self):
        self.labels["queries"][2]["accepted_target_ids"] = ["getter"]
        context = build_diagnostic(self.labels, [], self.replay)["contexts"][0]
        self.assertEqual(context["status"], "unknown")
        self.assertIsNone(context["observations"][0]["common_anchor_score"]["hit_at_10"])

    def test_missing_common_anchor_in_known_output_is_a_miss(self):
        self.replay["units"][1]["quality_observation"]["context_scores"][0]["request_scores"] = score([("class", 3)])
        row = build_diagnostic(self.labels, [], self.replay)["contexts"][0]["observations"][1]
        self.assertEqual(row["common_anchor_score"]["status"], "scored")
        self.assertEqual(row["common_anchor_score"]["rr_at_10"], 0)

    def test_later_repeat_and_other_context_cannot_replace_first_observation(self):
        self.replay["units"][1]["quality_observation"]["repetition"] = 5
        self.labels["request_bindings"][2]["context_id"] = "feedback"
        contexts = build_diagnostic(self.labels, [], self.replay)["contexts"]
        initial = next(c for c in contexts if c["context_id"] == "initial")
        row = next(r for r in initial["observations"] if r["annotation_id"] == "rewrite-a")
        self.assertEqual(row["common_anchor_score"]["status"], "unknown")
        feedback = next(c for c in contexts if c["context_id"] == "feedback")
        self.assertFalse(feedback["has_original_reference"])
        self.assertEqual(feedback["status"], "unknown")

    def test_actual_observations_remain_distinct_from_replay_and_unknown_is_preserved(self):
        chain = {"annotation_id": "rewrite-a", "context_id": "initial", "call_id": "call",
            "actual_observation": {"output_sha256": "actual", "request_scores": score([("class", 3), ("deps", 12)])}}
        groups = [{"group": "g", "trials": [{"trial_id": "r", "query_chains": [chain]}]}]
        context = build_diagnostic(self.labels, groups, self.replay)["contexts"][0]
        row = next(r for r in context["observations"] if r["kind"] == "actual_e2e")
        self.assertEqual(row["common_anchor_score"]["first_hit_rank"], 12)
        self.assertEqual(row["common_anchor_score"]["rr_at_10"], 0)
        chain["actual_observation"]["request_scores"] = None
        context = build_diagnostic(self.labels, groups, self.replay)["contexts"][0]
        row = next(r for r in context["observations"] if r["kind"] == "actual_e2e")
        self.assertIsNone(row["common_anchor_score"]["rr_at_10"])
