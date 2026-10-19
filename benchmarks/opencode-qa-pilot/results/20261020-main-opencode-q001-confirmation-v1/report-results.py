#!/usr/bin/env python3
"""Summarize every scheduled confirmation slot without choosing an outcome."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parent
REPEATS = ('c01', 'c02')
PROFILES = ('baseline', 'zvec-grep')
MAX_POINTS = {'C1': 2, 'C2': 2, 'C3': 2, 'C4': 1, 'C5': 1, 'C6': 1, 'C7': 1}


def read(relative, optional=False):
    path = ROOT / relative
    if optional and not path.is_file():
        return {}
    return json.loads(path.read_text())


def sha(relative):
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def delta(baseline, treatment):
    if isinstance(baseline, (int, float)) and baseline > 0 and isinstance(treatment, (int, float)):
        return 100 * (treatment / baseline - 1)
    return None


def quality_gate(grade):
    if not grade:
        return None
    scores = grade['criterion_scores']
    assert set(scores) == set(MAX_POINTS)
    assert all(type(scores[k]) is int and 0 <= scores[k] <= v for k, v in MAX_POINTS.items())
    assert sum(scores.values()) == grade['total_points']
    passed = (sum(scores.values()) >= 8 and all(scores[k] == 2 for k in ('C1', 'C2', 'C3'))
              and not grade['critical_contradictions']
              and all(grade['core_citation_verification'].get(k) is True for k in ('C1', 'C2', 'C3')))
    assert passed == grade['source_grounded_success'], 'blinded grade and unchanged success gate disagree'
    return passed


def main():
    plan = read('confirmation-v1.json')
    assert plan['protocol_id'] == 'main-opencode-q001-confirmation-v1'
    assert plan['repeats'] == list(REPEATS)
    assert plan['confirmation']['development_pair_pooled'] is False
    assert plan['confirmation']['success_rule'] == 'both_repeats_independently_meet_every_pair_condition'
    dispatch = read('dispatch.json')
    mapping = read('blind-key.json')['mapping']
    assert len(mapping) == 4
    labels = {(v['repeat'], v['profile']): k for k, v in mapping.items()}
    assert set(labels) == {(r, p) for r in REPEATS for p in PROFILES}
    execution = read('execution-review/execution-review.json')
    assert execution['plan_sha256'] == sha('confirmation-v1.json')
    assert execution['run_id'] == dispatch['run_id'] and execution['execution_head_sha'] == dispatch['head']
    quality = read('blinded-review/review.json', True)
    assert sha('blinded-review/question.md') == plan['question_sha256']
    assert sha('blinded-review/rubric.json') == plan['rubric_sha256']
    if quality:
        assert quality['inputs_hashes']['question.md'] == plan['question_sha256']
        assert quality['inputs_hashes']['rubric.json'] == plan['rubric_sha256']
    reviews = quality.get('reviews', [])
    grades = {g['blinded_answer_id']: g for g in reviews}
    assert len(grades) == len(reviews) and set(grades) <= set(mapping)
    rows, pairs = [], []
    for repeat in REPEATS:
        collection = read(f'ci/{repeat}/raw/qa-confirmation-reports/collection/collection.json', True)
        native = read(f'native-audit/{repeat}/final/audit.json', True)
        retrieval = read(f'native-audit/{repeat}/mcp-relevance-review.json', True)
        timing = execution.get('repeats', {}).get(repeat, {}).get('timing', {})
        execution_valid = execution.get('repeats', {}).get(repeat, {}).get('status') == 'passed'
        if collection:
            assert collection['repeat'] == repeat and collection['plan_sha256'] == sha('confirmation-v1.json')
        if native:
            assert native['repeat'] == repeat and native['plan_sha256'] == sha('confirmation-v1.json')
            assert native['run_id'] == dispatch['run_id'] and native['head'] == dispatch['head']
        if retrieval:
            assert retrieval['tool_queue_sha256'] == native['tool_queue_sha256']
        reports = {r['profile']: r for r in collection.get('profiles', [])}
        audits = {r['profile']: r for r in native.get('profiles', [])}
        pair_rows = []
        for profile in PROFILES:
            label = labels[repeat, profile]
            report, audit, grade = reports.get(profile, {}), audits.get(profile, {}), grades.get(label, {})
            answer = report.get('final') or {}
            if grade:
                assert answer.get('answer_sha256') == quality['inputs_hashes'][label + '.md']
                assert sha('blinded-review/' + label + '.md') == answer['answer_sha256']
            usage = audit.get('usage', {})
            total = usage.get('total')
            if total is not None and report.get('metrics'):
                assert total['input_tokens'] == report['metrics']['input_tokens']
            valid = (execution_valid and collection.get('structural_valid') is True
                     and native.get('provenance_valid') is True
                     and report.get('structural_valid') is True
                     and audit.get('protocol_valid') is True and usage.get('complete') is True)
            row = {'repeat': repeat, 'profile': profile, 'blinded_answer_id': label,
                   'answer_sha256': answer.get('answer_sha256'), 'completed_final_retained': bool(answer),
                   'protocol_valid': valid, 'quality_points': grade.get('total_points'),
                   'criterion_points': grade.get('criterion_scores'),
                   'source_grounded_success': quality_gate(grade),
                   'critical_contradictions': grade.get('critical_contradictions'),
                   'unsupported_claims': grade.get('unsupported_claims'),
                   'usage_scope': 'opencode-session-tree-v1', 'usage_complete': usage.get('complete'),
                   'usage': total, 'timing': timing.get(profile),
                   'collection_errors': report.get('errors'), 'native_audit_errors': audit.get('errors')}
            rows.append(row); pair_rows.append(row)
        b, z = pair_rows
        count = retrieval.get('successful_relevant_correct_workspace_calls')
        candidate = (all(r['protocol_valid'] and r['completed_final_retained'] and r['source_grounded_success'] is True
                         for r in pair_rows)
                     and z['quality_points'] >= b['quality_points']
                     and z['usage']['input_tokens'] < b['usage']['input_tokens']
                     and type(count) is int and count > 0)
        changes = {k: delta((b['usage'] or {}).get(k), (z['usage'] or {}).get(k))
                   for k in ('input_tokens', 'uncached_input_tokens', 'output_tokens', 'reasoning_tokens', 'tool_calls')}
        for k in ('solver_after_measured_collection_overhead_seconds', 'outer_slot_wall_seconds'):
            changes[k] = delta((b['timing'] or {}).get(k), (z['timing'] or {}).get(k))
        pairs.append({'repeat': repeat, 'scoped_input_candidate': bool(candidate),
                      'successful_relevant_correct_workspace_calls': count,
                      'zg_relative_change_percent': changes})
    confirmed = execution['status'] == 'passed' and all(p['scoped_input_candidate'] for p in pairs)
    quality_complete = bool(quality) and all(not r['completed_final_retained'] or r['quality_points'] is not None for r in rows)
    summary = {'schema_version': 1, 'protocol_id': plan['protocol_id'], 'phase': plan['phase'],
               'plan_sha256': sha('confirmation-v1.json'), 'run_id': execution['run_id'],
               'head_sha': execution['execution_head_sha'], 'scheduled_slots': 4,
               'rows': rows, 'pairs': pairs, 'scoped_input_candidate_confirmed_in_both_repeats': confirmed,
               'quality_review_available': bool(quality), 'quality_review_complete_for_retained_finals': quality_complete,
               'analysis_status': 'complete' if quality_complete else 'quality_review_pending_or_unavailable',
               'development_pair_pooled': False, 'held_out_generalization_established': False,
               'favorable_benchmark_set_established': False,
               'phase_status': 'closed_after_all_scheduled_slots',
               'decision': 'Report all four scheduled slots. No replacement, resampling, prompt change or task expansion in this phase.',
               'limits': ['One previously observed task and two prospective pairs; no held-out set or statistical population claim.',
                          'Fixed baseline-then-zg order, not counterbalanced.',
                          'Input includes cache and only persisted native session trees; embedding/background/provider totals and dollar cost are excluded or unknown.',
                          'Fresh containers, install volumes and index; Docker image/layer cache can be shared within a pair.',
                          'Blinded Codex source review, not human or official SWE-QA scoring. Unsupported claims remain disclosed.',
                          'No pooling with development, invalid prior native trials, or Claude trials.']}
    (ROOT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    verdict = 'Both pairs meet the frozen scoped-input replication rule.' if confirmed else 'The two-pair scoped-input replication rule was not met.'
    if not quality_complete:
        verdict = 'Execution is closed, but quality review is pending or unavailable. No confirmation conclusion is supported yet.'
    text = '# Original q001: two prospective native confirmation pairs\n\n'
    text += verdict + ' This is a result for one previously observed task, not a favorable benchmark set.\n\n'
    text += '| Repeat | Profile | Protocol valid | Quality gate | Session input incl. cache | Solver seconds | Outer slot seconds |\n|---|---|---|---|---:|---:|---:|\n'
    for row in rows:
        points = row['quality_points']
        grade = 'unavailable' if points is None else f"{points}/10, {'pass' if row['source_grounded_success'] else 'fail'}"
        total = row['usage'] or {}; timing = row['timing'] or {}
        values = [row['repeat'], row['profile'], str(row['protocol_valid']), grade, str(total.get('input_tokens', 'unavailable'))]
        for field in ('solver_after_measured_collection_overhead_seconds', 'outer_slot_wall_seconds'):
            values.append(f'{timing[field]:.2f}' if isinstance(timing.get(field), (int, float)) else 'unavailable')
        text += '| ' + ' | '.join(values) + ' |\n'
    text += '\nEach pair must independently have valid complete trials, both answers pass the original gate, no lower zg factual score, strictly lower zg input, and relevant successful correct-workspace MCP retrieval. Missing values are not zero.\n\n'
    text += 'The phase is closed after its four scheduled slots. The development pair is not pooled. Fixed profile order and shared within-pair Docker image cache limit causal and timing interpretation; whole-provider dollar savings are unknown.\n\n'
    text += '[Structured results](summary.json) · [blind source review](blinded-review/review.json) · [execution audit](execution-review/execution-review.json). Run `python3 report-results.py` to rebuild this summary from the retained evidence.\n'
    (ROOT / 'README.md').write_text(text)
    print(json.dumps({'confirmed_in_both_repeats': confirmed, 'pairs': pairs}))


if __name__ == '__main__':
    main()
