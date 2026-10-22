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

from swe_qa_fixtures import _judged_task_report, _set_report_trial_judgements
from zg_bench import no_subagent_e2e as run


class NoSubagentE2ETests(unittest.TestCase):
    def setUp(self):
        self.saved = [(module, module.REPETITIONS, getattr(module, 'EXPERIMENT', None))
                      for module in (run.three_arm, run.native, run.versions)]
        self.saved_arms = run.three_arm.ARMS
        self.environment = patch.dict(os.environ)
        self.environment.start()

    def tearDown(self):
        for module, repetitions, experiment in self.saved:
            module.REPETITIONS = repetitions
            if experiment is not None:
                module.EXPERIMENT = experiment
        run.three_arm.ARMS = self.saved_arms
        self.environment.stop()

    def test_phases_have_disjoint_provenance_and_exact_fixed_budgets(self):
        historical = copy.deepcopy(run.SOURCE_EXPERIMENT)
        self.assertEqual(run.selected_tasks('smoke'), ['conan-19'])
        self.assertEqual(len(run.selected_tasks('formal')), 20)
        smoke = run.configure('smoke')
        self.assertEqual(run.native.REPETITIONS, 1)
        self.assertEqual(run.versions.REPETITIONS, 1)
        self.assertEqual(run.three_arm.REPETITIONS, 1)
        self.assertEqual(smoke['expected_tasks'] * smoke['e2e_repetitions'] * 4, 4)
        self.assertEqual(smoke['planned_agent_trials'], 4)
        formal = run.configure('formal')
        self.assertEqual(formal['expected_tasks'] * formal['e2e_repetitions'] * 4, 800)
        self.assertEqual(formal['planned_agent_trials'], 800)
        self.assertNotEqual(smoke, formal)
        self.assertTrue(formal['no_subagent']['enabled'])
        self.assertEqual(formal['no_subagent']['mechanism'], 'permission.task=deny')
        self.assertEqual(formal['no_subagent']['allowed_control'], 'baseline_allowed')
        self.assertEqual(formal['no_subagent']['denied_arms'], ['baseline', 'node', 'rust'])
        self.assertEqual(formal['subagent_policy']['baseline_allowed'], 'allowed_not_forced')
        self.assertEqual(formal['kind'], 'glm52-subagent-ablation')
        self.assertEqual(run.three_arm.ARMS, run.ARMS)
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
                order = run.ordered_arms(task, repetition)
                self.assertEqual(set(order), set(run.ARMS))
                starts.append(order[0])
                for arm in order:
                    command = run.command(task, arm, Path('/evidence'), Path('/packages'), repetition)
                    self.assertEqual(command[command.index('--n-attempts') + 1], '1')
                    self.assertEqual(command[command.index('--max-retries') + 1], '2')
                    self.assertEqual(command[command.index('--model') + 1], 'custom-openai/glm-5.2')
                    if arm in ('baseline_allowed', 'baseline'):
                        self.assertEqual(command[command.index('--profile') + 1], 'baseline')
                        self.assertIn('/' + arm + '/runs/', command[command.index('--jobs-dir') + 1])
                        self.assertNotIn('--zvec-grep-package', command)
                    commands.append(tuple(command))
        self.assertEqual(len(commands), 800)
        self.assertEqual(len(set(commands)), 800)
        self.assertEqual(sorted(starts.count(arm) for arm in run.ARMS), [50, 50, 50, 50])

    def make_report(self):
        experiment = run.configure('smoke')
        source = _judged_task_report('conan:19')
        _set_report_trial_judgements(source, baseline=[75], zvec=[90])
        original = source['cases'][0]['profiles']
        profiles = {arm: copy.deepcopy(original['baseline' if arm.startswith('baseline') else 'zvec-grep'])
                    for arm in run.ARMS}
        return dict(kind=experiment['kind'], model=run.MODEL, task_id='conan:19',
                    profiles=profiles, judge=run.three_arm.judge_metadata(profiles),
                    provenance=dict(experiment=experiment, repetitions=1, task='conan-19',
                                    execution=[dict(arm=arm, repetition=1, returncode=0) for arm in run.ARMS]))

    def test_allowed_control_receives_complete_judge_and_usage_validation(self):
        report = self.make_report()
        with patch.object(run.three_arm, 'validate_task_report', wraps=run.three_arm.validate_task_report) as validate:
            run.validate_report(report, Path('/report.json'))
        self.assertEqual(validate.call_count, 3)
        for mutate in (
            lambda r: r['profiles']['baseline_allowed']['trials'].clear(),
            lambda r: r['profiles']['baseline_allowed']['metrics'].update(output_tokens=-1),
            lambda r: r['profiles']['baseline_allowed']['judge'].update(total=101),
            lambda r: r['provenance']['execution'].pop(0),
        ):
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.assertRaises(Exception):
                run.validate_report(changed, Path('/report.json'))

    def test_four_physical_answers_are_judged_once_each(self):
        report = self.make_report()
        trials = {arm: [dict(trial_index=1, trial_name=arm + '-1', answer='answer-' + arm)]
                  for arm in run.ARMS}

        def score(rows, reference, model):
            trial = rows[0]
            source = report['profiles']['baseline']['trials'][0]
            return [dict(trial_index=trial['trial_index'], trial_name=trial['trial_name'],
                         judge=copy.deepcopy(source['judge']), metrics=copy.deepcopy(source['metrics']))]

        with tempfile.TemporaryDirectory() as temp, patch.object(run.native, 'judge', side_effect=score) as judge:
            first = run.three_arm.judge_profiles(trials, {'question': 'q', 'reference_answer': 'a'}, Path(temp))
            second = run.three_arm.judge_profiles(trials, {'question': 'q', 'reference_answer': 'a'}, Path(temp))
            self.assertEqual(first, second)
            self.assertEqual(set(first), set(run.ARMS))
            self.assertEqual(judge.call_count, 4)
            self.assertEqual(run.three_arm.judge_metadata(first)['usage']['calls'], 4)

    def execute_smoke(self, *, exit_codes=(0, 0, 0, 0), audit_error=None):
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
            self.assertEqual(expected_slots, 4)
            if audit_error:
                raise audit_error
            return dict(complete=True, task_tool_calls=0, child_sessions=0, expected_slots=4)

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
                (run, 'validate_report', Mock()),
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

    def test_smoke_executes_four_trials_and_audits_before_collection_or_judging(self):
        output, events, commands, collect, judge, summary, error = self.execute_smoke()
        self.assertIsNone(error)
        self.assertEqual(events, ['execute', 'execute', 'execute', 'execute', 'audit', 'collect', 'judge'])
        self.assertEqual(collect.call_args.kwargs['repetitions'], 1)
        self.assertEqual(len(commands), 4)
        for command, env in commands:
            jobs = command[command.index('--jobs-dir') + 1]
            allowed = '/baseline_allowed/' in jobs
            self.assertEqual(env['ZG_BENCH_DISABLE_SUBAGENTS'], '0' if allowed else '1')
            if '/baseline/' in jobs or allowed:
                self.assertNotIn('ZG_BENCH_CLI_RUNTIME', env)
                self.assertNotIn('--embedding-model', command)
                self.assertNotIn('--zvec-grep-package', command)
        meta = json.loads((output / 'provenance.json').read_text())
        self.assertEqual(len(meta['execution']), 4)
        self.assertTrue(meta['no_subagent_audit']['complete'])
        self.assertEqual(meta['experiment']['phase'], 'smoke')
        self.assertEqual(summary.call_args.kwargs, {'phase': 'smoke', 'expected_tasks': 1})

    def test_failed_slot_does_not_skip_later_slots_or_publish_quality(self):
        output, events, commands, collect, judge, summary, error = self.execute_smoke(exit_codes=(1, 0, 0, 0))
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(events, ['execute', 'execute', 'execute', 'execute', 'audit'])
        self.assertEqual(len(json.loads((output / 'provenance.json').read_text())['execution']), 4)
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
        self.assertEqual(set(inputs), {'phase', 'recovery_task'})
        self.assertEqual(inputs['phase']['options'], ['smoke', 'formal', 'recover'])
        self.assertEqual(inputs['phase']['default'], 'smoke')
        self.assertEqual(inputs['recovery_task']['type'], 'string')
        self.assertIs(inputs['recovery_task']['required'], False)
        self.assertEqual(inputs['recovery_task']['default'], 'astropy-38')
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
