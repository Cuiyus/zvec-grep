"""Retain tri-state source findings and required zg retrieval for all scheduled rows."""
import datetime
import hashlib
import json
from pathlib import Path

A = Path(__file__).resolve().parent
rows = []
references = {}
for index in range(1, 11):
    key = f'q001-r{index:02d}'
    path = A / 'source-reviews' / key / 'report.json'
    data = path.read_bytes()
    report = json.loads(data)
    assert report['pair_id'] == key and report['source_file_count'] == 1232
    assert report['quality_graded'] is False and report['resources_compared'] is False
    references[str(path)] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    for profile in sorted(report['profiles'], key=lambda p: p['profile']):
        fields = ['strict_source_scope_valid', 'factual_evidence_scope_valid',
                  'external_content_observed', 'external_code_or_answer_facts_observed',
                  'source_unchanged', 'relevant_correct_workspace_zg_retrieval',
                  'unresolved_evidence', 'tool_count', 'root_tool_count', 'child_tool_count']
        row = {'pair_key': key, 'trial_index': index, 'profile': profile['profile'],
               **{field: profile[field] for field in fields}}
        row['strict_scope_gate_pass'] = (row['strict_source_scope_valid'] is True and not row['unresolved_evidence'])
        row['required_zg_retrieval_gate_pass'] = (True if row['profile'] == 'baseline'
                                                else row['relevant_correct_workspace_zg_retrieval'] is True)
        row['all_source_and_required_retrieval_gates_pass'] = (
            row['strict_scope_gate_pass'] and row['factual_evidence_scope_valid'] is True
            and row['source_unchanged'] is True and row['required_zg_retrieval_gate_pass'])
        rows.append(row)
assert len(rows) == len({(r['profile'], r['trial_index']) for r in rows}) == 20
summary = {'strict_true': sum(r['strict_source_scope_valid'] is True for r in rows),
           'strict_false': sum(r['strict_source_scope_valid'] is False for r in rows),
           'strict_unknown': sum(r['strict_source_scope_valid'] is None for r in rows),
           'source_and_required_retrieval_gate_pass': sum(r['all_source_and_required_retrieval_gates_pass'] for r in rows),
           'successful_relevant_zg_retrieval_executions': sum(r['relevant_correct_workspace_zg_retrieval'] is True for r in rows if r['profile'] == 'zvec-grep'),
           'external_file_content_observed': sum(r['external_content_observed'] is True for r in rows),
           'external_code_or_answer_facts_observed': sum(r['external_code_or_answer_facts_observed'] is True for r in rows),
           'all_sources_unchanged': all(r['source_unchanged'] is True for r in rows),
           'root_tool_calls_reviewed': sum(r['root_tool_count'] for r in rows),
           'child_tool_calls_reviewed': sum(r['child_tool_count'] for r in rows)}
report = {'schema_version': 1, 'recorded_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'run_id': 39364155852, 'execution_head': 'aa230d91eb426eb295cf95fe0fa2d80d52f588e2',
          'quality_or_resource_benefit_evaluated': False, 'private_assignment_accessed': False,
          'unknown_handling': 'Preserve null and specific unresolved evidence. Unknown never passes a gate.',
          'outside_content_interpretation': 'r08 zg read an outside status file containing 0. No outside code/reference-answer facts were observed; no project test, grader execution or answer pollution is inferred.',
          'summary': summary, 'rows': rows, 'references': references}
with (A / 'source-scope-evidence.json').open('x') as stream:
    json.dump(report, stream, indent=2, allow_nan=False)
    stream.write('\n')
print(json.dumps(summary))
