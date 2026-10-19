#!/usr/bin/env python3
"""Read-only trajectory analysis; intentionally does not open answer/review files."""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import re

BASE = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/answer-verification-pilot/execution-v1/downloaded/qa-zvec-q004')
SOURCE = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/architecture-pilot/source-validation/final-source')
WT = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
RUBRIC = WT / 'benchmarks/zvec-issue-qa/evaluator/q004.json'
OUT = Path('/tmp/zg-answer-verification-q004-trajectory-audit.json')

def digest(b):
    return hashlib.sha256(b).hexdigest()

def record(path):
    b = path.read_bytes()
    return {'path': str(path), 'bytes': len(b), 'sha256': digest(b)}

def norm(p):
    return p.removeprefix('/workspace/').removeprefix('./')

def text_content(c):
    if isinstance(c, str):
        return c
    return ''.join(x.get('text', '') for x in c if isinstance(x, dict) and x.get('type') == 'text') if isinstance(c, list) else ''

def ranges(nums):
    out = []
    for n in sorted(set(nums)):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out

manifest = json.loads((BASE / 'manifest.json').read_text())
source_manifest = json.loads((BASE / 'source-manifest.json').read_text())
rubric = json.loads(RUBRIC.read_text())
source_cache = {}
file_checks = {}

def source_lines(p):
    if p not in source_cache:
        b = (SOURCE / p).read_bytes()
        file_checks[p] = {'file_sha256': digest(b), 'matches_run_manifest': digest(b) == source_manifest['files'][p]}
        assert file_checks[p]['matches_run_manifest'], p
        source_cache[p] = b.decode().splitlines()
    return source_cache[p]

source_span_checks = []
for e in rubric['evidence_spans']:
    ls = source_lines(e['path'])
    sh = digest(('\n'.join(ls[e['start_line'] - 1:e['end_line']]) + '\n').encode())
    row = {'evidence_id': e['id'], 'path': e['path'], 'file_matches_rubric': file_checks[e['path']]['file_sha256'] == e['file_sha256'], 'span_sha256': sh, 'span_matches_rubric': sh == e['span_sha256']}
    assert row['file_matches_rubric'] and row['span_matches_rubric'], row
    source_span_checks.append(row)

