"""Strict local scripted providers. This is transport evidence, never QA scoring."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

CHAT_KEY = 'offline-v2-chat-fixture-not-a-real-key'
EMBEDDING_KEY = 'offline-v2-embedding-fixture-not-a-real-key'
FINAL_SENTINEL = 'OFFLINE_V2_NATIVE_TRANSPORT_COMPLETE'
CHILD_SENTINEL = 'OFFLINE_V2_CHILD_TRANSPORT_COMPLETE'
QUERY = 'collection storage vector search implementation'
REQUEST_CONTRACT = {'model': 'qwen3.8-max', 'temperature': 0, 'seed': 42,
                    'enable_thinking': True, 'reasoning_effort': 'high', 'max_tokens': 32000}


def scripted_delta(body: dict, *, child: bool) -> tuple[dict, str, str]:
    """Select only advertised tools; progression is derived from actual transcript."""
    for key, expected in REQUEST_CONTRACT.items():
        if body.get(key) != expected or type(body.get(key)) is not type(expected):
            raise ValueError('request_contract:' + key)
    if 'response_format' in body or 'reasoningEffort' in body:
        raise ValueError('request_contract:unsupported_option')
    functions = {t['function']['name']: t['function'] for t in body.get('tools', [])}
    if {'websearch', 'webfetch'} & functions.keys():
        raise ValueError('web_tools_must_be_denied')
    if not functions:
        return {'role': 'assistant', 'content': 'Offline transport fixture'}, 'stop', 'background'
    previous = [call.get('function', {}).get('name')
                for message in body.get('messages', []) if message.get('role') == 'assistant'
                for call in message.get('tool_calls', [])]
    mcp = [name for name in functions if 'zvec_grep_search' in name]
    if len(mcp) > 1:
        raise ValueError('ambiguous_mcp_search')
    if not child and mcp and mcp[0] not in previous:
        name, args, stage = mcp[0], {'root': '/workspace', 'query': QUERY}, 'root_mcp'
        props = functions[name].get('parameters', {}).get('properties', {})
        if not {'root', 'query'} <= props.keys():
            raise ValueError('unexpected_mcp_schema')
    elif 'read' not in previous:
        name, args, stage = 'read', {'filePath': '/workspace/README.md', 'limit': 12}, 'child_read' if child else 'root_read'
    elif not child and 'task' not in previous:
        name, args, stage = 'task', {
            'description': 'Exercise native child read transport',
            'prompt': 'Read the first twelve lines of /workspace/README.md. This is an offline transport fixture.',
            'subagent_type': 'general'}, 'root_child'
    else:
        return {'role': 'assistant', 'content': CHILD_SENTINEL if child else FINAL_SENTINEL}, 'stop', 'child_final' if child else 'root_final'
    if name not in functions:
        raise ValueError('required_tool_not_advertised:' + name)
    return {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'offline-v2-' + stage,
        'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, 'tool_calls', stage


def embedding_response(body: dict) -> tuple[dict, dict]:
    if body.get('model') != 'qwen3.7-text-embedding' or body.get('dimensions') != 1024 or body.get('encoding_format') != 'float':
        raise ValueError('embedding_contract')
    inputs = body.get('input')
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 20 or any(not isinstance(x, str) for x in inputs):
        raise ValueError('embedding_batch')
    data = []
    for index, value in enumerate(inputs):
        # Two nonzero coordinates; deliberately no semantic model or quality signal.
        vector = [0.0] * 1024
        vector[0] = 1.0
        vector[1 + int(hashlib.sha256(value.encode()).hexdigest()[:8], 16) % 1023] = 1.0
        data.append({'object': 'embedding', 'index': index, 'embedding': vector})
    shape = {'model': body['model'], 'dimensions': 1024, 'encoding_format': 'float', 'batch_size': len(inputs),
             'inputs': [{'sha256': hashlib.sha256(x.encode()).hexdigest(), 'bytes': len(x.encode())} for x in inputs],
             'is_query_text': inputs == [QUERY]}
    return {'object': 'list', 'model': body['model'], 'data': data,
            'usage': {'prompt_tokens': len(inputs), 'total_tokens': len(inputs)}}, shape


class FakeServices:
    """Bounded HTTP services whose logs are flushed before each response."""
    def __init__(self, output: Path, *, bind: str = '0.0.0.0', max_chat_requests: int = 40):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.lock = threading.Lock()
        self.rows: list[dict] = []
        self.errors: list[str] = []
        self.profile = 'not_started'
        self.max_chat_requests = max_chat_requests
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                received = time.time()
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size < 40_000_000:
                        raise ValueError('invalid_request_size')
                    body = json.loads(self.rfile.read(size))
                    kind = 'chat' if self.path == '/v1/chat/completions' else 'embedding' if self.path == '/v1/embeddings' else None
                    if kind is None:
                        raise ValueError('unexpected_path')
                    expected_key = CHAT_KEY if kind == 'chat' else EMBEDDING_KEY
                    if self.headers.get('Authorization') != 'Bearer ' + expected_key:
                        raise ValueError('credential_not_fixture')
                    if kind == 'embedding':
                        if outer.profile != 'zvec-grep':
                            raise ValueError('embedding_outside_treatment')
                        result, shape = embedding_response(body)
                        row = {'kind': kind, 'body_shape': shape}
                        payload = json.dumps(result, separators=(',', ':')).encode()
                        content_type = 'application/json'
                    else:
                        with outer.lock:
                            if sum(r['kind'] == 'chat' for r in outer.rows) >= outer.max_chat_requests:
                                raise ValueError('chat_request_budget_exceeded')
                        child = bool(self.headers.get('x-parent-session-id'))
                        delta, finish, stage = scripted_delta(body, child=child)
                        row = {'kind': kind, 'body': body, 'stage': stage,
                               'parent_session_id': self.headers.get('x-parent-session-id'),
                               'session_id': self.headers.get('x-session-id')}
                        envelope = {'id': 'offline-v2', 'object': 'chat.completion.chunk', 'created': 1, 'model': body['model']}
                        chunks = [
                            {**envelope, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'reasoning_content': 'offline-fixture-reasoning'}, 'finish_reason': None}]},
                            {**envelope, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                            {**envelope, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                             'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150,
                                       'prompt_tokens_details': {'cached_tokens': 60},
                                       'completion_tokens_details': {'reasoning_tokens': 20}}}]
                        payload = (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
                        content_type = 'text/event-stream'
                    row.update(profile=outer.profile, at_unix=received, path=self.path,
                               authorization_is_exact_fixture=True, peer=self.client_address[0])
                    outer.record(row)
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except Exception as exc:
                    reason = type(exc).__name__ + ':' + str(exc)
                    with outer.lock:
                        outer.errors.append(reason)
                        (outer.output / 'errors.json').write_text(json.dumps(outer.errors, indent=2) + '\n')
                    self.send_error(400, 'offline_fixture_rejected_request')

        self.server = ThreadingHTTPServer((bind, 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self):
        return self.server.server_port

    def record(self, row):
        with self.lock:
            self.rows.append(row)
            with (self.output / 'requests.jsonl').open('a') as f:
                f.write(json.dumps(row, sort_keys=True) + '\n')

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def validate(self):
        if self.errors:
            raise ValueError('fake_service_errors:' + ','.join(self.errors))
        summary = {}
        for profile in ('baseline', 'zvec-grep'):
            rows = [r for r in self.rows if r['profile'] == profile]
            chat = [r for r in rows if r['kind'] == 'chat']
            stages = [r['stage'] for r in chat]
            expected = ['root_read', 'root_child', 'child_read', 'child_final', 'root_final']
            if profile == 'zvec-grep':
                expected.insert(0, 'root_mcp')
            if [s for s in stages if s != 'background'] != expected:
                raise ValueError('unexpected_scripted_stage_sequence:' + profile)
            embeddings = [r for r in rows if r['kind'] == 'embedding']
            if profile == 'baseline' and embeddings:
                raise ValueError('baseline_embedding_request')
            if profile == 'zvec-grep':
                if not any(r['body_shape']['is_query_text'] for r in embeddings):
                    raise ValueError('real_mcp_query_embedding_not_seen')
                first_chat = min(r['at_unix'] for r in chat)
                if not any(r['at_unix'] < first_chat for r in embeddings):
                    raise ValueError('setup_index_embedding_not_seen')
            summary[profile] = {'chat_requests': len(chat), 'stages': stages,
                                'embedding_requests': len(embeddings),
                                'embedding_input_count': sum(r['body_shape']['batch_size'] for r in embeddings)}
        return {'valid': True, 'profiles': summary, 'quality_evaluated': False,
                'fake_credentials_only_at_stub': True, 'real_provider_calls_authorized': False}
