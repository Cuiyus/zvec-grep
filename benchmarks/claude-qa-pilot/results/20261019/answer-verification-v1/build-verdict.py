#!/usr/bin/env python3
"""Build exactly two outcome-neutral reports from explicitly verified inputs.

No grading, network calls, CI status inference, or automatic reruns. Answer and
stream contents are hashed as opaque bytes. Only explicitly named JSON metadata
is parsed. All inputs must already exist; output reports must not exist.

Required CLI inputs: --archived-dir --review --mapping --audit-summary --plan
--prompt --rubric. The archive must contain experiment-plan.json, manifest.json,
setup.json, prompt.txt, answer-verification-instructions.txt, and r01-baseline /
r01-zg with metrics.json, answer.txt and stream.jsonl[.gz]. The original raw and
anonymous answer paths in the mapping must remain readable. The review/mapping
use the earlier q004 blinded reviewer formats.

The explicit audit summary normalizes the independently completed audit without
assuming that audit's schema. Use --print-audit-summary-template to see its shape.
Every declaration is bound to that audit's bytes and to actual raw input hashes.
The builder checks those identities and numeric consistency; it does not replace
independent stream/root/source validation. An audit may conservatively invalidate
an optimistic raw metric, but it cannot upgrade a raw false flag to true.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import stat
import sys

PLAN_SHA256 = '95dc686efa7db675d81831ab74065fe652d775db201bc6ba14fd99e95b445b53'
PHASE = 'answer-verification-development'
PROTOCOL = 'answer-verification-v1'
PROFILES = ('baseline', 'zg')
REPORT_NAMES = ('review-input-verification.json', 'pair-verdict.json')
AUDIT_SUMMARY_TEMPLATE = {
    'schema_version': 1, 'task_id': 'q004', 'phase': PHASE,
    'run_id': None, 'head_sha': '<40 lowercase hex>', 'plan_sha256': PLAN_SHA256,
    'execution_audit': {'path': '<absolute independently verified audit JSON>', 'sha256': '<64 lowercase hex>'},
    'source_unchanged_verified': None,
    'arms': {profile: {
        'metrics_sha256': '<SHA of archived metrics bytes>',
        'stream_sha256': '<SHA of decoded actual QA stream bytes>',
        'answer_sha256': '<SHA of raw final-answer bytes>',
        'completed': None, 'protocol_valid': None, 'usage_complete': None, 'valid': None,
        'input_tokens_including_cache': None, 'output_tokens': None, 'wall_seconds': None,
    } for profile in PROFILES},
    'retrieval': {'basis': 'actual_qa_stream_tool_results', 'root': '/workspace',
                  'zg_stream_sha256': '<same SHA as arms.zg.stream_sha256>',
                  'successful_correct_root_calls': None},
    'verification_note': '<How the independent audit established terminal usage, source invariance and actual QA root/result identity; not CI success or preflight.>',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    path = Path(path)
    require(not path.is_symlink() and stat.S_ISREG(path.stat().st_mode), f'Expected regular non-symlink input: {path}')
    return path


def read_bytes(path):
    return regular(path).read_bytes()


def read_json(path):
    value = json.loads(read_bytes(path))
    require(isinstance(value, dict), f'Expected JSON object: {path}')
    return value


def fingerprint(path):
    data = read_bytes(path)
    return {'path': str(Path(path).resolve()), 'sha256': sha(data), 'bytes': len(data)}


def hex_identity(value, length):
    return isinstance(value, str) and len(value) == length and all(c in '0123456789abcdef' for c in value)


def number(value, name, *, nullable=False, integer=False):
    if nullable and value is None:
        return value
    require(type(value) in ((int,) if integer else (int, float)) and math.isfinite(value) and value >= 0,
            f'Invalid nonnegative {name}')
    return value


def boolean(value, name):
    require(type(value) is bool, f'Missing explicit boolean {name}')
    return value


def indexed(items, key, expected, label):
    require(isinstance(items, list) and len(items) == len(expected), f'Expected exactly {len(expected)} {label}')
    require(all(isinstance(item, dict) and key in item for item in items), f'Invalid {label}')
    result = {item[key]: item for item in items}
    require(len(result) == len(items) and set(result) == set(expected), f'Unexpected or duplicate {label}')
    return result


def decoded_stream_identity(archive, profile):
    candidates = [archive / f'r01-{profile}' / name for name in ('stream.jsonl', 'stream.jsonl.gz')]
    present = [path for path in candidates if path.exists() or path.is_symlink()]
    require(len(present) == 1, f'Exactly one archived stream representation required for {profile}')
    path = regular(present[0])
    digest, size = hashlib.sha256(), 0
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rb') as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block); size += len(block)
    return dict(fingerprint(path), decoded_sha256=digest.hexdigest(), decoded_bytes=size)


def percent_change(baseline, zg, comparable=True):
    if not comparable or baseline is None or zg is None or baseline == 0:
        return None
    return (zg - baseline) / baseline * 100


def derive_signals(quality, arms, source_unchanged, correct_root_successes):
    """The fixed prospective decision, independent of any observed scores."""
    complete_valid_usage = source_unchanged and all(
        all(arms[p][key] for key in ('completed', 'protocol_valid', 'usage_complete', 'valid'))
        for p in PROFILES)
    baseline, zg = quality['baseline'], quality['zg']
    not_lower = zg['factual_points'] >= baseline['factual_points']
    retrieval = correct_root_successes > 0
    usage_comparable = complete_valid_usage
    lower_input = (arms['zg']['input_tokens_including_cache'] < arms['baseline']['input_tokens_including_cache']
                   if usage_comparable else None)
    primary = bool(zg['source_grounded_success'] and not baseline['source_grounded_success']
                   and not_lower and complete_valid_usage and retrieval)
    efficiency = bool(baseline['source_grounded_success'] and zg['source_grounded_success']
                      and not_lower and complete_valid_usage and lower_input and retrieval)
    return {'primary_source_grounded_success_gain': primary, 'efficiency_improvement': efficiency,
            'stop_this_q004_shared_instruction_branch': not (primary or efficiency),
            'conditions': {'both_completed_protocol_valid_usage_complete_and_valid': complete_valid_usage,
                           'source_unchanged_verified': source_unchanged,
                           'zg_factual_points_not_lower': not_lower,
                           'both_source_grounded_success': baseline['source_grounded_success'] and zg['source_grounded_success'],
                           'zg_input_including_cache_lower': lower_input,
                           'usage_comparable': usage_comparable,
                           'correct_root_retrieval_succeeded': retrieval}}


def review_quality(review):
    final = boolean(review['final_answer_present'], 'review final_answer_present')
    success = boolean(review['source_grounded_success'], 'review source_grounded_success')
    scores = indexed(review['criterion_scores'], 'id', ['C1', 'C2', 'C3', 'C4', 'C5'], 'review criterion scores')
    for item in scores.values():
        number(item['points'], 'criterion points', integer=True)
        require(item['max'] == 2 and item['points'] <= item['max'], 'Unexpected frozen criterion maximum or score')
    require(review['total_points'] == sum(item['points'] for item in scores.values()), 'Review factual total mismatch')
    require(review['maximum_points'] == sum(item['max'] for item in scores.values()) == 10, 'Review maximum mismatch')
    units = indexed(review['point_unit_facts'], 'id', [f'C{i}.{j}' for i in range(1, 6) for j in (1, 2)], 'review point units')
    for criterion, item in scores.items():
        selected = [units[f'{criterion}.{j}'] for j in (1, 2)]
        require(all(type(unit['points']) is int and unit['points'] in (0, 1) and unit['maximum_points'] == 1 for unit in selected), 'Invalid review point unit')
        require(sum(unit['points'] for unit in selected) == item['points'], 'Review point-unit/criterion mismatch')
    require(isinstance(review['failed_gates'], list) and isinstance(review['critical_contradictions'], list), 'Missing reviewed gates/contradictions')
    require(success == (final and not review['failed_gates']), 'Review success flag conflicts with final-answer/gate findings')
    if success:
        require(not review['critical_contradictions'], 'Successful review includes a critical contradiction')
        require(all(item['passed'] is True for item in review['core_citation_gate_results']), 'Successful review includes a failed citation gate')
    return {'factual_points': review['total_points'], 'maximum': review['maximum_points'],
            'source_grounded_success': success, 'final_answer_present': final,
            'criterion_scores': {key: item['points'] for key, item in scores.items()},
            'failed_gates': review['failed_gates'],
            'critical_contradiction_count': len(review['critical_contradictions'])}


def build_reports(archive, review_path, mapping_path, audit_summary_path, plan_path, prompt_path, rubric_path):
    archive = Path(archive).resolve(strict=True)
    require(archive.is_dir(), 'Archive must be an existing directory')
    require({path.name for path in archive.iterdir() if path.is_dir() and path.name.startswith('r0')} == {'r01-baseline', 'r01-zg'}, 'Expected exactly one archived pair')
    inputs = [Path(p) for p in (review_path, mapping_path, audit_summary_path, plan_path, prompt_path, rubric_path)]
    review_path, mapping_path, audit_summary_path, plan_path, prompt_path, rubric_path = inputs
    initial_hashes = {str(path.resolve()): fingerprint(path) for path in inputs}
    plan, review, mapping, audit = map(read_json, (plan_path, review_path, mapping_path, audit_summary_path))
    require(sha(read_bytes(plan_path)) == PLAN_SHA256, 'Plan differs from the exact frozen answer-verification plan')
    require(plan['frozen'] is True and plan['phase'] == PHASE and plan['protocol_version'] == PROTOCOL, 'Wrong frozen phase/protocol')
    require(plan['run_plan']['trial_count'] == 2 and plan['run_plan']['order'] == list(PROFILES) and plan['run_plan']['repeats'] == plan['run_plan']['start_repeat'] == 1, 'Plan is not exactly one baseline then zg pair')
    require(read_bytes(archive / 'experiment-plan.json') == read_bytes(plan_path), 'Archived and explicit frozen plans differ')
    prompt_hash, rubric_hash = sha(read_bytes(prompt_path)), sha(read_bytes(rubric_path))
    require(prompt_hash == plan['task']['question']['sha256'] == sha(read_bytes(archive / 'prompt.txt')), 'Frozen question identity mismatch')
    require(rubric_hash == plan['task']['rubric']['sha256'], 'Frozen rubric identity mismatch')
    require(sha(read_bytes(archive / 'answer-verification-instructions.txt')) == plan['shared_answer_verification']['sha256'], 'Archived suffix identity mismatch')
    require(review['task_id'] == mapping['task_id'] == audit['task_id'] == 'q004', 'Wrong task identity')
    require(review['inputs']['source_commit'] == plan['task']['revision'], 'Reviewed source identity mismatch')
    require(review['inputs']['prompt_sha256'] == prompt_hash and review['inputs']['rubric_sha256'] == rubric_hash, 'Reviewer prompt/rubric identity mismatch')
    require(audit['schema_version'] == 1 and audit['phase'] == PHASE and audit['plan_sha256'] == PLAN_SHA256, 'Audit summary phase/plan mismatch')
    require(type(audit['run_id']) is int and audit['run_id'] > 0 and hex_identity(audit['head_sha'], 40), 'Missing audited execution identity')
    require(isinstance(audit['verification_note'], str) and bool(audit['verification_note'].strip()), 'Audit basis must be explicit')
    execution_audit = Path(audit['execution_audit']['path'])
    require(hex_identity(audit['execution_audit']['sha256'], 64) and sha(read_bytes(execution_audit)) == audit['execution_audit']['sha256'], 'Independent execution audit hash mismatch')
    read_json(execution_audit)  # Require an actual audit JSON; do not infer its unknown schema.
    initial_hashes[str(execution_audit.resolve())] = fingerprint(execution_audit)
    source_unchanged = boolean(audit['source_unchanged_verified'], 'audited source_unchanged_verified')
    require(set(audit['arms']) == set(PROFILES), 'Audit summary must declare exactly both arms')
    setup, manifest = read_json(archive / 'setup.json'), read_json(archive / 'manifest.json')
    require(setup['task'] == 'zvec-q004' and setup['phase'] == PHASE and setup['protocol_version'] == PROTOCOL, 'Setup identity mismatch')
    require(setup['revision'] == plan['task']['revision'] and setup['planned_trial_count'] == 2 and setup['planned_trials'] == [{'repeat': 1, 'profile': p} for p in PROFILES], 'Setup source/pair mismatch')
    require(setup['answer_verification_plan']['sha256'] == PLAN_SHA256, 'Setup plan identity mismatch')
    require(manifest['protocol_version'] == PROTOCOL and manifest['requested_model'] == plan['agent']['model'], 'Manifest phase/model mismatch')
    require(manifest['prompt_sha256'] == prompt_hash and manifest['common_instructions_sha256'] == sha(manifest['common_instructions'].encode()) == plan['common_instructions_sha256'], 'Manifest effective prompt mismatch')
    require(manifest['order'] == list(PROFILES) and manifest['repeats'] == manifest['start_repeat'] == 1, 'Manifest pair mismatch')
    records = indexed(mapping['records'], 'profile', PROFILES, 'mapping profiles')
    ids = [records[p]['blinded_answer_id'] for p in PROFILES]
    require(len(set(ids)) == 2, 'Blinded answer IDs must be distinct')
    reviews = indexed(review['reviews'], 'blinded_answer_id', ids, 'blinded reviews')
    review_inputs = indexed(review['inputs']['answer_files'], 'blinded_answer_id', ids, 'review answer inputs')
    quality, checks, streams, arms = {}, [], {}, {}
    for profile in PROFILES:
        record = records[profile]; answer_id = record['blinded_answer_id']; reviewed = reviews[answer_id]
        paths = {'raw': Path(record['raw_path']), 'anonymous': Path(record['blinded_path']),
                 'archived': archive / f'r01-{profile}/answer.txt'}
        hashes = {name: fingerprint(path) for name, path in paths.items()}
        expected = record['answer_sha256']
        require(hex_identity(expected, 64) and all(item['sha256'] == expected for item in hashes.values()), 'Raw/anonymous/archived answer hash mismatch')
        require(all(item['bytes'] == record['bytes'] for item in hashes.values()), 'Answer byte-count mismatch')
        require(reviewed['answer_sha256'] == review_inputs[answer_id]['sha256'] == expected, 'Reviewer answer hash mismatch')
        require(Path(review_inputs[answer_id]['path']).resolve() == paths['anonymous'].resolve(), 'Reviewer did not name the mapped anonymous answer')
        quality[profile] = review_quality(reviewed)
        metrics_path = archive / f'r01-{profile}/metrics.json'; metrics = read_json(metrics_path)
        require(metrics['profile'] == profile and metrics['repeat'] == 1, 'Metric arm/repeat mismatch')
        require(metrics['requested_model'] == plan['agent']['model'], 'Metric requested model mismatch')
        arm = audit['arms'][profile]
        require(arm['metrics_sha256'] == sha(read_bytes(metrics_path)) and arm['answer_sha256'] == expected, 'Audited metric/answer identity mismatch')
        streams[profile] = decoded_stream_identity(archive, profile)
        require(arm['stream_sha256'] == streams[profile]['decoded_sha256'], 'Audited actual QA stream identity mismatch')
        for key in ('completed', 'protocol_valid', 'usage_complete', 'valid'):
            boolean(arm[key], f'{profile} audited {key}')
            if key != 'completed' and arm[key]:
                require(metrics[key] is True, 'Audit summary cannot upgrade a false raw metric flag')
        if arm['completed']:
            require(metrics['result_subtype'] == 'success' and metrics['exit_code'] == 0 and quality[profile]['final_answer_present'], 'Audited completion conflicts with terminal metrics/review')
        for key in ('input_tokens_including_cache', 'output_tokens'):
            number(arm[key], key, nullable=not arm['usage_complete'], integer=True)
            require(arm[key] == metrics[key], 'Audited usage differs from archived metrics')
        number(arm['wall_seconds'], 'wall_seconds')
        require(arm['wall_seconds'] == metrics['wall_seconds'], 'Audited wall differs from archived metrics')
        if arm['usage_complete']:
            usage = metrics['usage']
            fields = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')
            for key in fields: number(usage[key], key, integer=True)
            require(arm['input_tokens_including_cache'] == sum(usage[key] for key in fields[:3]), 'Complete input including cache sum mismatch')
            require(arm['output_tokens'] == usage['output_tokens'], 'Complete output usage mismatch')
        arms[profile] = dict(arm)
        checks.append({'blinded_answer_id': answer_id, 'profile': profile, 'answer_identities': hashes,
                       'declared_sha256': expected, 'review_sha256': reviewed['answer_sha256'], 'all_equal': True})
        for path in (*paths.values(), metrics_path): initial_hashes[str(path.resolve())] = fingerprint(path)
    retrieval = audit['retrieval']
    require(retrieval['basis'] == 'actual_qa_stream_tool_results' and retrieval['root'] == '/workspace', 'Retrieval evidence must concern actual QA results at /workspace')
    require(retrieval['zg_stream_sha256'] == streams['zg']['decoded_sha256'], 'Retrieval evidence names a different stream')
    successes = number(retrieval['successful_correct_root_calls'], 'correct-root successes', integer=True)
    zg_metrics = read_json(archive / 'r01-zg/metrics.json')
    require(successes <= zg_metrics['zg_tool_successes'], 'Audited correct-root successes exceed raw MCP successes')
    signals = derive_signals(quality, arms, source_unchanged, successes)
    setup_seconds = {key: number(setup[key], key) for key in
                     ('embedding_authorization_seconds', 'index_setup_seconds', 'mcp_preflight_seconds')}
    comparable = signals['conditions']['usage_comparable']
    resources = {}
    for key in ('input_tokens_including_cache', 'output_tokens', 'wall_seconds'):
        can_compare = True if key == 'wall_seconds' else comparable
        resources['agent_wall_seconds' if key == 'wall_seconds' else key] = {
            'baseline': arms['baseline'][key], 'zg': arms['zg'][key], 'comparable': can_compare,
            'change_percent': percent_change(arms['baseline'][key], arms['zg'][key], can_compare)}
    resources.update(setup_seconds=setup_seconds, setup_seconds_sum=sum(setup_seconds.values()),
                     zg_agent_plus_measured_setup_seconds=arms['zg']['wall_seconds'] + sum(setup_seconds.values()),
                     setup_scope='Only measured grant + index/readiness + MCP preflight; excludes install/build and does not establish cold end-to-end speedup.')
    for path in (archive / name for name in ('experiment-plan.json', 'setup.json', 'manifest.json', 'prompt.txt', 'answer-verification-instructions.txt')):
        initial_hashes[str(path.resolve())] = fingerprint(path)
    for item in streams.values(): initial_hashes[item['path']] = {k: item[k] for k in ('path', 'sha256', 'bytes')}
    require(all(fingerprint(path) == saved for path, saved in initial_hashes.items()), 'An input changed while building the verdict')
    now = datetime.now(timezone.utc).isoformat()
    verification = {'schema_version': 1, 'task_id': 'q004', 'run_id': audit['run_id'], 'verified_at_utc': now,
                    'checks': checks, 'prompt_sha256': prompt_hash, 'rubric_sha256': rubric_hash,
                    'review_file_sha256': sha(read_bytes(review_path)), 'mapping_file_sha256': sha(read_bytes(mapping_path)),
                    'fact_totals_verified': True, 'pass_flags_match_failed_gates': True,
                    'scope': 'Identity/arithmetic consistency only; no grading or independent reassessment of source citations.',
                    'input_identities': list(initial_hashes.values())}
    verdict = {'schema_version': 1, 'run_id': audit['run_id'], 'head_sha': audit['head_sha'], 'task_id': 'q004',
               'phase': PHASE, 'plan': fingerprint(plan_path), 'prospective_decision': plan['prospective_decision'],
               'quality': quality, 'execution': {'arms': arms, 'source_unchanged_verified': source_unchanged,
                    'zg_correct_root_successes': successes, 'root_evidence_basis': retrieval['basis'],
                    'independent_audit': fingerprint(execution_audit), 'audit_summary': fingerprint(audit_summary_path),
                    'verification_note': audit['verification_note']},
               **signals, 'resources': resources, 'review_input_verification': REPORT_NAMES[0],
               'input_identities': list(initial_hashes.values()),
               'limitations': list(plan['prospective_decision']['limits']) + [
                    'This builder computes the frozen prospective conditions from reviewed grades and an explicitly verified execution audit; it does not grade, inspect semantic answer content, or infer validity from CI success.',
                    'Correct-root retrieval is declared by the independent audit from actual QA streams; stream hashes bind the declaration, but this builder does not parse tool results or infer retrieval from setup/preflight.',
                    'Shared prompt inclusion does not establish that either model performed an internal self-check, or that the instruction caused any observed difference.',
                    'Incomplete or invalid usage is uncomparable, even if partial token numbers are present. Agent wall and measured setup are reported independently; no install/build total or cold end-to-end speedup is claimed.']}
    return verification, verdict


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ('archived-dir', 'review', 'mapping', 'audit-summary', 'plan', 'prompt', 'rubric'):
        parser.add_argument('--' + flag, type=Path)
    parser.add_argument('--print-audit-summary-template', action='store_true')
    args = parser.parse_args(argv)
    if args.print_audit_summary_template:
        print(json.dumps(AUDIT_SUMMARY_TEMPLATE, indent=2)); return 0
    for key, value in vars(args).items():
        if key != 'print_audit_summary_template' and value is None:
            parser.error('--' + key.replace('_', '-') + ' is required')
    outputs = [args.archived_dir / name for name in REPORT_NAMES]
    require(all(not path.exists() and not path.is_symlink() for path in outputs), 'Refusing to overwrite an existing report')
    reports = build_reports(args.archived_dir, args.review, args.mapping, args.audit_summary, args.plan, args.prompt, args.rubric)
    for path, report in zip(outputs, reports):
        with path.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False); stream.write('\n')
    print(json.dumps({'reports': [fingerprint(path) for path in outputs], 'grading_performed': False,
                      'primary_source_grounded_success_gain': reports[1]['primary_source_grounded_success_gain'],
                      'efficiency_improvement': reports[1]['efficiency_improvement']}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('Verdict input validation failed: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
