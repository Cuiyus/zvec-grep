"""Preserve two independent native profile records for joint native collection.

This composes retained records. It never executes a solver, changes a slot,
rewrites recorded paths, or manufactures a shared host or elapsed pair time.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil

PROFILES = ('baseline', 'zvec-grep')
HEADER_KEYS = ('schema_version', 'protocol_id', 'plan_sha256', 'candidate_sha256',
               'offline_proof_sha256', 'offline_inputs_sha256', 'dry_run', 'quality_evaluated')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing_or_unsafe_native_record')
    return json.loads(path.read_text(), parse_constant=lambda value: (
        _ for _ in ()).throw(ValueError('nonfinite_json_constant:' + value)))


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
        stream.write('\n')


def inventory(root):
    if root.is_symlink() or not root.is_dir() or any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('unsafe_worker_tree')
    return {p.relative_to(root).as_posix(): {'bytes': p.stat().st_size, 'sha256': digest(p)}
            for p in sorted(root.rglob('*')) if p.is_file()}


def assemble(workers: dict[str, Path], destination: Path, *, expected_header: dict):
    if set(workers) != set(PROFILES) or set(expected_header) != set(HEADER_KEYS):
        raise ValueError('exact_two_profile_header_required')
    if expected_header['dry_run'] is not False or expected_header['quality_evaluated'] is not False:
        raise ValueError('only_original_ungraded_live_records')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('assembly_output_exists')
    inventories = {profile: inventory(root) for profile, root in workers.items()}
    records = {profile: load(root / 'execution.json') for profile, root in workers.items()}
    for profile, record in records.items():
        if {k: record[k] for k in HEADER_KEYS} != expected_header:
            raise ValueError('worker_native_header_mismatch:' + profile)
        if len(record['slots']) != 1 or record['slots'][0].get('profile') != profile:
            raise ValueError('worker_must_have_exact_one_original_slot:' + profile)
    destination.mkdir(parents=True)
    (destination / 'runs').mkdir()
    copied = []
    for profile in PROFILES:
        root = workers[profile]
        for path in sorted(root.iterdir()):
            if path.is_file() and path.name.startswith(profile + '-'):
                shutil.copyfile(path, destination / path.name)
                copied.append({'profile': profile, 'path': path.name, **inventories[profile][path.name]})
        run = Path('runs') / ('q001-' + profile)
        if (root / run).is_dir():
            shutil.copytree(root / run, destination / run)
            copied.extend({'profile': profile, 'path': relative, **metadata}
                          for relative, metadata in inventories[profile].items()
                          if relative.startswith(run.as_posix() + '/'))
    combined = copy.deepcopy(expected_header)
    combined['slots'] = [copy.deepcopy(records[p]['slots'][0]) for p in PROFILES]
    combined['system'] = {'per_profile': {p: records[p].get('system') for p in PROFILES},
                          'assembly': 'Two retained independent profile records; no shared-host assertion.'}
    write(destination / 'execution.json', combined)
    for item in copied:
        target = destination / item['path']
        if target.stat().st_size != item['bytes'] or digest(target) != item['sha256']:
            raise ValueError('assembled_file_changed')
    for profile, root in workers.items():
        if inventory(root) != inventories[profile]:
            raise ValueError('worker_bytes_changed_during_assembly')
    if combined['slots'] != [records[p]['slots'][0] for p in PROFILES]:
        raise ValueError('original_slot_changed')
    receipt = {'schema_version': 1, 'mode': 'independent_profile_records',
               'native_header': expected_header, 'profiles': list(PROFILES),
               'worker_execution_sha256': {p: digest(workers[p] / 'execution.json') for p in PROFILES},
               'worker_file_counts': {p: len(inventories[p]) for p in PROFILES},
               'copied_files': copied, 'slots_preserved_exactly': True,
               'original_worker_files_unchanged': True,
               'combined_execution_sha256': digest(destination / 'execution.json'),
               'solver_calls': 0, 'shared_host_claimed': False, 'aggregate_pair_time_invented': False}
    write(destination / 'profile-assembly.json', receipt)
    return receipt
