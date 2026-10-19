"""Offline contracts for the separate, symmetric final-answer verification pair."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import answer_verification_phase as phase
import architecture_phase as architecture
import ci_run as ci
import remote_embedding as remote
import run as runner
from test_architecture import arguments, workflow_script, write_json
from test_run import assistant, event_stream, init_event, result

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORKFLOW = ROOT / '.github/workflows/claude-qa-pilot.yml'
BASE_PROMPT_SHA = '220823c068789010a8f43aac840768b7d4ba7996d927ff58573f0b0641249325'
EFFECTIVE_PROMPT_SHA = 'fbdb8691b4a06e4702e04301fe5b1b573428e6951b5aaf81e7ba846d9f874c66'
ENV = {'ANTHROPIC_API_KEY': 'fixture-agent-key-not-real',
       'ANTHROPIC_BASE_URL': 'https://fixture.invalid',
       'ZVEC_GREP_API_KEY': 'fixture-embedding-key-not-real', 'ZVEC_GREP_ENDPOINT': remote.ENDPOINT}


def fixture(root, *, frozen=True):
    """Copy immutable controller inputs, with no solver output or runtime seeds."""
    here = root / 'benchmarks/claude-qa-pilot'
    base = json.loads((HERE / architecture.PLAN_NAME).read_text())
    files = [HERE / architecture.PLAN_NAME, HERE / remote.PLAN_NAME, HERE / 'tool_budget.py',
             HERE / phase.SUFFIX_NAME, ROOT / base['dataset']['path'],
             ROOT / base['task']['question']['path'], ROOT / base['task']['rubric']['path']]
    for original in files:
        target = root / original.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    plan = {key: copy.deepcopy(base[key]) for key in
            ('schema_version', 'agent', 'retrieval', 'completion_control', 'completion_hook',
             'preflight', 'accounting', 'task', 'dataset', 'evaluation', 'run_plan')}
    plan.update(experiment_id='claude-qwen-rust-zvec-answer-verification-v1', phase=phase.PHASE,
                protocol_version=phase.PROTOCOL, frozen=frozen,
                base_experiment={'path': str((here / architecture.PLAN_NAME).relative_to(root)),
                                 'sha256': phase.BASE_SHA256},
                base_common_instructions_sha256=BASE_PROMPT_SHA,
                common_instructions_sha256=EFFECTIVE_PROMPT_SHA,
                shared_answer_verification={'path': str((here / phase.SUFFIX_NAME).relative_to(root)),
                                            'sha256': phase.SUFFIX_SHA256,
                                            'applies_identically_to': ['baseline', 'zg']})
    plan['run_plan']['inputs'] = dict(phase.INPUTS)
    write_json(here / phase.PLAN_NAME, plan)
    return here, plan


def ci_arguments(root, **options):
    return arguments(root, architecture=False, **options) + ['--answer-verification-pilot']


def runner_arguments(root, here, enabled=True):
    source, index, home = (root / name for name in ('source', 'index', 'home'))
    for path in (source, index, home): path.mkdir()
    (source / 'fixture.py').write_text('x = 1\n')
    (index / 'index-marker').write_text('private index fixture')
    (home / 'grant-marker').write_text('private grant fixture')
    plan = json.loads((here / phase.PLAN_NAME).read_text())
    return ['run.py', '--source', str(source), '--prompt', str(root / plan['task']['question']['path']),
            '--output', str(root / 'out'), '--index-seed', str(index), '--home-seed', str(home),
            '--model', 'qwen3.8-max', '--embedding-model', remote.MODEL,
            '--max-output-tokens', '8192', '--tool-budget', '24', '--research-seconds', '600'] + (
                ['--answer-verification-pilot'] if enabled else [])


class AnswerVerificationTests(unittest.TestCase):
    def test_frozen_plan_inherits_exact_architecture_assets_and_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            here, plan = fixture(root)
            metadata = phase.validate_plan(root, here)
            self.assertEqual(metadata['common_instructions_sha256'], EFFECTIVE_PROMPT_SHA)
            self.assertEqual(metadata['sha256'], architecture.digest(here / phase.PLAN_NAME))
            self.assertEqual(plan['run_plan']['order'], ['baseline', 'zg'])
            self.assertFalse(plan['run_plan']['inputs']['architecture_pilot'])

    def test_drift_is_rejected_even_when_referenced_hash_is_updated(self):
        mutations = {
            'unfrozen': lambda p: p.update(frozen=False),
            'identity': lambda p: p.update(experiment_id='architecture'),
            'phase': lambda p: p.update(phase='architecture-development'),
            'protocol': lambda p: p.update(protocol_version='completion-v2'),
            'agent': lambda p: p['agent'].update(model='other'),
            'budget': lambda p: p['completion_control'].update(tool_budget=25),
            'runtime': lambda p: p['retrieval'].update(source_commit='0' * 40),
            'task': lambda p: p['task'].update(revision='0' * 40),
            'rubric': lambda p: p['task']['rubric'].update(sha256='0' * 64),
            'dataset': lambda p: p['dataset'].update(sha256='0' * 64),
            'preflight': lambda p: p['preflight'].update(outer_timeout_seconds=1),
            'accounting': lambda p: p['accounting'].update(agent='subtract waits'),
            'evaluation': lambda p: p['evaluation'].update(primary='score only'),
            'base_prompt': lambda p: p.update(base_common_instructions_sha256='0' * 64),
            'effective_prompt': lambda p: p.update(common_instructions_sha256='0' * 64),
            'repeat': lambda p: p['run_plan'].update(repeats=2),
            'order': lambda p: p['run_plan'].update(order=['zg', 'baseline']),
            'architecture_flag': lambda p: p['run_plan']['inputs'].update(architecture_pilot=True),
            'one_arm': lambda p: p['shared_answer_verification'].update(applies_identically_to=['zg']),
            'path_escape': lambda p: p['shared_answer_verification'].update(path='../outside'),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); here, plan = fixture(root)
                mutate(plan); write_json(here / phase.PLAN_NAME, plan)
                with self.assertRaises(ValueError): phase.validate_plan(root, here)
        for target in ('base', 'suffix', 'question', 'rubric', 'hook'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); here, plan = fixture(root)
                reference = (plan['base_experiment'] if target == 'base' else
                             plan['shared_answer_verification'] if target == 'suffix' else
                             plan['completion_hook'] if target == 'hook' else plan['task'][target])
                path = root / reference['path']; path.write_bytes(path.read_bytes() + b'\n')
                reference['sha256'] = architecture.digest(path)
                write_json(here / phase.PLAN_NAME, plan)
                with self.assertRaises(ValueError): phase.validate_plan(root, here)

    def test_workflow_and_wrapper_selection_are_exclusive_before_setup(self):
        script = workflow_script(WORKFLOW.read_text())
        class ReachedGate(Exception): pass
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); output = root / 'output'
            for task, root_flag, budget, confirmation, expansion, remote_flag, architecture_flag in itertools.product(
                    ('reflex-6', 'zvec-q001', 'zvec-q002', 'zvec-q003', 'zvec-q004'), *[(False, True)] * 6):
                allowed = (task, root_flag, budget, confirmation, expansion, remote_flag, architecture_flag) == (
                    'zvec-q004', False, True, False, False, False, False)
                argv = arguments(root, task=task, architecture=architecture_flag, root_guidance=root_flag,
                                 budget=budget, confirmation=confirmation, expansion=expansion,
                                 remote_flag=remote_flag) + ['--answer-verification-pilot']
                env = {'PILOT_TASK': task, 'PILOT_ROOT_GUIDANCE': str(root_flag).lower(),
                       'PILOT_COMPLETION_BUDGET': str(budget).lower(),
                       'PILOT_COMPLETION_CONFIRMATION': str(confirmation).lower(),
                       'PILOT_ISSUE_EXPANSION': str(expansion).lower(),
                       'PILOT_REMOTE_EMBEDDING': str(remote_flag).lower(),
                       'PILOT_ARCHITECTURE': str(architecture_flag).lower(),
                       'PILOT_ANSWER_VERIFICATION': 'true', 'GITHUB_OUTPUT': str(output)}
                with self.subTest(argv=argv), patch.object(sys, 'argv', argv), \
                        patch.object(ci, 'validate_answer_verification_plan', side_effect=ReachedGate) as gate, \
                        patch.object(ci, 'run') as run, patch.object(ci, 'upstream_protocol') as upstream, \
                        contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(ReachedGate if allowed else SystemExit): ci.main()
                    self.assertEqual(gate.called, allowed)
                    run.assert_not_called(); upstream.assert_not_called()
                    self.assertFalse((root / 'work').exists())
                with patch.dict(os.environ, env, clear=True):
                    output.write_text('')
                    if allowed:
                        exec(script, {})
                        self.assertEqual(json.loads(output.read_text().split('=', 1)[1]), ['zvec-q004'])
                    else:
                        with self.assertRaises(SystemExit): exec(script, {})
                        self.assertEqual(output.read_text(), '')

    def test_unfrozen_gate_runs_before_provider_environment_or_setup(self):
        workflow = WORKFLOW.read_text()
        label = '      - name: Verify answer verification plan and environment before provider calls\n'
        self.assertLess(workflow.index(label), workflow.index('      - name: Verify Qwen Anthropic'))
        step = workflow.split(label, 1)[1].split('      - name:', 1)[0]
        self.assertIn('if: inputs.answer_verification_pilot', step)
        self.assertIn("inputs.answer_verification_pilot && '--answer-verification-pilot'", workflow)
        self.assertEqual(workflow.count("github.repository == 'Cuiyus/zvec-grep'"), 2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); here, _ = fixture(root, frozen=False)
            with patch.object(ci, 'HERE', here), patch.object(ci, 'ROOT', root), \
                    patch.object(remote, 'validate_environment') as environment, \
                    patch.object(ci, 'upstream_protocol') as upstream, patch.object(ci, 'run') as run:
                with self.assertRaises(ValueError): exec(workflow_script(step), {})
                with patch.object(sys, 'argv', ci_arguments(root)), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit): ci.main()
                environment.assert_not_called(); upstream.assert_not_called(); run.assert_not_called()
                self.assertFalse((root / 'work').exists())

    def test_original_prompt_hashes_and_default_off_path_are_unchanged(self):
        expected = {(False, None): 'b177abd3db1e889cc4ef47c33af93bd069ed9a2444c7efe81078789c71eda503',
                    (True, None): 'f2d6b8061dff1996eff1be5ed6b431d4b6a72bfa9e8d2f49319e7b2dad2be5f2',
                    (False, 24): BASE_PROMPT_SHA}
        for (root_guidance, budget), digest in expected.items():
            args = runner.parser().parse_args(['--source', '/source', '--prompt', '/prompt', '--output', '/out',
                                               '--index-seed', '/index', '--model', 'qwen3.8-max'])
            args.workspace_root_guidance, args.tool_budget = root_guidance, budget
            self.assertFalse(args.answer_verification_pilot)
            with patch.object(phase, 'prepare_runner', side_effect=AssertionError('legacy policy read')):
                text = runner.effective_instructions(args)
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), digest)
            self.assertEqual(text, runner.common_instructions(root_guidance, budget, 600))

    def test_real_runner_command_and_manifest_share_one_render_for_both_arms(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); here, _ = fixture(root)
                argv = runner_arguments(root, here, enabled)
                commands = []
                def fake_popen(command, **options):
                    profile = 'baseline' if not commands else 'zg'
                    commands.append(command)
                    home_mount = next(part for part in command if str(part).startswith('type=bind,src=') and str(part).endswith('dst=/root'))
                    private_home = Path(home_mount.split('src=', 1)[1].split(',dst=', 1)[0])
                    self.assertEqual((private_home / '.zvec-grep/grant-marker').exists(), profile == 'zg')
                    mcp = json.loads((root / 'out' / ('r01-' + profile) / 'config/mcp.json').read_text())
                    self.assertEqual(set(mcp['mcpServers']), {'zvec_grep'} if profile == 'zg' else set())
                    self.assertEqual('ZVEC_GREP_API_KEY' in command, profile == 'zg')
                    event = assistant(); event['message']['content'] = [{'type': 'text', 'text': 'fixture'}]
                    options['stdout'].write(event_stream(init_event(profile), event, result()))
                    return Mock(returncode=0)
                with patch.object(sys, 'argv', argv), patch.dict(os.environ, ENV, clear=True), \
                        patch.object(runner, '__file__', str(here / 'run.py')), \
                        patch.object(phase, 'prepare_runner', wraps=phase.prepare_runner) as prepare, \
                        patch.object(runner.subprocess, 'run', return_value=Mock(stdout='fixture-version\n')), \
                        patch.object(runner.subprocess, 'Popen', side_effect=fake_popen), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(runner.main(), 0)
                self.assertEqual(prepare.call_count, int(enabled))
                self.assertEqual(len(commands), 2)
                manifest = json.loads((root / 'out/manifest.json').read_text())
                for command in commands:
                    self.assertEqual(command[command.index('--append-system-prompt') + 1], manifest['common_instructions'])
                    self.assertEqual(command[command.index('--max-turns') + 1], '30')
                digest = hashlib.sha256(manifest['common_instructions'].encode()).hexdigest()
                self.assertEqual(digest, EFFECTIVE_PROMPT_SHA if enabled else BASE_PROMPT_SHA)
                self.assertEqual(manifest['protocol_version'], phase.PROTOCOL if enabled else 'completion-v2')
                self.assertEqual('answer_verification_plan' in manifest, enabled)
                self.assertEqual('common_instructions_sha256' in manifest, enabled)
                self.assertEqual(manifest['order'], ['baseline', 'zg'])
                self.assertEqual(manifest['tool_budget'], 24)
                self.assertEqual(manifest['research_seconds'], 600)
                self.assertEqual(manifest['limits']['wall_seconds'], 900)

    def test_runner_rejects_budget_model_prompt_or_unfrozen_drift_before_docker(self):
        for mode in ('unfrozen', 'budget', 'model', 'turns', 'prompt', 'repeats', 'root'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); here, plan = fixture(root, frozen=mode != 'unfrozen')
                argv = runner_arguments(root, here)
                if mode in ('budget', 'model', 'turns'):
                    flag, value = {'budget': ('--tool-budget', '25'), 'model': ('--model', 'other'),
                                   'turns': ('--max-turns', '31')}[mode]
                    if flag in argv: argv[argv.index(flag) + 1] = value
                    else: argv += [flag, value]
                elif mode == 'prompt':
                    question = root / 'other.txt'; question.write_text('different question')
                    argv[argv.index('--prompt') + 1] = str(question)
                elif mode == 'repeats': argv += ['--repeats', '2']
                elif mode == 'root': argv += ['--workspace-root-guidance']
                with patch.object(sys, 'argv', argv), patch.dict(os.environ, ENV, clear=True), \
                        patch.object(runner, '__file__', str(here / 'run.py')), \
                        patch.object(runner.subprocess, 'run') as process, \
                        patch.object(runner.subprocess, 'Popen') as solver:
                    with self.assertRaises(ValueError): runner.main()
                    process.assert_not_called(); solver.assert_not_called()
                    self.assertFalse((root / 'out').exists())

    def test_wrapper_reuses_remote_stack_and_adds_only_the_new_runner_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); here, plan = fixture(root)
            commands = []
            def fake_run(command, **options):
                commands.append((command, options))
                if 'rev-parse' in command: return architecture.SOURCE_COMMIT
                if any(str(part).endswith('prepare_corpus.py') for part in command):
                    source = Path(command[command.index('--output') + 1]); source.mkdir()
                    (source / 'fixture.py').write_text('x = 1\n')
                if '/opt/qa/zg-provenance.json' in command: return json.dumps({'commit': ci.UPSTREAM_COMMIT})
                return ''
            with patch.object(ci, 'HERE', here), patch.object(ci, 'ROOT', root), \
                    patch.object(sys, 'argv', ci_arguments(root)), patch.dict(os.environ, ENV, clear=True), \
                    patch.object(ci, 'upstream_protocol', return_value=(remote.MODEL, '', {'index_ignore_sha256': 'fixture'})) as upstream, \
                    patch.object(ci, 'run', side_effect=fake_run), \
                    patch.object(remote, 'verify_index_identity', return_value={'verified': True}), \
                    patch.object(remote, 'scan_seed_credentials', return_value={'passed': True}):
                ci.main()
            upstream.assert_called_once_with(root / 'work', root / 'reports', remote=True, remote_plan_path=here / phase.PLAN_NAME)
            setup = json.loads((root / 'reports/setup.json').read_text())
            self.assertEqual(setup['phase'], phase.PHASE)
            self.assertEqual(setup['protocol_version'], phase.PROTOCOL)
            self.assertEqual(setup['answer_verification_plan']['sha256'], architecture.digest(here / phase.PLAN_NAME))
            self.assertNotIn('architecture_plan', setup)
            self.assertEqual(setup['planned_trials'], [{'repeat': 1, 'profile': 'baseline'}, {'repeat': 1, 'profile': 'zg'}])
            self.assertEqual((root / 'reports' / phase.SUFFIX_NAME).read_bytes(), (here / phase.SUFFIX_NAME).read_bytes())
            grant = next(i for i, (cmd, _) in enumerate(commands) if '--auth' in cmd and 'grant' in cmd)
            index = next(i for i, (cmd, _) in enumerate(commands) if '--index' in cmd)
            probe = next(i for i, (cmd, _) in enumerate(commands) if any(str(part).endswith('probe_mcp.py') for part in cmd))
            solver = next(i for i, (cmd, _) in enumerate(commands) if len(cmd) > 1 and str(cmd[1]).endswith('/run.py'))
            self.assertLess(grant, index); self.assertLess(index, probe); self.assertLess(probe, solver)
            self.assertEqual(commands[probe][1]['timeout'], 600)
            command, options = commands[solver]
            self.assertEqual(options['timeout'], 2000)
            for flag, value in {'--model': 'qwen3.8-max', '--embedding-model': remote.MODEL,
                                '--repeats': '1', '--max-turns': '30', '--timeout': '900',
                                '--max-output-tokens': '8192', '--effort': 'high',
                                '--tool-budget': '24', '--research-seconds': '600'}.items():
                self.assertEqual(command[command.index(flag) + 1], value)
            self.assertEqual(command.count('--answer-verification-pilot'), 1)
            self.assertNotIn('--architecture-pilot', command)


if __name__ == '__main__':
    unittest.main()
