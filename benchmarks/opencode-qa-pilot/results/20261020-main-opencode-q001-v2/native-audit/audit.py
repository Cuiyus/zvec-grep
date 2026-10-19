#!/usr/bin/env python3
"""Offline native/source audit of the single scheduled q001 v2 pair.

No agent, provider, CI, shell command from a transcript, or grader is executed.
Tool scope and retrieval relevance require an explicit, hash-bound review file.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
import zipfile

sys.dont_write_bytecode = True
EXPECTED_RUN = 39315776778
EXPECTED_HEAD = 'cbf62676ff7a3cc4f815deaedf275f167107d061'
PROTOCOL = 'main-opencode-q001-v2'


def load(path): return json.loads(Path(path).read_bytes())
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(data): return hashlib.sha256(data).hexdigest()
def canon(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
def require(value, reason):
    if not value: raise ValueError(reason)


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'symlink in retained artifact')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {'sha256': sha(path), 'bytes': path.stat().st_size}
    return result


def verify_zip(archive_path, raw, expected):
    require(sha(archive_path) == expected, 'artifact ZIP digest mismatch')
    files = {}
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            relative = PurePosixPath(entry.filename)
            require(not relative.is_absolute() and '..' not in relative.parts, 'unsafe ZIP member')
            require(((entry.external_attr >> 16) & 0o170000) != 0o120000, 'ZIP symlink')
            if entry.is_dir(): continue
            name = relative.as_posix()
            require(name not in files, 'duplicate normalized ZIP member')
            data = archive.read(entry)
            files[name] = {'sha256': digest(data), 'bytes': len(data)}
    require(files == inventory(raw), 'extracted bytes differ from complete ZIP')
    return {'sha256': expected, 'bytes': archive_path.stat().st_size, 'files': files}


def db_rows(snapshot):
    result = {'session': {}, 'message': {}, 'tool': {}}
    for row in snapshot:
        data = json.loads(row['data']) if isinstance(row['data'], str) else row['data']
        kind, identifier = row['kind'], data['id']
        require(kind in result and identifier not in result[kind], 'duplicate or unknown DB row')
        result[kind][identifier] = data
    return result


def independent_totals(rows, selected):
    keys = ('input_tokens', 'output_tokens', 'text_output_tokens', 'reasoning_tokens',
            'cache_read_tokens', 'cache_write_tokens', 'uncached_input_tokens', 'tool_calls', 'llm_calls')
    result = dict.fromkeys(keys, 0)
    costs = []
    for message in rows['message'].values():
        if message['session_id'] not in selected: continue
        require(message['finished'] and not message['has_error'] and message['tokens_present'], 'unfinished/error/usage-less message')
        require((message['provider_id'], message['model_id']) == ('custom-openai', 'qwen3.8-max'), 'unexpected persisted model/provider')
        for name in ('input', 'output', 'reasoning', 'cache_read', 'cache_write'):
            value = message[name]
            require(isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0, 'invalid token counter')
        result['llm_calls'] += 1
        result['uncached_input_tokens'] += message['input']
        result['cache_read_tokens'] += message['cache_read']
        result['cache_write_tokens'] += message['cache_write']
        result['input_tokens'] += message['input'] + message['cache_read'] + message['cache_write']
        result['text_output_tokens'] += message['output']
        result['reasoning_tokens'] += message['reasoning']
        result['output_tokens'] += message['output'] + message['reasoning']
        costs.append(message['cost_usd'])
    selected_tools = [tool for tool in rows['tool'].values() if tool['session_id'] in selected]
    require(all(t['status'] in ('completed', 'error') for t in selected_tools), 'unfinished native tool')
    result['tool_calls'] = len(selected_tools)
    result['cost_usd'] = None if any(not c for c in costs) else sum(costs)
    return result


def source_path(value, source):
    if not isinstance(value, str): return None
    path = PurePosixPath(value)
    if '..' in path.parts: return None
    if path.is_absolute():
        if value == '/workspace': return source
        if not value.startswith('/workspace/'): return None
        path = path.relative_to('/workspace')
    resolved = source.joinpath(*path.parts)
    if resolved.is_symlink() or not resolved.exists(): return None
    return resolved


def mcp_source_matches(output, source, evidence):
    matches, failures = [], []
    for block in re.split(r'(?m)(?=^#\d+[ \[])', output):
        header = re.search(r'^#(\d+)[^\n]*? ([^\s]+):(\d+)(?:-(\d+))?$', block, re.MULTILINE)
        if not header: continue
        rank, relative = int(header[1]), header[2]
        path = source_path(relative, source)
        if path is None or not path.is_file():
            failures.append({'rank': rank, 'path': relative, 'reason': 'source_path_missing_or_outside'})
            continue
        lines = path.read_text().splitlines()
        for number, text in re.findall(r'^(\d+)[:-]?\t(.*)$', block, re.MULTILINE):
            number = int(number)
            exact = 1 <= number <= len(lines) and lines[number - 1] == text
            if text.strip() and exact:
                spans = [e['id'] for e in evidence if e['path'] == relative and e['start_line'] <= number <= e['end_line']]
                matches.append({'rank': rank, 'path': relative, 'line': number,
                                'text_sha256': digest(text.encode()), 'evidence_span_overlap': spans})
            elif text.strip():
                failures.append({'rank': rank, 'path': relative, 'line': number,
                                 'reason': 'nonmatching_or_short_preview_truncated_line'})
    return {'exact_nonblank_source_lines': matches, 'unverified_lines': failures,
            'relevant_span_ids': sorted({x for row in matches for x in row['evidence_span_overlap']}),
            'semantic_relevance': 'pending_manual_review; overlap is diagnostic, not QA credit'}


def tool_rows(exports, source, evidence, profile):
    result = []
    forbidden = ('websearch', 'webfetch')
    for sid, export in sorted(exports.items()):
        for message in export['messages']:
            for part in message['parts']:
                if part.get('type') != 'tool': continue
                state, name = part['state'], part['tool']
                args, output = state.get('input', {}), state.get('output', '')
                require(isinstance(output, str), 'nontext tool output needs explicit handling')
                serialized = json.dumps(args, sort_keys=True)
                flags = []
                if name in forbidden: flags.append('web_tool_called')
                if re.search(r'(/logs/|/root/|qa-(?:run|reports|prepared)|reference-answer|pair-verdict|source-manifest\.json|experiment(?:-v2)?\.json|OPENAI_API_KEY|QWEN_EMBEDDING_API_KEY)', serialized, re.I):
                    flags.append('non_source_or_private_path_in_input')
                row = {'id': profile + '/' + sid + '/' + part['id'], 'profile': profile,
                       'session_id': sid, 'part_id': part['id'], 'message_id': part['messageID'],
                       'call_id': part['callID'], 'tool': name, 'status': state['status'],
                       'input': args, 'input_sha256': digest(canon(args)),
                       'output_sha256': digest(output.encode()), 'output_bytes': len(output.encode()),
                       'flags': flags, 'scope_verdict': 'pending_manual_review'}
                if name == 'bash':
                    command = args.get('command', '')
                    if re.search(r'\b(curl|wget|ssh|scp|nc|telnet|git\s+(?:clone|fetch|pull))\b|https?://', command):
                        flags.append('possible_network_command')
                    if re.search(r'\b(rm|mv|cp|touch|mkdir|tee|chmod|chown|sed\s+-i|git\s+(?:checkout|reset|commit))\b|(?<![>&])>{1,2}(?![>&])', command):
                        flags.append('possible_write_command')
                    row['exit_code'] = state.get('metadata', {}).get('exit')
                elif name == 'read':
                    path = source_path(args.get('filePath'), source)
                    display = state.get('metadata', {}).get('display')
                    row['frozen_source_path_exists'] = path is not None
                    if path is None: flags.append('read_path_not_in_frozen_source')
                    if path and path.is_file() and isinstance(display, dict) and isinstance(display.get('text'), str):
                        start, end = display.get('lineStart'), display.get('lineEnd')
                        if isinstance(start, int) and isinstance(end, int):
                            segment = '\n'.join(path.read_text().splitlines()[start - 1:end])
                            row['display_segment_verified'] = segment.strip() == display['text'].strip()
                            row['display_range'] = [start, end]
                            if not row['display_segment_verified']: flags.append('read_display_differs_from_frozen_source')
                elif 'zvec_grep_' in name:
                    row['correct_workspace_root'] = args.get('root') == '/workspace'
                    if not row['correct_workspace_root']: flags.append('wrong_mcp_workspace_root')
                    if name.endswith('zvec_grep_search'):
                        row['retrieval'] = mcp_source_matches(output, source, evidence)
                        row['successful_correct_workspace_source_retrieval'] = (
                            state['status'] == 'completed' and row['correct_workspace_root']
                            and 'freshness:' in output and bool(row['retrieval']['exact_nonblank_source_lines']))
                elif name not in ('grep', 'glob', 'task', 'todowrite', 'todoread', *forbidden):
                    flags.append('unclassified_tool_requires_review')
                result.append(row)
    return result


def capture_integrity(trial, exports, snapshot_path):
    captures = []
    for path in sorted((trial / 'qa-evidence-v2').glob('*/capture.json')):
        data = load(path)
        for name, row in data['files'].items():
            file = path.parent / name
            if 'sha256' in row:
                require(file.is_file() and sha(file) == row['sha256'] and file.stat().st_size == row['bytes'], 'captured file hash differs')
        if data.get('transport_file'):
            row = data['transport_file']; file = path.parent / row['filename']
            require(sha(file) == row['sha256'] and file.stat().st_size == row['bytes'], 'transport diagnostics differ')
        require(data.get('valid') is True, 'failed evidence capture retained')
        if data['kind'] == 'command_file_capture':
            require((path.parent / 'status.txt').read_bytes() == b'0\n', 'nonzero evidence status')
            require(all(row['complete'] for row in data['files'].values()), 'incomplete evidence downloads')
            require(data['transport'].get('return_code') == 0 and 'error_type' not in data['transport'], 'failed evidence transport')
        captures.append({'label': data['label'], 'capture_sha256': sha(path),
                         'wall_seconds': data['wall_seconds'], 'stderr_bytes': data['files'].get('stderr.txt', {}).get('bytes')})
    expected = {'source-before', 'source-after', 'usage-snapshot'} | {'export-' + sid for sid in exports}
    require({x['label'] for x in captures} == expected, 'missing/unexpected capture set')
    require(sha(trial / 'qa-evidence-v2/usage-snapshot/stdout.json') == sha(snapshot_path), 'native snapshot download differs')
    for sid in exports:
        root = trial / 'qa-evidence-v2' / ('export-' + sid)
        require(sha(root / 'stdout.json') == sha(trial / 'qa-session-exports' / (sid + '.json')), 'session export bytes differ')
        require(load(root / 'identity-validation.json')['valid'] is True, 'export identity gate failed')
    return captures


def audit_profile(profile, run, collection, plan, source, evidence, question, pilot, qa, native_usage):
    report = {'profile': profile, 'machine_evidence_valid': False, 'protocol_valid': None,
              'quality_evaluated': False, 'errors': []}
    tools = []
    try:
        trials = list((run / 'runs' / ('q001-' + profile)).glob('*/result.json'))
        require(len(trials) == 1, 'expected one retained trial, including failures')
        trial = trials[0].parent
        report['trial'] = trial.name
        result = load(trials[0]); report['exception_info'] = result.get('exception_info')
        require(result.get('exception_info') is None, 'native trial exception')
        audit = load(trial / 'qa-source-audit.json')
        require(audit.get('valid') is True and audit.get('session_exports_complete') is True and audit['errors'] == [], 'native source/evidence audit invalid')
        expected = [{'path': p.relative_to(source).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                    for p in sorted(source.rglob('*')) if p.is_file()]
        require(len(expected) == plan['source_file_count'] and digest(canon(expected)) == plan['source_inventory_sha256'], 'auditor frozen source mismatch')
        for when in ('before', 'after'):
            compared = qa.compare_inventory(audit[when]['observed'], expected, profile)
            require(compared == audit[when] and compared['valid'], 'source inventory or baseline isolation mismatch')
            captured = load(trial / 'qa-evidence-v2' / ('source-' + when) / 'stdout.json')
            require(captured == audit[when]['observed'], 'captured source JSON differs')
        report['source_before_after_valid'] = True
        snapshot_path = trial / 'agent/opencode-session-snapshot.json'
        snapshot, usage = load(snapshot_path), load(trial / 'agent/session-usage.json')
        root, rows = qa.snapshot_tree(snapshot)
        recomputed = native_usage(snapshot, root, capture_complete=True)
        require(recomputed['complete'], 'native recursive usage incomplete')
        for key in ('root_session_id', 'sessions', 'root', 'descendants', 'total', 'complete', 'errors'):
            require(usage[key] == recomputed[key], 'native usage differs from DB:' + key)
        require(independent_totals(rows, set(rows['session'])) == usage['total'], 'independent persisted totals mismatch')
        require(independent_totals(rows, {root}) == usage['root'], 'independent root totals mismatch')
        require(independent_totals(rows, set(rows['session']) - {root}) == usage['descendants'], 'independent descendant totals mismatch')
        require(audit['exported_root_session_id'] == root and set(audit['exported_sessions']) == set(rows['session']), 'export tree differs')
        exports = {sid: load(trial / 'qa-session-exports' / (sid + '.json')) for sid in rows['session']}
        require(set(path.stem for path in (trial / 'qa-session-exports').glob('*.json')) == set(exports), 'unexpected export files')
        for sid, export in exports.items():
            qa.validate_export(export, sid, rows)
            for message in export['messages']:
                info = message['info']
                if info.get('role') != 'assistant': continue
                db = rows['message'][info['id']]
                require(bool(info.get('time', {}).get('completed')) == bool(db['finished']) and not info.get('error'), 'export assistant completion differs')
                require((info['providerID'], info['modelID']) == (db['provider_id'], db['model_id']), 'export model differs')
                tokens = info['tokens']
                for key, value in {'input': tokens['input'], 'output': tokens['output'], 'reasoning': tokens['reasoning'],
                                   'cache_read': tokens['cache']['read'], 'cache_write': tokens['cache']['write']}.items():
                    require(db[key] == value, 'native export usage differs:' + key)
        report['file_capture_integrity'] = capture_integrity(trial, exports, snapshot_path)
        trajectory = load(trial / 'agent/trajectory.json')
        user_steps = [s for s in trajectory['steps'] if s['source'] == 'user']
        require(len(user_steps) == 1 and user_steps[0]['message'] == question, 'ATIF initial question differs')
        root_users = [m for m in exports[root]['messages'] if m['info'].get('role') == 'user']
        native_user_texts = [p.get('text', '') for p in root_users[0]['parts'] if p.get('type') == 'text'] if root_users else []
        # OpenCode 1.18.4 adds literal outer double quotes to the native CLI message.
        # Independently observed in all four pre-live mock root exports; do not broadly strip quotes.
        require(native_user_texts == ['"' + question + '"'], 'native initial question differs from exact pinned CLI rendering')
        report['initial_question'] = {'atif_exact_frozen_question': True,
            'native_text_exact_frozen_question': False,
            'native_text_exact_double_quote_wrapped_frozen_question': True,
            'native_text_sha256': digest(native_user_texts[0].encode()),
            'frozen_question_sha256': digest(question.encode()),
            'rendering_note': 'Literal outer double quotes also occur in all four pre-live native fixture root exports; complete question payload is unchanged.'}
        final = pilot.final_answer(trial / 'agent/opencode.txt', trajectory, usage, snapshot, exports[root])
        root_stream_tools = {}
        message_ids = set()
        for line in (trial / 'agent/opencode.txt').read_text().splitlines():
            if not line.strip(): continue
            try: event = json.loads(line)
            except ValueError: continue
            if event.get('type') == 'step_start': message_ids.add(event['part']['messageID'])
            if event.get('type') == 'tool_use': root_stream_tools[event['part']['id']] = event['part']
        require(message_ids == {m['id'] for m in rows['message'].values() if m['session_id'] == root}, 'root stream message IDs differ from DB')
        exported_tools = {p['id']: p for m in exports[root]['messages'] for p in m['parts'] if p['type'] == 'tool'}
        require(root_stream_tools.keys() == exported_tools.keys(), 'root stream tool IDs differ')
        for tid, part in root_stream_tools.items():
            for key in ('id', 'callID', 'messageID', 'sessionID', 'tool', 'state'):
                require(part[key] == exported_tools[tid][key], 'root stream/export tool state differs:' + key)
        collected = next(x for x in collection['profiles'] if x['profile'] == profile)
        require(collected.get('structural_valid') is True, 'collector structural validation failed')
        require(collected['final'] == final, 'collector final differs')
        report['terminal_answer'] = {k: v for k, v in final.items() if k != 'answer'}
        report['terminal_answer'].update(bytes=len(final['answer'].encode()), stream_atif_export_collector_equal=True,
                                         db_confirms_message_identity_and_completion=True, db_contains_answer_text=False)
        report['session_ids'] = sorted(exports)
        report['usage'] = usage
        before_runtime, after_runtime = (audit[when]['observed']['runtime'] for when in ('before', 'after'))
        require(before_runtime['opencode'] == after_runtime['opencode'], 'OpenCode runtime drift')
        require(after_runtime['opencode']['binary_sha256'] == '6ce6570e7db9a40e7bd3304ebdfff607920bde8cafd2eb5587bd7a26f89ba0b5', 'unexpected Linux OpenCode')
        if profile == 'zvec-grep':
            require(before_runtime['zg'] == after_runtime['zg'] and after_runtime['zg']['binary_sha256'] == plan['candidate']['binary_sha256'], 'zg runtime drift or wrong binary')
        require(collected['runtime'] == after_runtime, 'collector runtime differs')
        report['runtime'] = collected['runtime']
        report['source_audit_seconds'] = audit['audit_wall_seconds']
        report['export_seconds'] = audit['export_wall_seconds']
        tools = tool_rows(exports, source, evidence, profile)
        require(len(tools) == usage['total']['tool_calls'], 'all root/child tool count differs')
        report['tools'] = {'count': len(tools), 'names': dict(collections.Counter(t['tool'] for t in tools)),
                           'terminal_statuses': dict(collections.Counter(t['status'] for t in tools)),
                           'flagged_count': sum(bool(t['flags']) for t in tools)}
        report['machine_evidence_valid'] = True
    except Exception as exc:
        report['errors'].append(type(exc).__name__ + ': ' + str(exc))
        report['protocol_valid'] = False
    return report, tools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('raw-root', 'run-dir', 'reports-dir', 'artifact-zip', 'run-metadata',
                 'repo-root', 'main-root', 'source-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--artifact-sha256', required=True)
    parser.add_argument('--tool-review', type=Path)
    args = parser.parse_args()
    for key, value in vars(args).items():
        if isinstance(value, Path): setattr(args, key, value.resolve())
    require(not args.output.exists(), 'refuse output overwrite')
    require(args.raw_root in args.run_dir.parents and args.raw_root in args.reports_dir.parents,
            'run/reports must be inside the verified raw artifact')
    metadata = load(args.run_metadata)
    require(metadata['id'] == EXPECTED_RUN and metadata['head_sha'] == EXPECTED_HEAD and metadata['run_attempt'] == 1,
            'wrong dispatched run/head/attempt')
    require(metadata['status'] == 'completed' and metadata['event'] == 'workflow_dispatch', 'run not completed dispatched phase')
    require(metadata.get('repository', {}).get('full_name') == 'Cuiyus/zvec-grep', 'unexpected run repository')
    artifact = verify_zip(args.artifact_zip, args.raw_root, args.artifact_sha256)
    plan_path = args.reports_dir / 'experiment-v2.json'; plan = load(plan_path)
    require(plan['protocol_id'] == PROTOCOL and sha(plan_path) == sha(args.repo_root / 'benchmarks/opencode-qa-pilot/experiment-v2.json'), 'frozen plan mismatch')
    for relative, expected in plan['frozen_harness_sha256'].items():
        require(sha(args.repo_root / relative) == expected, 'auditor harness differs from frozen plan:' + relative)
    for relative, expected in plan['main_file_sha256'].items():
        require(sha(args.main_root / relative) == expected, 'native main differs:' + relative)
    sys.path[:0] = [str(args.repo_root / 'benchmarks/opencode-qa-pilot'), str(args.main_root)]
    import pilot
    from repair_v2 import qa_agents as qa
    from zg_bench.agents.opencode_usage import summarize_session_usage
    execution = load(args.run_dir / 'execution.json')
    require(execution['protocol_id'] == PROTOCOL and execution['plan_sha256'] == sha(plan_path), 'execution plan differs')
    require([s['profile'] for s in execution['slots']] == plan['profiles'], 'scheduled slots differ')
    collection_path = args.reports_dir / 'collection/collection.json'
    collection = load(collection_path) if collection_path.is_file() else {'profiles': [], 'structural_valid': False}
    evidence = load(args.repo_root / 'benchmarks/zvec-issue-qa/evaluator/q001.json')['evidence_spans']
    question_path = args.repo_root / 'benchmarks/zvec-issue-qa/tasks/q001/instruction.md'
    require(sha(question_path) == plan['question_sha256'], 'frozen question differs')
    question = question_path.read_text()
    reports, tools = [], []
    for profile in plan['profiles']:
        report, profile_tools = audit_profile(profile, args.run_dir, collection, plan, args.source_root, evidence, question, pilot, qa, summarize_session_usage)
        slot = next(s for s in execution['slots'] if s['profile'] == profile)
        report['slot'] = slot
        if (slot.get('status') != 'exited' or slot.get('returncode') != 0
                or slot.get('process_cleanup', {}).get('complete') is not True
                or slot.get('container_cleanup', {}).get('complete') is not True):
            report['machine_evidence_valid'] = False
            report['protocol_valid'] = False
            report['errors'].append('slot_failed_or_cleanup_unconfirmed')
        reports.append(report); tools.extend(profile_tools)
    queue_sha = digest(canon(tools))
    if args.tool_review:
        review = load(args.tool_review)
        require(review['tool_queue_sha256'] == queue_sha, 'tool review bound to different evidence')
        decisions = review['decisions']
        require(set(decisions) == {t['id'] for t in tools}, 'every root/child tool requires one review')
        for tool in tools:
            decision = decisions[tool['id']]
            require(type(decision.get('source_only')) is bool and type(decision.get('no_web')) is bool
                    and bool(decision.get('rationale')), 'incomplete manual scope decision')
            tool['review'] = decision
        for report in reports:
            reviewed = [t for t in tools if t['profile'] == report['profile']]
            report['protocol_valid'] = report['machine_evidence_valid'] and all(t['review']['source_only'] and t['review']['no_web'] for t in reviewed)
    result = {'schema_version': 1, 'kind': 'independent_native_source_audit', 'protocol_id': PROTOCOL,
              'run_id': EXPECTED_RUN, 'head': EXPECTED_HEAD, 'plan_sha256': sha(plan_path),
              'script_sha256': sha(Path(__file__)), 'artifact_integrity': artifact,
              'profiles': reports, 'tool_queue_sha256': queue_sha, 'tools': tools,
              'quality_evaluated': False, 'benefit_claim_supported_by_this_audit': False,
              'protocol_valid': all(x['protocol_valid'] is True for x in reports) if args.tool_review else None,
              'status': 'scope_review_applied' if args.tool_review else 'pending_manual_source_no_web_and_relevance_review',
              'limitations': ['Only retained model tools/session rows are audited; no packet-level or background-provider completeness claim.',
                  'DB snapshot contains usage/IDs/completion, not final answer text.',
                  'MCP exact-source overlap is diagnostic; semantic relevance needs separate review and is not quality grading.',
                  'Cost remains unknown when custom-provider stored cost is zero/missing.',
                  'This script performs no model/native-agent/CI calls and no quality grading.']}
    args.output.mkdir(parents=True)
    (args.output / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
    (args.output / 'tool-review-queue.json').write_text(json.dumps({'tool_queue_sha256': queue_sha, 'tools': tools}, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'profiles': [{'profile': r['profile'], 'machine_evidence_valid': r['machine_evidence_valid'],
        'errors': r['errors'], 'tool_count': r.get('tools', {}).get('count')} for r in reports], 'quality_evaluated': False}))


if __name__ == '__main__': main()
