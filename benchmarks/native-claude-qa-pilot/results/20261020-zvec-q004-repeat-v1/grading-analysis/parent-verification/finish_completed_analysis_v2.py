"""Finish the already-unblinded q004 analysis without rerunning or replacing data."""
import datetime,hashlib,json,math
from pathlib import Path
from collections import Counter
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');H=W/'benchmarks/native-claude-qa-pilot';A=B/'native-q004-repeat-v1-audit'
OUT=B/'native-q004-repeat-v1-final-analysis'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def write(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2,allow_nan=False);f.write('\n')
receipt=load(OUT/'unblinding-receipt.json');assert receipt['both_original_reports_and_comparison_completed_before_mapping_read'] is True
main=load(OUT/'analysis-input.json');diagnostic=load(OUT/'analysis-input-original-primary-diagnostic.json');consensus=main['cases'][0]['profiles']
report=load(OUT/'analysis-original-primary-diagnostic.json');resources=load(A/'raw-resource-statistics.json')
assert report['analysis_input_sha256']==sha(OUT/'analysis-input-original-primary-diagnostic.json')
assert report['bootstrap']['resamples']==10000 and report['bootstrap']['seed']==20261020
assert not (OUT/'analysis-report.json').exists()
raw=report['repeated_estimates']['q004']['raw']
for key in ('input_reduction_pct','agent_time_reduction_pct','outer_slot_reduction_pct'):
    assert math.isclose(raw['comparison'][key],resources['comparison'][key],rel_tol=1e-12,abs_tol=1e-10)
    assert all(raw['intervals'][key][k]==resources['intervals'][key][k] for k in ('status','valid_resamples','expected_resamples'))
    assert all(math.isclose(raw['intervals'][key][k],resources['intervals'][key][k],rel_tol=1e-12,abs_tol=1e-10) for k in ('lower','upper'))
quality={};validity={}
for name,profile in consensus.items():
    rows=profile['trials'];passes=sum(r['quality_pass'] is True for r in rows);unknown=sum(r['quality_pass'] is None for r in rows)
    quality[name]={'known_pass':passes,'known_fail':sum(r['quality_pass'] is False for r in rows),'unknown':unknown,'possible_pass_count_not_an_observed_rate':[passes,passes+unknown],
        'score_mean_bounds_not_observed_means':[sum(r['score_bounds_not_observations'][j] for r in rows)/10 for j in (0,1)],
        'original_primary_diagnostic_passes':sum(r['original_primary_quality_pass'] for r in rows)}
    validity[name]={'strict_scope_counts':dict(Counter('true' if r['source_scope']['strict_source_scope_valid'] is True else 'false' if r['source_scope']['strict_source_scope_valid'] is False else 'unknown' for r in rows)),
        'scope_and_required_retrieval_passes':sum(r['source_scope']['scope_and_retrieval_gate_passed'] is True for r in rows)}
unknown_scores=sum(r['judge']['total'] is None for p in consensus.values() for r in p['trials'])
unknown_quality=sum(r['quality_pass'] is None for p in consensus.values() for r in p['trials'])
assert unknown_scores==unknown_quality==4
main_report={'schema_version':1,'status':'complete_execution_and_review_with_unavailable_complete_primary_quality_and_score_trim_statistics',
    'planned_solver_executions':20,'retained_solver_executions':20,'primary_unknown_scores':unknown_scores,'primary_unknown_quality_decisions':unknown_quality,
    'reason':'Four C4.2 interpretations remain unresolved after the two independent source-grounded reviews. No complete primary score-tail selection or quality comparison is invented.',
    'quality_by_profile':quality,'validity_by_profile':validity,'raw_all20_resource_comparison':resources['comparison'],'raw_all20_resource_intervals':resources['intervals'],
    'diagnostic_original_primary':{'path':str(OUT/'analysis-original-primary-diagnostic.json'),'sha256':sha(OUT/'analysis-original-primary-diagnostic.json'),
        'raw':report['repeated_estimates']['q004']['raw'],'score_trimmed':report['repeated_estimates']['q004']['score_trimmed'],
        'interval_width_change_pct':report['repeated_estimates']['q004']['interval_width_change_pct']},
    'all_execution_scope_and_reported_metrics_valid':False,'stable_reported_input_with_quality_preserved_on_q004':False,
    'task_mad':'unavailable_insufficient_tasks: one fixed task; repetitions are not distinct tasks',
    'scope':'One fixed architecture QA case and pinned native stack. Scope failures, unknowns and missing relevant retrieval remain visible independently of score filtering. No population, provider-billing or favorable valid benchmark-set claim.',
    'analysis_input_sha256':sha(OUT/'analysis-input.json'),'method_sha256':sha(Path(__file__))}
write(OUT/'analysis-report.json',main_report)
write(OUT/'method-bindings.json',{'files':{str(p):sha(p) for p in (Path(__file__),H/'architecture_repeat_analysis_v1.py',H/'repeat_analysis_v1.py',H/'robust_statistics_v1.py')},'statistical_controls_changed':False,'invalid_trials_rehabilitated_by_trimming':False})
print(json.dumps({'retained_solver_trials':20,'quality_by_profile':quality,'validity_by_profile':validity,'diagnostic_filter_interval_width_change_pct':main_report['diagnostic_original_primary']['interval_width_change_pct'],'stable_favorable_valid_benchmark':False}))
