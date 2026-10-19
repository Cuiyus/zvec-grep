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
UPSTREAM_COMMIT = 'a424e0ce06868dc9ad8f142f57de59b8f9b58699'
UPSTREAM_REPOSITORY = 'https://github.com/zvec-ai/zvec-grep.git'
UPSTREAM_CONFIG_PATH = 'benchmarks/swe-qa-bench/ci-config.json'
UPSTREAM_IGNORE_PATH = 'benchmarks/swe-qa-bench/zg_bench/swe_qa/data/index.ignore'
SECRETS = ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ZVEC_GREP_API_KEY', 'DASHSCOPE_API_KEY', 'QWEN_API_KEY')
TRIAL_ARTIFACT_FILES = ('stream.jsonl', 'stderr.txt', 'answer.txt', 'metrics.json', 'command.json', 'tool-budget.json')
TOP_ARTIFACT_FILES = ('manifest.json', 'source-manifest.json', 'prompt.txt', 'summary.json', 'secret-scan.json')


def clean(text):
    for key in SECRETS:
        value = os.environ.get(key)
        if value:
            text = text.replace(value, '[REDACTED]')
    return text


def run(command, *, output=None, timeout=1200, live=False):
    if live:
        if output is None:
            raise ValueError('live command output needs a log file')
        with output.open('w') as stream:
            proc = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, text=True)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
                raise
        raw = output.read_text(errors='replace')
        returncode = proc.returncode
    else:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
        raw, returncode = result.stdout + result.stderr, result.returncode
    log = clean(raw)
    if output:
        output.write_text(log)
    print(log[-3000:], flush=True)
    if returncode:
        raise RuntimeError(f'command {command[0]} failed: {returncode}')
    return raw if live else result.stdout


def collect_evidence(work, reports):
    """Collect allowlisted complete or partial evidence; never runtime/index seeds."""
    reports.mkdir(parents=True, exist_ok=True)
    trials = work / 'trials'
    if trials.exists():
        for name in TOP_ARTIFACT_FILES:
            if (trials / name).is_file():
                shutil.copyfile(trials / name, reports / name)
        for path in trials.iterdir():
            if path.is_dir() and path.name.startswith('r'):
                target = reports / path.name
                target.mkdir(exist_ok=True)
                for name in TRIAL_ARTIFACT_FILES:
                    if (path / name).is_file():
                        shutil.copyfile(path / name, target / name)
    redacted_files, scanned_files = [], 0
    for path in reports.rglob('*'):
        if path.is_file():
            data = path.read_text(errors='replace')
            sanitized = clean(data)
            scanned_files += 1
            if sanitized != data:
                path.write_text(sanitized)
                redacted_files.append(str(path.relative_to(reports)))
    remaining = []
    for path in reports.rglob('*'):
        if path.is_file():
            data = path.read_text(errors='replace')
            if clean(data) != data:
                remaining.append(str(path.relative_to(reports)))
    result = {'passed': not remaining, 'scanned_files': scanned_files,
              'redacted_files': redacted_files, 'remaining_matches': remaining,
              'partial_evidence_allowed': True}
    (reports / 'collection-secret-scan.json').write_text(json.dumps(result, indent=2) + '\n')
    if remaining:
        raise RuntimeError('collected evidence still contains known credential values')
    return result


def upstream_protocol(work, reports):
    checkout = work / 'upstream-protocol'
    run(['git', 'init', '-q', str(checkout)])
    run(['git', '-C', str(checkout), 'fetch', '-q', '--depth=1', UPSTREAM_REPOSITORY, UPSTREAM_COMMIT])
    actual = run(['git', '-C', str(checkout), 'rev-parse', 'FETCH_HEAD']).strip()
    if actual != UPSTREAM_COMMIT:
        raise RuntimeError('upstream benchmark commit mismatch')
    config_text = run(['git', '-C', str(checkout), 'show', f'{actual}:{UPSTREAM_CONFIG_PATH}'])
    ignore_text = run(['git', '-C', str(checkout), 'show', f'{actual}:{UPSTREAM_IGNORE_PATH}'])
    config = json.loads(config_text)
    embedding = config['embedding_models']['local']
    if embedding != 'local/potion-code-16m-v2':
        raise RuntimeError('pinned main local embedding configuration changed unexpectedly')
    (reports / 'upstream-ci-config.json').write_text(config_text)
    (reports / 'upstream-index.ignore').write_text(ignore_text)
    provenance = {
        'repository': UPSTREAM_REPOSITORY, 'commit': actual,
        'config_path': UPSTREAM_CONFIG_PATH,
        'config_sha256': hashlib.sha256(config_text.encode()).hexdigest(),
        'index_ignore_path': UPSTREAM_IGNORE_PATH,
        'index_ignore_sha256': hashlib.sha256(ignore_text.encode()).hexdigest(),
    }
    return embedding, ignore_text, provenance


