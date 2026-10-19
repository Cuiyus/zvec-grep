from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from repair_v2.fake_services import (CHAT_KEY, EMBEDDING_KEY, FINAL_SENTINEL,
    FakeServices, QUERY, REQUEST_CONTRACT, embedding_response, scripted_delta)


def request(*, treatment=False, previous=()):
    names = ['read', 'task', 'grep', 'glob']
    if treatment:
        names.append('zg_zvec_grep_search')
    return {**REQUEST_CONTRACT, 'stream': True,
        'messages': [{'role': 'assistant', 'tool_calls': [{'function': {'name': n}}]} for n in previous],
        'tools': [{'type': 'function', 'function': {'name': n, 'parameters': {'properties': {'root': {}, 'query': {}}}}} for n in names]}


class FakeServicesTests(unittest.TestCase):
    def test_baseline_and_child_have_bounded_real_tool_sequence(self):
        previous = []
        stages = []
        for _ in range(3):
            delta, finish, stage = scripted_delta(request(previous=previous), child=False)
            stages.append(stage)
            if finish == 'tool_calls':
                previous.append(delta['tool_calls'][0]['function']['name'])
        self.assertEqual(stages, ['root_read', 'root_child', 'root_final'])
        self.assertEqual(delta['content'], FINAL_SENTINEL)
        self.assertEqual(scripted_delta(request(), child=True)[2], 'child_read')
        self.assertEqual(scripted_delta(request(previous=['read']), child=True)[2], 'child_final')

    def test_treatment_uses_exact_advertised_mcp_schema(self):
        delta, _, stage = scripted_delta(request(treatment=True), child=False)
        self.assertEqual(stage, 'root_mcp')
        call = delta['tool_calls'][0]['function']
        self.assertEqual(call['name'], 'zg_zvec_grep_search')
        self.assertEqual(json.loads(call['arguments']), {'root': '/workspace', 'query': QUERY})
        bad = request(treatment=True)
        bad['tools'][-1]['function']['parameters']['properties'] = {}
        with self.assertRaisesRegex(ValueError, 'mcp_schema'):
            scripted_delta(bad, child=False)

    def test_changed_request_contract_or_web_tool_rejected(self):
        for key in REQUEST_CONTRACT:
            bad = request()
            bad[key] = None
            with self.subTest(key=key), self.assertRaises(ValueError):
                scripted_delta(bad, child=False)
        bad = request()
        bad['tools'].append({'function': {'name': 'webfetch'}})
        with self.assertRaisesRegex(ValueError, 'web_tools'):
            scripted_delta(bad, child=False)

    def test_embedding_exact_dimension_batch_order_deterministic_nonzero(self):
        body = {'model': 'qwen3.7-text-embedding', 'dimensions': 1024,
                'encoding_format': 'float', 'input': ['one', 'two']}
        first, shape = embedding_response(body)
        self.assertEqual(first, embedding_response(body)[0])
        self.assertEqual([r['index'] for r in first['data']], [0, 1])
        self.assertTrue(all(len(r['embedding']) == 1024 and sum(r['embedding']) == 2 for r in first['data']))
        self.assertEqual(shape['batch_size'], 2)
        self.assertNotIn('one', json.dumps(shape))
        for key, value in [('dimensions', 512), ('input', [''] * 21), ('model', 'remote-other')]:
            bad = {**body, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                embedding_response(bad)

    def test_http_rejects_nonfixture_auth_without_recording_credential(self):
        with tempfile.TemporaryDirectory() as tmp, FakeServices(Path(tmp) / 'service', bind='127.0.0.1') as service:
            req = Request(f'http://127.0.0.1:{service.port}/v1/chat/completions',
                data=json.dumps(request()).encode(), headers={'Authorization': 'Bearer forbidden-sample', 'Content-Type': 'application/json'})
            with self.assertRaises(HTTPError) as raised:
                urlopen(req, timeout=5)
            self.assertEqual(raised.exception.code, 400)
            self.assertEqual(service.rows, [])
            self.assertNotIn('forbidden-sample', (service.output / 'errors.json').read_text())

    def test_http_native_sse_and_embedding_contract(self):
        with tempfile.TemporaryDirectory() as tmp, FakeServices(Path(tmp) / 'service', bind='127.0.0.1') as service:
            service.profile = 'zvec-grep'
            for path, key, body in [('/v1/chat/completions', CHAT_KEY, request()),
                    ('/v1/embeddings', EMBEDDING_KEY, {'model': 'qwen3.7-text-embedding', 'dimensions': 1024, 'encoding_format': 'float', 'input': [QUERY]})]:
                req = Request(f'http://127.0.0.1:{service.port}' + path, data=json.dumps(body).encode(),
                              headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
                with urlopen(req, timeout=5) as response:
                    payload = response.read()
                    self.assertEqual(response.status, 200)
                    if path.endswith('completions'):
                        self.assertIn(b'data: [DONE]', payload)
                        self.assertIn(b'"reasoning_content"', payload)
                    else:
                        self.assertEqual(len(json.loads(payload)['data'][0]['embedding']), 1024)
            self.assertEqual(len(service.rows), 2)
            self.assertEqual(service.errors, [])


if __name__ == '__main__':
    unittest.main()
