"""Offline protocol, integrity, terminal-evidence and failure-retention checks."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import pilot


REPO = pilot.HERE.parents[1]
DEFAULT_MAIN = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot/upstream-main/benchmarks/swe-qa-bench')
MAIN_ROOT = Path(os.environ.get('QA_MAIN_ROOT', DEFAULT_MAIN))
ZVEC_REPO = Path(os.environ.get('QA_ZVEC_REPO', '/Users/cc/workspace/zvec'))
NATIVE_FIXTURE = pilot.HERE / 'fixtures/native-qwen'


def slow_slot(*args):
    os.setsid()
    time.sleep(30)


class PilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = pilot.native(MAIN_ROOT)
        from qa_agents import compare_inventory
        cls.compare_inventory = staticmethod(compare_inventory)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plan = copy.deepcopy(pilot.FIXED)
        self.plan.update(frozen=True, candidate_sha256=hashlib.sha256(b'fixture').hexdigest(),
            embedding={'model': 'qwen/qwen3.7-text-embedding', 'endpoint': 'https://example.invalid/v1/embeddings'},
            main_file_sha256={p.relative_to(MAIN_ROOT).as_posix(): pilot.digest(p)
                for p in (MAIN_ROOT / 'zg_bench').rglob('*.py')})
        self.plan['main_file_sha256'].update({p: pilot.digest(MAIN_ROOT / p)
                                            for p in ('pyproject.toml', 'uv.lock')})
        self.path = self.root / 'plan.json'
        pilot.write(self.path, self.plan)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_frozen_gate_and_exact_main(self):
        pilot.validate_plan(self.path, REPO, MAIN_ROOT)
        self.plan['frozen'] = False
        pilot.write(self.path, self.plan)
        with self.assertRaisesRegex(ValueError, 'explicitly frozen'):
            pilot.validate_plan(self.path, REPO, MAIN_ROOT)

    def test_plan_fixed_bounds_and_complete_pins(self):
        for key, bad in [('task_id', 'q004'), ('n_attempts', 2), ('max_retries', 1),
                         ('profiles', ['zvec-grep', 'baseline']), ('agent_timeout_sec', 900),
                         ('slot_timeout_sec', 0)]:
            plan = copy.deepcopy(self.plan)
            plan[key] = bad
            pilot.write(self.path, plan)
            with self.assertRaises(ValueError):
                pilot.validate_plan(self.path, REPO, MAIN_ROOT)
        del self.plan['main_file_sha256']['zg_bench/runner.py']
        pilot.write(self.path, self.plan)
        with self.assertRaisesRegex(ValueError, 'pin all main'):
            pilot.validate_plan(self.path, REPO, MAIN_ROOT)

    def test_extra_narrative_and_optional_harness_pins(self):
        self.plan['provenance'] = {'note': 'postobservational development'}
        self.plan['main_file_sha256']['README.md'] = pilot.digest(MAIN_ROOT / 'README.md')
        self.plan['frozen_harness_sha256'] = {'benchmarks/opencode-qa-pilot/pilot.py': pilot.digest(pilot.HERE / 'pilot.py')}
        pilot.write(self.path, self.plan)
        pilot.validate_plan(self.path, REPO, MAIN_ROOT)
        self.plan['frozen_harness_sha256']['benchmarks/opencode-qa-pilot/pilot.py'] = '0' * 64
        pilot.write(self.path, self.plan)
        with self.assertRaisesRegex(ValueError, 'harness hash'):
            pilot.validate_plan(self.path, REPO, MAIN_ROOT)

    def test_credentials_and_endpoints_checked_for_entire_pair_before_execution(self):
        from zg_bench.engines.registry import resolve_opencode_model
        self.plan['provider'] = {'base_url': resolve_opencode_model(self.plan['agent']['model']).configuration.base_url}
        env = {'GLM_API_KEY': 'fake-model-key', 'ZVEC_GREP_API_KEY': 'fake-embedding-key',
               'ZVEC_GREP_ENDPOINT': self.plan['embedding']['endpoint']}
        with patch.dict(os.environ, env, clear=True):
            pilot.validate_runtime_inputs(self.plan, MAIN_ROOT)
        for field, value in [('ZVEC_GREP_API_KEY', ''), ('ZVEC_GREP_ENDPOINT', 'https://wrong.invalid'),
                             ('GLM_BASE_URL', 'https://wrong.invalid')]:
            with self.subTest(field=field), patch.dict(os.environ, {**env, field: value}, clear=True):
                with self.assertRaises(ValueError):
                    pilot.validate_runtime_inputs(self.plan, MAIN_ROOT)

    def test_native_fixture_bytes_match_retained_provenance(self):
        provenance = pilot.load(NATIVE_FIXTURE / 'provenance.json')
        self.assertEqual(len(provenance['files']), 7)
        for row in provenance['files']:
            path = NATIVE_FIXTURE / row['path']
            self.assertEqual(path.stat().st_size, row['bytes'])
            self.assertEqual(pilot.digest(path), row['sha256'])

    def test_native_command_preserved_except_instrumentation(self):
        suite = self.runner.BenchmarkSuite(name='q001', dataset=None, tier='smoke',
                                         tasks=('zvec-q001',), path=self.root)
        for profile in self.plan['profiles']:
            original = self.runner.build_harbor_command(suite, profile=profile,
                agent='opencode', model=self.plan['agent']['model'], jobs_dir=self.root / 'out/runs',
                job_name='q001-' + profile, n_attempts=1, max_retries=0, harbor_executable='harbor',
                zvec_grep_package='candidate.tgz', zvec_grep_package_sha256=self.plan['candidate_sha256'],
                embedding_model=self.plan['embedding']['model'], embedding_endpoint=self.plan['embedding']['endpoint'])
            wrapped = pilot.build_command(self.runner, suite, profile, self.root,
                self.root / 'out', 'candidate.tgz', self.plan['candidate_sha256'], self.plan, 'harbor')
            restored = wrapped[:-4]
            restored[restored.index('--agent') + 1] = original[original.index('--agent') + 1]
            self.assertEqual(restored, original)
            config = json.loads(next(x.split('=', 1)[1] for x in wrapped if x.startswith('opencode_config=')))
            self.assertEqual(bool(config.get('mcp')), profile == 'zvec-grep')
            self.assertIn('collect_session_usage=true', wrapped)
            self.assertIn('OPENAI_API_KEY=${OPENAI_API_KEY}', wrapped)

    def test_inventory_detects_edits_additions_links_and_contamination(self):
        expected = [{'path': 'a.cc', 'bytes': 1, 'sha256': 'hash'}]
        observed = {'files': [dict(expected[0], kind='file')],
                    'zg_executable_present': False, 'zg_home_present': False, 'mcp_config_present': False}
        self.assertTrue(self.compare_inventory(observed, expected, 'baseline')['valid'])
        for change in ('edit', 'addition', 'link', 'zg'):
            got = copy.deepcopy(observed)
            if change == 'edit': got['files'][0]['sha256'] = 'changed'
            if change == 'addition': got['files'].append({'path': 'answer.md', 'kind': 'file'})
            if change == 'link': got['files'].append({'path': '.zvec-grep/ref', 'kind': 'symlink'})
            if change == 'zg': got['zg_executable_present'] = True
            self.assertFalse(self.compare_inventory(got, expected, 'baseline')['valid'])
        observed['files'].append({'path': '.zvec-grep/index.db', 'kind': 'file', 'bytes': 4})
        self.assertTrue(self.compare_inventory(observed, expected, 'zvec-grep')['valid'])

    def terminal_fixture(self):
        events = [
            {'type': 'step_start', 'sessionID': 'ses_root', 'part': {'messageID': 'msg_final'}},
            {'type': 'text', 'sessionID': 'ses_root', 'part': {'messageID': 'msg_final', 'text': 'Final answer'}},
            {'type': 'step_finish', 'sessionID': 'ses_root', 'part': {'messageID': 'msg_final', 'reason': 'stop'}}]
        trajectory = {'session_id': 'ses_root', 'steps': [{'source': 'agent', 'message': 'Final answer'}]}
        usage = {'root_session_id': 'ses_root'}
        snapshot = [{'kind': 'message', 'data': {'id': 'msg_final', 'session_id': 'ses_root', 'finished': True, 'has_error': False}}]
        export = {'info': {'id': 'ses_root'}, 'messages': [{'info': {
            'role': 'assistant', 'id': 'msg_final', 'sessionID': 'ses_root',
            'time': {'completed': 123}, 'finish': 'stop'}, 'parts': [{'type': 'text', 'text': 'Final answer'}]}]}
        return events, trajectory, usage, snapshot, export

    def extract(self, data):
        events, *rest = data
        path = self.root / 'stream.jsonl'
        path.write_text('\n'.join(json.dumps(e) for e in events) + '\n')
        return pilot.final_answer(path, *rest)

    def test_final_answer_requires_root_stream_atif_db_export_agreement(self):
        self.assertEqual(self.extract(self.terminal_fixture())['answer'], 'Final answer')
        for mutation in ('child', 'partial', 'error', 'length', 'tool', 'atif', 'db', 'export'):
            data = self.terminal_fixture()
            events, trajectory, usage, snapshot, export = data
            if mutation == 'child': events[-1]['sessionID'] = 'ses_child'
            if mutation == 'partial': events.pop()
            if mutation == 'error': events.append({'type': 'error', 'sessionID': 'ses_root'})
            if mutation == 'length': events[-1]['part']['reason'] = 'length'
            if mutation == 'tool': events.insert(2, {'type': 'tool_use', 'sessionID': 'ses_root', 'part': {'messageID': 'msg_final'}})
            if mutation == 'atif': trajectory['steps'][-1]['message'] = 'Different answer'
            if mutation == 'db': snapshot[0]['data']['finished'] = False
            if mutation == 'export': export['messages'][0]['info']['time'] = {}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.extract(data)

    def test_malformed_event_not_silently_skipped(self):
        data = self.terminal_fixture()
        self.extract(data)
        path = self.root / 'stream.jsonl'
        path.write_text(path.read_text() + '{"type":')
        with self.assertRaisesRegex(ValueError, 'malformed'):
            pilot.final_answer(path, *data[1:])

    def prepare_fixture(self):
        out = self.root / 'prepared'
        pilot.prepare(self.plan, self.path, REPO, MAIN_ROOT, ZVEC_REPO, out)
        return out

    def test_real_q001_export_source_only_and_dry_run_both_slots(self):
        prepared = self.prepare_fixture()
        env = prepared / 'tasks/zvec-q001/environment'
        self.assertEqual({p.name for p in env.iterdir()}, {'Dockerfile', 'source'})
        self.assertEqual(pilot.digest(prepared / 'tasks/zvec-q001/instruction.md'), self.plan['question_sha256'])
        self.assertEqual(len(pilot.file_inventory(env / 'source')), 1232)
        self.assertFalse((env / 'source/.git').exists())
        candidate = self.root / 'candidate.tgz'
        candidate.write_bytes(b'fixture')
        run = self.root / 'run'
        result = pilot.run_pair(self.plan, self.path, MAIN_ROOT, prepared, run, candidate, dry_run=True)
        self.assertEqual([s['status'] for s in result['slots']], ['dry_run', 'dry_run'])
        self.assertEqual([s['profile'] for s in result['slots']], self.plan['profiles'])
        with self.assertRaisesRegex(ValueError, 'reruns'):
            pilot.run_pair(self.plan, self.path, MAIN_ROOT, prepared, run, candidate, dry_run=True)
        (env / 'source/leaked-reference.json').write_text('forbidden extra')
        with self.assertRaisesRegex(ValueError, 'unexpected content'):
            pilot.validate_prepared(prepared, self.path)

    def test_slot_timeout_kills_group_and_retains_both_failures(self):
        candidate = self.root / 'candidate.tgz'
        candidate.write_bytes(b'fixture')
        self.plan['slot_timeout_sec'] = 0.1  # exercise timeout without waiting an hour
        with patch.object(pilot, 'validate_prepared'), patch.object(pilot, 'validate_runtime_inputs'), \
                patch.object(pilot, '_execute_slot', slow_slot):
            result = pilot.run_pair(self.plan, self.path, MAIN_ROOT, self.root,
                                    self.root / 'run', candidate)
        self.assertEqual([s['status'] for s in result['slots']], ['timeout', 'timeout'])

    def test_cleanup_uncertainty_stops_second_slot(self):
        candidate = self.root / 'candidate.tgz'
        candidate.write_bytes(b'fixture')
        self.plan['slot_timeout_sec'] = 0.1
        with patch.object(pilot, 'validate_prepared'), patch.object(pilot, 'validate_runtime_inputs'), \
                patch.object(pilot, '_execute_slot', slow_slot), \
                patch.object(pilot, 'cleanup_slot_containers', return_value={'complete': False}):
            result = pilot.run_pair(self.plan, self.path, MAIN_ROOT, self.root,
                                    self.root / 'run', candidate)
        self.assertEqual([s['status'] for s in result['slots']], ['timeout', 'not_started'])

    def test_docker_cleanup_scopes_ids_and_never_inspects_env(self):
        config = self.root / 'runs/q001-baseline/zvec-q001__ABC/config.json'
        pilot.write(config, {})
        fields = ['cid', '/solver', 'image-sha', [], True]
        replies = ['cid\n', '\t'.join(json.dumps(x) for x in fields), 'cid\n', 'cid\n', '']
        with patch.object(pilot.subprocess, 'check_output', side_effect=replies) as call, \
                patch.object(pilot.subprocess, 'run') as stopped:
            stopped.return_value.returncode = 0
            known = {}
            pilot.capture_slot_containers(self.root, 'baseline', known)
            result = pilot.cleanup_slot_containers(self.root, 'baseline', known)
        self.assertTrue(result['complete'])
        self.assertEqual(result['stopped'][0]['id'], 'cid')
        commands = [args.args[0] for args in call.call_args_list]
        self.assertIn('label=com.docker.compose.project=zvec-q001__abc__env', commands[0])
        self.assertFalse(any('Config.Env' in str(c) for c in commands))
        stopped.assert_called_once()

    def test_mount_allowlist_rejects_private_home_and_wrong_profile(self):
        container = {'trial_dir': str(self.root / 'trial'), 'mounts': [
            {'Destination': '/root/.nvm', 'Type': 'volume', 'Name': 'zg-bench-opencode-1-baseline-linux'}]}
        pilot.check_mounts({'cid': container}, 'baseline', self.plan)
        container['mounts'].append({'Destination': '/root', 'Source': '/private/home', 'Type': 'bind'})
        with self.assertRaisesRegex(ValueError, 'unapproved'):
            pilot.check_mounts({'cid': container}, 'baseline', self.plan)

    def test_native_qwen_terminal_fixture_when_available(self):
        fixture = NATIVE_FIXTURE
        usage = pilot.load(fixture / 'agent/session-usage.json')
        answer = pilot.final_answer(fixture / 'agent/opencode.txt',
            pilot.load(fixture / 'agent/trajectory.json'), usage,
            pilot.load(fixture / 'agent/opencode-session-snapshot.json'),
            pilot.load(fixture / (usage['root_session_id'] + '.export.json')))
        self.assertEqual(answer['root_session_id'], usage['root_session_id'])

    def test_failed_or_partial_jobs_are_retained_and_not_quality_success(self):
        run = self.root / 'run'
        run.mkdir()
        pilot.write(run / 'execution.json', {'plan_sha256': pilot.digest(self.path),
            'dry_run': False, 'slots': [{'profile': p, 'status': 'failed'} for p in self.plan['profiles']]})
        (run / 'partial-job.log').write_text('partial evidence')
        result = pilot.collect(self.plan, self.path, MAIN_ROOT, run, self.root / 'reports')
        self.assertFalse(result['structural_valid'])
        self.assertIsNone(result['protocol_valid'])
        self.assertFalse(result['quality_evaluated'])
        self.assertEqual(len(result['profiles']), 2)
        self.assertTrue(any(r['path'] == 'partial-job.log' for r in result['raw_files']))
        self.assertEqual((run / 'partial-job.log').read_text(), 'partial evidence')

    def test_collect_native_fixture_complete_pair_remains_pending_tool_audit(self):
        fixture = NATIVE_FIXTURE
        run = self.root / 'run'
        run.mkdir()
        pilot.write(run / 'execution.json', {'plan_sha256': pilot.digest(self.path), 'dry_run': False,
            'slots': [{'profile': p, 'status': 'exited', 'returncode': 0, 'container_cleanup': {'complete': True}}
                      for p in self.plan['profiles']]})
        for profile in self.plan['profiles']:
            job = run / 'runs' / ('q001-' + profile)
            trial = job / 'zvec-q001__fixture'
            shutil.copytree(fixture / 'agent', trial / 'agent')
            usage = pilot.load(trial / 'agent/session-usage.json')
            mapping = {s['session_id']: s['session_id'] + '_' + profile for s in usage['sessions']}
            for file in (trial / 'agent').glob('*'):
                if file.is_file():
                    value = file.read_text()
                    for old, new in mapping.items(): value = value.replace(old, new)
                    file.write_text(value)
            exports = trial / 'qa-session-exports'
            exports.mkdir()
            for old, new in mapping.items():
                value = (fixture / (old + '.export.json')).read_text()
                for src, dst in mapping.items(): value = value.replace(src, dst)
                (exports / (new + '.json')).write_text(value)
            usage = pilot.load(trial / 'agent/session-usage.json')
            total = usage['total']
            context = {'n_input_tokens': total['input_tokens'], 'n_output_tokens': total['output_tokens'],
                'n_cache_tokens': total['cache_read_tokens'] + total['cache_write_tokens'],
                'cost_usd': total['cost_usd'], 'metadata': {'session_usage': usage}}
            pilot.write(trial / 'result.json', {'task_name': 'zvec-q001', 'trial_name': trial.name,
                'finished_at': '2026-10-20T01:02:00Z', 'exception_info': None,
                'verifier_result': {'rewards': {'reward': 1}}, 'agent_result': context,
                'agent_execution': {'started_at': '2026-10-20T01:00:00Z', 'finished_at': '2026-10-20T01:02:00Z'}})
            pilot.write(job / 'result.json', {'finished_at': '2026-10-20T01:02:00Z', 'n_total_trials': 1,
                'stats': {'n_completed_trials': 1, 'n_errored_trials': 0}})
            runtime = {'opencode': {'version': '1.18.4', 'binary_sha256': 'same-linux-binary'},
                       'resolved_config_sha256': 'config-hash'}
            if profile == 'zvec-grep': runtime['zg'] = {'version': 'candidate', 'binary_sha256': 'rust-binary'}
            pilot.write(trial / 'qa-source-audit.json', {'valid': True, 'profile': profile,
                'audit_wall_seconds': 1, 'export_wall_seconds': 1, 'session_exports_complete': True,
                'exported_root_session_id': usage['root_session_id'], 'exported_sessions': sorted(mapping.values()),
                'before': {'observed': {'runtime': runtime}}, 'after': {'observed': {'runtime': runtime}}})
            pilot.write(run / (profile + '-containers.json'), {'cid': {'trial_dir': str(trial), 'mounts': [
                {'Destination': '/root/.nvm', 'Type': 'volume', 'Name': 'zg-bench-opencode-' + profile + '-linux'}]}})
        result = pilot.collect(self.plan, self.path, MAIN_ROOT, run, self.root / 'reports')
        self.assertTrue(result['structural_valid'], result)
        self.assertIsNone(result['protocol_valid'])
        self.assertFalse(result['quality_evaluated'])
        self.assertTrue((self.root / 'reports/native-pair.json').is_file())


if __name__ == '__main__':
    unittest.main()