def audit(arm):
    directory = BASE / arm
    rows = [json.loads(l) for l in (directory / 'stream.jsonl').read_text().splitlines() if l]
    metrics = json.loads((directory / 'metrics.json').read_text())
    budget = json.loads((directory / 'tool-budget.json').read_text())
    calls = []
    byid = {}
    rounds = {}
    duplicates = []
    result_meta = None
    for ln, event in enumerate(rows, 1):
        if event.get('type') == 'result':
            # Exclude final-answer contents, thinking and output text.
            result_meta = {k: event.get(k) for k in ['subtype', 'is_error', 'stop_reason', 'num_turns']}
        if event.get('type') not in ['assistant', 'user']:
            continue
        msg = event.get('message', {})
        content = msg.get('content', [])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get('type') == 'tool_use':
                tid = block['id']
                if tid in byid:
                    duplicates.append(tid)
                    continue
                mid = msg['id']
                rounds.setdefault(mid, len(rounds) + 1)
                call = {'call': len(calls) + 1, 'tool_round': rounds[mid], 'assistant_message_id': mid, 'tool_use_id': tid, 'name': block['name'], 'input': block.get('input', {}), 'stream_call_line': ln, 'stream_result_lines': [], '_text': '', '_meta': {}, '_pairs': set(), '_read_pairs': []}
                calls.append(call)
                byid[tid] = call
            elif block.get('type') == 'tool_result':
                call = byid[block['tool_use_id']]
                call['stream_result_lines'].append(ln)
                call['_text'] += text_content(block.get('content', ''))
                call['is_error'] = bool(block.get('is_error', False))
                call['_meta'] = event.get('tool_use_result', {})
    assert result_meta is not None
    for call in calls:
        assert len(call['stream_result_lines']) == 1
        content = call['_text']
        call['result_text_characters'] = len(content)
        call['result_text_utf8_bytes'] = len(content.encode())
        call['result_text_sha256'] = digest(content.encode())
        hook = budget['state']['calls'][call['tool_use_id']]
        call['hook_elapsed_seconds'] = hook['elapsed_seconds']
        call['hook_admitted'] = hook['admitted']
        pairs = []
        if call['name'] == 'Read':
            path = norm(call['input']['file_path'])
            returned = []
            for l in content.splitlines():
                m = re.match(r'^(\d+)\t(.*)$', l)
                if m:
                    pairs.append((path, int(m[1]), m[2]))
                    returned.append(int(m[1]))
            meta = call['_meta'].get('file', {}) if isinstance(call['_meta'], dict) else {}
            call['read'] = {'path': path, 'requested_offset': call['input'].get('offset', 1), 'requested_limit': call['input'].get('limit'), 'returned_ranges': ranges(returned), 'displayed_numbered_line_count': len(returned), 'file_total_lines': meta.get('totalLines'), 'read_entire_file': bool(returned) and returned[0] == 1 and len(returned) == meta.get('totalLines')}
        elif call['name'] == 'Grep' and call['input'].get('output_mode') == 'content':
            path = norm(call['input'].get('path', ''))
            is_file = (SOURCE / path).is_file()
            for l in content.splitlines():
                m = re.match(r'^(\d+)[:-](.*)$', l) if is_file else None
                if m:
                    pairs.append((path, int(m[1]), m[2]))
                    continue
                m = re.match(r'^(.+?)[-:](\d+)[-:](.*)$', l)
                if m and (SOURCE / norm(m[1])).is_file():
                    pairs.append((norm(m[1]), int(m[2]), m[3]))
        elif call['name'].startswith('mcp__'):
            sections = []
            active = None
            for l in content.splitlines():
                m = re.match(r'^#(\d+).*?matchedBy=([^ ]+)\s+([^ :]+):(\d+)(?:-(\d+))?', l)
                if m:
                    active = {'rank': int(m[1]), 'matched_by': m[2], 'path': m[3], 'advertised_range': [int(m[4]), int(m[5] or m[4])], '_lines': [], 'truncated_preview': False}
                    sections.append(active)
                    continue
                if active:
                    if l == '...':
                        active['truncated_preview'] = True
                    m = re.match(r'^(\d+)\t(.*)$', l)
                    if m:
                        pairs.append((active['path'], int(m[1]), m[2]))
                        active['_lines'].append(int(m[1]))
            for sec in sections:
                sec['returned_ranges'] = ranges(sec['_lines'])
                sec['returned_line_count'] = len(sec['_lines'])
                sec['advertised_span_overlaps_evidence_ids'] = [e['id'] for e in rubric['evidence_spans'] if sec['path'] == e['path'] and max(sec['advertised_range'][0], e['start_line']) <= min(sec['advertised_range'][1], e['end_line'])]
                sec['actual_numbered_preview_overlaps'] = [{'evidence_id': e['id'], 'ranges': ranges(n for n in sec['_lines'] if e['start_line'] <= n <= e['end_line'])} for e in rubric['evidence_spans'] if sec['path'] == e['path'] and any(e['start_line'] <= n <= e['end_line'] for n in sec['_lines'])]
                del sec['_lines']
            call['mcp'] = {'correct_root': call['input'].get('root') == '/workspace', 'successful_result': not call['is_error'] and bool(sections), 'freshness_headers': content.splitlines()[:3], 'sections': sections, 'interpretation': 'Advertised ranges are navigation hints; source arrival is counted only for numbered preview lines, then exact-checked against frozen source.'}
        mismatches = []
        for path, n, line in pairs:
            ls = source_lines(path)
            if 0 < n <= len(ls) and ls[n - 1] == line:
                call['_pairs'].add((path, n))
                if call['name'] == 'Read':
                    call['_read_pairs'].append((path, n))
            else:
                reason = 'synthetic empty EOF row' if n == len(ls) + 1 and line == '' else 'numbered snippet boundary or text differs from full frozen source line'
                mismatches.append({'path': path, 'line': n, 'reason': reason})
        call['source_line_text_mismatches'] = mismatches
        if call['name'] == 'Read':
            assert all(m['reason'] == 'synthetic empty EOF row' for m in mismatches), mismatches
        call['verified_source_line_count'] = len(call['_pairs'])
        call['fully_returned_curated_evidence_spans'] = [e['id'] for e in rubric['evidence_spans'] if all((e['path'], n) in call['_pairs'] for n in range(e['start_line'], e['end_line'] + 1))]
    read_counts = Counter()
    all_counts = Counter()
    by_tool = defaultdict(lambda: {'calls': 0, 'result_characters': 0, 'result_utf8_bytes': 0})
    seen_read = set()
    requests = defaultdict(list)
    for call in calls:
        by_tool[call['name']]['calls'] += 1
        by_tool[call['name']]['result_characters'] += call['result_text_characters']
        by_tool[call['name']]['result_utf8_bytes'] += call['result_text_utf8_bytes']
        if call['name'] == 'Read':
            call['read']['previously_returned_by_Read_source_lines'] = len(set(call['_read_pairs']) & seen_read)
            seen_read.update(call['_read_pairs'])
        read_counts.update(call['_read_pairs'])
        all_counts.update(call['_pairs'])
        normalized = {k: norm(v) if k in ['path', 'file_path', 'root'] and isinstance(v, str) else v for k, v in call['input'].items()}
        requests[(call['name'], json.dumps(normalized, sort_keys=True))].append(call['call'])
    evidences = []
    for e in rubric['evidence_spans']:
        desired = {(e['path'], n) for n in range(e['start_line'], e['end_line'] + 1)}
        covered = set()
        arrivals = []
        complete_by = None
        for call in sorted(calls, key=lambda c: c['stream_result_lines'][0]):
            new = (call['_pairs'] & desired) - covered
            if new:
                arrivals.append({'call': call['call'], 'tool_round': call['tool_round'], 'name': call['name'], 'new_source_ranges': ranges(n for _, n in new)})
                covered |= new
            if covered == desired and complete_by is None:
                complete_by = {'call': call['call'], 'tool_round': call['tool_round'], 'stream_result_line': call['stream_result_lines'][0]}
        evidences.append({'evidence_id': e['id'], 'path': e['path'], 'source_range': [e['start_line'], e['end_line']], 'source_lines_in_span': len(desired), 'verified_lines_returned': len(covered), 'returned_ranges': ranges(n for _, n in covered), 'full_curated_span_complete_by': complete_by, 'arrivals': arrivals})
    evidence_map = {e['evidence_id']: e for e in evidences}
    bundles = []
    for criterion in rubric['criteria']:
        ids = criterion['evidence_sets'][0]
        points = [evidence_map[eid]['full_curated_span_complete_by'] for eid in ids]
        complete = max(points, key=lambda x: x['stream_result_line']) if all(points) else None
        bundles.append({'criterion_id': criterion['id'], 'evidence_ids': ids, 'missing_full_curated_spans': [eid for eid in ids if evidence_map[eid]['full_curated_span_complete_by'] is None], 'full_curated_bundle_complete_by': complete, 'not_quality_score': True})
    core_bundles = [b['full_curated_bundle_complete_by'] for b in bundles if b['criterion_id'] in rubric['success_rule']['required_full_credit']]
    core_complete = max(core_bundles, key=lambda x: x['stream_result_line']) if all(core_bundles) else None
    summary = {
        'artifacts': {n: record(directory / n) for n in ['stream.jsonl', 'metrics.json', 'tool-budget.json']},
        'stream_line_count': len(rows),
        'tool_calls': len(calls),
        'tool_bearing_assistant_message_rounds': len(rounds),
        'duplicate_tool_ids': duplicates,
        'all_results_resolved': all(len(c['stream_result_lines']) == 1 for c in calls),
        'successful_tool_results': sum(not c['is_error'] for c in calls),
        'error_results': sum(c['is_error'] for c in calls),
        'tool_return_volume': {'characters': sum(c['result_text_characters'] for c in calls), 'utf8_bytes': sum(c['result_text_utf8_bytes'] for c in calls), 'by_tool': dict(by_tool)},
        'read_overlap': {'returned_source_line_occurrences': sum(read_counts.values()), 'unique_source_lines': len(read_counts), 'repeated_line_occurrences': sum(v - 1 for v in read_counts.values()), 'repeated_ranges_by_path': {p: ranges(n for (pp, n), v in read_counts.items() if pp == p and v > 1) for p in sorted({p for (p, n), v in read_counts.items() if v > 1})}, 'read_calls_with_overlap': [c['call'] for c in calls if c.get('read', {}).get('previously_returned_by_Read_source_lines', 0)], 'entire_file_read_calls': [c['call'] for c in calls if c.get('read', {}).get('read_entire_file')]},
        'all_tools_source_overlap': {'verified_line_occurrences': sum(all_counts.values()), 'unique_verified_source_lines': len(all_counts), 'repeated_line_occurrences': sum(v - 1 for v in all_counts.values()), 'interpretation': 'Includes Grep-to-Read and MCP-preview-to-Read overlaps, which can provide useful context and are not automatically waste.'},
        'exact_duplicate_tool_requests': [{'name': key[0], 'normalized_input': json.loads(key[1]), 'calls': ids} for key, ids in requests.items() if len(ids) > 1],
        'evidence_spans': evidences,
        'conservative_source_bundles': bundles,
        'full_required_core_curated_bundle_complete_by': core_complete,
        'budget': {'admitted': budget['admitted'], 'denied': budget['denied'], 'all_hook_call_ids_match_stream': set(budget['state']['calls']) == set(byid)},
        'termination': result_meta,
        'metrics_context': {k: metrics[k] for k in ['input_tokens_including_cache', 'output_tokens', 'usage_complete', 'wall_seconds']},
        'metrics_match_observed_tool_counts': dict(Counter(c['name'] for c in calls)) == metrics['tool_counts'],
        'quality_not_audited': True,
        'calls': calls,
    }
    for call in calls:
        for key in list(call):
            if key.startswith('_'):
                del call[key]
    assert summary['metrics_match_observed_tool_counts']
    assert summary['budget']['all_hook_call_ids_match_stream']
    return summary

