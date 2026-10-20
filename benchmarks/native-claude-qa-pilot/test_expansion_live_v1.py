"""Focused prospective driver checks: synthetic approval, no provider or Docker."""
import copy
import inspect
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import expansion_live_v1 as driver
import test_expansion_identity_v1 as identity_tests
import test_live


class ExpansionLiveTests(unittest.TestCase):
    def setUp(self):
        self.identity_fixture = identity_tests.IdentityTests('runTest')
        self.identity_fixture.setUp(); self.addCleanup(self.identity_fixture.doCleanups)
        self.preparation = self.identity_fixture.fixture
        self.root, self.repo = self.preparation.root, self.preparation.repo
        self.args = SimpleNamespace(plan=self.root / 'synthetic-plan.json', task_id='q002',
            main_root=self.root / 'main', reference_prepared=self.preparation.reference,
            prepared_group=self.preparation.output, candidate=self.root / 'candidate.tgz',
            output=self.root / 'driver-run', command='run')
        base_path = self.repo / driver.BASE_PLAN
        base_path.parent.mkdir(parents=True)
        shutil.copyfile(driver.REPO / driver.BASE_PLAN, base_path)
        self.base = driver.live.pilot.load(base_path); self.proof = {'synthetic': True}
        self.plan = {'schema_version': 1, 'protocol_id': driver.PROTOCOL, 'frozen': True,
            'base_plan': {'path': driver.BASE_PLAN, 'sha256': driver.BASE_PLAN_SHA256},
            'dataset': {'path': driver.preparation.DATASET, 'sha256': driver.preparation.PINS[driver.preparation.DATASET]},
            'prepared_group': driver.prepared_reference(self.args.prepared_group),
            'tasks': copy.deepcopy(driver.TASKS), 'n_attempts': 1, 'max_retries': 0,
            'execution_workflow': driver.WORKFLOW, 'execution_decision': driver.DECISION,
            'grade_contract': driver.GRADE_CONTRACT, 'frozen_harness_sha256': {}}
        self.decision = {'protocol_id': driver.PROTOCOL, 'status': 'approved_for_two_fresh_task_pairs',
            'frozen': True, 'paid_execution_authorized': True,
            'base_plan_sha256': driver.BASE_PLAN_SHA256, 'n_attempts': 1, 'max_retries': 0,
            **{k: copy.deepcopy(self.plan[k]) for k in ('dataset', 'prepared_group', 'tasks')}}
        summary_path = driver.PREFIX + 'results/synthetic-confirmation/summary.json'
        driver.live.pilot.write(self.repo / summary_path, {'run_id': 39343873279, 'confirmation_success': False})
        self.decision['execution_prerequisites'] = {
            'current_q001_confirmation_run_id': 39343873279,
            'confirmation_conclusion': {'path': summary_path, 'sha256': driver.live.pilot.digest(self.repo / summary_path)},
            'upstream_verification': {'repo': 'zvec-ai/zvec-grep', 'ref': 'main', 'sha': driver.live.pilot.MAIN,
                                      'checked_at_utc': '2026-10-20T00:00:00Z'},
            'host_execution_decision': 'Synthetic host approval after reading the retained negative conclusion.'}
        driver.live.pilot.write(self.repo / driver.DECISION, self.decision)
        contract = {'frozen': True, 'dataset': self.plan['dataset'], 'task_ids': ['q002', 'q003'], 'inputs': {}}
        for task in ('q002', 'q003'):
            metadata = driver.live.pilot.load(self.args.prepared_group / task / 'prepared.json')
            contract['inputs'][task] = {key: {'path': 'benchmarks/zvec-issue-qa/' + metadata[key]['path'],
                                             'sha256': metadata[key]['sha256']} for key in ('question', 'rubric')}
        driver.live.pilot.write(self.repo / driver.GRADE_CONTRACT, contract)
        driver.live.pilot.write(self.repo / driver.WORKFLOW, {'synthetic': True})
        for relative in driver.CONTROLS - {driver.DECISION, driver.GRADE_CONTRACT, driver.WORKFLOW}:
            shutil.copyfile(driver.REPO / relative, self.repo / relative)
        self.pin()

    def pin(self):
        self.plan['frozen_harness_sha256'] = {p: driver.live.pilot.digest(self.repo / p) for p in driver.CONTROLS}
        driver.live.pilot.write(self.args.plan, self.plan)

    def validate(self):
        return driver.validate_plan(self.args.plan, self.args.task_id, self.args.main_root,
            self.args.reference_prepared, self.args.prepared_group, self.args.candidate, self.repo)

    def execute(self):
        return driver.execute(self.args, self.plan, self.base, self.proof, self.repo)

    def test_base_canonical_validation_precedes_task_selection_and_group_validation(self):
        def validate_base(*args):
            self.assertEqual(driver.live.PROFILES, ['baseline', 'zvec-grep'])
            self.assertEqual(args, (self.repo / driver.BASE_PLAN, self.args.main_root,
                self.args.reference_prepared, self.args.candidate, self.repo))
            return self.base, self.proof
        with patch.object(driver.live, 'validate_plan', side_effect=validate_base) as base:
            self.args.task_id = 'q003'
            self.assertEqual(self.validate(), (self.plan, self.base, self.proof))
            self.args.task_id = 'q001'
            with self.assertRaisesRegex(ValueError, 'undeclared_expansion_task'): self.validate()
            self.assertEqual(base.call_count, 2)

    def test_overrides_schedule_pins_and_draft_controls_rejected(self):
        original = copy.deepcopy(self.plan)
        mutations = [lambda p: p.update(provider={}), lambda p: p.update(frozen=False),
            lambda p: p.update(n_attempts=True), lambda p: p.update(max_retries=1),
            lambda p: p['tasks'][1].update(profiles=['baseline', 'zvec-grep']),
            lambda p: p['dataset'].update(sha256='0' * 64),
            lambda p: p['prepared_group']['metadata_sha256'].update(q002='0' * 64),
            lambda p: p.update(frozen_harness_sha256={})]
        with patch.object(driver.live, 'validate_plan', return_value=(self.base, self.proof)):
            for mutate in mutations:
                self.plan = copy.deepcopy(original); mutate(self.plan)
                driver.live.pilot.write(self.args.plan, self.plan)
                with self.subTest(mutate=mutate), self.assertRaises(ValueError): self.validate()
            self.plan = original
            contract = driver.live.pilot.load(self.repo / driver.GRADE_CONTRACT)
            contract['frozen'] = False; driver.live.pilot.write(self.repo / driver.GRADE_CONTRACT, contract); self.pin()
            with self.assertRaisesRegex(ValueError, 'grade_contract_not_frozen'): self.validate()
            contract['frozen'] = True; driver.live.pilot.write(self.repo / driver.GRADE_CONTRACT, contract)
            self.decision['status'] = 'draft'; driver.live.pilot.write(self.repo / driver.DECISION, self.decision); self.pin()
            with self.assertRaisesRegex(ValueError, 'decision_mismatch'): self.validate()

    def test_private_runtime_function_bodies_and_globals_are_preserved(self):
        for task in ('q002', 'q003'):
            module = driver.private_live(task)
            self.assertEqual(module.PROTOCOL, driver.PROTOCOL)
            self.assertEqual(module.PROFILES, driver.task_profiles(task))
            for name, original in vars(driver.live).items():
                if inspect.isfunction(original) and original.__module__ == driver.live.__name__:
                    observed = getattr(module, name)
                    self.assertEqual(inspect.getsource(observed), inspect.getsource(original), name)
                    self.assertEqual(inspect.unwrap(observed).__code__, inspect.unwrap(original).__code__, name)
        self.assertEqual(driver.live.PROFILES, ['baseline', 'zvec-grep'])
        self.assertEqual(driver.live.PROTOCOL, 'native-claude-q001-live-v2')

    def test_approval_requires_frozen_paid_and_bound_prerequisites_not_positive_outcome(self):
        original = copy.deepcopy(self.decision)
        mutations = [lambda d: d.pop('frozen'), lambda d: d.update(paid_execution_authorized=False),
            lambda d: d.pop('execution_prerequisites'),
            lambda d: d['execution_prerequisites'].update(current_q001_confirmation_run_id=39338284225),
            lambda d: d['execution_prerequisites'].update(confirmation_conclusion=None),
            lambda d: d['execution_prerequisites']['confirmation_conclusion'].update(sha256='0' * 64),
            lambda d: d['execution_prerequisites']['confirmation_conclusion'].update(path='/outside/summary.json'),
            lambda d: d['execution_prerequisites']['upstream_verification'].update(repo='Cuiyus/zvec-grep'),
            lambda d: d['execution_prerequisites']['upstream_verification'].update(ref='other'),
            lambda d: d['execution_prerequisites']['upstream_verification'].update(sha='0' * 40),
            lambda d: d['execution_prerequisites']['upstream_verification'].update(checked_at_utc=' '),
            lambda d: d['execution_prerequisites'].update(host_execution_decision='')]
        with patch.object(driver.live, 'validate_plan', return_value=(self.base, self.proof)):
            # A negative conclusion is acceptable: it is a prerequisite, not a success filter.
            self.assertEqual(self.validate(), (self.plan, self.base, self.proof))
            for mutate in mutations:
                self.decision = copy.deepcopy(original); mutate(self.decision)
                driver.live.pilot.write(self.repo / driver.DECISION, self.decision); self.pin()
                with self.subTest(mutate=mutate), self.assertRaises(ValueError): self.validate()

    def test_actual_dry_run_selects_reserve_and_reverse_profiles_without_provider(self):
        self.args.task_id, self.args.command = 'q003', 'dry-run'
        module = driver.private_live('q003')
        selected = self.args.prepared_group / 'q003'
        def suite(path):
            self.assertEqual(path, selected / 'suite.yaml')
            self.assertIn('tasks: [zvec-q003]', path.read_text())
            return SimpleNamespace(tasks=('zvec-q003',))
        def command(runner, suite, profile, prepared, *_):
            self.assertEqual(prepared, selected)
            return ['harbor', '--include-task-name', suite.tasks[0], '--profile', profile]
        with patch.object(driver, 'private_live', return_value=module), \
             patch.object(module.pilot, 'native', return_value=SimpleNamespace(load_suite=suite)), \
             patch.object(module, 'build_command', side_effect=command), \
             patch.object(module, 'run_slot', side_effect=AssertionError('no slot execution')), \
             patch.object(module, 'docker_info', side_effect=AssertionError('no Docker')), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.execute(), 0)
            with self.assertRaises(FileExistsError): self.execute()
        execution = driver.live.pilot.load(self.args.output / 'execution.json')
        self.assertEqual(execution['protocol_id'], driver.PROTOCOL)
        self.assertEqual([s['profile'] for s in execution['slots']], ['zvec-grep', 'baseline'])
        context = driver.live.pilot.load(driver.context_path(self.args.output))
        self.assertEqual(context['recorded_prepared_root'], selected.resolve().as_posix())
        self.args.command = 'collect'
        with self.assertRaisesRegex(ValueError, 'context_missing_or_mismatched'): self.execute()

    def combined_fixture(self):
        # Join the existing identity and native collector fixtures. Only test
        # evidence is changed; production never relabels an earlier phase.
        fixture = test_live.LiveTests('runTest'); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.proof = fixture.collection_fixture()
        self.args.output = self.identity_fixture.output
        shutil.rmtree(self.args.output / 'collection')
        native = driver.live.pilot.load(fixture.args.output / 'execution.json')
        bound = driver.live.pilot.load(self.args.output / 'execution.json')
        for native_slot, bound_slot in zip(native['slots'], bound['slots']):
            profile = native_slot['profile']
            source = fixture.args.output / 'runs' / ('q001-' + profile) / 'trial'
            target = self.args.output / 'runs' / ('q001-' + profile) / 'zvec-q002__fixture'
            shutil.rmtree(target / 'agent')
            for name in ('agent', 'qa-native-claude-logs'): shutil.copytree(source / name, target / name)
            native_audit = driver.live.pilot.load(source / 'qa-source-audit.json')
            audit = driver.live.pilot.load(target / 'qa-source-audit.json')
            audit.update(valid=True, native_run_wall_seconds=12)
            for phase in ('before', 'after'):
                audit[phase]['observed']['runtime'] = native_audit[phase]['observed']['runtime']
            driver.live.pilot.write(target / 'qa-source-audit.json', audit)
            native_slot['command'] = bound_slot['command']
            driver.live.pilot.write(self.args.output / (profile + '-slot.json'), native_slot)
            shutil.copyfile(fixture.args.output / (profile + '-containers.json'), self.args.output / (profile + '-containers.json'))
        native.update(protocol_id=driver.PROTOCOL, plan_sha256=driver.live.pilot.digest(self.args.plan),
                      offline_proof_sha256=self.base['offline_proof']['proof_sha256'])
        driver.live.pilot.write(self.args.output / 'execution.json', native)
        self.args.command = 'collect'
        context = driver.context_record(self.args, self.plan, self.base, dry_run=False,
                                       recorded_prepared_root=self.identity_fixture.remote)
        driver.live.pilot.write(driver.context_path(self.args.output), context)

    def test_actual_collect_and_identity_joint_gate_keep_remote_context(self):
        self.combined_fixture()
        before = driver.live.pilot.digest(self.args.output / 'execution.json')
        self.assertEqual(self.execute(), 0)
        report = driver.live.pilot.load(self.args.output / 'task-identity.json')
        self.assertTrue(report['joint_gate_valid'], report['errors'])
        self.assertEqual(report['recorded_prepared_root'], self.identity_fixture.remote)
        self.assertNotEqual(report['recorded_prepared_root'], str(self.args.prepared_group / 'q002'))
        self.assertEqual(before, driver.live.pilot.digest(self.args.output / 'execution.json'))
        self.assertFalse(driver.live.pilot.load(self.args.output / 'collection/collection.json')['favorable_conclusion_allowed'])
        with self.assertRaises(FileExistsError): self.execute()
        context = driver.live.pilot.load(driver.context_path(self.args.output)); context['task_id'] = 'q003'
        driver.live.pilot.write(driver.context_path(self.args.output), context)
        with self.assertRaisesRegex(ValueError, 'context_missing_or_mismatched'): self.execute()

    def test_original_structural_success_cannot_override_task_identity_failure(self):
        self.combined_fixture()
        path = self.args.output / 'runs/q001-baseline/zvec-q002__fixture/lock.json'
        lock = driver.live.pilot.load(path); lock['task']['digest'] = 'sha256:' + '0' * 64
        driver.live.pilot.write(path, lock)
        self.assertEqual(self.execute(), 1)
        report = driver.live.pilot.load(self.args.output / 'task-identity.json')
        self.assertTrue(report['native_structural_valid']); self.assertFalse(report['task_identity_valid'])
        self.assertFalse(report['joint_gate_valid'])

    def test_partial_failure_before_slots_is_retained_and_collect_returns_failure(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, 'missing_credential'):
            self.execute()
        self.assertTrue(driver.context_path(self.args.output).is_file()); self.assertFalse(self.args.output.exists())
        with self.assertRaises(FileExistsError): self.execute()
        self.args.command = 'collect'
        self.assertEqual(self.execute(), 1)
        report = driver.live.pilot.load(self.args.output / 'task-identity.json')
        self.assertFalse(report['joint_gate_valid']); self.assertFalse(report['native_collection_completed'])
        self.assertEqual(report['native_collection_error_type'], 'FileNotFoundError')
        self.assertEqual(len(report['profiles']), 2); self.assertTrue(report['errors'])

    def test_cleanup_uncertainty_stops_reverse_pair_without_replacement(self):
        self.args.task_id = 'q003'
        module = driver.private_live('q003')
        failed = {'profile': 'zvec-grep', 'status': 'exited', 'returncode': 0,
                  'process_cleanup': {'complete': False}, 'container_cleanup': {'complete': True}}
        with patch.object(driver, 'private_live', return_value=module), \
             patch.object(module, 'docker_info', return_value={}), \
             patch.object(module, 'run_slot', return_value=failed) as slot, \
             patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'synthetic', 'ZVEC_GREP_API_KEY': 'synthetic'}, clear=True):
            self.assertEqual(self.execute(), 1)
        self.assertEqual(slot.call_count, 1)
        execution = driver.live.pilot.load(self.args.output / 'execution.json')
        self.assertEqual([s['profile'] for s in execution['slots']], ['zvec-grep', 'baseline'])
        self.assertEqual(execution['slots'][1]['status'], 'not_started')
        self.args.command = 'collect'
        self.assertEqual(self.execute(), 1)
        report = driver.live.pilot.load(self.args.output / 'task-identity.json')
        self.assertFalse(report['joint_gate_valid']); self.assertEqual(len(report['profiles']), 2)


if __name__ == '__main__':
    unittest.main()
