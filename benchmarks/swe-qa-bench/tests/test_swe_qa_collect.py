"""Harbor evidence collection contracts."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from swe_qa_fixtures import (
    _harbor_trial,
    _write_harbor_job,
    _write_json,
)
from swe_qa_fixtures import (
    setUpModule as setUpModule,
)
from swe_qa_fixtures import (
    tearDownModule as tearDownModule,
)
from zg_bench.swe_qa import SweQaError
from zg_bench.swe_qa.collect import collect_pair


class CollectTests(unittest.TestCase):
    def test_collects_three_sorted_trials_from_each_harbor_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_harbor_job(
                root,
                profile="baseline",
                trials=[
                    _harbor_trial(
                        "reflex-6-baseline-c",
                        answer="baseline c",
                        input_tokens=300,
                        output_tokens=30,
                        tool_calls=9,
                        agent_wall_seconds=30,
                        cost_usd=0.09,
                    ),
                    _harbor_trial(
                        "reflex-6-baseline-a",
                        answer="baseline a",
                        input_tokens=100,
                        output_tokens=10,
                        tool_calls=3,
                        agent_wall_seconds=10,
                        cost_usd=0.03,
                    ),
                    _harbor_trial(
                        "reflex-6-baseline-b",
                        answer="baseline b",
                        input_tokens=200,
                        output_tokens=20,
                        tool_calls=6,
                        agent_wall_seconds=20,
                        cost_usd=0.06,
                    ),
                ],
            )
            _write_harbor_job(
                root,
                profile="zvec-grep",
                trials=[
                    _harbor_trial(
                        f"reflex-6-zvec-{suffix}",
                        answer=f"zvec {suffix}",
                        input_tokens=70 + index,
                        output_tokens=15 + index,
                        tool_calls=1 + index,
                        agent_wall_seconds=10 + index,
                        cost_usd=None,
                    )
                    for index, suffix in enumerate(("c", "a", "b"))
                ],
            )
            output = root / "pairs" / "reflex-6" / "pair.json"

            pair = collect_pair(
                runs_dir=root,
                task="reflex:6",
                output=output,
                expected_trials=3,
            )

            self.assertTrue(pair["valid"])
            self.assertEqual(pair["schema_version"], 2)
            self.assertEqual(pair["task_id"], "reflex:6")
            self.assertEqual(pair["expected_trials"], 3)
            self.assertEqual(pair["actual_trials"], 3)
            baseline = pair["profiles"]["baseline"]
            self.assertEqual(baseline["trial_count"], 3)
            self.assertEqual(
                [trial["trial_name"] for trial in baseline["trials"]],
                [
                    "reflex-6-baseline-c",
                    "reflex-6-baseline-a",
                    "reflex-6-baseline-b",
                ],
            )
            self.assertEqual(
                [trial["trial_index"] for trial in baseline["trials"]],
                [1, 2, 3],
            )
            self.assertEqual(baseline["trials"][1]["input_tokens"], 100)
            self.assertEqual(baseline["trials"][1]["tool_calls"], 3)
            self.assertEqual(baseline["trials"][1]["agent_wall_seconds"], 10.0)
            self.assertTrue(
                all(
                    trial["cost_usd"] is None
                    for trial in pair["profiles"]["zvec-grep"]["trials"]
                )
            )
            self.assertEqual(json.loads(output.read_text()), pair)

    def test_retry_history_does_not_add_trials_or_hide_final_errors(self) -> None:
        for exhausted in (False, True):
            with (
                self.subTest(exhausted=exhausted),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                for profile in ("baseline", "zvec-grep"):
                    _write_harbor_job(
                        root,
                        profile=profile,
                        trials=[
                            _harbor_trial(
                                f"reflex-6-{profile}-{index}",
                                answer="terminal answer",
                                input_tokens=100,
                                output_tokens=10,
                                tool_calls=2,
                                agent_wall_seconds=5,
                                cost_usd=None,
                            )
                            for index in range(1, 6)
                        ],
                    )
                    job_dir = root / f"fixture-reflex-6-{profile}"
                    trial_dir = job_dir / f"reflex-6-{profile}-1"
                    failure = json.loads((trial_dir / "result.json").read_text())
                    failure["exception_info"] = {"exception_type": "AgentTimeoutError"}
                    failure["agent_result"]["n_input_tokens"] = 1000
                    for attempt in (1, 2):
                        archive = (
                            job_dir
                            / ".retry-history"
                            / trial_dir.name
                            / f"attempt-{attempt}"
                        )
                        shutil.copytree(trial_dir, archive)
                        _write_json(archive / "result.json", failure)

                    has_final_error = exhausted and profile == "baseline"
                    if has_final_error:
                        _write_json(trial_dir / "result.json", failure)
                    job_result = json.loads((job_dir / "result.json").read_text())
                    job_result["stats"].update(
                        n_retries=2, n_errored_trials=int(has_final_error)
                    )
                    _write_json(job_dir / "result.json", job_result)

                output = root / "pair.json"
                if exhausted:
                    with self.assertRaisesRegex(SweQaError, "errored trials"):
                        collect_pair(
                            runs_dir=root,
                            task="reflex:6",
                            output=output,
                            expected_trials=5,
                        )
                    self.assertFalse(output.exists())
                else:
                    pair = collect_pair(
                        runs_dir=root,
                        task="reflex:6",
                        output=output,
                        expected_trials=5,
                    )
                    for profile in pair["profiles"].values():
                        self.assertEqual(profile["trial_count"], 5)
                        self.assertEqual(
                            [trial["input_tokens"] for trial in profile["trials"]],
                            [100] * 5,
                        )

    def test_empty_final_answer_fails_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for profile in ("baseline", "zvec-grep"):
                _write_harbor_job(
                    root,
                    profile=profile,
                    trials=[
                        _harbor_trial(
                            f"reflex-6-{profile}-{index}",
                            answer=(
                                ""
                                if profile == "zvec-grep" and index == 2
                                else "answer"
                            ),
                            input_tokens=10,
                            output_tokens=2,
                            tool_calls=0,
                            agent_wall_seconds=10,
                            cost_usd=None,
                        )
                        for index in range(1, 4)
                    ],
                )

            with self.assertRaisesRegex(SweQaError, "empty final answer"):
                collect_pair(
                    runs_dir=root,
                    task="reflex:6",
                    output=root / "pair.json",
                    expected_trials=3,
                )

    def test_collect_rejects_wrong_trial_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for profile in ("baseline", "zvec-grep"):
                _write_harbor_job(
                    root,
                    profile=profile,
                    trials=[
                        _harbor_trial(
                            f"reflex-6-{profile}-{index}",
                            answer="answer",
                            input_tokens=10,
                            output_tokens=2,
                            tool_calls=0,
                            agent_wall_seconds=10,
                            cost_usd=None,
                        )
                        for index in range(1, 3)
                    ],
                )

            with self.assertRaisesRegex(SweQaError, "expected exactly 3"):
                collect_pair(
                    runs_dir=root,
                    task="reflex:6",
                    output=root / "pair.json",
                    expected_trials=3,
                )


if __name__ == "__main__":
    unittest.main()
