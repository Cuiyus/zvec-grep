"""Write the q001 repetition result without selecting a favorable rating view."""
import datetime
import json
from pathlib import Path

B = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
A = B / 'native-zvec-q001-repeat-v1-audit'
D = A / 'final-analysis'
R = W / 'benchmarks/native-claude-qa-pilot/results/20261020-zvec-q001-repeat-v1'


def load(path):
    return json.loads(path.read_text())


primary = load(D / 'analysis-report.json')
primary_case = load(D / 'analysis-input.json')['cases'][0]
views = {}
for label in ['initial-grades', 'scorer-c7', 'consistency-c7']:
    report = load(D / f'analysis-report-{label}.json')
    case = load(D / f'analysis-input-{label}.json')['cases'][0]
    estimates = report['repeated_estimates']['q001']
    profiles = report['statistics']['task_statistics'][0]['profiles']
    row = {'rating_view': report['rating_view'], 'diagnostic_only': True,
           'statistical_criterion_met_only': estimates['statistical_criterion_met_only'],
           'raw_comparison': estimates['raw']['comparison'], 'trimmed_comparison': estimates['score_trimmed']['comparison'],
           'raw_intervals': estimates['raw']['intervals'], 'trimmed_intervals': estimates['score_trimmed']['intervals'],
           'interval_width_change_pct': estimates['interval_width_change_pct'], 'profiles': {}}
    for name, profile in case['profiles'].items():
        trials = profile['trials']
        retained = set(profiles[name]['filter']['retained_trial_indexes'])
        row['profiles'][name] = {
            'raw_scores_in_trial_order': [t['judge']['total'] for t in trials],
            'raw_pass_count': sum(t['quality_pass'] for t in trials), 'raw_count': 10,
            'trimmed_pass_count': sum(t['quality_pass'] for t in trials if t['trial_index'] in retained), 'trimmed_count': 8,
            'raw_means': estimates['raw']['profiles'][name], 'trimmed_means': estimates['score_trimmed']['profiles'][name],
            'raw_dispersion': report['raw_dispersion']['q001'][name], 'trimmed_dispersion': profiles[name]['dispersion'],
            'filter': profiles[name]['filter'],
            'all_raw_source_and_execution_flags': [{'trial_index': t['trial_index'], 'quality_pass': t['quality_pass'],
                'execution_and_scope_valid': t['execution_and_scope_valid'], 'reported_metric_eligible': t['reported_metric_eligible'],
                'strict_source_scope_valid': t['source_scope']['strict_source_scope_valid'],
                'required_zg_retrieval_gate_pass': t['source_scope']['required_zg_retrieval_gate_pass'],
                'retained_in_this_diagnostic_trim': t['trial_index'] in retained} for t in trials]}
    views[label] = row
quality = {}
unknown_scores = []
for name, profile in primary_case['profiles'].items():
    trials = profile['trials']
    known_pass = sum(t['quality_pass'] is True for t in trials)
    unknown_pass = sum(t['quality_pass'] is None for t in trials)
    quality[name] = {'planned': 10, 'known_pass': known_pass, 'known_fail': 10 - known_pass - unknown_pass,
                     'unknown': unknown_pass, 'pass_count_bounds': [known_pass, known_pass + unknown_pass]}
    for t in trials:
        if t['judge']['total'] is None:
            unknown_scores.append({'profile': name, 'trial_index': t['trial_index'], 'anonymous_answer_id': t['anonymous_answer_id'],
                                   'score_bounds_not_observations': t['score_bounds_not_observations'],
                                   'quality_pass_bounds_not_observations': t['quality_pass_bounds_not_observations']})
result = {'schema_version': 1, 'reported_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'task_id': 'q001', 'issue': 'https://github.com/alibaba/zvec/issues/594',
          'run_id': 39364155852, 'run_url': 'https://github.com/Cuiyus/zvec-grep/actions/runs/39364155852',
          'execution_head': 'aa230d91eb426eb295cf95fe0fa2d80d52f588e2',
          'planned_and_retained_executions': {'baseline': 10, 'zvec-grep': 10},
          'solver_retries_or_replacements': 0, 'historical_or_cancelled_samples_pooled': False,
          'conclusion': 'Reported input saving is repeatable on this fixed case. Score-tail trimming does not improve uncertainty uniformly and widens every reported resource-comparison interval in all three diagnostic rating views. Quality preservation and stable end-to-end improvement are not established. This phase does not establish a stable favorable benchmark.',
          'complete_primary_quality_statistics_available': False, 'unknowns_zero_filled_or_dropped': False,
          'primary_quality_counts_with_unknowns': quality, 'primary_unknown_scores': unknown_scores,
          'raw_resources_independent_of_rating_interpretation': primary['raw_resource_comparisons_rating_invariant'],
          'source_scope_summary': primary['source_scope_summary'], 'diagnostic_views': views,
          'stable_reported_input_with_quality_preserved_on_q001': False,
          'score_trim_policy': 'Independently remove exactly one minimum and one maximum score per profile; ties by original trial_index then position. Use the identical retained8 tuples for every metric. All raw failures/unknowns remain visible.',
          'bootstrap': {'resamples': 10000, 'seed': 20261020, 'unit': 'whole baseline/zg repetition pair',
                        'trim': 'recompute score tails in each resample', 'confidence': 0.95, 'percentile': 'linear_n_minus_1'},
          'task_level_mad': 'unavailable_insufficient_tasks; one fixed task, not ten tasks',
          'measurement_scope': primary['measurement_scope'], 'time_scope': primary['time_scope'],
          'primary_validity_note': '3 strict scope failures, 2 unknown strict scopes, and one zg execution without zg retrieval prevent the unchanged whole-cohort validity gate from passing. Trimming never repairs these.',
          'blinding_note': 'Original/clarification/consistency artifacts persisted before explicit analysis mapping read. Reviewer exposure is self-reported, existing reviewer reuse and prior metadata exposure disclosed. Parent inspected labelled execution/source/resource data and is not claimed blind.'}
