"""Aggregate version reports with explicit labels and the full planned denominator."""
import json
import os
from pathlib import Path
import sys
from zg_bench.reports.aggregate import aggregate_reports
from zg_bench.reports.render import render_report
from zg_bench.version_e2e import DATA, EXPERIMENT

reports, output = map(Path, sys.argv[1:])
expected = [t['task_id'] for t in json.loads((DATA / 'selection.json').read_text())['tasks']]
report = aggregate_reports(reports_root=reports, output_dir=output, expected=expected, allow_missing=True)
report['comparison_kind'] = 'node022-vs-rust-main'
report['comparison_labels'] = {'baseline': 'Node 0.2.2+zg', 'zvec-grep': 'Rust a09cd1236eee+zg'}
report['experiment'] = EXPERIMENT
report['model'] = os.environ['BENCH_MODEL']
report['embedding'] = os.environ['BENCH_EMBEDDING']
(output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
text = ('# Node 0.2.2 vs Rust main E2e\n\n'
        f"Model: {report['model']}; embedding: {report['embedding']}; five repetitions per version and task.\n\n"
        'Every table cell is **Node / Rust / Rust − Node**. Both versions receive zg; the legacy baseline slot means Node.\n\n'
        + render_report(report))
(output / 'report.md').write_text(text)
with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
    summary.write(text)
if not report['gate']['passed']:
    raise SystemExit('Incomplete version comparison; missing tasks are listed in the report')
