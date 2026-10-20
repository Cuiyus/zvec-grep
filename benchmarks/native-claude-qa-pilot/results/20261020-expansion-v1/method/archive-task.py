"""Retain completed expansion task evidence without modifying source records."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path

BASE = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
WORK = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
RESULT = WORK / 'benchmarks/native-claude-qa-pilot/results/20261020-expansion-v1'


def describe(path):
    data = path.read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def write_json(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', choices=['q003'], required=True)
    task = parser.parse_args().task
    ci = BASE / 'native-expansion-v1-ci' / task
    audit = BASE / 'native-expansion-v1-audit'
    scope = audit / (task + '-scope')
    receipt = json.loads((ci / 'artifact-byte-verification.json').read_text())
    assert receipt['complete'] and receipt['exact_extracted_inventory']
    assert receipt['run_id'] == 39353782766 and receipt['task'] == task
    assert receipt['head_sha'] == 'bbc15485ca317427f6b19cab4dc449c50d3ef731'
    destination = RESULT / task
    assert not destination.exists(), 'Never overwrite an existing task archive'
    sources = []
    omitted = []
    for src in sorted(ci.rglob('*')):
        if src.is_file():
            rel = Path('ci') / src.relative_to(ci)
            if src.relative_to(ci).as_posix() in ['artifact.zip', 'raw/zg-install.tgz']:
                omitted.append({'path': str(rel), 'original_path': str(src), **describe(src),
                    'reason': 'Original locally retained and ZIP/package byte verification archived; duplicate large binary omitted from Git.'})
            else:
                sources.append((src, rel))
    for src in sorted(scope.rglob('*')):
        if src.is_file():
            sources.append((src, Path('scope') / src.relative_to(scope)))
    for name in ['execution-review', 'task-identity-replay', 'root-execution-verification', 'root-scope-verification']:
        src = audit / (task + '-' + name + '.json')
        assert src.is_file(), src
        sources.append((src, Path('audit') / src.name))
    for name in ['review-execution.py', 'preparation.json', 'root-static-method-review.json']:
        src = audit / name
        assert src.is_file(), src
        sources.append((src, Path('method') / name))
    extractor = BASE / 'native-claude-live-scope-audit-v1/extract.py'
    assert extractor.is_file()
    sources.append((extractor, Path('method/extract.py')))
    destination.mkdir()
    copies = []
    for src, rel in sources:
        data = src.read_bytes()
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)
        assert target.read_bytes() == data
        copies.append({'path': str(rel), 'original_path': str(src), **describe(src)})
    write_json(destination / 'retention.json', {
        'schema_version': 1, 'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'task_id': task, 'execution_head': receipt['head_sha'], 'run_id': receipt['run_id'],
        'copied_files': len(copies), 'copies': copies, 'large_files_retained_locally': omitted,
        'scope': 'Immutable task CI, execution and source/tool audit evidence only; quality review is retained separately at phase level.',
        'global_run_in_progress_at_task_collection': receipt['run_status_at_collection'] == 'in_progress',
    })
    files = [{'path': str(p.relative_to(destination)), **describe(p)}
             for p in sorted(destination.rglob('*')) if p.is_file()]
    write_json(destination / 'manifest.json', {'schema_version': 1, 'files': files})
    print(json.dumps({'task': task, 'copied_files': len(copies), 'manifest': describe(destination / 'manifest.json')}))


if __name__ == '__main__':
    main()
