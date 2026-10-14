"""Use the frozen run command with the diagnostic adapter selected explicitly."""
from zg_bench import cli, runner

runner._ZVEC_AGENT_IMPORT_PATHS["opencode"] = "zg_bench.agents.traced_opencode:TracedZvecOpenCode"

if __name__ == "__main__":
    raise SystemExit(cli.main())
