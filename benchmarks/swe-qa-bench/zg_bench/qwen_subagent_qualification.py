"""Qualify final answers while preserving incomplete physical-attempt accounting.

Schema 3 distinguishes proved pre-agent apt failures from archived AgentTimeoutError
attempts with partial capture. Every final answer must be complete; no unknown
usage, tool count, or policy compliance is imputed.
"""
from __future__ import annotations

import hashlib
import copy
from datetime import datetime
from importlib.metadata import distribution, version
import json
from pathlib import Path
import re

from zg_bench import qwen_subagent_audit as auditor

KIND = 'qwen38-subagent-ablation-final-answer-qualification'
MISSING_ARTIFACTS = (
    'agent/opencode-session-snapshot.json', 'agent/session-usage.json', 'agent/trajectory.json',
)
LIMITATIONS = [
    '最终答案统计完整，但全物理执行审计未通过：允许委派组的失败历史缺少会话计量。',
    '失败执行的 token、子会话数量与成本为未知，不能填零；全执行仅报告已知小计及未知次数。',
    '质量、采用率和主/子会话均值仅描述既有成功 final；不得据此断言含全部失败执行的总成本收益。',
]
INSTALL_COMMAND = 'apt-get update && apt-get install -y curl'
LIFECYCLE_CONTRACT = dict(
    rule='frozen-opencode-pre-agent-apt-curl-exit100', source_binding='exact-reviewed-lifecycle-file-hashes-and-recorded-execution-head',
    harbor_version='0.18.0',
    repository_sha256={
        'zg_bench/agents/opencode.py': '5e3e2bdbb6fed972bc0e73eb9bed9ad8782ef2d5b8829a8639c5dbf62953891b',
        'zg_bench/agents/zvec_grep.py': '759bec239d95192892d415c9d668fa75618c55d55460322760b08bb3e9147ece',
        'zg_bench/agents/zvec_opencode.py': 'f72f0ed054fc6e37bcb375634315adf7ccad5882d1a0206089195e51f3a3412a',
        'uv.lock': '136fb8c5a3cc96828223968c572f39847f85476415bc59fa28f9765d37b7b751',
    },
    harbor_sha256={
        'harbor/trial/trial.py': '15a40691a5aa03152de4f6878347272503aaa3f976cf3eb22a1ec0e5d62609c9',
        'harbor/trial/single_step.py': '15deae31efe1287e3db4a4f57d7732eeffd28a8c4fd385e86193e81a3c944218',
        'harbor/agents/installed/base.py': 'fc715cbb5addf6088e370563f9b5ed11d16f30b76ca5185e702e62810312571b',
    },
)
INSTALL_LIMITATIONS = [
    '最终答案统计完整，但全物理执行审计未通过：部分归档失败有明确的 agent 启动前安装失败证据，缺少会话计量。',
    LIMITATIONS[1], LIMITATIONS[2],
]
NULL_PHASES = ('agent_execution', 'agent_result', 'verifier', 'verifier_result', 'step_results')


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lifecycle_source_errors(output):
    """Bind the narrow inference to the exact reviewed installer and Harbor code."""
    errors = []
    try:
        meta = json.loads((output / 'provenance.json').read_text())
        if (not re.fullmatch(r'[0-9a-f]{40}', meta.get('harness_commit', ''))
                or meta.get('installation_lifecycle_contract') != LIFECYCLE_CONTRACT):
            errors.append('installation_lifecycle_source_head_mismatch')
        if version('harbor') != LIFECYCLE_CONTRACT['harbor_version']:
            errors.append('installation_lifecycle_harbor_version_mismatch')
        for relative, digest in LIFECYCLE_CONTRACT['repository_sha256'].items():
            if _sha(Path(__file__).resolve().parents[1] / relative) != digest:
                errors.append('installation_lifecycle_source_hash_mismatch:' + relative)
        for relative, digest in LIFECYCLE_CONTRACT['harbor_sha256'].items():
            if _sha(Path(distribution('harbor').locate_file(relative))) != digest:
                errors.append('installation_lifecycle_source_hash_mismatch:' + relative)
    except (OSError, ValueError, KeyError):
        errors.append('installation_lifecycle_source_unavailable')
    return errors


