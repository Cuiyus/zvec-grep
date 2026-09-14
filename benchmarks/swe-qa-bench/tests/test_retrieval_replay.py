"""Frozen-query planning and refusal to score unverified replay observations."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zg_bench.swe_qa.retrieval_replay import build_plan, evaluate_replays, replay_markdown, request_queries
from zg_bench.swe_qa.retrieval_replay import main, SOURCE_COMMIT, SOURCE_RUN


QUESTION = "What role does the getter have?"
QUERY = "computation function getter"
ARRAY_LITERAL = '["computed getter", "dependency tracking"]'
REQUEST = {"root": "/app", "queries": [QUERY], "routes": [{"mode": "vector", "query": ARRAY_LITERAL}],
           "autoUpdate": False, "trace": True}


def call(cid, request=None):
    request = copy.deepcopy(REQUEST if request is None else request)
    return {"call_id": cid, "raw_arguments": {"root": "/app", "query": QUERY, "vector": ARRAY_LITERAL},
            "backend_link": {"status": "matched"},
            "backend": {"request": request, "source": {"path": "agent/zg-trace.jsonl", "line": 3, "sha256": "source-event-hash"}}}


def trial(tid, calls=None, *, profile="zvec-grep", adoption=True, evidence="observed"):
    return {"trial_id": tid, "profile": profile, "zg_adoption_observed": adoption,
            "query_evidence_status": evidence, "first_zg_decision_round": None if calls is None else {
                "zg_calls": calls, "model_turn_index": 1, "has_prior_turn_feedback": False}}


def analysis_fixture():
    return {"query_catalog": [{"query_id": "query-" + hashlib.sha256(QUERY.encode()).hexdigest()[:16], "text": QUERY, "occurrences": [{"group": "g", "trial_id": "r1", "call_id": "c1"},
                                                                 {"group": "g", "trial_id": "r2", "call_id": "c2"}]},
                              {"query_id": "query-" + hashlib.sha256(ARRAY_LITERAL.encode()).hexdigest()[:16], "text": ARRAY_LITERAL, "occurrences": [{"group": "g", "trial_id": "r1", "call_id": "c1"}]}],
            "groups": [{"group": "g", "trials": [trial("r1", [call("c1")]), trial("r2", [call("c2")])]}]}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def write_events(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in values) + "\n")


class ReplayPlanTests(unittest.TestCase):
    def test_default_replays_only_actual_requests_and_fixed_original_hybrid(self):
        plan = build_plan(analysis_fixture(), QUESTION)
        self.assertEqual([u["kind"] for u in plan["units"]].count("faithful"), 1)
        self.assertEqual([u["kind"] for u in plan["units"]].count("original"), 1)
        self.assertNotIn("controlled", {u["kind"] for u in plan["units"]})
        self.assertEqual(plan["planned_executions"], 10)
        self.assertEqual(plan["quality_repetition"], 1)
        original = next(u for u in plan["units"] if u["kind"] == "original")
        self.assertEqual(original["unit_id"], "original-hybrid")
        self.assertEqual(original["mode"], "hybrid")
        self.assertEqual(original["query_texts"], [QUESTION])
        self.assertEqual(original["request"], {
            "root": "/app", "query": QUESTION, "limit": 10, "autoUpdate": False, "trace": True})
        faithful_ids = [u["unit_id"] for u in plan["units"] if u["kind"] == "faithful"]
        self.assertTrue(plan["report_groups"]["primary"]["enabled"])
        self.assertEqual(plan["report_groups"]["primary"]["unit_ids"], faithful_ids)
        self.assertTrue(plan["report_groups"]["original"]["enabled"])
        self.assertEqual(plan["report_groups"]["original"]["unit_ids"], ["original-hybrid"])
        self.assertFalse(plan["report_groups"]["diagnostic"]["enabled"])
        self.assertEqual(plan["report_groups"]["diagnostic"]["unit_ids"], [])

    def test_controlled_inputs_are_only_original_and_recorded_literal_queries(self):
        source = analysis_fixture()
        plan = build_plan(source, QUESTION, include_mode_diagnostics=True)
        diagnostic_ids = plan["report_groups"]["diagnostic"]["unit_ids"]
        controlled = [u for u in plan["units"] if u["unit_id"] in diagnostic_ids]
        self.assertEqual(plan["distinct_query_texts_including_original"], 3)
        self.assertEqual(len(controlled), 9)
        self.assertEqual(sum(u["kind"] == "controlled" for u in plan["units"]), 8)
        self.assertEqual(plan["planned_executions"], 50)
        self.assertEqual({u["query_texts"][0] for u in controlled}, {QUESTION, QUERY, ARRAY_LITERAL})
        for text in (QUESTION, QUERY, ARRAY_LITERAL):
            units = [u for u in controlled if u["query_texts"] == [text]]
            self.assertEqual({u["mode"] for u in units}, {"fts", "vector", "hybrid"})
            self.assertTrue(all(u["request"]["limit"] == 10 for u in units))
            self.assertTrue(all(u["request"]["autoUpdate"] is False and u["request"]["trace"] is True for u in units))
        self.assertEqual(plan["independent_tasks"], 1)
        self.assertEqual(plan["planned_executions"], len(plan["units"]) * 5)
        self.assertEqual(plan["quality_repetition"], 1)
        with self.assertRaises(ValueError):
            build_plan(source, QUESTION, repetitions=4)

    def test_diagnostics_reuse_original_without_changing_primary_units(self):
        source = analysis_fixture()
        default = build_plan(source, QUESTION)
        diagnostic = build_plan(source, QUESTION, include_mode_diagnostics=True)
        main_units = lambda plan: [u for u in plan["units"] if u["kind"] in {"faithful", "original"}]
        self.assertEqual(main_units(default), main_units(diagnostic))
        for group in ("primary", "original"):
            self.assertEqual(default["report_groups"][group], diagnostic["report_groups"][group])
        self.assertTrue(diagnostic["report_groups"]["diagnostic"]["enabled"])
        self.assertEqual(diagnostic["report_groups"]["diagnostic"]["unit_ids"].count("original-hybrid"), 1)
        originals = [u for u in diagnostic["units"] if u["query_texts"] == [QUESTION] and u["mode"] == "hybrid"]
        self.assertEqual(len(originals), 1)
        self.assertEqual(originals[0]["kind"], "original")
        primary_ids = set(diagnostic["report_groups"]["primary"]["unit_ids"])
        diagnostic_ids = set(diagnostic["report_groups"]["diagnostic"]["unit_ids"])
        self.assertTrue(primary_ids.isdisjoint(diagnostic_ids))
        self.assertEqual(len({u["unit_id"] for u in diagnostic["units"]}), len(diagnostic["units"]))

    def test_full_recorded_request_is_preserved_in_both_suites(self):
        request = {**REQUEST, "limit": 20, "depth": 80, "fuse": False, "preferSymbol": True,
                   "routes": [{"mode": "vector", "query": ARRAY_LITERAL,
                               "filter": {"path": "reflex/vars/**", "kind": "function"}}]}
        source = analysis_fixture()
        source["groups"][0]["trials"] = [trial("full", [call("full-call", request)])]
        before = copy.deepcopy(source)
        for diagnostics in (False, True):
            with self.subTest(diagnostics=diagnostics):
                plan = build_plan(source, QUESTION, include_mode_diagnostics=diagnostics)
                faithful = [u for u in plan["units"] if u["kind"] == "faithful"]
                self.assertEqual(len(faithful), 1)
                self.assertEqual(faithful[0]["request"], request)
                occurrence = faithful[0]["occurrences"][0]
                original_call = source["groups"][0]["trials"][0]["first_zg_decision_round"]["zg_calls"][0]
                self.assertEqual(occurrence["raw_arguments"], original_call["raw_arguments"])
                self.assertEqual(occurrence["backend_source"], original_call["backend"]["source"])
                self.assertEqual(occurrence["trial_id"], "full")
                self.assertEqual(occurrence["call_id"], "full-call")
        self.assertEqual(source, before)

    def test_exact_requests_deduplicate_but_frequency_and_omitted_fields_survive(self):
        source = analysis_fixture()
        source["groups"][0]["trials"].append(trial("r3", [call("c3", {**REQUEST, "limit": 10})]))
        before = copy.deepcopy(source)
        plan = build_plan(source, QUESTION)
        faithful = [u for u in plan["units"] if u["kind"] == "faithful"]
        self.assertEqual(len(faithful), 2)
        omitted = next(u for u in faithful if "limit" not in u["request"])
        explicit = next(u for u in faithful if "limit" in u["request"])
        self.assertEqual(len(omitted["occurrences"]), 2)
        self.assertEqual({o["trial_id"] for o in omitted["occurrences"]}, {"r1", "r2"})
        self.assertEqual(len(explicit["occurrences"]), 1)
        self.assertEqual(omitted["query_texts"], [QUERY, ARRAY_LITERAL])
        self.assertNotIn("computed getter", omitted["query_texts"])
        self.assertEqual(source, before)
        self.assertEqual(request_queries({"query": QUERY, "queries": [QUERY], "routes": [{"mode": "vector", "query": ARRAY_LITERAL}]}), [QUERY, ARRAY_LITERAL])

    def test_baseline_excluded_but_no_adoption_unknown_and_ambiguous_treatments_retained(self):
        source = analysis_fixture()
        ambiguous = call("ambiguous")
        ambiguous["backend_link"]["status"] = "ambiguous"
        source["groups"][0]["trials"].extend([
            trial("baseline-empty", profile="baseline", adoption=False, evidence="no_call"),
            trial("baseline-unexpected-zg", [call("baseline-call")], profile="baseline"),
            trial("no-adoption", adoption=False, evidence="no_call"),
            trial("unknown", adoption=None, evidence="not_available"),
            trial("ambiguous", [ambiguous])])
        plan = build_plan(source, QUESTION)
        unavailable = {u["trial_id"]: u for u in plan["unreplayable_planned_trials_or_calls"]}
        self.assertEqual(set(unavailable), {"no-adoption", "unknown", "ambiguous"})
        self.assertIs(unavailable["no-adoption"]["adoption"], False)
        self.assertIsNone(unavailable["unknown"]["adoption"])
        faithful = [u for u in plan["units"] if u["kind"] == "faithful"]
        self.assertEqual(sum(len(u["occurrences"]) for u in faithful), 2)
        self.assertNotIn("baseline-unexpected-zg", {o["trial_id"] for u in faithful for o in u["occurrences"]})


class ReplayEvaluationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.unit = {"unit_id": "one-unit", "kind": "controlled", "mode": "hybrid", "request": copy.deepcopy(REQUEST),
                     "query_texts": [QUERY, ARRAY_LITERAL], "occurrences": [{"trial_id": "r1"}],
                     "quality_unit": "one formulation of one task"}
        self.plan = {"protocol": "readonly-query-retrieval-v4", "repetitions": 5, "planned_executions": 5,
                     "quality_repetition": 1, "units": [self.unit]}
        self.snapshot = {"source": {"sha256": "source-hash", "root": "/app", "git_commit": "a" * 40},
                         "index": {"id": "frozen-index", "documents": {"sha256": "documents-hash"},
                                   "embedding": {"provider": "local", "model": "potion-code-16m-v2", "dimension": 256}}}
        self.output = [self.event(i) for i in range(1, 6)]
        self.directory = self.root / self.unit["unit_id"]

    def event(self, repetition):
        text = f"public result {repetition}"
        return {"event": "search", "unit_id": self.unit["unit_id"], "kind": self.unit["kind"], "mode": self.unit["mode"],
                "origin": "query-aware-retrieval-replay", "repetition": repetition, "repetitions": 5,
                "status": "success", "request": copy.deepcopy(self.unit["request"]), "text": text,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "duration_ms": repetition * 10,
                "source_identity": copy.deepcopy(self.snapshot["source"]),
                "index_identity": copy.deepcopy(self.snapshot["index"])}

    def audit(self, output=None):
        output = self.output if output is None else output
        return [{"event": "integrity", "stage": "start", "unchanged": True, "semantic_unchanged": True, "mismatches": []},
                {"event": "start", "source_identity": copy.deepcopy(self.snapshot["source"]), "index_identity": copy.deepcopy(self.snapshot["index"])},
                *copy.deepcopy(output),
                {"event": "integrity", "stage": "end", "unchanged": True, "semantic_unchanged": True, "mismatches": []},
                {"event": "end", "searches": 5, "integrity": "semantic_unchanged"}]

    def evaluate(self, output=None, audit=None, returncode=0):
        output = self.output if output is None else output
        write_json(self.directory / "status.json", {"returncode": returncode, "status": "completed" if returncode == 0 else "failed"})
        write_events(self.directory / "stdout.jsonl", output)
        write_events(self.directory / "events.jsonl", self.audit(output) if audit is None else audit)
        with patch("zg_bench.swe_qa.retrieval_replay.score_output", return_value={"verified_score": True}) as score:
            result = evaluate_replays(self.plan, self.root, {}, {}, self.snapshot)
        return result, score.call_count

    def assert_unscored_repetition(self, result, number):
        repeats = result["units"][0]["repeats"]
        self.assertEqual([r["repetition"] for r in repeats], [1, 2, 3, 4, 5])
        row = repeats[number - 1]
        self.assertNotEqual(row["status"], "scored")
        self.assertIsNone(row["scores"])
        self.assertTrue(row["error"])
        return row

    def test_valid_five_preserve_first_quality_and_all_outputs(self):
        result, calls = self.evaluate()
        self.assertEqual(calls, 5)
        self.assertEqual(result["scored_executions"], 5)
        unit = result["units"][0]
        self.assertEqual(unit["quality_observation"]["repetition"], 1)
        self.assertEqual(unit["stability"]["known_repeats"], 5)
        self.assertEqual(unit["stability"]["distinct_public_outputs"], 5)
        self.assertFalse(unit["stability"]["all_public_outputs_identical"])

    def test_evaluation_preserves_separate_report_groups(self):
        self.plan["report_groups"] = {
            "primary": {"enabled": True, "unit_ids": []},
            "original": {"enabled": True, "unit_ids": []},
            "diagnostic": {"enabled": True, "unit_ids": [self.unit["unit_id"]]}}
        self.plan["include_mode_diagnostics"] = True
        self.plan["comparison_policy"] = "Separate primary, original, and diagnostic observations"
        result, calls = self.evaluate()
        self.assertEqual(calls, 5)
        self.assertEqual(result["report_groups"], self.plan["report_groups"])
        self.assertIs(result["include_mode_diagnostics"], True)
        self.assertEqual(result["comparison_policy"], self.plan["comparison_policy"])

    def test_bad_request_source_index_or_public_hash_never_reaches_scoring(self):
        mutations = {
            "request": lambda e: e["request"].update(limit=999),
            "source": lambda e: e["source_identity"].update(sha256="changed"),
            "index": lambda e: e["index_identity"]["documents"].update(sha256="changed"),
            "text_hash": lambda e: e.update(text_sha256="forged"),
            "missing_text": lambda e: e.pop("text"),
            "unit_id": lambda e: e.update(unit_id="another-unit"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                output = copy.deepcopy(self.output)
                mutate(output[0])
                result, calls = self.evaluate(output=output)
                self.assert_unscored_repetition(result, 1)
                self.assertEqual(calls, 4)
                self.assertEqual(result["units"][0]["quality_observation"]["repetition"], 1)
                self.assertIsNone(result["units"][0]["quality_observation"]["scores"])

    def test_missing_and_duplicate_repetitions_are_not_replaced(self):
        output = [copy.deepcopy(self.output[i]) for i in (1, 1, 2, 3, 4)]  # no r1, duplicate r2
        result, calls = self.evaluate(output)
        self.assert_unscored_repetition(result, 1)
        self.assert_unscored_repetition(result, 2)
        self.assertEqual(calls, 3)
        self.assertEqual(result["planned_executions"], 5)
        self.assertIsNone(result["units"][0]["stability"]["all_public_outputs_identical"])
        self.assertIsNone(result["units"][0]["quality_observation"]["scores"])

    def test_single_query_error_exit_one_keeps_four_valid_and_first_failure(self):
        output = copy.deepcopy(self.output)
        output[0] = {"event": "search", "unit_id": self.unit["unit_id"], "repetition": 1,
                     "status": "error", "error": {"message": "retrieval failed"}}
        result, calls = self.evaluate(output, returncode=1)
        self.assert_unscored_repetition(result, 1)
        self.assertEqual(calls, 4)
        self.assertEqual(result["scored_executions"], 4)
        self.assertEqual(result["units"][0]["quality_observation"]["repetition"], 1)
        self.assertIsNone(result["units"][0]["quality_observation"]["scores"])

    def test_start_identity_or_end_integrity_failure_disqualifies_whole_unit(self):
        mutations = {
            "source_at_start": lambda audit: audit[1]["source_identity"].update(sha256="different-source"),
            "index_at_start": lambda audit: audit[1]["index_identity"]["documents"].update(sha256="different-index"),
            "start_integrity": lambda audit: audit[0].update(unchanged=False, semantic_unchanged=False, mismatches=["source"]),
            "end_integrity": lambda audit: audit[-2].update(unchanged=False, semantic_unchanged=False, mismatches=["source"]),
            "missing_end": lambda audit: audit.pop(),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                audit = self.audit()
                mutate(audit)
                result, calls = self.evaluate(audit=audit)
                self.assertEqual(calls, 0)
                self.assertEqual(result["scored_executions"], 0)
                for n in range(1, 6):
                    self.assert_unscored_repetition(result, n)

    def test_stdout_audit_search_disagreement_cannot_be_scored(self):
        audit = self.audit()
        audit[2]["text"] = "different valid-looking public result"
        audit[2]["text_sha256"] = hashlib.sha256(audit[2]["text"].encode()).hexdigest()
        result, calls = self.evaluate(audit=audit)
        self.assert_unscored_repetition(result, 1)
        self.assertLessEqual(calls, 4)

    def test_duplicate_audit_search_even_if_only_one_matches_is_ambiguous(self):
        for conflicting in (False, True):
            with self.subTest(conflicting=conflicting):
                audit = self.audit()
                duplicate = copy.deepcopy(audit[2])
                if conflicting:
                    duplicate["text"] = "conflicting duplicate"
                    duplicate["text_sha256"] = hashlib.sha256(duplicate["text"].encode()).hexdigest()
                audit.insert(3, duplicate)
                result, calls = self.evaluate(audit=audit)
                self.assert_unscored_repetition(result, 1)
                self.assertLessEqual(calls, 4)

    def test_faithful_request_score_receives_complete_joint_request_separately(self):
        self.unit["kind"] = "faithful"
        self.output = [self.event(i) for i in range(1, 6)]
        with patch("zg_bench.swe_qa.retrieval_replay.score_request", return_value={"joint_request_score": True}) as scorer:
            result, calls = self.evaluate()
        self.assertEqual(calls, 5)
        self.assertEqual(scorer.call_count, 5)
        self.assertTrue(all(c.args[1] == self.unit["request"] for c in scorer.call_args_list))
        first = result["units"][0]["quality_observation"]
        self.assertEqual(first["request_scores"], {"joint_request_score": True})
        self.assertEqual(first["scores"], {"verified_score": True})

    def test_missing_entire_unit_preserves_all_five_planned_unknowns(self):
        with patch("zg_bench.swe_qa.retrieval_replay.score_output") as score:
            result = evaluate_replays(self.plan, self.root, {}, {}, self.snapshot)
        score.assert_not_called()
        self.assertEqual(result["planned_executions"], 5)
        self.assertEqual(result["scored_executions"], 0)
        for n in range(1, 6):
            self.assert_unscored_repetition(result, n)


class ReplayReportTests(unittest.TestCase):
    def report(self, *, diagnostics):
        plan = build_plan(analysis_fixture(), QUESTION, include_mode_diagnostics=diagnostics)
        report = copy.deepcopy(plan)
        report["scored_executions"] = plan["planned_executions"]
        def score(rank):
            return {"query_relevance": {"classification": "equivalent", "target": {
                        "first_hit_rank": rank, "bytes_through_first_hit": rank * 100}},
                    "task_entry_score": {"levels": {"function": {"first_hit_rank": rank}}}}
        for unit in report["units"]:
            # Deliberately make diagnostic and per-text scores look better than the
            # primary joint-request score: neither may replace the primary result.
            rank = 6 if unit["kind"] == "original" else 1
            unit["quality_observation"] = {
                "repetition": 1,
                "scores": {view: [score(rank)] for view in ("native", "bytes_4096", "bytes_8192")},
                "request_scores": {view: score(9) for view in ("native", "bytes_4096", "bytes_8192")}}
            unit["occurrence_count"] = len(unit["occurrences"])
            unit["stability"] = {"all_public_outputs_identical": True, "known_repeats": 5}
        return report

    def test_three_tables_do_not_promote_diagnostic_scores_into_primary_results(self):
        report = self.report(diagnostics=True)
        sections = replay_markdown(report).split("\n## ")[1:]
        self.assertEqual(len(sections), 3)
        self.assertTrue(sections[0].startswith("主回归：Agent 真实完整请求\n"))
        self.assertTrue(sections[1].startswith("原始问题：固定 hybrid 默认配置\n"))
        self.assertTrue(sections[2].startswith("辅助诊断：FTS / vector / hybrid\n"))
        for section, group in zip(sections, ("primary", "original", "diagnostic")):
            rows = [line for line in section.splitlines() if line.startswith("| ") and not line.startswith("| unit |")]
            ids = [row.split("|")[1].strip() for row in rows]
            self.assertEqual(ids, report["report_groups"][group]["unit_ids"])
        primary_row = next(line for line in sections[0].splitlines() if line.startswith("| faithful-"))
        self.assertEqual(primary_row.split("|")[4].strip(), "9")
        self.assertNotIn("| original-hybrid |", sections[0])
        self.assertIn("| original-hybrid |", sections[1])
        self.assertIn("| original-hybrid |", sections[2])
        self.assertIn("不重新运行", sections[2])
        self.assertEqual(report["planned_executions"], 50)

    def test_disabled_diagnostics_are_reported_as_not_executed_not_a_miss(self):
        report = self.report(diagnostics=False)
        rendered = replay_markdown(report)
        sections = rendered.split("\n## ")[1:]
        self.assertEqual(len(sections), 3)
        self.assertEqual(rendered.count("| unit |"), 2)
        self.assertIn("本次未启用；未执行不等于未命中。", sections[2])
        self.assertNotIn("| unit |", sections[2])
        self.assertIn("不合并总分、不挑最好模式", rendered)

    def test_historical_v4_reports_remain_renderable_without_report_groups(self):
        report = self.report(diagnostics=False)
        report.pop("report_groups")
        report["protocol"] = "readonly-query-retrieval-v4"
        rendered = replay_markdown(report)
        self.assertTrue(rendered.startswith("# Query-conditioned retrieval replay\n"))
        for unit in report["units"]:
            self.assertIn("| " + unit["unit_id"] + " |", rendered)


class OriginalArtifactContractTests(unittest.TestCase):
    def test_cli_diagnostics_are_opt_in_and_pass_the_selected_plan_to_execution(self):
        cases = Path(__file__).resolve().parents[1] / "cases"
        case_path = cases / "reflex-6.json"
        case = json.loads(case_path.read_text())
        for command in ("analyze", "run"):
            for diagnostics in (False, True):
                with self.subTest(command=command, diagnostics=diagnostics), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    combinations = [("opencode", "custom-openai/glm-5.2"),
                                    ("opencode", "custom-openai/qwen3.8-max"), ("qodercli", "qwen3.8-max")]
                    for i, (agent, model) in enumerate(combinations):
                        write_json(root / "recorded" / str(i) / "manifest.json", {
                            "ci_identity": {"GITHUB_RUN_ID": SOURCE_RUN, "GITHUB_SHA": SOURCE_COMMIT},
                            "case_sha256": hashlib.sha256(case_path.read_bytes()).hexdigest(),
                            "repo": case["repo"], "package": "@zvec/zvec-grep@0.2.2", "agent": agent, "model": model})
                    argv = [command, "--recorded-runs", str(root / "recorded"), "--case", str(case_path),
                            "--entries", str(cases / "reflex-6.entries.json"), "--labels", str(cases / "reflex-6.query-intents.json"),
                            "--output", str(root / "analysis")]
                    if diagnostics:
                        argv.append("--include-mode-diagnostics")
                    source = {**analysis_fixture(), "input_artifacts": []}
                    def completed(plan, *_):
                        return {"planned_executions": plan["planned_executions"],
                                "scored_executions": plan["planned_executions"]}
                    with patch("zg_bench.swe_qa.retrieval_replay.analyze", return_value=source), \
                            patch("zg_bench.swe_qa.retrieval_replay.write_report"), \
                            patch("zg_bench.swe_qa.retrieval_replay.observed_scores", return_value=[]), \
                            patch("zg_bench.swe_qa.retrieval_replay.build_plan", wraps=build_plan) as planner, \
                            patch("zg_bench.swe_qa.retrieval_replay.execute", side_effect=completed) as execution:
                        self.assertEqual(main(argv), 0)
                    planner.assert_called_once_with(source, case["question"], include_mode_diagnostics=diagnostics)
                    plan = json.loads((root / "analysis" / "replay-plan.json").read_text())
                    self.assertEqual(plan["planned_executions"], 50 if diagnostics else 10)
                    self.assertEqual(plan["report_groups"]["diagnostic"]["enabled"], diagnostics)
                    if command == "run":
                        execution.assert_called_once()
                        self.assertEqual(execution.call_args.args[0], plan)
                        self.assertIs(execution.call_args.args[1].include_mode_diagnostics, diagnostics)
                    else:
                        execution.assert_not_called()

    def test_native_qoder_model_identity_is_not_an_opencode_provider_identifier(self):
        cases = Path(__file__).resolve().parents[1] / "cases"
        case_path = cases / "reflex-6.json"
        case = json.loads(case_path.read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            combinations = [("opencode", "custom-openai/glm-5.2"),
                            ("opencode", "custom-openai/qwen3.8-max"), ("qodercli", "qwen3.8-max")]
            for i, (agent, model) in enumerate(combinations):
                write_json(root / "recorded" / str(i) / "manifest.json", {
                    "ci_identity": {"GITHUB_RUN_ID": SOURCE_RUN, "GITHUB_SHA": SOURCE_COMMIT},
                    "case_sha256": hashlib.sha256(case_path.read_bytes()).hexdigest(),
                    "repo": case["repo"], "package": "@zvec/zvec-grep@0.2.2", "agent": agent, "model": model})
            argv = ["analyze", "--recorded-runs", str(root / "recorded"), "--case", str(case_path),
                    "--entries", str(cases / "reflex-6.entries.json"), "--labels", str(cases / "reflex-6.query-intents.json"),
                    "--output", str(root / "analysis")]
            with patch("zg_bench.swe_qa.retrieval_replay.analyze", return_value={"groups": [], "query_catalog": [], "input_artifacts": []}), \
                    patch("zg_bench.swe_qa.retrieval_replay.write_report"), \
                    patch("zg_bench.swe_qa.retrieval_replay.execute") as execution:
                self.assertEqual(main(argv), 0)
                execution.assert_not_called()
                # A guessed provider-prefixed identity must not silently match.
                path = root / "recorded" / "2" / "manifest.json"
                manifest = json.loads(path.read_text()); manifest["model"] = "custom-openai/qwen3.8-max"
                write_json(path, manifest)
                argv[-1] = str(root / "rejected")
                with self.assertRaisesRegex(ValueError, "three original agent/model"):
                    main(argv)


if __name__ == "__main__":
    unittest.main()
