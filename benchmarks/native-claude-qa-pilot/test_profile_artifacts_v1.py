"""Synthetic origin/tamper fixtures only; no model, CI, network or outcome."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import profile_artifacts_v1 as artifacts
import profile_assembly_v1 as assembly


class ProfileArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run, self.head = 77, 'a' * 40
        raw = self.root / 'raw/live'
        raw.mkdir(parents=True)
        (raw / 'execution.json').write_bytes(b'{"fixture":true}\n')
        with zipfile.ZipFile(self.root / 'artifact.zip', 'w') as archive:
            archive.write(raw / 'execution.json', 'live/execution.json')
        zp = self.root / 'artifact.zip'
        metadata = {'id': 9, 'name': 'native-q005-v1-77-baseline',
                    'workflow_run': {'id': 77, 'head_sha': self.head},
                    'digest': 'sha256:' + assembly.digest(zp), 'size_in_bytes': zp.stat().st_size}
        run = {'id': 77, 'head_sha': self.head, 'run_attempt': 1, 'event': 'workflow_dispatch',
               'path': artifacts.WORKFLOW, 'repository': {'full_name': artifacts.REPOSITORY}}
        job = {'id': 8, 'name': 'worker (baseline)', 'run_id': 77, 'head_sha': self.head,
               'run_attempt': 1, 'status': 'completed', 'conclusion': 'success',
               'labels': ['ubuntu-24.04'], 'runner_name': 'GitHub Actions fixture', 'runner_id': 10}
        for name, value in [('run-metadata.json', run), ('job-metadata.json', {'jobs': [job]}),
                            ('artifact-metadata.json', metadata)]:
            assembly.write(self.root / name, value)
        receipt = dict(schema_version=1, complete=True, exact_extracted_inventory=True,
            repository=artifacts.REPOSITORY, run_id=77, head_sha=self.head, run_attempt=1,
            profile='baseline', task='q005', job_id=8, runner_id=10, job_conclusion='success',
            artifact_id=9, artifact_sha256=assembly.digest(zp), artifact_bytes=zp.stat().st_size,
            matches_api_digest=True,
            artifact_metadata_sha256=assembly.digest(self.root / 'artifact-metadata.json'),
            files=[{'path': p, **r, 'matches_zip': True}
                   for p, r in assembly.inventory(self.root / 'raw').items()])
        assembly.write(self.root / 'artifact-byte-verification.json', receipt)

    def verify(self):
        return artifacts.verify_retained(self.root, run_id=self.run,
                                         head_sha=self.head, profile='baseline')

    def test_original_zip_metadata_and_raw_all_verified(self):
        self.assertTrue(self.verify()['complete'])

    def test_changed_raw_and_zip_are_rejected(self):
        raw = self.root / 'raw/live/execution.json'
        before = raw.read_bytes()
        raw.write_bytes(before + b' ')
        with self.assertRaisesRegex(ValueError, 'raw_inventory_mismatch'):
            self.verify()
        raw.write_bytes(before)
        with (self.root / 'artifact.zip').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'zip_api_digest_mismatch'):
            self.verify()

    def test_cross_run_head_or_attempt_is_rejected(self):
        for attribute, value in [('run', 78), ('head', 'b' * 40)]:
            before = getattr(self, attribute)
            setattr(self, attribute, value)
            with self.subTest(attribute=attribute), self.assertRaisesRegex(ValueError, 'origin_mismatch'):
                self.verify()
            setattr(self, attribute, before)
        path = self.root / 'run-metadata.json'
        value = assembly.load(path)
        value['run_attempt'] = 2
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'origin_mismatch'):
            self.verify()

    def test_duplicate_inventory_or_raw_symlink_is_rejected(self):
        path = self.root / 'artifact-byte-verification.json'
        original = assembly.load(path)
        value = dict(original, files=original['files'] * 2)
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'raw_inventory_mismatch'):
            self.verify()
        path.write_text(json.dumps(original))
        (self.root / 'raw/link').symlink_to(self.root / 'artifact.zip')
        with self.assertRaisesRegex(ValueError, 'unsafe_worker_tree'):
            self.verify()

    def test_zip_traversal_member_is_rejected(self):
        path = self.root / 'unsafe.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('../outside', b'fixture')
        with zipfile.ZipFile(path) as archive, self.assertRaisesRegex(ValueError, 'unsafe_path'):
            artifacts.zip_members(archive)


if __name__ == '__main__':
    unittest.main()
