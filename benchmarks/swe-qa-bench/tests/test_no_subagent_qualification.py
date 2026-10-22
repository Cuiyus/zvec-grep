"""Recovery may retain final answers, never erase unknown failed-attempt costs."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_no_subagent_analysis import attempt, clean, report, write
from zg_bench.no_subagent_audit import audit
from zg_bench.no_subagent_qualification import MISSING_ARTIFACTS, qualify, verify_report_qualification
from zg_bench.no_subagent_summary import summarize


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def save_audit(self):
        try:
            audit(self.root, 4)
        except ValueError:
            pass
        return json.loads((self.root / 'no-subagent-audit.json').read_text())

    def missing_attempt(self, arm='baseline_allowed', *, retry=True, failed=True, missing=MISSING_ARTIFACTS):
        clean(self.root)
        path, _ = attempt(self.root, arm, retry=retry, failed=failed)
        for name in missing:
            (path / name).unlink()
        self.save_audit()
        return path

    def test_allowed_archived_failure_qualifies_without_mutating_false_audit(self):
        self.missing_attempt()
        before = (self.root / 'no-subagent-audit.json').read_bytes()
        result = qualify(self.root, expected_slots=4)
        self.assertTrue(result['eligible'], result['errors'])
        self.assertFalse(result['full_attempt_audit_complete'])
        self.assertEqual(len(result['allowed_unknown_attempts']), 1)
        self.assertTrue(result['limitations'])
        self.assertEqual(before, (self.root / 'no-subagent-audit.json').read_bytes())

    def test_denied_failure_missing_evidence_is_rejected(self):
        self.missing_attempt('node')
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_successful_final_missing_evidence_is_rejected(self):
        self.missing_attempt(retry=False, failed=False)
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_allowed_unarchived_failure_is_rejected(self):
        self.missing_attempt(retry=False, failed=True)
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_allowed_archived_nonfailure_is_rejected(self):
        path = self.missing_attempt()
        result = json.loads((path / 'result.json').read_text())
        result['exception_info'] = None
        write(path / 'result.json', result)
        self.save_audit()
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_missing_config_or_damaged_history_cannot_qualify(self):
        for damaged in ('config', 'history'):
            with self.subTest(damaged=damaged):
                # Fresh isolated fixture each time.
                self.root = Path(self.temp.name) / damaged
                path = self.missing_attempt()
                if damaged == 'config':
                    (path / 'config.json').unlink()
                else:
                    history = next(self.root.rglob('history.json'))
                    history.write_text('{')
                self.save_audit()
                self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_corrupt_existing_artifacts_are_not_equivalent_to_absence(self):
        path = self.missing_attempt()
        (path / MISSING_ARTIFACTS[0]).write_text('{')
        self.save_audit()
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_partial_missing_set_is_not_the_approved_exception(self):
        self.missing_attempt(missing=MISSING_ARTIFACTS[:1])
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_whole_failure_archive_missing_is_rejected(self):
        import shutil
        path = self.missing_attempt()
        shutil.rmtree(path)
        self.save_audit()
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_failed_archive_history_exception_must_match_result(self):
        path = self.missing_attempt()
        history_path = path.parent.parent / 'history.json'
        history = json.loads(history_path.read_text())
        next(iter(history['trials'].values()))[0]['exception_type'] = 'DifferentFailure'
        write(history_path, history)
        self.save_audit()
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_source_change_after_audit_is_rejected(self):
        path = self.missing_attempt()
        config = json.loads((path / 'config.json').read_text())
        config['agent']['kwargs']['opencode_config']['permission']['task'] = 'deny'
        write(path / 'config.json', config)
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_summary_accepts_qualified_finals_but_preserves_unknown_and_false_audit(self):
        value = report(self.root)
        baseline = summarize([value], self.root / 'before', expected_tasks=1, repetitions=1)
        path, _ = attempt(self.root, 'baseline_allowed', retry=True, failed=True)
        for name in MISSING_ARTIFACTS:
            (path / name).unlink()
        evidence = self.save_audit()
        decision = qualify(self.root, expected_slots=4, audit=evidence)
        self.assertTrue(decision['eligible'], decision['errors'])
        value['provenance']['no_subagent_audit'] = evidence
        value['provenance']['final_answer_qualification'] = decision
        verify_report_qualification(value)
        result = summarize([value], self.root / 'after', expected_tasks=1, repetitions=1)
        self.assertTrue(result['complete'])
        self.assertFalse(result['full_attempt_audit_complete'])
        self.assertEqual(result['rows'], baseline['rows'])
        self.assertEqual(result['comparisons'], baseline['comparisons'])
        self.assertIsNone(result['per_arm_audit']['baseline_allowed']['task_tool_calls'])
        self.assertFalse(value['provenance']['no_subagent_audit']['complete'])
        self.assertTrue(result['limitations'])
        value['provenance']['no_subagent_audit']['failed_attempts'] = 999
        with self.assertRaises(ValueError):
            verify_report_qualification(value)

    def test_clean_reports_remain_compatible_without_qualification(self):
        value = report(self.root)
        self.assertNotIn('final_answer_qualification', value['provenance'])
        result = summarize([value], self.root / 'summary', expected_tasks=1, repetitions=1)
        self.assertTrue(result['complete'])
        self.assertTrue(result['full_attempt_audit_complete'])
        self.assertEqual(result['limitations'], [])
        self.assertTrue(verify_report_qualification(value)['eligible'])


if __name__ == '__main__':
    unittest.main()
