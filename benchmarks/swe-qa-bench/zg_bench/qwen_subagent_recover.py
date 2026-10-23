"""Copy, verify and collect Qwen final answers; this module cannot run agents."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess

from zg_bench import qwen_subagent_e2e as experiment
from zg_bench.no_subagent_recover import canonical_digest, manifest


def prepare(args):
    protocol = experiment.configure('formal')
    experiment.native.validate_inputs()
    settings = experiment.validate_model_settings()
    source, output = args.source.resolve(), args.output.resolve()
    if source == output or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Recovery output must be separate from source evidence')
    if output.exists():
        raise ValueError('Refusing to overwrite recovery output; resume it with run --collect-only')
    if args.task not in experiment.selected_tasks('formal'):
        raise ValueError('Task is not in the frozen formal population')
    run = json.loads(args.source_run.read_text())
    if (run.get('id') != args.source_run_id or run.get('head_sha') != args.expected_source_head
            or run.get('repository', {}).get('full_name') != 'Cuiyus/zvec-grep'
            or run.get('event') != 'workflow_dispatch'):
        raise ValueError('Source workflow identity differs from the requested run')
    meta = json.loads((source / 'provenance.json').read_text())
    identities = {'rust': experiment.versions.identity(args.packages / 'rust', 'rust')}
    if (meta.get('experiment') != protocol or meta.get('identities') != identities
            or meta.get('task') != args.task or meta.get('repetitions') != 10
            or meta.get('model') != experiment.MODEL or meta.get('model_settings') != settings
            or meta.get('harness_commit') != args.expected_source_head):
        raise ValueError('Cannot recover evidence from a different frozen experiment')
    expected = {(arm, rep) for arm in experiment.ARMS for rep in range(1, 11)}
    execution = meta.get('execution', [])
    if (len(execution) != 30 or {(r.get('arm'), r.get('repetition')) for r in execution} != expected
            or any(r.get('returncode') != 0 for r in execution)):
        raise ValueError('Every existing final execution slot must have succeeded')
    files = manifest(source)
    shutil.copytree(source, output)
    experiment.versions.write_json(output / 'source-manifest.json', files)
    experiment.versions.write_json(output / 'source-run.json', run)
    experiment.versions.write_json(output / 'original-provenance.json', meta)
    audit_path = source / 'no-subagent-audit.json'
    if audit_path.exists():
        shutil.copy2(audit_path, output / 'original-no-subagent-audit.json')
    meta['collection_recovery'] = dict(
        kind='collect-existing-answers-only', source_run_id=args.source_run_id,
        source_run_url=run['html_url'], source_harness_commit=args.expected_source_head,
        collector_harness_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        started_at=datetime.now(timezone.utc).isoformat(), source_manifest_sha256=canonical_digest(files),
        new_agent_executions=0, policy='Reuse all final answers and judge checkpoints; preserve failed attempts and unknown usage')
    experiment.versions.write_json(output / 'provenance.json', meta)
    experiment.audit_and_qualify(output, meta)
    trials = experiment.collect_trials(output, args.task, identities)
    if any(len(rows) != 10 or {r['trial_index'] for r in rows} != set(range(1, 11)) for rows in trials.values()):
        raise ValueError('Collection did not preserve every final answer')
    experiment.versions.write_json(output / 'trials.json', trials)
    if manifest(source) != files:
        raise ValueError('Source evidence changed during collection')
    return meta, identities


def main(args):
    meta, identities = prepare(args)
    if not args.qualify_only:
        experiment.finish_collection(args.output.resolve(), args.task, identities, meta, phase='formal')
    print(json.dumps(dict(qualified=True, final_answers=30, new_agent_executions=0,
                          judged=not args.qualify_only,
                          full_attempt_audit_complete=meta['no_subagent_audit']['complete'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--packages', required=True, type=Path)
    parser.add_argument('--task', required=True)
    parser.add_argument('--source-run', required=True, type=Path)
    parser.add_argument('--source-run-id', required=True, type=int)
    parser.add_argument('--expected-source-head', required=True)
    parser.add_argument('--qualify-only', action='store_true')
    main(parser.parse_args())
