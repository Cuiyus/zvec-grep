"""No network, Docker or inference: independent architecture phase contracts."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import architecture_phase as phase
import ci_run as ci
import remote_embedding as remote

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORKFLOW = ROOT / '.github/workflows/claude-qa-pilot.yml'
ENV = {'ZVEC_GREP_API_KEY': 'fixture-embedding-key-not-real', 'ZVEC_GREP_ENDPOINT': remote.ENDPOINT}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')


def reference(root, path):
    return {'path': str(path.relative_to(root)), 'sha256': phase.digest(path)}


def fixture(root, *, frozen=True):
    here = root / 'benchmarks/claude-qa-pilot'
    here.mkdir(parents=True)
    base = json.loads((HERE / remote.PLAN_NAME).read_text())
    base_path = here / remote.PLAN_NAME
    write_json(base_path, base)
    hook = here / 'tool_budget.py'
    hook.write_bytes((HERE / hook.name).read_bytes())
    dataset_path = root / phase.DATASET_PATH
    question = dataset_path.parent / 'tasks/q004/instruction.md'
    question.parent.mkdir(parents=True)
    question.write_text('Explain the architecture from source evidence.\n')
    rubric = dataset_path.parent / 'evaluator/q004.json'
    write_json(rubric, {'task_id': 'q004', 'source_commit': phase.SOURCE_COMMIT, 'evidence_spans': []})
    task = {'id': 'q004', 'source_repository': 'https://github.com/alibaba/zvec.git',
            'source_commit': phase.SOURCE_COMMIT, 'instruction': 'tasks/q004/instruction.md',
            'instruction_sha256': phase.digest(question), 'evaluator': 'evaluator/q004.json',
            'evaluator_sha256': phase.digest(rubric)}
    write_json(dataset_path, {'independent_task_count': 1, 'tasks': [task]})
    plan = {key: copy.deepcopy(base[key]) for key in
            ('agent', 'completion_control', 'retrieval', 'preflight', 'accounting', 'common_instructions_sha256')}
    plan.update({'experiment_id': 'claude-qwen-rust-zvec-architecture-v1',
                 'protocol_version': 'completion-v2', 'phase': 'architecture-development', 'frozen': frozen,
                 'base_experiment': reference(root, base_path), 'completion_hook': reference(root, hook),
                 'dataset': reference(root, dataset_path), 'task': {'id': 'zvec-q004',
                 'repository': 'alibaba/zvec', 'revision': phase.SOURCE_COMMIT,
                 'question': reference(root, question), 'rubric': reference(root, rubric)},
                 'run_plan': {'inputs': dict(phase.INPUTS), 'repeats': 1, 'start_repeat': 1,
                              'trial_count': 2, 'order': ['baseline', 'zg'],
                              'automatic_repeats': False, 'automatic_task_expansion': False,
                              'bad_outcome_replacement': False, 'job_timeout_minutes': 45,
                              'runner_timeout_seconds': 2000}})
    write_json(here / phase.PLAN_NAME, plan)
    return here, plan, dataset_path


def arguments(root, task='zvec-q004', *, architecture=True, root_guidance=False,
              budget=True, confirmation=False, expansion=False, remote_flag=False, repeats=1):
    values = ['ci_run.py', '--task', task, '--work', str(root / 'work'),
              '--reports', str(root / 'reports'), '--repeats', str(repeats)]
    for flag, enabled in (('--architecture-pilot', architecture), ('--workspace-root-guidance', root_guidance),
                          ('--completion-budget', budget), ('--completion-confirmation', confirmation),
                          ('--closed-issue-expansion', expansion), ('--remote-embedding-pilot', remote_flag)):
        if enabled:
            values.append(flag)
    return values


def workflow_script(text):
    script = text.split("          python3 - <<'PY'\n", 1)[1].split('          PY\n', 1)[0]
    return '\n'.join(line[10:] for line in script.splitlines())


class ArchitectureTests(unittest.TestCase):
    def test_frozen_independent_plan_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            here, plan, dataset = fixture(root)
            actual = phase.validate_plan(root, here)
            self.assertEqual(actual['sha256'], phase.digest(here / phase.PLAN_NAME))
            self.assertEqual(actual['dataset_sha256'], phase.digest(dataset))
            # The new primary endpoint does not inherit the old joint efficiency criterion.
            plan['evaluation'] = {'primary': 'source_grounded_success', 'resources': 'reported separately'}
            write_json(here / phase.PLAN_NAME, plan)
            phase.validate_plan(root, here)

    def test_plan_and_asset_drift_rejected(self):
        mutations = {
            'unfrozen': lambda p: p.update(frozen=False),
            'phase': lambda p: p.update(phase='remote-embedding-development'),
            'protocol': lambda p: p.update(protocol_version='v1'),
            'task': lambda p: p['task'].update(id='zvec-q001'),
            'source': lambda p: p['task'].update(revision='0' * 40),
            'agent': lambda p: p['agent'].update(model='fallback'),
            'budget': lambda p: p['completion_control'].update(tool_budget=25),
            'embedding': lambda p: p['retrieval'].update(embedding_model='local/potion-code-16m-v2'),
            'preflight': lambda p: p['preflight'].update(outer_timeout_seconds=450),
            'accounting': lambda p: p['accounting'].update(agent='subtract query waits'),
            'instructions': lambda p: p.update(common_instructions_sha256='drift'),
            'repeat': lambda p: p['run_plan'].update(repeats=2),
            'start': lambda p: p['run_plan'].update(start_repeat=2),
            'count': lambda p: p['run_plan'].update(trial_count=4),
            'order': lambda p: p['run_plan'].update(order=['zg', 'baseline']),
            'auto_repeat': lambda p: p['run_plan'].update(automatic_repeats=True),
            'auto_expand': lambda p: p['run_plan'].update(automatic_task_expansion=True),
            'replace': lambda p: p['run_plan'].update(bad_outcome_replacement=True),
            'inputs': lambda p: p['run_plan']['inputs'].update(remote_embedding_pilot=True),
            'base_path': lambda p: p['base_experiment'].update(path='../outside'),
            'dataset_hash': lambda p: p['dataset'].update(sha256='drift'),
            'question_hash': lambda p: p['task']['question'].update(sha256='drift'),
            'rubric_hash': lambda p: p['task']['rubric'].update(sha256='drift'),
            'hook_hash': lambda p: p['completion_hook'].update(sha256='drift'),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                here, plan, _ = fixture(root)
                mutate(plan)
                write_json(here / phase.PLAN_NAME, plan)
                with self.assertRaises(ValueError):
                    phase.validate_plan(root, here)

    def test_dataset_identity_and_asset_paths_rejected_even_with_updated_dataset_hash(self):
        for mode in ('extra_task', 'wrong_task', 'wrong_source', 'wrong_repository', 'question_path', 'rubric_path', 'question_hash'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                here, plan, path = fixture(root)
                dataset = json.loads(path.read_text())
                task = dataset['tasks'][0]
                if mode == 'extra_task': dataset['tasks'].append(dict(task))
                elif mode == 'wrong_task': task['id'] = 'q001'
                elif mode == 'wrong_source': task['source_commit'] = '0' * 40
                elif mode == 'wrong_repository': task['source_repository'] = 'https://fixture.invalid'
                elif mode == 'question_path': task['instruction'] = '../outside'
                elif mode == 'rubric_path': task['evaluator'] = '/tmp/outside'
                elif mode == 'question_hash': task['instruction_sha256'] = 'drift'
                write_json(path, dataset)
                plan['dataset'] = reference(root, path)
                write_json(here / phase.PLAN_NAME, plan)
                with self.assertRaises(ValueError): phase.validate_plan(root, here)

    def test_workflow_selection_requires_exact_new_phase_without_changing_old_selection(self):
        script = workflow_script(WORKFLOW.read_text())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            for task, root_flag, budget, confirmation, expansion, remote_flag in itertools.product(
                    ('both', 'reflex-6', 'zvec-q001', 'zvec-expansion', 'zvec-q004'), *[(False, True)] * 5):
                env = {'PILOT_TASK': task, 'PILOT_ROOT_GUIDANCE': str(root_flag).lower(),
                       'PILOT_COMPLETION_BUDGET': str(budget).lower(),
                       'PILOT_COMPLETION_CONFIRMATION': str(confirmation).lower(),
                       'PILOT_ISSUE_EXPANSION': str(expansion).lower(),
                       'PILOT_REMOTE_EMBEDDING': str(remote_flag).lower(),
                       'PILOT_ARCHITECTURE': 'true', 'GITHUB_OUTPUT': str(output)}
                allowed = (task, root_flag, budget, confirmation, expansion, remote_flag) == ('zvec-q004', False, True, False, False, False)
                with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                    output.write_text('')
                    if allowed:
                        exec(script, {})
                        self.assertEqual(json.loads(output.read_text().split('=', 1)[1]), ['zvec-q004'])
                    else:
                        with self.assertRaises(SystemExit): exec(script, {})
                        self.assertEqual(output.read_text(), '')
            env.update(PILOT_TASK='zvec-q004', PILOT_ARCHITECTURE='false', PILOT_ROOT_GUIDANCE='false',
                       PILOT_COMPLETION_BUDGET='true', PILOT_COMPLETION_CONFIRMATION='false',
                       PILOT_ISSUE_EXPANSION='false', PILOT_REMOTE_EMBEDDING='false')
            with patch.dict(os.environ, env, clear=True), self.assertRaises(SystemExit): exec(script, {})
            env.update(PILOT_TASK='both', PILOT_COMPLETION_BUDGET='false')
            with patch.dict(os.environ, env, clear=True):
                output.write_text(''); exec(script, {})
                self.assertEqual(json.loads(output.read_text().split('=', 1)[1]), ['reflex-6', 'zvec-q001'])

    def test_ci_gate_rejects_nonexclusive_phase_before_setup(self):
        class ReachedGate(Exception): pass
        for task, root_flag, budget, confirmation, expansion, remote_flag, repeats in itertools.product(
                ('reflex-6', 'zvec-q001', 'zvec-q002', 'zvec-q003', 'zvec-q004'), *[(False, True)] * 5, (1, 2)):
            allowed = (task, root_flag, budget, confirmation, expansion, remote_flag, repeats) == ('zvec-q004', False, True, False, False, False, 1)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                argv = arguments(root, task, root_guidance=root_flag, budget=budget,
                                 confirmation=confirmation, expansion=expansion, remote_flag=remote_flag, repeats=repeats)
                with self.subTest(argv=argv), patch.object(sys, 'argv', argv), \
                        patch.object(ci, 'validate_architecture_plan', side_effect=ReachedGate) as gate, \
                        patch.object(ci, 'upstream_protocol') as upstream, contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(ReachedGate if allowed else SystemExit): ci.main()
                    self.assertEqual(gate.called, allowed)
                    upstream.assert_not_called()
                    self.assertFalse((root / 'work').exists())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(sys, 'argv', arguments(root, architecture=False)), \
                    patch.object(ci, 'upstream_protocol') as upstream, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit): ci.main()
                upstream.assert_not_called()

    def test_shared_plan_gate_runs_before_provider_and_setup(self):
        workflow = WORKFLOW.read_text()
        label = '      - name: Verify architecture plan and environment before provider calls\n'
        self.assertLess(workflow.index(label), workflow.index('      - name: Verify Qwen Anthropic'))
        step = workflow.split(label, 1)[1].split('      - name:', 1)[0]
        self.assertIn('if: inputs.architecture_pilot', step)
        script = workflow_script(step)
        for frozen in (False, True):
            with self.subTest(frozen=frozen), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                here, _, _ = fixture(root, frozen=frozen)
                with patch.object(ci, 'HERE', here), patch.object(ci, 'ROOT', root), \
                        patch.dict(os.environ, ENV, clear=True), patch.object(sys, 'path', list(sys.path)):
                    if frozen: exec(script, {})
                    else:
                        with self.assertRaises(ValueError): exec(script, {})
                        with patch.object(sys, 'argv', arguments(root)), patch.object(ci, 'run') as run, \
                                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): ci.main()
                        run.assert_not_called()
                        self.assertFalse((root / 'work').exists())
        self.assertIn('(inputs.remote_embedding_pilot || inputs.architecture_pilot) && secrets.QWEN_EMBEDDING_API_KEY', workflow)
        self.assertIn("inputs.architecture_pilot && '--architecture-pilot'", workflow)
        self.assertEqual(workflow.count("github.repository == 'Cuiyus/zvec-grep'"), 2)

    def test_upstream_configuration_checks_use_the_new_plan_explicitly(self):
        config = json.dumps({'embedding_models': {'local': 'local/potion-code-16m-v2', 'remote': remote.MODEL}})
        source = {'path': ci.UPSTREAM_CONFIG_PATH, 'commit': ci.UPSTREAM_COMMIT,
                  'sha256': hashlib.sha256(config.encode()).hexdigest()}
        for drift in (False, True):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                here, plan, _ = fixture(root)
                entry = dict(source)
                if drift: entry['sha256'] = 'drift'
                plan['retrieval']['configuration_sources'] = [entry]
                write_json(here / phase.PLAN_NAME, plan)
                def fake_run(command, **options):
                    if 'rev-parse' in command: return ci.UPSTREAM_COMMIT
                    if 'show' in command:
                        return config if command[-1].endswith(ci.UPSTREAM_CONFIG_PATH) else '*.fixture\n'
                    return ''
                reports = root / 'reports'; reports.mkdir()
                with patch.object(ci, 'HERE', here), patch.object(ci, 'run', side_effect=fake_run):
                    if drift:
                        with self.assertRaises(ValueError):
                            ci.upstream_protocol(root / 'work', reports, remote=True, remote_plan_path=here / phase.PLAN_NAME)
                    else:
                        model, _, provenance = ci.upstream_protocol(root / 'work', reports, remote=True,
                                                                   remote_plan_path=here / phase.PLAN_NAME)
                        self.assertEqual(model, remote.MODEL)
                        self.assertEqual(provenance['remote_embedding_configuration_sources'], [source])

    def test_wrapper_reuses_remote_setup_and_unchanged_pair_with_new_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            here, plan, dataset = fixture(root)
            commands = []
            def fake_run(command, **options):
                commands.append((command, options))
                if 'rev-parse' in command: return phase.SOURCE_COMMIT
                if any(str(part).endswith('prepare_corpus.py') for part in command):
                    source = Path(command[command.index('--output') + 1]); source.mkdir()
                    (source / 'fixture.py').write_text('x = 1\n')
                if '/opt/qa/zg-provenance.json' in command: return json.dumps({'commit': ci.UPSTREAM_COMMIT})
                return ''
            with patch.object(ci, 'HERE', here), patch.object(ci, 'ROOT', root), \
                    patch.object(sys, 'argv', arguments(root)), patch.dict(os.environ, ENV, clear=True), \
                    patch.object(ci, 'upstream_protocol', return_value=(remote.MODEL, '', {'index_ignore_sha256': 'fixture'})) as upstream, \
                    patch.object(ci, 'run', side_effect=fake_run), \
                    patch.object(remote, 'verify_index_identity', return_value={'verified': True}), \
                    patch.object(remote, 'scan_seed_credentials', return_value={'passed': True}):
                ci.main()
            upstream.assert_called_once_with(root / 'work', root / 'reports', remote=True, remote_plan_path=here / phase.PLAN_NAME)
            setup = json.loads((root / 'reports/setup.json').read_text())
            self.assertEqual(setup['phase'], 'architecture-development')
            self.assertEqual(setup['revision'], phase.SOURCE_COMMIT)
            self.assertEqual(setup['planned_trials'], [{'repeat': 1, 'profile': 'baseline'}, {'repeat': 1, 'profile': 'zg'}])
            self.assertEqual(setup['planned_trial_count'], 2)
            self.assertEqual(setup['embedding_runtime'], 'remote')
            self.assertEqual(setup['embedding_model'], remote.MODEL)
            self.assertEqual(setup['embedding_endpoint'], remote.ENDPOINT)
            self.assertEqual(setup['embedding_accounting'], plan['accounting'])
            self.assertNotIn('remote_embedding_plan', setup)
            self.assertEqual(setup['architecture_plan']['sha256'], phase.digest(here / phase.PLAN_NAME))
            self.assertEqual((root / 'reports/experiment-plan.json').read_bytes(), (here / phase.PLAN_NAME).read_bytes())
            self.assertEqual((root / 'reports/dataset-manifest.json').read_bytes(), dataset.read_bytes())
            self.assertTrue(json.loads((root / 'reports/collection-secret-scan.json').read_text())['passed'])
            export, _ = next(item for item in commands if any(str(part).endswith('prepare_corpus.py') for part in item[0]))
            self.assertEqual(export[export.index('--dataset') + 1], str(dataset))
            self.assertEqual(export[export.index('--task') + 1], 'q004')
            grant = next(index for index, (cmd, _) in enumerate(commands) if '--auth' in cmd and 'grant' in cmd)
            index = next(index for index, (cmd, _) in enumerate(commands) if '--index' in cmd)
            probe = next(index for index, (cmd, _) in enumerate(commands) if any(str(part).endswith('probe_mcp.py') for part in cmd))
            runner = next(index for index, (cmd, _) in enumerate(commands) if len(cmd) > 1 and str(cmd[1]).endswith('/run.py'))
            self.assertLess(grant, index); self.assertLess(index, probe); self.assertLess(probe, runner)
            for position in (grant, index):
                command = commands[position][0]
                self.assertIn('ZVEC_GREP_API_KEY', command)
                self.assertNotIn(ENV['ZVEC_GREP_API_KEY'], command)
            probe_command, options = commands[probe]
            self.assertEqual(options['timeout'], 600)
            self.assertEqual(probe_command[probe_command.index('--embedding-model') + 1], remote.MODEL)
            command, options = commands[runner]
            self.assertEqual(options['timeout'], 2000)
            expected = {'--prompt': str(root / plan['task']['question']['path']), '--model': 'qwen3.8-max',
                        '--embedding-model': remote.MODEL, '--repeats': '1', '--max-turns': '30',
                        '--timeout': '900', '--max-output-tokens': '8192', '--effort': 'high',
                        '--tool-budget': '24', '--research-seconds': '600',
                        '--mcp-args-json': '["--server","--stdio","--mcp-toolset","agent"]'}
            for flag, value in expected.items(): self.assertEqual(command[command.index(flag) + 1], value)
            self.assertNotIn('--workspace-root-guidance', command)
            self.assertNotIn('--start-repeat', command)


if __name__ == '__main__':
    unittest.main()