def _config_subset(config, expanded):
    """Harbor result.config adds defaults; every explicitly saved value must agree."""
    if isinstance(config, dict):
        return isinstance(expanded, dict) and all(k in expanded and _config_subset(v, expanded[k]) for k, v in config.items())
    return config == expanded


def _installation_proof(path, arm):
    result = json.loads((path / 'result.json').read_text())
    config = json.loads((path / 'config.json').read_text())
    if not _config_subset(config, result['config']):
        raise ValueError('saved_config_differs_from_result')
    if any(file.is_file() for file in (path / 'agent').rglob('*')):
        raise ValueError('unexpected_agent_execution_artifacts')
    exception = result['exception_info']
    trace = exception['exception_traceback']
    if (path / 'exception.txt').read_text() != trace:
        raise ValueError('exception_file_differs_from_result')
    # Direct indexing is deliberate: absent fields are not evidence of null.
    proof = {key: result[key] for key in (*NULL_PHASES, 'started_at', 'finished_at', 'environment_setup', 'agent_setup')}
    proof.update(agent_class=config['agent']['name'], exception_type=exception['exception_type'],
                 exception_message=exception['exception_message'], exception_traceback=trace,
                 trial_log=(path / 'trial.log').read_text())
    errors = _proof_errors(proof, arm)
    if errors:
        raise ValueError(';'.join(errors))
    return proof


def _proof_errors(proof, arm):
    errors = []
    if arm not in auditor.ARMS:
        return ['unknown_installation_arm']
    expected_agent = ('zg_bench.agents.opencode:ResilientOpenCode' if arm.startswith('baseline')
                      else 'zg_bench.agents.zvec_opencode:ZvecOpenCode')
    try:
        if proof['agent_class'] != expected_agent or any(key not in proof or proof[key] is not None for key in NULL_PHASES):
            errors.append('agent_or_verifier_execution_not_explicitly_absent')
        times = [proof['started_at'], proof['environment_setup']['started_at'], proof['environment_setup']['finished_at'],
                 proof['agent_setup']['started_at'], proof['agent_setup']['finished_at'], proof['finished_at']]
        parsed = [datetime.fromisoformat(value.replace('Z', '+00:00')) for value in times]
        if parsed != sorted(parsed):
            errors.append('installation_phase_timing_invalid')
        message = proof['exception_message']
        if proof['exception_type'] != 'NonZeroAgentExitCodeError' or message.splitlines()[0] != 'Command failed (exit 100): ' + INSTALL_COMMAND:
            errors.append('not_the_frozen_apt_install_failure')
        trace = proof['exception_traceback']
        frames = re.findall(r'  File "([^"]+)", line \d+, in (\w+)\n', trace)
        expected = [('harbor/trial/trial.py', 'run'), ('harbor/trial/trial.py', '_prepare'),
                    ('harbor/trial/trial.py', '_setup_agent'), ('asyncio/tasks.py', 'wait_for')]
        if arm == 'rust':
            expected.append(('zg_bench/agents/zvec_grep.py', 'setup'))
        expected += [('harbor/agents/installed/base.py', 'setup'), ('zg_bench/agents/opencode.py', 'install'),
                     ('harbor/agents/installed/base.py', 'exec_as_root'), ('harbor/agents/installed/base.py', '_exec')]
        if len(frames) != len(expected) or any(not path.endswith('/' + suffix) or method != name
                                             for (path, method), (suffix, name) in zip(frames, expected)):
            errors.append('traceback_does_not_prove_pre_agent_installation')
        if not trace.endswith('harbor.agents.installed.base.NonZeroAgentExitCodeError: ' + message + '\n'):
            errors.append('traceback_exception_message_mismatch')
        commands = [line for line in proof['trial_log'].splitlines() if line.startswith('Running command:')]
        if commands != ['Running command: ' + INSTALL_COMMAND]:
            errors.append('trial_log_has_other_or_missing_execution_commands')
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        errors.append('missing_or_invalid_installation_proof')
    return errors


def audit_fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


