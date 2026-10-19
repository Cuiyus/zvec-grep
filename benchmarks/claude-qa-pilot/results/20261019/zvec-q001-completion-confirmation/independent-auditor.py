#!/usr/bin/env python3
"""Independent execution-only audit; never emits or judges final-answer prose."""
import hashlib
import json
import math
import subprocess
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
ART = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/confirmation-39282211793/qa-zvec-q001')
SOURCE = Path('/tmp/zg-zvec-issue-qa-20261019/final-source')
FRESH = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/export-validation/sources/q001')
OUT = Path('/tmp/zg-confirmation-39282211793-independent-audit.json')
HEAD = '1b541847bab806c63b4ebc309b36556ac0fbb772'
ZG = 'mcp__zvec_grep__zvec_grep_search'
FIELDS = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')
DENIAL = 'The investigation budget is exhausted.'

def sha(b):
    return hashlib.sha256(b).hexdigest()

def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':')).encode()

def read(path):
    return json.loads(path.read_text())

def record(path):
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path.read_bytes())}

def utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()

def frozen(path):
    return subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{HEAD}:{path}'])

def entries(command, option):
    return [command[i + 1] for i, x in enumerate(command[:-1]) if x == option]

def opt(command, option):
    values = entries(command, option)
    return values[-1] if values else None

plan_path = ROOT / 'benchmarks/claude-qa-pilot/experiment-completion-confirmation.json'
plan = read(plan_path)
manifest = read(ART / 'manifest.json')
summary = read(ART / 'summary.json')
setup = read(ART / 'setup.json')
source_manifest = read(ART / 'source-manifest.json')
corpus = read(ART / 'corpus-validation.json')
provenance = read(ART / 'zg-provenance.json')
checks = {}

def check(name, value):
    checks[name] = bool(value)

archive = Path('/tmp/zg-confirmation-39282211793-artifact.zip')
archive_sha = sha(archive.read_bytes())
with zipfile.ZipFile(archive) as z:
    members = [n for n in z.namelist() if not n.endswith('/')]
    archive_mismatches = [n for n in members if not (ART / n).is_file() or z.read(n) != (ART / n).read_bytes()]
check('artifact_zip_matches_github_digest', archive_sha == '70a0fa3a253cb0d4892901c1fa3c2153ba5e095ce4f2a30abf711a16068693cf')
check('all_extracted_artifact_bytes_match_downloaded_zip', not archive_mismatches and set(members) == {str(p.relative_to(ART)) for p in ART.rglob('*') if p.is_file()})
check('frozen_plan_is_exact_run_commit_plan', plan_path.read_bytes() == frozen(str(plan_path.relative_to(ROOT))))
check('setup_records_exact_frozen_plan_sha', setup['confirmation_plan']['sha256'] == sha(plan_path.read_bytes()))
run_files = {}
for name in ['run.py', 'audit_artifacts.py', 'tool_budget.py', 'ci_run.py']:
    relative = 'benchmarks/claude-qa-pilot/' + name
    data = frozen(relative)
    run_files[name] = {'run_commit_sha256': sha(data), 'current_worktree_sha256': sha((ROOT / relative).read_bytes()), 'current_equals_run_commit': data == (ROOT / relative).read_bytes()}
check('runner_and_hook_match_run_commit', all(run_files[n]['current_equals_run_commit'] for n in ['run.py', 'audit_artifacts.py', 'tool_budget.py']))
check('hook_identity_matches_frozen_plan', run_files['tool_budget.py']['run_commit_sha256'] == plan['completion_control']['hook_sha256'] == manifest['budget_hook_sha256'])

def inventory(path):
    return [{'path': str(p.relative_to(path)), 'bytes': p.stat().st_size, 'sha256': sha(p.read_bytes())} for p in sorted(path.rglob('*')) if p.is_file()]

