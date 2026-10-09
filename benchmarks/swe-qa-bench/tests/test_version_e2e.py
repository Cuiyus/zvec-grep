import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zg_bench import version_e2e as runner
from zg_bench.agents.zvec_grep import _zg

class VersionComparisonTests(unittest.TestCase):
    def test_five_repetitions_and_balanced_order(self):
        self.assertEqual(runner.REPETITIONS, 5)
        tasks = json.loads((runner.DATA / 'selection.json').read_text())['tasks']
        orders = [runner.version_order(t['task_slug'], r) for t in tasks for r in range(1,6)]
        self.assertEqual(orders.count(('node','rust')), 50)
        self.assertEqual(orders.count(('rust','node')), 50)

    def test_public_cli_spelling_matches_each_version(self):
        with patch.dict('os.environ', {'ZG_BENCH_CLI_RUNTIME':'node'}):
            self.assertEqual(_zg('index'), 'zg index')
            self.assertEqual(_zg('server'), 'zg server')
        with patch.dict('os.environ', {'ZG_BENCH_CLI_RUNTIME':'rust'}):
            self.assertEqual(_zg('index'), 'zg --index')

    def test_missing_repetition_rejects_incomplete_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'expected one zg job'):
                runner.collect(Path(temp), 'reflex-6', {})

if __name__ == '__main__':
    unittest.main()
