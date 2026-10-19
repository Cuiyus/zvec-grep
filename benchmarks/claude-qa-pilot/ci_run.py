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

import remote_embedding
import architecture_phase

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
IMAGE = 'zg-claude-qa-pilot:20261019'
UPSTREAM_COMMIT = 'a424e0ce06868dc9ad8f142f57de59b8f9b58699'
UPSTREAM_REPOSITORY = 'https://github.com/zvec-ai/zvec-grep.git'
UPSTREAM_CONFIG_PATH = 'benchmarks/swe-qa-bench/ci-config.json'
UPSTREAM_IGNORE_PATH = 'benchmarks/swe-qa-bench/zg_bench/swe_qa/data/index.ignore'
SECRETS = ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ZVEC_GREP_API_KEY', 'DASHSCOPE_API_KEY', 'QWEN_API_KEY', 'QWEN_EMBEDDING_API_KEY')
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


def upstream_protocol(work, reports, *, remote=False, remote_plan_path=None):
    checkout = work / 'upstream-protocol'
    run(['git', 'init', '-q', str(checkout)])
    run(['git', '-C', str(checkout), 'fetch', '-q', '--depth=1', UPSTREAM_REPOSITORY, UPSTREAM_COMMIT])
    actual = run(['git', '-C', str(checkout), 'rev-parse', 'FETCH_HEAD']).strip()
    if actual != UPSTREAM_COMMIT:
        raise RuntimeError('upstream benchmark commit mismatch')
    config_text = run(['git', '-C', str(checkout), 'show', f'{actual}:{UPSTREAM_CONFIG_PATH}'])
    ignore_text = run(['git', '-C', str(checkout), 'show', f'{actual}:{UPSTREAM_IGNORE_PATH}'])
    config = json.loads(config_text)
    embedding = config['embedding_models']['remote' if remote else 'local']
    if embedding != (remote_embedding.MODEL if remote else 'local/potion-code-16m-v2'):
        raise RuntimeError('pinned main embedding configuration changed unexpectedly')
    (reports / 'upstream-ci-config.json').write_text(config_text)
    (reports / 'upstream-index.ignore').write_text(ignore_text)
    provenance = {
        'repository': UPSTREAM_REPOSITORY, 'commit': actual,
        'config_path': UPSTREAM_CONFIG_PATH,
        'config_sha256': hashlib.sha256(config_text.encode()).hexdigest(),
        'index_ignore_path': UPSTREAM_IGNORE_PATH,
        'index_ignore_sha256': hashlib.sha256(ignore_text.encode()).hexdigest(),
    }
    if remote:
        plan = json.loads((remote_plan_path or HERE / remote_embedding.PLAN_NAME).read_text())
        sources = []
        for entry in plan['retrieval']['configuration_sources']:
            if entry['commit'] != actual:
                raise ValueError('remote embedding configuration source commit mismatch')
            content = run(['git', '-C', str(checkout), 'show', f"{actual}:{entry['path']}"])
            if hashlib.sha256(content.encode()).hexdigest() != entry['sha256']:
                raise ValueError('remote embedding configuration source hash mismatch')
            sources.append(entry)
        provenance['remote_embedding_configuration_sources'] = sources
    return embedding, ignore_text, provenance


def index_command(embedding, endpoint=None):
    return ['zg', '--index', '--embedding', embedding, '--mode', 'direct',
            '--ignore-file', '.zg-bench-index.ignore'] + (['--endpoint', endpoint] if endpoint else [])


def verify_runtime_provenance(value):
    if not isinstance(value, dict) or value.get('commit') != UPSTREAM_COMMIT:
        raise RuntimeError('runtime does not contain the pinned current-main Rust zg candidate')


def validate_expansion_plan():
    """Shared pre-provider and pre-solver gate for this separately frozen batch."""
    plan_path = HERE / 'experiment-issue-expansion-v1.json'
    dataset_path = ROOT / 'benchmarks/zvec-issue-qa/dataset-expansion-v1.json'
    plan = json.loads(plan_path.read_text())
    if plan.get('frozen') is not True:
        raise ValueError('issue expansion plan has not been frozen for execution')
    metadata = {
        'path': str(plan_path.relative_to(ROOT)),
        'sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        'dataset_path': str(dataset_path.relative_to(ROOT)),
        'dataset_sha256': hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
    }
    if metadata['dataset_sha256'] != plan.get('dataset_sha256'):
        raise ValueError('issue expansion dataset differs from its frozen plan')
    return metadata


def validate_remote_embedding_plan():
    return remote_embedding.validate_plan(ROOT, HERE)


