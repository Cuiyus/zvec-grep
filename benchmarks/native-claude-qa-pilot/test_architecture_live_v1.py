"""Protect the one-pair scope and frozen native/runtime/quality references."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import architecture_live_v1 as driver
import expansion_live_v1 as original


class ArchitectureLiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.plan = json.loads((driver.HERE / 'architecture-live-plan-v1.json').read_text())
        decision = json.loads((driver.REPO / driver.DECISION).read_text())
        files = set(self.plan['frozen_harness_sha256']) | {driver.BASE_PLAN}
        files.update(x['path'] for x in decision['prerequisites'].values())
        for relative in files:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(driver.REPO / relative, path)
        self.plan_path = self.root / 'candidate-plan.json'
        self.group = self.root / 'group'
        (self.group / 'q004').mkdir(parents=True)
        contract = json.loads((self.root / driver.CONTRACT).read_text())
        metadata = {key: {'path': contract[key]['path'].removeprefix('benchmarks/zvec-issue-qa/'),
                          'sha256': contract[key]['sha256']} for key in ('question', 'rubric')}
        metadata.update({key: contract[key] for key in ('source_commit', 'source_inventory_sha256')})
        (self.group / 'q004/prepared.json').write_text(json.dumps(metadata))

    def validate(self):
        self.plan_path.write_text(json.dumps(self.plan))
        with patch.object(driver.live, 'validate_plan', return_value=({'runtime': 'original'}, {'proof': 'original'})), \
             patch.object(driver.preparation, 'validate'), \
             patch.object(driver, 'prepared_reference', return_value=self.prepared_reference):
            return driver.validate_plan(self.plan_path, self.root / 'main', self.root / 'reference',
                                        self.group, self.root / 'candidate.tgz', self.root)

    @property
    def prepared_reference(self):
        return self._prepared_reference if hasattr(self, '_prepared_reference') else self.plan['prepared_group']

    def test_exact_frozen_plan_keeps_original_runtime(self):
        plan, base, proof = self.validate()
        self.assertEqual(plan['tasks'], [{'id': 'q004', 'profiles': ['baseline', 'zvec-grep']}])
        self.assertEqual((base, proof), ({'runtime': 'original'}, {'proof': 'original'}))

    def test_other_task_or_order_is_rejected(self):
        original_tasks = copy.deepcopy(self.plan['tasks'])
        for tasks in [[{'id': 'q001', 'profiles': ['baseline', 'zvec-grep']}],
                      [{'id': 'q004', 'profiles': ['zvec-grep', 'baseline']}]]:
            with self.subTest(tasks=tasks):
                self.plan['tasks'] = tasks
                with self.assertRaisesRegex(ValueError, 'schema_or_schedule'):
                    self.validate()
        self.plan['tasks'] = original_tasks

    def test_retries_multiple_attempts_and_boolean_counts_are_rejected(self):
        for key, value in [('n_attempts', 2), ('n_attempts', True), ('max_retries', 1), ('schema_version', True)]:
            with self.subTest(key=key, value=value):
                old = self.plan[key]
                self.plan[key] = value
                with self.assertRaisesRegex(ValueError, 'schema_or_schedule'):
                    self.validate()
                self.plan[key] = old

    def test_extra_budget_override_is_rejected(self):
        self.plan['max_budget_usd'] = 999
        with self.assertRaisesRegex(ValueError, 'schema_or_schedule'):
            self.validate()

    def test_changed_workflow_or_contract_bytes_are_rejected(self):
        for relative in [driver.WORKFLOW, driver.CONTRACT]:
            with self.subTest(relative=relative):
                path = self.root / relative
                before = path.read_bytes()
                path.write_bytes(before + b' ')
                with self.assertRaisesRegex(ValueError, 'control_changed'):
                    self.validate()
                path.write_bytes(before)

    def test_stale_prepared_identity_is_rejected(self):
        self._prepared_reference = {'group_sha256': '0' * 64, 'metadata_sha256': {'q004': '0' * 64}}
        with self.assertRaisesRegex(ValueError, 'prepared_identity_changed'):
            self.validate()

    def test_original_runtime_reference_cannot_be_replaced(self):
        self.plan['base_plan']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'base_reference_changed'):
            self.validate()

    def test_private_execution_adapter_does_not_mutate_original(self):
        before = (original.PROTOCOL, copy.deepcopy(original.TASKS), original.context_path)
        adapted = driver.execution_implementation()
        self.assertEqual(adapted.task_profiles('q004'), ['baseline', 'zvec-grep'])
        self.assertEqual(adapted.private_live('q004').PROTOCOL, driver.PROTOCOL)
        self.assertEqual(adapted.context_path(Path('/tmp/live')), Path('/tmp/live-q004-context.json'))
        self.assertEqual((original.PROTOCOL, original.TASKS, original.context_path), before)


if __name__ == '__main__':
    unittest.main()
