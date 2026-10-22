"""Collect and score existing formal answers; this entry point cannot run agents.

Preserve the original strict audit, including unknown failed-attempt usage.
An independent final-answer qualification must pass before any paid judging.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from zg_bench import no_subagent_e2e as experiment
from zg_bench import native_baseline_e2e as native
from zg_bench import three_arm_e2e as three_arm
from zg_bench import version_e2e as versions
from zg_bench.no_subagent_audit import audit
from zg_bench.no_subagent_qualification import qualify
from zg_bench.swe_qa.pairs import load_references


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def manifest(root):
    rows = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Recovery requires regular immutable source files')
        if path.is_file():
            rows.append(dict(path=str(path.relative_to(root)), size=path.stat().st_size,
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return rows


def prepare(args):
    protocol = experiment.configure('formal')
    native.validate_inputs()
    source, output = args.source.resolve(), args.output.resolve()
    if source == output or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Recovery output must be separate from source evidence')
    if output.exists():
        raise ValueError('Refusing to overwrite recovery output or repeat cached grading')
    if args.task not in experiment.selected_tasks('formal'):
        raise ValueError('Task is not in the frozen formal population')
    run = json.loads(args.source_run.read_text())
    if (run.get('id') != args.source_run_id or run.get('head_sha') != args.expected_source_head
            or run.get('repository', {}).get('full_name') != 'Cuiyus/zvec-grep'
            or run.get('event') != 'workflow_dispatch'):
        raise ValueError('Source workflow identity differs from the requested run')
    meta = json.loads((source / 'provenance.json').read_text())
    identities = {arm: versions.identity(args.packages / arm, arm) for arm in ('node', 'rust')}
    if (meta.get('experiment') != protocol or meta.get('identities') != identities
            or meta.get('task') != args.task or meta.get('repetitions') != 10
            or meta.get('model') != experiment.MODEL
            or meta.get('harness_commit') != args.expected_source_head):
        raise ValueError('Cannot recover evidence from a different frozen experiment')
    expected = {(arm, rep) for arm in experiment.ARMS for rep in range(1, 11)}
    execution = meta.get('execution', [])
    if (len(execution) != 40 or {(r.get('arm'), r.get('repetition')) for r in execution} != expected
            or any(r.get('returncode') != 0 for r in execution)):
        raise ValueError('Every existing final execution slot must have succeeded')
    if (source / 'report/report.json').exists():
        raise ValueError('Task already has a report; do not grade it again')
    original_files = manifest(source)
    original_audit = json.loads((source / 'no-subagent-audit.json').read_text())
    shutil.copytree(source, output)
    versions.write_json(output / 'source-manifest.json', original_files)
    versions.write_json(output / 'source-run.json', run)
    versions.write_json(output / 'original-provenance.json', meta)
    versions.write_json(output / 'original-no-subagent-audit.json', original_audit)
    try:
        computed = audit(output, expected_slots=40)
    except ValueError:
        # audit writes its strict result before rejecting incomplete history.
        computed = json.loads((output / 'no-subagent-audit.json').read_text())
    if computed != original_audit:
        raise ValueError('Raw recomputation must exactly reproduce the source strict audit')
    qualification = qualify(output, expected_slots=40, audit=computed)
    versions.write_json(output / 'qualification.json', qualification)
    if not qualification.get('eligible'):
        raise ValueError('Existing final answers did not qualify for collection')
    meta['no_subagent_audit'] = computed
    meta['final_answer_qualification'] = qualification
    meta['collection_recovery'] = dict(
        kind='collect-existing-answers-only', source_run_id=args.source_run_id,
        source_run_url=run['html_url'], source_harness_commit=args.expected_source_head,
        collector_harness_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        qualified_before_judging_at=datetime.now(timezone.utc).isoformat(),
        source_manifest_sha256=canonical_digest(original_files), new_agent_executions=0,
        original_strict_audit_preserved=True,
        policy='Reuse every existing final answer; preserve failed attempts and unknown usage; unchanged judge and references',
    )
    versions.write_json(output / 'provenance.json', meta)
    pair = versions.collect(output, args.task, identities, repetitions=10,
                            expected_model=experiment.MODEL, expected_embedding=protocol['embedding_model'])
    trials = {
        'baseline_allowed': native.collect(output / 'baseline_allowed', args.task, experiment.MODEL),
        'baseline': native.collect(output / 'baseline', args.task, experiment.MODEL),
        'node': pair['profiles']['baseline']['trials'],
        'rust': pair['profiles']['zvec-grep']['trials'],
    }
    if any(len(rows) != 10 or {r['trial_index'] for r in rows} != set(range(1, 11)) for rows in trials.values()):
        raise ValueError('Collection did not preserve every final answer')
    versions.write_json(output / 'trials.json', trials)
    if manifest(source) != original_files:
        raise ValueError('Source evidence changed during collection')
    return protocol, meta, trials


def main(args):
    protocol, meta, trials = prepare(args)
    if args.qualify_only:
        print(json.dumps(dict(qualified=True, final_answers=40, new_agent_executions=0,
                              full_attempt_audit_complete=meta['no_subagent_audit']['complete'])))
        return
    reference = load_references(experiment.DATA / 'references.json')[args.task]
    profiles = three_arm.judge_profiles(trials, reference, args.output)
    report = dict(schema_version=1, kind=protocol['kind'], model=experiment.MODEL,
                  task_id=reference['task_id'], profiles=profiles,
                  judge=three_arm.judge_metadata(profiles), provenance=meta)
    experiment.validate_report(report, args.output / 'report/report.json')
    versions.write_json(args.output / 'report/report.json', report)
    summary = experiment.summarize([report], args.output / 'report', phase='formal', expected_tasks=1)
    if not summary['complete']:
        raise ValueError('Final-answer report is incomplete; retain recovery evidence')
    print(json.dumps(dict(complete=True, final_answers=40, new_agent_executions=0,
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