def validate_architecture_plan():
    return architecture_phase.validate_plan(ROOT, HERE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', choices=['reflex-6', 'zvec-q001', 'zvec-q002', 'zvec-q003', 'zvec-q004'])
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--workspace-root-guidance', action='store_true')
    parser.add_argument('--completion-budget', action='store_true')
    parser.add_argument('--completion-confirmation', action='store_true')
    parser.add_argument('--closed-issue-expansion', action='store_true')
    parser.add_argument('--remote-embedding-pilot', action='store_true')
    parser.add_argument('--architecture-pilot', action='store_true')
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
    if args.architecture_pilot:
        if (args.task != 'zvec-q004' or not args.completion_budget or args.workspace_root_guidance
                or args.completion_confirmation or args.closed_issue_expansion or args.remote_embedding_pilot):
            parser.error('architecture phase requires one q004 completion-budget pair with no other phase')
    elif args.task == 'zvec-q004':
        parser.error('q004 requires the separately frozen architecture phase')
    if args.remote_embedding_pilot and (args.task != 'zvec-q001' or not args.completion_budget
            or args.workspace_root_guidance or args.completion_confirmation or args.closed_issue_expansion):
        parser.error('remote embedding requires one q001 completion-budget pair with no other phase')
    if args.closed_issue_expansion:
        if (args.task not in ('zvec-q002', 'zvec-q003') or not args.completion_budget
                or args.workspace_root_guidance or args.completion_confirmation):
            parser.error('issue expansion requires q002/q003 with completion budget and no other phase')
    elif args.task in ('zvec-q002', 'zvec-q003'):
        parser.error('q002/q003 require the separately frozen issue expansion phase')
    if args.completion_budget and (args.workspace_root_guidance
            or (args.task != 'zvec-q001' and not args.closed_issue_expansion and not args.architecture_pilot)):
        parser.error('completion-v2 requires zvec-q001 or a separate issue expansion/architecture phase')
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
    expansion_plan = None
    expansion_dataset_path = ROOT / 'benchmarks/zvec-issue-qa/dataset-expansion-v1.json'
    if args.closed_issue_expansion:
        plan_path = HERE / 'experiment-issue-expansion-v1.json'
        try:
            expansion_plan = validate_expansion_plan()
        except (ValueError, OSError) as error:
            parser.error(str(error))
    remote_plan = None
    if args.remote_embedding_pilot:
        try:
            remote_plan = validate_remote_embedding_plan()
            remote_embedding.validate_environment()
        except (ValueError, KeyError, OSError) as error:
            parser.error(str(error))
    architecture_plan = None
    architecture_dataset_path = ROOT / architecture_phase.DATASET_PATH
    if args.architecture_pilot:
        try:
            architecture_plan = validate_architecture_plan()
            remote_embedding.validate_environment()
        except (ValueError, KeyError, OSError) as error:
            parser.error(str(error))
    use_remote_embedding = args.remote_embedding_pilot or args.architecture_pilot
    selected_remote_plan_path = HERE / (architecture_phase.PLAN_NAME if args.architecture_pilot else remote_embedding.PLAN_NAME)
    work, reports = args.work.resolve(), args.reports.resolve()
    work.mkdir(parents=True, exist_ok=False)
    reports.mkdir(parents=True, exist_ok=True)
    if args.closed_issue_expansion:
        shutil.copyfile(plan_path, reports / 'experiment-plan.json')
        shutil.copyfile(expansion_dataset_path, reports / 'dataset-manifest.json')
    if args.architecture_pilot:
        shutil.copyfile(selected_remote_plan_path, reports / 'experiment-plan.json')
        shutil.copyfile(architecture_dataset_path, reports / 'dataset-manifest.json')
        embedding, ignore_text, upstream = upstream_protocol(work, reports, remote=True,
                                                           remote_plan_path=selected_remote_plan_path)
    elif args.remote_embedding_pilot:
        shutil.copyfile(HERE / remote_embedding.PLAN_NAME, reports / 'experiment-plan.json')
        embedding, ignore_text, upstream = upstream_protocol(work, reports, remote=True)
    else:
        embedding, ignore_text, upstream = upstream_protocol(work, reports)
    if args.task == 'reflex-6':
        repository = 'https://github.com/reflex-dev/reflex.git'
        revision = 'fe0f946dc0c240c6c1e513318c21db407e191c78'
        prompt = ROOT / 'benchmarks/swe-qa-bench/datasets/reflex-6/instruction.md'
    elif args.closed_issue_expansion or args.architecture_pilot:
        selected_dataset_path = architecture_dataset_path if args.architecture_pilot else expansion_dataset_path
        dataset = json.loads(selected_dataset_path.read_text())
        selected = [task for task in dataset['tasks'] if 'zvec-' + task['id'] == args.task]
        if len(selected) != 1:
            raise RuntimeError('selected task must occur exactly once in its frozen dataset')
        repository, revision = selected[0]['source_repository'], selected[0]['source_commit']
        prompt = selected_dataset_path.parent / selected[0]['instruction']
    else:
        repository = 'https://github.com/alibaba/zvec.git'
        revision = 'ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d'
        prompt = ROOT / 'benchmarks/zvec-issue-qa/tasks/q001/instruction.md'
    repo, source = work / 'git', work / 'source'
    run(['git', 'init', '-q', str(repo)])
    run(['git', '-C', str(repo), 'fetch', '-q', '--depth=1', repository, revision])
    actual = run(['git', '-C', str(repo), 'rev-parse', 'FETCH_HEAD']).strip()
    assert actual == revision
    if args.task.startswith('zvec-'):
        run(['python3', str(ROOT / 'benchmarks/zvec-issue-qa/prepare_corpus.py'),
             '--repo', str(repo), '--output', str(source),
             '--manifest', str(reports / 'corpus-validation.json')]
            + (['--dataset', str(selected_dataset_path), '--task', selected[0]['id']]
               if args.closed_issue_expansion or args.architecture_pilot else []))
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
        embedding_base, endpoint, authorization_seconds = base, None, None
        if use_remote_embedding:
            endpoint = remote_embedding.ENDPOINT
            embedding_base = base[:-1] + [item for name in remote_embedding.ENV_NAMES
                                         for item in ('--env', name)] + base[-1:]
            started = time.monotonic()
            run(embedding_base + remote_embedding.authorization_command(), output=reports / 'embedding-authorization.log')
            run(embedding_base + ['zg', '--auth', 'status', '/workspace'], output=reports / 'embedding-authorization-status.log')
            authorization_seconds = time.monotonic() - started
        start = time.monotonic()
        run(embedding_base + index_command(embedding, endpoint),
            output=reports / 'index.log')
        run(embedding_base + ['zg', '--status', '--check-ready'], output=reports / 'index-status.log')
        index_setup_seconds = time.monotonic() - start
        if use_remote_embedding:
            identity = remote_embedding.verify_index_identity(index)
            (reports / 'embedding-index-identity.json').write_text(json.dumps(identity, indent=2) + '\n')
            seed_scan = remote_embedding.scan_seed_credentials([index, home], SECRETS)
            (reports / 'private-seed-secret-scan.json').write_text(json.dumps(seed_scan, indent=2) + '\n')
        metadata = {'task': args.task, 'repository': repository, 'revision': revision,
                    'phase': ('architecture-development' if args.architecture_pilot else
                              'remote-embedding-development' if args.remote_embedding_pilot else
                              'issue-expansion' if args.closed_issue_expansion else
                              'confirmation' if args.completion_confirmation else 'development'),
                    'confirmation_plan': confirmation_plan,
                    'issue_expansion_plan': expansion_plan,
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
                    'embedding_model': embedding, 'embedding_runtime': 'remote' if use_remote_embedding else 'local',
                    'embedding_endpoint': endpoint,
                    'upstream_benchmark': upstream, 'zg_runtime': runtime_provenance,
                    'index_setup_seconds': index_setup_seconds,
                    'index_file_count': sum(p.is_file() for p in index.rglob('*')),
                    'original_source_file_count': original_source_file_count,
                    'runtime_source_file_count': original_source_file_count + 1,
                    'runtime_auxiliary_files': [{
                        'path': '.zg-bench-index.ignore', 'sha256': upstream['index_ignore_sha256'],
                        'purpose': 'main benchmark indexing exclusions; added after original gold validation',
                    }],
                    'model_cache': ('Remote service for index/query embeddings; no local embedding cache used'
                                    if use_remote_embedding else 'baked runtime-image cache; query-time network download unnecessary'),
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
        if use_remote_embedding:
            metadata.update({'architecture_plan' if args.architecture_pilot else 'remote_embedding_plan':
                                 architecture_plan if args.architecture_pilot else remote_plan,
                             'embedding_authorization_seconds': authorization_seconds,
                             'embedding_index_identity': 'embedding-index-identity.json',
                             'embedding_accounting': json.loads(selected_remote_plan_path.read_text())['accounting'],
                             'embedding_authorization_command': remote_embedding.authorization_command(),
                             'embedding_index_command': index_command(embedding, endpoint)})
        (reports / 'setup.json').write_text(json.dumps(metadata, indent=2) + '\n')
        home_seed = home / '.zvec-grep'
        home_seed.mkdir(exist_ok=True)
        probe_started = time.monotonic()
        run(['python3', str(HERE / 'probe_mcp.py'), '--source', str(source),
             '--index-seed', str(index), '--home-seed', str(home_seed), '--image', IMAGE,
             '--output', str(reports / 'mcp-preflight.json')]
            + (['--embedding-model', embedding] if use_remote_embedding else []),
            output=reports / 'mcp-preflight-run.log', timeout=600 if use_remote_embedding else 450, live=True)
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
