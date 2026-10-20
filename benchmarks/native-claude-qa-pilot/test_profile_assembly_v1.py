"""Check data fidelity and fail-closed boundaries without a model or Docker."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import profile_assembly_v1 as assembly


class ProfileAssemblyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.header = dict(schema_version=1, protocol_id='fixture', plan_sha256='a' * 64,
            candidate_sha256='b' * 64, offline_proof_sha256='c' * 64,
            offline_inputs_sha256='d' * 64, dry_run=False, quality_evaluated=False)
        self.workers = {}
        for profile in assembly.PROFILES:
            root = self.root / profile
            root.mkdir()
            run = root / 'runs' / ('q001-' + profile) / 'trial/agent/subagents'
            run.mkdir(parents=True)
            (run / 'child.jsonl').write_bytes(b'{"type":"fixture"}\n')
            (root / (profile + '-job.log')).write_bytes(b'original trailing whitespace \n\n')
            slot = {'profile': profile, 'status': 'exited', 'returncode': 0,
                    'started_at_unix': 100.25, 'finished_at_unix': 105.75,
                    'command': ['original', '/home/runner/work/_temp/live', profile]}
            assembly.write(root / 'execution.json', dict(self.header, slots=[slot],
                                                        system={'host': profile}))
            self.workers[profile] = root

    def test_original_slots_bytes_and_children_preserved(self):
        before = {p: assembly.inventory(r) for p, r in self.workers.items()}
        receipt = assembly.assemble(self.workers, self.root / 'joint', expected_header=self.header)
        combined = assembly.load(self.root / 'joint/execution.json')
        self.assertEqual(combined['slots'], [assembly.load(self.workers[p] / 'execution.json')['slots'][0]
                                            for p in assembly.PROFILES])
        self.assertEqual(combined['system']['per_profile'],
                         {p: {'host': p} for p in assembly.PROFILES})
        self.assertFalse(receipt['shared_host_claimed'])
        self.assertFalse(receipt['aggregate_pair_time_invented'])
        for profile, root in self.workers.items():
            self.assertEqual(assembly.inventory(root), before[profile])
            self.assertEqual((self.root / 'joint' / (profile + '-job.log')).read_bytes(),
                             (root / (profile + '-job.log')).read_bytes())
            self.assertTrue((self.root / 'joint/runs' / ('q001-' + profile) /
                             'trial/agent/subagents/child.jsonl').is_file())

    def test_failed_slot_is_retained_without_replacement(self):
        path = self.workers['zvec-grep'] / 'execution.json'
        value = assembly.load(path)
        value['slots'][0].update(status='failed', returncode=1, error_type='fixture_failure')
        path.write_text(json.dumps(value))
        assembly.assemble(self.workers, self.root / 'joint', expected_header=self.header)
        actual = assembly.load(self.root / 'joint/execution.json')['slots'][1]
        self.assertEqual(actual, value['slots'][0])

    def test_missing_or_duplicate_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'two_profile'):
            assembly.assemble({'baseline': self.workers['baseline']}, self.root / 'joint',
                              expected_header=self.header)
        path = self.workers['zvec-grep'] / 'execution.json'
        value = assembly.load(path)
        value['slots'][0]['profile'] = 'baseline'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'exact_one_original_slot'):
            assembly.assemble(self.workers, self.root / 'joint', expected_header=self.header)

    def test_cross_plan_or_protocol_is_rejected(self):
        path = self.workers['zvec-grep'] / 'execution.json'
        original = assembly.load(path)
        for key in ('protocol_id', 'plan_sha256'):
            value = copy.deepcopy(original)
            value[key] = 'wrong'
            path.write_text(json.dumps(value))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'header_mismatch'):
                assembly.assemble(self.workers, self.root / 'joint', expected_header=self.header)

    def test_symlink_tree_and_nonfinite_json_are_rejected(self):
        link = self.workers['baseline'] / 'outside-link'
        link.symlink_to(self.workers['zvec-grep'] / 'execution.json')
        with self.assertRaisesRegex(ValueError, 'unsafe_worker_tree'):
            assembly.assemble(self.workers, self.root / 'joint', expected_header=self.header)
        link.unlink()
        bad = self.root / 'bad.json'
        bad.write_text('{"value":NaN}')
        with self.assertRaisesRegex(ValueError, 'nonfinite_json'):
            assembly.load(bad)

    def test_existing_output_is_not_overwritten(self):
        destination = self.root / 'joint'
        destination.mkdir()
        marker = destination / 'keep'
        marker.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            assembly.assemble(self.workers, destination, expected_header=self.header)
        self.assertEqual(marker.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
