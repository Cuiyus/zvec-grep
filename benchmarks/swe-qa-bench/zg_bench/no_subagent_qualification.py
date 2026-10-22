"""Qualify final answers while preserving incomplete physical-attempt accounting.

Schema 1 retains the previously frozen allowed-arm recovery. Schema 2 requires
affirmative evidence of the frozen installer's first apt command failing before
agent execution, irrespective of arm. Neither schema invents missing usage.
"""
from __future__ import annotations

import hashlib
import copy
from datetime import datetime
from importlib.metadata import distribution, version
import json
from pathlib import Path
import re

from zg_bench import no_subagent_audit as auditor

KIND = 'glm52-subagent-ablation-final-answer-qualification'
MISSING_ARTIFACTS = (
    'agent/opencode-session-snapshot.json', 'agent/session-usage.json', 'agent/trajectory.json',
)
LIMITATIONS = [
    '最终答案统计完整，但全物理执行审计未通过：允许委派组的失败历史缺少会话计量。',
    '失败执行的 token、子会话数量与成本为未知，不能填零；全执行仅报告已知小计及未知次数。',
    '质量、采用率和主/子会话均值仅描述既有成功 final；不得据此断言含全部失败执行的总成本收益。',
]
INSTALL_COMMAND = 'apt-get update && apt-get install -y curl'
SOURCE_HEAD = '7a957f4f1e25fa45452d18c0e063bfe574d0fa61'
LIFECYCLE_CONTRACT = dict(
    rule='frozen-opencode-pre-agent-apt-curl-exit100', source_harness_commit=SOURCE_HEAD,
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
        if meta.get('harness_commit') != SOURCE_HEAD:
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
        if arm in ('node', 'rust'):
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


def _decision(audit, expected_slots):
    errors, unknown = [], []
    if expected_slots < 1 or expected_slots % len(auditor.ARMS):
        return ['invalid_expected_slots'], []
    if audit.get('expected_slots') != expected_slots or audit.get('observed_slots') != expected_slots:
        errors.append('audit_slot_count_mismatch')
    if audit.get('issues'):
        errors.append('audit_has_structural_issues')
    attempts = audit.get('attempts', [])
    expected = {(arm, rep) for arm in auditor.ARMS for rep in range(1, expected_slots // len(auditor.ARMS) + 1)}
    current = [row for row in attempts if not row.get('retry_history')]
    if len(current) != expected_slots or {(r.get('arm'), r.get('repetition')) for r in current} != expected:
        errors.append('missing_duplicate_or_unexpected_final_slots')
    if len({row.get('path') for row in attempts}) != len(attempts):
        errors.append('duplicate_attempt_paths')
    for row in attempts:
        path = row.get('path', '<missing>')
        if not row.get('trial_id') or not row.get('trial_name') or (row.get('arm'), row.get('repetition')) not in expected:
            errors.append(f'{path}:invalid_attempt_identity')
        if not row.get('retry_history') and (row.get('failed') is not False or row.get('complete') is not True):
            errors.append(f'{path}:final_attempt_incomplete_or_failed')
        if row.get('complete') is True:
            if row.get('errors'):
                errors.append(f'{path}:contradictory_complete_attempt')
            if row.get('arm') in auditor.DENIED_ARMS and (row.get('task_tool_calls') != 0 or row.get('child_sessions') != 0):
                errors.append(f'{path}:delegation_in_denied_arm')
            continue
        expected_errors = {f'missing_or_invalid:{name}' for name in MISSING_ARTIFACTS}
        allowed = (row.get('arm') == 'baseline_allowed' and row.get('retry_history') is True
                   and row.get('failed') is True and isinstance(row.get('exception_type'), str)
                   and bool(row['exception_type']) and set(row.get('errors', [])) == expected_errors
                   and all(row.get(key) is None for key in ('task_tool_calls', 'child_sessions', 'usage', 'behavior')))
        if not allowed:
            errors.append(f'{path}:failure_is_not_the_permitted_unknown_case')
        else:
            unknown.append(dict(path=path, trial_id=row['trial_id'], arm=row['arm'], repetition=row['repetition'],
                                missing_artifacts=list(MISSING_ARTIFACTS)))
    if audit.get('incomplete_attempts') != len(unknown):
        errors.append('incomplete_attempt_count_mismatch')
    for arm in auditor.DENIED_ARMS:
        part = audit.get('per_arm', {}).get(arm, {})
        if part.get('complete') is not True or part.get('task_policy') != 'deny' or part.get('task_tool_calls') != 0 or part.get('child_sessions') != 0:
            errors.append(f'{arm}:denial_not_fully_verified')
    allowed_part = audit.get('per_arm', {}).get('baseline_allowed', {})
    if allowed_part.get('task_policy') != 'allow':
        errors.append('allowed_permission_policy_not_verified')
    if unknown and audit.get('complete') is not False:
        errors.append('unknown_attempts_must_preserve_failed_audit')
    if not unknown and audit.get('complete') is not True:
        errors.append('failed_audit_without_permitted_unknown_attempts')
    return sorted(set(errors)), sorted(unknown, key=lambda r: r['path'])


def _decision_v2(audit, expected_slots, installations):
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
    proofs = {row.get('path'): row for row in installations}
    if len(proofs) != len(installations):
        errors.append('duplicate_installation_proofs')
    incomplete_paths = {row.get('path') for row in attempts if row.get('complete') is not True}
    if set(proofs) != incomplete_paths:
        errors.append('installation_proofs_do_not_cover_exact_incomplete_attempts')
    for row in attempts:
        path = row.get('path', '<missing>')
        policy = 'allow' if row.get('arm') == 'baseline_allowed' else 'deny'
        if (not row.get('trial_id') or not row.get('trial_name') or (row.get('arm'), row.get('repetition')) not in expected
                or row.get('task_policy') != policy):
            errors.append(f'{path}:invalid_attempt_identity_or_policy')
        if not row.get('retry_history') and (row.get('failed') is not False or row.get('complete') is not True):
            errors.append(f'{path}:final_attempt_incomplete_or_failed')
        if row.get('complete') is True:
            if row.get('errors'):
                errors.append(f'{path}:contradictory_complete_attempt')
            if row.get('arm') in auditor.DENIED_ARMS and (row.get('task_tool_calls') != 0 or row.get('child_sessions') != 0):
                errors.append(f'{path}:delegation_in_denied_arm')
            continue
        proof = proofs.get(path, {})
        if (row.get('retry_history') is not True or row.get('failed') is not True
                or row.get('exception_type') != 'NonZeroAgentExitCodeError'
                or set(row.get('errors', [])) != {f'missing_or_invalid:{name}' for name in MISSING_ARTIFACTS}
                or any(row.get(key) is not None for key in ('task_tool_calls', 'child_sessions', 'usage', 'behavior'))
                or any(proof.get(key) != row.get(key) for key in ('path', 'trial_id', 'arm', 'repetition'))
                or proof.get('missing_artifacts') != list(MISSING_ARTIFACTS)):
            errors.append(f'{path}:not_an_archived_pre_agent_install_failure')
        errors.extend(f'{path}:{message}' for message in _proof_errors(proof.get('proof', {}), row.get('arm', '')))
    if audit.get('incomplete_attempts') != len(installations):
        errors.append('incomplete_attempt_count_mismatch')
    for arm in auditor.ARMS:
        part = audit.get('per_arm', {}).get(arm, {})
        exempt = any(row.get('arm') == arm for row in installations)
        if part.get('task_policy') != ('allow' if arm == 'baseline_allowed' else 'deny'):
            errors.append(f'{arm}:permission_policy_not_verified')
        if part.get('complete') is not (not exempt):
            errors.append(f'{arm}:unexpected_per_arm_audit_status')
        if exempt:
            if part.get('task_tool_calls') is not None or part.get('child_sessions') is not None:
                errors.append(f'{arm}:unknown_attempt_totals_must_remain_unknown')
        elif arm in auditor.DENIED_ARMS and (part.get('task_tool_calls') != 0 or part.get('child_sessions') != 0):
            errors.append(f'{arm}:denial_not_fully_verified')
    if audit.get('complete') is not (not installations):
        errors.append('unknown_attempts_must_preserve_failed_audit')
    return sorted(set(errors))


def qualify(output: Path, *, expected_slots: int, audit: dict | None = None, schema_version: int = 2) -> dict:
    """Read-only source qualification. Caller saves the returned decision before judging."""
    output = output.resolve()
    original = json.loads((output / 'no-subagent-audit.json').read_text())
    if audit is None:
        audit = original
    if schema_version not in (1, 2):
        raise ValueError('Unknown final-answer qualification schema')
    installations, proof_errors = [], []
    if schema_version == 1:
        errors, unknown = _decision(audit, expected_slots)
    else:
        for row in audit.get('attempts', []):
            if row.get('complete') is True:
                continue
            path = (output / row.get('path', '')).resolve()
            if not path.is_relative_to(output) or path == output:
                proof_errors.append('attempt_path_escapes_output')
                continue
            try:
                proof = _installation_proof(path, row.get('arm', ''))
                installations.append({**{key: row[key] for key in ('path', 'trial_id', 'arm', 'repetition')},
                                      'missing_artifacts': list(MISSING_ARTIFACTS), 'proof': proof})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                proof_errors.append(f'{row["path"]}:installation_evidence_invalid:{exc}')
        installations.sort(key=lambda row: row['path'])
        unknown = installations
        errors = _decision_v2(audit, expected_slots, installations) + proof_errors
        if installations:
            errors += _lifecycle_source_errors(output)
    if audit_fingerprint(original) != audit_fingerprint(audit):
        errors.append('supplied_audit_differs_from_saved_original')
    source_sha256 = {}
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
    recorded = set()
    for entry in audit.get('attempts', []):
        path = (output / entry.get('path', '')).resolve()
        if not path.is_relative_to(output) or path == output:
            errors.append('attempt_path_escapes_output')
            continue
        recorded.add(path)
        computed = auditor._attempt(path, output)
        if computed != entry:
            errors.append(f'{entry["path"]}:source_differs_from_audited_attempt')
        names = ('config.json', 'result.json', *MISSING_ARTIFACTS)
        if schema_version == 2 and entry.get('complete') is not True:
            names += ('exception.txt', 'trial.log')
        for name in names:
            file = path / name
            relative = str(file.relative_to(output))
            source_sha256[relative] = hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None
        if any(row['path'] == entry['path'] for row in unknown):
            if any((path / name).exists() for name in MISSING_ARTIFACTS):
                errors.append(f'{entry["path"]}:expected_absence_not_corrupt_artifacts')
            # The history is authoritative for archive identity and failure status.
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
            source_sha256[str(path.relative_to(output))] = hashlib.sha256(path.read_bytes()).hexdigest()
    source_sha256['no-subagent-audit.json'] = hashlib.sha256((output / 'no-subagent-audit.json').read_bytes()).hexdigest()
    result = dict(schema_version=schema_version, kind=KIND, eligible=not errors, expected_slots=expected_slots,
                audit_sha256=audit_fingerprint(audit), full_attempt_audit_complete=audit.get('complete') is True,
                allowed_unknown_attempts=unknown, errors=sorted(set(errors)), limitations=LIMITATIONS if unknown else [],
                source_sha256=source_sha256)
    if schema_version == 2:
        result.pop('allowed_unknown_attempts')
        result.update(pre_agent_install_failures=installations,
                      lifecycle_contract=copy.deepcopy(LIFECYCLE_CONTRACT) if installations else None,
                      limitations=list(INSTALL_LIMITATIONS) if installations else [])
        if installations:
            path = output / 'provenance.json'
            source_sha256['provenance.json'] = _sha(path) if path.is_file() else None
    return result


def qualification_allows_incomplete_arm(qualification, arm):
    """Use only after verify_report_qualification; this never changes the audit."""
    if not qualification or qualification.get('eligible') is not True:
        return False
    rows = (qualification.get('pre_agent_install_failures', []) if qualification.get('schema_version') == 2
            else qualification.get('allowed_unknown_attempts', []))
    return any(row.get('arm') == arm for row in rows)


def _verify_v2(report, qualification):
    meta = report['provenance']
    audit = meta.get('no_subagent_audit', {})
    expected_slots = len(auditor.ARMS) * meta['repetitions']
    installations = qualification.get('pre_agent_install_failures', [])
    errors = _decision_v2(audit, expected_slots, installations)
    if (qualification.get('kind') != KIND or qualification.get('eligible') is not True or qualification.get('errors') != []
            or qualification.get('expected_slots') != expected_slots
            or qualification.get('audit_sha256') != audit_fingerprint(audit)
            or qualification.get('full_attempt_audit_complete') is not (audit.get('complete') is True)
            or qualification.get('limitations') != (INSTALL_LIMITATIONS if installations else [])
            or qualification.get('lifecycle_contract') != (LIFECYCLE_CONTRACT if installations else None)
            or (installations and meta.get('harness_commit') != SOURCE_HEAD)):
        errors.append('qualification_contract_or_audit_binding_mismatch')
    sources = qualification.get('source_sha256', {})
    incomplete = {row['path'] for row in installations}
    for row in audit.get('attempts', []):
        for name in ('config.json', 'result.json'):
            if not re.fullmatch(r'[0-9a-f]{64}', sources.get(f'{row["path"]}/{name}') or ''):
                errors.append('qualification_missing_source_hashes')
        for name in MISSING_ARTIFACTS:
            key = f'{row["path"]}/{name}'
            if key not in sources or (sources[key] is not None if row['path'] in incomplete
                                      else not re.fullmatch(r'[0-9a-f]{64}', sources[key] or '')):
                errors.append('qualification_artifact_hash_contract_mismatch')
    for row in installations:
        for name, field in (('exception.txt', 'exception_traceback'), ('trial.log', 'trial_log')):
            content = row.get('proof', {}).get(field)
            if not isinstance(content, str) or sources.get(f'{row["path"]}/{name}') != hashlib.sha256(content.encode()).hexdigest():
                errors.append('installation_proof_source_hash_mismatch')
    if installations and not re.fullmatch(r'[0-9a-f]{64}', sources.get('provenance.json') or ''):
        errors.append('missing_installation_source_provenance_hash')
    if not sources.get('no-subagent-audit.json') or not any(key.endswith('/.retry-history/history.json') for key in sources):
        errors.append('qualification_missing_original_audit_or_history_hashes')
    if errors:
        raise ValueError('Invalid final-answer qualification: ' + '; '.join(sorted(set(errors))))
    return qualification


def verify_report_qualification(report: dict) -> dict:
    """Verify the embedded decision and original-audit binding without changing either."""
    meta = report['provenance']
    audit = meta.get('no_subagent_audit', {})
    qualification = meta.get('final_answer_qualification')
    if audit.get('complete') is True and qualification is None:
        return dict(eligible=True, full_attempt_audit_complete=True, allowed_unknown_attempts=[], limitations=[])
    if isinstance(qualification, dict) and qualification.get('schema_version') == 2:
        return _verify_v2(report, qualification)
    expected_slots = len(auditor.ARMS) * meta['repetitions']
    errors, unknown = _decision(audit, expected_slots)
    if not isinstance(qualification, dict):
        raise ValueError('Incomplete attempt audit requires a source-verified final-answer qualification')
    if (qualification.get('schema_version') != 1 or qualification.get('kind') != KIND
            or qualification.get('eligible') is not True or qualification.get('errors') != []
            or qualification.get('expected_slots') != expected_slots
            or qualification.get('audit_sha256') != audit_fingerprint(audit)
            or qualification.get('full_attempt_audit_complete') is not (audit.get('complete') is True)
            or qualification.get('allowed_unknown_attempts') != unknown
            or qualification.get('limitations') != (LIMITATIONS if unknown else [])):
        errors.append('qualification_contract_or_audit_binding_mismatch')
    sources = qualification.get('source_sha256', {})
    for row in audit.get('attempts', []):
        for name in ('config.json', 'result.json'):
            digest = sources.get(f'{row["path"]}/{name}')
            if not isinstance(digest, str) or len(digest) != 64:
                errors.append('qualification_missing_source_hashes')
        for name in MISSING_ARTIFACTS:
            key = f'{row["path"]}/{name}'
            digest = sources.get(key)
            expected_missing = any(u['path'] == row['path'] for u in unknown)
            if key not in sources or (digest is not None if expected_missing else not isinstance(digest, str) or len(digest) != 64):
                errors.append('qualification_artifact_hash_contract_mismatch')
    if not sources.get('no-subagent-audit.json') or not any(key.endswith('/.retry-history/history.json') for key in sources):
        errors.append('qualification_missing_original_audit_or_history_hashes')
    if errors:
        raise ValueError('Invalid final-answer qualification: ' + '; '.join(sorted(set(errors))))
    return qualification
