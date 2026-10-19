#!/usr/bin/env python3
"""Retain a completed scheduled confirmation run; never dispatch or rerun it."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

REPO = 'Cuiyus/zvec-grep'
PROTOCOL = 'main-opencode-q001-confirmation-v1'
WORKFLOW = '.github/workflows/opencode-qa-confirmation-v1.yml'
REPEATS = ('c01', 'c02')


def api(path):
    return subprocess.check_output(['gh', 'api', 'repos/' + REPO + '/' + path],
                                   stderr=subprocess.PIPE, timeout=300)


def write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, indent=2) + '\n')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def paginated(path, key):
    rows, pages, total = [], [], None
    for page_number in range(1, 1001):
        page = json.loads(api(path + '?per_page=100&page=' + str(page_number)))
        pages.append(page)
        if total is None:
            total = page['total_count']
        require(page['total_count'] == total, 'metadata pagination changed during collection')
        rows.extend(page[key])
        if len(rows) >= total:
            break
        require(page[key], 'metadata pagination ended prematurely')
    require(len(rows) == total and len({row['id'] for row in rows}) == total,
            'metadata pagination incomplete or duplicated')
    return {'total_count': total, key: rows, 'pages': pages}


def inventory(root):
    rows = []
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'symlink in retained evidence')
        if path.is_file():
            data = path.read_bytes()
            rows.append({'path': path.relative_to(root).as_posix(),
                         'bytes': len(data), 'sha256': sha(data)})
    return rows


def extract_verified(data, raw):
    """Reject unsafe/ambiguous paths before creating any extracted file."""
    raw.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries, seen = [], set()
        for info in archive.infolist():
            rel = PurePosixPath(info.filename)
            normal = rel.as_posix()
            expected = normal + '/' if info.is_dir() else normal
            mode = (info.external_attr >> 16) & 0o170000
            require(normal not in ('', '.') and not rel.is_absolute()
                    and '..' not in rel.parts and '\\' not in info.filename
                    and '\x00' not in info.orig_filename and info.filename == expected
                    and normal not in seen, 'unsafe or duplicated artifact path')
            require(mode in (0, 0o040000, 0o100000), 'artifact contains a symlink or special file')
            seen.add(normal)
            entries.append((info, rel))
        rows = []
        for info, rel in entries:
            if info.is_dir():
                continue
            body = archive.read(info)
            destination = raw.joinpath(*rel.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(body)
            require(destination.read_bytes() == body, 'retained artifact bytes changed')
            rows.append({'path': rel.as_posix(), 'bytes': len(body), 'sha256': sha(body)})
    rows.sort(key=lambda row: row['path'])
    require(inventory(raw) == rows, 'extracted evidence inventory differs from archive')
    return rows


def payload_identity(raw, repeat):
    """Check identity records if present, while retaining failures before setup."""
    records = []
    for path in sorted(raw.rglob('*.json')):
        if path.name not in ('confirmation-v1.json', 'execution.json', 'collection.json'):
            continue
        value = json.loads(path.read_text())
        require(value.get('protocol_id') == PROTOCOL, 'retained payload protocol mismatch')
        if path.name == 'confirmation-v1.json':
            require(value.get('repeats') == list(REPEATS), 'retained plan repeat schedule mismatch')
        else:
            require(value.get('repeat') == repeat, 'retained payload repeat mismatch')
        records.append({'path': path.relative_to(raw).as_posix(), 'sha256': sha(path.read_bytes())})
    return {'status': 'checked' if records else 'no_identity_records_retained', 'records': records}


def collect_repeat(root, artifacts, run_id, head, repeat):
    destination = root / repeat
    destination.mkdir()
    expected_name = PROTOCOL + '-' + str(run_id) + '-' + repeat
    status = {'protocol_id': PROTOCOL, 'repeat': repeat, 'run_id': run_id,
              'head': head, 'expected_artifact_name': expected_name, 'retained': False}
    try:
        matches = [a for a in artifacts['artifacts'] if a.get('name') == expected_name]
        require(len(matches) == 1, 'expected exactly one artifact; found ' + str(len(matches)))
        artifact = matches[0]
        write(destination / 'artifact-metadata.json', artifact)
        status['artifact_id'] = artifact['id']
        require(artifact.get('expired') is False, 'required retained artifact expired')
        require(artifact.get('workflow_run', {}).get('id') == run_id
                and artifact['workflow_run'].get('head_sha') == head,
                'artifact run/head identity mismatch')
        data = api('actions/artifacts/' + str(artifact['id']) + '/zip')
        with (destination / 'artifact.zip').open('xb') as stream:
            stream.write(data)
        zip_sha = sha(data)
        status.update(artifact_sha256=zip_sha, zip_bytes=len(data))
        require(artifact.get('digest') == 'sha256:' + zip_sha, 'artifact ZIP digest mismatch')
        rows = extract_verified(data, destination / 'raw')
        identity = payload_identity(destination / 'raw', repeat)
        report = {**status, 'retained': True, 'payload_identity': identity, 'files': rows}
        write(destination / 'artifact-byte-verification.json', report)
        status.update(retained=True, file_count=len(rows), payload_identity=identity)
    except Exception as exc:
        status.update(unavailability={'type': type(exc).__name__, 'reason': str(exc)},
                      incomplete_evidence_retained=True)
        if (destination / 'raw').exists():
            write(destination / 'partial-byte-inventory.json', {'files': inventory(destination / 'raw')})
    write(destination / 'retention-status.json', status)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    require(args.run_id > 0 and re.fullmatch('[0-9a-f]{40}', args.head) is not None,
            'supply an explicit positive run ID and full lowercase head SHA')
    root = args.output.expanduser().absolute()
    require(not root.exists() and not root.is_symlink(), 'existing evidence must not be overwritten')
    root.mkdir(parents=True)
    result = {'schema_version': 1, 'protocol_id': PROTOCOL, 'repository': REPO,
              'run_id': args.run_id, 'head': args.head, 'complete': False,
              'global_errors': [], 'repeats': []}
    try:
        metadata = json.loads(api('actions/runs/' + str(args.run_id)))
        write(root / 'run-metadata.json', metadata)
        require(metadata.get('id') == args.run_id and metadata.get('status') == 'completed',
                'requested run must be completed; do not redispatch')
        require(metadata.get('head_sha') == args.head and metadata.get('run_attempt') == 1
                and metadata.get('event') == 'workflow_dispatch'
                and metadata.get('repository', {}).get('full_name') == REPO
                and metadata.get('path', '').split('@', 1)[0] == WORKFLOW,
                'run repository/head/attempt/event/workflow identity mismatch')
        result['conclusion'] = metadata.get('conclusion')
    except Exception as exc:
        result['global_errors'].append({'stage': 'run_identity', 'type': type(exc).__name__, 'reason': str(exc)})
        write(root / 'collection-status.json', result)
        print(json.dumps(result))
        return 1
    for stage in ('jobs', 'logs', 'artifacts'):
        try:
            if stage == 'jobs':
                write(root / 'job-metadata.json', paginated('actions/runs/' + str(args.run_id) + '/jobs', 'jobs'))
            elif stage == 'logs':
                logs = subprocess.check_output(['gh', 'run', 'view', str(args.run_id), '--repo', REPO, '--log'],
                                               stderr=subprocess.PIPE, timeout=300)
                with (root / 'job-log.txt').open('xb') as stream:
                    stream.write(logs)
            else:
                artifacts = paginated('actions/runs/' + str(args.run_id) + '/artifacts', 'artifacts')
                write(root / 'artifacts-metadata.json', artifacts)
        except Exception as exc:
            result['global_errors'].append({'stage': stage, 'type': type(exc).__name__, 'reason': str(exc)})
    if not (root / 'artifacts-metadata.json').is_file():
        # Metadata retrieval failure is retained for each repeat; no fake counts,
        # token totals or quality scores substitute for missing observations.
        for repeat in REPEATS:
            destination = root / repeat; destination.mkdir()
            status = {'protocol_id': PROTOCOL, 'repeat': repeat, 'run_id': args.run_id,
                      'head': args.head, 'retained': False,
                      'unavailability': {'reason': 'artifact listing unavailable'}}
            write(destination / 'retention-status.json', status)
            result['repeats'].append(status)
    else:
        for repeat in REPEATS:
            result['repeats'].append(collect_repeat(root, artifacts, args.run_id, args.head, repeat))
    result['complete'] = not result['global_errors'] and all(row['retained'] for row in result['repeats'])
    write(root / 'collection-status.json', result)
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
