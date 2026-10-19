"""Reconcile the native async completion shapes observed in probe 39337209994.

The frozen v1 reader still owns message identity, usage, child linkage and final
text validation. This extension changes no totals and removes only the two
launch-specific incomplete errors when an independently retained TaskOutput or
native system/task_notification matches the linked child's exact final text.
Ordinary user text is never notification evidence. Synchronous reports are
returned byte-for-byte equivalent as Python values to the original v1 report.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

from native_claude import accounting as v1

PARENT_SHA256 = 'a3460881e4b28d72c6c230f2b6de45a702e03ab7b17e48730d5631b407d52d9b'


def _read_bound_events(logs, report):
    """Read precisely the byte inventory already examined by frozen v1."""
    rows, errors = [], []
    for item in report['files']:
        path = logs / item['path']
        if path.is_symlink() or logs not in path.resolve().parents:
            errors.append({'code': 'async_evidence_path_unsafe', 'file': item['path']})
            continue
        try:
            data = path.read_bytes()
        except OSError:
            errors.append({'code': 'async_evidence_unreadable', 'file': item['path']})
            continue
        if v1._hash(data) != item['sha256']:
            errors.append({'code': 'async_evidence_changed', 'file': item['path']})
            continue
        child = re.fullmatch(r'agent-(.+)\.jsonl', path.name) if 'subagents' in path.parts else None
        for number, raw in enumerate(data.splitlines(), 1):
            try:
                event = json.loads(raw, parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
            except (ValueError, UnicodeError):
                continue  # v1 retains the malformed-input error.
            if not isinstance(event, dict):
                continue
            aid = event.get('agentId') or (child[1] if child else None)
            session = 'agent:' + aid if isinstance(aid, str) and aid else 'root'
            rows.append({'event': event, 'session': session, 'stream': item['path'] == 'claude-code.txt',
                         'location': {'file': item['path'], 'line': number, 'file_sha256': item['sha256'],
                                      'line_text_sha256': v1._hash(raw), 'event_sha256': v1._hash(event)}})
    return rows, errors


def _one_variant(values):
    unique = {}
    for row in values:
        signature = v1._hash({'block': row['block'], 'metadata': row.get('metadata')})
        unique.setdefault(signature, row)
    return next(iter(unique.values())) if len(unique) == 1 else None


def _output_matches(block, task_id, final):
    """Validate the actual TaskOutput text wrapper as well as its metadata."""
    text = v1._text(block.get('content'))
    expected = {'retrieval_status': 'success', 'task_id': task_id,
                'task_type': 'local_agent', 'status': 'completed', 'output': '\n' + final + '\n'}
    for tag, value in expected.items():
        matches = re.findall(r'<' + tag + r'>(.*?)</' + tag + r'>', text, flags=re.DOTALL)
        if matches != [value]:
            return False
    return True


def analyze(agent_logs: Path, expected_model: str = 'qwen3.8-max') -> dict:
    if v1._hash(Path(v1.__file__).read_bytes()) != PARENT_SHA256:
        raise ValueError('frozen_parent_accounting_changed')
    logs = Path(agent_logs).resolve()
    report = v1.analyze(logs, expected_model=expected_model)
    rows, read_errors = _read_bound_events(logs, report)
    calls, results, messages = {}, {}, {}
    notifications = []
    for row in rows:
        event = row['event']
        if row['stream']:
            if event.get('type') == 'system' and event.get('subtype') == 'task_notification':
                notifications.append(row)
            continue
        message = event.get('message')
        if event.get('type') not in ('assistant', 'user') or not isinstance(message, dict):
            continue
        content = message.get('content')
        if not isinstance(content, list):
            continue
        if event['type'] == 'assistant' and isinstance(message.get('id'), str):
            messages.setdefault((row['session'], message['id']), []).append(row)
        for block in content:
            if not isinstance(block, dict):
                continue
            if event['type'] == 'assistant' and block.get('type') == 'tool_use' and isinstance(block.get('id'), str):
                calls.setdefault((row['session'], block['id']), []).append(dict(row, block=block))
            if event['type'] == 'user' and block.get('type') == 'tool_result' and isinstance(block.get('tool_use_id'), str):
                metadata = event.get('toolUseResult', event.get('tool_use_result', {}))
                results.setdefault((row['session'], block['tool_use_id']), []).append(dict(row, block=block, metadata=metadata))
    launches = []
    for key, values in results.items():
        for row in values:
            metadata = row['metadata']
            if isinstance(metadata, dict) and (metadata.get('status') == 'async_launched' or metadata.get('isAsync') is True):
                if key not in launches:
                    launches.append(key)
    if not launches:
        # Do not change either the successful or failed synchronous behavior.
        return report
    out = copy.deepcopy(report)
    extra_errors, resolved, evidence = list(read_errors), set(), []
    sessions = {s['id']: s for s in report['sessions']}
    known_tools = {(t['session'], t['id']): t for t in report['tools']}
    matched_notifications = set()

    def error(code, key, **details):
        item = {'code': code, 'session': key[0], 'tool_id': key[1], **details}
        if item not in extra_errors:
            extra_errors.append(item)

    for key in launches:
        call = _one_variant(calls.get(key, []))
        launch = _one_variant(results.get(key, []))
        known = known_tools.get(key)
        if not call or not launch or not known:
            error('async_launch_identity_ambiguous', key)
            continue
        metadata = launch['metadata']
        aid = metadata.get('agentId')
        child_id = 'agent:' + aid if isinstance(aid, str) and aid else None
        child = sessions.get(child_id)
        if (call['block'].get('name') not in ('Agent', 'Task') or launch['block'].get('is_error') is True
                or metadata.get('status') != 'async_launched' or metadata.get('isAsync') is not True
                or metadata.get('interrupted') is True or metadata.get('resolvedModel') != expected_model
                or not child or child.get('parent_session') != key[0]
                or child.get('parent_tool_use_id') != key[1] or child.get('reachable') is not True):
            error('async_launch_or_child_identity_invalid', key, child=child_id)
            continue
        mids = child['message_ids']
        final_rows = messages.get((child_id, mids[-1]), []) if mids else []
        blocks, seen = [], set()
        for row in final_rows:
            for block in row['event']['message']['content']:
                digest = v1._hash(block)
                if digest not in seen:
                    blocks.append(block)
                    seen.add(digest)
        final = v1._text(blocks)
        if (not final or any(b.get('type') == 'tool_use' for b in blocks if isinstance(b, dict))
                or not final_rows or final_rows[-1]['event']['message'].get('stop_reason') not in (None, 'end_turn')):
            error('async_child_terminal_text_missing', key, child=child_id)
            continue
        parent_mids = sessions[key[0]]['message_ids']
        parent_final_mid = parent_mids[-1] if parent_mids else None
        parent_final_rows = messages.get((key[0], parent_final_mid), [])
        completions, conflicts = [], []
        for wait_key, call_rows in calls.items():
            wait = _one_variant(call_rows)
            if not wait or wait_key[0] != key[0] or wait['block'].get('name') != 'TaskOutput':
                continue
            args = wait['block'].get('input')
            if not isinstance(args, dict):
                continue
            terminal = _one_variant(results.get(wait_key, []))
            if not terminal:
                continue
            meta = terminal['metadata']
            task = meta.get('task') if isinstance(meta, dict) else None
            meta = meta if isinstance(meta, dict) else {}
            task = task if isinstance(task, dict) else {}
            visible = v1._text(terminal['block'].get('content'))
            visible_ids = re.findall(r'<task_id>(.*?)</task_id>', visible, re.DOTALL)
            visible_statuses = re.findall(r'<status>(.*?)</status>', visible, re.DOTALL)
            if aid not in [args.get('task_id'), task.get('task_id'), *visible_ids]:
                continue
            # Running/timeout observations are not completion. A claimed terminal
            # result with contradictory identity/text must not be overridden by
            # another notification that happens to match.
            declared_ids = [value for value in [args.get('task_id'), task.get('task_id'), *visible_ids]
                            if value is not None]
            identity_conflict = any(value != aid for value in declared_ids)
            status_conflict = bool(task.get('status') and visible_statuses
                                   and visible_statuses != [task['status']])
            if identity_conflict or status_conflict or task.get('status') in ('failed', 'cancelled', 'killed', 'interrupted'):
                conflicts.append({'kind': 'TaskOutput', 'tool_id': wait_key[1], 'location': terminal['location']})
                continue
            if task.get('status') != 'completed' and 'completed' not in visible_statuses:
                continue
            valid = (meta.get('retrieval_status') == 'success' and terminal['block'].get('is_error') is not True
                and args.get('task_id') == aid and task.get('task_id') == aid
                and task.get('status') == 'completed' and task.get('task_type') == 'local_agent'
                and task.get('isRawTranscript') is False and task.get('output') == final and task.get('result') == final
                and _output_matches(terminal['block'], aid, final)
                and wait['location']['file'] == launch['location']['file'] == terminal['location']['file']
                and launch['location']['line'] < wait['location']['line'] < terminal['location']['line']
                and bool(parent_final_rows) and all(
                    terminal['location']['file'] == r['location']['file']
                    and terminal['location']['line'] < r['location']['line'] for r in parent_final_rows))
            if valid:
                completions.append({'kind': 'TaskOutput', 'tool_id': wait_key[1],
                                    'call': wait['location'], 'result': terminal['location']})
            else:
                conflicts.append({'kind': 'TaskOutput', 'tool_id': wait_key[1], 'location': terminal['location']})
        for index, row in enumerate(notifications):
            event = row['event']
            if event.get('task_id') != aid and event.get('tool_use_id') != key[1]:
                continue
            matched_notifications.add(index)
            valid = (event.get('task_id') == aid and event.get('tool_use_id') == key[1]
                     and event.get('session_id') == report['root_session_id']
                     and event.get('status') == 'completed' and event.get('summary') == final)
            # Bind the observed single-stream order: actual launch, native task
            # start, exact streamed child final, notification, parent final.
            stream_rows = [r for r in rows if r['stream']]
            launch_lines = [r['location']['line'] for r in stream_rows
                if r['event'].get('type') == 'assistant'
                and isinstance(r['event'].get('message'), dict)
                and r['event']['message'].get('id') == known['message_id']]
            starts = [r for r in stream_rows if r['event'].get('type') == 'system'
                and r['event'].get('subtype') == 'task_started'
                and (r['event'].get('task_id') == aid or r['event'].get('tool_use_id') == key[1])]
            child_stream = [r for r in stream_rows if r['event'].get('type') == 'assistant'
                and isinstance(r['event'].get('message'), dict)
                and r['event']['message'].get('id') == mids[-1]
                and r['event'].get('parent_tool_use_id') == key[1]]
            streamed_blocks = {}
            for child_row in child_stream:
                content = child_row['event']['message'].get('content', [])
                for block in content if isinstance(content, list) else []:
                    streamed_blocks.setdefault(v1._hash(block), block)
            parent_final_lines = [r['location']['line'] for r in stream_rows
                if r['event'].get('type') == 'assistant'
                and isinstance(r['event'].get('message'), dict)
                and r['event']['message'].get('id') == parent_final_mid]
            valid &= bool(launch_lines and starts and child_stream and parent_final_lines)
            if launch_lines and starts and child_stream and parent_final_lines:
                valid &= all(r['event'].get('task_id') == aid and r['event'].get('tool_use_id') == key[1]
                    and r['event'].get('session_id') == report['root_session_id']
                    and r['event'].get('task_type') == 'local_agent' for r in starts)
                valid &= v1._text(list(streamed_blocks.values())) == final
                valid &= (min(launch_lines) < min(r['location']['line'] for r in starts)
                    < min(r['location']['line'] for r in child_stream)
                    <= max(r['location']['line'] for r in child_stream)
                    < row['location']['line'] < min(parent_final_lines))
            terminal_lines = [r['location']['line'] for r in rows if r['stream']
                              and r['event'].get('type') == 'result']
            valid &= len(terminal_lines) == 1 and row['location']['line'] < terminal_lines[0]
            if valid:
                completions.append({'kind': 'native-task-notification', 'location': row['location']})
            else:
                conflicts.append({'kind': 'native-task-notification', 'location': row['location']})
        if conflicts:
            error('async_completion_conflict', key, child=child_id, conflicts=conflicts)
        if not completions:
            error('async_completion_missing', key, child=child_id)
        if completions and not conflicts and not read_errors:
            resolved.add((key[0], key[1], child_id))
            evidence.append({'parent_session': key[0], 'tool_id': key[1], 'child_session': child_id,
                             'launch_call': call['location'], 'launch_result': launch['location'],
                             'child_final_locations': [r['location'] for r in final_rows],
                             'child_final_sha256': v1._hash(final.encode()), 'completion_evidence': completions})
    for index, row in enumerate(notifications):
        if index not in matched_notifications:
            extra_errors.append({'code': 'unmatched_native_task_notification', 'location': row['location']})
    def removable(error):
        return any((error['code'] == 'unfinished_tool_metadata' and error.get('session') == parent
                    and error.get('tool_id') == tid)
                   or (error['code'] == 'child_final_not_in_parent_result' and error.get('child') == child
                       and error.get('tool_id') == tid) for parent, tid, child in resolved)
    out['errors'] = [error for error in report['errors'] if not removable(error)] + extra_errors
    out['complete'] = out['complete_tree'] = not out['errors']
    out['usage_partial'] = bool(out['errors'])
    out['async_completion_reconciliation'] = {
        'version': 2, 'parent_accounting_sha256': PARENT_SHA256,
        'observed_shape_source': 'native Claude 2.1.212 fake-provider probe 39337209994',
        'resolved': evidence, 'original_errors': report['errors'],
        'usage_recomputed': False, 'notification_usage_counted': False,
        'limitations': ['Only retained TaskOutput completed/local_agent metadata and native system/task_notification are supported.',
                        'All frozen identity, model, tool, final text and usage errors outside the resolved launch remain.']}
    return out
