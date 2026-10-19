"""Append only the independently audited and blindly graded new pair."""
from pathlib import Path
import datetime
import hashlib
import json

WT = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
BASE = WT / 'benchmarks/claude-qa-pilot/results/20261019'
OUT = BASE / 'answer-verification-v1'
RAW = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/answer-verification-pilot/execution-v1')

def read(path):
    return json.loads(path.read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

verdict = read(OUT / 'pair-verdict.json')
assert verdict['run_id'] == 39302184475
assert verdict['phase'] == 'answer-verification-development'
review = read(OUT / 'blinded-review.json')
reviews = {r['blinded_answer_id']: r for r in review['reviews']}
mapping = read(OUT / 'review-mapping.json')
by_profile = {r['profile']: reviews[r['blinded_answer_id']] for r in mapping['records']}
setup = read(OUT / 'setup.json')
results = read(BASE / 'results.json')
old_rows = results['rows'][:]
assert len(old_rows) == 20 and not any(r['run_id'] == 39302184475 for r in old_rows)
assert all(read(OUT / n)['passed'] is True for n in ('secret-scan.json', 'private-seed-secret-scan.json', 'collection-secret-scan.json'))
for profile in ('baseline', 'zg'):
    m = read(OUT / f'r01-{profile}/metrics.json')
    r = by_profile[profile]
    a = verdict['execution']['arms'][profile]
    q = verdict['quality'][profile]
    assert q['factual_points'] == r['total_points'] and q['source_grounded_success'] == r['source_grounded_success']
    assert a['metrics_sha256'] == sha(OUT / f'r01-{profile}/metrics.json')
    row = {
        'task_id': 'zvec-q004', 'protocol': 'answer-verification-v1',
        'phase': 'answer-verification-development', 'run_id': 39302184475,
        'repeat': 1, 'profile': profile, 'quality_score': r['total_points'],
        'quality_maximum': 10, 'criterion_scores': q['criterion_scores'],
        'source_grounded_success': r['source_grounded_success'],
        'quality_status': 'fresh blinded Codex source audit; factual points and core citation gates evaluated separately',
        'quality_failed_gates': r['failed_gates'],
        'quality_critical_contradictions': [c['contradiction_id'] for c in r['critical_contradictions']],
        'protocol_valid': a['protocol_valid'], 'completed': a['completed'],
        'usage_source': m['usage_source'], 'usage_complete': a['usage_complete'],
        'efficiency_comparable': verdict['conditions']['usage_comparable'],
        'input_tokens_including_cache': a['input_tokens_including_cache'],
        'input_tokens_excluding_cache_read': m['usage']['input_tokens'] + m['usage']['cache_creation_input_tokens'] if a['usage_complete'] else None,
        'output_tokens': a['output_tokens'], 'tool_calls': m['tool_calls'],
        'tool_errors': m['tool_error_count'], 'wall_seconds': a['wall_seconds'],
        'tool_budget_admitted': m['tool_budget']['admitted'], 'tool_budget_denied': m['tool_budget']['denied'],
        'evidence_directory': 'answer-verification-v1', 'evidence_trial_directory': f'r01-{profile}',
        'source_unchanged': verdict['execution']['source_unchanged_verified'], 'secret_scan_passed': True,
        'paired_retrieval_embedding': 'qwen/qwen3.7-text-embedding',
        'active_embedding': 'qwen/qwen3.7-text-embedding' if profile == 'zg' else None,
    }
    for key in ('zg_tool_attempts', 'zg_tool_successes', 'zg_tool_errors', 'zg_tool_unresolved'):
        row[key] = m[key]
    for key in ('index_setup_seconds', 'embedding_authorization_seconds', 'mcp_preflight_seconds'):
        row[key] = setup[key]
    results['rows'].append(row)
assert results['rows'][:20] == old_rows
signal = verdict['primary_source_grounded_success_gain']
efficiency = verdict['efficiency_improvement']
stop = verdict['stop_this_q004_shared_instruction_branch']
results['planned_experiments'].append({
    'task_id': 'zvec-q004', 'phase': verdict['phase'], 'protocol': 'answer-verification-v1',
    'run_id': 39302184475, 'head_sha': verdict['head_sha'],
    'plan': '../../experiment-answer-verification-v1.json',
    'status': 'complete and independently audited/blindly graded; both outcomes retained',
    'evidence_directory': 'answer-verification-v1',
})
results['answer_verification_verdict'] = {
    'path': 'answer-verification-v1/pair-verdict.json', 'sha256': sha(OUT / 'pair-verdict.json'),
    'primary_source_grounded_success_gain': signal, 'efficiency_improvement': efficiency,
    'stop_this_q004_shared_instruction_branch': stop,
}
b, z = verdict['quality']['baseline'], verdict['quality']['zg']
sentence = (f" Shared-answer-verification q004 scores baseline {b['factual_points']}/10 versus zg {z['factual_points']}/10; "
            f"source-grounded success is {b['source_grounded_success']} versus {z['source_grounded_success']}. "
            f"Frozen candidate-quality signal={signal}, efficiency signal={efficiency}, stop-this-instruction-branch={stop}. "
            "This single post-observation development pair does not establish a stable favorable set or isolate the prompt's causal effect.")
results['status'] = ('All 22 planned trials retained across separately frozen phases; the shared-answer-verification pair is independently audited and blindly graded. Stable favorable suite benefit remains unestablished.')
results['overall_finding'] += sentence
results['interpretation_notes'].append(sentence.strip())
save(BASE / 'results.json', results)
save(RAW / 'turn-status-observation.json', {
    'observed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'progress_classification': 'progress', 'run_id': 39302184475,
    'status': 'completed', 'conclusion': 'success',
    'authoritative_evidence': 'terminal GitHub metadata and all downloaded artifacts independently audited',
    'live_run': False, 'restart_performed': False,
    'quality_pass': {p: verdict['quality'][p]['source_grounded_success'] for p in ('baseline', 'zg')},
    'goal_complete': False,
})
print(json.dumps({'rows': len(results['rows']), 'previous_20_rows_unchanged': results['rows'][:20] == old_rows,
                  'primary_source_grounded_success_gain': signal, 'efficiency_improvement': efficiency,
                  'stop_this_q004_shared_instruction_branch': stop}))
