"""Prepare reviewed q005 with the unchanged native task templates; no paid calls.

The private adapter reuses the validated reserve preparation format. It changes
only the declared dataset/task references; q001-q003 preparations stay frozen.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TASK_IDS = ('q005',)
DATASET = 'benchmarks/zvec-issue-qa/dataset-runtime-v1.json'
BASE_PREPARER = 'benchmarks/native-claude-qa-pilot/expansion_v1.py'
QUESTION = 'benchmarks/zvec-issue-qa/tasks/q005/instruction.md'
RUBRIC = 'benchmarks/zvec-issue-qa/evaluator/q005.json'
REVIEW = 'benchmarks/zvec-issue-qa/evaluator/provenance/q005/canonical-asset-review.json'
PINS = {
    BASE_PREPARER: '5e2738ae616b62730c1ac25c6bfd168172da29eaf34c1a3a3257994a24a24f6a',
    DATASET: '407acf55cab575da9b72fac569601e674b0a9ea678a71e74d6024bd295c8146e',
    QUESTION: '02018cd483b233297065b7114b483d58ecbfce106155f3e2fa54e2c579bf968c',
    RUBRIC: 'e7c5abcd68d2a34cff975cd6956f64af2a9cbb962cc722bc34caea612bebfee7',
    REVIEW: 'a181e15034dc1768dbe668031b182ba2ce99d5d9199f28ffa4044aae8f74a035',
}


def validate_assets(repo_root=REPO):
    for relative, expected in PINS.items():
        path = repo_root / relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('runtime_asset_changed:' + relative)
    dataset = json.loads((repo_root / DATASET).read_text())
    review = json.loads((repo_root / REVIEW).read_text())
    if [task['id'] for task in dataset['tasks']] != list(TASK_IDS):
        raise ValueError('runtime_requires_q005_only')
    if review['remaining_blocking_findings']:
        raise ValueError('runtime_asset_review_incomplete')
    expected, seen = {DATASET, QUESTION, RUBRIC}, set()
    for reviewed in review['reviewed_assets']:
        matches = [key for key in expected if str(reviewed['path']).endswith('/' + key)]
        if (len(matches) != 1 or matches[0] in seen
                or PINS[matches[0]] != reviewed['sha256']
                or (repo_root / matches[0]).stat().st_size != reviewed['bytes']):
            raise ValueError('runtime_reviewed_identity_mismatch')
        seen.add(matches[0])
    if seen != expected:
        raise ValueError('runtime_reviewed_assets_incomplete')
    return dataset


def implementation(repo_root=REPO):
    validate_assets(repo_root)
    spec = importlib.util.spec_from_file_location('_native_q005_preparation', repo_root / BASE_PREPARER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TASK_IDS = TASK_IDS
    module.PINS = {key: value for key, value in module.PINS.items() if key != module.DATASET}
    module.DATASET = DATASET
    module.PINS[DATASET] = PINS[DATASET]
    return module


def prepare(reference_prepared, zvec_repo, output, repo_root=REPO):
    return implementation(repo_root).prepare(reference_prepared, zvec_repo, output, repo_root)


def validate(prepared_group, reference_prepared, repo_root=REPO):
    return implementation(repo_root).validate(prepared_group, reference_prepared, repo_root)


def task_hashes(task_dir):
    return implementation().task_hashes(task_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'validate'])
    parser.add_argument('--reference-prepared', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--zvec-repo', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        if args.zvec_repo is None:
            parser.error('--zvec-repo is required to prepare')
        result = prepare(args.reference_prepared, args.zvec_repo, args.output)
    else:
        result = validate(args.output, args.reference_prepared)
    print(json.dumps({'task_ids': result['task_ids'],
                      'prepared_metadata_sha256': result['tasks'],
                      'solver_executed': False, 'paid_execution_authorized': False}))


if __name__ == '__main__':
    main()
