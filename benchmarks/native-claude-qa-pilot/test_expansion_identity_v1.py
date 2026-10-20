"""Synthetic native trial shapes plus a read-only retained q001 rejection."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import unittest

import expansion_identity_v1 as identity
import test_expansion_v1 as preparation_tests


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = preparation_tests.ExpansionTests('runTest')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.output = self.fixture.root / 'run'
        self.remote = '/home/runner/frozen-reserves/q002'
        self.order = ['baseline', 'zvec-grep']
        self.make_run()

    def write(self, relative, data):
        identity.pilot.write(self.output / relative, data)

    def change(self, relative, fn):
        data = identity.pilot.load(self.output / relative); fn(data); self.write(relative, data)

    def make_run(self, task_id='q002', order=None):
        order = order or self.order
        root = self.fixture.output / task_id
        metadata = identity.pilot.load(root / 'prepared.json')
        source = identity.pilot.load(root / 'source-manifest.json')['files']
        slots, rows = [], []
        for profile in order:
            trial_name = 'zvec-' + task_id + '__fixture'
            relative = 'runs/q001-' + profile + '/' + trial_name  # Legacy job name intentionally retained.
            job_dir = '/home/runner/run/runs/q001-' + profile
            task_path = self.remote + '/tasks/zvec-' + task_id
            agent = {'name': 'native_claude.agents:' + ('QABaseline' if profile == 'baseline' else 'QAZvec'),
                     'model_name': 'qwen3.8-max', 'kwargs': {'qa_profile': profile,
                        'qa_source_manifest': self.remote + '/source-manifest.json'}}
            command = ['harbor', 'run', '--path', self.remote + '/tasks', '--agent', agent['name'],
                       '--include-task-name', 'zvec-' + task_id, '--n-attempts', '1', '--max-retries', '0',
                       '--jobs-dir', '/home/runner/run/runs', '--job-name', 'q001-' + profile,
                       '--agent-kwarg', 'qa_profile=' + profile,
                       '--agent-kwarg', 'qa_source_manifest=' + self.remote + '/source-manifest.json']
            slot = {'profile': profile, 'status': 'exited', 'returncode': 0, 'command': command}
            slots.append(slot); self.write(profile + '-slot.json', slot)
            config = {'task': {'path': task_path, 'source': 'tasks'}, 'trial_name': trial_name,
                      'trials_dir': job_dir, 'agent': agent}
            self.write(relative + '/config.json', config)
            self.write(relative + '/lock.json', {'task': {'name': 'zvec-' + task_id, 'type': 'local',
                'path': task_path, 'source': 'tasks', 'digest': metadata['native_task_hashes']['lock_task_digest']}, 'agent': agent})
            self.write(relative + '/result.json', {'task_name': 'zvec-' + task_id, 'trial_name': trial_name,
                'task_id': {'path': task_path}, 'task_checksum': metadata['native_task_hashes']['result_task_checksum'], 'config': config})
            observed = [dict(x, kind='file') for x in source]
            if profile == 'zvec-grep':
                observed.append({'path': '.zvec-grep/generated', 'kind': 'file', 'bytes': 10})
                observed.append({'path': '.zg-bench-index.ignore', 'kind': 'file', 'bytes': 2, 'sha256': 'a' * 64})
            audit = {'profile': profile, 'engine': 'native-claude',
                     'before': {'observed': {'files': observed}}, 'after': {'observed': {'files': observed}}}
            self.write(relative + '/qa-source-audit.json', audit)
            terminal = {'type': 'result', 'subtype': 'success', 'is_error': False,
                        'session_id': profile + '-root', 'result': 'Synthetic final ' + task_id + ' ' + profile}
            path = self.output / relative / 'agent/claude-code.txt'; path.parent.mkdir()
            path.write_text(json.dumps(terminal) + '\n')
            self.write('collection/' + profile + '-root-result.json', terminal)
            answer = terminal['result'].encode()
            (self.output / 'collection' / (profile + '-answer.txt')).write_bytes(answer)
            rows.append({'profile': profile, 'slot_status': 'exited', 'trial_directory': relative,
                'answer_present': True, 'answer_file': profile + '-answer.txt',
                'answer_sha256': hashlib.sha256(answer).hexdigest()})
        provenance = {'protocol_id': 'synthetic-future-expansion', 'plan_sha256': 'a' * 64,
                      'offline_proof_sha256': 'b' * 64}
        self.write('execution.json', dict(provenance, slots=slots, dry_run=False))
        self.write('collection/collection.json', dict(provenance, profiles=rows))

    def verify(self, task_id='q002', **overrides):
        args = {'reference_prepared': self.fixture.reference, 'recorded_prepared_root': self.remote,
                'expected_profiles': self.order, 'repo_root': self.fixture.repo}
        args.update(overrides)
        return identity.verify(self.fixture.output, task_id, self.output, **args)

    def test_both_tasks_and_reverse_order_use_actual_identity_not_legacy_prefix(self):
        before = identity.pilot.file_inventory(self.output)
        self.assertTrue(self.verify()['task_identity_valid'])
        self.assertEqual(before, identity.pilot.file_inventory(self.output))
        shutil.rmtree(self.output)
        self.remote = '/home/runner/frozen-reserves/q003'; self.order.reverse()
        self.make_run('q003', self.order)
        report = self.verify('q003')
        self.assertTrue(report['task_identity_valid'], report['errors'])
        self.assertEqual([r['profile'] for r in report['profiles']], self.order)
        self.assertTrue(all(r['task_id'] == 'q003' and r['answer_sha256'] for r in report['profiles']))

    def test_cross_task_and_unbound_remote_root_or_order_fail(self):
        for task_id, extra in [('q003', {}), ('q001', {}), ('q002', {'recorded_prepared_root': '/other/q002'}),
                               ('q002', {'expected_profiles': list(reversed(self.order))})]:
            with self.subTest(task=task_id, extra=extra):
                self.assertFalse(self.verify(task_id, **extra)['task_identity_valid'])

    def test_wrong_or_duplicate_command_include_and_wrong_manifest_fail(self):
        original = identity.pilot.load(self.output / 'execution.json')
        mutations = [lambda c: c.__setitem__(c.index('--include-task-name') + 1, 'zvec-q003'),
                     lambda c: c.extend(['--include-task-name=zvec-q002']),
                     lambda c: c.__setitem__(c.index('--path') + 1, '/wrong/tasks'),
                     lambda c: c.extend(['--agent-kwarg', 'qa_source_manifest=/wrong/manifest'])]
        for mutate in mutations:
            data = deepcopy(original); mutate(data['slots'][0]['command']); self.write('execution.json', data)
            self.assertFalse(self.verify()['task_identity_valid'])
        self.write('execution.json', original)

    def test_native_lock_checksum_config_source_and_answer_swaps_fail(self):
        trial = 'runs/q001-baseline/zvec-q002__fixture/'
        mutations = [
            ('lock.json', lambda d: d['task'].__setitem__('digest', 'sha256:' + '0' * 64)),
            ('result.json', lambda d: d.__setitem__('task_checksum', '0' * 64)),
            ('config.json', lambda d: d['task'].__setitem__('path', self.remote + '/tasks/zvec-q003')),
            ('result.json', lambda d: d['config']['agent']['kwargs'].__setitem__('qa_profile', 'zvec-grep')),
            ('qa-source-audit.json', lambda d: d['before']['observed']['files'][0].__setitem__('sha256', '0' * 64)),
            ('qa-source-audit.json', lambda d: d['after']['observed']['files'].clear()),
            ('qa-source-audit.json', lambda d: d['after']['observed']['files'].append({'path': '.zvec-grep/not-allowed', 'kind': 'file', 'bytes': 0})),
        ]
        for name, mutate in mutations:
            path = trial + name; original = identity.pilot.load(self.output / path)
            with self.subTest(name=name):
                self.change(path, mutate); self.assertFalse(self.verify()['task_identity_valid'])
            self.write(path, original)
        self.change('collection/collection.json', lambda d: d['profiles'][0].__setitem__('answer_sha256', '0' * 64))
        self.assertFalse(self.verify()['task_identity_valid'])

    def test_missing_and_partial_evidence_retains_both_profiles_and_errors(self):
        (self.output / 'runs/q001-baseline/zvec-q002__fixture/result.json').unlink()
        self.change('runs/q001-baseline/zvec-q002__fixture/qa-source-audit.json', lambda d: d.__setitem__('after', None))
        self.change('execution.json', lambda d: d['slots'][1].update(status='not_started', command=None))
        self.change('collection/collection.json', lambda d: d['profiles'][1].pop('trial_directory'))
        report = self.verify()
        self.assertFalse(report['task_identity_valid']); self.assertEqual(len(report['profiles']), 2)
        self.assertIn('baseline:source_files:after', report['errors'])
        self.assertIn('zvec-grep:retained_trial_path', report['errors'])
        self.assertTrue(any('result.json' in error for error in report['errors']))

    def test_extra_trial_and_duplicate_source_rows_are_rejected(self):
        (self.output / 'runs/unknown/another-trial').mkdir(parents=True)
        self.assertIn('all_actual_trials_accounted_for', self.verify()['errors'])
        shutil.rmtree(self.output / 'runs/unknown')
        path = 'runs/q001-zvec-grep/zvec-q002__fixture/qa-source-audit.json'
        self.change(path, lambda d: d['before']['observed']['files'].append(d['before']['observed']['files'][0]))
        self.assertIn('zvec-grep:source_files:before', self.verify()['errors'])

    def test_retained_real_q001_schema_cannot_pass_as_reserve_task(self):
        self.output = Path(identity.__file__).parent / 'results/20261020-live-v2/raw/live'
        report = self.verify(recorded_prepared_root='/home/runner/work/_temp/qa-prepared')
        self.assertFalse(report['task_identity_valid']); self.assertEqual(len(report['profiles']), 2)
        self.assertIn('baseline:command:--include-task-name', report['errors'])
        self.assertIn('baseline:lock_task_identity', report['errors'])
        self.assertIn('zvec-grep:result_task_identity', report['errors'])
        self.assertIn('zvec-grep:source_files:after', report['errors'])
        self.assertNotIn('execution_collection_profile_order', report['errors'])


if __name__ == '__main__':
    unittest.main()
