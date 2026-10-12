"""Synthetic CI integration fixture: no retrieval, inference or judging occurs."""
import hashlib
import json
import os
import sys
from pathlib import Path
from swe_qa_fixtures import EXPECTED_TASK_IDS, _judged_task_report, _set_report_trial_judgements, _write_json
from zg_bench.ci.task_reports import run_identity, stamp_report, select_reports
from zg_bench.reports.aggregate import aggregate_reports

root = Path(os.environ["RUNNER_TEMP"])
identity = run_identity({
    **os.environ,
    "BENCH_CANDIDATE_COMMIT": os.environ["GITHUB_SHA"],
    "BENCH_CANDIDATE_REPOSITORY": os.environ["GITHUB_REPOSITORY"],
    "BENCH_HARNESS_COMMIT": os.environ["GITHUB_SHA"],
    "BENCH_PROTOCOL_HASH": hashlib.sha256(Path(".github/workflows/swe-qa-bench.yml").read_bytes()).hexdigest(),
    "BENCH_SCOPE": "all-full", "SWE_QA_MODEL": "glm-5.2",
    "BENCH_EMBEDDING_RUNTIME": "local",
    "BENCH_EMBEDDING_MODEL": "local/potion-code-16m-v2",
})
if sys.argv[1] == "seed":
    group = os.environ["FIXTURE_GROUP"]
    if group == "last" and identity["run_attempt"] == 1:
        print("Intentional fixture failure: leave the twentieth task for a failed-job rerun.")
        sys.exit(1)
    tasks = EXPECTED_TASK_IDS[:-1] if group == "first" else EXPECTED_TASK_IDS[-1:]
    for index, task in enumerate(tasks):
        directory = root / "fixture-reports" / str(index)
        report = _judged_task_report(task)
        _set_report_trial_judgements(report, baseline=[75] * 5, zvec=[80] * 5)
        _write_json(directory / "report.json", report)
        stamp_report(directory, identity)
else:
    selection = select_reports(reports_root=root / "task-reports", output_dir=root / "selected", identity=identity, expected=EXPECTED_TASK_IDS)
    report = aggregate_reports(reports_root=root / "selected", output_dir=root / "aggregate", expected=EXPECTED_TASK_IDS, allow_missing=True)
    _write_json(root / "aggregate" / "task-report-selection.json", selection)
    print(json.dumps({"fixture_only": True, "completed": len(report["cases"]), "missing": report["gate"]["missing_tasks"], "attempts": [r["attempt"] for r in selection["selected"]]}))
    if identity["run_attempt"] == 1:
        assert len(report["cases"]) == 19 and report["gate"]["passed"] is False
        print("Intentional incomplete fixture aggregate; rerun failed jobs to verify 19 + 1 reuse.")
        sys.exit(1)
    assert report["gate"]["passed"] and report["gate"]["valid_pairs"] == 20
    assert report["gate"]["successful_judgements"] == 200
    assert [r["attempt"] for r in selection["selected"]] == [1] * 19 + [2]
    print("PASS: 19 earlier successes + 1 retried task = complete report, without model calls.")