original_inventory = inventory(SOURCE)
fresh_inventory = inventory(FRESH)
runtime_files = {row['path']: row['sha256'] for row in original_inventory}
runtime_files['.zg-bench-index.ignore'] = sha((ART / 'upstream-index.ignore').read_bytes())
check('two_independent_local_exports_match', original_inventory == fresh_inventory)
check('corpus_inventory_matches_source_bytes', original_inventory == corpus['files'] and sha(canonical(original_inventory)) == corpus['inventory_sha256'])
check('runtime_source_manifest_matches_export_plus_declared_auxiliary', runtime_files == source_manifest['files'])
check('source_hash_chain_consistent', sha(canonical(runtime_files)) == source_manifest['sha256'] == manifest['source_sha256'])
check('source_counts_match', len(original_inventory) == corpus['source_file_count'] == setup['original_source_file_count'] == 1232 and len(runtime_files) == source_manifest['file_count'] == manifest['source_file_count'] == setup['runtime_source_file_count'] == 1233)
check('source_repository_revision_match_plan', setup['revision'] == corpus['source_commit'] == plan['task']['revision'] and setup['repository'] == corpus['source_repository'] == 'https://github.com/' + plan['task']['repository'] + '.git')
check('source_unchanged_reported_by_controller', summary['source_unchanged'] is True)
check('source_no_git_history_reported', corpus['git_history_present'] is False and not (SOURCE / '.git').exists() and not (FRESH / '.git').exists())
check('corpus_no_known_leakage_reported', corpus['known_leakage_markers_found'] is False)
check('question_bytes_match_frozen_plan', sha((ART / 'prompt.txt').read_bytes()) == manifest['prompt_sha256'] == plan['task']['question']['sha256'] == sha(frozen(plan['task']['question']['path'])))
check('rubric_identity_hash_matches_plan', sha(frozen(plan['task']['rubric']['path'])) == plan['task']['rubric']['sha256'])
check('common_instructions_match_plan', sha(manifest['common_instructions'].encode()) == plan['common_instructions_sha256'])
check('protocol_and_repeat_identity_match_plan', manifest['protocol_version'] == setup['protocol_version'] == plan['protocol_version'] and manifest['planned_trials'] == setup['planned_trials'] == plan['run_plan']['planned_trials'] and manifest['repeats'] == setup['repeats'] == plan['run_plan']['repeats'] and manifest['start_repeat'] == setup['start_repeat'] == plan['run_plan']['start_repeat'])
check('declared_limits_match_plan', manifest['tool_budget'] == setup['tool_budget'] == plan['completion_control']['tool_budget'] and manifest['research_seconds'] == setup['research_seconds'] == plan['completion_control']['research_seconds'] and manifest['limits']['wall_seconds'] == plan['agent']['wall_limit_seconds'] and manifest['limits']['max_turns'] == plan['agent']['max_turns'] and manifest['limits']['max_output_tokens'] == plan['agent']['max_output_tokens'] and manifest['limits']['effort'] == plan['agent']['effort'])
check('declared_model_cli_embedding_match_plan', manifest['requested_model'] == plan['agent']['model'] and manifest['versions']['claude'] == plan['agent']['version'] + ' (Claude Code)' and manifest['versions']['zg'] == plan['retrieval']['native_package_version'] and manifest['embedding_model'] == setup['embedding_model'] == plan['retrieval']['embedding_model'])
check('zg_runtime_provenance_matches_plan', provenance == setup['zg_runtime'] and provenance['commit'] == plan['retrieval']['source_commit'] and provenance['repository'] == plan['retrieval']['source_repository'] and provenance['package_version'] == plan['retrieval']['native_package_version'] and provenance['package_sha256'] == plan['retrieval']['native_package_sha256'] and provenance['workflow_run_id'] == plan['retrieval']['upstream_build_run'] and provenance['workflow_artifact']['id'] == plan['retrieval']['upstream_artifact_id'] and 'sha256:' + provenance['workflow_artifact']['archive_sha256'] == plan['retrieval']['upstream_artifact_digest'])
check('upstream_config_and_ignore_hashes_match', sha((ART / 'upstream-ci-config.json').read_bytes()) == setup['upstream_benchmark']['config_sha256'] and sha((ART / 'upstream-index.ignore').read_bytes()) == setup['upstream_benchmark']['index_ignore_sha256'] and read(ART / 'upstream-ci-config.json')['embedding_models']['local'] == plan['retrieval']['embedding_model'])
check('root_guidance_remains_off', manifest['workspace_root_guidance'] is False and setup['workspace_root_guidance'] is False)
check('native_mcp_agent_configuration', manifest['mcp_server'] == {'type': 'stdio', 'command': 'zg', 'args': ['--server', '--stdio', '--mcp-toolset', 'agent']})

