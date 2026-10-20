import datetime,hashlib,importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot');P=W/'benchmarks/native-claude-qa-pilot';M=B/'upstream-main/benchmarks/swe-qa-bench';R=B/'native-current-tasks-repeat-v1-audit';None
sys.path[:0]=[str(P),str(W/'benchmarks/opencode-qa-pilot'),str(M)]
import live
BASE=live.pilot.load(P/'live-plan-v2.json')
def expected_command(profile,selected,remote_selected,remote_live):
 output=Path('/tmp/native-repeat-readonly-command-replay').resolve()
 with live.live_environment(BASE,profile,M,require_credentials=False):
  runner=live.pilot.native(M);suite=runner.load_suite(selected/'suite.yaml')
  cache=SimpleNamespace(zvec_grep_package='/tmp/zg-bench-zvec-grep.tgz' if profile=='zvec-grep' else None,zvec_grep_package_sha256=live.PACKAGE_SHA if profile=='zvec-grep' else None)
  command=live.build_command(runner,suite,profile,selected,output,cache,BASE['embedding']['endpoint'])
 runtime_root=Path(remote_selected).parent if selected.name=='prepared' else Path(remote_selected).parents[1]
 mapping={str(selected):str(remote_selected),str(output):str(remote_live),str(M):str(runtime_root/'qa-main/benchmarks/swe-qa-bench')}
 for i,value in enumerate(command):
  for a,b in sorted(mapping.items(),key=lambda x:-len(x[0])):value=value.replace(a,b)
  command[i]=value
 return command
