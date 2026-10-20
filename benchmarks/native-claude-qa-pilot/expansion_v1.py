"""Prepare the two frozen reserve tasks; no solver or paid execution entrypoint.

These artifacts need a future separately frozen phase. They are intentionally
not accepted by the existing q001 live.validate_plan.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import warnings

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / 'opencode-qa-pilot'))
import pilot

TASK_IDS = ('q002', 'q003')
DATASET = 'benchmarks/zvec-issue-qa/dataset-expansion-v1.json'
ORIGINAL_PLAN = 'benchmarks/opencode-qa-pilot/experiment.json'
EXPORTER = 'benchmarks/zvec-issue-qa/prepare_corpus.py'
PINS = {
    DATASET: 'e874a1d6509967f76f739ac99f70910c5a5a0a6c590514aac8de7cb4e2bf0e91',
    ORIGINAL_PLAN: 'ce13c13785117c49c515a79cfbba13ad70eaf7113dbcb3d6e91b29ec3a8dcc43',
    EXPORTER: 'eed5b21c622d6f2c3805ba925fcd9bc3611c33c0292b58b400aec1ef4816e51e',
}
TEMPLATES = {
    'Dockerfile': 'tasks/zvec-q001/environment/Dockerfile',
    'test.sh': 'tasks/zvec-q001/tests/test.sh',
    'task.toml': 'tasks/zvec-q001/task.toml',
    'index.ignore': 'index.ignore',
    'suite.yaml': 'suite.yaml',
}
TEMPLATE_SHA256 = {
    'Dockerfile': 'dc6cdb60cb9ceca9129a364c6a4168ac08a69f9587adaec5604046d111c7f107',
    'test.sh': '7ed723ea9d32b65d2069146a40cdf36ad92b465e39c66bc4c9d45deb4297d3f8',
    'task.toml': '9e7e9ab1b6b6ee9ffd6e57f533e51f6bf9610f0c031403619bf38bf2358c1a3e',
    'index.ignore': '9e216b4ed9b3d6d3a16f846c563d872300c5d15d9bbb483a5997b661da2c0be2',
    'suite.yaml': '7763aec38da60d360465b4c14967bfba91fb62db50fa566f5d83e89349cd8784',
}


def _outside(path, *protected):
    target = path.resolve()
    for root in protected:
        root = root.resolve()
        if target == root or target in root.parents or root in target.parents:
            raise ValueError('prepared_output_must_be_independent')


def _inputs(reference_prepared, repo_root):
    for relative, expected in PINS.items():
        if pilot.digest(pilot.safe_file(repo_root, relative)) != expected:
            raise ValueError('frozen_input_changed:' + relative)
    pilot.validate_prepared(reference_prepared, repo_root / ORIGINAL_PLAN)
    templates = {name: pilot.safe_file(reference_prepared, path).read_bytes()
                 for name, path in TEMPLATES.items()}
    for name, content in templates.items():
        if hashlib.sha256(content).hexdigest() != TEMPLATE_SHA256[name]:
            raise ValueError('reference_template_changed:' + name)
    if templates['task.toml'].count(b'task_id = "zvec-q001"') != 1:
        raise ValueError('reference_task_identity_missing')
    dataset = pilot.load(repo_root / DATASET)
    if [task['id'] for task in dataset['tasks']] != list(TASK_IDS):
        raise ValueError('both_frozen_reserves_required')
    for task in dataset['tasks']:
        for field in ('instruction', 'evaluator'):
            path = pilot.safe_file((repo_root / DATASET).parent, task[field])
            if pilot.digest(path) != task[field + '_sha256']:
                raise ValueError('frozen_task_asset_changed:' + task['id'] + ':' + field)
    reference = {
        'original_plan': {'path': ORIGINAL_PLAN, 'sha256': PINS[ORIGINAL_PLAN]},
        'prepared_metadata_sha256': pilot.digest(reference_prepared / 'prepared.json'),
        'templates': {name: {'path': path, 'sha256': hashlib.sha256(templates[name]).hexdigest()}
                      for name, path in TEMPLATES.items()},
    }
    return dataset['tasks'], templates, reference


def _suite(task_id):
    return ('name: native-claude-' + task_id + '-prepared-v1\npath: tasks\n'
            'index_ignore_file: index.ignore\ntiers:\n  smoke:\n    tasks: [zvec-' + task_id + ']\n').encode()


def _task_config(templates, task_id):
    return templates['task.toml'].replace(b'task_id = "zvec-q001"',
                                        ('task_id = "zvec-' + task_id + '"').encode())


def task_hashes(task_dir):
    """Use Harbor's two distinct identities rather than reimplementing them."""
    if importlib.metadata.version('harbor') != '0.18.0':
        raise ValueError('harbor_version_mismatch')
    from harbor.publisher.packager import Packager
    from harbor.models.task.task import Task
    digest, files = Packager.compute_content_hash(task_dir)
    task = Task(task_dir=task_dir)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        checksum = task.checksum
    return {'native_task_name': task.name, 'lock_task_digest': 'sha256:' + digest,
            'result_task_checksum': checksum, 'lock_packaged_file_count': len(files)}