def index_command(embedding):
    return ['zg', '--index', '--embedding', embedding, '--mode', 'direct',
            '--ignore-file', '.zg-bench-index.ignore']


def verify_runtime_provenance(value):
    if not isinstance(value, dict) or value.get('commit') != UPSTREAM_COMMIT:
        raise RuntimeError('runtime does not contain the pinned current-main Rust zg candidate')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', choices=['reflex-6', 'zvec-q001'])
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--workspace-root-guidance', action='store_true')
    parser.add_argument('--completion-budget', action='store_true')
    parser.add_argument('--completion-confirmation', action='store_true')
    parser.add_argument('--collect-only', action='store_true', help='Always-run safe collection, including cancelled partial trials')
    args = parser.parse_args()
    if args.collect_only:
        collect_evidence(args.work.resolve(), args.reports.resolve())
        return
    if not args.task:
        parser.error('--task is required unless --collect-only is used')
    expected_repeats = 2 if args.completion_confirmation else 1
    if args.repeats != expected_repeats:
        parser.error(f'this phase is frozen to {expected_repeats} pair(s) per task')
    if args.workspace_root_guidance and args.task != 'reflex-6':
        parser.error('root-v2 is frozen to reflex-6 only')
    if args.completion_budget and (args.task != 'zvec-q001' or args.workspace_root_guidance):
        parser.error('completion-v2 is frozen to zvec-q001 without workspace-root guidance')
    if args.completion_confirmation and (args.task != 'zvec-q001' or not args.completion_budget or args.workspace_root_guidance):
        parser.error('completion confirmation requires zvec-q001 with completion-budget and without workspace-root guidance')
    start_repeat = 2 if args.completion_confirmation else 1
    confirmation_plan = None
    if args.completion_confirmation:
        plan_path = HERE / 'experiment-completion-confirmation.json'
        confirmation_plan = {
            'path': str(plan_path.relative_to(ROOT)),
            'sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        }
    work, reports = args.work.resolve(), args.reports.resolve()
    work.mkdir(parents=True, exist_ok=False)
    reports.mkdir(parents=True, exist_ok=True)
    embedding, ignore_text, upstream = upstream_protocol(work, reports)
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
    original_source_file_count = sum(path.is_file() for path in source.rglob('*'))
    # Gold validation above concerns the original corpus. This exact main-branch
    # ignore file is a declared runtime auxiliary, itself excluded from indexing.
    (source / '.zg-bench-index.ignore').write_text(ignore_text)
    # The source is read-only in every solver. This empty mountpoint holds a
    # private index overlay in treatment and an empty overlay in baseline.
    (source / '.zvec-grep').mkdir(exist_ok=True)
    index, home = work / 'prepared-index', work / 'prepared-home'
    index.mkdir(); home.mkdir()
    base = ['docker', 'run', '--rm', '--init', '--user', f'{os.getuid()}:{os.getgid()}',
            '--mount', f'type=bind,src={source},dst=/workspace,readonly',
            '--mount', f'type=bind,src={index},dst=/workspace/.zvec-grep',
            '--mount', f'type=bind,src={home},dst=/root',
            '--workdir', '/workspace', '--env', 'HOME=/root', IMAGE]
    try:
        runtime_provenance = json.loads(run(base + ['cat', '/opt/qa/zg-provenance.json']))
        verify_runtime_provenance(runtime_provenance)
        (reports / 'zg-provenance.json').write_text(json.dumps(runtime_provenance, indent=2) + '\n')
        versions = run(base + ['claude', '--version']) + run(base + ['zg', '--version'])
        (reports / 'versions.txt').write_text(versions)
        start = time.monotonic()
        run(base + index_command(embedding),
            output=reports / 'index.log')
        run(base + ['zg', '--status', '--check-ready'], output=reports / 'index-status.log')
        metadata = {'task': args.task, 'repository': repository, 'revision': revision,
                    'phase': 'confirmation' if args.completion_confirmation else 'development',
                    'confirmation_plan': confirmation_plan,
                    'start_repeat': start_repeat, 'planned_trial_count': args.repeats * 2,
                    'planned_trials': [
                        {'repeat': repeat, 'profile': profile}
                        for repeat in range(start_repeat, start_repeat + args.repeats)
                        for profile in (('baseline', 'zg') if repeat % 2 else ('zg', 'baseline'))
                    ],
                    'protocol_version': 'completion-v2' if args.completion_budget else ('root-v2' if args.workspace_root_guidance else 'v1'),
                    'workspace_root_guidance': args.workspace_root_guidance,
                    'completion_budget': args.completion_budget,
                    'tool_budget': 24 if args.completion_budget else None,
                    'research_seconds': 600 if args.completion_budget else None,
                    'embedding_model': embedding, 'embedding_runtime': 'local',
                    'embedding_endpoint': None,
                    'upstream_benchmark': upstream, 'zg_runtime': runtime_provenance,
                    'index_setup_seconds': time.monotonic() - start,
                    'index_file_count': sum(p.is_file() for p in index.rglob('*')),
                    'original_source_file_count': original_source_file_count,
                    'runtime_source_file_count': original_source_file_count + 1,
                    'runtime_auxiliary_files': [{
                        'path': '.zg-bench-index.ignore', 'sha256': upstream['index_ignore_sha256'],
                        'purpose': 'main benchmark indexing exclusions; added after original gold validation',
                    }],
                    'model_cache': 'baked runtime-image cache; query-time network download unnecessary',
                    'repeats': args.repeats,
                    'protocol_differences_from_main': [
                        ('two confirmation pairs on the same case; no automatic infrastructure retries'
                         if args.completion_confirmation else 'one pair per case rather than main five trials and two infrastructure retries'),
                        'Claude Code high rather than main OpenCode provider-specific Qwen high/xhigh settings',
                        '8192 output tokens and 30 turns/900 seconds rather than main 32000 output tokens',
                        'Read/Grep/Glob-only native tools; fresh HOME with CLAUDE.md and auto-memory disabled',
                        'exact native current-main MCP schema/instructions remain exposed',
                        'exported source without git history; new isolated index at /workspace',
                        'explicit direct-mode index setup; startup included in measured agent wall',
                    ]}
        (reports / 'setup.json').write_text(json.dumps(metadata, indent=2) + '\n')
        home_seed = home / '.zvec-grep'
        home_seed.mkdir(exist_ok=True)
        probe_started = time.monotonic()
        run(['python3', str(HERE / 'probe_mcp.py'), '--source', str(source),
             '--index-seed', str(index), '--home-seed', str(home_seed), '--image', IMAGE,
             '--output', str(reports / 'mcp-preflight.json')],
            output=reports / 'mcp-preflight-run.log', timeout=450, live=True)
        metadata['mcp_preflight_seconds'] = time.monotonic() - probe_started
        metadata['mcp_preflight'] = 'mcp-preflight.json; private seed clones; excluded from QA metrics'
        (reports / 'setup.json').write_text(json.dumps(metadata, indent=2) + '\n')
        run(['python3', str(HERE / 'run.py'), '--source', str(source), '--prompt', str(prompt),
             '--output', str(work / 'trials'), '--image', IMAGE, '--model', 'qwen3.8-max',
             '--index-seed', str(index), '--home-seed', str(home_seed),
             '--embedding-model', embedding,
             '--mcp-args-json', '["--server","--stdio","--mcp-toolset","agent"]',
             '--repeats', str(args.repeats), '--max-turns', '30', '--timeout', '900',
             '--max-output-tokens', '8192', '--effort', 'high']
            + (['--workspace-root-guidance'] if args.workspace_root_guidance else [])
            + (['--tool-budget', '24', '--research-seconds', '600'] if args.completion_budget else [])
            + (['--start-repeat', '2'] if args.completion_confirmation else []),
            output=reports / 'runner.log', timeout=4500 if args.completion_confirmation else 2000, live=True)
    finally:
        collect_evidence(work, reports)


if __name__ == '__main__':
    main()
