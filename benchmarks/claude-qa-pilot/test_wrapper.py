"""Free checks of dispatch guards and the frozen CI-to-runner contract."""
import contextlib
import hashlib
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('confirmation_ci', HERE / 'ci_run.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

# task, root guidance, completion budget, confirmation, repeats
ALLOWED = {
    ('reflex-6', False, False, False, 1),
    ('reflex-6', True, False, False, 1),
    ('zvec-q001', False, False, False, 1),
    ('zvec-q001', False, True, False, 1),
    ('zvec-q001', False, True, True, 2),
}


class ReachedSetup(Exception):
    pass


class WrapperTests(unittest.TestCase):
    def test_dispatch_rejects_other_confirmation_combinations_before_provider(self):
        workflow = (HERE.parents[1] / '.github/workflows/claude-qa-pilot.yml').read_text()
        script = workflow.split("          python3 - <<'PY'\n", 1)[1].split('          PY\n', 1)[0]
        script = '\n'.join(line[10:] for line in script.splitlines())
        allowed = {entry[:4] for entry in ALLOWED} | {('both', False, False, False)}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'output'
            for task, root, budget, confirmation in itertools.product(
                    ('both', 'reflex-6', 'zvec-q001'), (False, True), (False, True), (False, True)):
                case = (task, root, budget, confirmation)
                output.write_text('')
                environment = {
                    'PILOT_TASK': task, 'PILOT_ROOT_GUIDANCE': str(root).lower(),
                    'PILOT_COMPLETION_BUDGET': str(budget).lower(),
                    'PILOT_COMPLETION_CONFIRMATION': str(confirmation).lower(),
                    'GITHUB_OUTPUT': str(output),
                }
                with self.subTest(case=case), patch.dict(os.environ, environment):
                    if case in allowed:
                        exec(script, {})
                        tasks = json.loads(output.read_text().split('=', 1)[1])
                        self.assertEqual(tasks, ['reflex-6', 'zvec-q001'] if task == 'both' else [task])
                    else:
                        with self.assertRaises(SystemExit):
                            exec(script, {})
                        self.assertEqual(output.read_text(), '')

    def test_ci_requires_exact_phase_task_flags_and_repeat_count_before_setup(self):
        for case in itertools.product(('reflex-6', 'zvec-q001'), (False, True),
                                      (False, True), (False, True), (1, 2, 3)):
            task, root, budget, confirmation, repeats = case
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                (base / 'experiment-completion-confirmation.json').write_text('{}\n')
                argv = self.arguments(base, task, root, budget, confirmation, repeats)
                with patch('sys.argv', argv), patch.object(ci, 'HERE', base), \
                        patch.object(ci, 'ROOT', base), contextlib.redirect_stderr(io.StringIO()), \
                        patch.object(ci, 'upstream_protocol', side_effect=ReachedSetup) as setup:
                    if case in ALLOWED:
                        with self.assertRaises(ReachedSetup):
                            ci.main()
                    else:
                        with self.assertRaises(SystemExit) as raised:
                            ci.main()
                        self.assertEqual(raised.exception.code, 2)
                        setup.assert_not_called()
                        self.assertFalse((base / 'work').exists())

    @staticmethod
    def arguments(base, task, root, budget, confirmation, repeats):
        argv = ['ci_run.py', '--task', task, '--work', str(base / 'work'),
                '--reports', str(base / 'reports'), '--repeats', str(repeats)]
        for enabled, flag in ((root, '--workspace-root-guidance'),
                              (budget, '--completion-budget'),
                              (confirmation, '--completion-confirmation')):
            if enabled:
                argv.append(flag)
        return argv

    def test_runner_arguments_limits_timeout_and_plan_provenance(self):
        for task, root, budget, confirmation, repeats in sorted(ALLOWED):
            with self.subTest(task=task, root=root, budget=budget, confirmation=confirmation), \
                    tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                plan = b'{"fixture": true}\n'
                (base / 'experiment-completion-confirmation.json').write_bytes(plan)
                commands = []
                revision = ('fe0f946dc0c240c6c1e513318c21db407e191c78' if task == 'reflex-6'
                            else 'ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d')

                def fake_run(command, **kwargs):
                    commands.append((command, kwargs))
                    if 'rev-parse' in command:
                        return revision
                    if any(str(part).endswith('prepare_corpus.py') for part in command):
                        source = Path(command[command.index('--output') + 1])
                        source.mkdir()
                        (source / 'fixture.py').write_text('x = 1\n')
                    if '/opt/qa/zg-provenance.json' in command:
                        return json.dumps({'commit': ci.UPSTREAM_COMMIT})
                    return ''

                archive = MagicMock()
                archive.__enter__.return_value = archive
                archive.extractall.side_effect = lambda source, **kwargs: (source / 'fixture.py').write_text('x = 1\n')
                with patch('sys.argv', self.arguments(base, task, root, budget, confirmation, repeats)), \
                        patch.object(ci, 'HERE', base), patch.object(ci, 'ROOT', base), \
                        patch.object(ci, 'run', side_effect=fake_run), \
                        patch.object(ci.tarfile, 'open', return_value=archive), \
                        patch.object(ci, 'upstream_protocol', return_value=(
                            'local/potion-code-16m-v2', '', {'index_ignore_sha256': 'fixture'})):
                    ci.main()
                command, options = next((command, options) for command, options in commands
                                        if len(command) > 1 and str(command[1]).endswith('/run.py'))
                metadata = json.loads((base / 'reports/setup.json').read_text())
                self.assertEqual(metadata['phase'], 'confirmation' if confirmation else 'development')
                self.assertEqual(metadata['protocol_version'], 'completion-v2' if budget else ('root-v2' if root else 'v1'))
                self.assertEqual(metadata['start_repeat'], 2 if confirmation else 1)
                self.assertEqual(metadata['repeats'], repeats)
                self.assertEqual(metadata['planned_trial_count'], repeats * 2)
                self.assertEqual(metadata['planned_trials'], [
                    {'repeat': 2, 'profile': 'zg'},
                    {'repeat': 2, 'profile': 'baseline'},
                    {'repeat': 3, 'profile': 'baseline'},
                    {'repeat': 3, 'profile': 'zg'},
                ] if confirmation else [
                    {'repeat': 1, 'profile': 'baseline'},
                    {'repeat': 1, 'profile': 'zg'},
                ])
                self.assertEqual(options['timeout'], 4500 if confirmation else 2000)
                self.assertEqual('--workspace-root-guidance' in command, root)
                self.assertEqual('--tool-budget' in command, budget)
                self.assertEqual('--start-repeat' in command, confirmation)
                expected = {'--repeats': str(repeats), '--model': 'qwen3.8-max',
                            '--max-turns': '30', '--timeout': '900',
                            '--max-output-tokens': '8192', '--effort': 'high'}
                if budget:
                    expected.update({'--tool-budget': '24', '--research-seconds': '600'})
                if confirmation:
                    expected['--start-repeat'] = '2'
                    self.assertEqual(metadata['confirmation_plan'], {
                        'path': 'experiment-completion-confirmation.json',
                        'sha256': hashlib.sha256(plan).hexdigest(),
                    })
                else:
                    self.assertIsNone(metadata['confirmation_plan'])
                for flag, value in expected.items():
                    self.assertEqual(command[command.index(flag) + 1], value)


if __name__ == '__main__':
    unittest.main()