with (R / 'summary.json').open('x') as stream:
    json.dump(result, stream, indent=2, allow_nan=False)
    stream.write('\n')
raw = views['initial-grades']
base, zg = (raw['profiles'][p]['raw_means'] for p in ['baseline', 'zvec-grep'])
resource_rows = []
for label, field, metric in [('Reported input tokens', 'input_tokens', 'input_reduction_pct'),
                             ('Agent execution seconds', 'agent_wall_seconds', 'agent_time_reduction_pct'),
                             ('Full outer-slot seconds', 'outer_slot_seconds', 'outer_slot_reduction_pct')]:
    ci = raw['raw_intervals'][metric]
    resource_rows.append(f'| {label} | {base[field]:,.1f} | {zg[field]:,.1f} | {raw["raw_comparison"][metric]:.2f}% [{ci["lower"]:.2f}%, {ci["upper"]:.2f}%] |')
diagnostic_rows = []
dispersion_rows = []
selection_rows = []
for label, view in views.items():
    b, z = (view['profiles'][p] for p in ['baseline', 'zvec-grep'])
    diagnostic_rows.append(f'| {label} | {b["raw_pass_count"]}/10 / {z["raw_pass_count"]}/10 | {b["trimmed_pass_count"]}/8 / {z["trimmed_pass_count"]}/8 | {view["trimmed_comparison"]["input_reduction_pct"]:.2f}% | +{view["interval_width_change_pct"]["input_reduction_pct"]:.2f}% | +{view["interval_width_change_pct"]["agent_time_reduction_pct"]:.2f}% | {view["trimmed_comparison"]["outer_slot_reduction_pct"]:.2f}% |')
    for name in ['baseline', 'zvec-grep']:
        p = view['profiles'][name]
        d0, d1 = p['raw_dispersion'], p['trimmed_dispersion']
        dispersion_rows.append(f'| {label} | {name} | {d0["judge"]["value"]:.3f} → {d1["judge"]["value"]:.3f} | {d0["input_tokens"]["value"]*100:.2f}% → {d1["input_tokens"]["value"]*100:.2f}% | {d0["agent_wall_seconds"]["value"]*100:.2f}% → {d1["agent_wall_seconds"]["value"]*100:.2f}% |')
        selections = ', '.join(f'r{x["trial_index"]:02d} ({x["direction"]}, score {x["score"]})' for x in p['filter']['excluded_trials'])
        selection_rows.append(f'| {label} | {name} | {selections} |')
