"""Replay retained native bytes and mutate only temporary copies for negatives."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import accounting_v2
from native_claude import accounting as v1

HERE = Path(__file__).parent
ASYNC = HERE / 'results/20261020-async-probe-v1/agent'
R3 = HERE / 'results/20261020-offline-r3/ci/raw/integration/runs'


def load(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))


class AccountingV2Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.agent = Path(self.temporary.name) / 'agent'
        shutil.copytree(ASYNC, self.agent)
        self.root = next((self.agent / 'sessions/projects/-workspace').glob('*.jsonl'))
        self.child = next((self.agent / 'sessions/projects/-workspace').rglob('agent-*.jsonl'))
        self.stream = self.agent / 'claude-code.txt'

    def edit_root(self, change):
        rows = load(self.root)
        change(rows)
        write(self.root, rows)

    def edit_stream(self, change):
        rows = load(self.stream)
        change(rows)
        write(self.stream, rows)

    def launch(self, rows):
        return next(row for row in rows if row.get('toolUseResult', {}).get('status') == 'async_launched')

    def wait(self, rows):
        return next(row for row in rows if 'retrieval_status' in row.get('toolUseResult', {}))

    def notification(self, rows):
        return next(row for row in rows if row.get('subtype') == 'task_notification')

    def remove_notification(self):
        self.edit_stream(lambda rows: rows.__setitem__(slice(None), [r for r in rows if r.get('subtype') != 'task_notification']))

    def remove_wait(self):
        root = load(self.root)
        wait_result = self.wait(root)
        tid = wait_result['message']['content'][0]['tool_use_id']
        def keep(row):
            message = row.get('message', {})
            content = message.get('content', []) if isinstance(message, dict) else []
            return not any(isinstance(block, dict) and (block.get('id') == tid or block.get('tool_use_id') == tid)
                           for block in content if isinstance(content, list))
        write(self.root, [row for row in root if keep(row)])
        write(self.stream, [row for row in load(self.stream) if keep(row)])

    def assert_incomplete(self, code=None):
        report = accounting_v2.analyze(self.agent)
        self.assertFalse(report['complete'], report)
        self.assertTrue(report['usage_partial'])
        if code:
            self.assertIn(code, {row['code'] for row in report['errors']})
        return report

    def test_actual_async_probe_resolves_only_two_errors_with_identical_totals(self):
        original = v1.analyze(self.agent)
        self.assertEqual({e['code'] for e in original['errors']}, {'unfinished_tool_metadata', 'child_final_not_in_parent_result'})
        report = accounting_v2.analyze(self.agent)
        self.assertTrue(report['complete'], report['errors'])
        self.assertEqual(report['usage'], original['usage'])
        for field in ('messages', 'tools', 'sessions', 'files', 'final', 'root_result_usage'):
            self.assertEqual(report[field], original[field])
        self.assertEqual((report['child_count'], report['usage']['tool_calls']), (1, 4))
        resolved = report['async_completion_reconciliation']['resolved']
        self.assertEqual(len(resolved), 1)
        self.assertEqual({c['kind'] for c in resolved[0]['completion_evidence']}, {'TaskOutput', 'native-task-notification'})
        self.assertFalse(report['async_completion_reconciliation']['notification_usage_counted'])

    def test_both_actual_r3_sync_profiles_are_exact_original_dicts(self):
        for profile in ('baseline', 'zvec-grep'):
            with self.subTest(profile=profile):
                agent = next((R3 / ('q001-' + profile)).glob('*/agent'))
                original = v1.analyze(agent)
                self.assertTrue(original['complete'])
                self.assertEqual(accounting_v2.analyze(agent), original)

    def test_each_observed_completion_channel_alone(self):
        self.remove_notification()
        report = accounting_v2.analyze(self.agent)
        self.assertTrue(report['complete'], report['errors'])
        self.assertEqual({c['kind'] for c in report['async_completion_reconciliation']['resolved'][0]['completion_evidence']}, {'TaskOutput'})
        shutil.rmtree(self.agent)
        shutil.copytree(ASYNC, self.agent)
        self.remove_wait()
        report = accounting_v2.analyze(self.agent)
        self.assertTrue(report['complete'], report['errors'])
        self.assertEqual({c['kind'] for c in report['async_completion_reconciliation']['resolved'][0]['completion_evidence']}, {'native-task-notification'})

    def test_running_child_without_terminal_evidence_remains_unfinished(self):
        self.remove_notification()
        self.edit_root(lambda rows: self.wait(rows)['toolUseResult']['task'].__setitem__('status', 'running'))
        report = self.assert_incomplete('async_completion_missing')
        self.assertIn('unfinished_tool_metadata', {e['code'] for e in report['errors']})

    def test_orphan_child_and_orphan_notification_remain_invalid(self):
        self.child.unlink()
        self.assert_incomplete('missing_child_transcript')
        shutil.rmtree(self.agent)
        shutil.copytree(ASYNC, self.agent)
        def add_orphan(rows):
            event = copy.deepcopy(self.notification(rows))
            event.update(task_id='orphan-child', tool_use_id='orphan-launch', uuid='orphan-notification')
            rows.insert(-1, event)
        self.edit_stream(add_orphan)
        self.assert_incomplete('unmatched_native_task_notification')

    def test_conflicting_notification_is_not_overridden_by_valid_taskoutput(self):
        for field, value in (('task_id', 'wrong-child'), ('tool_use_id', 'wrong-launch'),
                             ('session_id', 'wrong-root'), ('summary', 'wrong final'), ('status', 'failed')):
            with self.subTest(field=field):
                shutil.copyfile(ASYNC / 'claude-code.txt', self.stream)
                self.edit_stream(lambda rows: self.notification(rows).__setitem__(field, value))
                self.assert_incomplete('async_completion_conflict')

    def test_conflicting_taskoutput_is_not_overridden_by_valid_notification(self):
        original_root = next((ASYNC / 'sessions/projects/-workspace').glob('*.jsonl'))
        for field, value in (('task_id', 'wrong-child'), ('task_type', 'remote_agent'), ('status', 'failed'),
                             ('status', 'running'), ('output', 'wrong final'),
                             ('result', 'wrong final'), ('isRawTranscript', True)):
            with self.subTest(field=field):
                shutil.copyfile(original_root, self.root)
                self.edit_root(lambda rows: self.wait(rows)['toolUseResult']['task'].__setitem__(field, value))
                self.assert_incomplete('async_completion_conflict')
        shutil.copyfile(original_root, self.root)
        self.edit_root(lambda rows: self.wait(rows)['message']['content'][0].__setitem__('content', 'forged visible output'))
        self.assert_incomplete('async_completion_conflict')
        shutil.copyfile(original_root, self.root)
        self.edit_root(lambda rows: self.wait(rows)['toolUseResult'].__setitem__('retrieval_status', 'failure'))
        self.assert_incomplete('async_completion_conflict')

    def test_user_text_is_not_native_completion_and_post_terminal_notification_rejected(self):
        self.remove_wait()
        self.edit_stream(lambda rows: self.notification(rows).__setitem__('type', 'user'))
        self.assert_incomplete('async_completion_missing')
        shutil.copyfile(ASYNC / 'claude-code.txt', self.stream)
        # Restore the untouched fixture, then move only the notification after result.
        shutil.copyfile(next((ASYNC / 'sessions/projects/-workspace').glob('*.jsonl')), self.root)
        def after_result(rows):
            notification = self.notification(rows)
            rows.remove(notification)
            rows.append(notification)
        self.edit_stream(after_result)
        self.assert_incomplete('async_completion_conflict')

    def test_resolved_model_and_existing_usage_errors_are_not_relaxed(self):
        self.edit_root(lambda rows: self.launch(rows)['toolUseResult'].__setitem__('resolvedModel', 'not-qwen'))
        self.assert_incomplete('async_launch_or_child_identity_invalid')
        shutil.copyfile(next((ASYNC / 'sessions/projects/-workspace').glob('*.jsonl')), self.root)
        rows = load(self.child)
        next(r for r in reversed(rows) if r.get('type') == 'assistant')['message']['usage'].pop('output_tokens')
        write(self.child, rows)
        self.assert_incomplete('invalid_usage_fields')

    def test_notification_must_follow_bound_launch_start_and_child_final(self):
        for position in ('before-launch', 'before-child-final', 'missing-start', 'wrong-start-identity'):
            with self.subTest(position=position):
                shutil.copyfile(ASYNC / 'claude-code.txt', self.stream)
                def mutate(rows):
                    notification = self.notification(rows)
                    if position == 'missing-start':
                        rows[:] = [r for r in rows if r.get('subtype') != 'task_started']
                    elif position == 'wrong-start-identity':
                        next(r for r in rows if r.get('subtype') == 'task_started')['tool_use_id'] = 'wrong-launch'
                    else:
                        rows.remove(notification)
                        if position == 'before-launch':
                            index = 1
                        else:
                            index = next(i for i, r in enumerate(rows)
                                if r.get('message', {}).get('id', '').endswith('_child_final'))
                        rows.insert(index, notification)
                self.edit_stream(mutate)
                self.assert_incomplete('async_completion_conflict')

    def test_taskoutput_must_finish_before_parent_final(self):
        def move_after_final(rows):
            wait = self.wait(rows)
            rows.remove(wait)
            rows.append(wait)
        self.edit_root(move_after_final)
        self.assert_incomplete('async_completion_conflict')

    def test_taskoutput_argument_cannot_disagree_with_its_completed_result(self):
        def wrong_argument(rows):
            for row in rows:
                message = row.get('message', {})
                content = message.get('content', []) if isinstance(message, dict) else []
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get('name') == 'TaskOutput':
                        block['input']['task_id'] = 'wrong-child'
        self.edit_root(wrong_argument)
        self.edit_stream(wrong_argument)
        # Keep the transcript mirrors consistent: the new rejection must come
        # from the actual args/result identity conflict, not a v1 mirror error.
        self.assertNotIn('stream_block_not_retained', {e['code'] for e in v1.analyze(self.agent)['errors']})
        self.assert_incomplete('async_completion_conflict')


if __name__ == '__main__':
    unittest.main(verbosity=2)
