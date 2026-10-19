"""Probe-only scripting layered over a private instance of the frozen transport.

No native agent method, CLI option, background flag or original module is patched.
Only the fake provider reply changes: Explore uses the native default background
behavior, and TaskOutput consumes an ID actually returned in its launch result.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re

from native_claude import fake_services as frozen

FROZEN_TRANSPORT_SHA = 'a3740153d3cf84ea23505b9e91ee49c923967334b7f4397cae4e48acd2ee8534'
MAX_CHAT_REQUESTS = 40
MAX_WAITS = 3
WAIT_TIMEOUT_MS = 30000
PREFIX = 'tool_native_claude_v1_baseline_'


def _private_transport():
    path = Path(frozen.__file__)
    if hashlib.sha256(path.read_bytes()).hexdigest() != FROZEN_TRANSPORT_SHA:
        raise ValueError('frozen_transport_changed')
    spec = importlib.util.spec_from_file_location('async_probe_private_frozen_transport', path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


TRANSPORT = _private_transport()
ORIGINAL_SCRIPT = TRANSPORT.scripted_response


def resolve_task_id(result: dict) -> dict:
    """Extract only explicit ID labels from the actual launch result.

    A task_id is preferred to agentId when both are explicitly provided. No
    guessed filenames, fixture IDs, arbitrary prose tokens, or server state can
    become a TaskOutput argument. Conflicting IDs within one label family fail.
    """
    found = {'task': [], 'agent': []}
    def add(family, value, source):
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
            row = {'id': value, 'source': source}
            if row not in found[family]:
                found[family].append(row)
    def visit(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                lower = key.replace('_', '').replace('-', '').lower()
                if lower in ('taskid', 'agentid'):
                    add('task' if lower == 'taskid' else 'agent', child, path + '.' + key)
                visit(child, path + '.' + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f'{path}[{index}]')
        elif isinstance(value, str):
            # JSON tool-result text is parsed only for explicit keyed IDs.
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                decoded = None
            if isinstance(decoded, (dict, list)):
                visit(decoded, path + ':json')
            patterns = (
                ('task', r'(?i)\btask[_ -]?id\s*[:=]\s*["\x27`]?([A-Za-z0-9_-]{1,128})'),
                ('agent', r'(?i)\bagent[_ -]?id\s*[:=]\s*["\x27`]?([A-Za-z0-9_-]{1,128})'),
                ('task', r'(?i)<task[_-]id>\s*([A-Za-z0-9_-]{1,128})\s*</task[_-]id>'),
                ('agent', r'(?i)<agent[_-]id>\s*([A-Za-z0-9_-]{1,128})\s*</agent[_-]id>'),
            )
            for family, pattern in patterns:
                for match in re.finditer(pattern, value):
                    add(family, match[1], f'{path}:text@{match.start()}')
    visit(result.get('content'), 'launch_result.content')
    family = 'task' if found['task'] else 'agent'
    values = {row['id'] for row in found[family]}
    if len(values) != 1:
        raise ValueError('missing_or_ambiguous_native_task_id')
    return {'task_id': next(iter(values)), 'selected_label_family': family,
            'observed_labels': found,
            'launch_result_sha256': hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def _reply(stage, role, tool=None, args=None, text=None, details=None):
    ordinal = 10 + int(stage.rsplit('_', 1)[1]) if stage.startswith('root_wait_') else 19
    usage = {'input_tokens': 100 + ordinal, 'output_tokens': 20 + ordinal,
             'cache_read_input_tokens': 30 + ordinal, 'cache_creation_input_tokens': 4 + ordinal}
    content = [{'type': 'tool_use', 'id': PREFIX + stage, 'name': tool['name'],
                'input': TRANSPORT._checked_args(tool, args)}] if tool else [{'type': 'text', 'text': text}]
    message_id = 'msg_native_claude_v1_baseline_' + stage
    response = {'id': message_id, 'type': 'message', 'role': 'assistant', 'model': frozen.MODEL,
                'content': content, 'stop_reason': 'tool_use' if tool else 'end_turn', 'stop_sequence': None, 'usage': usage}
    return {'classification': role, 'stage': stage, 'message_id': message_id, 'usage': usage,
            'selected_tool_schema': copy.deepcopy(tool), 'response': response,
            'probe_details': details or {}}


def scripted_response(body: dict, profile: str, *, background_index: int = 0) -> dict:
    if profile != 'baseline':
        raise ValueError('probe_baseline_only')
    # Preserve all frozen validation and root/child classification. Root-after-
    # launch is the sole stage the frozen synchronous script cannot interpret.
    role = TRANSPORT.classify_request(body)
    calls, results = TRANSPORT._history(body, role)
    launch = calls.get(PREFIX + 'root_child')
    if role != 'root' or launch is None:
        reply = ORIGINAL_SCRIPT(body, profile, background_index=background_index)
        if reply['stage'] == 'root_child':
            selected = reply['selected_tool_schema']
            if selected['name'].lower() != 'agent':
                raise ValueError('native_agent_schema_required')
            props = selected.get('input_schema', {}).get('properties', {})
            if 'run_in_background' not in props:
                raise ValueError('background_default_schema_unavailable')
            args = reply['response']['content'][0]['input']
            args['subagent_type'] = 'Explore'
            args.pop('run_in_background', None)
            args.pop('model', None)
            TRANSPORT._checked_args(selected, args)
            reply['probe_details'] = {'subagent_type': 'Explore', 'run_in_background_omitted': True,
                                      'model_override_omitted': True}
        return reply
    if body.get('model') != frozen.MODEL:
        raise ValueError('wrong_model')
    if type(body.get('max_tokens')) is not int or not 1 <= body['max_tokens'] <= 131072:
        raise ValueError('invalid_max_tokens')
    launch_result = results[PREFIX + 'root_child']
    resolution = resolve_task_id(launch_result)
    waits = sorted(tid for tid in calls if isinstance(tid, str) and tid.startswith(PREFIX + 'root_wait_'))
    completion = []
    if frozen.CHILD_SENTINEL in TRANSPORT._text(launch_result.get('content')):
        completion.append({'source': 'Agent-launch-result', 'tool_use_id': PREFIX + 'root_child',
                           'result': launch_result})
    for tid in waits:
        result = results[tid]
        if frozen.CHILD_SENTINEL in TRANSPORT._text(result.get('content')):
            completion.append({'source': 'TaskOutput', 'tool_use_id': tid, 'result': result})
    # Native notifications may carry the completed output separately from
    # TaskOutput. Require the real task ID and child sentinel in one direct user
    # text, after at least one actual TaskOutput, rather than server-side state.
    if waits:
        for index, message in enumerate(body.get('messages', [])):
            text = TRANSPORT._text(message.get('content')) if message.get('role') == 'user' else ''
            if frozen.CHILD_SENTINEL in text and resolution['task_id'] in text and '<task-notification>' in text:
                completion.append({'source': 'native-task-notification', 'message_index': index, 'text': text})
    if completion:
        return _reply('root_final', role, text=frozen.FINAL_SENTINEL,
                      details={'native_task_resolution': resolution, 'completion_evidence': completion,
                               'task_output_calls': len(waits)})
    if len(waits) >= MAX_WAITS:
        raise ValueError('bounded_task_output_limit_without_completion')
    selected = TRANSPORT._tool(body, ('TaskOutput',))
    args = {'task_id': resolution['task_id'], 'block': True, 'timeout': WAIT_TIMEOUT_MS}
    return _reply(f'root_wait_{len(waits) + 1}', role, tool=selected, args=args,
                  details={'native_task_resolution': resolution})


# This module instance is private to the probe. The imported frozen module and
# every native Harbor/Claude class remain unchanged.
TRANSPORT.scripted_response = scripted_response


class ProbeServices(TRANSPORT.FakeServices):
    def __init__(self, *args, **kwargs):
        if kwargs.get('profile', 'baseline') != 'baseline':
            raise ValueError('probe_baseline_only')
        kwargs['max_chat_requests'] = MAX_CHAT_REQUESTS
        super().__init__(*args, **kwargs)

    def validate(self, **_):
        with self.lock:
            chat = [row for row in self.rows if row['kind'] == 'chat']
            stages = [row['stage'] for row in chat if row['classification'] != 'background']
            launches = [row for row in chat if row['stage'] == 'root_child']
            waits = [row for row in chat if row['stage'].startswith('root_wait_')]
            finals = [row for row in chat if row['stage'] == 'root_final']
            checks = {
                'no_transport_errors': not self.errors,
                'bounded_chat_requests': len(chat) <= MAX_CHAT_REQUESTS,
                'one_launch': len(launches) == 1,
                'default_background_explore_requested': len(launches) == 1 and launches[0]['response']['content'][0]['input'].get('subagent_type') == 'Explore'
                    and 'run_in_background' not in launches[0]['response']['content'][0]['input']
                    and 'model' not in launches[0]['response']['content'][0]['input'],
                'bounded_actual_task_output_or_completed_launch': len(waits) <= MAX_WAITS and (
                    len(waits) > 0 or any(e.get('source') == 'Agent-launch-result'
                        for r in finals for e in r.get('probe_details', {}).get('completion_evidence', []))),
                'actual_child_read_and_final': stages.count('child_read') == stages.count('child_final') == 1,
                'one_root_final_after_observed_completion': len(finals) == 1 and bool(finals[0].get('probe_details', {}).get('completion_evidence')),
                'only_expected_model_requests': bool(chat) and all(r['body'].get('model') == frozen.MODEL for r in chat),
                'no_embedding_requests': not any(r['kind'] == 'embedding' for r in self.rows),
            }
            return {'valid': all(checks.values()), 'checks': checks, 'stages': stages,
                    'root_requests': sum(r['classification'] == 'root' for r in chat),
                    'child_requests': sum(r['classification'] == 'child' for r in chat),
                    'background_requests': sum(r['classification'] == 'background' for r in chat),
                    'quality_evaluated': False, 'all_native_child_modes_validated': False}

    def dump_trace(self, path=None):
        trace = super().dump_trace()
        trace.update(scope='native-claude-default-background-explore-probe-v1',
                     frozen_transport_sha256=FROZEN_TRANSPORT_SHA,
                     limits={'chat_requests': MAX_CHAT_REQUESTS, 'task_output_calls': MAX_WAITS,
                             'task_output_timeout_ms': WAIT_TIMEOUT_MS},
                     all_native_child_modes_validated=False)
        if path is not None:
            Path(path).write_text(json.dumps(trace, indent=2) + '\n')
        return trace
