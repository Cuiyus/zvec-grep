"""Real Linux/Docker native pair against a scripted local GLM transport.

Local Potion embeddings are real. No paid language-model endpoint is used;
fixture answers are excluded from the 100 planned live executions and scores.
"""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import cohort


def run(args):
    from repair_v2.integration import docker_info, fake_environment, verify_mcp_source
    from repair_v2 import fake_services
    from repair_v2.fake_services import FakeServices, FINAL_SENTINEL
    plan = cohort.validate(args)
    docker = docker_info()
    # Only the scripted fixture's expected request identity changes. The
    # production model/SDK/agent/request renderer remains the pinned main code.
    fake_services.REQUEST_CONTRACT = {**fake_services.REQUEST_CONTRACT, 'model': 'glm-5.2'}
    args.output.mkdir(parents=True, exist_ok=False)
    proofs = []
    with FakeServices(args.output / 'scripted-provider') as services:
        with fake_environment(f"http://{docker['docker_gateway']}:{services.port}/v1", 'http://unused-local-embedding.invalid/v1/embeddings'):
            selected = copy.copy(args); selected.output = args.output / 'native-pair'
            pair = next(r for r in plan['schedule'] if r['key'] == 'q001-r01')
            cohort.run_pair(selected, plan, pair, services=services)
            result = cohort.collect(selected, plan, pair)
            if not result['all_valid']: raise ValueError('scripted_native_pair_failed')
            for row in result['profiles']:
                if row['answer'] != FINAL_SENTINEL: raise ValueError('wrong_scripted_terminal_answer')
                usage = row['metrics']['session_usage']; expected = 5 if row['profile'] == 'baseline' else 6
                if len(usage['sessions']) != 2: raise ValueError('real_root_and_child_required')
                for key, value in {'llm_calls': expected, 'input_tokens': expected * 100,
                        'output_tokens': expected * 50, 'cache_read_tokens': expected * 60,
                        'reasoning_tokens': expected * 20}.items():
                    if usage['total'][key] != value: raise ValueError('scripted_recursive_usage_mismatch:' + key)
                if row['runtime']['opencode']['binary_sha256'] != '6ce6570e7db9a40e7bd3304ebdfff607920bde8cafd2eb5587bd7a26f89ba0b5':
                    raise ValueError('opencode_binary_changed')
                proofs.append({'profile': row['profile'], 'native_recursive_usage_exact': True,
                    'native_final_exact': True, 'original_source_unchanged': row['source_audit']['valid'],
                    'runtime_binary': row['runtime']['opencode']['binary_sha256']})
    if services.errors: raise ValueError('scripted_provider_contract_errors')
    if any(r['kind'] == 'embedding' for r in services.rows): raise ValueError('local_embedding_must_never_call_remote_embedding')
    for profile in cohort.PROFILES:
        calls = [r for r in services.rows if r['profile'] == profile and r['kind'] == 'chat']
        stages = [r['stage'] for r in calls]
        expected = ['root_read', 'root_child', 'child_read', 'child_final', 'root_final']
        if profile == 'zvec-grep': expected.insert(0, 'root_mcp')
        if sorted(s for s in stages if s != 'background') != sorted(expected):
            raise ValueError('native_fixture_stage_coverage_incomplete')
    # Match one concrete returned MCP source line to the actual frozen q001.
    z = next(r for r in result['profiles'] if r['profile'] == 'zvec-grep')
    trial = selected.output / 'runs/q001-zvec-grep' / z['retained_trials'][0]
    hits = []
    for p in (trial / 'qa-session-exports').glob('*.json'):
        export = cohort.load(p)
        for message in export['messages']:
            for part in message.get('parts', []):
                if part.get('type') == 'tool' and 'zvec_grep_search' in part.get('tool', ''):
                    state = part['state']
                    if state.get('status') != 'completed': raise ValueError('MCP_fixture_not_complete')
                    hits.append(verify_mcp_source(state['output'], cohort.prepared_root(args, 'q001')))
    if not hits: raise ValueError('actual_local_MCP_source_hit_required')
    report = {'schema_version': 1, 'protocol_id': cohort.PROTOCOL,
        'plan_sha256': cohort.sha(args.plan), 'valid': True, 'quality_evaluated': False,
        'paid_provider_calls': 0, 'remote_embedding_calls': 0,
        'language_model': 'scripted_local_GLM_transport_only', 'embedding': cohort.EMBEDDING,
        'docker': docker, 'profiles': proofs, 'MCP_source_matches': hits,
        'scripted_requests': len(services.rows), 'live_trial_count': 0}
    cohort.write(args.output / 'proof.json', report)
    print(json.dumps({'valid': True, 'proof_sha256': cohort.sha(args.output / 'proof.json'), 'paid_provider_calls': 0}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('main-root', 'prepared', 'plan', 'candidate', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    for k, v in vars(args).items(): setattr(args, k, v.resolve())
    run(args)
