"""Input-integrity and isolation checks for the q005 native preparation adapter."""
import copy
from pathlib import Path
import shutil
import tempfile
import unittest

import runtime_preparation_v1 as preparation
import expansion_v1 as original


class RuntimePreparationTests(unittest.TestCase):
    def fixture(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for relative in preparation.PINS:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(preparation.REPO / relative, target)
        return root

    def test_private_adapter_preserves_original_task_preparer(self):
        before = (original.TASK_IDS, original.DATASET, copy.deepcopy(original.PINS))
        adapted = preparation.implementation()
        self.assertEqual(adapted.TASK_IDS, ('q005',))
        self.assertEqual(adapted.DATASET, preparation.DATASET)
        self.assertNotIn(original.DATASET, adapted.PINS)
        self.assertEqual((original.TASK_IDS, original.DATASET, original.PINS), before)
        self.assertIsNot(adapted, original)

    def test_question_tampering_invalidates_accepted_review(self):
        root = self.fixture()
        preparation.validate_assets(root)
        path = root / 'benchmarks/zvec-issue-qa/tasks/q005/instruction.md'
        path.write_text(path.read_text() + '\nChanged task premise.\n')
        with self.assertRaisesRegex(ValueError, 'runtime_asset_changed:'):
            preparation.validate_assets(root)

    def test_review_tampering_is_not_admitted(self):
        root = self.fixture()
        path = root / 'benchmarks/zvec-issue-qa/evaluator/provenance/q005/canonical-asset-review.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'runtime_asset_changed:'):
            preparation.validate_assets(root)

    def test_identical_bytes_through_symlink_are_rejected(self):
        root = self.fixture()
        path = root / 'benchmarks/zvec-issue-qa/tasks/q005/instruction.md'
        target = path.with_name('outside-question.md')
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'runtime_asset_changed:'):
            preparation.validate_assets(root)


if __name__ == '__main__':
    unittest.main()
