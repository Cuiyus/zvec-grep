"""Freeze all 20 native reported resource rows without quality or assignment access."""
import datetime
import hashlib
import json
from pathlib import Path

A = Path(__file__).resolve().parent
rows = []
references = {}
for index in range(1, 11):
    key = f'q001-r{index:02d}'
    path = A / 'reviews' / key / 'execution-review.json'
    data = path.read_bytes()
    audit = json.loads(data)
    assert audit['passed'] and audit['check_count'] == 63 and not audit['failed_checks']
    assert audit['run_id'] == 39364155852 and audit['head_sha'] == 'aa230d91eb426eb295cf95fe0fa2d80d52f588e2'
    references[str(path)] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    for profile in sorted(audit['profiles'], key=lambda p: p['profile']):
        usage = profile['primary_usage']
        times = profile['timing_seconds']
        assert usage == profile['strict_usage'] and profile['reported_metric_eligible'] is True
        rows.append({'pair_key': key, 'trial_index': index, 'profile': profile['profile'],
                     'answer_sha256': profile['answer_sha256'], 'root_session_id': profile['root_session_id'],
                     'execution_valid': audit['execution_valid'], 'reported_metric_eligible': True,
                     'primary_usage': usage, 'strict_usage': profile['strict_usage'],
                     'timing_seconds': times, 'tool_count': profile['tool_count'],
                     'native_zg_setup_timing': profile['native_zg_setup_timing'],
                     'metrics': {'input_tokens': usage['input_tokens'], 'output_tokens': usage['output_tokens'],
                                 'uncached_input_tokens': usage['uncached_input_tokens'],
                                 'cache_read_tokens': usage['cache_read_tokens'],
                                 'cache_write_tokens': usage['cache_write_tokens'],
                                 'tool_calls': profile['tool_count'],
                                 'agent_wall_seconds': times['agent_execution'],
                                 'native_run_seconds': times['native_run'],
                                 'outer_slot_seconds': times['outer_slot'],
                                 'agent_setup_seconds': times['agent_setup']}})
assert len(rows) == len({(r['profile'], r['trial_index']) for r in rows}) == 20
report = {'schema_version': 1, 'frozen_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'run_id': 39364155852, 'execution_head': 'aa230d91eb426eb295cf95fe0fa2d80d52f588e2',
          'measurement_scope': 'Claude per-model reported input = uncached input + cache read + cache write; output separate. Complete persisted root/child records corroborate usage. This is not provider billing.',
          'time_scope': 'agent_wall_seconds is Harbor agent_execution for PR261 dispersion; native_run and full outer_slot (including fresh zg indexing/setup) are separate measurements.',
          'quality_graded': False, 'private_assignment_accessed': False, 'all_raw_rows_retained': True,
          'rows': rows, 'references': references}
destination = A / 'reported-resource-evidence.json'
with destination.open('x') as stream:
    json.dump(report, stream, indent=2, allow_nan=False)
    stream.write('\n')
print(json.dumps({'resource_rows': len(rows), 'output': str(destination), 'quality_or_benefit_evaluated': False}))
