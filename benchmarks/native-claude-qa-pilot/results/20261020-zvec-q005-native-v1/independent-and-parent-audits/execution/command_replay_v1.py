"""Frozen command builder only; no native execution, network, files or credentials."""
from pathlib import Path
from types import SimpleNamespace
import live

def expected_command(profile, selected, remote_selected, remote_live, main_root, base, logical_output):
    with live.live_environment(base, profile, main_root, require_credentials=False):
        runner = live.pilot.native(main_root)
        suite = runner.load_suite(selected / 'suite.yaml')
        cache = SimpleNamespace(
            zvec_grep_package='/tmp/zg-bench-zvec-grep.tgz' if profile == 'zvec-grep' else None,
            zvec_grep_package_sha256=live.PACKAGE_SHA if profile == 'zvec-grep' else None)
        command = live.build_command(runner, suite, profile, selected, logical_output, cache, base['embedding']['endpoint'])
    runtime_root = Path(remote_selected).parents[1]
    mapping = {str(selected): str(remote_selected), str(logical_output): str(remote_live),
               str(main_root): str(runtime_root / 'qa-main/benchmarks/swe-qa-bench')}
    for i, value in enumerate(command):
        for local, recorded in sorted(mapping.items(), key=lambda x: -len(x[0])):
            value = value.replace(local, recorded)
        command[i] = value
    return command
