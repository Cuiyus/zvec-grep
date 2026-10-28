"""Offline contract checks; loopback fixtures never contact a model provider."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from zg_bench.mcp_schema_reference_probe import (
    base, inline_query_refs, inspect_case, proxy_source, read_lines,
)


NAME = 'zvec_grep_zvec_grep_search'
CONTROLS = dict(max_tokens=32000, temperature=0, seed=42,
                enable_thinking=True, reasoning_effort='high', stream=True)


def definition():
    query = dict(description='A string is literal; use native arrays.', anyOf=[
        dict(type='string', maxLength=4000, description='One literal query.'),
        dict(type='array', maxItems=32, items=dict(type='string', maxLength=4000),
             description='Multiple query groups.'),
    ])
    schema = dict(type='object', additionalProperties=False, required=['root'],
                  properties={'root': dict(type='string', minLength=1)})
    schema['$defs'] = {'QueryListInput': query, 'OtherInput': {'type': 'string'}}
    schema['properties']['unrelated'] = {'$ref': '#/$defs/OtherInput'}
    for field in base.PROBES:
        schema['properties'][field] = {
            'description': f'{field} description unchanged.',
            'anyOf': [{'$ref': '#/$defs/QueryListInput'}, {'type': 'null'}],
        }
    return dict(name=NAME, description='Do not JSON-stringify arrays.', parameters=schema)


def request_body(expected):
    return dict(model='qwen3.8-max', **CONTROLS,
                messages=[{'role': 'user', 'content': 'Two independent searches.'}],
                tools=[{'type': 'function', 'function': copy.deepcopy(expected)},
                       {'type': 'function', 'function': {
                           'name': 'unrelated_tool', 'description': 'Keep this unchanged.',
                           'parameters': {'type': 'object', 'properties': {}}}}])


def write_lines(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def write_case(root, field='fts', value=None, groups=2, status='completed',
               arm='original', tool_count=1, raw_value=None, input_overrides=None):
    expected = definition()
    proposal = inline_query_refs(expected)
    agent = root / 'agent'
    agent.mkdir()
    value = copy.deepcopy(base.PROBES[field] if value is None else value)
    body = request_body(expected if arm == 'original' else proposal)
    write_lines(agent / 'model-requests.jsonl', [dict(
        id=2, model=body['model'], tools=body['tools'], controls=CONTROLS,
        schema_only_change_verified=True, local_text_in_messages=False,
        normalized_body_sha256='normalized', messages_sha256='messages',
        outbound_body_sha256='outbound')])
    write_lines(agent / 'outbound-attempts.jsonl', [
        dict(id=2, event='started', attempt=1), dict(id=2, event='completed', status=200)])
    fragments = []
    snapshot = [dict(kind='session', data={'id': 'root', 'parent_id': None})]
    for index in range(tool_count):
        inputs = {'root': '/app', field: value, **(input_overrides or {})}
        raw_inputs = copy.deepcopy(inputs)
        if raw_value is not None:
            raw_inputs[field] = raw_value
        arguments = json.dumps(raw_inputs)
        call_id = f'call-{index}'
        split = max(1, len(arguments) // 2)
        fragments.extend([
            dict(request=2, calls=[dict(index=index, id=call_id,
                 function=dict(name=NAME, arguments=arguments[:split]))]),
            dict(request=2, calls=[dict(index=index,
                 function=dict(arguments=arguments[split:]))]),
        ])
        output = ('No groups available.' if groups is None else
                  f'query groups ({groups}):\n#1 result.py:1\ngroups: Q1#1\n')
        snapshot.append(dict(kind='tool', data=dict(name=NAME, call_id=call_id,
                        input=inputs, status=status, output=output)))
    write_lines(agent / 'raw-tool-call-fragments.jsonl', fragments)
    (agent / 'opencode-session-snapshot.json').write_text(json.dumps(snapshot))
    return expected, proposal


class SchemaReferenceInspectionTests(unittest.TestCase):
    def test_three_references_only_and_exact_roundtrip(self):
        original = definition()
        before = copy.deepcopy(original)
        proposal = inline_query_refs(original)
        self.assertEqual(original, before, 'Transformation must not mutate its input')
        restored = copy.deepcopy(proposal)
        for field in base.PROBES:
            self.assertEqual(proposal['parameters']['properties'][field]['anyOf'][0],
                             original['parameters']['$defs']['QueryListInput'])
            restored['parameters']['properties'][field]['anyOf'][0] = {
                '$ref': '#/$defs/QueryListInput'}
        self.assertNotIn('QueryListInput', proposal['parameters']['$defs'])
        restored['parameters']['$defs']['QueryListInput'] = original['parameters']['$defs']['QueryListInput']
        self.assertEqual(restored, original)
        proposal['parameters']['properties']['fts']['anyOf'][0]['description'] = 'changed'
        self.assertEqual(original, before, 'Expanded schemas must be independent copies')

    def test_unexpected_reference_shape_is_rejected(self):
        for field in base.PROBES:
            changed = definition()
            changed['parameters']['properties'][field]['anyOf'][0]['description'] = 'sibling'
            with self.subTest(field=field), self.assertRaises(AssertionError):
                inline_query_refs(changed)

    def inspect(self, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, proposal = write_case(root, **kwargs)
            return inspect_case(root, kwargs.get('field', 'fts'),
                                kwargs.get('arm', 'original'), expected, proposal)

    def test_valid_exact_values_pass_for_each_field_and_arm(self):
        for field in base.PROBES:
            for arm in ('original', 'inline'):
                with self.subTest(field=field, arm=arm):
                    self.assertTrue(self.inspect(field=field, arm=arm)['passed'])

    def test_wrong_values_groups_status_and_call_count_fail(self):
        cases = [dict(value=['wrong', 'values']), dict(value=['ConanFile']),
                 dict(value=['GraphManager', 'ConanFile']),
                 dict(value='["ConanFile","GraphManager"]'), dict(value='ConanFile'),
                 dict(groups=1), dict(groups=3), dict(groups=None),
                 dict(status='error'), dict(tool_count=0), dict(tool_count=2)]
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(self.inspect(**case)['passed'])

    def test_api_to_mcp_argument_difference_fails_integrity(self):
        with self.assertRaisesRegex(AssertionError, 'API-to-MCP arguments changed'):
            self.inspect(raw_value=['wrong', 'values'])

    def test_wrong_root_extra_queries_or_fusing_cannot_count_as_success(self):
        for overrides in ({'root': '/wrong'}, {'queries': ['extra']},
                          {'vector': 'extra'}, {'query': 'extra'}, {'fuse': True}):
            with self.subTest(overrides=overrides):
                self.assertFalse(self.inspect(input_overrides=overrides)['passed'])


class MockUpstream:
    """Only binds loopback; records raw bytes so preservation is testable."""
    def __init__(self, mode='success', delay=0):
        self.mode = mode
        self.delay = delay
        self.requests = []
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['content-length']))
                with owner.lock:
                    owner.requests.append((self.path, raw))
                if owner.delay:
                    time.sleep(owner.delay)
                if owner.mode == 'disconnect':
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if owner.mode == 'http_error':
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b'Unavailable')
                    return
                self.send_response(200)
                self.send_header('content-type', 'text/event-stream')
                self.end_headers()
                data = {'model': 'qwen3.8-max', 'choices': [{'delta': {'tool_calls': [
                    {'id': 'real-call', 'index': 0, 'function': {
                        'name': NAME, 'arguments': '{"fts":["ConanFile","GraphManager"]}'}}]}}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}
                self.wfile.write(('data: ' + json.dumps(data) + '\n\ndata: [DONE]\n\n').encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}/v1'


NODE = os.environ.get('ZG_SCHEMA_TEST_NODE') or shutil.which('node')


@unittest.skipUnless(NODE, 'Node is required for the real loopback proxy test')
class SchemaReferenceProxyTests(unittest.TestCase):
    @contextmanager
    def proxy(self, upstream, arm='original'):
        expected = definition()
        proposal = inline_query_refs(expected)
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory)
            source = proxy_source(upstream.url, arm, expected, proposal)
            first_line, template = source.split('\n', 1)
            config = json.loads(first_line.removeprefix('const config = ').removesuffix(';'))
            config.update(logs=str(logs), port=0)
            script = logs / 'proxy.cjs'
            script.write_text('const config = ' + json.dumps(config) + ';\n' + template)
            process = subprocess.Popen([NODE, str(script)], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 10
                while not (logs / 'proxy.ready').exists():
                    if process.poll() is not None:
                        self.fail('Proxy exited: ' + process.communicate()[1])
                    if time.monotonic() > deadline:
                        self.fail('Proxy did not bind loopback within 10 seconds')
                    time.sleep(0.02)
                port = (logs / 'proxy.ready').read_text().strip()
                yield f'http://127.0.0.1:{port}/v1/chat/completions', logs, expected, proposal
            finally:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)

    def post(self, url, body):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        request = Request(url, data=raw, headers={'content-type': 'application/json'})
        try:
            with urlopen(request, timeout=10) as result:
                return result.status, result.read()
        except HTTPError as error:
            return error.code, error.read()

    def test_original_preserves_bytes_and_titles_summaries_are_local(self):
        with MockUpstream() as upstream, self.proxy(upstream) as (url, logs, expected, _):
            title = dict(model='qwen3.8-max', messages=[{'role': 'user', 'content': 'Title'}], stream=False)
            status, raw_title = self.post(url, title)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw_title)['usage']['total_tokens'], 0)
            self.assertEqual(upstream.requests, [])
            body = request_body(expected)
            raw = json.dumps(body, indent=2).encode()
            self.assertEqual(self.post(url, raw)[0], 200)
            summary = copy.deepcopy(body)
            summary['messages'].append({'role': 'tool', 'content': 'query groups (2):', 'tool_call_id': 'real-call'})
            self.assertIn(b'Schema diagnostic complete.', self.post(url, summary)[1])
            self.assertEqual(upstream.requests, [('/v1/chat/completions', raw)])
            self.assertEqual(len(read_lines(logs / 'model-requests.jsonl')), 1)
            local = read_lines(logs / 'local-responses.jsonl')
            self.assertEqual([row['reason'] for row in local], ['auxiliary_request', 'generation_budget_spent'])
            self.assertEqual(read_lines(logs / 'provider-usage.jsonl')[0]['usage']['total_tokens'], 12)
            self.assertEqual(read_lines(logs / 'raw-tool-call-fragments.jsonl')[0]['calls'][0]['id'], 'real-call')

    def test_inline_modifies_only_approved_schema(self):
        with MockUpstream() as upstream, self.proxy(upstream, 'inline') as (url, logs, expected, proposal):
            body = request_body(expected)
            self.assertEqual(self.post(url, body)[0], 200)
            self.assertEqual(len(upstream.requests), 1)
            sent = json.loads(upstream.requests[0][1])
            self.assertEqual(sent['tools'][0]['function'], proposal)
            restored = copy.deepcopy(sent)
            restored['tools'][0]['function'] = expected
            self.assertEqual(restored, body)
            recorded = read_lines(logs / 'model-requests.jsonl')[0]
            self.assertTrue(recorded['schema_only_change_verified'])
            self.assertNotEqual(recorded['normalized_body_sha256'], recorded['outbound_body_sha256'])

    def test_schema_mismatch_rejects_before_any_upstream_attempt(self):
        with MockUpstream() as upstream, self.proxy(upstream, 'inline') as (url, logs, expected, _):
            body = request_body(expected)
            body['tools'][0]['function']['parameters']['properties']['fts']['description'] = 'unapproved'
            self.assertEqual(self.post(url, body)[0], 502)
            self.assertEqual(upstream.requests, [])
            self.assertEqual(read_lines(logs / 'outbound-attempts.jsonl'), [])

    def test_concurrent_requests_and_retries_share_one_attempt(self):
        with MockUpstream(delay=0.15) as upstream, self.proxy(upstream) as (url, logs, expected, _):
            body = request_body(expected)
            with ThreadPoolExecutor(max_workers=6) as pool:
                responses = list(pool.map(lambda _: self.post(url, body), range(6)))
            self.assertTrue(all(status == 200 for status, _ in responses))
            for _ in range(3):
                self.assertIn(b'Schema diagnostic complete.', self.post(url, body)[1])
            self.assertEqual(len(upstream.requests), 1)
            events = read_lines(logs / 'outbound-attempts.jsonl')
            self.assertEqual(sum(row['event'] == 'started' for row in events), 1)
            self.assertEqual(len(read_lines(logs / 'local-responses.jsonl')), 8)

    def test_http_and_transport_failures_consume_budget_without_retry(self):
        for mode, status in [('http_error', 503), ('disconnect', 502)]:
            with self.subTest(mode=mode), MockUpstream(mode=mode) as upstream, self.proxy(upstream) as (url, logs, expected, _):
                body = request_body(expected)
                self.assertEqual(self.post(url, body)[0], status)
                for _ in range(3):
                    self.assertIn(b'Schema diagnostic complete.', self.post(url, body)[1])
                self.assertEqual(len(upstream.requests), 1)
                events = read_lines(logs / 'outbound-attempts.jsonl')
                self.assertEqual(sum(row['event'] == 'started' for row in events), 1)
                if mode == 'disconnect':
                    self.assertEqual(sum(row['event'] == 'failed' for row in events), 1)


if __name__ == '__main__':
    unittest.main()
