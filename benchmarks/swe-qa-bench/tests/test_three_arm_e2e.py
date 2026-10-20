"""Offline execution/provenance contracts for the six-hundred-trial experiment."""
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
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
        for profile in profiles.values():
            profile['judge'].update(model=run.MODEL, label=run.judge_label(run.MODEL))
            for trial in profile['trials']:
                trial['judge'].update(model=run.MODEL, label=run.judge_label(run.MODEL))
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
                    self.assertEqual(cmd[cmd.index('--model')+1],'custom-openai/qwen3.8-max')
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

    def test_trimming_does_not_erase_judge_calls_or_rejudge_shared_baseline(self):
        report=self.make_report()
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':''}):
            summary=run.summarize([report],Path(temp))
            self.assertEqual(summary['judged_trials'],30)
            self.assertFalse(summary['complete'])
            self.assertEqual(len(summary['missing_tasks']),19)
            self.assertEqual([r['retained_count'] for r in summary['rows']],[8,8,8])
            self.assertEqual([r['filtered']['judge'] for r in summary['rows']],[75,90,90])
            self.assertEqual(len(report['profiles']['rust']['trials']),10)
            markdown=(Path(temp)/'report.md').read_text()
            self.assertIn('Node 0.2.2 + zg',markdown)
            self.assertIn('Raw statistics:',markdown)
            self.assertIn('Tool calls / execution',markdown)

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
        for arm in ('node', 'rust'):
            job = workflow['jobs']['package-' + arm]
            self.assertEqual(job['permissions']['actions'], 'read')
            download = next(s for s in job['steps'] if s.get('uses', '').startswith('actions/download-artifact@'))
            self.assertEqual(download['with']['run-id'], 39231111089)
            self.assertEqual(download['with']['repository'], 'Cuiyus/zvec-grep')
            self.assertEqual(download['with']['name'], 'three-arm-package-' + arm)
            self.assertEqual(download['with']['github-token'], '${{ github.token }}')
            self.assertTrue(any('verify_reused_package.py' in s.get('run', '') and '--variant ' + arm in s['run'] for s in job['steps']))
            self.assertFalse(any('rust-candidate-package' in s.get('uses', '') for s in job['steps']))
        aggregate = workflow['jobs']['aggregate']
        self.assertEqual(aggregate['permissions']['actions'], 'read')
        self.assertFalse(any('--reference-reports' in s.get('run','') for s in aggregate['steps']))
        self.assertEqual(run.EXPERIMENT['independent_rounds'], 3)
        self.assertEqual(run.EXPERIMENT['execution_reference_run_id'], 39231111089)
        self.assertEqual(run.EXPERIMENT['kind'], 'three-arm-qwen-local-three-rounds')
        self.assertEqual(run.EXPERIMENT['total_planned_agent_trials'], 1800)
        self.assertEqual(workflow['env']['ZG_BENCH_ROUND'], '${{ inputs.round }}')
        self.assertEqual(workflow['on']['workflow_dispatch']['inputs']['round']['options'], ['1','2','3'])

    def test_reused_package_verification_rejects_changed_package_and_cli(self):
        helper = run.ROOT.parent / 'version-comparison/verify_reused_package.py'
        spec = importlib.util.spec_from_file_location('verify_reused_package', helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for arm in ('node', 'rust'):
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                entrypoint = 'dist/cli/index.js' if arm == 'node' else 'bin/zg'
                cli = b'unchanged first-round CLI'
                package = directory / 'candidate.tgz'
                with tarfile.open(package, 'w:gz') as archive:
                    metadata = json.dumps({'name':'@zvec/zvec-grep', 'version':'0.2.2' if arm == 'node' else '0.0.1', 'bin':{'zg':entrypoint}}).encode()
                    for name, content in [('package/package.json', metadata), ('package/' + entrypoint, cli)]:
                        member = tarfile.TarInfo(name)
                        member.size = len(content)
                        archive.addfile(member, io.BytesIO(content))
                body = package.read_bytes()
                config = {'source_commit':'a' * 40, 'tarball_sha256':hashlib.sha256(body).hexdigest(),
                          'cli_sha256':hashlib.sha256(cli).hexdigest(),
                          'registry_integrity':'sha512-' + base64.b64encode(hashlib.sha512(body).digest()).decode()}
                manifest = {'schema_version':1, 'candidate_commit':config['source_commit'],
                            'tarball':'candidate.tgz', 'tarball_sha256':config['tarball_sha256']}
                manifest_path = directory / 'manifest.json'
                manifest_path.write_text(json.dumps(manifest))
                verified = module.verify(directory, arm, {arm:config})
                self.assertEqual(verified['cli_sha256'], config['cli_sha256'])
                self.assertEqual(verified['package_sha256'], config['tarball_sha256'])
                # A matching, rewritten manifest cannot authorize a new tarball.
                package.write_bytes(body + b'tampered')
                manifest['tarball_sha256'] = hashlib.sha256(package.read_bytes()).hexdigest()
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'Package SHA256'):
                    module.verify(directory, arm, {arm:config})
                package.write_bytes(body)
                manifest['tarball_sha256'] = config['tarball_sha256']
                manifest_path.write_text(json.dumps(manifest))
                wrong_cli = dict(config, cli_sha256='0' * 64)
                with self.assertRaisesRegex(ValueError, 'CLI SHA256'):
                    module.verify(directory, arm, {arm:wrong_cli})
                manifest['candidate_commit'] = 'b' * 40
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'manifest'):
                    module.verify(directory, arm, {arm:config})



if __name__=='__main__':
    unittest.main()
