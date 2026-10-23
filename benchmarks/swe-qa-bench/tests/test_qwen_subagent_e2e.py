"""Freeze the 600-answer budget and forbid accidental agent reruns on recovery."""
from collections import Counter
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import yaml
from swe_qa_fixtures import _judged_task_report, _set_report_trial_judgements
from zg_bench import qwen_subagent_e2e as run


class QwenE2ETests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(setattr, run.native, 'REPETITIONS', run.native.REPETITIONS)

    def make_report(self):
        protocol = run.configure('smoke')
        source = json.loads(json.dumps(_judged_task_report('conan:19')).replace('glm-5.2', run.MODEL))
        _set_report_trial_judgements(source, baseline=[75], zvec=[90])
        old = source['cases'][0]['profiles']
        profiles = {arm: copy.deepcopy(old['baseline' if arm.startswith('baseline') else 'zvec-grep']) for arm in run.ARMS}
        return dict(kind=protocol['kind'], model=run.MODEL, task_id='conan:19', profiles=profiles,
                    judge=run.judge_metadata(profiles), provenance=dict(experiment=protocol, repetitions=1,
                    task='conan-19', execution=[dict(arm=arm, repetition=1, returncode=0) for arm in run.ARMS]))

    def test_exact_model_three_groups_and_budgets(self):
        historical = copy.deepcopy(run.SOURCE_EXPERIMENT)
        self.assertEqual(run.ARMS, ('baseline_allowed', 'baseline', 'rust'))
        self.assertEqual(run.configure('smoke')['planned_agent_trials'], 3)
        formal = run.configure('formal')
        self.assertEqual(formal['planned_agent_trials'], 600)
        self.assertEqual(formal['models'], ['qwen3.8-max'])
        self.assertEqual(formal['no_subagent']['denied_arms'], ['baseline', 'rust'])
        self.assertNotIn('node', formal)
        self.assertEqual(formal['round'], 1)
        self.assertEqual(run.validate_model_settings()['opencode_version'], '1.18.4')
        self.assertEqual(run.native.validate_inputs(), historical['locked_input_sha256'])
        self.assertEqual(run.SOURCE_EXPERIMENT, historical)
        starts, commands = Counter(), set()
        for task in run.selected_tasks('formal'):
            for rep in range(1, 11):
                order = run.ordered_arms(task, rep)
                starts[order[0]] += 1
                for arm in order:
                    cmd = run.command(task, arm, Path('/evidence'), Path('/packages'), rep)
                    self.assertEqual(cmd[cmd.index('--model') + 1], 'custom-openai/qwen3.8-max')
                    self.assertEqual(cmd[cmd.index('--max-retries') + 1], '2')
                    self.assertEqual(cmd[cmd.index('--n-attempts') + 1], '1')
                    if arm.startswith('baseline'):
                        self.assertNotIn('--zvec-grep-package', cmd)
                    commands.add(tuple(cmd))
        self.assertEqual(len(commands), 600)
        self.assertEqual(sorted(starts.values()), [66, 67, 67])
        with self.assertRaises(ValueError):
            run.command('conan-19', 'node', Path('/evidence'), Path('/packages'), 1)

    def test_all_three_profiles_receive_usage_score_validation(self):
        report = self.make_report()
        with patch.object(run, 'validate_task_report', wraps=run.validate_task_report) as validate:
            run.validate_report(report, Path('/report.json'))
        self.assertEqual(validate.call_count, 2)
        for mutate in (
            lambda r: r['profiles']['baseline_allowed']['trials'].clear(),
            lambda r: r['profiles']['rust']['metrics'].update(output_tokens=-1),
            lambda r: r['provenance']['execution'].pop(),
            lambda r: r['profiles'].update(node=r['profiles']['rust']),
            lambda r: r.update(model='qwen3.7-max'),
        ):
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.assertRaises(Exception):
                run.validate_report(changed, Path('/report.json'))

    def test_judge_checkpoint_reuses_every_answer_without_extra_requests(self):
        report = self.make_report()
        trials = {arm: [dict(trial_index=1, trial_name=arm, answer='answer-' + arm)] for arm in run.ARMS}
        def judge(rows, reference, model):
            self.assertEqual(model, 'qwen3.8-max')
            return [dict(trial_index=1, trial_name=rows[0]['trial_name'], **{
                key: copy.deepcopy(report['profiles']['baseline']['trials'][0][key]) for key in ('judge', 'metrics')})]
        with tempfile.TemporaryDirectory() as temp, patch.object(run.native, 'judge', side_effect=judge) as model:
            a = run.judge_profiles(trials, {'question': 'q'}, Path(temp))
            b = run.judge_profiles(trials, {'question': 'q'}, Path(temp))
            self.assertEqual(a, b)
            self.assertEqual(model.call_count, 3)
            trials['baseline'][0]['answer'] = 'different answer'
            with self.assertRaisesRegex(ValueError, 'different input'):
                run.judge_profiles(trials, {'question': 'q'}, Path(temp))

    def test_run_executes_only_three_and_collect_only_never_executes(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            args = SimpleNamespace(phase='smoke', task='conan-19', output=Path(temp), packages=Path('/packages'), collect_only=False)
            execute = stack.enter_context(patch.object(run.subprocess, 'run', return_value=SimpleNamespace(returncode=0)))
            stack.enter_context(patch.object(run.subprocess, 'check_output', return_value='f' * 40))
            identities = stack.enter_context(patch.object(run.versions, 'identity', return_value={'cli_sha256': 'fixed'}))
            prebuild = stack.enter_context(patch.object(run.versions, 'prebuild_task_image', return_value={}))
            finish = stack.enter_context(patch.object(run, 'finish_collection'))
            run.run(args)
            self.assertEqual(execute.call_count, 3)
            self.assertEqual(identities.call_args.args[1], 'rust')
            for call in execute.call_args_list:
                cmd, env = call.args[0], call.kwargs['env']
                allowed = '/baseline_allowed/' in cmd[cmd.index('--jobs-dir') + 1]
                self.assertEqual(env['ZG_BENCH_DISABLE_SUBAGENTS'], '0' if allowed else '1')
            args.collect_only = True
            run.run(args)
            self.assertEqual(execute.call_count, 3)
            self.assertEqual(prebuild.call_count, 1)
            self.assertEqual(finish.call_count, 2)
            args.collect_only = False
            with self.assertRaisesRegex(ValueError, 'do not overwrite'):
                run.run(args)

    def test_workflow_only_rust_and_two_explicit_phases(self):
        wf = yaml.safe_load((run.versions.ROOT.parents[1] / '.github/workflows/swe-qa-bench.yml').read_text())
        self.assertIn('package-rust', wf['jobs'])
        self.assertNotIn('package-node', wf['jobs'])
        commands = '\n'.join(step.get('run', '') for job in wf['jobs'].values() for step in job.get('steps', []))
        self.assertIn('zg_bench.qwen_subagent_e2e', commands)
        self.assertNotIn('zg_bench.no_subagent_e2e', commands)
        self.assertEqual(wf['jobs']['task']['strategy']['max-parallel'], 8)
        self.assertFalse(wf['jobs']['task']['strategy']['fail-fast'])

if __name__ == '__main__':
    unittest.main()