readme = '''# zvec q001: ten baseline and ten zg executions

The fixed study of 20 executions is complete. **It does not establish a stable favorable benchmark.** Reported input saving is repeatable on this case, but full end-to-end speed and quality preservation are not established. Score-tail filtering did not make the resource comparisons more precise.

[Fork run 39364155852](https://github.com/Cuiyus/zvec-grep/actions/runs/39364155852) completed all 10 pairs, attempt 1, at commit `aa230d91eb426eb295cf95fe0fa2d80d52f588e2`. Baseline 10 and zg 10 are all retained; no retry, replacement, early favorable stop, historical pooling or cancelled-run pooling. This replaces the cancelled campaign of 60 executions with the user-requested q001-only cohort.

## Raw 10 resources per profile

The following resource values and paired 95% bootstrap intervals are identical under all three rating interpretations, because all 20 original executions are used.

| Metric | Baseline mean | zg mean | zg reduction and 95% interval |
|---|---:|---:|---:|
''' + '\n'.join(resource_rows) + '''

Input includes Claude-reported uncached input, cache reads and cache writes; output is separate. These are not provider bills. Agent execution excludes setup. Full outer slot includes fresh setup/indexing, whose extra time largely offsets agent-stage savings here. All 20 reported usage aggregates matched complete persisted session trees.

## Quality interpretation remains partly unknown

The original rubric's C7 requires comparing known documents and vectors after a fresh reopen. Initial grading sometimes accepted successful known-pk fetches or related checks without full payload comparison. The original scorer and an independent reviewer rechecked all 20 anonymous answers before mapping read. They agreed on 17 C7 points and disagreed on 3. The primary view preserves 3 unknown exact scores and 2 unknown pass decisions; it does not zero-fill or drop them. A complete primary score/score-trimmed statistic is therefore unavailable.

Under the agreed portion and remaining original-rule ambiguity, baseline has **5/10** quality passes; zg has **2–4/10**. The initial ratings, scorer's C7 clarification and consistency review are retained separately. Their complete numeric statistics below are diagnostics, not interchangeable primary results. C1–C6 and core-citation/critical-contradiction gates were preserved; no relaxed C6 sensitivity replaced the original rule.

## Raw 10 versus score-trimmed 8 diagnostics

Each profile loses exactly one minimum and one maximum score. All metrics use the same retained tuples; ties use trial index then position. Selection is independent between profiles. Intervals resample whole pairs 10000 times (seed 20261020) and recompute trimming each draw. Positive reduction means zg used less; negative means zg used more.

| Rating interpretation | Raw quality B / zg | Trimmed quality B / zg | Trimmed input reduction | Input CI width change | Agent-time CI width change | Trimmed full-slot reduction |
|---|---|---|---:|---:|---:|---:|
''' + '\n'.join(diagnostic_rows) + '''

The input-saving interval widens by 18%–34% after filtering across these interpretations. Agent-time and full-slot intervals also widen in every interpretation. No interpretation meets even the statistical-only unchanged favorable criterion; scope validity separately fails. Trimming is not evidence that underlying solver randomness changed.

| Interpretation | Profile | Score sample SD raw → trimmed | Input CV raw → trimmed | Agent-time CV raw → trimmed |
|---|---|---:|---:|---:|
''' + '\n'.join(dispersion_rows) + '''

| Interpretation | Profile | Score-tail exclusions |
|---|---|---|
''' + '\n'.join(selection_rows) + '''

One fixed task cannot enable the task-level MAD filter. Ten repetitions are not ten different tasks. The analyzer's `all_tasks` field duplicates q001's single-task estimate and is not an additional sample.

## Validity and provenance

All 10 pairs passed 63 execution/accounting checks each. Ten hosted runners, 20 distinct root sessions and 40 runner-qualified fresh volume identities were verified; no child sessions were present. All 1232 frozen source files stayed unchanged. Independent source review covered 739 actual tool calls; parent verification rehashed 1667 per-pair evidence references.

Strict source scope is true for 15 executions, false for 3 and unknown for 2. The known violations are r08 in both profiles and r09 baseline. r02/r04 baseline have outside fallback branches whose execution could not be determined. r08 zg read an outside status file containing `0`; no outside code/reference-answer facts were observed, and no project-test execution or answer pollution is inferred. r07 zg used no zg retrieval, including through Bash. Every failure/unknown remains visible even if a score trim removes the corresponding row. Only 14/20 pass all source and required-retrieval gates, so whole-cohort strict validity fails.

This is custom zvec QA from [closed issue 594](https://github.com/alibaba/zvec/issues/594), frozen source `ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d`, Claude Code 2.1.212 plus Qwen3.8-max. Native harness and Rust build are pinned to `a424e0ce06868dc9ad8f142f57de59b8f9b58699`. Preflight observed upstream main `49de68563a7d3da9b408dd64277a8a51e3dd0fc6`, whose Rust subtree `de02030056a3224012725799d845bf2be9edae2b` was identical. No cross-task, provider-billing or general model-population claim follows.

Original source-grounded model grades, clarification and consistency artifacts were saved before explicit analysis mapping read. Reviewer reuse and exposure are self-reported, not independently certified from complete tool histories. Root had labelled execution/source/resource evidence and is not claimed blind. Exact anonymous answers, unblinding receipts and all disagreements remain retained.

See [summary.json](summary.json), [analysis](analysis/), [grading](grading/), [anonymous packet](anonymous-grading-packet/), [per-pair evidence](pairs/), [source cohort checks](source-cohort-evidence/) and [final execution/CI evidence](execution-cohort-evidence/). Large original artifact ZIPs and duplicate install packages are retained locally with exact hashes and API/byte-verification receipts; they are omitted from Git. Every other archived byte is covered by the phase manifest. The preserved first-five status is an earlier immutable partial snapshot, not this phase's final status.
'''
with (R / 'README.md').open('x') as stream:
    stream.write(readme)
print(json.dumps({'summary_written': True, 'primary_quality_counts': quality, 'diagnostic_views': 3,
                  'input_CI_width_change_pct': [v['interval_width_change_pct']['input_reduction_pct'] for v in views.values()]}))
