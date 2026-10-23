"""Offline, fail-closed audit of every physical attempt, including Harbor retries."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from zg_bench.agents.opencode_usage import summarize_session_usage

BUILTIN_AGENTS = ('build', 'plan', 'general', 'explore', 'compaction', 'title', 'summary')
ARMS = ('baseline_allowed', 'baseline', 'rust')
DENIED_ARMS = ('baseline', 'rust')


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


def _slot(path: Path, root: Path) -> tuple[str | None, int | None]:
    parts = path.relative_to(root).parts
    for i, name in enumerate(parts):
        if name == 'runs' and i + 1 < len(parts):
            if parts[i + 1] == 'rust' and i + 2 < len(parts):
                arm, rep = parts[i + 1], parts[i + 2]
            elif i and parts[i - 1] in ('baseline_allowed', 'baseline'):
                arm, rep = parts[i - 1], parts[i + 1]
            else:
                continue
            if re.fullmatch(r'r[1-9][0-9]*', rep):
                return arm, int(rep[1:])
    return None, None


def _config_errors(config: Any, arm: str | None) -> list[str]:
    errors = []
    try:
        value = config['agent']['kwargs']['opencode_config']
        value = json.loads(value) if isinstance(value, str) else value
        if config['agent'].get('model_name') != 'custom-openai/qwen3.8-max' or value.get('model') != 'custom-openai/qwen3.8-max':
            errors.append('configured_model_mismatch')
        model = value.get('provider', {}).get('custom-openai', {}).get('models', {}).get('qwen3.8-max', {})
        if (model.get('options') != {'enable_thinking': True, 'reasoningEffort': 'high'}
                or model.get('interleaved') != {'field': 'reasoning_content'}
                or model.get('limit', {}).get('output') != 32000):
            errors.append('configured_model_settings_mismatch')
        if config['agent']['kwargs'].get('version') != '1.18.4' or config['agent']['kwargs'].get('collect_session_usage') is not True:
            errors.append('configured_runtime_mismatch')
        if any(value.get('agent', {}).get(name, {}).get('temperature') != 0
               or value.get('agent', {}).get(name, {}).get('options') != {'seed': 42} for name in BUILTIN_AGENTS):
            errors.append('configured_agent_settings_mismatch')
        expected = {'websearch': 'deny', 'webfetch': 'deny'}
        if arm != 'baseline_allowed':
            expected['task'] = 'deny'
        permissions = [('global', value.get('permission'))] + [
            (f'agent:{name}', value.get('agent', {}).get(name, {}).get('permission'))
            for name in BUILTIN_AGENTS]
        for label, permission in permissions:
            if permission != expected:
                errors.append(f'permission_policy_mismatch:{label}')
            if isinstance(permission, dict) and arm in DENIED_ARMS and permission.get('task') != 'deny':
                errors.append(f'agent_task_permission_not_deny:{label.split(":")[-1]}')
    except (KeyError, TypeError, ValueError, AttributeError):
        errors.append('missing_or_invalid_opencode_config')
    return errors


def _trajectory_statistics(trajectory: dict) -> dict:
    sequence, output_chars, bash_search, delegations = [], Counter(), 0, []
    for step in trajectory.get('steps', []):
        calls = step.get('tool_calls') or []
        names = {}
        for call in calls:
            name = call.get('function_name', '')
            if name == 'task':
                delegations.append(dict(tool_call_id=call.get('tool_call_id'), step_id=step.get('step_id'),
                                        timestamp=step.get('timestamp'), arguments=call.get('arguments'),
                                        preceding_tool_sequence=list(sequence),
                                        agent_message=step.get('message'), agent_reasoning=step.get('reasoning_content')))
            sequence.append(name)
            names[call.get('tool_call_id')] = name
            command = (call.get('arguments') or {}).get('command', '')
            if name == 'bash' and isinstance(command, str) and re.search(
                r'(^|[\s;&|])(rg|grep|find|ls|cat|head|tail|sed|awk)([\s;|]|$)', command
            ):
                bash_search += 1
        for result in (step.get('observation') or {}).get('results', []):
            name = names.get(result.get('source_call_id'), '')
            value = result.get('content')
            if isinstance(value, str):
                output_chars['zg' if 'zvec_grep' in name else 'native'] += len(value)
    zg_positions = [i for i, name in enumerate(sequence) if 'zvec_grep' in name]
    first = zg_positions[0] if zg_positions else None
    return dict(sequence=sequence, delegations=delegations, task_calls=sequence.count('task'),
                zg_calls=len(zg_positions), first_zg_order=None if first is None else first + 1,
                native_calls_after_first_zg=None if first is None else sum(
                    'zvec_grep' not in name for name in sequence[first + 1:]),
                bash_search_or_read_calls=bash_search,
                zg_output_chars=output_chars['zg'], native_output_chars=output_chars['native'])


def _tree_behavior(tools, root_id, root_behavior):
    """All-session tool evidence; absent capture fields remain unknown."""
    def arguments(tool):
        value = tool.get('input')
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return None
        return value if isinstance(value, dict) else None
    def output_size(rows, encoding=False):
        values = [tool.get('output') for tool in rows]
        if any(not isinstance(value, str) for value in values):
            return None
        return sum(len(value.encode('utf-8') if encoding else value) for value in values)
    rows = list(tools.values())
    zg = [tool for tool in rows if 'zvec_grep' in tool.get('name', '')]
    native = [tool for tool in rows if 'zvec_grep' not in tool.get('name', '')]
    bash = [tool for tool in rows if tool.get('name') == 'bash']
    bash_args = [arguments(tool) for tool in bash]
    bash_search = None if any(args is None or not isinstance(args.get('command'), str) for args in bash_args) else sum(
        bool(re.search(r'(^|[\s;&|])(rg|grep|find|ls|cat|head|tail|sed|awk)([\s;|]|$)', args['command']))
        for args in bash_args)
    root_delegations = {row.get('tool_call_id'): row for row in (root_behavior or {}).get('delegations', [])}
    delegations = []
    for tool in rows:
        if tool.get('name') != 'task':
            continue
        context = root_delegations.get(tool.get('call_id'), {})
        args = arguments(tool)
        if args is None and context:
            args = context['arguments']
        delegations.append(dict(tool_id=tool.get('id'), tool_call_id=tool.get('call_id'),
                                session_id=tool.get('session_id'), child_session_id=tool.get('child_session_id'),
                                root_call=tool.get('session_id') == root_id, status=tool.get('status'),
                                arguments=args, target=None if args is None else args.get('subagent_type'),
                                prompt=None if args is None else args.get('prompt'),
                                root_trigger_context=context or None,
                                output_chars=output_size([tool])))
    return dict(zg_output_chars=output_size(zg), native_output_chars=output_size(native),
                zg_output_bytes=output_size(zg, True), native_output_bytes=output_size(native, True),
                bash_search_or_read_calls=bash_search, delegations=delegations,
                tool_input_capture_complete=all(arguments(tool) is not None for tool in rows),
                tool_output_capture_complete=all(isinstance(tool.get('output'), str) for tool in rows))


def _attempt(path: Path, root: Path) -> dict:
    errors = []
    arm, repetition = _slot(path, root)
    def read(name):
        try:
            return _read(path / name)
        except (OSError, ValueError):
            errors.append(f'missing_or_invalid:{name}')
            return None
    result, config = read('result.json'), read('config.json')
    if config is not None:
        errors.extend(_config_errors(config, arm))
    if result is not None:
        errors.extend('result:' + e for e in _config_errors(result.get('config'), arm))
    snapshot, usage = read('agent/opencode-session-snapshot.json'), read('agent/session-usage.json')
    tools, sessions, child_refs = {}, {}, set()
    snapshot_valid = isinstance(snapshot, list)
    if snapshot is not None and not snapshot_valid:
        errors.append('invalid_snapshot_type')
    for index, row in enumerate(snapshot if snapshot_valid else []):
        try:
            data = row['data']
            data = json.loads(data) if isinstance(data, str) else data
            kind, identifier = row['kind'], data['id']
            if kind == 'session':
                sessions[identifier] = data
            elif kind == 'tool':
                tools[identifier] = data
                if data.get('child_session_id'):
                    child_refs.add(data['child_session_id'])
        except (KeyError, TypeError, ValueError):
            errors.append(f'invalid_snapshot_row:{index}')
            snapshot_valid = False
    task_calls = sum(t.get('name') == 'task' for t in tools.values()) if snapshot_valid else None
    child_ids = {sid for sid, value in sessions.items() if value.get('parent_id')}
    child_ids.update(child_refs)
    totals = None
    if isinstance(usage, dict):
        root_id = usage.get('root_session_id')
        models = {(model.get('provider_id'), model.get('model_id'))
                  for session in usage.get('sessions', []) for model in session.get('provider_models', [])}
        if models != {('custom-openai', 'qwen3.8-max')}:
            errors.append('actual_session_model_mismatch' if models else 'actual_session_model_unobserved')
        child_ids.update(sid for sid in sessions if root_id and sid != root_id)
        child_ids.update(s.get('session_id') for s in usage.get('sessions', [])
                         if s.get('session_id') != root_id)
        if usage.get('complete') is not True:
            errors.append('incomplete_session_usage')
        if not root_id:
            errors.append('missing_root_session_id')
        if snapshot_valid:
            computed = summarize_session_usage(snapshot, root_id)
            if not computed['complete']:
                errors.extend('snapshot:' + e for e in computed['errors'])
            for scope in ('root', 'descendants', 'total'):
                if usage.get(scope) != computed[scope]:
                    errors.append(f'usage_snapshot_mismatch:{scope}')
            totals = computed['total']
    elif usage is not None:
        errors.append('invalid_session_usage_type')
    children = len(child_ids) if snapshot_valid and isinstance(usage, dict) else None
    if arm in DENIED_ARMS and task_calls:
        errors.append('task_tool_called')
    if arm in DENIED_ARMS and child_ids:
        errors.append('child_session_created_or_referenced')
    trajectory = read('agent/trajectory.json')
    behavior = _trajectory_statistics(trajectory) if isinstance(trajectory, dict) else None
    if behavior and behavior['task_calls']:
        if arm in DENIED_ARMS:
            errors.append('trajectory_task_tool_called')
        task_calls = max(task_calls or 0, behavior['task_calls'])
    if arm is None:
        errors.append('unknown_execution_slot')
    tree_behavior = (_tree_behavior(tools, usage.get('root_session_id'), behavior)
                     if snapshot_valid and isinstance(usage, dict) else None)
    result = result or {}
    failed = bool(result.get('exception_info')) or not result.get('finished_at')
    return dict(path=str(path.relative_to(root)), trial_id=result.get('id'), trial_name=result.get('trial_name', path.name),
                arm=arm, repetition=repetition, task_policy='allow' if arm == 'baseline_allowed' else 'deny',
                retry_history='.retry-history' in path.parts,
                failed=failed, exception_type=(result.get('exception_info') or {}).get('exception_type'),
                complete=not errors, errors=sorted(set(errors)), task_tool_calls=task_calls if not errors else None,
                child_sessions=children if not errors else None, observed_task_tool_calls=task_calls, observed_child_sessions=children, tool_counts=dict(Counter(t.get('name', '') for t in tools.values()))
                if snapshot_valid else None, usage=totals if not errors else None, observed_usage=totals, behavior=behavior, tree_behavior=tree_behavior)



def _job(path: Path, root: Path) -> Path | None:
    relative = path.relative_to(root)
    for i, part in enumerate(relative.parts):
        if re.fullmatch(r'r[1-9][0-9]*', part) and i + 1 < len(relative.parts):
            return root.joinpath(*relative.parts[:i + 2])
    return None


def _history(candidates: set[Path], root: Path) -> list[str]:
    """Cross-check the manifest so wholly missing failure directories cannot vanish."""
    issues = []
    jobs = {job for path in candidates if (job := _job(path, root)) is not None}
    jobs.update(path.parent.parent for path in root.rglob('history.json') if path.parent.name == '.retry-history')
    for job in sorted(jobs):
        label = str(job.relative_to(root))
        try:
            history = _read(job / '.retry-history/history.json')
            trials = history['trials']
            if history.get('schema_version') != 1 or not isinstance(trials, dict) or not trials:
                raise ValueError('invalid history')
            declared = set()
            for name, rows in trials.items():
                if Path(name).name != name or not isinstance(rows, list) or not rows:
                    raise ValueError('invalid trial history')
                if [row.get('attempt') for row in rows] != list(range(1, len(rows) + 1)):
                    raise ValueError('invalid attempt sequence')
                for row in rows:
                    archive = row.get('archive')
                    if archive is not None:
                        if archive != f"{name}/attempt-{row['attempt']}":
                            raise ValueError('invalid archive path')
                        path = job / '.retry-history' / archive
                    else:
                        if row is not rows[-1] or row.get('exception_type') is not None:
                            raise ValueError('missing failure archive')
                        path = job / name
                    declared.add(path)
                    candidates.add(path)
                    try:
                        result = _read(path / 'result.json')
                        if not row.get('trial_id') or result.get('id') != row['trial_id']:
                            issues.append(f'{label}:history_trial_identity_mismatch:{name}:{row["attempt"]}')
                    except (OSError, ValueError):
                        issues.append(f'{label}:missing_declared_attempt:{name}:{row["attempt"]}')
                current = job / name
                candidates.add(current)
                declared.add(current)
                try:
                    current_result = _read(current / 'result.json')
                    if current_result.get('id') != rows[-1].get('trial_id'):
                        issues.append(f'{label}:current_attempt_not_last_history_entry:{name}')
                except (OSError, ValueError):
                    issues.append(f'{label}:missing_current_attempt:{name}')
            undeclared = {path for path in candidates if _job(path, root) == job} - declared
            if undeclared:
                issues.append(f'{label}:attempts_missing_from_history')
        except (OSError, KeyError, TypeError, ValueError):
            issues.append(f'{label}:missing_or_invalid_retry_history')
    return issues


def audit(output: Path, expected_slots: int | None = None) -> dict:
    """Audit all attempts; persist evidence before rejecting an incomplete strict audit.

    Missing evidence is ``None``, never zero. A successful retry does not excuse
    missing delegation evidence on its failed predecessor. ``expected_slots``
    counts logical arm/repetition slots, not physical retry attempts.
    """
    output = output.resolve()
    candidates = set()
    for path in output.rglob('config.json'):
        try:
            value = _read(path)
            if isinstance(value, dict) and ('trial_name' in value or ('agent' in value and 'task' in value)):
                candidates.add(path.parent)
        except (OSError, ValueError):
            if _slot(path.parent, output)[0]:
                candidates.add(path.parent)
    for path in output.rglob('result.json'):
        try:
            value = _read(path)
            if isinstance(value, dict) and ('trial_name' in value or 'agent_info' in value):
                candidates.add(path.parent)
        except (OSError, ValueError):
            pass
    candidates.update(path.parent for path in output.rglob('agent') if path.is_dir())
    issues = _history(candidates, output)
    attempts = [_attempt(path, output) for path in sorted(candidates)]
    slots = {(r['arm'], r['repetition']) for r in attempts if r['arm']}
    current = [r for r in attempts if not r['retry_history']]
    current_slots = [(r['arm'], r['repetition']) for r in current]
    if not attempts:
        issues.append('no_attempt_evidence')
    if len(current_slots) != len(set(current_slots)):
        issues.append('duplicate_current_attempt_slot')
    if expected_slots is not None:
        if expected_slots < 1 or expected_slots % len(ARMS):
            raise ValueError('expected_slots must be positive and divisible by three')
        expected = {(arm, rep) for arm in ARMS for rep in range(1, expected_slots // len(ARMS) + 1)}
        if slots != expected or set(current_slots) != expected:
            issues.append('missing_or_unexpected_execution_slots')
        if any(row['failed'] for row in current):
            issues.append('failed_current_execution')
    # Terminal failures may exist both in the active folder and the archive.
    # Audit both copies, but count their physical execution only once.
    physical = {row['trial_id'] or row['path']: row for row in attempts}
    def known_sum(key, rows=None):
        values = [row[key] for row in (physical.values() if rows is None else rows)]
        return sum(values) if values and all(value is not None for value in values) else None
    complete = bool(attempts) and not issues and all(row['complete'] for row in attempts)
    per_arm = {}
    for arm in ARMS:
        rows = [row for row in physical.values() if row['arm'] == arm]
        all_copies = [row for row in attempts if row['arm'] == arm]
        per_arm[arm] = dict(task_policy='allow' if arm == 'baseline_allowed' else 'deny',
                            policy='allowed_not_forced' if arm == 'baseline_allowed' else 'denied',
                            complete=bool(rows) and not issues and all(row['complete'] for row in all_copies),
                            physical_attempts=len(rows),
                            task_tool_calls=known_sum('task_tool_calls', rows),
                            child_sessions=known_sum('child_sessions', rows),
                            usage={key: dict(known_subtotal=sum(row['observed_usage'][key] for row in rows
                                                               if row.get('observed_usage') and row['observed_usage'].get(key) is not None),
                                             unknown_attempts=sum(row.get('usage') is None or row['usage'].get(key) is None for row in rows),
                                             total=(sum(row['usage'][key] for row in rows)
                                                    if rows and all(row.get('usage') and row['usage'].get(key) is not None for row in rows) else None))
                                   for key in ('input_tokens', 'output_tokens', 'text_output_tokens', 'reasoning_tokens', 'tool_calls', 'llm_calls')})
    data = dict(schema_version=2, kind='qwen38-subagent-ablation-audit', complete=complete,
                expected_slots=expected_slots, observed_slots=len(slots),
                physical_attempts=len(physical), evidence_directories=len(attempts),
                retry_attempts=max(0, len(physical) - len(current)),
                failed_attempts=sum(r['failed'] for r in physical.values()),
                task_tool_calls=known_sum('task_tool_calls'), child_sessions=known_sum('child_sessions'),
                per_arm=per_arm, issues=issues, incomplete_attempts=sum(not r['complete'] for r in attempts), attempts=attempts)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'no-subagent-audit.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    if expected_slots is not None and not complete:
        raise ValueError('No-subagent audit incomplete or failed; see no-subagent-audit.json')
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-slots', type=int)
    args = parser.parse_args()
    result = audit(args.output, args.expected_slots)
    raise SystemExit(0 if result['complete'] else 1)
