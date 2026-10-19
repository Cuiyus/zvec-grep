"""Live-controller boundaries using local synthetic evidence, never providers."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import live
from native_claude.test_accounting import Fixture


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = dict(live.FIXED, frozen=True,
            provider={'base_url': 'https://provider.invalid/apps/anthropic'},
            embedding={'endpoint': 'https://provider.invalid/v1/embeddings', 'model': 'qwen/qwen3.7-text-embedding'},
            offline_proof={'proof_sha256': 'a' * 64, 'inputs_sha256': 'b' * 64, 'inputs_revision': 3})
        self.args = SimpleNamespace(plan=self.root / 'plan.json', main_root=self.root / 'main',
                                    prepared=self.root / 'prepared', candidate=self.root / 'zg.tgz',
                                    output=self.root / 'run')
        live.pilot.write(self.args.plan, self.plan)

    def test_environment_allowlist_parent_route_and_baseline_isolation(self):
        ambient = {'PATH': '/bin', 'HOME': '/tmp/home', 'ANTHROPIC_API_KEY': 'chat-key',
                   'ZVEC_GREP_API_KEY': 'embedding-key', 'ANTHROPIC_AUTH_TOKEN': 'wrong-token',
                   'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CODE_OAUTH_TOKEN': 'oauth',
                   'ANTHROPIC_MODEL': 'wrong-model', 'CLAUDE_CODE_MAX_TURNS': '1',
                   'OPENAI_API_KEY': 'wrong-key', 'PYTHONPATH': '/ambient'}
        with patch.dict(os.environ, ambient, clear=True):
            for profile in live.PROFILES:
                with live.live_environment(self.plan, profile, self.args.main_root) as env:
                    self.assertEqual(os.environ['ANTHROPIC_BASE_URL'], self.plan['provider']['base_url'])
                    self.assertEqual(env['ANTHROPIC_API_KEY'], 'chat-key')
                    self.assertEqual('ZVEC_GREP_API_KEY' in env, profile == 'zvec-grep')
                    self.assertFalse({'ANTHROPIC_MODEL', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_USE_BEDROCK',
                                      'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_MAX_TURNS', 'OPENAI_API_KEY'} & env.keys())
            self.assertEqual(dict(os.environ), ambient)

    def test_environment_restores_on_error_and_missing_embedding_prevents_first_slot(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'key'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'missing_credential:ZVEC_GREP_API_KEY'):
                live.run_pair(self.args, self.plan)
            self.assertFalse(self.args.output.exists())
            with self.assertRaises(RuntimeError):
                with live.live_environment(self.plan, 'baseline', self.args.main_root):
                    raise RuntimeError('test')
            self.assertEqual(dict(os.environ), {'ANTHROPIC_API_KEY': 'key'})

    def test_endpoint_rejects_credentials_queries_and_non_https(self):
        for value in ('http://provider.invalid', 'https://key@provider.invalid',
                      'https://provider.invalid/?key=secret', 'https://provider.invalid/#token'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                live.https_endpoint(value)

    def test_reported_usage_exact_scope_cache_and_no_missing_zero_fallback(self):
        raw = {live.MODEL: dict(inputTokens=10, outputTokens=4, cacheReadInputTokens=20, cacheCreationInputTokens=30, costUSD=12)}
        report = live.reported_model_usage({'modelUsage': raw})
        self.assertEqual(report['usage']['input_tokens'], 60)
        self.assertEqual(report['raw'], raw)
        self.assertIsNone(report['cost_usd'])
        self.assertFalse(report['proves_complete_session_tree'])
        for key in live.MODEL_USAGE_FIELDS:
            for value in (None, True, -1, float('nan'), float('inf'), '1'):
                damaged = copy.deepcopy(raw)
                damaged[live.MODEL][key] = value
                result = live.reported_model_usage({'modelUsage': damaged, 'usage': {'input_tokens': 999}})
                self.assertFalse(result['available'])
                self.assertIsNone(result['usage'])
        self.assertFalse(live.reported_model_usage({'modelUsage': dict(raw, other={})})['available'])
        self.assertFalse(live.reported_model_usage({'usage': {'input_tokens': 999}})['available'])

    def test_complete_evidence_contradiction_withholds_but_extra_reported_usage_does_not(self):
        report = live.reported_model_usage({'modelUsage': {live.MODEL: dict(inputTokens=10, outputTokens=10, cacheReadInputTokens=10, cacheCreationInputTokens=10)}})
        accounting = {'complete': True, 'errors': [], 'usage': dict(uncached_input_tokens=5, output_tokens=5, cache_read_tokens=5, cache_write_tokens=5)}
        self.assertFalse(live.metric_consistency(report, accounting)['metric_conclusion_withheld'])
        self.assertEqual(live.metric_consistency(report, accounting)['corroboration_status'], 'reported-exceeds-persisted-coverage-unverified')
        matching = dict(accounting, usage={key: 10 for key in accounting['usage']})
        self.assertEqual(live.metric_consistency(report, matching)['corroboration_status'], 'complete-and-matching')
        accounting['usage']['cache_write_tokens'] = 11
        self.assertTrue(live.metric_consistency(report, accounting)['metric_conclusion_withheld'])
        accounting.update(complete=False, errors=[{'code': 'missing_usage'}])
        self.assertFalse(live.metric_consistency(report, accounting)['metric_conclusion_withheld'])
        accounting['errors'] = [{'code': 'usage_regression'}]
        self.assertTrue(live.metric_consistency(report, accounting)['metric_conclusion_withheld'])

    def test_cleanup_uncertainty_retains_second_slot_not_started(self):
        first = {'profile': 'baseline', 'status': 'exited', 'returncode': 0,
                 'process_cleanup': {'complete': False}, 'container_cleanup': {'complete': True}}
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'chat-key', 'ZVEC_GREP_API_KEY': 'embedding-key'}), \
             patch.object(live, 'docker_info', return_value={}), patch.object(live, 'run_slot', return_value=first) as execute:
            record = live.run_pair(self.args, self.plan)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual([s['profile'] for s in record['slots']], live.PROFILES)
        self.assertEqual(record['slots'][1]['status'], 'not_started')
        self.assertEqual(live.pilot.load(self.args.output / 'execution.json'), record)
        self.assertNotIn('chat-key', json.dumps(record))

    def test_cleaned_failed_slot_does_not_replace_or_skip_scheduled_second(self):
        def slot(args, plan, profile):
            return {'profile': profile, 'status': 'failed', 'process_cleanup': {'complete': True}, 'container_cleanup': {'complete': True}}
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'chat-key', 'ZVEC_GREP_API_KEY': 'embedding-key'}), \
             patch.object(live, 'docker_info', return_value={}), patch.object(live, 'run_slot', side_effect=slot) as execute:
            record = live.run_pair(self.args, self.plan)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual([s['profile'] for s in record['slots']], live.PROFILES)

    @unittest.skipUnless(hasattr(signal, 'setitimer'), 'POSIX deadline')
    def test_deadline_in_cache_preparation_still_retains_cleanup(self):
        self.args.output.mkdir()
        fake_runner = SimpleNamespace(prepare_zvec_grep_package=lambda _: (_ for _ in ()).throw(TimeoutError()))
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'chat-key'}), patch.object(live.pilot, 'native', return_value=fake_runner), \
             patch.object(live.pilot, 'cleanup_slot_containers', return_value={'complete': True}) as cleanup:
            slot = live.run_slot(self.args, self.plan, 'baseline')
        self.assertEqual(slot['status'], 'timeout')
        self.assertEqual(slot['returncode'], 124)
        self.assertTrue(live.cleanup_complete(slot))
        cleanup.assert_called_once()
        self.assertTrue((self.args.output / 'baseline-slot.json').exists())

    def make_offline(self):
        folder = self.root / 'ci/raw/integration'
        folder.mkdir(parents=True)
        (folder / 'evidence.txt').write_text('original evidence')
        (folder.parent / 'inputs.json').write_text('frozen inputs')
        proof = {'protocol': 'native-claude-offline-v1', 'valid': True, 'quality_evaluated': False, 'errors': [],
                 'pair_checks': {'same_actual_claude_binary': True, 'same_native_init_tools_except_zg': True},
                 'slots': [{'profile': p, 'status': 'exited', 'returncode': 0,
                            'process_cleanup': {'complete': True}, 'container_cleanup': {'complete': True}} for p in live.PROFILES],
                 'verification': [{'profile': p, 'valid': True, 'checks': [{'passed': True}]} for p in live.PROFILES]}
        proof['evidence_files'] = live.pilot.file_inventory(folder) + [{'path': 'proof.json', 'bytes': 1, 'sha256': 'old-self-hash'}]
        live.pilot.write(folder / 'proof.json', proof)
        ref = {'directory': 'ci/raw/integration', 'proof_sha256': live.pilot.digest(folder / 'proof.json'),
               'inputs_sha256': live.pilot.digest(folder.parent / 'inputs.json'), 'review_path': 'accepted.json'}
        live.pilot.write(self.root / 'accepted.json', {'valid': True, 'proof_sha256': ref['proof_sha256']})
        ref['review_sha256'] = live.pilot.digest(self.root / 'accepted.json')
        self.plan['offline_proof'] = ref
        return folder

    def test_offline_proof_requires_success_hashes_review_and_all_retained_bytes(self):
        folder = self.make_offline()
        self.assertTrue(live.validate_offline(self.plan, self.root)['valid'])
        (folder / 'evidence.txt').write_text('changed evidence')
        with self.assertRaisesRegex(ValueError, 'offline_retained_evidence_changed'):
            live.validate_offline(self.plan, self.root)
        (folder / 'evidence.txt').write_text('original evidence')
        self.plan['offline_proof']['proof_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'offline_proof_hash_mismatch'):
            live.validate_offline(self.plan, self.root)

    def test_failed_proof_and_unaccepted_review_fail_closed(self):
        folder = self.make_offline()
        proof = live.pilot.load(folder / 'proof.json')
        proof['valid'] = False
        live.pilot.write(folder / 'proof.json', proof)
        self.plan['offline_proof']['proof_sha256'] = live.pilot.digest(folder / 'proof.json')
        with self.assertRaisesRegex(ValueError, 'offline_pair_not_successful'):
            live.validate_offline(self.plan, self.root)
        proof['valid'] = True
        live.pilot.write(folder / 'proof.json', proof)
        self.plan['offline_proof']['proof_sha256'] = live.pilot.digest(folder / 'proof.json')
        live.pilot.write(self.root / 'accepted.json', {'valid': False, 'proof_sha256': self.plan['offline_proof']['proof_sha256']})
        self.plan['offline_proof']['review_sha256'] = live.pilot.digest(self.root / 'accepted.json')
        with self.assertRaisesRegex(ValueError, 'offline_review_not_accepted'):
            live.validate_offline(self.plan, self.root)

    def collection_fixture(self, usage_missing=False, identity_bad=False):
        self.args.output.mkdir()
        slots = []
        runtime = {'claude': {'binary_sha256': 'a' * 64}}
        for profile in live.PROFILES:
            trial = self.args.output / 'runs' / ('q001-' + profile) / 'trial'
            agent = trial / 'agent'
            f = Fixture(agent)
            f.result['modelUsage'] = {live.MODEL: dict(inputTokens=100, outputTokens=100, cacheReadInputTokens=100, cacheCreationInputTokens=100)}
            if usage_missing:
                del f.root[-1]['message']['usage']['cache_read_input_tokens']
            if identity_bad:
                f.root[-1]['message']['model'] = 'wrong-model'
            f.write()
            stream = agent / 'claude-code.txt'
            lines = [json.loads(x) for x in stream.read_text().splitlines()]
            lines[0]['tools'] = ['Read', 'Bash', 'Agent'] + (['mcp__zvec_grep__zvec_grep_search'] if profile == 'zvec-grep' else [])
            stream.write_text(''.join(json.dumps(x) + '\n' for x in lines))
            shutil.copytree(agent, trial / 'qa-native-claude-logs')
            live.pilot.write(agent / 'trajectory.json', {'steps': [{'source': 'agent', 'message': 'FINAL'}]})
            live.pilot.write(trial / 'result.json', {'exception_info': None})
            run = copy.deepcopy(runtime)
            if profile == 'zvec-grep':
                run['zg'] = {'payloads': {p.removeprefix('package/'): {'bytes': live.EXPECTED_MEMBERS[p][0], 'sha256': live.EXPECTED_MEMBERS[p][1]} for p in live.RUNTIME_PATHS}}
            audit = {'valid': True, 'native_run_wall_seconds': 12,
                     'before': {'observed': {'runtime': run}}, 'after': {'observed': {'runtime': run}}}
            live.pilot.write(trial / 'qa-source-audit.json', audit)
            slot = {'profile': profile, 'status': 'exited', 'returncode': 0, 'started_at_unix': 0, 'finished_at_unix': 20,
                    'process_cleanup': {'complete': True}, 'container_cleanup': {'complete': True}, 'fresh_volumes': ['nvm-' + profile, 'local-' + profile]}
            mounts = [{'Destination': '/root/' + target, 'Type': 'volume', 'Name': name} for target, name in zip(('.nvm', '.local'), slot['fresh_volumes'])]
            if profile == 'zvec-grep':
                slot.update(install_package_source='/retained/package.tgz', install_package_sha256=live.PACKAGE_SHA)
                mounts.append({'Destination': '/tmp/zg-bench-zvec-grep.tgz', 'Type': 'bind', 'RW': False, 'Source': '/retained/package.tgz'})
            live.pilot.write(self.args.output / (profile + '-containers.json'), {'container': {'mounts': mounts, 'trial_dir': str(trial)}})
            slots.append(slot)
        live.pilot.write(self.args.output / 'execution.json', {'protocol_id': live.PROTOCOL, 'plan_sha256': live.pilot.digest(self.args.plan),
            'offline_proof_sha256': self.plan['offline_proof']['proof_sha256'], 'candidate_sha256': live.PACKAGE_SHA, 'dry_run': False, 'slots': slots})
        return {'verification': [{'source_audit': {'before': {'observed': {'runtime': runtime}}}}]}

    def test_usage_only_missing_does_not_invalidate_answer_or_substitute_strict_totals(self):
        proof = self.collection_fixture(usage_missing=True)
        report = live.collect(self.args, self.plan, proof)
        self.assertTrue(report['structural_valid'], report)
        self.assertFalse(report['strict_usage_complete'])
        self.assertTrue(report['reported_metric_pair_eligible'])
        self.assertFalse(report['favorable_conclusion_allowed'])
        for row in report['profiles']:
            self.assertIsNone(row['strict_usage'])
            self.assertIsNone(row['quality'])
            self.assertTrue(row['answer_present'])
            self.assertEqual((self.args.output / 'collection' / row['answer_file']).read_bytes(), b'FINAL')

    def test_identity_error_keeps_answer_but_invalidates_execution(self):
        proof = self.collection_fixture(identity_bad=True)
        report = live.collect(self.args, self.plan, proof)
        self.assertFalse(report['structural_valid'])
        self.assertFalse(report['reported_metric_pair_eligible'])
        self.assertTrue(all(r['answer_present'] and r['quality'] is None for r in report['profiles']))

    def test_missing_scheduled_slot_kept_without_synthetic_zeros(self):
        proof = self.collection_fixture()
        shutil.rmtree(self.args.output / 'runs/q001-zvec-grep')
        report = live.collect(self.args, self.plan, proof)
        self.assertEqual([r['profile'] for r in report['profiles']], live.PROFILES)
        self.assertFalse(report['structural_valid'])
        self.assertIsNone(report['profiles'][1]['strict_usage'])
        self.assertIsNone(report['profiles'][1]['claude_reported_model_usage']['usage'])

    def test_retained_linux_mounts_do_not_resolve_on_analyst_filesystem(self):
        slot = {'profile': 'zvec-grep', 'fresh_volumes': ['nvm', 'local'],
                'install_package_source': '/home/runner/package.tgz', 'install_package_sha256': live.PACKAGE_SHA}
        container = {'trial_dir': '/home/runner/trial', 'mounts': [
            {'Destination': '/root/.nvm', 'Type': 'volume', 'Name': 'nvm'},
            {'Destination': '/root/.local', 'Type': 'volume', 'Name': 'local'},
            {'Destination': '/tmp/zg-bench-zvec-grep.tgz', 'Source': '/home/runner/package.tgz', 'RW': False},
            {'Destination': '/logs/agent', 'Source': '/home/runner/trial/agent'}]}
        with patch.object(Path, 'resolve', side_effect=AssertionError('must not resolve remote paths')):
            self.assertTrue(live.mounts_valid({'id': container}, slot))
        container['mounts'][-1]['Source'] = '/home/runner/trial/../outside'
        self.assertFalse(live.mounts_valid({'id': container}, slot))

    def test_timing_intervals_retain_timestamps_and_missing_is_not_zero(self):
        raw = {'started_at': '2026-10-20T00:00:00Z', 'finished_at': '2026-10-20T00:00:01.25Z'}
        self.assertEqual(live.timing_interval(raw), {'timestamps': raw, 'seconds': 1.25})
        self.assertIsNone(live.timing_interval(None)['seconds'])
        self.assertIsNone(live.timing_interval({'started_at': raw['finished_at'], 'finished_at': raw['started_at']})['seconds'])


if __name__ == '__main__':
    unittest.main()
