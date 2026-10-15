"""Two-round stability contracts using synthetic evidence, without model calls."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from swe_qa_fixtures import _judged_task_report, _set_report_trial_judgements
from zg_bench import stability
from zg_bench.metrics.summary import summarize_profile


class StabilityTests(unittest.TestCase):
    def make_round(self, *, reference):
        runner = stability.runner
        reports = {}
        for task in stability._expected_tasks():
            source = _judged_task_report(task['task_id'])
            _set_report_trial_judgements(source, baseline=list(range(10, 101, 10)),
                                        zvec=list(range(10, 101, 10)))
            trial_profile = source['cases'][0]['profiles']['baseline']
            for trial in trial_profile['trials']:
                trial['metrics'].update(input_tokens=trial['trial_index'] * 100,
                                        agent_wall_seconds=trial['trial_index'] * 2)
            profile = summarize_profile(trial_profile['trials'])
            profiles = {arm: copy.deepcopy(profile) for arm in stability.ARMS}
            experiment = copy.deepcopy(runner.EXPERIMENT)
            kind = stability.REFERENCE_KIND if reference else stability.REPEAT_KIND
            experiment['kind'] = kind
            if reference:
                # Historical IQR source hashes are deliberately different.
                experiment['locked_input_sha256']['zg_bench/metrics/summary.py'] = 'old-iqr-hash'
                experiment['locked_input_sha256']['zg_bench/reports/render.py'] = 'old-render-hash'
            identities = {
                arm: dict(source_commit=runner.EXPERIMENT[arm]['source_commit'],
                          package_sha256=runner.EXPERIMENT[arm]['tarball_sha256'],
                          cli_sha256=runner.EXPERIMENT[arm]['cli_sha256'])
                for arm in ('node', 'rust')
            }
            reports[task['task_id']] = dict(
                schema_version=1, kind=kind, model='glm-5.2', task_id=task['task_id'],
                profiles=profiles, judge=runner.judge_metadata(profiles),
                provenance=dict(task=task['task_slug'], model='glm-5.2',
                                repetitions=10, identities=identities, experiment=experiment,
                                harness_commit=stability.REFERENCE_HARNESS if reference else 'f' * 40,
                                task_image={key: task['task_id'] + ':' + key for key in stability.TASK_IMAGE_FIELDS},
                                execution=[dict(arm=arm, repetition=r, returncode=0)
                                           for arm in stability.ARMS for r in range(1, 11)]))
        return reports

    def write_round(self, path, reports):
        for task, report in reports.items():
            target = path / task.replace(':', '-') / 'report.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report))

    def test_complete_rounds_same_data_point_estimates_and_independent_uncertainty(self):
        old, new = self.make_round(reference=True), self.make_round(reference=False)
        before = copy.deepcopy((old, new))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_round(root / 'old', old)
            self.write_round(root / 'new', new)
            old, old_sources = stability.load_round(root / 'old', reference=True)
            new, new_sources = stability.load_round(root / 'new', reference=False)
            self.assertEqual((len(old_sources), len(new_sources)), (20, 20))
            result = stability.compare_rounds(old, new, bootstrap_repetitions=50)
        self.assertEqual((old, new), before)
        for arm in stability.ARMS:
            self.assertEqual(result['rounds']['repeat'][arm]['original_count'], 200)
            self.assertEqual(result['rounds']['repeat'][arm]['retained_count'], 160)
            for view in stability.VIEWS:
                for key in stability.METRICS:
                    self.assertEqual(result['round_changes'][arm][view][key]['delta'], 0)
                # Identical source values do not imply paired or identical draws.
                interval = result['bootstrap']['intervals']['round_change/' + arm][view]['judge']
                self.assertLess(interval[0], 0)
                self.assertGreater(interval[1], 0)
        markdown = stability.render(result)
        self.assertIn('An interval containing zero does not establish equivalence', markdown)
        self.assertIn('Per-task raw and trimmed changes', markdown)

    def test_recomputes_ten_to_eight_with_one_shared_selection(self):
        old = self.make_round(reference=True)
        summary, tasks = stability._round_summary(old)
        for task in tasks.values():
            for arm in stability.ARMS:
                self.assertEqual(task[arm]['selection']['retained_trial_indexes'], list(range(2, 10)))
                self.assertEqual(task[arm]['trimmed'], dict(judge=55, agent_wall_seconds=11, input_tokens=550))
        self.assertEqual(summary['rust']['retained_count'], 160)

    def test_identity_protocol_and_reference_harness_mismatches_rejected(self):
        reports = self.make_round(reference=True)
        source = next(iter(reports.values()))
        mutations = (
            lambda r: r['provenance']['identities']['rust'].update(cli_sha256='different'),
            lambda r: r['provenance']['identities']['node'].update(package_sha256='different'),
            lambda r: r['provenance'].update(harness_commit='0' * 40),
            lambda r: r['provenance']['experiment'].update(embedding_dimension=1024),
            lambda r: r['provenance']['experiment'].update(max_parallel_task_jobs=16),
            lambda r: r['provenance']['experiment']['locked_input_sha256'].update({'uv.lock': 'different'}),
            lambda r: r['profiles']['rust']['trials'].pop(),
        )
        for mutate in mutations:
            report = copy.deepcopy(source)
            mutate(report)
            with self.subTest(mutation=mutate), self.assertRaises(Exception):
                stability._validate_protocol(report, reference=True)

    def test_incomplete_or_duplicate_round_rejected(self):
        reports = self.make_round(reference=True)
        reports.pop(next(iter(reports)))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.write_round(path, reports)
            with self.assertRaisesRegex(ValueError, 'Incomplete 20-task round'):
                stability.load_round(path, reference=True)
            self.write_round(path / 'duplicates', reports)
            with self.assertRaisesRegex(ValueError, 'duplicate task'):
                stability.load_round(path, reference=True)

    def test_task_image_changes_rejected_and_cli_writes_only_outputs(self):
        old, new = self.make_round(reference=True), self.make_round(reference=False)
        task = next(iter(new))
        original = new[task]['provenance']['task_image']['repository_commit']
        new[task]['provenance']['task_image']['repository_commit'] = 'different'
        with self.assertRaisesRegex(ValueError, 'Task image protocol differs'):
            stability.compare_rounds(old, new, bootstrap_repetitions=10)
        new[task]['provenance']['task_image']['repository_commit'] = original
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_round(root / 'old', old)
            self.write_round(root / 'new', new)
            before = {p: p.read_bytes() for p in root.rglob('report.json')}
            with patch.dict('os.environ', {'GITHUB_STEP_SUMMARY': str(root / 'summary.md')}):
                self.assertEqual(stability.main([
                    '--reference-reports', str(root / 'old'),
                    '--repeat-reports', str(root / 'new'),
                    '--output', str(root / 'output'), '--bootstrap-repetitions', '10']), 0)
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            self.assertEqual((root / 'summary.md').read_text(), (root / 'output' / 'stability.md').read_text())
            self.assertEqual(json.loads((root / 'output' / 'stability.json').read_text())['task_count'], 20)


if __name__ == '__main__':
    unittest.main()
