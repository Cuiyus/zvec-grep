"""Prepare frozen source/index and run the small paired pilot on a CI host."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
IMAGE = 'zg-claude-qa-pilot:20261019'
EMBEDDING = 'qwen/qwen3.7-text-embedding'
SECRETS = ('ANTHROPIC_API_KEY', 'ZVEC_GREP_API_KEY')


def clean(text):
    for key in SECRETS:
        value = os.environ.get(key)
        if value:
            text = text.replace(value, '[REDACTED]')
    return text


def run(command, *, output=None, timeout=1200):
    result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    log = clean(result.stdout + result.stderr)
    if output:
        output.write_text(log)
    print(log[-3000:], flush=True)
    if result.returncode:
        raise RuntimeError(f'command {command[0]} failed: {result.returncode}')
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', choices=['reflex-6', 'zvec-q001'], required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=1)
    args = parser.parse_args()
    work, reports = args.work.resolve(), args.reports.resolve()
    work.mkdir(parents=True, exist_ok=False)
    reports.mkdir(parents=True, exist_ok=True)
    if args.task == 'reflex-6':
        repository = 'https://github.com/reflex-dev/reflex.git'
        revision = 'fe0f946dc0c240c6c1e513318c21db407e191c78'
        prompt = ROOT / 'benchmarks/swe-qa-bench/datasets/reflex-6/instruction.md'
    else:
        repository = 'https://github.com/alibaba/zvec.git'
        revision = 'ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d'
        prompt = ROOT / 'benchmarks/zvec-issue-qa/tasks/q001/instruction.md'
    repo, source = work / 'git', work / 'source'
    run(['git', 'init', '-q', str(repo)])
    run(['git', '-C', str(repo), 'fetch', '-q', '--depth=1', repository, revision])
    actual = run(['git', '-C', str(repo), 'rev-parse', 'FETCH_HEAD']).strip()
    assert actual == revision
    if args.task == 'zvec-q001':
        run(['python3', str(ROOT / 'benchmarks/zvec-issue-qa/prepare_corpus.py'),
             '--repo', str(repo), '--output', str(source),
             '--manifest', str(reports / 'corpus-validation.json')])
    else:
        archive = work / 'source.tar'
        run(['git', '-C', str(repo), 'archive', '--format=tar', '--output', str(archive), revision])
        source.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(source, filter='data')
    # The source is read-only in every solver. This empty mountpoint holds a
    # private index overlay in treatment and an empty overlay in baseline.
    (source / '.zvec-grep').mkdir(exist_ok=True)
    index, home = work / 'prepared-index', work / 'prepared-home'
    index.mkdir(); home.mkdir()
    base = ['docker', 'run', '--rm', '--init', '--user', f'{os.getuid()}:{os.getgid()}',
            '--mount', f'type=bind,src={source},dst=/workspace,readonly',
            '--mount', f'type=bind,src={index},dst=/workspace/.zvec-grep',
            '--mount', f'type=bind,src={home},dst=/root',
            '--workdir', '/workspace', '--env', 'ZVEC_GREP_API_KEY',
            '--env', 'ZVEC_GREP_ENDPOINT', '--env', 'HOME=/root', IMAGE]
    try:
        versions = run(base + ['bash', '-lc',
            '/opt/qa/node_modules/.bin/claude --version; /opt/qa/node_modules/.bin/zg --version; node --version'])
        (reports / 'versions.txt').write_text(versions)
        start = time.monotonic()
        run(base + ['zg', 'auth', 'grant', '/workspace', '--capability', 'embedding',
                    '--scope', 'workspace', '--embedding', EMBEDDING],
            output=reports / 'authorization.log')
        run(base + ['zg', 'index', '/workspace', '--mode', 'direct',
                    '--embedding', EMBEDDING, '--allow-remote'],
            output=reports / 'index.log')
        metadata = {'task': args.task, 'repository': repository, 'revision': revision,
                    'embedding_model': EMBEDDING,
                    'embedding_endpoint': os.environ['ZVEC_GREP_ENDPOINT'],
                    'index_setup_seconds': time.monotonic() - start,
                    'index_file_count': sum(p.is_file() for p in index.rglob('*')),
                    'runtime_lock_sha256': hashlib.sha256((HERE / 'runtime/package-lock.json').read_bytes()).hexdigest(),
                    'repeats': args.repeats}
        (reports / 'setup.json').write_text(json.dumps(metadata, indent=2) + '\n')
        run(['python3', str(HERE / 'run.py'), '--source', str(source), '--prompt', str(prompt),
             '--output', str(work / 'trials'), '--image', IMAGE, '--model', 'qwen3.8-max',
             '--index-seed', str(index), '--home-seed', str(home / '.zvec-grep'),
             '--mcp-args-json', '["server","--stdio","--mcp-toolset","agent"]',
             '--repeats', str(args.repeats), '--max-turns', '30', '--timeout', '900',
             '--max-output-tokens', '8192', '--effort', 'high'],
            output=reports / 'runner.log', timeout=2000 * args.repeats)
    finally:
        trials = work / 'trials'
        if trials.exists():
            for path in trials.iterdir():
                if path.is_file():
                    shutil.copyfile(path, reports / path.name)
                elif path.name.startswith('r'):
                    target = reports / path.name
                    target.mkdir(exist_ok=True)
                    for name in ['stream.jsonl', 'stderr.txt', 'answer.txt', 'metrics.json', 'command.json']:
                        if (path / name).is_file():
                            shutil.copyfile(path / name, target / name)
        # Home, credentials, signing key, and index contents are never artifacts.
        for path in reports.rglob('*'):
            if path.is_file():
                data = path.read_text()
                sanitized = clean(data)
                if sanitized != data:
                    path.write_text(sanitized)


if __name__ == '__main__':
    main()
