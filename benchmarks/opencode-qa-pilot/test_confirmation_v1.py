"""Provider-free confirmation protocol, provenance and unchanged execution checks."""
from __future__ import annotations

import ast
import copy
import inspect
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import confirmation_v1 as confirmation
import pilot
import pilot_v2 as v2
import test_pilot_v2 as parent_tests
from repair_v2.package import EXPECTED_MEMBERS, RUNTIME_PATHS

REPO = pilot.HERE.parents[1]
MAIN = Path(os.environ.get('QA_MAIN_ROOT', '/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot/upstream-main/benchmarks/swe-qa-bench'))


def slow_slot(*args):
    os.setsid()
    time.sleep(5)


def stop_test_child(process):
    if process.is_alive():
        process.terminate()
        process.join(2)
    return {'complete': not process.is_alive()}


class ConfirmationTests(unittest.TestCase):
    # Reuse the exact frozen Linux-only real process-group regression scenario.
    test_linux_descendant_ignores_term_after_leader_exits = (
        parent_tests.V2ControllerTests.test_linux_descendant_ignores_term_after_leader_exits)

    @classmethod
    def setUpClass(cls):
        cls.parent = pilot.load(REPO / confirmation.PARENT_PLAN)
        cls.native = pilot.native(MAIN)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.proof_dir = self.root / 'proof'
        self.proof_dir.mkdir()
        self.plan_path = self.root / 'confirmation.json'
        self.plan = copy.deepcopy(self.parent)
        self.plan.update(confirmation.FIXED)
        self.plan.update(parent_development_plan=confirmation.PARENT_PLAN,
            parent_development_plan_sha256=confirmation.PARENT_PLAN_SHA256,
            parent_development_stopping=copy.deepcopy(self.parent['stopping']),
            confirmation=copy.deepcopy(confirmation.CONFIRMATION),
            stopping=copy.deepcopy(confirmation.STOPPING),
            execution_workflow='.github/workflows/confirmation-fixture.yml',
            execution_decision='benchmarks/opencode-qa-pilot/provenance/confirmation-fixture.json')
        paths = set(self.parent['frozen_harness_sha256']) | {confirmation.PARENT_PLAN,
            'benchmarks/opencode-qa-pilot/confirmation_v1.py',
            'benchmarks/opencode-qa-pilot/test_confirmation_v1.py'}
        for relative in paths:
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / relative, target)
        (self.repo / self.plan['execution_workflow']).write_text('# Local unit fixture; no jobs or dispatch.\n')
        self.decision = {'status': 'approved_for_two_confirmation_pairs',
            'protocol_id': confirmation.PROTOCOL, 'repeats': ['c01', 'c02'],
            'n_attempts': 1, 'max_retries': 0,
            'parent_development_plan_sha256': confirmation.PARENT_PLAN_SHA256,
            'development_scoped_input_candidate_accepted': True,
            'offline_proof_sha256': self.parent['offline_proof']['proof_sha256']}
        pilot.write(self.repo / self.plan['execution_decision'], self.decision)
        paths |= {self.plan['execution_decision'], self.plan['execution_workflow']}
        self.plan['frozen_harness_sha256'] = {p: pilot.digest(self.repo / p) for p in paths}
        self.save_plan()

    def save_plan(self):
        pilot.write(self.plan_path, self.plan)

    def validate_fixture(self, repeat='c01'):
        # Parent validation has its own frozen tests and actual Docker proof.
        # Only this new schema layer is replaced by a synthetic parent result.
        with patch.object(v2, 'validate_plan', return_value=self.parent) as validate:
            result = confirmation.validate_plan(self.plan_path, self.repo, MAIN, self.proof_dir, repeat)
        validate.assert_called_once_with(self.repo / confirmation.PARENT_PLAN, self.repo, MAIN, self.proof_dir)
        return result

    def prepared_fixture(self):
        prepared = self.root / 'prepared'
        prepared.mkdir()
        (prepared / 'source-manifest.json').write_bytes(b'fixture manifest')
        pilot.write(prepared / 'prepared.json', {
            **confirmation.provenance(self.plan, self.plan_path, 'c01'),
            'files': pilot.file_inventory(prepared),
            'source_inventory_sha256': self.plan['source_inventory_sha256']})
        candidate = self.root / 'candidate.tgz'
        candidate.write_bytes(b'fixture package')
        self.plan['candidate_sha256'] = pilot.digest(candidate)
        self.plan['slot_timeout_sec'] = 0.1
        self.save_plan()
        metadata = pilot.load(prepared / 'prepared.json')
        metadata.update(confirmation.provenance(self.plan, self.plan_path, 'c01'))
        pilot.write(prepared / 'prepared.json', metadata)
        pilot.write(self.proof_dir / 'proof.json', {
            'source_manifest_sha256': pilot.digest(prepared / 'source-manifest.json')})
        pilot.write(self.proof_dir / 'collection/collection.json', {'profiles': [
            {'profile': p, 'runtime': {'opencode': {'binary_sha256': 'a' * 64}}}
            for p in self.plan['profiles']]})
        return prepared, candidate

    def test_both_exact_repeats_validate_and_invalid_or_unfrozen_plan_fails(self):
        for repeat in confirmation.REPEATS:
            self.assertEqual(self.validate_fixture(repeat)['protocol_id'], confirmation.PROTOCOL)
        with self.assertRaisesRegex(ValueError, 'repeat'):
            self.validate_fixture('c03')
        self.plan['frozen'] = False
        self.save_plan()
        with self.assertRaisesRegex(ValueError, 'explicitly frozen'):
            self.validate_fixture()

    def test_parent_and_offline_proof_validation_cannot_be_bypassed(self):
        with patch.object(v2, 'validate_plan', side_effect=ValueError('invalid or stale offline proof')) as gate:
            with self.assertRaisesRegex(ValueError, 'offline proof'):
                confirmation.validate_plan(self.root / 'missing-plan', self.repo, MAIN, self.proof_dir, 'c01')
            gate.assert_called_once()
        path = self.repo / confirmation.PARENT_PLAN
        path.write_text(path.read_text() + '\n')
        with patch.object(v2, 'validate_plan') as gate, self.assertRaisesRegex(ValueError, 'parent development plan changed'):
            confirmation.validate_plan(self.plan_path, self.repo, MAIN, self.proof_dir, 'c01')
        gate.assert_not_called()

    def test_all_native_controls_proof_and_prospective_rules_are_unchanged(self):
        mutations = [
            ('provider', 'request_seed', 99), ('embedding', 'dimensions', 8),
            ('evaluation', 'scoped_efficiency_candidate', 'choose best of two'),
            ('accounting', 'scope', 'root-only'), ('runtime', 'setup_timeout_sec', 5),
            ('offline_proof', 'inputs_sha256', '0' * 64),
            ('parent_development_stopping', 'no_automatic_repeats', False),
            ('stopping', 'no_replacement', False),
            ('confirmation', 'development_pair_pooled', True),
            ('confirmation', 'success_rule', 'either_repeat_passes'),
            ('confirmation', 'maximum_parallel_pairs', 2),
        ]
        for block, key, value in mutations:
            saved = copy.deepcopy(self.plan[block])
            self.plan[block][key] = value
            self.save_plan()
            with self.subTest(block=block, key=key), self.assertRaises(ValueError):
                self.validate_fixture()
            self.plan[block] = saved
        for key, value in [('repeats', ['c01', 'c03']), ('n_attempts', 2), ('max_retries', 1), ('slot_timeout_sec', 4000)]:
            saved = self.plan[key]
            self.plan[key] = value
            self.save_plan()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate_fixture()
            self.plan[key] = saved

    def test_decision_acceptance_and_harness_pin_are_required(self):
        self.decision['development_scoped_input_candidate_accepted'] = False
        path = self.repo / self.plan['execution_decision']
        pilot.write(path, self.decision)
        self.plan['frozen_harness_sha256'][self.plan['execution_decision']] = pilot.digest(path)
        self.save_plan()
        with self.assertRaisesRegex(ValueError, 'accepted-candidate'):
            self.validate_fixture()
        self.decision['development_scoped_input_candidate_accepted'] = True
        pilot.write(path, self.decision)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.validate_fixture()

    def test_exact_native_arguments_match_parent_for_both_profiles(self):
        suite = self.native.BenchmarkSuite(name='q001', dataset=None, tier='smoke', tasks=('zvec-q001',), path=self.root)
        for profile in self.plan['profiles']:
            args = (self.native, suite, profile, self.root / 'prepared', self.root / 'run',
                    'derived.tgz', self.plan['candidate_sha256'])
            parent = v2.build_command(*args, self.parent, 'harbor')
            actual = confirmation.build_command(*args, self.plan, 'harbor')
            self.assertEqual(actual, parent)
            self.assertEqual(actual[actual.index('--agent') + 1],
                             'repair_v2.qa_agents:' + ('QABaseline' if profile == 'baseline' else 'QAZvec'))
            self.assertIn('collect_session_usage=true', actual)

    def test_preparation_adds_only_host_provenance_and_binds_repeat(self):
        def export_fixture(plan, plan_path, repo_root, main_root, zvec_repo, output):
            output.mkdir()
            (output / 'solver-input.txt').write_bytes(b'unchanged source/question')
            pilot.write(output / 'prepared.json', {'plan_sha256': pilot.digest(plan_path),
                                                 'files': pilot.file_inventory(output)})
        output = self.root / 'prepared'
        with patch.object(pilot, 'prepare', side_effect=export_fixture) as export:
            result = confirmation.prepare(self.plan, self.plan_path, self.repo, MAIN, self.root / 'zvec', output, 'c01')
        export.assert_called_once()
        self.assertEqual(result['repeat'], 'c01')
        confirmation.validate_prepared(output, self.plan, self.plan_path, 'c01')
        with self.assertRaisesRegex(ValueError, 'repeat identity'):
            confirmation.validate_prepared(output, self.plan, self.plan_path, 'c02')
        self.assertEqual((output / 'solver-input.txt').read_bytes(), b'unchanged source/question')

    def test_timeout_partial_slots_and_cleanup_uncertainty_retain_new_provenance(self):
        prepared, candidate = self.prepared_fixture()
        for cleanup, expected in ((True, ['timeout', 'timeout']), (False, ['timeout', 'not_started'])):
            with self.subTest(cleanup=cleanup), patch.object(pilot, 'validate_runtime_inputs'), \
                    patch.object(v2, '_execute_slot', slow_slot), patch.object(v2, 'stop_slot_group', stop_test_child), \
                    patch.object(pilot, 'capture_slot_containers'), \
                    patch.object(pilot, 'cleanup_slot_containers', return_value={'complete': cleanup}):
                output = self.root / ('run-' + str(cleanup))
                result = confirmation.run_pair(self.plan, self.plan_path, MAIN, prepared, output,
                                               candidate, self.proof_dir, 'c01')
            self.assertEqual([s['status'] for s in result['slots']], expected)
            self.assertEqual([s['profile'] for s in result['slots']], ['baseline', 'zvec-grep'])
            self.assertTrue(v2.execution_failed(result))
            self.assertEqual(result['protocol_id'], confirmation.PROTOCOL)
            self.assertEqual(result['repeat'], 'c01')
            self.assertEqual(result['plan_sha256'], pilot.digest(self.plan_path))
            for slot in result['slots']:
                self.assertEqual(slot['repeat'], 'c01')
                self.assertEqual(slot['protocol_id'], confirmation.PROTOCOL)
            summary = confirmation.collect(self.plan, self.plan_path, MAIN, output,
                self.root / ('collected-' + str(cleanup)), self.proof_dir, 'c01')
            self.assertFalse(summary['structural_valid'])
            self.assertEqual(summary['repeat'], 'c01')
            self.assertEqual(len(summary['profiles']), 2)

    def test_cross_repeat_or_reused_output_fails_before_provider_validation(self):
        prepared, candidate = self.prepared_fixture()
        output = self.root / 'run'
        with patch.object(pilot, 'validate_runtime_inputs') as credentials:
            with self.assertRaisesRegex(ValueError, 'repeat identity'):
                confirmation.run_pair(self.plan, self.plan_path, MAIN, prepared, output, candidate, self.proof_dir, 'c02')
            credentials.assert_not_called()
        output.mkdir()
        with self.assertRaisesRegex(ValueError, 'reruns/replacements forbidden'):
            confirmation.run_pair(self.plan, self.plan_path, MAIN, prepared, output, candidate, self.proof_dir, 'c01', True)

    def test_collector_rejects_old_or_other_repeat_record_before_native_collection(self):
        run = self.root / 'run'; run.mkdir()
        record = {**confirmation.provenance(self.plan, self.plan_path, 'c01'),
                  'candidate_sha256': self.plan['candidate_sha256'],
                  'offline_proof': self.plan['offline_proof'],
                  'upstream_archive_sha256': self.plan['upstream_archive_sha256']}
        for key, value in [('protocol_id', v2.PROTOCOL), ('repeat', 'c02'), ('plan_sha256', '0' * 64)]:
            wrong = {**record, key: value}
            pilot.write(run / 'execution.json', wrong)
            with patch.object(pilot, 'collect') as collect, self.subTest(key=key), self.assertRaisesRegex(ValueError, 'run/repeat identity'):
                confirmation.collect(self.plan, self.plan_path, MAIN, run, self.root / 'collection', self.proof_dir, 'c01')
            collect.assert_not_called()

    def test_four_runtime_payload_and_binary_checks_remain_in_collect(self):
        run = self.root / 'run'; run.mkdir()
        record = {**confirmation.provenance(self.plan, self.plan_path, 'c01'),
            'candidate_sha256': self.plan['candidate_sha256'], 'offline_proof': self.plan['offline_proof'],
            'upstream_archive_sha256': self.plan['upstream_archive_sha256'],
            'slots': [{'profile': p, 'install_cache_absent_before_prepare': True} for p in self.plan['profiles']]}
        pilot.write(run / 'execution.json', record)
        pilot.write(self.proof_dir / 'collection/collection.json', {'profiles': [
            {'profile': p, 'runtime': {'opencode': {'binary_sha256': 'a' * 64}}} for p in self.plan['profiles']]})
        payloads = {p.removeprefix('package/'): {'bytes': EXPECTED_MEMBERS[p][0],
            'sha256': EXPECTED_MEMBERS[p][1], 'mode': f'{EXPECTED_MEMBERS[p][2]:04o}'} for p in RUNTIME_PATHS}
        summary = {'structural_valid': True, 'profiles': [{'profile': p, 'structural_valid': True,
            'errors': [], 'runtime': {'opencode': {'binary_sha256': 'a' * 64}, 'zg': {'payloads': copy.deepcopy(payloads)}}}
            for p in self.plan['profiles']]}
        summary['profiles'][1]['runtime']['zg']['payloads'].pop(next(iter(payloads)))
        with patch.object(pilot, 'collect', return_value=summary):
            result = confirmation.collect(self.plan, self.plan_path, MAIN, run, self.root / 'collected', self.proof_dir, 'c01')
        self.assertFalse(result['structural_valid'])
        self.assertIn('installed_zg_runtime_payloads_differ_from_offline_proof', result['profiles'][1]['errors'])

    def test_cli_dispatches_selected_repeat_and_preserves_cleanup_failure_exit(self):
        common = ['--plan', str(self.plan_path), '--repo-root', str(self.repo),
                  '--main-root', str(MAIN), '--proof-dir', str(self.proof_dir), '--repeat', 'c02']
        with patch.object(confirmation, 'validate_plan', return_value=self.plan), \
                patch.object(confirmation, 'prepare') as prepare:
            self.assertEqual(confirmation.main(common + ['prepare', '--zvec-repo', str(self.root / 'zvec'),
                '--output', str(self.root / 'prepared')]), 0)
            self.assertEqual(prepare.call_args.args[-1], 'c02')
        failed = {'dry_run': False, 'slots': [{'status': 'exited', 'returncode': 0,
            'process_cleanup': {'complete': False}, 'container_cleanup': {'complete': False}}]}
        with patch.object(confirmation, 'validate_plan', return_value=self.plan), \
                patch.object(confirmation, 'run_pair', return_value=failed) as run:
            self.assertEqual(confirmation.main(common + ['run', '--prepared', str(self.root / 'prepared'),
                '--output', str(self.root / 'run'), '--candidate-tgz', str(self.root / 'candidate.tgz')]), 1)
            self.assertEqual(run.call_args.args[-2], 'c02')

    def test_owned_execution_loop_matches_frozen_parent_after_provenance_normalization(self):
        # Compare the actual scheduling/deadline/cleanup loop, not a copied list
        # of expected behavior. Only added host provenance and qualified reuse
        # of immutable v2 functions may differ.
        def loop(function):
            tree = ast.parse(inspect.getsource(function))
            return next(n for n in tree.body[0].body if isinstance(n, ast.For))
        class Normalize(ast.NodeTransformer):
            def visit_Dict(self, node):
                self.generic_visit(node)
                pairs = [(k, v) for k, v in zip(node.keys, node.values)
                         if not (k is None and isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == 'provenance')]
                node.keys, node.values = [p[0] for p in pairs], [p[1] for p in pairs]
                return node
            def visit_Attribute(self, node):
                self.generic_visit(node)
                if isinstance(node.value, ast.Name) and node.value.id == 'v2' and node.attr in ('_execute_slot', 'stop_slot_group'):
                    return ast.Name(id=node.attr, ctx=ast.Load())
                return node
        parent_loop = ast.dump(Normalize().visit(loop(v2.run_pair)), include_attributes=False)
        own_loop = ast.dump(Normalize().visit(loop(confirmation.run_pair)), include_attributes=False)
        self.assertEqual(own_loop, parent_loop)
        def collection_checks(function):
            source = inspect.getsource(function)
            return source[source.index('    proof_collection ='):source.index("    summary['offline_proof']")].split('    summary.update(identity)')[0]
        self.assertEqual(collection_checks(confirmation.collect), collection_checks(v2.collect))


if __name__ == '__main__':
    unittest.main()
