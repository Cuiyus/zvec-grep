"""Focused confirmation wrapper checks: no Docker or provider calls."""
from __future__ import annotations

import copy
import inspect
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import confirmation_v1 as confirmation
import live
import test_live


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = SimpleNamespace(plan=self.root / 'confirmation.json', pair='c01',
            main_root=self.root / 'main', prepared=self.root / 'prepared',
            candidate=self.root / 'package.tgz', output=self.root / 'run', command='run')
        base_path = self.root / confirmation.BASE_PLAN
        base_path.parent.mkdir(parents=True)
        shutil.copyfile(confirmation.REPO / confirmation.BASE_PLAN, base_path)
        self.base = live.pilot.load(base_path)
        self.proof = {'retained': 'test proof'}
        self.plan = {
            'schema_version': 1, 'protocol_id': confirmation.PROTOCOL, 'frozen': True,
            'base_plan': {'path': confirmation.BASE_PLAN, 'sha256': confirmation.BASE_PLAN_SHA256},
            'development_summary': {'path': confirmation.DEVELOPMENT_SUMMARY, 'sha256': ''},
            'pairs': copy.deepcopy(confirmation.PAIRS), 'n_attempts': 1, 'max_retries': 0,
            'execution_workflow': confirmation.WORKFLOW, 'execution_decision': confirmation.DECISION,
            'grade_contract': confirmation.GRADE_CONTRACT, 'frozen_harness_sha256': {},
        }
        summary = {'protocol_id': live.PROTOCOL, 'plan_sha256': confirmation.BASE_PLAN_SHA256,
                   'evaluation': {'reported_input_development_candidate': True}}
        live.pilot.write(self.root / confirmation.DEVELOPMENT_SUMMARY, summary)
        self.plan['development_summary']['sha256'] = live.pilot.digest(self.root / confirmation.DEVELOPMENT_SUMMARY)
        self.decision = {'protocol_id': confirmation.PROTOCOL, 'status': 'approved_for_two_fresh_pairs',
                         'base_plan_sha256': confirmation.BASE_PLAN_SHA256, 'pairs': copy.deepcopy(confirmation.PAIRS),
                         'n_attempts': 1, 'max_retries': 0}
        live.pilot.write(self.root / confirmation.DECISION, self.decision)
        for name in ('confirmation_v1.py', 'test_confirmation_v1.py'):
            shutil.copyfile(confirmation.HERE / name, self.root / confirmation.PREFIX / name)
        for path in (confirmation.WORKFLOW, confirmation.GRADE_CONTRACT):
            live.pilot.write(self.root / path, {'fixture': True})
        self.pin_controls()

    def pin_controls(self):
        required = [confirmation.PREFIX + name for name in ('confirmation_v1.py', 'test_confirmation_v1.py')]
        required += [confirmation.WORKFLOW, confirmation.DECISION, confirmation.GRADE_CONTRACT]
        self.plan['frozen_harness_sha256'] = {p: live.pilot.digest(self.root / p) for p in required}
        live.pilot.write(self.args.plan, self.plan)

    def validate(self):
        return confirmation.validate_plan(self.args.plan, self.args.pair, self.args.main_root,
            self.args.prepared, self.args.candidate, self.root)

    def test_original_plan_validation_precedes_new_protocol_and_pair_selection(self):
        with patch.object(live, 'validate_plan', return_value=(self.base, self.proof)) as validate:
            self.assertEqual(self.validate(), (self.plan, self.base, self.proof))
            validate.assert_called_once_with(self.root / confirmation.BASE_PLAN, self.args.main_root,
                self.args.prepared, self.args.candidate, self.root)
            self.args.pair = 'c03'
            with self.assertRaisesRegex(ValueError, 'undeclared_confirmation_pair'):
                self.validate()
            self.assertEqual(validate.call_count, 2)

    def test_references_controls_and_config_overrides_fail_closed(self):
        original = copy.deepcopy(self.plan)
        mutations = [
            lambda p: p.update(provider={'base_url': 'https://wrong.invalid'}),
            lambda p: p.update(agent={'version': 'other'}),
            lambda p: p.update(pairs=list(reversed(p['pairs']))),
            lambda p: p['pairs'][1].update(profiles=['baseline', 'zvec-grep']),
            lambda p: p.update(n_attempts=2), lambda p: p.update(max_retries=1),
            lambda p: p.update(n_attempts=True),
            lambda p: p['base_plan'].update(sha256='0' * 64),
            lambda p: p['development_summary'].update(sha256='0' * 64),
            lambda p: p.update(frozen_harness_sha256={}),
            lambda p: p['frozen_harness_sha256'].update({confirmation.GRADE_CONTRACT: '0' * 64}),
        ]
        with patch.object(live, 'validate_plan', return_value=(self.base, self.proof)):
            for mutate in mutations:
                with self.subTest(mutation=mutate):
                    self.plan = copy.deepcopy(original); mutate(self.plan)
                    live.pilot.write(self.args.plan, self.plan)
                    with self.assertRaises(ValueError): self.validate()
        base_path = self.root / confirmation.BASE_PLAN
        base_path.write_bytes(base_path.read_bytes() + b'\n')
        with patch.object(live, 'validate_plan') as validate, self.assertRaisesRegex(ValueError, 'frozen_base_plan_changed'):
            self.validate()
        validate.assert_not_called()

    def test_decision_exact_schedule_and_development_candidate_required(self):
        self.decision['pairs'][1]['profiles'] = ['baseline', 'zvec-grep']
        live.pilot.write(self.root / confirmation.DECISION, self.decision); self.pin_controls()
        with patch.object(live, 'validate_plan', return_value=(self.base, self.proof)), \
                self.assertRaisesRegex(ValueError, 'confirmation_decision_mismatch'):
            self.validate()
        summary = live.pilot.load(self.root / confirmation.DEVELOPMENT_SUMMARY)
        summary['evaluation']['reported_input_development_candidate'] = False
        live.pilot.write(self.root / confirmation.DEVELOPMENT_SUMMARY, summary)
        self.plan['development_summary']['sha256'] = live.pilot.digest(self.root / confirmation.DEVELOPMENT_SUMMARY)
        live.pilot.write(self.args.plan, self.plan)
        with patch.object(live, 'validate_plan', return_value=(self.base, self.proof)), \
                self.assertRaisesRegex(ValueError, 'accepted_development_candidate_required'):
            self.validate()

    def test_private_instances_preserve_every_original_function_body(self):
        instances = [confirmation.private_live(pair) for pair in ('c01', 'c02')]
        for module in instances:
            self.assertEqual(module.PROTOCOL, confirmation.PROTOCOL)
            for name, fn in vars(live).items():
                if inspect.isfunction(fn) and fn.__module__ == live.__name__:
                    other = getattr(module, name)
                    self.assertEqual(inspect.getsource(other), inspect.getsource(fn), name)
                    self.assertEqual(inspect.unwrap(other).__code__, inspect.unwrap(fn).__code__, name)
                    self.assertIs(inspect.unwrap(other).__globals__, vars(module))
        self.assertEqual(instances[0].PROFILES, ['baseline', 'zvec-grep'])
        self.assertEqual(instances[1].PROFILES, ['zvec-grep', 'baseline'])
        self.assertEqual(live.PROTOCOL, 'native-claude-q001-live-v2')
        self.assertEqual(live.PROFILES, ['baseline', 'zvec-grep'])

    def test_dry_run_is_provider_free_records_new_identity_and_refuses_replacement(self):
        self.args.command, self.args.pair = 'dry-run', 'c02'
        module = confirmation.private_live('c02')
        runner = SimpleNamespace(load_suite=lambda _: object())
        def command(*args): return ['harbor', args[2]]
        with patch.object(confirmation, 'private_live', return_value=module), \
                patch.object(module.pilot, 'native', return_value=runner), \
                patch.object(module, 'build_command', side_effect=command), \
                patch.object(module, 'run_slot', side_effect=AssertionError('must not execute')), \
                patch.object(module, 'docker_info', side_effect=AssertionError('must not call Docker')), \
                patch.dict(os.environ, {}, clear=True):
            self.assertEqual(confirmation.execute(self.args, self.plan, self.base, self.proof), 0)
            with self.assertRaises(FileExistsError):
                confirmation.execute(self.args, self.plan, self.base, self.proof)
        record = live.pilot.load(self.args.output / 'execution.json')
        self.assertEqual(record['protocol_id'], confirmation.PROTOCOL)
        self.assertEqual(record['plan_sha256'], live.pilot.digest(self.args.plan))
        self.assertEqual([s['profile'] for s in record['slots']], ['zvec-grep', 'baseline'])
        context = live.pilot.load(confirmation.context_path(self.args.output))
        self.assertEqual(context['pair_id'], 'c02'); self.assertTrue(context['dry_run'])
        self.args.command = 'collect'
        with self.assertRaisesRegex(ValueError, 'confirmation_context_missing_or_mismatched'):
            confirmation.execute(self.args, self.plan, self.base, self.proof)

    def test_cleanup_uncertainty_stops_reverse_pair_and_run_returns_nonzero(self):
        self.args.pair = 'c02'
        module = confirmation.private_live('c02')
        failed = {'profile': 'zvec-grep', 'status': 'exited', 'returncode': 0,
                  'process_cleanup': {'complete': False}, 'container_cleanup': {'complete': True}}
        with patch.object(confirmation, 'private_live', return_value=module), \
                patch.object(module, 'docker_info', return_value={}), \
                patch.object(module, 'run_slot', return_value=failed) as slot, \
                patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fake', 'ZVEC_GREP_API_KEY': 'fake'}):
            self.assertEqual(confirmation.execute(self.args, self.plan, self.base, self.proof), 1)
        self.assertEqual(slot.call_count, 1)
        record = live.pilot.load(self.args.output / 'execution.json')
        self.assertEqual(record['slots'][1]['profile'], 'baseline')
        self.assertEqual(record['slots'][1]['status'], 'not_started')
        self.args.command = 'collect'
        self.assertEqual(confirmation.execute(self.args, self.plan, self.base, self.proof), 1)
        collected = live.pilot.load(self.args.output / 'collection/collection.json')
        self.assertEqual([r['profile'] for r in collected['profiles']], ['zvec-grep', 'baseline'])
        self.assertTrue(all(r['strict_usage'] is None and r['quality'] is None for r in collected['profiles']))

    def test_actual_collector_body_supports_reverse_order_and_exact_provenance(self):
        # Reuse the frozen live synthetic evidence fixture, without changing it.
        fixture = test_live.LiveTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        proof = fixture.collection_fixture()
        self.args.output = fixture.args.output; self.args.pair = 'c02'; self.args.command = 'collect'
        base = fixture.plan
        # Synthetic execution was created for live-v2; this fixture adjustment
        # is test-only. Production execute never relabels old execution bytes.
        execution = live.pilot.load(self.args.output / 'execution.json')
        execution.update(protocol_id=confirmation.PROTOCOL, plan_sha256=live.pilot.digest(self.args.plan))
        execution['slots'].reverse()
        live.pilot.write(self.args.output / 'execution.json', execution)
        base['candidate_sha256'] = live.PACKAGE_SHA
        live.pilot.write(confirmation.context_path(self.args.output), confirmation.context_record(self.args, self.plan, base, dry_run=False))
        self.assertEqual(confirmation.execute(self.args, self.plan, base, proof), 0)
        report = live.pilot.load(self.args.output / 'collection/collection.json')
        self.assertEqual(report['protocol_id'], confirmation.PROTOCOL)
        self.assertEqual(report['plan_sha256'], live.pilot.digest(self.args.plan))
        self.assertEqual([r['profile'] for r in report['profiles']], ['zvec-grep', 'baseline'])
        self.assertTrue(report['structural_valid'])
        self.assertEqual(live.pilot.load(self.args.output / 'execution.json'), execution)
        context = live.pilot.load(confirmation.context_path(self.args.output)); context['pair_id'] = 'c01'
        live.pilot.write(confirmation.context_path(self.args.output), context)
        with self.assertRaisesRegex(ValueError, 'confirmation_context_missing_or_mismatched'):
            confirmation.execute(self.args, self.plan, base, proof)

    def test_context_retained_if_original_run_fails_before_first_slot(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, 'missing_credential'):
            confirmation.execute(self.args, self.plan, self.base, self.proof)
        self.assertFalse(self.args.output.exists())
        self.assertTrue(confirmation.context_path(self.args.output).is_file())
        with self.assertRaises(FileExistsError):
            confirmation.execute(self.args, self.plan, self.base, self.proof)


if __name__ == '__main__':
    unittest.main()
