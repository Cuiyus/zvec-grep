"""Offline tests for the new experiment's execution and provenance boundaries."""
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from zg_bench import no_subagent_e2e as run


class NoSubagentE2ETests(unittest.TestCase):
    def setUp(self):
        self.saved = [(module, module.REPETITIONS, getattr(module, 'EXPERIMENT', None))
                      for module in (run.three_arm, run.native, run.versions)]
        self.environment = patch.dict(os.environ)
        self.environment.start()

    def tearDown(self):
        for module, repetitions, experiment in self.saved:
            module.REPETITIONS = repetitions
            if experiment is not None:
                module.EXPERIMENT = experiment
        self.environment.stop()

    def test_phases_have_disjoint_provenance_and_exact_fixed_budgets(self):
        historical = copy.deepcopy(run.SOURCE_EXPERIMENT)
        self.assertEqual(run.selected_tasks('smoke'), ['conan-19'])
        self.assertEqual(len(run.selected_tasks('formal')), 20)
        smoke = run.configure('smoke')
        self.assertEqual(run.native.REPETITIONS, 1)
        self.assertEqual(run.versions.REPETITIONS, 1)
        self.assertEqual(run.three_arm.REPETITIONS, 1)
        self.assertEqual(smoke['expected_tasks'] * smoke['e2e_repetitions'] * 3, 3)
        self.assertEqual(smoke['planned_agent_trials'], 3)
        formal = run.configure('formal')
        self.assertEqual(formal['expected_tasks'] * formal['e2e_repetitions'] * 3, 600)
        self.assertEqual(formal['planned_agent_trials'], 600)
        self.assertNotEqual(smoke, formal)
        self.assertEqual(formal['no_subagent'], {'enabled': True, 'mechanism': 'permission.task=deny'})
        self.assertEqual(os.environ['ZG_BENCH_DISABLE_SUBAGENTS'], '1')
        self.assertEqual(run.SOURCE_EXPERIMENT, historical)
        self.assertEqual(run.native.validate_inputs(), historical['locked_input_sha256'])
        self.assertNotIn('local_validation', formal)
        self.assertNotIn('supplemental_operations', formal)
        self.assertNotIn('modified Z', formal['aggregation_policy'])
        self.assertEqual(formal['package_source']['run_id'], 38700270964)

    def test_formal_commands_preserve_retries_and_interleave_every_arm(self):
        run.configure('formal')
        commands, starts = [], []
        for task in run.selected_tasks('formal'):
            for repetition in range(1, 11):
                order = run.three_arm.ordered_arms(task, repetition)
                self.assertEqual(set(order), set(run.ARMS))
                starts.append(order[0])
                for arm in order:
                    command = run.three_arm.command(task, arm, Path('/evidence'), Path('/packages'), repetition)
                    self.assertEqual(command[command.index('--n-attempts') + 1], '1')
                    self.assertEqual(command[command.index('--max-retries') + 1], '2')
                    self.assertEqual(command[command.index('--model') + 1], 'custom-openai/glm-5.2')
                    commands.append(tuple(command))
        self.assertEqual(len(commands), 600)
        self.assertEqual(len(set(commands)), 600)
        self.assertEqual(sorted(starts.count(arm) for arm in run.ARMS), [66, 67, 67])

    def execute_smoke(self, *, exit_codes=(0, 0, 0), audit_error=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        output = Path(temp.name).resolve()
        args = SimpleNamespace(phase='smoke', task='conan-19', output=output,
                               packages=Path('/packages'), collect_only=False)
        events = []
        commands = []
        exits = iter(exit_codes)

        def execute(command, env):
            commands.append((command, env))
            events.append('execute')
            return SimpleNamespace(returncode=next(exits))

        def audit(path, expected_slots):
            events.append('audit')
            self.assertEqual(path, output)
            self.assertEqual(expected_slots, 3)
            if audit_error:
                raise audit_error
            return dict(complete=True, task_tool_calls=0, child_sessions=0, expected_slots=3)

        pair = {'profiles': {'baseline': {'trials': []}, 'zvec-grep': {'trials': []}}}
        collector = Mock(side_effect=lambda *a, **kw: events.append('collect') or pair)
        judge = Mock(side_effect=lambda *a: events.append('judge') or {})
        summary = Mock(return_value={'complete': True})
        with ExitStack() as stack:
            for target, name, value in (
                (run.native, 'validate_inputs', Mock()),
                (run.versions, 'identity', Mock(return_value={'cli_sha256': 'fixed'})),
                (run.versions, 'prebuild_task_image', Mock(return_value={'image': 'fixed'})),
                (run.versions, 'collect', collector),
                (run.native, 'collect', Mock(return_value=[])),
                (run.three_arm, 'judge_profiles', judge),
                (run.three_arm, 'judge_metadata', Mock(return_value={})),
                (run.three_arm, 'validate_report', Mock()),
                (run.subprocess, 'run', execute),
                (run.subprocess, 'check_output', Mock(return_value='test-commit\n')),
                (run, 'summarize', summary),
            ):
                stack.enter_context(patch.object(target, name, value))
            stack.enter_context(patch.dict('sys.modules', {'zg_bench.no_subagent_audit': SimpleNamespace(audit=audit)}))
            error = None
            try:
                run.run(args)
            except (ValueError, RuntimeError) as exc:
                error = exc
        return output, events, commands, collector, judge, summary, error

    def test_smoke_executes_three_trials_and_audits_before_collection_or_judging(self):
        output, events, commands, collect, judge, summary, error = self.execute_smoke()
        self.assertIsNone(error)
        self.assertEqual(events, ['execute', 'execute', 'execute', 'audit', 'collect', 'judge'])
        self.assertEqual(collect.call_args.kwargs['repetitions'], 1)
        self.assertEqual(len(commands), 3)
        for _, env in commands:
            self.assertEqual(env['ZG_BENCH_DISABLE_SUBAGENTS'], '1')
        meta = json.loads((output / 'provenance.json').read_text())
        self.assertEqual(len(meta['execution']), 3)
        self.assertTrue(meta['no_subagent_audit']['complete'])
        self.assertEqual(meta['experiment']['phase'], 'smoke')
        self.assertEqual(summary.call_args.kwargs, {'phase': 'smoke', 'expected_tasks': 1})

    def test_failed_slot_does_not_skip_later_slots_or_publish_quality(self):
        output, events, commands, collect, judge, summary, error = self.execute_smoke(exit_codes=(1, 0, 0))
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(events, ['execute', 'execute', 'execute', 'audit'])
        self.assertEqual(len(json.loads((output / 'provenance.json').read_text())['execution']), 3)
        collect.assert_not_called()
        judge.assert_not_called()
        summary.assert_not_called()
        self.assertFalse((output / 'report/report.json').exists())

    def test_failed_delegation_audit_prevents_all_paid_judging(self):
        _, events, _, collect, judge, summary, error = self.execute_smoke(audit_error=ValueError('child session'))
        self.assertIsInstance(error, ValueError)
        self.assertEqual(events[-1], 'audit')
        collect.assert_not_called()
        judge.assert_not_called()
        summary.assert_not_called()

    def test_smoke_cannot_expand_task_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(phase='smoke', task='reflex-6', output=Path(temp),
                                   packages=Path('/packages'), collect_only=False)
            with self.assertRaisesRegex(ValueError, 'not scheduled'):
                run.run(args)
        with self.assertRaisesRegex(ValueError, 'Unknown experiment phase'):
            run.selected_tasks('three-rounds')

    def test_workflow_preserves_raw_evidence_and_has_only_explicit_phases(self):
        workflow = yaml.safe_load((run.versions.ROOT.parents[1] / '.github/workflows/swe-qa-bench.yml').read_text())
        self.assertEqual(set(workflow['on']), {'workflow_dispatch'})
        inputs = workflow['on']['workflow_dispatch']['inputs']
        self.assertEqual(set(inputs), {'phase'})
        self.assertEqual(inputs['phase']['options'], ['smoke', 'formal'])
        self.assertEqual(inputs['phase']['default'], 'smoke')
        self.assertEqual(workflow['env']['ZG_BENCH_DISABLE_SUBAGENTS'], '1')
        task = workflow['jobs']['task']
        self.assertEqual(task['strategy']['max-parallel'], 8)
        self.assertIs(task['strategy']['fail-fast'], False)
        for job in workflow['jobs'].values():
            guard = job['steps'][0]['run']
            self.assertIn('Cuiyus/zvec-grep', guard)
            self.assertIn('BENCH_TRIGGERING_ACTOR', guard)
        for arm in ('node', 'rust'):
            steps = workflow['jobs']['package-' + arm]['steps']
            download = next(s for s in steps if s.get('uses', '').startswith('actions/download-artifact@'))
            self.assertEqual(download['with']['run-id'], 38700270964)
            self.assertEqual(download['with']['name'], 'three-arm-package-' + arm)
        raw = next(s for s in task['steps'] if s.get('name') == 'Preserve all trials and failed attempts')
        self.assertIn('always()', raw['if'])
        self.assertIn('secret-scan.outcome', raw['if'])
        self.assertTrue(raw['with']['include-hidden-files'])
        aggregate = workflow['jobs']['aggregate']
        commands = '\n'.join(s.get('run', '') for s in aggregate['steps'])
        self.assertIn('zg_bench.no_subagent_e2e aggregate', commands)
        self.assertNotIn('glm_local_mad', commands)
        self.assertNotIn('reference-reports', commands)


if __name__ == '__main__':
    unittest.main()
