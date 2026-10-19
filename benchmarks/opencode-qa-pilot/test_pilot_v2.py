"""Offline-only tests of the provisional live v2 gate; no plan is authorized."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import shutil
import multiprocessing
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import pilot
import pilot_v2 as v2
from repair_v2.package import EXPECTED_MEMBERS, RUNTIME_PATHS

REPO = pilot.HERE.parents[1]
MAIN = Path(os.environ.get('QA_MAIN_ROOT', '/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot/upstream-main/benchmarks/swe-qa-bench'))


def slow_slot(*args):
    os.setsid()
    time.sleep(5)


def stop_test_child(process):
    # Unit child has no descendants. The real process-group/Docker cleanup
    # contract is separately exercised by the required Linux integration proof.
    if process.is_alive():
        process.terminate()
        process.join(2)
    return {'complete': not process.is_alive()}


def leader_with_term_ignoring_child(marker):
    os.setsid()
    # Child acknowledges signal setup before leader deliberately exits.
    code = 'import os,signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(' + repr(marker) + ').write_text(str(os.getpid())); time.sleep(20)'
    subprocess.Popen([sys.executable, '-c', code])
    deadline = time.monotonic() + 5
    while not Path(marker).exists() and time.monotonic() < deadline:
        time.sleep(0.02)



class V2ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = pilot.native(MAIN)
        cls.original = pilot.load(pilot.HERE / 'experiment.json')
        cls.inputs = pilot.load(pilot.HERE / 'repair_v2/inputs.json')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.proof_dir = self.root / 'proof'
        self.proof_dir.mkdir()
        self.plan = copy.deepcopy(self.original)
        self.plan.update(protocol_id=v2.PROTOCOL, phase=v2.PHASE,
                         candidate_sha256=self.inputs['install_package_sha256'],
                         upstream_archive_sha256=self.inputs['original_package_sha256'])
        self.plan_path = self.root / 'plan.json'
        self.proof = self.make_proof()
        self.write_proof()

    def tearDown(self):
        self.temp.cleanup()

    def make_proof(self):
        offline = copy.deepcopy(self.original)
        offline.update(protocol_id='main-opencode-q001-v2-offline-infrastructure', phase='provider-offline',
                       candidate_sha256=self.inputs['install_package_sha256'])
        pilot.write(self.proof_dir / 'offline-plan.json', offline)
        pilot.write(self.proof_dir / 'package-manifest.json', {
            'upstream_archive_sha256': self.inputs['original_package_sha256'],
            'install_package_sha256': self.inputs['install_package_sha256'],
            'binary_sha256': self.inputs['rust_binary_sha256'],
            'runtime_payloads': [{'path': p, 'derived_bytes': EXPECTED_MEMBERS[p][0],
                'derived_sha256': EXPECTED_MEMBERS[p][1], 'mode': f'{EXPECTED_MEMBERS[p][2]:04o}'} for p in sorted(RUNTIME_PATHS)]})
        profiles = self.original['profiles']
        pilot.write(self.proof_dir / 'collection/collection.json', {'structural_valid': True,
            'profiles': [{'profile': p, 'structural_valid': True,
                          'runtime': {'opencode': {'binary_sha256': 'a' * 64}}} for p in profiles]})
        return {'schema_version': 1, 'valid': True, 'scope': 'provider-offline-infrastructure-only',
            'quality_evaluated': False, 'live_provider_calls_authorized': False, 'errors': [],
            'inputs_revision': self.inputs['revision'], 'inputs_sha256': pilot.digest(pilot.HERE / 'repair_v2/inputs.json'),
            'input_files': self.inputs['files'], 'candidate_sha256': self.inputs['install_package_sha256'],
            'source_manifest_sha256': hashlib.sha256(b'fixture manifest').hexdigest(),
            'system': {'harbor_version': '0.18.0', 'docker_architecture': 'x86_64'},
            'negative_proof': {'complete': True, 'cleanup': {'complete': True},
                'uncertain_cleanup_policy': {'next_slot_started': False},
                'cases': [{'label': label, 'succeeded': good} for label, good in {
                    'stderr-json': True, 'nonzero-json': False, 'malformed-json': False,
                    'timeout-partial': False, 'missing-child-download': False}.items()]},
            'execution': {'dry_run': False, 'slots': [{'profile': p, 'status': 'exited', 'returncode': 0,
                'container_cleanup': {'complete': True}} for p in profiles]},
            'native_proof': {'valid': True, 'profiles': {p: {'root_session_id': p + '_root',
                'child_session_id': p + '_child', 'final_sentinel_confirmed': True,
                'llm_calls': 5 if p == 'baseline' else 6, 'tool_count': 3 if p == 'baseline' else 4,
                'mcp_tool_count': 0 if p == 'baseline' else 1} for p in profiles},
                'stub': {'valid': True, 'fake_credentials_only_at_stub': True, 'real_provider_calls_authorized': False}}}

    def write_proof(self):
        self.proof['evidence_files'] = [r for r in pilot.file_inventory(self.proof_dir) if r['path'] != 'proof.json']
        pilot.write(self.proof_dir / 'proof.json', self.proof)
        self.plan['offline_proof'] = {'proof_sha256': pilot.digest(self.proof_dir / 'proof.json'),
            'inputs_sha256': pilot.digest(pilot.HERE / 'repair_v2/inputs.json'), 'inputs_revision': self.inputs['revision'],
            'ci_run_id': 123, 'ci_head_sha': '1' * 40, 'artifact_sha256': '2' * 64}
        pilot.write(self.plan_path, self.plan)

    def test_proof_hash_status_and_every_retained_byte_are_required(self):
        v2.validate_offline_proof(self.plan, self.inputs, self.proof_dir)
        (self.proof_dir / 'injected.txt').write_text('not part of proof')
        with self.assertRaisesRegex(ValueError, 'retained bytes'):
            v2.validate_offline_proof(self.plan, self.inputs, self.proof_dir)
        (self.proof_dir / 'injected.txt').unlink()
        path = self.proof_dir / 'offline-plan.json'
        path.write_text(path.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'retained bytes'):
            v2.validate_offline_proof(self.plan, self.inputs, self.proof_dir)
        self.write_proof()
        self.proof['valid'] = False
        self.write_proof()
        with self.assertRaisesRegex(ValueError, 'successful'):
            v2.validate_offline_proof(self.plan, self.inputs, self.proof_dir)
        self.plan['offline_proof']['proof_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            v2.validate_offline_proof(self.plan, self.inputs, self.proof_dir)

    def test_missing_stale_or_incomplete_proof_is_rejected(self):
        for mutation in ('stale_input', 'failed_cleanup', 'missing_negative', 'failed_native', 'wrong_main', 'wrong_payload'):
            with self.subTest(mutation=mutation):
                saved = copy.deepcopy(self.proof)
                if mutation == 'stale_input': self.proof['input_files'] = {}
                if mutation == 'failed_cleanup': self.proof['negative_proof']['cleanup']['complete'] = False
                if mutation == 'missing_negative': self.proof['negative_proof']['cases'].pop()
                if mutation == 'failed_native': self.proof['native_proof']['profiles']['zvec-grep']['mcp_tool_count'] = 0
                changed_file = None
                if mutation in ('wrong_main', 'wrong_payload'):
                    changed_file = self.proof_dir / ('offline-plan.json' if mutation == 'wrong_main' else 'package-manifest.json')
                    old_bytes = changed_file.read_bytes()
                    data = pilot.load(changed_file)
                    if mutation == 'wrong_main': data['main_file_sha256'] = {}
                    else: data['runtime_payloads'].pop()
                    pilot.write(changed_file, data)
                self.write_proof()
                with self.assertRaises(ValueError):
                    v2.validate_offline_proof(self.plan, self.inputs, self.proof_dir)
                if changed_file:
                    changed_file.write_bytes(old_bytes)
                self.proof = saved
                self.write_proof()
        self.plan['offline_proof']['inputs_revision'] += 1
        with self.assertRaisesRegex(ValueError, 'stale'):
            v2.validate_offline_proof(self.plan, self.inputs, self.proof_dir)
        with self.assertRaises((OSError, ValueError)):
            v2.validate_offline_proof(self.plan, self.inputs, self.root / 'missing')

    def prepare_validation_repo(self):
        repo = self.root / 'repo'
        old_plan_relative = 'benchmarks/opencode-qa-pilot/experiment.json'
        paths = set(self.original['frozen_harness_sha256']) | set(self.inputs['files']) | {
            old_plan_relative, 'benchmarks/opencode-qa-pilot/repair_v2/inputs.json',
            'benchmarks/opencode-qa-pilot/pilot_v2.py', 'benchmarks/opencode-qa-pilot/test_pilot_v2.py',
            'benchmarks/opencode-qa-pilot/provenance/infrastructure-repair-design-20261019.json',
            'benchmarks/zvec-issue-qa/dataset.json', 'benchmarks/zvec-issue-qa/tasks/q001/instruction.md',
            'benchmarks/zvec-issue-qa/evaluator/q001.json'}
        for relative in paths:
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / relative, target)
        self.plan['execution_workflow'] = '.github/workflows/opencode-qa-live-v2.yml'
        self.plan['execution_decision'] = 'benchmarks/opencode-qa-pilot/provenance/live-v2-fixture-decision.json'
        (repo / self.plan['execution_workflow']).write_text('# Unit fixture only; no workflow jobs or dispatch.\n')
        pilot.write(repo / self.plan['execution_decision'], {'status': 'approved_for_one_fresh_pair',
            'protocol_id': v2.PROTOCOL, 'n_attempts': 1, 'max_retries': 0,
            'offline_proof_sha256': self.plan['offline_proof']['proof_sha256']})
        pins = paths | {self.plan['execution_workflow'], self.plan['execution_decision']}
        self.plan['frozen_harness_sha256'] = {p: pilot.digest(repo / p) for p in pins}
        pilot.write(self.plan_path, self.plan)
        return repo

    def test_v2_requires_separate_frozen_plan_and_concrete_decision(self):
        repo = self.prepare_validation_repo()
        result = v2.validate_plan(self.plan_path, repo, MAIN, self.proof_dir)
        self.assertEqual(result['protocol_id'], v2.PROTOCOL)
        self.plan['frozen'] = False
        pilot.write(self.plan_path, self.plan)
        with self.assertRaisesRegex(ValueError, 'explicitly frozen'):
            v2.validate_plan(self.plan_path, repo, MAIN, self.proof_dir)
        self.plan['frozen'] = True
        for block, key, value in [('runtime', 'setup_timeout_sec', 30), ('stopping', 'no_automatic_repeats', False)]:
            saved = copy.deepcopy(self.plan[block])
            self.plan[block][key] = value
            pilot.write(self.plan_path, self.plan)
            with self.subTest(block=block), self.assertRaisesRegex(ValueError, 'unchanged native'):
                v2.validate_plan(self.plan_path, repo, MAIN, self.proof_dir)
            self.plan[block] = saved

    def test_native_argv_identical_except_explicit_v2_adapter(self):
        suite = self.native.BenchmarkSuite(name='q001', dataset=None, tier='smoke', tasks=('zvec-q001',), path=self.root)
        for profile in self.plan['profiles']:
            native = self.native.build_harbor_command(suite, profile=profile, agent='opencode',
                model=self.plan['agent']['model'], jobs_dir=self.root / 'out/runs', job_name='q001-' + profile,
                n_attempts=1, max_retries=0, harbor_executable='harbor', zvec_grep_package='derived.tgz',
                zvec_grep_package_sha256=self.plan['candidate_sha256'], embedding_model=self.plan['embedding']['model'],
                embedding_endpoint=self.plan['embedding']['endpoint'])
            wrapped = v2.build_command(self.native, suite, profile, self.root, self.root / 'out',
                'derived.tgz', self.plan['candidate_sha256'], self.plan, 'harbor')
            self.assertEqual(wrapped[wrapped.index('--agent') + 1], 'repair_v2.qa_agents:' + ('QABaseline' if profile == 'baseline' else 'QAZvec'))
            restored = wrapped[:-4]
            restored[restored.index('--agent') + 1] = native[native.index('--agent') + 1]
            self.assertEqual(restored, native)
            self.assertIn('collect_session_usage=true', wrapped)
            self.assertIn('OPENAI_API_KEY=${OPENAI_API_KEY}', wrapped)

    def test_timeout_is_retained_and_cleanup_uncertainty_stops_next_slot(self):
        candidate = self.root / 'candidate.tgz'
        candidate.write_bytes(b'fixture')
        self.plan['candidate_sha256'] = pilot.digest(candidate)
        self.plan['slot_timeout_sec'] = 0.1
        prepared = self.root / 'prepared'
        prepared.mkdir()
        (prepared / 'source-manifest.json').write_bytes(b'fixture manifest')
        for cleanup, statuses in ((True, ['timeout', 'timeout']), (False, ['timeout', 'not_started'])):
            with self.subTest(cleanup=cleanup), patch.object(pilot, 'validate_prepared'), \
                    patch.object(pilot, 'validate_runtime_inputs'), patch.object(v2, '_execute_slot', slow_slot), \
                    patch.object(pilot, 'capture_slot_containers'), \
                    patch.object(v2, 'stop_slot_group', stop_test_child), \
                    patch.object(pilot, 'cleanup_slot_containers', return_value={'complete': cleanup}):
                result = v2.run_pair(self.plan, self.plan_path, MAIN, prepared, self.root / str(cleanup), candidate, self.proof_dir)
            self.assertEqual([s['status'] for s in result['slots']], statuses)
            self.assertEqual(result['offline_proof'], self.plan['offline_proof'])

    def test_process_group_cleanup_failure_is_retained_and_blocks_next_slot(self):
        candidate = self.root / 'candidate.tgz'
        candidate.write_bytes(b'fixture')
        self.plan['candidate_sha256'] = pilot.digest(candidate)
        self.plan['slot_timeout_sec'] = 0.1
        prepared = self.root / 'prepared'
        prepared.mkdir()
        (prepared / 'source-manifest.json').write_bytes(b'fixture manifest')
        with patch.object(pilot, 'validate_prepared'), patch.object(pilot, 'validate_runtime_inputs'), \
                patch.object(v2, '_execute_slot', slow_slot), patch.object(pilot, 'capture_slot_containers'), \
                patch.object(v2, '_group_exists', side_effect=PermissionError('fixture failure')), \
                patch.object(pilot, 'cleanup_slot_containers', return_value={'complete': True}) as cleanup:
            result = v2.run_pair(self.plan, self.plan_path, MAIN, prepared, self.root / 'run', candidate, self.proof_dir)
        self.assertEqual([s['status'] for s in result['slots']], ['timeout', 'not_started'])
        self.assertFalse(result['slots'][0]['process_cleanup']['complete'])
        self.assertFalse(result['slots'][0]['container_cleanup']['complete'])
        cleanup.assert_called_once()

    def test_exited_leader_does_not_skip_surviving_group_kill(self):
        class ExitedLeader:
            pid = 991122
            def is_alive(self): return False
            def join(self, timeout): pass
        existence = [True, True, False]
        with patch.object(v2, '_group_exists', side_effect=existence), \
                patch.object(v2.os, 'killpg') as send_signal:
            result = v2.stop_slot_group(ExitedLeader(), grace_seconds=0, kill_seconds=0)
        self.assertTrue(result['complete'])
        self.assertEqual([args.args[1] for args in send_signal.call_args_list], [signal.SIGTERM, signal.SIGKILL])

    def test_cli_exit_verdict_includes_cleanup_failure_after_successful_exit(self):
        slot = {'status': 'exited', 'returncode': 0, 'process_cleanup': {'complete': True},
                'container_cleanup': {'complete': True}}
        self.assertFalse(v2.execution_failed({'dry_run': False, 'slots': [slot]}))
        for field in ('process_cleanup', 'container_cleanup'):
            bad = copy.deepcopy(slot)
            bad[field]['complete'] = False
            self.assertTrue(v2.execution_failed({'dry_run': False, 'slots': [bad]}))

    @unittest.skipUnless(sys.platform == 'linux', 'real process-group test requires the Linux gate host')
    def test_linux_descendant_ignores_term_after_leader_exits(self):
        # No providers or Docker: a real private group and a real TERM-ignoring
        # descendant expose the race that checking only leader.is_alive misses.
        marker = self.root / 'descendant.pid'
        proc = multiprocessing.get_context('fork').Process(target=leader_with_term_ignoring_child, args=(str(marker),))
        proc.start()
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists())
            proc.join(2)
            self.assertFalse(proc.is_alive())
            result = v2.stop_slot_group(proc, grace_seconds=0.1, kill_seconds=2)
            self.assertIn('SIGKILL', result['signals'])
            # Some CI init processes retain an orphan zombie. Such uncertainty
            # must remain incomplete rather than allowing another live slot.
            self.assertEqual(result['complete'], not v2._group_exists(proc.pid))
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.join(2)

    def test_source_manifest_mismatch_rejected_before_any_credential_or_agent_action(self):
        candidate = self.root / 'candidate.tgz'
        candidate.write_bytes(b'fixture')
        self.plan['candidate_sha256'] = pilot.digest(candidate)
        prepared = self.root / 'prepared'
        prepared.mkdir()
        (prepared / 'source-manifest.json').write_bytes(b'changed source')
        with patch.object(pilot, 'validate_prepared'), patch.object(pilot, 'validate_runtime_inputs') as credentials, \
                patch.object(v2, '_execute_slot') as execute:
            with self.assertRaisesRegex(ValueError, 'source manifest'):
                v2.run_pair(self.plan, self.plan_path, MAIN, prepared, self.root / 'run', candidate, self.proof_dir)
        credentials.assert_not_called()
        execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
