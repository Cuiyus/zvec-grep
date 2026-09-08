from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from zg_bench.swe_qa.retrieval_eval import (
    digest,
    evaluate,
    load_events,
    load_manifest,
    main,
    match_visible_entries,
    public_items,
    score_public_text,
)


BASE = Path(__file__).resolve().parents[1]
MANIFEST = BASE / "cases/reflex-6.entries.json"
FUNCTION = "function-computed-var-deps"


def fixture():
    manifest = load_manifest(MANIFEST)
    manifest["queries"] = [manifest["queries"][0]]
    return manifest


def item(rank=1, path="reflex/vars/base.py", start=2411, end=2460,
         body="source:\n2411\tdef _deps(\n2412\t        self,\n...\n"):
    return f"#{rank} matchedBy=vector {path}:{start}-{end}\n{body}\n"


def public(*items):
    return "freshness: fresh\n" + "".join(items)


def event(manifest, text, *, repetition=1, mode="vector", **updates):
    result = {"event": "search", "mode": mode, "repetition": repetition, "status": "success",
              "source_identity": {"git_commit": manifest["repo"]["commit"]},
              "index_identity": {"embedding": {"provider": "local", "model": "potion-code-16m-v2"}},
              "package": {"name": "@zvec/zvec-grep", "version": "0.2.2"},
              "request": {"query": manifest["queries"][0]["text"], "limit": 10, "autoUpdate": False},
              "text": text, "text_sha256": digest(text), "duration_ms": repetition * 10}
    result.update(updates)
    return result


