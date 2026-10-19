"""Offline execution gate for the separately frozen q004 architecture pair."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import remote_embedding
from prepare_rust_runtime import COMMIT, PACKAGE_SHA

PLAN_NAME = 'experiment-architecture-v1.json'
DATASET_PATH = 'benchmarks/zvec-issue-qa/dataset-architecture-v1.json'
SOURCE_COMMIT = '1a573058b24e11cdb7e91f27d6638c5f1c0cdcaa'
INPUTS = {'task': 'zvec-q004', 'workspace_root_guidance': False,
          'completion_budget': True, 'completion_confirmation': False,
          'closed_issue_expansion': False, 'remote_embedding_pilot': False,
          'architecture_pilot': True}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_file(root: Path, item: dict) -> Path:
    """Check a frozen controller asset without allowing paths outside the checkout."""
    relative = Path(item['path'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('architecture asset path must stay inside the checkout')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('architecture asset is not a file inside the checkout')
    if digest(path) != item['sha256']:
        raise ValueError('architecture frozen asset hash mismatch: ' + item['path'])
    return path


def validate_plan(root: Path, here: Path) -> dict:
    """Validate the new phase before any provider/setup call; no network or inference."""
    root, here = root.resolve(), here.resolve()
    path = here / PLAN_NAME
    plan = json.loads(path.read_text())
    if plan.get('frozen') is not True:
        raise ValueError('architecture plan has not been frozen for execution')
    if (plan.get('experiment_id') != 'claude-qwen-rust-zvec-architecture-v1'
            or plan.get('phase') != 'architecture-development'
            or plan.get('protocol_version') != 'completion-v2'):
        raise ValueError('architecture plan identity differs from the declared phase')
    base_path = checked_file(root, plan['base_experiment'])
    if base_path != (here / remote_embedding.PLAN_NAME).resolve():
        raise ValueError('architecture phase must reference the frozen remote-embedding base')
    base = json.loads(base_path.read_text())
    for group in ('agent', 'completion_control', 'retrieval', 'preflight', 'accounting'):
        if plan.get(group) != base[group]:
            raise ValueError('architecture phase changed its inherited ' + group)
    retrieval = plan['retrieval']
    if (retrieval['source_commit'] != COMMIT or retrieval['native_package_sha256'] != PACKAGE_SHA
            or retrieval['embedding_model'] != remote_embedding.MODEL
            or retrieval['embedding_endpoint'] != remote_embedding.ENDPOINT):
        raise ValueError('architecture native/embedding runtime identity mismatch')
    if checked_file(root, plan['completion_hook']) != (here / 'tool_budget.py').resolve():
        raise ValueError('architecture phase must use the unchanged completion hook')
    if plan['completion_hook']['sha256'] != plan['completion_control']['hook_sha256']:
        raise ValueError('architecture completion hook identity mismatch')
    from run import common_instructions
    if (plan['common_instructions_sha256'] != base['common_instructions_sha256']
            or hashlib.sha256(common_instructions(False, 24, 600).encode()).hexdigest()
            != plan['common_instructions_sha256']):
        raise ValueError('architecture common instructions changed')
    dataset_path = checked_file(root, plan['dataset'])
    if dataset_path != (root / DATASET_PATH).resolve():
        raise ValueError('architecture phase requires its separate dataset')
    dataset = json.loads(dataset_path.read_text())
    tasks = dataset.get('tasks')
    if (not isinstance(tasks, list) or len(tasks) != 1 or tasks[0].get('id') != 'q004'
            or dataset.get('independent_task_count') != 1):
        raise ValueError('architecture dataset must contain exactly q004')
    selected = tasks[0]
    task = plan['task']
    if (task['id'] != 'zvec-q004' or task['repository'] != 'alibaba/zvec'
            or task['revision'] != SOURCE_COMMIT or selected['source_commit'] != SOURCE_COMMIT
            or selected['source_repository'] != 'https://github.com/alibaba/zvec.git'):
        raise ValueError('architecture task/source identity mismatch')
    for plan_name, dataset_name in (('question', 'instruction'), ('rubric', 'evaluator')):
        asset = checked_file(root, task[plan_name])
        relative = Path(selected[dataset_name])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('architecture dataset task path must stay inside its directory')
        dataset_asset = (dataset_path.parent / relative).resolve()
        if (not dataset_asset.is_relative_to(dataset_path.parent) or asset != dataset_asset
                or selected[dataset_name + '_sha256'] != task[plan_name]['sha256']):
            raise ValueError('architecture task asset differs between plan and dataset')
    rubric = json.loads((root / task['rubric']['path']).read_text())
    if rubric.get('task_id') != 'q004' or rubric.get('source_commit') != SOURCE_COMMIT:
        raise ValueError('architecture rubric task/source identity mismatch')
    run = plan['run_plan']
    if (run['inputs'] != INPUTS or run['repeats'] != 1 or run['start_repeat'] != 1
            or run['trial_count'] != 2 or run['order'] != ['baseline', 'zg']
            or run['automatic_repeats'] is not False or run['automatic_task_expansion'] is not False
            or run['bad_outcome_replacement'] is not False or run['job_timeout_minutes'] != 45
            or run['runner_timeout_seconds'] != 2000):
        raise ValueError('architecture phase requires exactly its single frozen pair')
    return {'path': str(path.relative_to(root)), 'sha256': digest(path),
            'dataset_path': str(dataset_path.relative_to(root)), 'dataset_sha256': digest(dataset_path)}
