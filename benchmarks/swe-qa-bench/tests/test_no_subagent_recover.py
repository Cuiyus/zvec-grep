"""Offline recovery boundaries; no agent, reference service, or judge is invoked."""
from contextlib import contextmanager, ExitStack, redirect_stdout
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from zg_bench import no_subagent_recover as recovery


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class NoSubagentRecoverTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp).resolve()
            args = SimpleNamespace(source=root / 'source', output=root / 'derived',
                                   packages=root / 'packages', task='astropy-38',
                                   source_run=root / 'run.json', source_run_id=39757221956,
                                   expected_source_head='frozen-source-head', qualify_only=False)
            protocol = dict(kind='glm52-subagent-ablation', phase='formal',
                            embedding_model='local/frozen-embedding')
            identities = {arm: dict(runtime=arm, cli_sha256=arm + '-fixed')
                          for arm in ('node', 'rust')}
            run = dict(id=args.source_run_id, head_sha=args.expected_source_head,
                       repository={'full_name': 'Cuiyus/zvec-grep'}, event='workflow_dispatch',
                       html_url='https://example.invalid/formal-run')
            meta = dict(experiment=protocol, identities=identities, task=args.task,
                        repetitions=10, model=recovery.experiment.MODEL,
                        harness_commit=args.expected_source_head,
                        execution=[dict(arm=arm, repetition=rep, returncode=0)
                                   for arm in recovery.experiment.ARMS for rep in range(1, 11)])
            strict = dict(complete=False, expected_slots=40, physical_attempts=41,
                          incomplete_attempts=[{'trial_id': 'unknown-failed-allowed',
                                                'usage': None}])
            qualification = dict(eligible=True, full_attempt_audit_complete=False,
                                 allowed_unknown_attempts=[{'trial_id': 'unknown-failed-allowed'}])
            write(args.source_run, run)
            write(args.source / 'provenance.json', meta)
            write(args.source / 'no-subagent-audit.json', strict)
            write(args.source / '.retry-history/attempt-1/result.json',
                  {'trial_id': 'unknown-failed-allowed', 'error': 'setup failure'})
            trials = {arm: [dict(trial_index=rep, trial_name=f'{arm}-{rep}',
                                 answer=f'existing answer {arm}-{rep}')
                            for rep in range(1, 11)] for arm in recovery.experiment.ARMS}
            pair = dict(profiles={'baseline': {'trials': trials['node']},
                                  'zvec-grep': {'trials': trials['rust']}})
            events = []

            def audit(output, *, expected_slots):
                events.append('audit')
                self.assertEqual(output, args.output)
                self.assertEqual(expected_slots, 40)
                write(output / 'no-subagent-audit.json', strict)
                raise ValueError('Strict physical-attempt audit remains incomplete')

            def qualify(output, *, expected_slots, audit):
                events.append('qualify')
                self.assertEqual(expected_slots, 40)
                self.assertEqual(audit, strict)
                return copy.deepcopy(qualification)

            mocks = SimpleNamespace(
                audit=Mock(side_effect=audit), qualify=Mock(side_effect=qualify),
                collect=Mock(side_effect=lambda *a, **k: events.append('collect') or copy.deepcopy(pair)),
                native_collect=Mock(side_effect=lambda output, *a: copy.deepcopy(trials[output.name])),
                judge=Mock(return_value={}), references=Mock(return_value={args.task: {'task_id': 'astropy:38'}}),
                validate=Mock(), summary=Mock(return_value={'complete': True}),
                execute=Mock(side_effect=AssertionError('Recovery must never dispatch agents')),
            )
            for target, name, replacement in (
                (recovery.experiment, 'configure', Mock(return_value=protocol)),
                (recovery.experiment, 'selected_tasks', Mock(return_value=[args.task])),
                (recovery.native, 'validate_inputs', Mock()),
                (recovery.versions, 'identity', Mock(side_effect=lambda path, arm: identities[arm])),
                (recovery.subprocess, 'check_output', Mock(return_value='collector-head\n')),
                (recovery.subprocess, 'run', mocks.execute),
                (recovery, 'audit', mocks.audit),
                (recovery, 'qualify', mocks.qualify),
                (recovery.versions, 'collect', mocks.collect),
                (recovery.native, 'collect', mocks.native_collect),
                (recovery, 'load_references', mocks.references),
                (recovery.three_arm, 'judge_profiles', mocks.judge),
                (recovery.three_arm, 'judge_metadata', Mock(return_value={})),
                (recovery.experiment, 'validate_report', mocks.validate),
                (recovery.experiment, 'summarize', mocks.summary),
            ):
                stack.enter_context(patch.object(target, name, replacement))
            stack.enter_context(redirect_stdout(io.StringIO()))
            yield SimpleNamespace(args=args, meta=meta, run=run, strict=strict,
                                  qualification=qualification, trials=trials,
                                  mocks=mocks, events=events)
            mocks.execute.assert_not_called()

    def assert_before_judging(self, fixture):
        fixture.mocks.references.assert_not_called()
        fixture.mocks.judge.assert_not_called()
        fixture.mocks.validate.assert_not_called()
        fixture.mocks.summary.assert_not_called()
        self.assertFalse((fixture.args.output / 'report/report.json').exists())

    def test_qualify_only_preserves_source_and_never_calls_judge(self):
        with self.fixture() as f:
            before = recovery.manifest(f.args.source)
            f.args.qualify_only = True
            recovery.main(f.args)
            self.assertEqual(f.events, ['audit', 'qualify', 'collect'])
            self.assert_before_judging(f)
            self.assertEqual(recovery.manifest(f.args.source), before)
            self.assertEqual(json.loads((f.args.output / 'source-manifest.json').read_text()), before)
            self.assertEqual(json.loads((f.args.output / 'original-no-subagent-audit.json').read_text()), f.strict)
            self.assertEqual(json.loads((f.args.output / 'no-subagent-audit.json').read_text()), f.strict)
            meta = json.loads((f.args.output / 'provenance.json').read_text())
            self.assertFalse(meta['no_subagent_audit']['complete'])
            self.assertIsNone(meta['no_subagent_audit']['incomplete_attempts'][0]['usage'])
            self.assertEqual(meta['final_answer_qualification'], f.qualification)
            self.assertEqual(meta['collection_recovery']['new_agent_executions'], 0)
            self.assertEqual(meta['collection_recovery']['source_manifest_sha256'], recovery.canonical_digest(before))
            self.assertEqual(json.loads((f.args.output / 'trials.json').read_text()), f.trials)
            self.assertEqual(f.mocks.collect.call_args.kwargs['repetitions'], 10)

    def test_qualification_and_all_final_answers_are_saved_before_judging(self):
        with self.fixture() as f:
            before = recovery.manifest(f.args.source)

            def judge(trials, reference, output):
                self.assertEqual(f.events, ['audit', 'qualify', 'collect'])
                self.assertEqual(trials, f.trials)
                self.assertEqual(json.loads((output / 'qualification.json').read_text()), f.qualification)
                meta = json.loads((output / 'provenance.json').read_text())
                self.assertTrue(meta['collection_recovery']['qualified_before_judging_at'])
                self.assertFalse(meta['no_subagent_audit']['complete'])
                self.assertEqual(recovery.manifest(f.args.source), before)
                return {}

            f.mocks.judge.side_effect = judge
            recovery.main(f.args)
            f.mocks.judge.assert_called_once()
            self.assertEqual(recovery.manifest(f.args.source), before)
            self.assertTrue((f.args.output / 'report/report.json').exists())

    def test_wrong_workflow_identity_rejected_before_audit_or_scoring(self):
        for key, value in (('id', 1), ('head_sha', 'other-head'),
                           ('repository', {'full_name': 'other/repo'}), ('event', 'push')):
            with self.subTest(key=key), self.fixture() as f:
                f.run[key] = value
                write(f.args.source_run, f.run)
                before = recovery.manifest(f.args.source)
                with self.assertRaisesRegex(ValueError, 'Source workflow identity'):
                    recovery.main(f.args)
                self.assert_before_judging(f)
                f.mocks.audit.assert_not_called()
                f.mocks.qualify.assert_not_called()
                f.mocks.collect.assert_not_called()
                self.assertEqual(recovery.manifest(f.args.source), before)

    def test_wrong_experiment_identity_rejected_before_audit_or_scoring(self):
        for key, value in (('experiment', {}), ('identities', {}), ('task', 'other-1'),
                           ('repetitions', 9), ('model', 'other-model'), ('harness_commit', 'other-head')):
            with self.subTest(key=key), self.fixture() as f:
                f.meta[key] = value
                write(f.args.source / 'provenance.json', f.meta)
                with self.assertRaisesRegex(ValueError, 'different frozen experiment'):
                    recovery.main(f.args)
                self.assert_before_judging(f)
                f.mocks.audit.assert_not_called()
                f.mocks.qualify.assert_not_called()
                f.mocks.collect.assert_not_called()

    def test_failed_missing_or_duplicate_final_slot_rejected_before_scoring(self):
        mutations = {
            'failed': lambda rows: rows[0].update(returncode=1),
            'missing': lambda rows: rows.pop(),
            'duplicate': lambda rows: rows.__setitem__(0, copy.deepcopy(rows[1])),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), self.fixture() as f:
                mutate(f.meta['execution'])
                write(f.args.source / 'provenance.json', f.meta)
                with self.assertRaisesRegex(ValueError, 'Every existing final execution slot'):
                    recovery.main(f.args)
                self.assert_before_judging(f)
                f.mocks.audit.assert_not_called()
                f.mocks.qualify.assert_not_called()
                f.mocks.collect.assert_not_called()

    def test_recomputed_audit_mismatch_rejected_before_qualification_or_scoring(self):
        for raises in (False, True):
            with self.subTest(strict_rejects=raises), self.fixture() as f:
                before = recovery.manifest(f.args.source)

                def different_audit(output, **kwargs):
                    changed = dict(f.strict, physical_attempts=42)
                    write(output / 'no-subagent-audit.json', changed)
                    if raises:
                        raise ValueError('Incomplete but different physical-attempt evidence')
                    return changed

                f.mocks.audit.side_effect = different_audit
                with self.assertRaisesRegex(ValueError, 'exactly reproduce'):
                    recovery.main(f.args)
                self.assert_before_judging(f)
                f.mocks.qualify.assert_not_called()
                f.mocks.collect.assert_not_called()
                self.assertEqual(recovery.manifest(f.args.source), before)

    def test_failed_qualification_saved_and_rejected_before_collection_or_scoring(self):
        with self.fixture() as f:
            before = recovery.manifest(f.args.source)
            f.qualification.update(eligible=False, errors=['denied historical attempt lacks evidence'])
            with self.assertRaisesRegex(ValueError, 'did not qualify'):
                recovery.main(f.args)
            self.assert_before_judging(f)
            f.mocks.collect.assert_not_called()
            f.mocks.native_collect.assert_not_called()
            self.assertEqual(json.loads((f.args.output / 'qualification.json').read_text()), f.qualification)
            self.assertEqual(recovery.manifest(f.args.source), before)

    def test_source_mutation_during_collection_is_detected_before_scoring(self):
        with self.fixture() as f:
            original_collect = f.mocks.collect.side_effect

            def changed_source(*args, **kwargs):
                write(f.args.source / 'new-evidence.json', {'unexpected': 'change'})
                return original_collect(*args, **kwargs)

            f.mocks.collect.side_effect = changed_source
            with self.assertRaisesRegex(ValueError, 'Source evidence changed'):
                recovery.main(f.args)
            self.assert_before_judging(f)


if __name__ == '__main__':
    unittest.main()
