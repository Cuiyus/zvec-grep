"""Read-only task binding for the three unchanged current tasks.

The caller must bind recorded_prepared_root (the selected task's prepared root
on the execution host) and expected_profiles prospectively in its own sidecar.
Neither value is inferred from the evidence being checked. A passing result
must still be combined with the unchanged native collector and scope audit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

import repeat_inputs_v1 as preparation

pilot = preparation.pilot
PROFILES = {'baseline', 'zvec-grep'}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _relative(value):
    return (isinstance(value, str) and bool(value) and not PurePosixPath(value).is_absolute()
            and '..' not in PurePosixPath(value).parts and str(PurePosixPath(value)) == value)


def _absolute(value):
    return (isinstance(value, str) and PurePosixPath(value).is_absolute()
            and '..' not in PurePosixPath(value).parts and str(PurePosixPath(value)) == value)


def _options(command, flag):
    values = []
    if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
        return values
    for index, token in enumerate(command):
        if token == flag:
            values.append(command[index + 1] if index + 1 < len(command) else None)
        elif token.startswith(flag + '='):
            values.append(token[len(flag) + 1:])
    return values


def _source_matches(observed, expected, profile):
    """Same allowed generated paths as frozen qa_agents.compare_inventory."""
    if not isinstance(observed, list):
        return False
    actual = {}
    for row in observed:
        if (not isinstance(row, dict) or not _relative(row.get('path'))
                or row['path'] in actual or row.get('kind') != 'file'
                or type(row.get('bytes')) is not int or row['bytes'] < 0):
            return False
        actual[row['path']] = row
    wanted = {row['path']: row for row in expected}
    for name, row in wanted.items():
        if name not in actual or any(actual[name].get(k) != row[k] for k in ('bytes', 'sha256')):
            return False
    return all(profile == 'zvec-grep' and (name.startswith('.zvec-grep/') or name == '.zg-bench-index.ignore')
               for name in actual.keys() - wanted.keys())


def verify(prepared_group: Path, task_id: str, output: Path, *, reference_prepared: Path,
           recorded_prepared_root: str, expected_profiles, repo_root: Path = preparation.REPO):
    """Return explicit failures for missing/partial trials; do not rewrite inputs."""
    checks, rows = [], []
    report = {'schema_version': 1, 'task_id': task_id, 'task_identity_valid': False,
              'recorded_prepared_root': recorded_prepared_root,
              'expected_profiles': list(expected_profiles), 'checks': checks, 'errors': [],
              'profiles': rows, 'execution_authorized': False,
              'scope': 'Host task identity only; native collection and independent source-scope audit are also required.'}

    def check(code, passed):
        checks.append({'code': code, 'passed': bool(passed)})
        if not passed:
            report['errors'].append(code)
        return bool(passed)

    def load(relative):
        try:
            value = pilot.load(pilot.safe_file(output, relative))
            if not isinstance(value, dict):
                raise ValueError('object_required')
            check('read:' + relative, True)
            return value
        except (OSError, ValueError, TypeError):
            check('read:' + relative, False)
            return {}

    if not check('explicit_task_and_order', task_id in preparation.TASK_IDS
                 and len(expected_profiles) == 2 and set(expected_profiles) == PROFILES):
        return report
    if not check('explicit_execution_prepared_root', _absolute(recorded_prepared_root)):
        return report
    try:
        metadata, source, selected_root = preparation.selected_inputs(
            prepared_group, reference_prepared, task_id, repo_root)
        report.update(prepared_group_sha256=pilot.digest(prepared_group / 'group.json'),
                      prepared_metadata_sha256=pilot.digest(selected_root / 'prepared.json'),
                      question_sha256=metadata['question']['sha256'],
                      source_inventory_sha256=metadata['source_inventory_sha256'],
                      native_task_hashes=metadata['native_task_hashes'])
        check('prepared_group_valid', True)
    except (OSError, ValueError, KeyError, TypeError):
        check('prepared_group_valid', False)
        return report
    if not check('evidence_directory_regular', output.is_dir() and not output.is_symlink()
                 and not any(p.is_symlink() for p in output.rglob('*'))):
        return report
    execution = load('execution.json')
    collection = load('collection/collection.json')
    slots, collected = execution.get('slots', []), collection.get('profiles', [])
    check('execution_collection_provenance', bool(execution.get('protocol_id'))
          and all(collection.get(k) == execution.get(k) and isinstance(execution.get(k), str)
                  for k in ('protocol_id', 'plan_sha256', 'offline_proof_sha256'))
          and execution.get('dry_run') is False)
    check('execution_collection_profile_order', isinstance(slots, list) and isinstance(collected, list)
                 and all(isinstance(x, dict) for x in slots + collected)
                 and [s.get('profile') for s in slots] == list(expected_profiles)
                 and [r.get('profile') for r in collected] == list(expected_profiles))
    slots = slots if isinstance(slots, list) else []
    collected = collected if isinstance(collected, list) else []

    def selected(items, profile):
        matching = [x for x in items if isinstance(x, dict) and x.get('profile') == profile]
        return matching[0] if len(matching) == 1 else {}

    remote = PurePosixPath(recorded_prepared_root)
    native_name = 'zvec-' + task_id
    task_path, manifest_path = str(remote / 'tasks' / native_name), str(remote / 'source-manifest.json')
    mapped_trials = []
    for profile in expected_profiles:
        slot, result_row = selected(slots, profile), selected(collected, profile)
        prefix = profile + ':'
        row = {'profile': profile, 'task_id': task_id, 'trial_directory': result_row.get('trial_directory'),
               'slot_status': slot.get('status'), 'answer_sha256': None, 'task_identity_valid': False}
        rows.append(row); start = len(report['errors'])
        command = slot.get('command')
        agent = 'native_claude.agents:' + ('QABaseline' if profile == 'baseline' else 'QAZvec')
        for flag, expected in [('--include-task-name', native_name), ('--path', str(remote / 'tasks')),
                               ('--agent', agent), ('--n-attempts', '1'), ('--max-retries', '0')]:
            check(prefix + 'command:' + flag, _options(command, flag) == [expected])
        kwargs = _options(command, '--agent-kwarg')
        for key, expected in [('qa_source_manifest', manifest_path), ('qa_profile', profile)]:
            check(prefix + 'command:' + key, [x for x in kwargs if isinstance(x, str) and x.startswith(key + '=')]
                  == [key + '=' + expected])
        saved_slot = load(profile + '-slot.json')
        check(prefix + 'slot_record', all(saved_slot.get(k) == slot.get(k) for k in ('profile', 'command', 'status', 'returncode')))
        check(prefix + 'completed_slot', slot.get('status') == 'exited' and slot.get('returncode') == 0
              and result_row.get('slot_status') == slot.get('status'))
        relative = result_row.get('trial_directory')
        if not check(prefix + 'retained_trial_path', _relative(relative) and len(PurePosixPath(relative).parts) == 3
                     and PurePosixPath(relative).parts[0] == 'runs' and (output / relative).is_dir()):
            continue
        mapped_trials.append(relative)
        trial = PurePosixPath(relative)
        job_names, job_dirs = _options(command, '--job-name'), _options(command, '--jobs-dir')
        job_valid = len(job_names) == len(job_dirs) == 1 and job_names[0] == trial.parts[1] and _absolute(job_dirs[0])
        check(prefix + 'command_trial_location', job_valid)
        expected_trials_dir = str(PurePosixPath(job_dirs[0]) / job_names[0]) if job_valid else None
        config, lock, result, audit = [load(relative + '/' + name) for name in
                                      ('config.json', 'lock.json', 'result.json', 'qa-source-audit.json')]
        for label, item in [('config', config), ('lock', lock), ('result_config', result.get('config', {}))]:
            try:
                check(prefix + label + ':task_path', item['task']['path'] == task_path and item['task']['source'] == 'tasks')
                check(prefix + label + ':agent_profile', item['agent']['name'] == agent
                      and item['agent']['kwargs']['qa_profile'] == profile
                      and item['agent']['kwargs']['qa_source_manifest'] == manifest_path)
                if label != 'lock':
                    check(prefix + label + ':trial', item['trial_name'] == trial.name
                          and item['trials_dir'] == expected_trials_dir)
            except (KeyError, TypeError):
                check(prefix + label + ':required_fields', False)
        locked_task = _mapping(lock.get('task'))
        check(prefix + 'lock_task_identity', locked_task.get('name') == native_name
              and locked_task.get('type') == 'local'
              and locked_task.get('digest') == metadata['native_task_hashes']['lock_task_digest'])
        check(prefix + 'result_task_identity', result.get('task_name') == native_name
              and result.get('trial_name') == trial.name and _mapping(result.get('task_id')).get('path') == task_path
              and result.get('task_checksum') == metadata['native_task_hashes']['result_task_checksum'])
        check(prefix + 'source_audit_profile', audit.get('profile') == profile and audit.get('engine') == 'native-claude')
        for phase in ('before', 'after'):
            observed = _mapping(_mapping(audit.get(phase)).get('observed')).get('files')
            check(prefix + 'source_files:' + phase, _source_matches(observed, source, profile))
        try:
            terminals = []
            for line in pilot.safe_file(output, relative + '/agent/claude-code.txt').read_text().splitlines():
                if line.strip():
                    event = json.loads(line)
                    if event.get('type') == 'result':
                        terminals.append(event)
            terminal = terminals[0] if len(terminals) == 1 else {}
            answer = terminal.get('result')
            answer_hash = hashlib.sha256(answer.encode()).hexdigest() if isinstance(answer, str) and answer else None
            check(prefix + 'native_terminal_answer', bool(answer_hash) and terminal.get('subtype') == 'success'
                  and terminal.get('is_error') is False)
            saved_result = load('collection/' + profile + '-root-result.json')
            check(prefix + 'collected_terminal_matches', saved_result == terminal and bool(terminal))
            expected_answer = profile + '-answer.txt'
            check(prefix + 'answer_identity', result_row.get('answer_present') is True
                  and result_row.get('answer_file') == expected_answer and bool(answer_hash)
                  and result_row.get('answer_sha256') == answer_hash
                  and pilot.digest(pilot.safe_file(output, 'collection/' + expected_answer)) == answer_hash)
            row['answer_sha256'] = answer_hash
        except (OSError, ValueError, TypeError, AttributeError):
            check(prefix + 'answer_evidence_readable', False)
        row['task_identity_valid'] = len(report['errors']) == start
    observed_trials = sorted(str(p.relative_to(output)) for p in (output / 'runs').glob('*/*') if p.is_dir())
    check('all_actual_trials_accounted_for', len(mapped_trials) == 2 and len(set(mapped_trials)) == 2
          and sorted(mapped_trials) == observed_trials)
    report['task_identity_valid'] = not report['errors']
    return report
