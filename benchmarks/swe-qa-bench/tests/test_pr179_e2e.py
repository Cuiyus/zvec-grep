import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml
from zg_bench import pr179_e2e as runner


class AdoptionComparisonTests(unittest.TestCase):
    def test_order_is_balanced_over_twenty_tasks_and_three_repetitions(self):
        tasks = json.loads((runner.DATA / 'selection.json').read_text())['tasks']
        orders = [runner.version_order(t['task_slug'], r) for t in tasks for r in range(1,4)]
        self.assertEqual(orders.count(('before','after')), 30)
        self.assertEqual(orders.count(('after','before')), 30)

    def test_missing_repetition_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'expected one zg job'):
                runner.collect(Path(temp), 'reflex-6', {})

    def test_installed_binary_mismatch_rejects_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            trial=root/'trial'
            (trial/'agent').mkdir(parents=True)
            (trial/'agent/zvec-grep-setup.json').write_text(json.dumps({
                'status':'ready', 'package_sha256':'expected', 'native_cli_sha256':'wrong',
            }))
            with patch.object(runner,'_job_dirs',return_value=[root/'job']), \
                 patch.object(runner,'_completed_job'), \
                 patch.object(runner,'_select_trials',return_value=[(trial,{})]):
                with self.assertRaisesRegex(ValueError, 'native_cli_sha256 mismatch'):
                    runner.collect(root, 'reflex-6', {'before':{
                        'package_sha256':'expected','native_cli_sha256':'expected-binary',
                    }})

    def test_fork_only_manual_workflow_guards_all_jobs_and_preserves_evidence(self):
        path=runner.ROOT.parents[1]/'.github/workflows/swe-qa-bench.yml'
        workflow=yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow['on']), {'workflow_dispatch'})
        for job in workflow['jobs'].values():
            self.assertEqual(job['steps'][0]['id'], 'authorize')
            self.assertIn('BENCH_TRIGGERING_ACTOR', job['steps'][0]['run'])
            self.assertEqual(job['permissions']['contents'], 'read')
        self.assertEqual(workflow['jobs']['remaining']['needs'], ['validate','canary'])
        trial_steps=workflow['jobs']['canary']['steps']
        downloads=[s for s in trial_steps if s.get('uses')=='actions/download-artifact@v8']
        self.assertEqual({s['with']['run-id'] for s in downloads}, {'37903765050','37903768937'})
        self.assertTrue(any(s.get('id')=='secret-scan' for s in trial_steps))


if __name__ == '__main__':
    unittest.main()