class RetrievalEvalTest(unittest.TestCase):
    def test_frozen_manifest_original_gold_identity_and_distinct_subintents(self):
        manifest = load_manifest(MANIFEST)
        case_path = BASE / "cases/reflex-6.json"
        case = json.loads(case_path.read_text())
        self.assertEqual(manifest["source_case_sha256"], digest(case_path.read_bytes()))
        self.assertEqual(manifest["queries"][0]["text"], case["question"])
        self.assertEqual(len({q["intent_id"] for q in manifest["queries"]}), 1)
        self.assertEqual(len({q["stratum"] for q in manifest["queries"]}), 3)
        self.assertEqual(len(case["evidence"]), 9)
        self.assertFalse(manifest["protocol"]["all_optional_groups_required"])
        for query in manifest["queries"]:
            for target in manifest["targets"]:
                if target.get("symbol"):
                    self.assertNotIn(target["symbol"], query["text"])

    def test_numbered_exact_definition_and_separate_file_level(self):
        manifest = fixture()
        scored = score_public_text(public(item()), manifest)
        self.assertEqual(scored["levels"]["function"]["first_hit_rank"], 1)
        self.assertTrue(scored["levels"]["function"]["hit_at_1"])
        self.assertEqual(scored["levels"]["file"]["first_hit_rank"], 1)
        self.assertIsNone(scored["levels"]["class"]["first_hit_rank"])
        self.assertEqual(next(m for m in scored["matches"] if m["target_id"] == FUNCTION)["match_kind"], "numbered_definition")

    def test_parent_range_name_mentions_and_wrong_definition_line_are_not_function_hits(self):
        manifest = fixture()
        variants = [
            item(start=2050, end=2531, body="outline:\nclass ComputedVar(Var[RETURN_TYPE]):\n...\nsource:\n2415\t    ) -> dict[str, set[str]]:\n2416\t\"\"\"Determine var dependencies of this ComputedVar.\n"),
            item(body="source:\n2411\t# calls def _deps( through another object\n"),
            item(body="source:\n100\tdef _deps(\n"),
            item(start=2050, end=2531, body="outline:\ndef _deps(\nsource:\n2416\tDetermine dependencies.\n"),
            item(path="reflex/other.py"),
        ]
        for text in variants:
            with self.subTest(text=text):
                self.assertFalse(score_public_text(public(text), manifest)["levels"]["function"]["hit_at_10"])
        first = score_public_text(public(variants[0]), manifest)
        self.assertTrue(first["levels"]["class"]["hit_at_1"])

    def test_exact_function_outline_is_a_valid_entry(self):
        text = public(item(path="reflex/state.py", start=769, end=822,
                           body="outline:\n@classmethod\n    def _init_var_dependency_dicts(cls):\n...\nsource:\n771\tInitialize dependencies.\n"))
        scored = score_public_text(text, fixture())
        self.assertTrue(scored["levels"]["function"]["hit_at_1"])
        self.assertEqual(next(m for m in scored["matches"] if m["level"] == "function")["match_kind"], "definition_outline_at_exact_entry")

    def test_read_observations_are_supported_but_path_conflicts_and_prose_are_not(self):
        manifest = fixture()
        text = "<path>/app/reflex/vars/base.py</path>\n<type>file</type>\n<content>\n2411:     def _deps(\n</content>\n"
        matches = match_visible_entries(text, manifest)
        self.assertIn(FUNCTION, [m["target_id"] for m in matches])
        self.assertTrue(all(m["rank"] is None for m in matches))
        self.assertEqual(match_visible_entries(text, manifest, path_hint="/app/reflex/state.py"), [])
        hinted = match_visible_entries("2411:     def _deps(\n", manifest, path_hint="/app/reflex/vars/base.py")
        self.assertIn(FUNCTION, [m["target_id"] for m in hinted])
        prose = match_visible_entries("The function _deps analyzes dependencies.", manifest, path_hint="/app/reflex/vars/base.py")
        self.assertFalse(any(m["level"] == "function" for m in prose))

    def test_original_rank_duplicates_and_prefix_cost_are_preserved(self):
        manifest = fixture()
        chunks = [item(i, path="unrelated.py", start=1, end=2, body="source:\n1\tpass\n") for i in range(1, 6)]
        text = public(*chunks, item(6), item(7))
        scored = score_public_text(text, manifest)
        self.assertEqual(scored["levels"]["function"]["first_hit_rank"], 6)
        self.assertFalse(scored["levels"]["function"]["hit_at_5"])
        self.assertEqual(scored["levels"]["function"]["rr_at_10"], 1 / 6)
        self.assertEqual(scored["levels"]["function"]["bytes_through_first_hit"], len(public(*chunks, item(6)).encode()))
        self.assertEqual([m["rank"] for m in scored["matches"] if m["target_id"] == FUNCTION], [6, 7])
        self.assertEqual(scored["public_slots_visible"], 7)

    def test_native_grep_definitions_match_only_under_the_correct_file(self):
        manifest = fixture()
        text = "Found 3 matches\n/app/reflex/state.py:\n  Line 2411:     def _deps(\n/app/reflex/vars/base.py:\n  Line 2411:     def _deps(\n  Line 2500:     mentions _populate_dependencies\n"
        matches = match_visible_entries(text, manifest)
        functions = [m for m in matches if m["level"] == "function"]
        self.assertEqual([m["target_id"] for m in functions], [FUNCTION])
        self.assertIsNone(functions[0]["rank"])
        self.assertEqual(match_visible_entries("no path", manifest, path_hint={}), [])

    def test_byte_budget_only_counts_complete_anchors_and_preserves_unicode(self):
        manifest = fixture()
        text = public(item(body="source:\n2410\t中文 context\n2411\tdef _deps(\n2412\t        self,\n...\n"))
        cutoff = len(text[:text.index("def _deps(") + len("def _deps(")].encode())
        partial = score_public_text(text, manifest, cutoff)
        self.assertFalse(partial["levels"]["function"]["hit_at_10"])
        enough = score_public_text(text, manifest, cutoff + 1)
        self.assertTrue(enough["levels"]["function"]["hit_at_10"])
        self.assertEqual(enough["levels"]["function"]["bytes_through_first_hit"], cutoff + 1)
        unicode_cutoff = len(text[:text.index("中")].encode()) + 1
        unicode_score = score_public_text(text, manifest, unicode_cutoff)
        self.assertEqual(unicode_score["visible_bytes"], unicode_cutoff - 1)
        self.assertTrue(unicode_score["truncated"])
        self.assertFalse(unicode_score["levels"]["function"]["hit_at_10"])

    def test_or_group_needs_one_primary_entry_not_full_chain(self):
        manifest = fixture()
        scored = score_public_text(public(item()), manifest)
        self.assertTrue(scored["groups"]["dependency-entry"]["hit_at_1"])
        self.assertFalse(scored["groups"]["registration"]["hit_at_10"])
        self.assertTrue(scored["levels"]["function"]["hit_at_1"])
        getter = public(item(start=2524, end=2531,
                             body="source:\n2525\tdef fget(self) -> Callable[[BaseState], RETURN_TYPE]:\n"))
        scored_getter = score_public_text(getter, manifest)
        self.assertTrue(scored_getter["groups"]["accessor"]["hit_at_1"])
        self.assertFalse(scored_getter["levels"]["function"]["hit_at_10"])

    def test_missing_invalid_and_nonsequential_public_text_are_unknown_not_zero(self):
        manifest = fixture()
        self.assertEqual(score_public_text("unrecognized formatter", manifest)["status"], "unknown")
        self.assertEqual(score_public_text(public(item(2)), manifest)["status"], "unknown")
        scored = score_public_text(public(item(path="elsewhere.py")), manifest)
        self.assertEqual(scored["status"], "scored")
        self.assertEqual(scored["levels"]["function"]["rr_at_10"], 0)
        self.assertIsNone(scored["levels"]["function"]["bytes_through_first_hit"])

    def test_five_repeats_do_not_average_into_quality_or_replace_missing_first(self):
        manifest = fixture()
        hit, miss = public(item()), public(item(path="elsewhere.py"))
        events = [event(manifest, hit if r == 1 else miss, repetition=r) for r in range(1, 6)]
        report = evaluate(events, manifest)
        quality = next(q for q in report["query_quality"] if q["mode"] == "vector")
        self.assertEqual(quality["quality_samples"], 1)
        self.assertEqual(quality["scores"]["native"]["levels"]["function"]["rr_at_10"], 1)
        self.assertEqual(report["independent_intents"], 1)
        stability = next(q for q in report["repeat_stability"] if q["mode"] == "vector")
        self.assertEqual(stability["scored_repeats"], 5)
        self.assertFalse(stability["public_text_identical_all_repeats"])
        self.assertEqual(stability["entry_rank_signatures"]["function"], [1, None, None, None, None])
        absent = evaluate(events[1:], manifest)
        first_quality = next(q for q in absent["query_quality"] if q["mode"] == "vector")
        self.assertEqual(first_quality["scores"]["native"]["status"], "unknown")
        self.assertIsNone(first_quality["scores"]["native"]["levels"]["function"]["rr_at_10"])

    def test_unknown_formulations_keep_planned_denominator(self):
        manifest = load_manifest(MANIFEST)
        report = evaluate([event(manifest, public(item()))], manifest)
        self.assertEqual(report["planned_executions"], 45)
        aggregate = next(r for r in report["descriptive_formulation_aggregates"]
                         if r["mode"] == "vector" and r["budget"] == "native" and r["level"] == "function")
        self.assertEqual(aggregate["planned_formulations"], 3)
        self.assertEqual(aggregate["scored_formulations"], 1)
        self.assertIsNone(aggregate["mrr_at_10"])
        self.assertEqual(aggregate["observed_formulations_mrr_at_10"], 1)

    def test_identity_mismatch_duplicate_slots_and_hidden_chunks_never_supply_hits(self):
        manifest = fixture()
        miss = public(item(path="elsewhere.py"))
        good = event(manifest, miss, result={"items": [{"text": public(item())}]})
        valid = evaluate([good], manifest)
        self.assertFalse(next(q for q in valid["query_quality"] if q["mode"] == "vector")["scores"]["native"]["levels"]["function"]["hit_at_10"])
        for update in ({"source_identity": {}}, {"package": {"name": "@zvec/zvec-grep", "version": "0.2.1"}},
                       {"status": "error"}, {"text_sha256": "0" * 64}, {"index_identity": {}}):
            with self.subTest(update=update):
                bad = evaluate([event(manifest, public(item()), **update)], manifest)
                self.assertEqual(next(q for q in bad["query_quality"] if q["mode"] == "vector")["scores"]["native"]["status"], "unknown")
        duplicate = evaluate([good, good], manifest)
        self.assertEqual(next(q for q in duplicate["query_quality"] if q["mode"] == "vector")["scores"]["native"]["status"], "unknown")

    def test_source_validation_rejects_anchor_and_file_hash_drift(self):
        manifest = fixture()
        target = next(t for t in manifest["targets"] if t["target_id"] == FUNCTION)
        manifest["targets"] = [target]
        manifest["groups"] = [g for g in manifest["groups"] if g["group_id"] == "dependency-entry"]
        manifest["groups"][0]["target_ids"] = [FUNCTION]
        source = "\n" * (target["definition_line"] - 1) + target["definition"]
        manifest["source_files"] = [{"path": target["path"], "sha256": digest(source)}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / target["path"]
            source_path.parent.mkdir(parents=True)
            source_path.write_text(source)
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest))
            load_manifest(path, root)
            source_path.write_text(source + "# drift\n")
            with self.assertRaisesRegex(ValueError, "source file hash mismatch"):
                load_manifest(path, root)
            invalid = copy.deepcopy(manifest)
            invalid["targets"][0]["definition"] = "def invented():\n"
            path.write_text(json.dumps(invalid))
            with self.assertRaisesRegex(ValueError, "definition anchor"):
                load_manifest(path)

    def test_offline_cli_context_identity_and_parse_errors_are_preserved(self):
        manifest = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            data = event(manifest, public(item()))
            package = data.pop("package")
            data["run_id"] = "run1"
            events = root / "events.jsonl"
            events.write_text(json.dumps({"event": "start", "run_id": "run1", "package": package}) + "\n" + json.dumps(data) + "\nNOT_JSON\n")
            loaded, errors = load_events(events)
            self.assertEqual(loaded[0]["_package"]["version"], "0.2.2")
            self.assertEqual(errors[0]["line"], 3)
            output = root / "report.json"
            main(["--events", str(events), "--manifest", str(manifest_path), "--output", str(output)])
            report = json.loads(output.read_text())
            self.assertEqual(report["event_parse_errors"], errors)
            self.assertEqual(report["manifest_sha256"], digest(manifest_path.read_bytes()))
            self.assertIn("post-hoc", output.with_suffix(".md").read_text())
            self.assertEqual(report["planned_executions"], 15)


if __name__ == "__main__":
    unittest.main()
