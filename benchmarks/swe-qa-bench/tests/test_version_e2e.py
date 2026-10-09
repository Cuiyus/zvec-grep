import json
import os
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace
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

    def test_isolated_retry_attempts_later_pairs_after_a_setup_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            args=SimpleNamespace(output=Path(temp)/'output',packages=Path(temp)/'packages',
                model='qwen3.8-max',embedding='local',task='matplotlib-37',
                collect_only=False,continue_after_failure=True)
            results=[SimpleNamespace(returncode=1)]+[SimpleNamespace(returncode=0) for _ in range(9)]
            identities=lambda package,variant:dict(cli_sha256=variant,package_sha256=variant)
            with patch.object(runner,'identity',side_effect=identities), \
                 patch.object(runner.subprocess,'check_output',return_value='test-harness\n'), \
                 patch.object(runner.subprocess,'run',side_effect=results) as execute:
                with self.assertRaisesRegex(RuntimeError,'1 of 10 frozen executions failed'):
                    runner.run(args)
            self.assertEqual(execute.call_count,10)
            meta=json.loads((args.output/'provenance.json').read_text())
            self.assertEqual(len(meta['execution']),10)
            self.assertEqual(sum(r['returncode']!=0 for r in meta['execution']),1)
            self.assertFalse((args.output/'report/report.json').exists())

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

class MatrixScopeTests(unittest.TestCase):
    def test_e2e_retry_selects_one_locked_task_model_without_empty_matrix(self):
        import yaml
        root=runner.ROOT.parents[1]
        workflow=yaml.load((root/'.github/workflows/swe-qa-bench.yml').read_text(),Loader=yaml.BaseLoader)
        step=next(s for s in workflow['jobs']['validate']['steps'] if s.get('id')=='matrix')
        script=step['run'].split("python - <<'PYCODE'\n",1)[1].rsplit('PYCODE',1)[0]
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'output'
            env=dict(os.environ,GITHUB_OUTPUT=str(output),TASK_SCOPE='matplotlib-37',
                MODEL_SCOPE='qwen3.8-max',EMBEDDING_SCOPE='remote')
            subprocess.run([__import__('sys').executable,'-c',script],cwd=root,env=env,check=True)
            rows=dict(line.split('=',1) for line in output.read_text().splitlines())
            self.assertEqual(json.loads(rows['canary'])['include'],[
                dict(task='matplotlib-37',model='qwen3.8-max',embedding='remote')])
            self.assertEqual(rows['has_remaining'],'false')
            self.assertEqual(json.loads(rows['remaining'])['include'],[])
            self.assertIn('has_remaining',workflow['jobs']['remaining']['if'])
            self.assertIn("inputs.task_scope == ''",workflow['jobs']['aggregate']['if'])
    def test_retrieval_retry_runs_only_the_requested_suite(self):
        import yaml
        root = runner.ROOT.parents[1]
        workflow = yaml.load((root / '.github/workflows/retrieval-only.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['on']['workflow_dispatch']['inputs']['suite']['default'], 'all')
        for name in ('sweqa','beir','duretrieval','quarry'):
            self.assertEqual(workflow['jobs'][name]['if'], f"inputs.suite == 'all' || inputs.suite == '{name}'")
        self.assertIn("inputs.suite == 'all'",workflow['jobs']['results']['if'])

    def test_aggregate_uses_the_selected_embedding_scope(self):
        import yaml
        root = runner.ROOT.parents[1]
        workflow = yaml.load((root / '.github/workflows/swe-qa-bench.yml').read_text(), Loader=yaml.BaseLoader)
        step = next(s for s in workflow['jobs']['validate']['steps'] if s.get('id')=='matrix')
        self.assertIn("('aggregate',[dict(model=m,embedding=e)",step['run'])
        self.assertEqual(workflow['jobs']['aggregate']['strategy']['matrix'], '${{ fromJSON(needs.validate.outputs.aggregate) }}')
