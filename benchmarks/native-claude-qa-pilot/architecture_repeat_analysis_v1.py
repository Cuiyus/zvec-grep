"""Use the existing frozen repeat statistics for the new q004 cohort only."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('_q004_repeat_statistics',
                                             Path(__file__).with_name('repeat_analysis_v1.py'))
implementation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(implementation)
implementation.EXPECTED_TASK_IDS = ('q004',)


def analyze(cases, **kwargs):
    if [case['task_id'] for case in cases] != ['q004']:
        raise implementation.SweQaError('this cohort requires q004 only')
    report = implementation.analyze(cases, **kwargs)
    report['stable_reported_input_with_quality_preserved_on_q004'] = report.pop(
        'stable_reported_input_with_quality_preserved_on_all_existing_tasks')
    report['scope'] = ('Observed repeatability on the fixed zvec q004 question and pinned stack; '
                       'development observations are excluded; no cross-task or population inference.')
    return report
