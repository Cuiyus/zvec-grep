"""Local Git/export and identity checks only; never a solver or Docker run."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import expansion_v1 as expansion


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'benchmark'; self.git = self.root / 'source-git'
        self.reference = self.root / 'reference'; self.output = self.root / 'prepared'
        self.git.mkdir()
        self.command('init', '-q')
        self.command('config', 'user.email', 'fixture@example.invalid')
        self.command('config', 'user.name', 'Fixture')
        tasks = []
        for task_id in expansion.TASK_IDS:
            content = ('source for ' + task_id + '\n').encode()
            (self.git / 'source.txt').write_bytes(content)
            self.command('add', 'source.txt'); self.command('commit', '-qm', task_id)
            commit = self.command('rev-parse', 'HEAD').strip()
            instruction = self.repo / 'benchmarks/zvec-issue-qa/tasks' / task_id / 'instruction.md'
            instruction.parent.mkdir(parents=True); instruction.write_text('Question ' + task_id + '\n')
            evaluator = self.repo / 'benchmarks/zvec-issue-qa/evaluator' / (task_id + '.json')
            span = {'id': 'source', 'path': 'source.txt', 'start_line': 1, 'end_line': 1,
                    'file_sha256': self.digest(content), 'span_sha256': self.digest(content)}
            expansion.pilot.write(evaluator, {'task_id': task_id, 'source_commit': commit,
                                             'evidence_spans': [span], 'reference_answer': 'host-only gold'})
            inventory = [{'path': 'source.txt', 'bytes': len(content), 'sha256': self.digest(content)}]
            tasks.append({'id': task_id, 'source_repository': 'https://example.invalid/source.git',
                'source_commit': commit, 'instruction': 'tasks/' + task_id + '/instruction.md',
                'instruction_sha256': expansion.pilot.digest(instruction),
                'evaluator': 'evaluator/' + task_id + '.json', 'evaluator_sha256': expansion.pilot.digest(evaluator),
                'source_file_count': 1, 'source_inventory_sha256': self.digest(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()),
                'forbidden_corpus_markers': ['host-only gold']})
        expansion.pilot.write(self.repo / expansion.DATASET, {'tasks': tasks})
        expansion.pilot.write(self.repo / expansion.ORIGINAL_PLAN, {'fixture': True})
        exporter = self.repo / expansion.EXPORTER
        shutil.copyfile(expansion.REPO / expansion.EXPORTER, exporter)
        for name, relative in expansion.TEMPLATES.items():
            path = self.reference / relative; path.parent.mkdir(parents=True, exist_ok=True)
            # Exact original small templates; their independent pins are not mocked.
            if name == 'Dockerfile': content = b'FROM ubuntu:24.04\nARG DEBIAN_FRONTEND=noninteractive\nRUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git python3 libgomp1 libstdc++6 && rm -rf /var/lib/apt/lists/*\nCOPY source/ /workspace/\nWORKDIR /workspace\n'
            elif name == 'test.sh': content = b'#!/usr/bin/env bash\nset -euo pipefail\nmkdir -p /logs/verifier\ntest -d /workspace\necho 1 > /logs/verifier/reward.txt\n'
            elif name == 'index.ignore': content = b'# Fixed benchmark fixtures that zvec-grep cannot decode as text.\n.zg-bench-index.ignore\ntests/regrtest_data/unicode/pep_bidirectional_utf_16_bom.txt\ntests/regrtest_data/unicode/pep_bidirectional_utf_32_bom.txt\ntests/mail/attachments/file_png.txt\n'
            elif name == 'suite.yaml': content = b'name: opencode-q001-development\npath: tasks\nindex_ignore_file: index.ignore\ntiers:\n  smoke:\n    tasks: [zvec-q001]\n'
            else: content = b'version = "1.0"\n[metadata]\ntask_id = "zvec-q001"\n[agent]\ntimeout_sec = 1800.0\n[verifier]\ntimeout_sec = 60.0\n[environment]\nbuild_timeout_sec = 1200.0\n'
            path.write_bytes(content)
        expansion.pilot.write(self.reference / 'prepared.json', {
            'plan_sha256': expansion.pilot.digest(self.repo / expansion.ORIGINAL_PLAN),
            'files': expansion.pilot.file_inventory(self.reference)})
        self.pins = {p: expansion.pilot.digest(self.repo / p) for p in expansion.PINS}
        self.pin_patch = patch.object(expansion, 'PINS', self.pins); self.pin_patch.start()
        self.addCleanup(self.pin_patch.stop)

    @staticmethod
    def digest(content): return hashlib.sha256(content).hexdigest()

    def command(self, *args):
        return subprocess.check_output(['git', '-C', str(self.git), *args], text=True, stderr=subprocess.DEVNULL)

    def prepare(self):
        return expansion.prepare(self.reference, self.git, self.output, self.repo)

    def validate(self, output=None):
        return expansion.validate(output or self.output, self.reference, self.repo)

    def test_full_group_exports_distinct_commits_and_exact_templates_and_native_hashes(self):
        before = expansion.pilot.file_inventory(self.reference)
        group = self.prepare()
        self.assertEqual(group['task_ids'], ['q002', 'q003'])
        self.assertFalse(group['solver_executed']); self.assertFalse(group['paid_execution_authorized'])
        self.assertEqual(group, self.validate())
        self.assertEqual(before, expansion.pilot.file_inventory(self.reference))
        checksums = []
        for task_id in expansion.TASK_IDS:
            root = self.output / task_id; task = root / 'tasks' / ('zvec-' + task_id)
            metadata = expansion.pilot.load(root / 'prepared.json')
            self.assertEqual(metadata['native_task_hashes'], expansion.task_hashes(task))
            self.assertEqual(metadata['native_task_hashes']['native_task_name'], 'zvec-' + task_id)
            self.assertEqual((task / 'environment/source/source.txt').read_text(), 'source for ' + task_id + '\n')
            self.assertEqual((task / 'environment/Dockerfile').read_bytes(), (self.reference / expansion.TEMPLATES['Dockerfile']).read_bytes())
            self.assertEqual((task / 'tests/test.sh').read_bytes(), (self.reference / expansion.TEMPLATES['test.sh']).read_bytes())
            self.assertEqual((root / 'index.ignore').read_bytes(), (self.reference / 'index.ignore').read_bytes())
            self.assertNotIn('plan_sha256', metadata)
            checksums.append(metadata['native_task_hashes']['result_task_checksum'])
        self.assertNotEqual(*checksums)
        with self.assertRaises(FileExistsError): self.prepare()

    def test_question_source_missing_file_wrong_suite_and_hidden_gold_fail_closed(self):
        self.prepare()
        mutations = {
            'question': lambda root: (root / 'tasks/zvec-q002/instruction.md').write_text('Question q003\n'),
            'source': lambda root: (root / 'tasks/zvec-q002/environment/source/source.txt').write_text('source for q003\n'),
            'missing': lambda root: (root / 'tasks/zvec-q002/environment/source/source.txt').unlink(),
            'suite': lambda root: (root / 'suite.yaml').write_bytes(expansion._suite('q003')),
            'gold_in_source': lambda root: (root / 'tasks/zvec-q002/environment/source/.hidden-rubric').write_text('host-only gold'),
            'gold_in_image': lambda root: (root / 'tasks/zvec-q002/environment/gold.json').write_text('host-only gold'),
            'wrong_task_metadata': lambda root: (root / 'tasks/zvec-q002/task.toml').write_bytes((root / 'tasks/zvec-q002/task.toml').read_bytes().replace(b'zvec-q002', b'zvec-q003')),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                damaged = self.root / label; shutil.copytree(self.output, damaged); mutate(damaged / 'q002')
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.validate(damaged)

    def test_cross_task_manifest_and_metadata_cannot_substitute_for_selected_task(self):
        self.prepare()
        target = self.output / 'q002/source-manifest.json'
        shutil.copyfile(self.output / 'q003/source-manifest.json', target)
        with self.assertRaisesRegex(ValueError, 'task_source_identity_mismatch:q002'): self.validate()

    def test_metadata_task_hash_and_group_membership_are_checked(self):
        self.prepare()
        for field, replacement in [('task_id', 'q003'), ('native_task_hashes', {'lock_task_digest': 'sha256:' + '0' * 64})]:
            damaged = self.root / field; shutil.copytree(self.output, damaged)
            path = damaged / 'q002/prepared.json'; record = expansion.pilot.load(path); record[field] = replacement
            expansion.pilot.write(path, record)
            with self.assertRaisesRegex(ValueError, 'prepared_metadata_mismatch:q002'): self.validate(damaged)
        shutil.rmtree(self.output / 'q003')
        with self.assertRaises(FileNotFoundError): self.validate()

    def test_frozen_inputs_and_reference_are_checked_before_creating_output(self):
        question = self.repo / 'benchmarks/zvec-issue-qa/tasks/q002/instruction.md'
        original = question.read_bytes(); question.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'frozen_task_asset_changed'): self.prepare()
        self.assertFalse(self.output.exists()); question.write_bytes(original)
        (self.reference / 'index.ignore').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'prepared files changed'): self.prepare()
        self.assertFalse(self.output.exists())
        metadata = expansion.pilot.load(self.reference / 'prepared.json')
        metadata['files'] = [r for r in expansion.pilot.file_inventory(self.reference) if r['path'] != 'prepared.json']
        expansion.pilot.write(self.reference / 'prepared.json', metadata)
        with self.assertRaisesRegex(ValueError, 'reference_template_changed:index.ignore'): self.prepare()
        self.assertFalse(self.output.exists())

    def test_output_must_be_independent_from_source_reference_and_benchmark(self):
        for protected in (self.git, self.reference, self.repo):
            with self.subTest(protected=protected), self.assertRaisesRegex(ValueError, 'must_be_independent'):
                expansion.prepare(self.reference, self.git, protected / 'new', self.repo)


if __name__ == '__main__':
    unittest.main()
