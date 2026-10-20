"""Apply the frozen repeat statistics to q001's ten pairs only."""
import importlib.util
from pathlib import Path

# A private module keeps the original three-task analyzer and its callers intact.
spec = importlib.util.spec_from_file_location("_q001_repeat_statistics", Path(__file__).with_name("repeat_analysis_v1.py"))
implementation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(implementation)
implementation.EXPECTED_TASK_IDS = ("q001",)


def analyze(cases, **kwargs):
    if [case["task_id"] for case in cases] != ["q001"]:
        raise implementation.SweQaError("this cohort requires q001 only")
    report = implementation.analyze(cases, **kwargs)
    report["stable_reported_input_with_quality_preserved_on_q001"] = report.pop(
        "stable_reported_input_with_quality_preserved_on_all_existing_tasks")
    report["scope"] = "Observed repeatability on the single fixed zvec q001 case and pinned stack; no cross-task or population inference."
    return report
