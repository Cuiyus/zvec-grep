from __future__ import annotations

import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from zg_bench.swe_qa.query_relevance import (
    canonical_request_key, load_labels, main, resolve_label, score_query_text,
)
from zg_bench.swe_qa.retrieval_eval import digest, load_manifest


BASE = Path(__file__).resolve().parents[1]
LABELS = BASE / "cases/reflex-6.query-intents.json"
ENTRIES = BASE / "cases/reflex-6.entries.json"
GETTER_QUERY = "derived state variable computation function getter"
FULL_QUERY = "derived state variable class computation function accessor dependency tracking recomputation"


def item(rank=1, *, path="reflex/vars/base.py", start=2524, end=2531,
         body="source:\n2525\t    def fget(self) -> Callable[[BaseState], RETURN_TYPE]:\n2531\t        return self._fget\n"):
    return f"#{rank} matchedBy=vector {path}:{start}-{end}\n{body}\n"


def public(*items):
    return "freshness: fresh\n" + "".join(items)


def irrelevant(rank):
    return item(rank, path="unreviewed.py", start=1, end=2, body="source:\n1\tpass\n")


class QueryRelevanceTest(unittest.TestCase):
    def setUp(self):
        self.labels = load_labels(LABELS)
        self.task = load_manifest(ENTRIES)

    def score(self, text, query=GETTER_QUERY, **kwargs):
        return score_query_text(text, self.labels, self.task, query=query, **kwargs)

    def test_frozen_question_source_facts_and_observed_query_catalog(self):
        case = json.loads((BASE / "cases/reflex-6.json").read_text())
        self.assertEqual(self.labels["original_question"], case["question"])
        self.assertEqual(self.labels["source_case_sha256"], digest((BASE / "cases/reflex-6.json").read_bytes()))
        self.assertEqual(self.labels["legacy_task_entries_sha256"], digest(ENTRIES.read_bytes()))
        self.assertEqual(len(self.labels["task_facts"]), 3)
        self.assertEqual(len(self.labels["queries"]), 12)
        self.assertFalse(self.labels["annotation_provenance"]["independent_human_gold"])
        self.assertTrue(self.labels["annotation_provenance"]["prior_getter_result_seen"])
        artificial = {q["text"] for q in self.task["queries"] if q["query_id"] != "original"}
        self.assertFalse(artificial & {q["text"] for q in self.labels["queries"]})
        self.assertTrue(all(q["observed_at"] for q in self.labels["queries"] if q["classification"] != "original"))

    def test_getter_subgoal_hit_at_four_does_not_become_legacy_primary_hit(self):
        text = public(*(irrelevant(i) for i in range(1, 4)), item(4))
        report = self.score(text)
        target = report["query_relevance"]["target"]
        self.assertEqual(target["first_hit_rank"], 4)
        self.assertTrue(target["hit_at_5"])
        self.assertEqual(target["rr_at_10"], 0.25)
        self.assertEqual(target["bytes_through_first_hit"], len(text.encode()))
        self.assertFalse(report["task_entry_score"]["levels"]["function"]["hit_at_10"])
        self.assertTrue(report["task_entry_score"]["groups"]["accessor"]["hit_at_5"])
        self.assertEqual(report["query_relevance"]["unreviewed_public_ranks"], [1, 2, 3])

    def test_original_task_entry_or_and_bridge_are_separate(self):
        deps = public(item(start=2411, end=2460, body="source:\n2411\t    def _deps(\n"))
        r = self.score(deps, self.labels["original_question"])
        self.assertTrue(r["query_relevance"]["target"]["hit_at_1"])
        self.assertTrue(r["task_entry_score"]["levels"]["function"]["hit_at_1"])
        self.assertFalse(r["task_entry_score"]["groups"]["registration"]["hit_at_10"])
        getter = self.score(public(item()), self.labels["original_question"])["query_relevance"]
        self.assertFalse(getter["target"]["hit_at_1"])
        self.assertTrue(getter["bridge"]["hit_at_1"])

    def test_async_accessor_is_valid_alternative_without_needing_both_getters(self):
        text = public(item(start=2667, end=2674, body="source:\n2668\t    def fget(self) -> Callable[[BaseState], Coroutine[None, None, RETURN_TYPE]]:\n"))
        r = self.score(text)
        self.assertTrue(r["query_relevance"]["target"]["hit_at_1"])
        self.assertEqual(r["query_relevance"]["target"]["matches"][0]["symbol"], "AsyncComputedVar.fget")

    def test_unknown_or_ambiguous_intent_never_zeros_independent_task_score(self):
        text = public(item(start=2411, end=2460, body="source:\n2411\t    def _deps(\n"))
        ambiguous = next(q["text"] for q in self.labels["queries"] if q["classification"] == "ambiguous")
        for query in ["unseen reformulation", ambiguous, {"unexpected": "object"}]:
            with self.subTest(query=query):
                r = self.score(text, query)
                self.assertEqual(r["query_relevance"]["status"], "unknown")
                self.assertIsNone(r["query_relevance"]["target"]["hit_at_10"])
                self.assertTrue(r["task_entry_score"]["levels"]["function"]["hit_at_1"])
                self.assertEqual(r["query_relevance"]["unreviewed_public_ranks"], [1])

    def test_off_task_label_retains_classification_and_task_observation(self):
        q = copy.deepcopy(self.labels["queries"][0])
        q.update(query_id="test-drift", text="test drift query", classification="off_task", accepted_target_ids=[], bridge_target_ids=[])
        self.labels["queries"].append(q)
        r = self.score(public(item()), "test drift query")
        self.assertEqual(r["query_relevance"]["classification"], "off_task")
        self.assertEqual(r["query_relevance"]["status"], "unknown")
        self.assertTrue(r["task_entry_score"]["groups"]["accessor"]["hit_at_1"])

    def test_array_looking_strings_are_never_split_or_normalized(self):
        text = '["derived state computed getter dependency tracking", "state variable recompute dependencies"]'
        q, method = resolve_label(self.labels, query=text)
        self.assertIsNotNone(q)
        self.assertEqual(q["text"], text)
        self.assertEqual(method, "exact_query")
        self.assertIsNone(resolve_label(self.labels, query="derived state computed getter dependency tracking")[0])
        self.assertIsNone(resolve_label(self.labels, query=text.replace(", ", ","))[0])
        original = {"root": "/app", "vector": text, "query": FULL_QUERY, "limit": 15, "fuse": True}
        shuffled = dict(reversed(list(original.items())))
        self.assertEqual(canonical_request_key(original), canonical_request_key(shuffled))
        self.assertNotEqual(canonical_request_key(original), canonical_request_key({**original, "vector": json.loads(text)}))
        label, method = resolve_label(self.labels, request=shuffled)
        self.assertEqual(label["text"], FULL_QUERY)
        self.assertEqual(method, "exact_canonical_request")

    def test_unreviewed_multi_route_request_does_not_fall_back_to_main_query(self):
        label, method = resolve_label(self.labels, request={"query": FULL_QUERY, "vector": "new unrelated route"})
        self.assertIsNone(label)
        self.assertEqual(method, "unreviewed_multi_text_request")
        # Controlled replay explicitly selects a text view, not a faithful request view.
        label, method = resolve_label(self.labels, query=FULL_QUERY, request={"query": FULL_QUERY, "vector": "new unrelated route"})
        self.assertEqual(label["text"], FULL_QUERY)
        self.assertEqual(method, "exact_query")

    def test_complete_anchor_required_at_byte_cut_and_unicode_is_safe(self):
        text = public(item(body="source:\n2524\t中文\n2525\t    def fget(self) -> Callable[[BaseState], RETURN_TYPE]:\n2531\treturn self._fget\n"))
        cutoff = len(text[:text.index("RETURN_TYPE]:") + len("RETURN_TYPE]:")].encode())
        self.assertFalse(self.score(text, budget=cutoff)["query_relevance"]["target"]["hit_at_10"])
        self.assertTrue(self.score(text, budget=cutoff + 1)["query_relevance"]["target"]["hit_at_10"])
        inside_unicode = len(text[:text.index("中")].encode()) + 1
        r = self.score(text, budget=inside_unicode)
        self.assertEqual(r["query_relevance"]["visible_bytes"], inside_unicode - 1)
        self.assertFalse(r["query_relevance"]["target"]["hit_at_10"])

    def test_faithful_twenty_slots_preserve_rank_and_fixed_top_ten(self):
        text = public(*(item(i) if i == 15 else irrelevant(i) for i in range(1, 21)))
        r = self.score(text)
        self.assertEqual(r["query_relevance"]["target"]["first_hit_rank"], 15)
        self.assertFalse(r["query_relevance"]["target"]["hit_at_10"])
        self.assertEqual(r["query_relevance"]["target"]["rr_at_10"], 0)
        self.assertEqual(r["task_entry_score"]["native_parser_slot_limit"], 20)
        self.assertEqual(r["task_entry_score"]["public_slots_visible"], 20)
        self.assertFalse(r["task_entry_score"]["groups"]["accessor"]["hit_at_10"])

    def test_duplicates_keep_slots_and_unannotated_is_not_irrelevant(self):
        text = public(irrelevant(1), item(2), item(3))
        r = self.score(text)["query_relevance"]
        self.assertEqual([m["rank"] for m in r["target"]["matches"]], [2, 3])
        self.assertEqual(r["target"]["first_hit_rank"], 2)
        self.assertEqual(r["unreviewed_public_ranks"], [1])
        self.assertFalse(r["exhaustive_relevance_judgments"])

    def test_empty_is_scored_miss_but_malformed_format_is_unknown(self):
        empty = self.score("freshness: fresh\nNo results.")
        self.assertEqual(empty["query_relevance"]["status"], "scored")
        self.assertFalse(empty["query_relevance"]["target"]["hit_at_10"])
        self.assertEqual(empty["query_relevance"]["unreviewed_public_ranks"], [])
        for text in ["unsupported formatter", public(item(2))]:
            r = self.score(text)
            self.assertEqual(r["query_relevance"]["status"], "unknown")
            self.assertIsNone(r["query_relevance"]["target"]["hit_at_10"])

    def test_parent_range_and_symbol_mentions_cannot_create_target_hits(self):
        for body in ["source:\n2525\t# fget returns a getter\n", "source:\n999\t    def fget(self) -> Callable[[BaseState], RETURN_TYPE]:\n", "outline:\n    def fget(self) -> Callable[[BaseState], RETURN_TYPE]:\n"]:
            r = self.score(public(item(start=2050, end=2531, body=body)))
            self.assertFalse(r["query_relevance"]["target"]["hit_at_10"])

    def test_labels_reject_invalid_anchor_unknown_reference_and_role_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.json"
            for mutation in [lambda x: x["targets"][0].update(definition="class Forged:\n"),
                             lambda x: x["queries"][0]["accepted_target_ids"].append("unknown-id"),
                             lambda x: x["queries"][0]["bridge_target_ids"].append(x["queries"][0]["accepted_target_ids"][0])]:
                broken = copy.deepcopy(self.labels)
                mutation(broken)
                path.write_text(json.dumps(broken))
                with self.assertRaises(ValueError):
                    load_labels(path)

    def test_source_validation_rejects_content_drift_even_if_file_hash_is_replaced(self):
        # Construct a portable source fixture from the frozen proof excerpts and
        # definition anchors; no developer's checkout is required by CI tests.
        labels = copy.deepcopy(self.labels)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for file in labels["source_files"]:
                fragments = [(e["start_line"], e["text"]) for e in labels["source_evidence"] if e["path"] == file["path"]]
                fragments += [(t["definition_line"], t["definition"]) for t in labels["targets"] if t["path"] == file["path"]]
                size = max(start + len(text.splitlines()) for start, text in fragments)
                lines = ["\n"] * size
                for start, text in fragments:
                    for offset, line in enumerate(text.splitlines(keepends=True)):
                        lines[start - 1 + offset] = line
                target = root / file["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("".join(lines))
                file["sha256"] = digest(target.read_bytes())
            path = root / "labels.json"
            path.write_text(json.dumps(labels))
            load_labels(path, root)
            source = root / "reflex/vars/base.py"
            source.write_text(source.read_text().replace("return self._fget", "return unrelated", 1))
            with self.assertRaisesRegex(ValueError, "Source file hash mismatch"):
                load_labels(path, root)
            next(f for f in labels["source_files"] if f["path"] == "reflex/vars/base.py")["sha256"] = digest(source.read_bytes())
            path.write_text(json.dumps(labels))
            with self.assertRaisesRegex(ValueError, "Source evidence text differs"):
                load_labels(path, root)

    def test_cli_preserves_inputs_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text_path, output = root / "public.txt", root / "result.json"
            text_path.write_text(public(item()))
            args = ["--labels", str(LABELS), "--task-entries", str(ENTRIES), "--public-text", str(text_path), "--query", GETTER_QUERY, "--output", str(output)]
            main(args)
            report = json.loads(output.read_text())
            self.assertTrue(report["query_relevance"]["target"]["hit_at_1"])
            self.assertEqual(report["input_sha256"][str(text_path)], digest(text_path.read_bytes()))
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(args)


if __name__ == "__main__":
    unittest.main()