trials = {arm: audit(arm) for arm in ['r01-baseline', 'r01-zg']}
baseline, zg = trials.values()
comparison = {
    'tool_calls': [baseline['tool_calls'], zg['tool_calls']],
    'tool_bearing_rounds': [baseline['tool_bearing_assistant_message_rounds'], zg['tool_bearing_assistant_message_rounds']],
    'Read_calls': [t['tool_return_volume']['by_tool'].get('Read', {}).get('calls', 0) for t in trials.values()],
    'Read_return_characters': [t['tool_return_volume']['by_tool'].get('Read', {}).get('result_characters', 0) for t in trials.values()],
    'all_tool_return_characters': [t['tool_return_volume']['characters'] for t in trials.values()],
    'repeated_Read_line_occurrences': [t['read_overlap']['repeated_line_occurrences'] for t in trials.values()],
    'input_tokens_including_cache': [t['metrics_context']['input_tokens_including_cache'] for t in trials.values()],
    'wall_seconds': [t['metrics_context']['wall_seconds'] for t in trials.values()],
    'full_required_core_curated_bundle_complete_by': [t['full_required_core_curated_bundle_complete_by'] for t in trials.values()],
}
report = {
    'schema_version': 1,
    'run_id': 39302184475,
    'task_id': 'q004',
    'phase': 'answer-verification-development',
    'scope': 'Only tool-use/tool-result blocks and execution metadata inspected; no answer, blind mapping or quality review file opened; no answer grading.',
    'inputs': {'manifest': record(BASE / 'manifest.json'), 'source_manifest': record(BASE / 'source-manifest.json'), 'rubric': record(RUBRIC), 'script': record(Path(__file__))},
    'source_commit': rubric['source_commit'],
    'source_span_verification': source_span_checks,
    'returned_source_file_verification': file_checks,
    'definitions': {
        'tool_round': 'Unique assistant message.id containing one or more tool_use blocks; not SDK num_turns.',
        'volume': 'Sum Unicode code points and UTF-8 bytes of tool_result content text only; excludes duplicate tool_use_result metadata, JSON framing, assistant prose/thinking and usage counters. Not tokens or price.',
        'read_repetition': 'Exact frozen-source path/line pairs returned by more than one Read; each later occurrence counted once.',
        'curated_coverage': 'Conservative first evidence-set, full-line coverage diagnostic. Valid equivalent or partial evidence may support correct reasoning; missing a full span is not a quality failure.',
    },
    'trials': trials,
    'comparison_baseline_to_zg': comparison,
    'observations': [],
    'limitations': [
        'The shared final-verification instruction is a post-observation development intervention; one pair cannot identify its causal effect or verify an internal self-check occurred.',
        'One baseline-first pair; no causal attribution to embedding model and no stable benefit/generalization claim.',
        'Tool-result delivery and source-span coverage are retrieval diagnostics, not answer-quality scores.',
        'No contemporaneous local-embedding arm exists in this pair; comparing historical runs cannot isolate embedding effects.',
        'matchedBy=fts+vector is the native result annotation, not an independent measurement of vector contribution or ranking causality.',
    ],
}
OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'output': str(OUT), 'sha256': digest(OUT.read_bytes()), 'script_sha256': digest(Path(__file__).read_bytes()), 'comparison_baseline_to_zg': comparison}, ensure_ascii=False, indent=2))
