"""Qualify complete final answers without certifying missing failed-attempt usage.

The sole relaxation is an archived, failed baseline_allowed attempt whose
snapshot, usage and trajectory are all absent. Its config/result/history must
remain verifiable. Original audit evidence and its false status are preserved.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

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


def qualify(output: Path, *, expected_slots: int, audit: dict | None = None) -> dict:
    """Read-only source qualification. Caller saves the returned decision before judging."""
    output = output.resolve()
    original = json.loads((output / 'no-subagent-audit.json').read_text())
    if audit is None:
        audit = original
    errors, unknown = _decision(audit, expected_slots)
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
        for name in ('config.json', 'result.json', *MISSING_ARTIFACTS):
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
    return dict(schema_version=1, kind=KIND, eligible=not errors, expected_slots=expected_slots,
                audit_sha256=audit_fingerprint(audit), full_attempt_audit_complete=audit.get('complete') is True,
                allowed_unknown_attempts=unknown, errors=sorted(set(errors)), limitations=LIMITATIONS if unknown else [],
                source_sha256=source_sha256)


def verify_report_qualification(report: dict) -> dict:
    """Verify the embedded decision and original-audit binding without changing either."""
    meta = report['provenance']
    audit = meta.get('no_subagent_audit', {})
    qualification = meta.get('final_answer_qualification')
    if audit.get('complete') is True and qualification is None:
        return dict(eligible=True, full_attempt_audit_complete=True, allowed_unknown_attempts=[], limitations=[])
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