TIMEOUT_LIMITATION = '部分归档 Agent 执行超时且会话采集不完整；最终答案的禁用委派验证完整，失败执行仅能排除已观察到的违例，不能宣称全物理执行的委派/成本总量已知。'
TIMEOUT_MESSAGE = 'Agent execution timed out after 1800.0 seconds'


def _timeout_errors(proof, arm):
    errors = []
    expected_agent = ('zg_bench.agents.opencode:ResilientOpenCode' if arm.startswith('baseline')
                      else 'zg_bench.agents.zvec_opencode:ZvecOpenCode')
    try:
        if (proof['agent_class'] != expected_agent or proof['exception_type'] != 'AgentTimeoutError'
                or proof['exception_message'] != TIMEOUT_MESSAGE):
            errors.append('not_the_frozen_agent_timeout')
        phases = [proof['started_at'], proof['agent_setup']['started_at'], proof['agent_setup']['finished_at'],
                  proof['agent_execution']['started_at'], proof['agent_execution']['finished_at'], proof['finished_at']]
        times = [datetime.fromisoformat(value.replace('Z', '+00:00')) for value in phases]
        if times != sorted(times) or (times[4] - times[3]).total_seconds() < 1800:
            errors.append('timeout_execution_phase_not_proven')
        trace = proof['exception_traceback']
        if ('in _run_agent_phase\n' not in trace or 'harbor/trial/trial.py' not in trace
                or 'zg_bench/agents/opencode.py' not in trace
                or not trace.rstrip().endswith('harbor.trial.errors.AgentTimeoutError: ' + TIMEOUT_MESSAGE)):
            errors.append('timeout_trace_not_proven')
    except (KeyError, TypeError, ValueError, AttributeError):
        errors.append('missing_or_invalid_timeout_proof')
    return errors


def _timeout_proof(path, arm):
    result = json.loads((path / 'result.json').read_text())
    config = json.loads((path / 'config.json').read_text())
    if not _config_subset(config, result['config']):
        raise ValueError('saved_config_differs_from_result')
    exception = result['exception_info']
    if (path / 'exception.txt').read_text() != exception['exception_traceback']:
        raise ValueError('exception_file_differs_from_result')
    proof = {key: result[key] for key in ('started_at', 'finished_at', 'agent_setup', 'agent_execution')}
    proof.update(agent_class=config['agent']['name'], **exception)
    errors = _timeout_errors(proof, arm)
    if errors:
        raise ValueError(';'.join(errors))
    return proof


def _allowed_timeout_error(error):
    # Corruption, identity/config mismatch and observed delegation violations
    # can never be converted into merely incomplete resource evidence.
    return (error in ('incomplete_session_usage', 'missing_root_session_id', 'actual_session_model_unobserved',
                      'missing_or_invalid:agent/trajectory.json')
            or any(error.startswith(prefix) for prefix in (
                'snapshot:unfinished_message:', 'snapshot:unfinished_tool:',
                'snapshot:missing_root_session:', 'snapshot:missing_session_messages:',
                'snapshot:missing_message_usage:', 'snapshot:missing_child_session:',
                'snapshot:missing_tool_message:', 'snapshot:missing_task_child_reference:',
                'snapshot:message_error:')))


def _limitations(installations, timeouts):
    lines = list(INSTALL_LIMITATIONS) if installations else []
    if timeouts:
        lines += [TIMEOUT_LIMITATION, LIMITATIONS[1], LIMITATIONS[2]]
    return list(dict.fromkeys(lines))


