from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zg_bench.reports.retries import render_retry_summary


class RetrySummaryTests(unittest.TestCase):
    def test_renders_both_profiles_and_missing_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline = root / "job-baseline"
            baseline.mkdir()
            (baseline / "result.json").write_text(
                json.dumps(
                    {
                        "n_total_trials": 5,
                        "stats": {
                            "n_completed_trials": 5,
                            "n_errored_trials": 1,
                            "n_retries": 2,
                        },
                    }
                )
            )

            markdown = render_retry_summary(runs_dir=root, trials=5, retries=2)

        self.assertIn("| baseline | 4/5 | 2 | 1 |", markdown)
        self.assertIn(
            "| zvec-grep | unavailable | unavailable | unavailable |", markdown
        )

    def test_invalid_result_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_dir = root / "job-baseline"
            result_dir.mkdir()
            (result_dir / "result.json").write_text("not json")
            with self.assertRaisesRegex(ValueError, "could not read retry data"):
                render_retry_summary(runs_dir=root, trials=5, retries=2)
