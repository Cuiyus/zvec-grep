"""Recompute immutable CI artifacts offline, without any model calls."""
import json
from pathlib import Path
import sys

from zg_bench.version_summary import include_all_cases, render_version_report

source, output = map(Path, sys.argv[1:])
report = include_all_cases(json.loads(source.read_text()))
output.mkdir(parents=True, exist_ok=True)
(output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
(output / 'report.md').write_text(render_version_report(report))
