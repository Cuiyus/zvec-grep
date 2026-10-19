#!/usr/bin/env python3
"""Archive downloaded public q004 evidence without judging its outcome.

Example (use exact observed metadata paths, after downloading the artifact):
  python3 /tmp/zg-architecture-package-evidence.py \
    --raw-dir /absolute/path/downloaded/qa-zvec-q004 \
    --output /absolute/worktree/benchmarks/claude-qa-pilot/results/20261019/architecture-v1 \
    --run-metadata /absolute/path/run-metadata.json \
    --artifacts /absolute/path/artifacts-metadata.json \
    --artifact-id 123456789 \
    --job-metadata /absolute/path/job-metadata.json \
    --job-log /absolute/path/job-log.txt \
    --provider-probe /absolute/path/provider-probe.json \
    --dispatch-request /absolute/path/dispatch-request.json \
    --dispatch-observation /absolute/path/dispatch-observation.json \
    --execution-audit /absolute/path/independent-execution-audit.json

Required inputs: raw-dir, output, run-metadata, artifacts. Artifact ID is needed
only when more than one qa-zvec-q004 artifact appears in the supplied listing.
Optional metadata inputs are exact files, never directories. This program makes
no network/subprocess calls and reads no environment credentials. It does not
redact or interpret answers, streams, quality, scans or an external audit. Inputs
must be the already-public CI reports/metadata, not private work/index/home seeds.

Missing allowlisted reports are retained as a partial inventory, not an error.
Unexpected paths, symlinks and special files fail closed before output creation.
The output must not exist. A packaging failure may leave that newly created
output for diagnosis; retry only with a different unused output path. Generated
execution.json describes provenance, not execution success or trial completeness.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

# Frozen public report inventory: 39 files in the previous remote single pair,
# plus this architecture phase's dataset-manifest.json = 40 possible files.
TOP_FILES = frozenset({
    'budget-probe.json', 'cli-probe.json', 'collection-secret-scan.json',
    'corpus-validation.json', 'dataset-manifest.json',
    'embedding-authorization-status.log', 'embedding-authorization.log',
    'embedding-index-identity.json', 'experiment-plan.json',
    'index-status.log', 'index.log', 'manifest.json', 'mcp-preflight-run.log',
    'mcp-preflight.json', 'mcp-preflight.requests.jsonl',
    'mcp-preflight.stderr.txt', 'mcp-preflight.stream.jsonl',
    'private-seed-secret-scan.json', 'prompt.txt', 'runner.log', 'secret-scan.json',
    'setup.json', 'source-manifest.json', 'summary.json', 'upstream-ci-config.json',
    'upstream-index.ignore', 'versions.txt', 'zg-provenance.json',
})
TRIAL_DIRS = frozenset({'r01-baseline', 'r01-zg'})
TRIAL_FILES = frozenset({'answer.txt', 'command.json', 'metrics.json', 'stderr.txt',
                         'stream.jsonl', 'tool-budget.json'})
ALL_RAW_FILES = TOP_FILES | frozenset(f'{trial}/{name}' for trial in TRIAL_DIRS for name in TRIAL_FILES)
assert len(ALL_RAW_FILES) == 40
CHUNK = 1024 * 1024


def regular_file(path: Path) -> Path:
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f'Input must be a regular non-symlink file: {path}')
    return path.resolve(strict=True)


def public_inventory(raw: Path) -> dict[str, Path]:
    """Inspect only the root and the two known trial dirs; never unrestricted rglob."""
    found = {}
    for path in sorted(raw.iterdir()):
        if path.is_symlink():
            raise ValueError(f'Unexpected symlink in public artifact: {path.name}')
        if path.name in TOP_FILES and stat.S_ISREG(path.lstat().st_mode):
            found[path.name] = regular_file(path)
        elif path.name in TRIAL_DIRS and path.is_dir():
            for trial_file in sorted(path.iterdir()):
                relative = f'{path.name}/{trial_file.name}'
                if relative not in ALL_RAW_FILES:
                    raise ValueError(f'Unexpected path in public artifact: {relative}')
                found[relative] = regular_file(trial_file)
        else:
            raise ValueError(f'Unexpected path in public artifact: {path.name}')
    if any(not path.is_relative_to(raw) for path in found.values()):
        raise ValueError('A public artifact path escaped the raw directory')
    return dict(sorted(found.items()))


def open_input(path: Path):
    # O_NOFOLLOW additionally rejects a replaced final-component symlink.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError(f'Input changed to a non-regular file: {path}')
    return os.fdopen(descriptor, 'rb')


def stream_hash(stream) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    while True:
        block = stream.read(CHUNK)
        if not block:
            return digest.hexdigest(), size
        digest.update(block)
        size += len(block)


def file_hash(path: Path) -> tuple[str, int]:
    with open_input(path) as stream:
        return stream_hash(stream)


def json_object(path: Path) -> dict:
    # Only explicit execution metadata is parsed. Answer/stream bytes are opaque.
    with open_input(path) as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f'Expected a metadata JSON object: {path}')
    return value


def observed_identity(run: dict, artifact_listing: dict, artifact_id: int | None) -> tuple[int, str, str, dict]:
    run_id = run.get('id', run.get('databaseId'))
    head = run.get('head_sha', run.get('headSha'))
    if type(run_id) is not int or run_id <= 0:
        raise ValueError('Run metadata must contain a positive id or databaseId')
    if not isinstance(head, str) or len(head) != 40 or any(c not in '0123456789abcdef' for c in head):
        raise ValueError('Run metadata must contain an exact lowercase head_sha or headSha')
    run_url = f'https://github.com/Cuiyus/zvec-grep/actions/runs/{run_id}'
    observed_url = run.get('html_url', run.get('url'))
    if observed_url and observed_url not in (run_url, f'https://api.github.com/repos/Cuiyus/zvec-grep/actions/runs/{run_id}'):
        raise ValueError('Run metadata URL differs from the expected fork run identity')
    artifacts = artifact_listing.get('artifacts', [artifact_listing])
    if not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts):
        raise ValueError('Artifact metadata must be one artifact or an artifacts list')
    candidates = [item for item in artifacts if item.get('name') == 'qa-zvec-q004'
                  and (artifact_id is None or item.get('id') == artifact_id)]
    if len(candidates) != 1:
        raise ValueError('Exactly one qa-zvec-q004 artifact must be selected; supply --artifact-id if needed')
    artifact = candidates[0]
    if type(artifact.get('id')) is not int or artifact['id'] <= 0:
        raise ValueError('Artifact metadata must contain a positive id')
    workflow_run = artifact.get('workflow_run') or {}
    if workflow_run.get('id', run_id) != run_id or workflow_run.get('head_sha', head) != head:
        raise ValueError('Artifact workflow run identity differs from supplied run metadata')
    expected_artifact_url = f'https://api.github.com/repos/Cuiyus/zvec-grep/actions/artifacts/{artifact["id"]}'
    if artifact.get('url', expected_artifact_url) != expected_artifact_url:
        raise ValueError('Artifact metadata URL is outside the expected fork identity')
    return run_id, head, run_url, artifact


def compress_raw(relative: str) -> bool:
    return (relative.endswith(('.log', '.jsonl')) or relative.endswith('/stderr.txt')
            or relative == 'mcp-preflight.stderr.txt')


def store_file(source: Path, destination: Path, *, compress: bool) -> dict:
    before = source.stat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    with open_input(source) as incoming, destination.open('xb') as disk:
        if compress:
            outgoing = gzip.GzipFile(filename='', mode='wb', fileobj=disk, mtime=0)
        else:
            outgoing = disk
        try:
            while True:
                block = incoming.read(CHUNK)
                if not block:
                    break
                digest.update(block); size += len(block); outgoing.write(block)
        finally:
            if compress:
                outgoing.close()
    after = source.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError(f'Input changed during archival: {source}')
    if compress:
        with gzip.open(destination, 'rb') as stream:
            restored = stream_hash(stream)
    else:
        restored = file_hash(destination)
    original = (digest.hexdigest(), size)
    if restored != original or file_hash(source) != original:
        raise ValueError(f'Byte verification failed for: {source}')
    archived_sha, archived_bytes = file_hash(destination)
    return {'source_sha256': original[0], 'source_bytes': size,
            'encoding': 'gzip' if compress else 'identity',
            'archive_sha256': archived_sha, 'archive_bytes': archived_bytes,
            'decoded_bytes_verified': True}


def write_json(path: Path, value: dict) -> None:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--raw-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-metadata', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--artifact-id', type=int)
    for flag in ('job-metadata', 'job-log', 'provider-probe', 'dispatch-request',
                 'dispatch-observation', 'execution-audit'):
        parser.add_argument('--' + flag, type=Path)
    args = parser.parse_args(argv)
    if args.raw_dir.is_symlink() or not args.raw_dir.is_dir():
        raise ValueError('--raw-dir must be an existing non-symlink directory')
    raw = args.raw_dir.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('--output must not exist; archival never overwrites an existing output')
    output_parent = output.parent.resolve(strict=True)
    output = output_parent / output.name
    if output.is_relative_to(raw) or raw.is_relative_to(output):
        raise ValueError('Output and raw input trees must not overlap')
    files = public_inventory(raw)
    external = {'run-metadata.json': regular_file(args.run_metadata),
                'artifacts-metadata.json': regular_file(args.artifacts)}
    for flag in ('job_metadata', 'job_log', 'provider_probe', 'dispatch_request',
                 'dispatch_observation', 'execution_audit'):
        source = getattr(args, flag)
        if source:
            name = flag.replace('_', '-') + ('.txt' if flag == 'job_log' else '.json')
            external[name] = regular_file(source)
    if any(path.is_relative_to(output) for path in external.values()):
        raise ValueError('External metadata input must not be inside output')
    # Keep identity parsing tied to the exact supplied metadata bytes, even if
    # another observer updates a metadata file while packaging is in progress.
    external_fingerprints = {name: file_hash(path) for name, path in external.items()}
    run = json_object(external['run-metadata.json'])
    listing = json_object(external['artifacts-metadata.json'])
    run_id, head, run_url, artifact = observed_identity(run, listing, args.artifact_id)
    job = json_object(external['job-metadata.json']) if 'job-metadata.json' in external else None
    if job is not None and (job.get('run_id', run_id) != run_id or job.get('head_sha', head) != head
                            or job.get('name') != 'paired-pilot (zvec-q004)'):
        raise ValueError('Job metadata differs from supplied run identity')
    # Parse remaining explicitly supplied JSON metadata before creating output;
    # never parse opaque public artifact answers/streams/metrics as findings.
    for name, source in external.items():
        if name.endswith('.json') and name not in ('run-metadata.json', 'artifacts-metadata.json', 'job-metadata.json'):
            json_object(source)
    script = regular_file(Path(__file__))
    external['packaging-script.py'] = script
    external_fingerprints['packaging-script.py'] = file_hash(script)
    output.mkdir(exist_ok=False)  # Atomic refusal if another process created it.
    records = []
    for relative, source in files.items():
        compress = compress_raw(relative)
        stored = relative + ('.gz' if compress else '')
        record = store_file(source, output / stored, compress=compress)
        records.append({'input_kind': 'raw_artifact', 'source_relative_path': relative,
                        'archive_relative_path': stored, **record})
    for name, source in sorted(external.items()):
        compress = name == 'job-log.txt'
        stored = name + ('.gz' if compress else '')
        record = store_file(source, output / stored, compress=compress)
        if (record['source_sha256'], record['source_bytes']) != external_fingerprints[name]:
            raise ValueError(f'Explicit metadata changed after identity validation: {source}')
        records.append({'input_kind': 'explicit_metadata_or_script', 'source_path': str(source),
                        'archive_relative_path': stored, **record})
    # Ensure no report was added/removed during packaging, and repeat source hash
    # verification after all copies (not just immediately after each one).
    if public_inventory(raw) != files:
        raise ValueError('Raw artifact inventory changed during packaging')
    for record in records:
        source = files[record['source_relative_path']] if record['input_kind'] == 'raw_artifact' else Path(record['source_path'])
        if file_hash(source) != (record['source_sha256'], record['source_bytes']):
            raise ValueError(f'Input changed before final verification: {source}')
    raw_records = [record for record in records if record['input_kind'] == 'raw_artifact']
    execution = {
        'schema_version': 1, 'run_id': run_id, 'run_url': run_url, 'head_sha': head,
        'job': job, 'artifact': artifact, 'raw_artifact_local_path': str(raw),
        'raw_artifact_files': {record['source_relative_path']: record['source_sha256'] for record in raw_records},
        'raw_artifact_file_bytes': {record['source_relative_path']: record['source_bytes'] for record in raw_records},
        'job_log_sha256': next((record['source_sha256'] for record in records
                               if record['archive_relative_path'] == 'job-log.txt.gz'), None),
        'provenance_inputs': ['run-metadata.json', 'artifacts-metadata.json'] + (['job-metadata.json'] if job else []),
        'artifact_digest_scope': 'Artifact digest is copied from supplied GitHub metadata; this script did not download or verify the original ZIP.',
        'execution_validation': {
            'determined_by_archiver': False, 'all_planned_trials_complete': None,
            'protocol_valid': None, 'source_grounded_success': None,
            'external_audit': 'execution-audit.json' if args.execution_audit else None,
            'external_audit_interpreted_by_archiver': False,
            'note': 'Copies preserve every observed allowlisted report, including failure or partial evidence. Byte fidelity is not a claim of successful or complete execution, quality, or secret-scan validity.',
        },
    }
    manifest = {
        'schema_version': 1, 'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'scope': 'Byte-preserving archive of explicitly supplied public q004 evidence only; no outcome inference.',
        'allowed_raw_paths': sorted(ALL_RAW_FILES), 'observed_raw_file_count': len(files),
        'missing_allowed_raw_paths': sorted(ALL_RAW_FILES - files.keys()),
        'unexpected_paths_policy': 'Reject before output creation; no skipped/hidden files, symlinks, special files or private runtime/index/home directory traversal.',
        'raw_input_inventory_archived': True, 'all_archived_input_decoded_bytes_verified': True,
        'verification_scope': 'Every observed allowlisted raw file and every explicit metadata input; absence of an expected report remains visible and does not imply successful execution.',
        'credential_policy': 'No environment credentials read and no redaction performed. These must be the already-public CI artifacts and metadata. This script cannot attest that their existing scans are correct.',
        'files': records,
    }
    write_json(output / 'execution.json', execution)
    write_json(output / 'archive-manifest.json', manifest)
    print(json.dumps({'output': str(output), 'observed_raw_files': len(files),
                      'archived_inputs': len(records), 'all_decoded_input_bytes_verified': True,
                      'execution_success_inferred': False}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(2)
