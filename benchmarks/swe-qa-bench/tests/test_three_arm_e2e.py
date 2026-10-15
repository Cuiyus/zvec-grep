"""Offline execution/provenance contracts for the six-hundred-trial experiment."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml

from swe_qa_fixtures import _judged_task_report, _set_report_trial_judgements
from zg_bench import three_arm_e2e as run
from zg_bench.agents.zvec_grep import _zg
from zg_bench.engines.opencode.config import build_opencode_config
from zg_bench.engines.registry import resolve_opencode_model


class ThreeArmTests(unittest.TestCase):
    def make_report(self):
        source = _judged_task_report('reflex:6')
        _set_report_trial_judgements(source, baseline=[75]*10, zvec=[90]*9+[5])
        profiles = source['cases'][0]['profiles']
        profiles = {'baseline': profiles['baseline'], 'node': profiles['zvec-grep'],
                    'rust': copy.deepcopy(profiles['zvec-grep'])}
        return dict(kind=run.EXPERIMENT['kind'], model=run.MODEL, task_id='reflex:6', profiles=profiles,
                    judge=run.judge_metadata(profiles), provenance=dict(experiment=run.EXPERIMENT,
                    repetitions=10, harness_commit='test', identities={'node':'node','rust':'rust'},
                    execution=[dict(arm=arm,repetition=rep,returncode=0) for arm in run.ARMS for rep in range(1,11)]))

    def test_frozen_pr_statistics_and_main_tasks(self):
        self.assertEqual(run.native.validate_inputs(), run.EXPERIMENT['locked_input_sha256'])
        tasks = json.loads((run.DATA/'selection.json').read_text())['tasks']
        self.assertEqual(len(tasks),20)
        ids = {t['task_id'] for t in tasks}
        self.assertTrue({'pylint:10','pylint:9','sympy:38','astropy:38'} <= ids)
        self.assertFalse({'reflex:10','flask:5','sympy:36','astropy:36'} & ids)
        self.assertEqual(run.REPETITIONS,10)

    def test_six_hundred_fresh_commands_and_balanced_order(self):
        tasks=json.loads((run.DATA/'selection.json').read_text())['tasks']
        commands=[]
        for task in tasks:
            for rep in range(1,11):
                order=run.ordered_arms(task['task_slug'],rep)
                self.assertEqual(set(order),set(run.ARMS))
                for arm in order:
                    cmd=run.command(task['task_slug'],arm,Path('/evidence'),Path('/packages'),rep)
                    self.assertEqual(cmd[cmd.index('--n-attempts')+1],'1')
                    self.assertEqual(cmd[cmd.index('--max-retries')+1],'2')
                    self.assertEqual(cmd[cmd.index('--model')+1],'custom-openai/glm-5.2')
                    if arm=='baseline':
                        self.assertNotIn('--embedding-model',cmd)
                        self.assertNotIn('--zvec-grep-package',cmd)
                        self.assertEqual(cmd[cmd.index('--profile')+1],'baseline')
                    else:
                        self.assertEqual(cmd[cmd.index('--profile')+1],'zvec-grep')
                        self.assertEqual(cmd[cmd.index('--zvec-grep-package')+1],f'/packages/{arm}/candidate.tgz')
                    commands.append((arm,tuple(cmd)))
        self.assertEqual(len(commands),len(set(commands)))
        self.assertEqual(len(commands),600)
        self.assertEqual([sum(a==arm for a,c in commands) for arm in run.ARMS],[200,200,200])
        starts=[run.ordered_arms(t['task_slug'],r)[0] for t in tasks for r in range(1,11)]
        self.assertEqual(sorted(starts.count(a) for a in run.ARMS),[66,67,67])

    def test_profiles_differ_only_in_zg_access_and_public_cli_spelling(self):
        config=resolve_opencode_model(run.MODEL).configuration
        baseline=build_opencode_config(config,profile='baseline')
        with_zg=build_opencode_config(config,profile='zvec-grep')
        with_zg.pop('mcp')
        self.assertEqual(baseline,with_zg)
        for variant,expected in [('node','zg index'),('rust','zg --index')]:
            with patch.dict(os.environ,{'ZG_BENCH_CLI_RUNTIME':variant}):
                self.assertEqual(_zg('index'),expected)

    def test_iqr_does_not_erase_judge_calls_or_rejudge_shared_baseline(self):
        report=self.make_report()
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':''}):
            summary=run.summarize([report],Path(temp))
            self.assertEqual(summary['judged_trials'],30)
            self.assertFalse(summary['complete'])
            self.assertEqual(len(summary['missing_tasks']),19)
            self.assertEqual([r['retained_count'] for r in summary['rows']],[10,9,9])
            self.assertEqual([r['filtered']['judge'] for r in summary['rows']],[75,90,90])
            self.assertEqual(len(report['profiles']['rust']['trials']),10)
            markdown=(Path(temp)/'report.md').read_text()
            self.assertIn('Node 0.2.2 + zg',markdown)
            self.assertIn('r10=5 (low)',markdown)

    def test_failures_missing_trials_and_protocol_changes_cannot_pass(self):
        for mutate in (
            lambda r:r['provenance']['execution'].pop(),
            lambda r:r['provenance']['execution'][0].update(returncode=1),
            lambda r:r['profiles']['rust']['trials'].pop(),
            lambda r:r['provenance'].update(experiment={}),
        ):
            report=copy.deepcopy(self.make_report());mutate(report)
            with self.assertRaises(Exception):
                run.validate_report(report,Path('/report.json'))

    def test_judging_is_checkpointed_once_per_physical_answer(self):
        report = self.make_report()
        trials = {arm: [dict(trial_index=t['trial_index'], trial_name=t['trial_name'],
                            answer=f"answer-{arm}-{t['trial_index']}", **t['metrics'])
                       for t in report['profiles'][arm]['trials']] for arm in run.ARMS}
        def score(rows, reference, model):
            t = rows[0]
            return [dict(trial_index=t['trial_index'], trial_name=t['trial_name'],
                         judge=copy.deepcopy(report['profiles']['baseline']['trials'][0]['judge']),
                         metrics=copy.deepcopy(report['profiles']['baseline']['metrics']))]
        with tempfile.TemporaryDirectory() as temp, patch.object(run.native,'judge',side_effect=score) as judge:
            first = run.judge_profiles(trials, {'question':'q','reference_answer':'r'}, Path(temp))
            second = run.judge_profiles(trials, {'question':'q','reference_answer':'r'}, Path(temp))
            self.assertEqual(first,second)
            self.assertEqual(judge.call_count,30)
            trials['baseline'][0]['answer']='changed input'
            with self.assertRaisesRegex(ValueError,'different input evidence'):
                run.judge_profiles(trials, {'question':'q','reference_answer':'r'}, Path(temp))

    def test_workflow_stays_on_fork_with_eight_serial_task_jobs(self):
        workflow=yaml.safe_load((run.ROOT.parents[1]/'.github/workflows/swe-qa-bench.yml').read_text())
        self.assertEqual(set(workflow['on']),{'workflow_dispatch'})
        self.assertEqual(workflow['jobs']['task']['strategy']['max-parallel'],8)
        for job in workflow['jobs'].values():
            guard=job['steps'][0]['run']
            self.assertIn('Cuiyus/zvec-grep',guard)
            self.assertIn('BENCH_TRIGGERING_ACTOR',guard)
        action=next(s for s in workflow['jobs']['package-rust']['steps'] if s.get('uses','').startswith('./.github/actions'))
        self.assertEqual(action['with']['candidate_ref'],run.EXPERIMENT['rust']['source_commit'])


if __name__=='__main__':
    unittest.main()
