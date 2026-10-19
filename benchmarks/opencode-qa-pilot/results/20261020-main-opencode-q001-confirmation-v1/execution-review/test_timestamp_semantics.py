"""Synthetic timing fixtures only; never loads any confirmation evidence."""
import copy
from datetime import datetime, timezone
import unittest

import audit_confirmation_execution as audit


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def step(first, last, name='Run fixture'):
    return {'number': 1, 'name': name, 'status': 'completed', 'conclusion': 'success',
            'started_at': iso(first), 'completed_at': iso(last)}


def timing(first, last):
    return {'slot_started_at_unix': first, 'slot_finished_at_unix': last,
            'native_trial_started_at': iso(first + 1), 'native_trial_finished_at': iso(last - 1)}


class TimestampSemantics(unittest.TestCase):
    def setUp(self):
        self.jobs = {
            'c01': {'id': 1, 'status': 'completed', 'started_at': iso(90), 'completed_at': iso(200),
                    'steps': [step(100, 105, 'Set up job'), step(105, 195), step(195, 200, 'Complete job')]},
            'c02': {'id': 2, 'status': 'completed', 'started_at': iso(80), 'completed_at': iso(300),
                    'steps': [step(200, 205, 'Set up job'), step(205, 295), step(295, 300, 'Complete job')]},
        }
        self.reports = {
            'c01': {'timing': {'baseline': timing(110, 140), 'zvec-grep': timing(145, 180)}},
            'c02': {'timing': {'baseline': timing(210, 240), 'zvec-grep': timing(245, 280)}},
        }

    def test_queued_placeholder_does_not_create_false_overlap(self):
        value = audit.serial_execution_evidence(self.jobs, self.reports)
        self.assertTrue(value['confirmed'])
        self.assertEqual(value['step_intervals']['c02']['job_started_at_as_reported'], iso(80))
        self.assertEqual(value['step_intervals']['c02']['started_at_unix'], 200)
        changed = copy.deepcopy(self.jobs)
        changed['c02']['started_at'] = iso(10)
        self.assertTrue(audit.serial_execution_evidence(changed, self.reports)['confirmed'])

    def test_real_step_overlap_still_fails_despite_serial_native_slots(self):
        self.jobs['c02']['steps'][0]['started_at'] = iso(199)
        value = audit.serial_execution_evidence(self.jobs, self.reports)
        self.assertFalse(value['confirmed'])
        self.assertFalse(value['step_execution_nonoverlap'])
        self.assertTrue(value['native_trials_nonoverlap'])

    def test_native_and_outer_overlap_still_fails_despite_serial_steps(self):
        self.reports['c02']['timing']['baseline'] = timing(170, 190)
        value = audit.serial_execution_evidence(self.jobs, self.reports)
        self.assertFalse(value['confirmed'])
        self.assertTrue(value['step_execution_nonoverlap'])
        self.assertFalse(value['outer_slots_nonoverlap'])
        self.assertFalse(value['native_trials_nonoverlap'])

    def test_missing_executed_step_timestamp_is_unconfirmed(self):
        del self.jobs['c02']['steps'][1]['completed_at']
        value = audit.serial_execution_evidence(self.jobs, self.reports)
        self.assertFalse(value['confirmed'])
        self.assertFalse(value['step_intervals_confirmed'])
        self.assertTrue(value['step_intervals']['c02']['unconfirmed'])

    def test_missing_native_timestamp_is_unconfirmed(self):
        del self.reports['c02']['timing']['baseline']['native_trial_started_at']
        value = audit.serial_execution_evidence(self.jobs, self.reports)
        self.assertFalse(value['confirmed'])
        self.assertTrue(value['unconfirmed_slot_intervals'])

    def test_end_boundary_uses_last_executed_step_not_job_summary(self):
        self.jobs['c01']['steps'][-1]['completed_at'] = iso(201)
        value = audit.serial_execution_evidence(self.jobs, self.reports)
        self.assertFalse(value['confirmed'])
        self.assertEqual(value['step_intervals']['c01']['finished_at_unix'], 201)
        self.assertEqual(value['overlap_tolerance_seconds'], 0)


if __name__ == '__main__':
    unittest.main()
