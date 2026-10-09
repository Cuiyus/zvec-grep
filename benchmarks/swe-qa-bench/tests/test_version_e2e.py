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

class ActiveWorkflowTests(unittest.TestCase):
    def test_registered_workflows_guard_every_job_and_pin_versions(self):
        import yaml
        root = runner.ROOT.parents[1]
        for name in ('retrieval-only','swe-qa-bench'):
            workflow = yaml.load((root / f'.github/workflows/{name}.yml').read_text(), Loader=yaml.BaseLoader)
            self.assertEqual(set(workflow['on']), {'workflow_dispatch'})
            for job in workflow['jobs'].values():
                steps = job['steps']
                self.assertTrue(any('Cuiyus/zvec-grep' in s.get('run','') for s in steps))
                self.assertTrue(any(s.get('uses')=='./.github/actions/retrieval-authorize' or 'BENCH_TRIGGERING_ACTOR' in s.get('run','') for s in steps))
            source = (root / f'.github/workflows/{name}.yml').read_text()
            self.assertIn(runner.COMMITS['rust'],source)
        e2e = yaml.load((root / '.github/workflows/swe-qa-bench.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(e2e['jobs']['canary']['needs']), {'validate','package-node','package-rust'})
        self.assertEqual(e2e['jobs']['remaining']['needs'][-1], 'canary')
