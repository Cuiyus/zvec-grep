"""Protect q005's two executions, native bodies, and original QA contract."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runtime_live_v1 as driver


class RuntimeLiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = json.loads((driver.HERE / 'runtime-live-plan-v1.json').read_text())
        for relative in set(self.plan['frozen_harness_sha256']) | {driver.BASE_PLAN}:
            dest = self.root / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(driver.REPO / relative, dest)
        contract = json.loads((driver.REPO / driver.CONTRACT).read_text())
        group = self.root / 'group'
        (group / 'q005').mkdir(parents=True)
        metadata = {k: {'path': contract[k]['path'].removeprefix('benchmarks/zvec-issue-qa/'),
                        'sha256': contract[k]['sha256']} for k in ('question', 'rubric')}
        metadata.update({k: contract[k] for k in ('source_commit', 'source_inventory_sha256')})
        (group / 'q005/prepared.json').write_text(json.dumps(metadata))
        self.args = SimpleNamespace(plan=self.root / 'plan.json', main_root=self.root / 'main',
            reference_prepared=self.root / 'reference', prepared_group=group,
            candidate=self.root / 'candidate.tgz', output=self.root / 'live', profile='baseline')

    def validate(self):
        self.args.plan.write_text(json.dumps(self.plan))
        with patch.object(driver.live, 'validate_plan', return_value=({'runtime': 'original'}, {'proof': 'original'})), \
             patch.object(driver.preparation, 'validate'), \
             patch.object(driver, 'prepared_reference', return_value=self.plan['prepared_group']):
            return driver.validate_plan(self.args, self.root)

    def test_original_runtime_two_profile_plan(self):
        plan, base, proof = self.validate()
        self.assertEqual(plan['scheduled_solver_trials'], 2)
        self.assertEqual(plan['profiles'], ['baseline', 'zvec-grep'])
        self.assertEqual((base, proof), ({'runtime': 'original'}, {'proof': 'original'}))

    def test_other_task_order_mode_count_retry_or_budget_rejected(self):
        for key, value in [('task_id', 'q004'), ('profiles', ['zvec-grep', 'baseline']),
                           ('mode', 'serial'), ('scheduled_solver_trials', 20),
                           ('n_attempts_per_profile', True), ('max_retries', 1),
                           ('max_budget_usd', 999)]:
            with self.subTest(key=key):
                before = copy.deepcopy(self.plan)
                self.plan[key] = value
                with self.assertRaisesRegex(ValueError, 'frozen_pair_or_mode'):
                    self.validate()
                self.plan = before

    def test_original_source_runtime_or_contract_changes_rejected(self):
        for relative in (driver.WORKFLOW, driver.CONTRACT):
            with self.subTest(relative=relative):
                path = self.root / relative
                old = path.read_bytes()
                path.write_bytes(old + b' ')
                with self.assertRaisesRegex(ValueError, 'control_changed'):
                    self.validate()
                path.write_bytes(old)
        self.plan['base_plan']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'runtime_reference_changed'):
            self.validate()

    def test_missing_source_readiness_prerequisite_rejected(self):
        del self.plan['prerequisites']['grading_readiness']
        with self.assertRaisesRegex(ValueError, 'prerequisite_set_changed'):
            self.validate()

    def test_native_execution_collection_bodies_and_global_state_unchanged(self):
        before = (driver.live.PROTOCOL, copy.deepcopy(driver.live.PROFILES))
        for profiles in [('baseline',), ('zvec-grep',), driver.PROFILES]:
            native = driver.native_implementation(profiles)
            self.assertEqual(native.PROFILES, list(profiles))
            self.assertEqual(native.run_pair.__code__.co_code, driver.live.run_pair.__code__.co_code)
            self.assertEqual(native.collect.__code__.co_code, driver.live.collect.__code__.co_code)
        self.assertEqual((driver.live.PROTOCOL, driver.live.PROFILES), before)
        with self.assertRaisesRegex(ValueError, 'profile_set_invalid'):
            driver.native_implementation(['baseline', 'baseline'])

    def test_preview_selects_only_one_original_profile_and_records_context(self):
        self.args.plan.write_text(json.dumps(self.plan))
        native = SimpleNamespace(run_pair=lambda args, base, dry_run: {
            'slots': [{'profile': 'baseline', 'status': 'dry_run'}]})
        with patch.object(driver, 'native_implementation', return_value=native) as implementation:
            result = driver.worker(self.args, self.plan, {'runtime': 'original'}, dry_run=True)
        self.assertEqual(result, 0)
        implementation.assert_called_once_with(['baseline'])
        context = json.loads((self.root / 'live-q005-worker-context.json').read_text())
        self.assertEqual(context['profile'], 'baseline')
        self.assertTrue(context['dry_run'])
        self.assertEqual(context['task_id'], 'q005')
        self.assertEqual(context['prepared_group'], self.plan['prepared_group'])

    def test_live_execution_blocks_without_terminal_prior_cohort(self):
        with patch.object(driver, 'native_implementation') as implementation:
            with self.assertRaisesRegex(ValueError, 'missing_or_unsafe_native_record'):
                driver.worker(self.args, self.plan, {'runtime': 'original'}, dry_run=False)
            (self.root / 'q004-confirmation-execution.json').write_text(json.dumps({
                'id': 39380248003, 'run_attempt': 1, 'status': 'in_progress',
                'head_sha': '3978d4bbbbb1e37961c277cdf76696b84c69e3bc'}))
            with self.assertRaisesRegex(ValueError, 'must_be_terminal'):
                driver.worker(self.args, self.plan, {'runtime': 'original'}, dry_run=False)
        implementation.assert_not_called()


if __name__ == '__main__':
    unittest.main()
