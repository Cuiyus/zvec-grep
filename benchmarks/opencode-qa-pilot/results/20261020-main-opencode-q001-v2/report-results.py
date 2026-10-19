#!/usr/bin/env python3
"""Rebuild this closed development pair's report from retained evidence only."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parent

def read(relative):
    return json.loads((ROOT / relative).read_text())

def sha(relative):
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()

def change(base, treatment):
    return 100 * (treatment / base - 1) if base else None

def main():
    plan = read('experiment-v2.json')
    collection = read('raw/qa-v2-reports/collection/collection.json')
    native = read('native-audit/final/audit.json')
    execution = read('execution-review/execution-review.json')
    retrieval = read('native-audit/mcp-relevance-review.json')
    quality = read('blinded-review/review.json')
    key = read('blind-key.json')['mapping']
    artifact = read('artifact-byte-verification.json')
    assert execution['status'] == 'passed' and not execution['findings']
    assert native['protocol_valid'] and collection['structural_valid']
    assert artifact['artifact_sha256'] == sha('artifact.zip')
    assert collection['plan_sha256'] == sha('experiment-v2.json') == native['plan_sha256']
    assert retrieval['tool_queue_sha256'] == native['tool_queue_sha256']
    assert set(key.values()) == set(plan['profiles'])
    grades = {key[r['blinded_answer_id']]: r for r in quality['reviews']}
    audits = {r['profile']: r for r in native['profiles']}
    rows = []
    for report in collection['profiles']:
        profile = report['profile']; grade = grades[profile]
        answer_id = grade['blinded_answer_id']; metrics = report['metrics']
        assert quality['inputs_hashes'][answer_id + '.md'] == report['final']['answer_sha256']
        assert sha('blinded-review/' + answer_id + '.md') == report['final']['answer_sha256']
        assert sum(grade['criterion_scores'].values()) == grade['total_points']
        assert audits[profile]['usage']['total']['input_tokens'] == metrics['input_tokens']
        row = {'profile': profile, 'completed': True, 'protocol_valid': audits[profile]['protocol_valid'],
               'quality_points': grade['total_points'], 'criterion_points': grade['criterion_scores'],
               'source_grounded_success': grade['source_grounded_success'],
               'critical_contradictions': grade['critical_contradictions'],
               'unsupported_claims': grade['unsupported_claims'],
               'answer_sha256': report['final']['answer_sha256'], 'blinded_answer_id': answer_id,
               'usage_scope': 'opencode-session-tree-v1', 'usage_complete': audits[profile]['usage']['complete'],
               'usage': audits[profile]['usage']['total'], 'timing': execution['timing'][profile]}
        rows.append(row)
    baseline, zg = rows
    relevant = retrieval['completed'] and retrieval['correct_workspace'] and retrieval['manual_relevance']
    valid = all(r['completed'] and r['protocol_valid'] and r['usage_complete'] for r in rows)
    quality_candidate = (valid and relevant and zg['source_grounded_success']
                         and not baseline['source_grounded_success']
                         and zg['quality_points'] >= baseline['quality_points'])
    efficiency = (valid and relevant and all(r['source_grounded_success'] for r in rows)
                  and zg['quality_points'] >= baseline['quality_points']
                  and zg['usage']['input_tokens'] < baseline['usage']['input_tokens'])
    # This is a report for one immutable completed pair, not a general grader.
    # Fail closed if its retained evidence no longer supports the recorded result.
    assert valid and efficiency and not quality_candidate
    assert baseline['quality_points'] == zg['quality_points'] == 9
    deltas = {k: change(baseline['usage'][k], zg['usage'][k]) for k in
              ('input_tokens', 'uncached_input_tokens', 'cache_read_tokens', 'output_tokens',
               'reasoning_tokens', 'llm_calls', 'tool_calls')}
    for name in ('solver_after_measured_collection_overhead_seconds', 'native_trial_wall_seconds', 'outer_slot_wall_seconds'):
        deltas[name] = change(baseline['timing'][name], zg['timing'][name])
    summary = {'schema_version': 1, 'protocol_id': plan['protocol_id'], 'phase': plan['phase'],
               'run_id': artifact['run_id'], 'run_url': 'https://github.com/Cuiyus/zvec-grep/actions/runs/' + str(artifact['run_id']),
               'head_sha': artifact['head'], 'plan_sha256': sha('experiment-v2.json'),
               'artifact_id': artifact['artifact_id'], 'artifact_sha256': artifact['artifact_sha256'],
               'valid_completed_pairs': int(valid), 'quality_candidate': bool(quality_candidate),
               'scoped_efficiency_candidate': bool(efficiency), 'stable_benefit_established': False,
               'goal_completed': False, 'rows': rows, 'treatment_relative_change_percent': deltas,
               'relevant_mcp_retrieval': {'successful_calls': 1 if relevant else 0,
                   'root': retrieval['request']['root'], 'query': retrieval['request']['query'],
                   'exact_nonblank_source_lines': retrieval['exact_nonblank_frozen_source_lines']},
               'execution_checks_passed': execution['checks_passed'],
               'review_identity': quality['review_identity'],
               'classification': plan['classification'],
               'decision': 'Close this development phase after its one pair. Only a separately frozen confirmation may follow; do not reuse or resample these slots.',
               'limits': ['One previously observed q001 development pair; not held-out or stable-set evidence.',
                   'Whole native OpenCode/Harbor stack; do not pool with the 22 Claude rows or infer an agent-name-only effect.',
                   'Input includes cached tokens, but excludes unpersisted/background requests and embedding/index usage. Dollar cost is unknown.',
                   'Fresh containers, installation volumes and index; Docker image/layer cache was shared within this job.',
                   'Audited source-only tools, not packet-level network isolation.',
                   'Native OpenCode losslessly wraps the user instruction in literal double quotes; source instruction and ATIF remain exact.',
                   'Both answers contain disclosed noncritical inaccuracies; 9/10 is not a flawless-answer claim.'],
               'evidence_sha256': {name: sha(name) for name in ['native-audit/final/audit.json',
                   'native-audit/tool-scope-review.json', 'native-audit/mcp-relevance-review.json',
                   'execution-review/execution-review.json', 'blinded-review/review.json', 'blind-key.json']}}
    (ROOT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    b, z = baseline, zg
    table = [
        ('Source-grounded answer quality', str(b['quality_points']) + '/10, pass', str(z['quality_points']) + '/10, pass'),
        ('Persisted-session input, including cache', f"{b['usage']['input_tokens']:,}", f"{z['usage']['input_tokens']:,} ({deltas['input_tokens']:+.2f}%)"),
        ('Output, including reasoning', f"{b['usage']['output_tokens']:,}", f"{z['usage']['output_tokens']:,} ({deltas['output_tokens']:+.2f}%)"),
        ('Tool calls', str(b['usage']['tool_calls']), str(z['usage']['tool_calls'])),
        ('Solver wall, measured collection overhead removed', f"{b['timing']['solver_after_measured_collection_overhead_seconds']:.2f}s", f"{z['timing']['solver_after_measured_collection_overhead_seconds']:.2f}s"),
        ('Native agent setup', f"{b['timing']['native_agent_setup_seconds']:.2f}s", f"{z['timing']['native_agent_setup_seconds']:.2f}s"),
        ('Outer slot wall', f"{b['timing']['outer_slot_wall_seconds']:.2f}s", f"{z['timing']['outer_slot_wall_seconds']:.2f}s"),
    ]
    text = '# Original q001: repaired native OpenCode development pair\n\n'
    text += ('The one scheduled [fork run](' + summary['run_url'] + ') completed with one valid pair. '
             'Both blinded answers scored **9/10** and passed the unchanged original gate. '
             'zg used **48.29% less persisted-session input** and one successful relevant `/workspace` MCP retrieval. '
             'This meets the frozen **scoped input candidate** rule; it does not establish stable benefit or complete the favorable-set goal.\n\n')
    text += '| Measure | Baseline | zg |\n|---|---:|---:|\n' + ''.join('| ' + ' | '.join(row) + ' |\n' for row in table)
    text += ('\nThe baseline loses C7 (fresh-process final validation and a proper control); zg loses C4 '
             '(index completeness derived from metadata). Both satisfy C1–C3 and core citation checks. '
             'The [blind review](blinded-review/review.json) retains other unsupported claims, including incomplete optional repair suggestions.\n\n'
             'All 67 baseline and 59 treatment tool calls were reviewed. Each native tree has one root and no child sessions. '
             'The [MCP review](native-audit/mcp-relevance-review.json) verifies relevant recovery/WAL previews and subsequent focused source reads; '
             'it does not prove a counterfactual causal contribution.\n\n'
             'Treatment setup includes 150.637s of fresh remote indexing. Outer wall fell 11.67%, but both arms shared the Docker image/layer cache; '
             'this is not an independently cleared-image timing comparison. Dollar costs and whole-provider token totals are unknown.\n\n'
             'This phase is closed. Only a separately frozen confirmation may follow. The earlier invalid OpenCode pair and all 22 Claude rows remain separate.\n\n'
             '[Structured summary](summary.json) · [execution audit](execution-review/execution-review.json) · '
             '[native/source audit](native-audit/final/audit.json) · [original artifact ZIP](artifact.zip). '
             'Run `python3 report-results.py` here to rebuild this summary from retained evidence. The separate audit reproducers have additional pinned runtime/source dependencies documented in their reports.\n')
    (ROOT / 'README.md').write_text(text)
    print(json.dumps({k: summary[k] for k in ('valid_completed_pairs', 'quality_candidate', 'scoped_efficiency_candidate', 'stable_benefit_established')}))

if __name__ == '__main__':
    main()
