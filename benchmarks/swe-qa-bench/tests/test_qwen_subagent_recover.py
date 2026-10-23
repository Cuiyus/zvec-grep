"""Recovery copies immutable source, preserves grading caches and cannot execute agents."""
from contextlib import ExitStack, redirect_stdout
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from qwen_subagent_fixtures import report, write
from zg_bench import qwen_subagent_recover as recovery
from zg_bench.qwen_subagent_qualification import LIFECYCLE_CONTRACT


class QwenRecoveryTests(unittest.TestCase):
    def fixture(self, root):
        args = SimpleNamespace(source=root / 'source', output=root / 'derived', packages=root / 'packages',
                               task='astropy-38', source_run=root / 'run.json', source_run_id=123456,
                               expected_source_head='f' * 40, qualify_only=True)
        protocol = recovery.experiment.configure('formal')
        value = report(args.source, reps=10)
        meta = value['provenance']
        meta.update(task=args.task, model=recovery.experiment.MODEL, experiment=protocol,
                    model_settings=recovery.experiment.validate_model_settings(),
                    installation_lifecycle_contract=copy.deepcopy(LIFECYCLE_CONTRACT), identities={'rust': {'cli_sha256': 'fixed'}})
        write(args.source / 'provenance.json', meta)
        write(args.source / 'judgements/baseline/1.json', {'already': 'paid judge cache'})
        write(args.source_run, dict(id=args.source_run_id, head_sha=args.expected_source_head,
                                   repository={'full_name': 'Cuiyus/zvec-grep'}, event='workflow_dispatch',
                                   html_url='https://github.com/Cuiyus/zvec-grep/actions/runs/123456'))
        trials = {arm: [dict(trial_index=rep, trial_name=f'{arm}-{rep}', answer='unchanged answer')
                        for rep in range(1, 11)] for arm in recovery.experiment.ARMS}
        return args, trials

    def test_qualify_only_preserves_source_and_judge_cache_without_agents_or_judging(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            args, trials = self.fixture(Path(temp))
            before = recovery.manifest(args.source)
            execute = stack.enter_context(patch.object(recovery.subprocess, 'run', side_effect=AssertionError('no agent runs')))
            stack.enter_context(patch.object(recovery.subprocess, 'check_output', return_value='e' * 40))
            stack.enter_context(patch.object(recovery.experiment.versions, 'identity', return_value={'cli_sha256': 'fixed'}))
            collect = stack.enter_context(patch.object(recovery.experiment, 'collect_trials', return_value=trials))
            finish = stack.enter_context(patch.object(recovery.experiment, 'finish_collection', side_effect=AssertionError('no paid judging')))
            with redirect_stdout(io.StringIO()) as stream:
                recovery.main(args)
            result = json.loads(stream.getvalue())
            self.assertEqual(result['final_answers'], 30)
            self.assertEqual(result['new_agent_executions'], 0)
            self.assertFalse(result['judged'])
            execute.assert_not_called();finish.assert_not_called();collect.assert_called_once()
            self.assertEqual(recovery.manifest(args.source), before)
            self.assertEqual((args.output / 'judgements/baseline/1.json').read_bytes(),
                             (args.source / 'judgements/baseline/1.json').read_bytes())
            meta = json.loads((args.output / 'provenance.json').read_text())
            self.assertEqual(meta['collection_recovery']['source_manifest_sha256'], recovery.canonical_digest(before))
            with self.assertRaisesRegex(ValueError, 'Refusing to overwrite'):
                recovery.prepare(args)

    def test_wrong_run_identity_fails_before_copy_or_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            args, _ = self.fixture(Path(temp))
            wrong = json.loads(args.source_run.read_text());wrong['repository']['full_name'] = 'zvec-ai/zvec-grep'
            write(args.source_run, wrong)
            with self.assertRaisesRegex(ValueError, 'workflow identity'):
                recovery.prepare(args)
            self.assertFalse(args.output.exists())

    def test_source_destination_overlap_cannot_overwrite_raw_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            args, _ = self.fixture(Path(temp));args.output = args.source / 'nested'
            with self.assertRaisesRegex(ValueError, 'separate from source'):
                recovery.prepare(args)

if __name__ == '__main__':
    unittest.main()