def _decision(audit, expected_slots, installations, timeouts):
    errors = []
    if expected_slots < 1 or expected_slots % len(auditor.ARMS):
        return ['invalid_expected_slots']
    if audit.get('expected_slots') != expected_slots or audit.get('observed_slots') != expected_slots or audit.get('issues'):
        errors.append('audit_structural_or_slot_issue')
    attempts = audit.get('attempts', [])
    expected = {(arm, rep) for arm in auditor.ARMS for rep in range(1, expected_slots // len(auditor.ARMS) + 1)}
    current = [row for row in attempts if not row.get('retry_history')]
    if len(current) != expected_slots or {(r.get('arm'), r.get('repetition')) for r in current} != expected:
        errors.append('missing_duplicate_or_unexpected_final_slots')
    if len({row.get('path') for row in attempts}) != len(attempts):
        errors.append('duplicate_attempt_paths')
    exceptions = installations + timeouts
    proofs = {row.get('path'): row for row in exceptions}
    if len(proofs) != len(exceptions):
        errors.append('duplicate_failure_proofs')
    if set(proofs) != {row.get('path') for row in attempts if row.get('complete') is not True}:
        errors.append('proofs_do_not_cover_exact_incomplete_attempts')
    timeout_paths = {row.get('path') for row in timeouts}
    for row in attempts:
        path = row.get('path', '<missing>')
        policy = 'allow' if row.get('arm') == 'baseline_allowed' else 'deny'
        if (not row.get('trial_id') or not row.get('trial_name') or (row.get('arm'), row.get('repetition')) not in expected
                or row.get('task_policy') != policy):
            errors.append(f'{path}:invalid_attempt_identity_or_policy')
        if row.get('arm') in auditor.DENIED_ARMS and (row.get('observed_task_tool_calls') or row.get('observed_child_sessions')):
            errors.append(f'{path}:observed_delegation_in_denied_arm')
        if not row.get('retry_history') and (row.get('failed') is not False or row.get('complete') is not True):
            errors.append(f'{path}:final_attempt_incomplete_or_failed')
        if row.get('complete') is True:
            if row.get('errors'):
                errors.append(f'{path}:contradictory_complete_attempt')
            continue
        proof = proofs.get(path, {})
        if (row.get('retry_history') is not True or row.get('failed') is not True
                or any(proof.get(key) != row.get(key) for key in ('path', 'trial_id', 'arm', 'repetition'))
                or any(row.get(key) is not None for key in ('task_tool_calls', 'child_sessions', 'usage'))):
            errors.append(f'{path}:not_an_archived_unknown_failure')
        if path in timeout_paths:
            if (row.get('exception_type') != 'AgentTimeoutError' or not row.get('errors')
                    or not all(_allowed_timeout_error(error) for error in row.get('errors', []))
                    or proof.get('missing_artifacts') != (['agent/trajectory.json']
                        if 'missing_or_invalid:agent/trajectory.json' in row.get('errors', []) else [])):
                errors.append(f'{path}:not_a_permitted_incomplete_timeout')
            errors.extend(f'{path}:{error}' for error in _timeout_errors(proof.get('proof', {}), row.get('arm', '')))
        else:
            if (row.get('exception_type') != 'NonZeroAgentExitCodeError'
                    or set(row.get('errors', [])) != {f'missing_or_invalid:{name}' for name in MISSING_ARTIFACTS}
                    or row.get('behavior') is not None or proof.get('missing_artifacts') != list(MISSING_ARTIFACTS)):
                errors.append(f'{path}:not_an_archived_pre_agent_install_failure')
            errors.extend(f'{path}:{error}' for error in _proof_errors(proof.get('proof', {}), row.get('arm', '')))
    if audit.get('incomplete_attempts') != len(exceptions):
        errors.append('incomplete_attempt_count_mismatch')
    for arm in auditor.ARMS:
        part = audit.get('per_arm', {}).get(arm, {})
        exempt = any(row.get('arm') == arm for row in exceptions)
        if part.get('task_policy') != ('allow' if arm == 'baseline_allowed' else 'deny'):
            errors.append(f'{arm}:permission_policy_not_verified')
        if part.get('complete') is not (not exempt):
            errors.append(f'{arm}:unexpected_per_arm_audit_status')
        if exempt and (part.get('task_tool_calls') is not None or part.get('child_sessions') is not None):
            errors.append(f'{arm}:unknown_attempt_totals_must_remain_unknown')
        if not exempt and arm in auditor.DENIED_ARMS and (part.get('task_tool_calls') != 0 or part.get('child_sessions') != 0):
            errors.append(f'{arm}:denial_not_fully_verified')
    if audit.get('complete') is not (not exceptions):
        errors.append('unknown_attempts_must_preserve_failed_audit')
    return sorted(set(errors))


def qualify(output: Path, *, expected_slots: int, audit: dict | None = None) -> dict:
    """Read-only source verification before collecting every existing final answer."""
    output = output.resolve()
    original = json.loads((output / 'no-subagent-audit.json').read_text())
    audit = original if audit is None else audit
    meta = json.loads((output / 'provenance.json').read_text())
    installations, timeouts, errors = [], [], []
    for row in audit.get('attempts', []):
        if row.get('complete') is True:
            continue
        path = (output / row.get('path', '')).resolve()
        if not path.is_relative_to(output) or path == output:
            errors.append('attempt_path_escapes_output')
            continue
        try:
            entry = {key: row[key] for key in ('path', 'trial_id', 'arm', 'repetition')}
            if row.get('exception_type') == 'AgentTimeoutError':
                entry['proof'] = _timeout_proof(path, row['arm'])
                trajectory = path / 'agent/trajectory.json'
                if trajectory.exists():
                    if not isinstance(json.loads(trajectory.read_text()), dict):
                        raise ValueError('invalid_timeout_trajectory')
                    entry['missing_artifacts'] = []
                else:
                    entry['missing_artifacts'] = ['agent/trajectory.json']
                timeouts.append(entry)
            else:
                entry.update(missing_artifacts=list(MISSING_ARTIFACTS), proof=_installation_proof(path, row['arm']))
                installations.append(entry)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f'{row["path"]}:failure_evidence_invalid:{exc}')
    installations.sort(key=lambda row: row['path'])
    timeouts.sort(key=lambda row: row['path'])
    errors += _decision(audit, expected_slots, installations, timeouts)
    if installations or timeouts:
        errors += _lifecycle_source_errors(output)
    if audit_fingerprint(original) != audit_fingerprint(audit):
        errors.append('supplied_audit_differs_from_saved_original')
    candidates = set()
    for name in ('config.json', 'result.json'):
        for path in output.rglob(name):
            try:
                value = json.loads(path.read_text())
                if isinstance(value, dict) and ('trial_name' in value or 'agent_info' in value):
                    candidates.add(path.parent)
            except (OSError, ValueError):
                if auditor._slot(path.parent, output)[0]:
                    candidates.add(path.parent)
    candidates.update(path.parent for path in output.rglob('agent') if path.is_dir())
    errors.extend('source:' + issue for issue in auditor._history(candidates, output))
    recorded, source_sha256 = set(), {}
    for entry in audit.get('attempts', []):
        path = (output / entry.get('path', '')).resolve()
        if not path.is_relative_to(output) or path == output:
            errors.append('attempt_path_escapes_output')
            continue
        recorded.add(path)
        if auditor._attempt(path, output) != entry:
            errors.append(f'{entry["path"]}:source_differs_from_audited_attempt')
        names = ('config.json', 'result.json', *MISSING_ARTIFACTS)
        if entry.get('complete') is not True:
            names += ('exception.txt', 'trial.log')
        for name in names:
            file = path / name
            source_sha256[str(file.relative_to(output))] = _sha(file) if file.is_file() else None
        if any(row['path'] == entry['path'] for row in installations):
            if any((path / name).exists() for name in MISSING_ARTIFACTS):
                errors.append(f'{entry["path"]}:expected_absence_not_corrupt_artifacts')
        if entry.get('complete') is not True:
            job = auditor._job(path, output)
            try:
                history = json.loads((job / '.retry-history/history.json').read_text())
                matches = [row for row in history['trials'][entry['trial_name']]
                           if row.get('archive') and (job / '.retry-history' / row['archive']).resolve() == path]
                if len(matches) != 1 or matches[0].get('trial_id') != entry['trial_id'] or matches[0].get('exception_type') != entry['exception_type']:
                    errors.append(f'{entry["path"]}:failed_archive_history_mismatch')
            except (OSError, ValueError, KeyError, TypeError):
                errors.append(f'{entry["path"]}:invalid_failed_archive_history')
    if recorded != candidates:
        errors.append('source_attempt_population_differs_from_audit')
    for path in output.rglob('history.json'):
        if path.parent.name == '.retry-history':
            source_sha256[str(path.relative_to(output))] = _sha(path)
    for name in ('provenance.json', 'no-subagent-audit.json'):
        source_sha256[name] = _sha(output / name)
    return dict(schema_version=3, kind=KIND, eligible=not errors, expected_slots=expected_slots,
                audit_sha256=audit_fingerprint(audit), full_attempt_audit_complete=audit.get('complete') is True,
                pre_agent_install_failures=installations, agent_timeout_failures=timeouts,
                lifecycle_contract=copy.deepcopy(LIFECYCLE_CONTRACT) if installations or timeouts else None,
                errors=sorted(set(errors)), limitations=_limitations(installations, timeouts),
                source_sha256=source_sha256, source_harness_commit=meta.get('harness_commit'))


def qualification_allows_incomplete_arm(qualification, arm):
    if not qualification or qualification.get('eligible') is not True:
        return False
    return any(row.get('arm') == arm for row in qualification.get('pre_agent_install_failures', [])
               + qualification.get('agent_timeout_failures', []))


def verify_report_qualification(report: dict) -> dict:
    """Validate embedded source-bound proof without changing the original audit."""
    meta = report['provenance']
    audit = meta.get('no_subagent_audit', {})
    q = meta.get('final_answer_qualification')
    if audit.get('complete') is True and q is None:
        return dict(eligible=True, full_attempt_audit_complete=True, pre_agent_install_failures=[], agent_timeout_failures=[], limitations=[])
    if not isinstance(q, dict) or q.get('schema_version') != 3:
        raise ValueError('Qwen requires a source-verified schema 3 final-answer qualification')
    installations, timeouts = q.get('pre_agent_install_failures', []), q.get('agent_timeout_failures', [])
    expected_slots = len(auditor.ARMS) * meta['repetitions']
    errors = _decision(audit, expected_slots, installations, timeouts)
    if (q.get('kind') != KIND or q.get('eligible') is not True or q.get('errors') != []
            or q.get('expected_slots') != expected_slots or q.get('audit_sha256') != audit_fingerprint(audit)
            or q.get('source_harness_commit') != meta.get('harness_commit')
            or q.get('full_attempt_audit_complete') is not (audit.get('complete') is True)
            or q.get('limitations') != _limitations(installations, timeouts)
            or q.get('lifecycle_contract') != (LIFECYCLE_CONTRACT if installations or timeouts else None)
            or ((installations or timeouts) and (not re.fullmatch(r'[0-9a-f]{40}', meta.get('harness_commit', ''))
                                                 or meta.get('installation_lifecycle_contract') != LIFECYCLE_CONTRACT))):
        errors.append('qualification_contract_or_audit_binding_mismatch')
    sources = q.get('source_sha256', {})
    missing_by_path = {row['path']: row.get('missing_artifacts', []) for row in installations + timeouts}
    for row in audit.get('attempts', []):
        for name in ('config.json', 'result.json', *MISSING_ARTIFACTS):
            key = f'{row["path"]}/{name}'
            absent = name in missing_by_path.get(row['path'], [])
            if key not in sources or (sources[key] is not None if absent else not re.fullmatch(r'[0-9a-f]{64}', sources[key] or '')):
                errors.append('qualification_source_hash_contract_mismatch')
    for row in installations + timeouts:
        names = [('exception.txt', 'exception_traceback')]
        if row in installations:
            names.append(('trial.log', 'trial_log'))
        for name, field in names:
            content = row.get('proof', {}).get(field)
            if not isinstance(content, str) or sources.get(f'{row["path"]}/{name}') != hashlib.sha256(content.encode()).hexdigest():
                errors.append('failure_proof_source_hash_mismatch')
    for name in ('provenance.json', 'no-subagent-audit.json'):
        if not re.fullmatch(r'[0-9a-f]{64}', sources.get(name) or ''):
            errors.append('missing_original_provenance_or_audit_hash')
    if not any(key.endswith('/.retry-history/history.json') for key in sources):
        errors.append('missing_retry_history_hashes')
    if errors:
        raise ValueError('Invalid final-answer qualification: ' + '; '.join(sorted(set(errors))))
    return q
