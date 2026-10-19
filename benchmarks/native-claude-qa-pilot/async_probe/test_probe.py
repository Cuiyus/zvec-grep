"""Synthetic transport tests only; actual background behavior needs Docker capture."""
import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from native_claude import fake_services as frozen
from native_claude import integration
from async_probe import run, services


def tool(name, properties, required):
    return {'name': name, 'input_schema': {'type': 'object', 'properties': properties, 'required': required}}


def request(child=False):
    return {'model': frozen.MODEL, 'max_tokens': 8192, 'stream': True,
            'output_config': {'effort': 'high'},
            'messages': [{'role': 'user', 'content': frozen.CHILD_MARKER if child else frozen.ROOT_MARKER}],
            'tools': [
                tool('Read', {'file_path': {'type': 'string'}, 'limit': {'type': 'integer'}}, ['file_path']),
                tool('Agent', {'description': {'type': 'string'}, 'prompt': {'type': 'string'},
                              'subagent_type': {'type': 'string'}, 'run_in_background': {'type': 'boolean'},
                              'model': {'type': 'string', 'enum': ['sonnet', 'opus', 'haiku', 'fable']}}, ['description', 'prompt']),
                tool('TaskOutput', {'task_id': {'type': 'string'}, 'block': {'type': 'boolean'},
                                   'timeout': {'type': 'number', 'maximum': 600000}}, ['task_id', 'block', 'timeout'])]}


def advance(body, response, result):
    body['messages'].append({'role': 'assistant', 'content': response['content']})
    for block in response['content']:
        if block['type'] == 'tool_use':
            body['messages'].append({'role': 'user', 'content': [{'type': 'tool_result',
                'tool_use_id': block['id'], 'content': result}]})


def launched_body(result='Agent launched asynchronously. agentId: native-returned-agent-42'):
    body = request()
    reply = services.scripted_response(body, 'baseline')
    advance(body, reply['response'], 'actual read fixture result')
    reply = services.scripted_response(body, 'baseline')
    advance(body, reply['response'], result)
    return body


def post(server, body):
    req = Request(server.anthropic_base_url + '/v1/messages', data=json.dumps(body).encode(),
                  headers={'x-api-key': frozen.CHAT_KEY, 'Content-Type': 'application/json'})
    with urlopen(req, timeout=5) as response:
        data = response.read()
        assert b'message_stop' in data
    return server.rows[-1]['response']


