"""Protect the new cohort boundary, fixed quality contract and native execution."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import architecture_repeat_v1 as driver
import expansion_live_v1 as original


class ArchitectureRepeatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = json.loads((driver.HERE / 'architecture-repeat-plan-v1.json').read_text())
        self.reference = json.loads((driver.REPO / driver.REFERENCE).read_text())
        files = set(self.plan['frozen_harness_sha256']) | {driver.native.CONTRACT}
        for relative in files:
            dest = self.root / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(driver.REPO / relative, dest)
        self.args = SimpleNamespace(plan=self.root / 'plan.json', repetition=1,
            main_root=self.root / 'main', reference_prepared=self.root / 'reference',
            prepared_group=self.root / 'group', candidate=self.root / 'candidate.tgz')

    def validate(self):
        self.args.plan.write_text(json.dumps(self.plan))
        with patch.object(driver.native, 'validate_plan',
                          return_value=(self.reference, {'runtime': 'original'}, {'proof': 'original'})):
            return driver.validate_plan(self.args, self.root)

    def test_exact_new_cohort_and_original_runtime(self):
        plan, base, proof = self.validate()
        self.assertEqual(plan['scheduled_solver_trials'], 20)
        self.assertEqual((base, proof), ({'runtime': 'original'}, {'proof': 'original'}))
        pairs = driver.schedule()
        self.assertEqual({p['trial_index'] for p in pairs}, set(range(1, 11)))
        self.assertEqual(len({p['key'] for p in pairs}), 10)
        self.assertEqual(sum(p['profiles'][0] == 'baseline' for p in pairs), 5)
        self.assertEqual({p['task_id'] for p in pairs}, {'q004'})

    def test_outside_cohort_or_boolean_repetition_rejected(self):
        for repetition in (0, 11, True, '1'):
            with self.subTest(repetition=repetition), self.assertRaises(ValueError):
                driver.selected_pair(repetition)

    def test_retry_count_budget_and_schedule_overrides_rejected(self):
        for key, value in [('scheduled_solver_trials', 60), ('max_retries', 1),
                           ('n_attempts_per_pair', True), ('max_parallel_pairs', 20),
                           ('max_budget_usd', 999), ('schedule', driver.schedule()[:-1])]:
            with self.subTest(key=key):
                before = copy.deepcopy(self.plan)
                self.plan[key] = value
                with self.assertRaisesRegex(ValueError, 'cohort_mismatch'):
                    self.validate()
                self.plan = before

    def test_changed_workflow_bytes_rejected(self):
        path = self.root / driver.WORKFLOW
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'control_changed'):
            self.validate()

    def test_other_task_source_or_native_reference_rejected(self):
        self.plan['dataset']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'original_task_or_runtime'):
            self.validate()

    def test_non_candidate_development_observation_blocks_execution(self):
        ref = self.plan['prerequisites']['development_conclusion']
        path = self.root / ref['path']
        value = json.loads(path.read_text())
        value['input_efficiency_candidate'] = False
        path.write_text(json.dumps(value))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        ref['sha256'] = self.plan['frozen_harness_sha256'][ref['path']] = digest
        with self.assertRaisesRegex(ValueError, 'prerequisite_not_met'):
            self.validate()

    def test_private_adapter_keeps_native_bodies_and_global_state(self):
        before = (original.PROTOCOL, copy.deepcopy(original.TASKS), original.context_path)
        for repetition in (1, 2):
            adapted = driver.execution_implementation(repetition)
            expected = driver.selected_pair(repetition)['profiles']
            self.assertEqual(adapted.task_profiles('q004'), expected)
            self.assertEqual(adapted.execute.__code__.co_code, original.execute.__code__.co_code)
            self.assertEqual(adapted.private_live('q004').PROFILES, expected)
            self.assertEqual(adapted.context_path(Path('/tmp/live')),
                             Path('/tmp/live-q004-repeat-context.json'))
        self.assertEqual((original.PROTOCOL, original.TASKS, original.context_path), before)


if __name__ == '__main__':
    unittest.main()