def _metadata(root, task, templates, reference, repo_root):
    task_id = task['id']
    task_dir = root / 'tasks' / ('zvec-' + task_id)
    source = task_dir / 'environment/source'
    manifest = pilot.load(root / 'source-manifest.json')
    inventory = pilot.file_inventory(source)
    inventory_sha = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if (len(inventory) != task['source_file_count'] or inventory_sha != task['source_inventory_sha256']
            or manifest.get('files') != inventory or manifest.get('source_commit') != task['source_commit']
            or manifest.get('source_repository') != task['source_repository']
            or manifest.get('source_file_count') != len(inventory) or manifest.get('inventory_sha256') != inventory_sha
            or manifest.get('git_history_present') is not False or manifest.get('known_leakage_markers_found') is not False):
        raise ValueError('task_source_identity_mismatch:' + task_id)
    expected = {
        'suite.yaml': _suite(task_id), 'index.ignore': templates['index.ignore'],
        'tasks/zvec-' + task_id + '/task.toml': _task_config(templates, task_id),
        'tasks/zvec-' + task_id + '/environment/Dockerfile': templates['Dockerfile'],
        'tasks/zvec-' + task_id + '/tests/test.sh': templates['test.sh'],
        'tasks/zvec-' + task_id + '/instruction.md':
            ((repo_root / DATASET).parent / task['instruction']).read_bytes(),
    }
    for relative, content in expected.items():
        if pilot.safe_file(root, relative).read_bytes() != content:
            raise ValueError('task_support_or_question_mismatch:' + task_id + ':' + relative)
    files = [row for row in pilot.file_inventory(root) if row['path'] != 'prepared.json']
    allowed = set(expected) | {'source-manifest.json'} | {
        'tasks/zvec-' + task_id + '/environment/source/' + row['path'] for row in inventory}
    if {row['path'] for row in files} != allowed:
        raise ValueError('unexpected_prepared_file_or_gold:' + task_id)
    hashes = task_hashes(task_dir)
    if hashes['native_task_name'] != 'zvec-' + task_id:
        raise ValueError('native_task_name_mismatch')
    return {
        'schema_version': 1, 'artifact_type': 'native-reserve-task-preparation-v1',
        'task_id': task_id, 'scheduled_task_ids': list(TASK_IDS),
        'dataset': {'path': DATASET, 'sha256': PINS[DATASET]},
        'exporter': {'path': EXPORTER, 'sha256': PINS[EXPORTER]},
        'question': {'path': task['instruction'], 'sha256': task['instruction_sha256']},
        'rubric': {'path': task['evaluator'], 'sha256': task['evaluator_sha256'], 'host_only': True},
        'source_repository': task['source_repository'], 'source_commit': task['source_commit'],
        'source_file_count': len(inventory), 'source_inventory_sha256': inventory_sha,
        'source_manifest_sha256': pilot.digest(root / 'source-manifest.json'),
        'reference': reference, 'native_task_hashes': hashes, 'files': files,
        'solver_executed': False, 'paid_execution_authorized': False,
    }


def _group(root, tasks):
    return {'schema_version': 1, 'artifact_type': 'native-reserve-group-preparation-v1',
            'dataset': {'path': DATASET, 'sha256': PINS[DATASET]},
            'task_ids': list(TASK_IDS), 'solver_executed': False, 'paid_execution_authorized': False,
            'tasks': [{'task_id': task['id'], 'prepared_metadata_sha256': pilot.digest(root / task['id'] / 'prepared.json')}
                      for task in tasks],
            'files': [row for row in pilot.file_inventory(root) if row['path'] != 'group.json']}


def validate(prepared_group: Path, reference_prepared: Path, repo_root: Path = REPO):
    _outside(prepared_group, reference_prepared, repo_root)
    tasks, templates, reference = _inputs(reference_prepared, repo_root)
    for task in tasks:
        root = prepared_group / task['id']
        if pilot.load(root / 'prepared.json') != _metadata(root, task, templates, reference, repo_root):
            raise ValueError('prepared_metadata_mismatch:' + task['id'])
    if {p.name for p in prepared_group.iterdir()} != {*TASK_IDS, 'group.json'}:
        raise ValueError('exact_two_task_group_required')
    group = _group(prepared_group, tasks)
    if pilot.load(prepared_group / 'group.json') != group:
        raise ValueError('group_metadata_mismatch')
    return group


def prepare(reference_prepared: Path, zvec_repo: Path, output: Path, repo_root: Path = REPO):
    _outside(output, reference_prepared, repo_root, zvec_repo)
    if output.exists() or output.is_symlink():
        raise FileExistsError('fresh_prepared_group_required')
    tasks, templates, reference = _inputs(reference_prepared, repo_root)
    output.mkdir(parents=True, exist_ok=False)
    for task in tasks:
        task_id = task['id']; root = output / task_id
        task_dir = root / 'tasks' / ('zvec-' + task_id)
        (task_dir / 'environment').mkdir(parents=True)
        subprocess.run([sys.executable, str(repo_root / EXPORTER), '--repo', str(zvec_repo),
                        '--output', str(task_dir / 'environment/source'),
                        '--manifest', str(root / 'source-manifest.json'), '--dataset', str(repo_root / DATASET),
                        '--task', task_id], check=True, capture_output=True, text=True)
        shutil.copyfile((repo_root / DATASET).parent / task['instruction'], task_dir / 'instruction.md')
        (task_dir / 'environment/Dockerfile').write_bytes(templates['Dockerfile'])
        (task_dir / 'tests').mkdir()
        (task_dir / 'tests/test.sh').write_bytes(templates['test.sh'])
        (task_dir / 'task.toml').write_bytes(_task_config(templates, task_id))
        (root / 'index.ignore').write_bytes(templates['index.ignore'])
        (root / 'suite.yaml').write_bytes(_suite(task_id))
        pilot.write(root / 'prepared.json', _metadata(root, task, templates, reference, repo_root))
    pilot.write(output / 'group.json', _group(output, tasks))
    return validate(output, reference_prepared, repo_root)