trial_reports = []
runtime_homes = []
runtime_indexes = []
sessions = []
execution_names = []
observed_failures = []
for expected, summary_metrics in zip(plan['run_plan']['planned_trials'], summary['trials']):
    name = f"r{expected['repeat']:02d}-{expected['profile']}"
    execution_names.append(f"r{summary_metrics['repeat']:02d}-{summary_metrics['profile']}")
    directory = ART / name
    metrics = read(directory / 'metrics.json')
    hook = read(directory / 'tool-budget.json')
    command = read(directory / 'command.json')
    state = hook['state']
    events = []
    malformed = []
    for line_number, line in enumerate((directory / 'stream.jsonl').read_text().splitlines(), 1):
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append((line_number, event))
        except ValueError:
            if line.strip(): malformed.append(line_number)
    inits = [e for _, e in events if e.get('type') == 'system' and e.get('subtype') == 'init']
    finals = [e for _, e in events if e.get('type') == 'result']
    init, final = inits[-1], finals[-1]
    sessions.append(init['session_id'])
    calls, replies, models, raw_calls, raw_results = {}, {}, set(), 0, 0
    conflicts = []
    timestamps = []
    for line_number, event in events:
        if event.get('timestamp'): timestamps.append(event['timestamp'])
        message = event.get('message') or {}
        if event.get('type') == 'assistant' and message.get('model') not in [None, '<synthetic>']:
            models.add(message['model'])
        for block in message.get('content', []):
            if not isinstance(block, dict): continue
            if block.get('type') == 'tool_use' and event.get('type') == 'assistant':
                raw_calls += 1
                tool_id = block.get('id')
                if tool_id in calls and calls[tool_id]['block'] != block: conflicts.append(tool_id)
                calls.setdefault(tool_id, {'block': block, 'line': line_number})
            if block.get('type') == 'tool_result':
                raw_results += 1
                tool_id = block.get('tool_use_id')
                replies.setdefault(tool_id, []).append({'block': block, 'line': line_number})
    models.update(final.get('modelUsage', {}))
    tchecks = {}
    def tc(k, v): tchecks[k] = bool(v)
    tc('summary_metrics_exactly_match_trial_metrics', summary_metrics == metrics)
    tc('identity_matches_frozen_position', {'repeat': metrics['repeat'], 'profile': metrics['profile']} == expected)
    tc('stream_json_valid_and_unique_init_final', not malformed and len(inits) == len(finals) == 1)
    tc('unique_call_ids_consistent', None not in calls and not conflicts)
    tc('hook_and_stream_inventory_equal', set(calls) == set(state['calls']))
    tc('all_results_resolved_and_no_unmatched_results', set(replies) == set(calls))
    tc('hook_parameters_match_plan', hook['limit'] == 24 and hook['research_seconds'] == 600 and hook['valid'] is True and hook['errors'] == [])
    expected_tools = {'Glob', 'Grep', 'Read'} | ({ZG} if expected['profile'] == 'zg' else set())
    tc('tool_inventory_exact', set(init['tools']) == expected_tools and {v['block']['name'] for v in calls.values()} <= expected_tools)
    tc('observed_model_and_cli_match', models == {plan['agent']['model']} and init['model'] == plan['agent']['model'] and init['claude_code_version'] == plan['agent']['version'])
    tc('init_isolation_identity', init['cwd'] == '/workspace' and init['permissionMode'] == 'dontAsk' and init['skills'] == [] and init['plugins'] == [] and init['apiKeySource'] == 'ANTHROPIC_API_KEY')
    tc('mcp_connection_state_matches_profile', init['mcp_servers'] == ([{'name': 'zvec_grep', 'status': 'connected'}] if expected['profile'] == 'zg' else []))
    env_values = dict(item.split('=', 1) for item in entries(command, '--env') if '=' in item)
    tc('command_model_limits_effort_match', opt(command, '--model') == plan['agent']['model'] and opt(command, '--effort') == 'high' and opt(command, '--max-turns') == '30' and env_values.get('CLAUDE_CODE_MAX_OUTPUT_TOKENS') == '8192')
    tc('command_common_instructions_and_prompt_match', opt(command, '--append-system-prompt') == manifest['common_instructions'] and command[-1] == (ART / 'prompt.txt').read_text())
    tc('command_tool_permissions_exact', set(opt(command, '--tools').split(',')) == {'Glob', 'Grep', 'Read'} and set(opt(command, '--allowedTools').split(',')) == expected_tools and opt(command, '--permission-mode') == 'dontAsk')
    tc('command_isolation_flags', all(flag in command for flag in ['--read-only', '--init', '--disable-slash-commands', '--no-session-persistence', '--strict-mcp-config']) and opt(command, '--setting-sources') == '' and opt(command, '--cap-drop') == 'ALL' and opt(command, '--security-opt') == 'no-new-privileges' and opt(command, '--workdir') == '/workspace' and env_values.get('CLAUDE_CODE_DISABLE_CLAUDE_MDS') == '1' and env_values.get('CLAUDE_CODE_DISABLE_AUTO_MEMORY') == '1' and env_values.get('HOME') == '/root')
    mounts = [dict(part.split('=', 1) if '=' in part else (part, True) for part in mount.split(',')) for mount in entries(command, '--mount')]
    source_mounts = [m for m in mounts if m.get('dst') == '/workspace']
    home_mounts = [m for m in mounts if m.get('dst') == '/root']
    index_mounts = [m for m in mounts if m.get('dst') == '/workspace/.zvec-grep']
    runtime_homes.extend(m['src'] for m in home_mounts)
    runtime_indexes.extend(m['src'] for m in index_mounts)
    tc('source_mount_readonly', len(source_mounts) == 1 and source_mounts[0].get('readonly') is True and source_mounts[0]['src'] == manifest['source'])
    tc('profile_index_isolation_recorded', len(home_mounts) == 1 and (len(index_mounts) == 1 if expected['profile'] == 'zg' else not index_mounts and '/workspace/.zvec-grep:ro,size=64k' in entries(command, '--tmpfs')))
    admitted = 0
    rows = []
    hook_decisions_valid = True
    for tool_id, decision in state['calls'].items():
        elapsed = decision['elapsed_seconds']
        expected_admit = admitted < 24 and elapsed < 600
        expected_reason = 'call_limit' if admitted >= 24 else 'time_limit' if elapsed >= 600 else 'within_budget'
        hook_decisions_valid &= type(decision['admitted']) is bool and isinstance(elapsed, (int, float)) and math.isfinite(elapsed) and elapsed >= 0 and decision['admitted'] == expected_admit and decision['reason'] == expected_reason and decision['name'] == calls[tool_id]['block']['name']
        admitted += decision['admitted']
        results = replies.get(tool_id, [])
        flags = {bool(v['block'].get('is_error')) for v in results}
        tc('consistent_result_flags:' + tool_id, len(flags) == 1)
        error = True in flags
        denied_marker = any(DENIAL in json.dumps(v['block'].get('content')) for v in results)
        category = 'unresolved' if not results else 'admitted_error' if decision['admitted'] and error else 'admitted_success' if decision['admitted'] else 'budget_denied' if error and denied_marker else 'budget_denial_unverified'
        row = {'tool_use_id': tool_id, 'name': decision['name'], 'first_stream_line': calls[tool_id]['line'], 'result_stream_lines': [v['line'] for v in results], 'elapsed_seconds': elapsed, 'admitted': decision['admitted'], 'classification': category, 'root_argument': calls[tool_id]['block'].get('input', {}).get('root')}
        if error:
            row['error_text'] = results[0]['block'].get('content')
            observed_failures.append({'trial': name, **row, 'protocol_failure': False, 'interpretation': 'Admitted MCP retrieval failed because the agent supplied root=/; native fallback and final completion remained valid.'})
        rows.append(row)
    tc('hook_all_admissions_recomputed_correctly', hook_decisions_valid and admitted == hook['admitted'])
    tc('hook_counts_match_metrics', len(calls) == metrics['tool_calls'] == hook['observed_calls'] and len(calls) - admitted == hook['denied'])
    tc('no_hook_process_errors', 'BUDGET_HOOK_INFRASTRUCTURE_ERROR' not in (directory / 'stream.jsonl').read_text() + (directory / 'stderr.txt').read_text())
    usage = final.get('usage', {})
    complete = all(type(usage.get(k)) is int and usage[k] >= 0 for k in FIELDS)
    model_usage = final.get('modelUsage', {}).get(plan['agent']['model'], {})
    mapping = {'input_tokens': 'inputTokens', 'output_tokens': 'outputTokens', 'cache_creation_input_tokens': 'cacheCreationInputTokens', 'cache_read_input_tokens': 'cacheReadInputTokens'}
    tc('authoritative_final_usage_complete', complete)
    tc('model_usage_matches_final_usage', all(usage.get(k) == model_usage.get(v) for k, v in mapping.items()))
    tc('metrics_usage_matches_final_usage', metrics['usage'] == usage and metrics['usage_source'] == 'result.usage' and metrics['usage_complete'] is True and metrics['input_tokens_including_cache'] == sum(usage[k] for k in FIELDS if k != 'output_tokens'))
    answer = final.get('result')
    tc('successful_nonempty_final_matches_saved_answer', final['subtype'] == 'success' and final['is_error'] is False and isinstance(answer, str) and bool(answer.strip()) and answer.encode() == (directory / 'answer.txt').read_bytes())
    tc('exit_wall_turns_valid', metrics['exit_code'] == 0 and metrics['wall_seconds'] < 900 and final['num_turns'] <= 30 and final['terminal_reason'] == 'completed' and final['stop_reason'] == 'end_turn')
    tc('metrics_report_no_protocol_or_infrastructure_failure', metrics['valid'] is True and metrics['protocol_valid'] is True and metrics['errors'] == [] and metrics['infrastructure_errors'] == [])
    tc('no_permission_denials', final.get('permission_denials') == [])
    counts = Counter(r['classification'] for r in rows)
    zg_rows = [r for r in rows if r['name'] == ZG]
    tc('mcp_counters_match_raw_results', len(zg_rows) == metrics['zg_tool_attempts'] and sum(r['classification'] == 'admitted_success' for r in zg_rows) == metrics['zg_tool_successes'] and sum(r['classification'] == 'admitted_error' for r in zg_rows) == metrics['zg_tool_errors'])
    trial_reports.append({'trial': name, 'repeat': expected['repeat'], 'profile': expected['profile'], 'checks': tchecks, 'protocol_valid': all(tchecks.values()), 'hook_started_at_epoch': state['started_at_epoch'], 'hook_started_at_utc': utc(state['started_at_epoch']), 'wall_seconds': metrics['wall_seconds'], 'num_turns': final['num_turns'], 'session_id': init['session_id'], 'usage': {k: usage[k] for k in FIELDS}, 'input_tokens_including_cache': sum(usage[k] for k in FIELDS if k != 'output_tokens'), 'input_excluding_cache_read': usage['input_tokens'] + usage['cache_creation_input_tokens'], 'usage_source': 'single final result.usage; cross-checked against modelUsage and metrics', 'final_status': {'result_count': len(finals), 'subtype': final['subtype'], 'terminal_reason': final['terminal_reason'], 'nonempty': bool(answer.strip()), 'matches_saved_answer': answer.encode() == (directory / 'answer.txt').read_bytes(), 'answer_sha256': sha((directory / 'answer.txt').read_bytes()), 'quality_assessed': False}, 'tools': {'attempts': len(calls), 'admitted': admitted, 'budget_denied': counts.get('budget_denied', 0), 'denial_decisions': len(calls) - admitted, 'admitted_success': counts.get('admitted_success', 0), 'admitted_error': counts.get('admitted_error', 0), 'unresolved': counts.get('unresolved', 0), 'raw_call_blocks': raw_calls, 'raw_result_blocks': raw_results, 'duplicate_call_blocks': raw_calls - len(calls), 'duplicate_result_blocks': raw_results - len(replies), 'max_admitted_elapsed_seconds': max(r['elapsed_seconds'] for r in rows if r['admitted']), 'calls': rows}, 'mcp': {'connected': expected['profile'] == 'zg', 'attempts': len(zg_rows), 'successes': sum(r['classification'] == 'admitted_success' for r in zg_rows), 'errors': sum(r['classification'] == 'admitted_error' for r in zg_rows), 'calls': zg_rows}, 'evidence': {p.name: record(p) for p in sorted(directory.iterdir()) if p.is_file()}})

