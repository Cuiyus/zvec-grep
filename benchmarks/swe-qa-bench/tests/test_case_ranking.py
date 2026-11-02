import json
from pathlib import Path
import tempfile
import unittest

from zg_bench.case_ranking import analyze


class CaseRankingTests(unittest.TestCase):
    def test_non_pylint_candidates_and_each_query_group_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "diagnostic").mkdir()
            stages = []
            for path in ("reflex/state.py", "tests/test_state.py"):
                stages.extend([
                    {"stage": "plan", "plan": "query group " + path},
                    {"stage": "ranking_continuation", "requested_limit": 1, "candidates": [{
                        "entity_id": path, "path": path, "range": {"text": {}},
                        "metadata": "a source symbol", "rank": 1,
                        "trace": {"recall": [], "fusion": {"rank": 1, "score": 0.5},
                                  "ranking": {"static_score": 0.5, "symbol_bonus": 0,
                                              "definition_bonus": 0, "path_bonus": 0, "path_factor": 1}},
                    }]},
                ])
            content = "".join(json.dumps(s) + "\n" for s in stages).encode()
            (root / "diagnostic/stages.jsonl").write_bytes(content)
            (root / "summary.json").write_text(json.dumps({"queries": [
                {"query_id": "q", "stage_byte_range": [0, len(content)]}]}))
            result = analyze(root)
            self.assertEqual([p["group_index"] for p in result["plans"]], [1, 2])
            self.assertEqual([p["targets"][0]["path"] for p in result["plans"]],
                             ["reflex/state.py", "tests/test_state.py"])
            self.assertTrue(all(p["offline_rank_parity"] for p in result["plans"]))
            self.assertTrue(all(p["targets"][0]["returned_by_plan_limit"] for p in result["plans"]))
            self.assertIn("independent per-case source review", result["target_scope"])