class ProbeTests(unittest.TestCase):
    def test_launch_explore_without_background_or_model_override(self):
        body = request()
        first = services.scripted_response(body, 'baseline')
        advance(body, first['response'], 'source read')
        launch = services.scripted_response(body, 'baseline')
        args = launch['response']['content'][0]['input']
        self.assertEqual(args['subagent_type'], 'Explore')
        self.assertNotIn('run_in_background', args)
        self.assertNotIn('model', args)
        self.assertEqual(launch['selected_tool_schema'], body['tools'][1])
        self.assertIsNot(frozen.scripted_response, services.scripted_response)
        # The frozen script still produces its original synchronous launch.
        original = frozen.scripted_response(body, 'baseline')['response']['content'][0]['input']
        self.assertEqual(original['subagent_type'], 'general-purpose')
        self.assertIs(original['run_in_background'], False)

    def test_ids_only_from_explicit_actual_result_labels(self):
        for content, expected in [('agentId: abc-123 (native reference)', 'abc-123'),
                                  ('<task-id>task_123</task-id>', 'task_123'),
                                  ('{"task_id":"task42","agentId":"agent42"}', 'task42'),
                                  ([{'type': 'text', 'text': 'Agent ID: native42'}], 'native42')]:
            with self.subTest(content=content):
                self.assertEqual(services.resolve_task_id({'content': content})['task_id'], expected)
        for content in ('/tmp/claude/tasks/guessed.output', 'unlabeled-42', 'task_id: first\ntask_id: second'):
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, 'missing_or_ambiguous'):
                services.resolve_task_id({'content': content})

    def test_actual_wait_schema_receives_native_id_and_bounded_arguments(self):
        reply = services.scripted_response(launched_body(), 'baseline')
        call = reply['response']['content'][0]
        self.assertEqual(call['name'], 'TaskOutput')
        self.assertEqual(call['input'], {'task_id': 'native-returned-agent-42', 'block': True, 'timeout': 30000})
        body = launched_body()
        body['tools'] = [t for t in body['tools'] if t['name'] != 'TaskOutput']
        with self.assertRaisesRegex(ValueError, 'required_tool_not_advertised:TaskOutput'):
            services.scripted_response(body, 'baseline')

    def test_wait_limit_and_unknown_models_reject_without_synthetic_completion(self):
        body = launched_body()
        for index in range(3):
            reply = services.scripted_response(body, 'baseline')
            self.assertEqual(reply['stage'], f'root_wait_{index + 1}')
            advance(body, reply['response'], 'native task is still running')
        with self.assertRaisesRegex(ValueError, 'bounded_task_output_limit'):
            services.scripted_response(body, 'baseline')
        for body in (request(child=True), launched_body()):
            body['model'] = 'other-model'
            with self.assertRaisesRegex(ValueError, 'wrong_model'):
                services.scripted_response(body, 'baseline')

    def test_sync_launch_finishes_without_claiming_task_output(self):
        body = launched_body('agentId: native-sync-id\n' + frozen.CHILD_SENTINEL)
        reply = services.scripted_response(body, 'baseline')
        self.assertEqual(reply['stage'], 'root_final')
        self.assertEqual(reply['probe_details']['task_output_calls'], 0)
        self.assertEqual(reply['probe_details']['completion_evidence'][0]['source'], 'Agent-launch-result')

    def test_real_local_http_transport_retains_all_scripted_requests_and_results(self):
        with tempfile.TemporaryDirectory() as temp, services.ProbeServices(host='127.0.0.1', output=Path(temp)) as server:
            root, child = request(), request(child=True)
            reply = post(server, root)
            advance(root, reply, 'native read result fixture')
            reply = post(server, root)
            advance(root, reply, 'agentId: actual-native-id\nNative child launched asynchronously')
            wait = post(server, root)
            child_reply = post(server, child)
            advance(child, child_reply, 'native child read result fixture')
            self.assertEqual(post(server, child)['content'][0]['text'], frozen.CHILD_SENTINEL)
            advance(root, wait, json.dumps({'status': 'completed', 'task_id': 'actual-native-id', 'output': frozen.CHILD_SENTINEL}))
            self.assertEqual(post(server, root)['content'][0]['text'], frozen.FINAL_SENTINEL)
            validation = server.validate()
            self.assertTrue(validation['valid'], validation)
            self.assertFalse(validation['all_native_child_modes_validated'])
            trace = server.dump_trace()
            self.assertEqual(len(trace['rows']), 6)
            self.assertTrue(all(row['authorization_is_exact_fixture'] for row in trace['rows']))
            self.assertTrue((Path(temp) / 'requests.jsonl').exists())
            self.assertEqual(len(trace['rows']), len((Path(temp) / 'requests.jsonl').read_text().splitlines()))
            # The original full launch result is still retained in the wait request.
            self.assertIn('actual-native-id', json.dumps(trace['rows'][2]['body']))

    def test_rejected_unknown_native_id_is_retained_by_transport(self):
        with tempfile.TemporaryDirectory() as temp, services.ProbeServices(host='127.0.0.1', output=Path(temp)) as server:
            body = launched_body('native launch shape has no explicit ID')
            with self.assertRaises(HTTPError):
                post(server, body)
            self.assertEqual(server.rejected_requests[0]['body'], body)
            self.assertFalse(server.validate()['valid'])

    def test_slot_controller_ast_matches_frozen_except_fake_service_class(self):
        def node(path):
            tree = ast.parse(Path(path).read_text())
            return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_slot')
        original, probe = node(integration.__file__), node(run.__file__)
        class Normalize(ast.NodeTransformer):
            def visit_Name(self, n):
                if n.id == 'ProbeServices':
                    n.id = 'FakeServices'
                return n
        self.assertEqual(ast.dump(original, include_attributes=False), ast.dump(Normalize().visit(probe), include_attributes=False))


if __name__ == '__main__':
    unittest.main(verbosity=2)
