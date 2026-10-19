"""Offline guards and identity checks for the separately frozen remote pilot."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

MODEL = 'qwen/qwen3.7-text-embedding'
ENDPOINT = 'https://llm-67x4s810wr6kl2i4.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings'
ENV_NAMES = ('ZVEC_GREP_API_KEY', 'ZVEC_GREP_ENDPOINT')
PLAN_NAME = 'experiment-remote-embedding-v1.json'
INPUTS = {'task': 'zvec-q001', 'workspace_root_guidance': False,
          'completion_budget': True, 'completion_confirmation': False,
          'closed_issue_expansion': False, 'remote_embedding_pilot': True}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_plan(root: Path, here: Path) -> dict:
    """Fail closed before any provider call; this function performs no I/O network calls."""
    path = here / PLAN_NAME
    plan = json.loads(path.read_text())
    if plan.get('frozen') is not True:
        raise ValueError('remote embedding plan has not been frozen for execution')
    base = json.loads((here / 'experiment-completion-v2.json').read_text())
    checks = [plan['base_experiment'], plan['task']['question'], plan['task']['rubric'],
              plan['completion_hook'], plan['dataset'], plan['configuration_research']]
    for item in checks:
        if digest(root / item['path']) != item['sha256']:
            raise ValueError('remote embedding frozen source hash mismatch: ' + item['path'])
    if (plan.get('task') != base['task'] or plan.get('agent') != base['agent']
            or plan.get('completion_control') != base['completion_control']):
        raise ValueError('remote embedding phase must preserve the completion-v2 task and agent')
    from run import common_instructions
    if hashlib.sha256(common_instructions(False, 24, 600).encode()).hexdigest() != plan['common_instructions_sha256']:
        raise ValueError('remote embedding common instructions changed')
    if (plan['retrieval']['source_commit'] != base['retrieval']['source_commit']
            or plan['retrieval']['native_package_sha256'] != base['retrieval']['native_package_sha256']
            or plan['retrieval']['embedding_model'] != MODEL
            or plan['retrieval']['embedding_endpoint'] != ENDPOINT):
        raise ValueError('remote embedding runtime/model/endpoint differs from the declared factor')
    run = plan['run_plan']
    if (run['inputs'] != INPUTS or run['repeats'] != 1 or run['trial_count'] != 2
            or run['order'] != ['baseline', 'zg'] or run['automatic_repeats'] is not False
            or run['automatic_task_expansion'] is not False):
        raise ValueError('remote embedding phase requires exactly its single frozen pair')
    return {'path': str(path.relative_to(root)), 'sha256': digest(path)}


def validate_environment(*, require_key: bool = True) -> None:
    if os.environ.get('ZVEC_GREP_ENDPOINT') != ENDPOINT:
        raise ValueError('remote embedding requires the exact frozen QWEN_EMBEDDING_ENDPOINT variable')
    if require_key and not os.environ.get('ZVEC_GREP_API_KEY', '').strip():
        raise ValueError('remote embedding requires QWEN_EMBEDDING_API_KEY mapped to ZVEC_GREP_API_KEY')
    if any(os.environ.get(key) for key in ('DASHSCOPE_API_KEY', 'QWEN_API_KEY',
                                         'ZVEC_GREP_EMBEDDING_ENDPOINT')):
        raise ValueError('unset alternate embedding credential/endpoint environment variables')


def authorization_command() -> list[str]:
    # Main's persistent grant binds root, model and endpoint. No --api-key flag or
    # once-only --allow-remote: the subsequent real MCP probe must use the grant.
    return ['zg', '--auth', 'grant', '/workspace', '--capability', 'embedding',
            '--scope', 'workspace', '--embedding', MODEL, '--endpoint', ENDPOINT]


def verify_index_identity(index: Path) -> dict:
    """Export only non-secret identity fields, never the raw runtime manifest."""
    path = index / 'manifest.json'
    manifest = json.loads(path.read_text())
    expected = {'model': {'provider': 'qwen', 'name': 'qwen3.7-text-embedding', 'endpoint': ENDPOINT},
                'dimension': 1024, 'metric': 'cosine', 'maxBatchSize': 20, 'maxInputTokens': 128000}
    embeddings = manifest.get('embeddings', [])
    if (manifest.get('root') != '/workspace' or manifest.get('indexPolicy') != 'enabled'
            or len(embeddings) != 1 or any(embeddings[0].get(key) != value for key, value in expected.items())):
        raise ValueError('remote index model/endpoint/dimensions differ from frozen identity')
    runtimes = manifest.get('embeddingRuntimes', {})
    if set(runtimes) != {MODEL} or runtimes[MODEL].get('endpoint') != ENDPOINT:
        raise ValueError('remote index runtime endpoint differs from frozen identity')
    if any(runtime.get('apiKey') for runtime in runtimes.values()):
        raise ValueError('remote index unexpectedly persisted a provider credential')
    return {'manifest_sha256': digest(path), 'root': '/workspace', 'index_policy': 'enabled',
            'embedding': expected, 'runtime_endpoint': ENDPOINT,
            'identity_scope': 'Requested catalog model and stored index configuration; not a provider-attested model version or immutable weight revision.'}


def scan_seed_credentials(paths: list[Path], names: tuple[str, ...]) -> dict:
    """Scan private seeds without exporting their data, grant signatures or keys."""
    values = [value.encode() for name in names if (value := os.environ.get(name))]
    matches, count = [], 0
    for root in paths:
        for path in root.rglob('*'):
            if path.is_file():
                count += 1
                data = path.read_bytes()
                if any(value in data for value in values):
                    matches.append(root.name + '/' + str(path.relative_to(root)))
    result = {'passed': not matches, 'scanned_files': count, 'matched_paths': matches,
              'provider_credential_values_checked': len(values), 'seeds_uploaded': False}
    if matches:
        raise ValueError('provider credential found in private seed: ' + ', '.join(matches))
    return result
