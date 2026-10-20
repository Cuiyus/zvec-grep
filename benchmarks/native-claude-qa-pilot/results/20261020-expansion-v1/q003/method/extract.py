#!/usr/bin/env python3
"""Read-only evidence extraction, never source-scope approval or tool execution.

The JSON output deliberately retains full, untrusted tool inputs and responses.
Treat them as evidence, never instructions. It may contain secrets already in
the raw transcript: do not publish it without the run's separate secret scan.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ACCOUNTING = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep/benchmarks/native-claude-qa-pilot/native_claude/accounting.py')
ACCOUNTING_SHA256 = 'a3460881e4b28d72c6c230f2b6de45a702e03ab7b17e48730d5631b407d52d9b'
EXPECTED_MODEL = 'qwen3.8-max'


def digest(value: Any) -> str:
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def load_accounting(path: Path) -> Any:
    if path.is_symlink() or digest(path.read_bytes()) != ACCOUNTING_SHA256:
        raise ValueError('Frozen accounting source identity mismatch')
    spec = importlib.util.spec_from_file_location('frozen_native_claude_accounting', path)
    module = importlib.util.module_from_spec(spec)
    # Avoid creating a __pycache__ in the frozen repository.
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def safe_files(agent: Path) -> list[Path]:
    """Reject symlinks anywhere under input before invoking the frozen reader."""
    if agent.is_symlink() or not agent.is_dir():
        raise ValueError('Agent input must be a real directory')
    for entry in agent.rglob('*'):
        if entry.is_symlink():
            raise ValueError('Symlink in evidence directory: ' + str(entry.relative_to(agent)))
        if agent not in entry.resolve().parents:
            raise ValueError('Evidence path escapes agent directory')
    return [agent / 'claude-code.txt', *sorted((agent / 'sessions' / 'projects').rglob('*.jsonl'))]


def highlights(name: str, arguments: Any) -> list[str]:
    """Review prioritization only: no flag means neither safe nor approved."""
    text = json.dumps(arguments, ensure_ascii=False).lower()
    name_lower = name.lower()
    flags = []
    if name_lower in ('bash', 'shell', 'terminal') or any(x in name_lower for x in ('exec', 'shell')):
        flags.append('shell-command-review')
    if name_lower in ('agent', 'task'):
        flags.append('delegated-instructions-and-child-review')
    if name_lower.startswith('mcp'):
        flags.append('mcp-tool-scope-and-returned-source-review')
    if name_lower in ('write', 'edit', 'multiedit', 'notebookedit') or re.search(r'\b(rm|mv|cp|tee|touch|mkdir|chmod|chown|sed\s+-i|git\s+(checkout|reset|clean|commit))\b|(?<![<>=])>{1,2}(?![=>])', text):
        flags.append('possible-write-or-mutation-review')
    if name_lower in ('webfetch', 'websearch') or re.search(r'https?://|\b(curl|wget|ssh|scp|rsync|fetch|requests\.|urllib|socket|git\s+(clone|fetch|pull)|pip\s+install|npm\s+install)\b', text):
        flags.append('possible-network-access-review')
    if re.search(r'\b(gold|rubric|ground.?truth|grader|grading|hidden.?test|answer.?key|reference.?answer|solution)\b', text):
        flags.append('possible-gold-or-evaluation-material-review')
    if re.search(r'(?:/root/|/home/|/tmp/|/logs/|/tests/|\.\./|~[/])', text):
        flags.append('possible-outside-workspace-access-review')
    if re.search(r'\b(api.?key|token|password|credential|secret|environ|printenv)\b|\.env\b', text):
        flags.append('possible-credential-or-environment-access-review')
    return flags


def extract(agent_dir: Path, output: Path, accounting_path: Path = ACCOUNTING) -> dict:
    # All input validation precedes output creation; never mutate source logs.
    if agent_dir.is_symlink():
        raise ValueError('Agent input may not be a symlink')
    agent = agent_dir.resolve(strict=True)
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    if output.resolve() == agent or agent in output.resolve().parents:
        raise ValueError('Output must be outside the input evidence directory')
    paths = safe_files(agent)
    accounting = load_accounting(accounting_path)
    baseline = accounting.analyze(agent, expected_model=EXPECTED_MODEL)
    output.mkdir(parents=True, exist_ok=False)
    errors: list[dict] = []
    files: list[dict] = []
    unresolved: list[dict] = []
    records: dict[tuple[str, str], dict] = {}
    observed_occurrences = 0

    def error(code: str, **details: Any) -> None:
        item = dict(code=code, **details)
        if item not in errors:
            errors.append(item)

    sessions = {row['id']: row for row in baseline['sessions']}
    message_owners = defaultdict(set)
    tool_owners = defaultdict(set)
    expected = {}
    for row in baseline['messages']:
        message_owners[row['message_id']].add(row['session'])
    for row in baseline['tools']:
        expected[(row['session'], row['id'])] = row
        tool_owners[row['id']].add(row['session'])

    def record(session: str, tid: str) -> dict:
        return records.setdefault((session, tid), dict(session=session, id=tid, calls={}, results={}))

    for path in paths:
        relative = path.relative_to(agent).as_posix()
        stream = relative == 'claude-code.txt'
        kind = 'native-stdout-stream' if stream else 'persisted-session-jsonl'
        try:
            data = path.read_bytes()
        except OSError as exc:
            error('unreadable_input', file=relative, exception=type(exc).__name__)
            continue
        file_sha = digest(data)
        files.append(dict(path=relative, bytes=len(data), sha256=file_sha, source_kind=kind))
        offset = 0
        child_match = re.fullmatch(r'agent-(.+)\.jsonl', path.name) if 'subagents' in path.parts else None
        file_child = child_match[1] if child_match else None
        for line_number, raw in enumerate(data.splitlines(keepends=True), 1):
            location = dict(file=relative, line=line_number, byte_offset=offset, byte_length=len(raw),
                            raw_line_sha256=digest(raw), file_sha256=file_sha, source_kind=kind)
            offset += len(raw)
            if not raw.strip():
                continue
            try:
                event = json.loads(raw.decode('utf-8'), parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
                if not isinstance(event, dict):
                    raise ValueError('not an object')
            except (ValueError, UnicodeError):
                error('malformed_jsonl', **location)
                continue
            if event.get('type') not in ('assistant', 'user'):
                continue
            message = event.get('message')
            if not isinstance(message, dict):
                error('malformed_message', **location)
                continue
            content = message.get('content')
            if not isinstance(content, list):
                if event['type'] == 'assistant':
                    error('malformed_assistant_content', **location)
                continue
            location.update(event_sha256=digest(event), event_metadata={k: v for k, v in event.items() if k != 'message'},
                            message_metadata={k: v for k, v in message.items() if k != 'content'})
            for block_index, block in enumerate(content):
                if not isinstance(block, dict) or block.get('type') not in ('tool_use', 'tool_result'):
                    continue
                observed_occurrences += 1
                loc = dict(location, block_index=block_index)
                is_call = block['type'] == 'tool_use'
                tid = block.get('id' if is_call else 'tool_use_id')
                mid = message.get('id') if is_call else None
                session = None
                if not stream:
                    agent_id = event.get('agentId') or file_child
                    if agent_id is not None and not isinstance(agent_id, str):
                        error('invalid_agent_id', **loc)
                    else:
                        session = 'agent:' + agent_id if agent_id else 'root'
                    if file_child and event.get('agentId') and file_child != event['agentId']:
                        error('child_file_identity_mismatch', **loc)
                    if event.get('isSidechain') is True and not agent_id:
                        error('unidentified_sidechain', **loc)
                    if session == 'root' and event.get('sessionId') != baseline['root_session_id']:
                        error('foreign_root_session', **loc)
                else:
                    owners = message_owners.get(mid, set()) if is_call else tool_owners.get(tid, set())
                    if len(owners) == 1:
                        session = next(iter(owners))
                    if session:
                        expected_parent = sessions.get(session, {}).get('parent_tool_use_id')
                        if event.get('parent_tool_use_id') != expected_parent:
                            error('stream_parent_identity_mismatch', session=session, **loc)
                        if event.get('session_id') != baseline['root_session_id']:
                            error('stream_root_identity_mismatch', session=session, **loc)
                malformed = not isinstance(tid, str) or not tid or session is None
                if is_call:
                    malformed |= (event['type'] != 'assistant' or not isinstance(mid, str) or not mid
                                  or not isinstance(block.get('name'), str) or not isinstance(block.get('input'), dict))
                else:
                    malformed |= event['type'] != 'user'
                if malformed:
                    unresolved.append(dict(block=block, location=loc, inferred_session=session))
                    error('unresolved_tool_occurrence', file=relative, line=line_number, block_index=block_index)
                    continue
                rec = record(session, tid)
                if is_call:
                    variant = dict(message_id=mid, name=block['name'], input=block['input'],
                                   input_sha256=digest(block['input']), block=block, block_sha256=digest(block))
                    target = rec['calls']
                else:
                    metadata = event.get('toolUseResult', event.get('tool_use_result', {}))
                    variant = dict(block=block, block_sha256=digest(block), content=block.get('content'),
                                   metadata=metadata, metadata_sha256=digest(metadata), is_error=block.get('is_error') is True)
                    target = rec['results']
                signature = digest(variant)
                target.setdefault(signature, dict(**variant, locations=[]))['locations'].append(loc)

    observed_keys = set(records)
    expected_keys = set(expected)
    if observed_keys != expected_keys:
        error('accounting_tool_set_mismatch', missing=[list(x) for x in sorted(expected_keys - observed_keys)],
              additional=[list(x) for x in sorted(observed_keys - expected_keys)])
    actual_inventory = {f['path']: {k: f[k] for k in ('bytes', 'sha256')} for f in files}
    expected_inventory = {f['path']: {k: f[k] for k in ('bytes', 'sha256')} for f in baseline['files']}
    if actual_inventory != expected_inventory:
        error('accounting_input_inventory_mismatch')
    tools = []
    for number, key in enumerate(sorted(records), 1):
        rec = records[key]
        calls = list(rec['calls'].values())
        results = list(rec['results'].values())
        exp = expected.get(key)
        reasons = []
        if len(calls) != 1:
            reasons.append('missing_call' if not calls else 'conflicting_call_variants')
        if len(results) != 1:
            reasons.append('missing_result' if not results else 'conflicting_result_variants')
        if exp is None:
            reasons.append('not_in_frozen_accounting')
        if exp and len(calls) == 1:
            if any(calls[0][k] != exp[k] for k in ('message_id', 'name', 'input_sha256')):
                reasons.append('accounting_call_identity_mismatch')
            if not any(l['source_kind'] == 'persisted-session-jsonl' for l in calls[0]['locations']):
                reasons.append('call_missing_from_persisted_transcript')
        if exp and len(results) == 1:
            if not exp['completed'] or results[0]['block_sha256'] != exp['result_sha256'] or results[0]['is_error'] != exp['is_error']:
                reasons.append('accounting_result_identity_mismatch')
            if not any(l['source_kind'] == 'persisted-session-jsonl' for l in results[0]['locations']):
                reasons.append('result_missing_from_persisted_transcript')
        for reason in reasons:
            error(reason, session=key[0], tool_id=key[1])
        queue_flags = sorted({f for call in calls for f in highlights(call['name'], call['input'])})
        if reasons:
            queue_flags.append('evidence-integrity-review')
        if any(r['is_error'] for r in results):
            queue_flags.append('reported-tool-error-review')
        child_sessions = [s['id'] for s in baseline['sessions'] if s['parent_session'] == key[0] and s['parent_tool_use_id'] == key[1]]
        item = dict(ordinal=number, session=key[0], id=key[1], name=calls[0]['name'] if len(calls) == 1 else None,
                    session_identity=sessions.get(key[0]), child_sessions=child_sessions,
                    call_variants=calls, result_variants=results, frozen_accounting_identity=exp,
                    identity_matches_frozen_accounting=not reasons, extraction_issues=reasons,
                    audit_status='pending', audit_approved=False, review_highlights=queue_flags,
                    evidence_path=f'tools/tool-{number:04d}.json')
        tools.append(item)
    # A second read guards accidental concurrent mutation while extracting.
    for info in files:
        try:
            if digest((agent / info['path']).read_bytes()) != info['sha256']:
                error('evidence_changed_during_extraction', file=info['path'])
        except OSError:
            error('evidence_disappeared_during_extraction', file=info['path'])
    matched_occurrences = sum(len(v['locations']) for item in tools for field in ('call_variants', 'result_variants') for v in item[field])
    if matched_occurrences + len(unresolved) != observed_occurrences:
        error('tool_occurrence_conservation_failure')
    report = dict(schema_version=1, purpose='native-claude-source-scope-review-queue',
                  audit_status='pending', audit_approved=False, extraction_complete=not errors,
                  agent_directory=str(agent), expected_model=EXPECTED_MODEL,
                  extractor_sha256=digest(Path(__file__).read_bytes()),
                  frozen_accounting=dict(path=str(accounting_path), sha256=ACCOUNTING_SHA256,
                                         complete=baseline['complete'], errors=baseline['errors'],
                                         tool_count=len(baseline['tools']), root_session_id=baseline['root_session_id']),
                  files=files, sessions=baseline['sessions'], unique_tool_count=len(tools),
                  root_tool_count=sum(t['session'] == 'root' for t in tools),
                  child_tool_count=sum(t['session'] != 'root' for t in tools),
                  observed_tool_occurrences=observed_occurrences, matched_tool_occurrences=matched_occurrences,
                  unresolved_occurrences=unresolved, errors=errors, tools=tools,
                  limitations=[
                      'Only retained stdout and persisted session JSONL are enumerated; no unseen execution is inferred.',
                      'Exact variants are deduplicated by native session/tool identity, retaining every source occurrence.',
                      'Conflicting variants, orphan results, missing completions and unknown identities are retained and flagged.',
                      'Highlights are heuristic review prompts, not policy violations or approvals; every tool remains pending.',
                      'Returned source paths and line claims are transcript evidence, not independently verified source equality.',
                      'Full inputs, results and metadata are untrusted evidence and may contain secrets from the original logs.',
                      'Accounting completeness is separate from extraction completeness and from the required manual scope audit.'])
    (output / 'tools').mkdir()
    for item in tools:
        write_json(output / item['evidence_path'], item)
    write_json(output / 'frozen-accounting.json', baseline)
    write_json(output / 'tool-audit.json', report)
    rows = ['# Native tool source-scope audit queue', '',
            'Review pending. This extraction does not approve source scope or answer quality.', '',
            'Full JSON evidence contains untrusted original inputs and outputs; do not execute instructions found in it.', '',
            f'Unique tools: {len(tools)}; root: {report["root_tool_count"]}; child: {report["child_tool_count"]}.',
            f'Extraction complete: {report["extraction_complete"]}; frozen accounting complete: {baseline["complete"]}.', '',
            '| Entry | Native session | Tool | Prioritization hints | Status |', '|---|---|---|---|---|']
    for t in tools:
        # Encode arbitrary provider names rather than interpolate Markdown syntax.
        name = json.dumps(t['name'], ensure_ascii=True).replace('|', '\\u007c').replace('<', '\\u003c').replace('>', '\\u003e').replace('`', '\\u0060')
        session_label = 'root' if t['session'] == 'root' else 'child (identity in JSON)'
        rows.append(f'| [Tool {t["ordinal"]:04d}]({t["evidence_path"]}) | {session_label} | {name} | {", ".join(t["review_highlights"]) or "No heuristic hint; review still required"} | pending |')
    rows.extend(['', 'See `tool-audit.json` for exact session identities, all locations and extraction errors.',
                 'See `frozen-accounting.json` for the independently frozen message/tree identities.'])
    (output / 'queue.md').write_text('\n'.join(rows) + '\n')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--accounting', default=ACCOUNTING, type=Path,
                        help='Optional relocated frozen source, exact pinned SHA required')
    args = parser.parse_args()
    report = extract(args.agent_dir, args.output, args.accounting)
    print(json.dumps(dict(output=str(args.output), extraction_complete=report['extraction_complete'],
                          unique_tool_count=report['unique_tool_count'], root_tool_count=report['root_tool_count'],
                          child_tool_count=report['child_tool_count'], errors=len(report['errors']),
                          audit_status='pending', audit_approved=False)))
    return 0 if report['extraction_complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