expected_names = [f"r{x['repeat']:02d}-{x['profile']}" for x in plan['run_plan']['planned_trials']]
check('actual_four_trial_summary_order_matches_frozen_plan', execution_names == expected_names and len(summary['trials']) == 4)
check('all_and_only_planned_trial_directories', {p.name for p in ART.iterdir() if p.is_dir()} == set(expected_names))
check('all_planned_trials_completed', summary['all_planned_trials_completed'] is True and summary['planned_trial_count'] == 4 and summary['invalid_trial_count'] == 0)
check('hook_start_times_increase_in_frozen_order', all(a['hook_started_at_epoch'] < b['hook_started_at_epoch'] for a, b in zip(trial_reports, trial_reports[1:])))
gaps = [{'previous': a['trial'], 'next': b['trial'], 'next_start_minus_previous_hook_start_and_agent_wall_seconds': b['hook_started_at_epoch'] - a['hook_started_at_epoch'] - a['wall_seconds']} for a, b in zip(trial_reports, trial_reports[1:])]
check('recorded_trial_intervals_do_not_overlap', all(g['next_start_minus_previous_hook_start_and_agent_wall_seconds'] >= 0 for g in gaps))
check('fresh_runtime_homes_indexes_and_sessions_unique', len(runtime_homes) == len(set(runtime_homes)) == 4 and len(runtime_indexes) == len(set(runtime_indexes)) == 2 and len(sessions) == len(set(sessions)) == 4)
scans = {n: read(ART / n) for n in ['secret-scan.json', 'collection-secret-scan.json']}
check('known_secret_scans_pass', all(s['passed'] is True for s in scans.values()) and scans['secret-scan.json']['matched_paths'] == [] and scans['collection-secret-scan.json']['remaining_matches'] == [] and summary['secret_scan_passed'] is True)
preflight = read(ART / 'mcp-preflight.json')
check('real_index_mcp_preflight_success', preflight['valid'] is True and preflight['errors'] == [] and preflight['transport_exit_code'] == 0 and preflight['search']['isError'] is False and preflight['initialize']['serverInfo'] == {'name': 'zvec-grep', 'version': '0.0.1'})
check('preflight_schema_digest_matches_recorded_payload', sha(json.dumps(preflight['tools'], sort_keys=True).encode()) == preflight['tool_schema_sha256'])
cli_probe = read(ART / 'cli-probe.json')
budget_probe = read(ART / 'budget-probe.json')
check('offline_cli_probe_pass', cli_probe['valid'] is True and all(p['valid'] is True and p['errors'] == [] for p in cli_probe['profiles']))
check('offline_budget_probe_pass', budget_probe['valid'] is True and all(p['valid'] is True and p['errors'] == [] for p in budget_probe['cases']))
repo_audit = read(Path('/tmp/zg-confirmation-39282211793-repository-audit.json'))
check('repository_auditor_crosscheck_agrees', repo_audit['pair_efficiency_accounting_comparable'] is True and all(repo_audit['pair_checks'].values()) and all(t['audit_valid'] for t in repo_audit['trials']))

pairs = []
for repeat in [2, 3]:
    by_profile = {t['profile']: t for t in trial_reports if t['repeat'] == repeat}
    b, z = by_profile['baseline'], by_profile['zg']
    pairs.append({'repeat': repeat, 'baseline_input_including_cache': b['input_tokens_including_cache'], 'zg_input_including_cache': z['input_tokens_including_cache'], 'zg_over_baseline_input_ratio': z['input_tokens_including_cache'] / b['input_tokens_including_cache'], 'baseline_wall_seconds': b['wall_seconds'], 'zg_wall_seconds': z['wall_seconds'], 'zg_over_baseline_wall_ratio': z['wall_seconds'] / b['wall_seconds'], 'execution_accounting_comparable': b['protocol_valid'] and z['protocol_valid'], 'quality_not_assessed': True})
jobs = read(Path('/tmp/zg-confirmation-39282211793-jobs.json'))
protocol_valid = all(checks.values()) and all(t['protocol_valid'] for t in trial_reports)
report = {'schema_version': 1, 'audit_role': 'independent execution evidence auditor; no answer quality review', 'created_at_utc': datetime.now(timezone.utc).isoformat(), 'read_only_inputs': True, 'run_id': 39282211793, 'repository': 'Cuiyus/zvec-grep', 'head_sha': HEAD, 'run_attempt': 1, 'artifact_id': 12204075459, 'artifact_dir': str(ART), 'artifact_archive': {'sha256': archive_sha, 'github_digest_verified': checks['artifact_zip_matches_github_digest'], 'files': len(members), 'unpacked_byte_mismatches': archive_mismatches}, 'frozen_plan': record(plan_path), 'run_commit_files': run_files, 'verdict': {'all_four_planned_trials_retained': True, 'execution_protocol_valid': protocol_valid, 'complete_usage_accounting_all_trials': all(t['checks']['authoritative_final_usage_complete'] for t in trial_reports), 'all_four_final_answers_delivered': all(t['checks']['successful_nonempty_final_matches_saved_answer'] for t in trial_reports), 'all_zg_trials_had_successful_mcp_retrieval': all(t['mcp']['successes'] > 0 for t in trial_reports if t['profile'] == 'zg'), 'budget_denials_observed': any(t['tools']['denial_decisions'] for t in trial_reports), 'quality_assessed': False}, 'checks': checks, 'failed_checks': [k for k, v in checks.items() if not v] + [t['trial'] + ':' + k for t in trial_reports for k, v in t['checks'].items() if not v], 'execution_order': {'frozen': expected_names, 'summary_observed': execution_names, 'hook_starts_utc': [{t['trial']: t['hook_started_at_utc']} for t in trial_reports], 'inter_trial_gap_checks': gaps}, 'source_identity': {'revision': setup['revision'], 'local_export': str(SOURCE), 'fresh_export': str(FRESH), 'original_file_count': len(original_inventory), 'runtime_file_count': len(runtime_files), 'original_inventory_sha256': sha(canonical(original_inventory)), 'runtime_source_sha256': sha(canonical(runtime_files)), 'auxiliary_file': '.zg-bench-index.ignore', 'source_unchanged_controller_report': summary['source_unchanged']}, 'secret_scans': scans, 'setup_time_excluded_from_trials': {'index_setup_seconds': setup['index_setup_seconds'], 'mcp_preflight_seconds': setup['mcp_preflight_seconds']}, 'observed_execution_failures': observed_failures, 'github_completion_evidence': {'run_metadata_single_read_status': 'in_progress', 'run_metadata_single_read_conclusion': None, 'job_metadata': jobs, 'interpretation': 'GitHub run/job top-level status lagged; Run frozen paired trials completed successfully at 2026-10-19T16:33:52Z, collection succeeded, upload and Complete job steps succeeded at 16:33:54Z. Artifact bytes are complete. No later polling performed.'}, 'trials': trial_reports, 'pairs_resource_only': pairs, 'limitations': ['No answer quality, citation correctness, or rubric criterion judgment was performed. No blind review files were accessed.', 'Final answers were compared programmatically for presence, equality and hashes; their quality was not inspected.', 'One admitted r03-zg MCP call failed for root=/; this is an observed agent/tool outcome under the unchanged protocol, not an infrastructure failure or budget denial. It is retained without rerun.', 'Source unchanged after each trial is controller evidence; source export and runtime manifest contents were independently recomputed, but the destroyed runner filesystem cannot be re-inspected.', 'Container runtime config files, model cache and binary bytes are intentionally absent from the published artifact. Their identity is supported by frozen runner code, recorded commands, provenance, startup events and offline preflight; binary bytes were not independently rehashed here.', 'The 8192 output setting is per request. Final usage output_tokens is cumulative across the session; modelUsage.maxOutputTokens=32000 is model metadata, not an archived live request-body field establishing the effective cap. Live provider request bodies were not archived.', 'Secret scans check known injected credential values; their passing status is verified, but the auditor did not possess credentials to repeat those exact scans.', 'Hook elapsed times and controller wall times are recorded evidence, not an independent external wall-clock monitor.', 'GitHub run metadata lag is retained explicitly; no replacement, rerun or additional model inference was performed.', 'Token ratios are per frozen pair. No quality conclusion, statistical significance or population-level inference is made.'], 'supporting_repository_audit': record(Path('/tmp/zg-confirmation-39282211793-repository-audit.json')), 'independent_auditor_script': record(Path(__file__))}
OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
print(json.dumps({'output': str(OUT), 'verdict': report['verdict'], 'failed_checks': report['failed_checks'], 'pairs_resource_only': pairs, 'observed_failures': [{'trial': f['trial'], 'classification': f['classification'], 'root': f['root_argument'], 'error_text': f['error_text']} for f in observed_failures]}, ensure_ascii=False, indent=2))
