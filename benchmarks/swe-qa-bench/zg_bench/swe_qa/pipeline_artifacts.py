"""Small CI boundaries for the E2E-first benchmark: current-run inputs and redaction."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

GROUPS = {
    ("opencode", "custom-openai/glm-5.2"): "opencode-glm52",
    ("opencode", "custom-openai/qwen3.8-max"): "opencode-qwen38max",
    ("qodercli", "qwen3.8-max"): "qoder-qwen38max",
}
CREDENTIAL_ENVS = ("GLM_API_KEY", "OPENAI_API_KEY", "QODER_PERSONAL_ACCESS_TOKEN", "QWEN_API_KEY")


def scan_credentials(root: Path) -> None:
    """Fail before upload without disclosing either the value or matching content."""
    secrets = [os.environ[key].encode() for key in CREDENTIAL_ENVS if os.environ.get(key)]
    if not root.is_dir():
        raise ValueError("Artifact directory is missing")
    if not secrets:
        return
    overlap = max(len(secret) for secret in secrets) - 1
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        tail = b""
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                chunk = tail + block
                if any(secret in chunk for secret in secrets):
                    raise ValueError("Credential detected in artifact; upload blocked")
                tail = chunk[-overlap:] if overlap else b""


def assemble(downloaded: Path, output: Path, *, run_id: str, commit: str) -> dict:
    """Reject stale runs and duplicate group attempts instead of choosing a winner."""
    if not run_id or not commit:
        raise ValueError("Current run and commit identities are required")
    if output.exists():
        raise ValueError("Assembled directory must be new")
    found = {}
    for directory in sorted(downloaded.iterdir()):
        manifest_path = directory / "manifest.json"
        if not directory.is_dir() or not manifest_path.is_file():
            raise ValueError("Downloaded group lacks its E2E manifest")
        manifest = json.loads(manifest_path.read_text())
        ci = manifest.get("ci_identity", {})
        if str(ci.get("GITHUB_RUN_ID")) != run_id or ci.get("GITHUB_SHA") != commit:
            raise ValueError("Downloaded E2E group belongs to another run or commit")
        group = GROUPS.get((manifest.get("agent"), manifest.get("model")))
        if group is None:
            raise ValueError("Unexpected Agent/Model group")
        if group in found:
            raise ValueError("Multiple E2E attempts for one group; do not silently replace trials")
        plan = json.loads((directory / "plan.json").read_text())
        trials = plan.get("trials", [])
        if len(trials) != 10 or any(sum(t.get("profile") == p for t in trials) != 5
                                   for p in ("baseline", "zvec-grep")):
            raise ValueError("Each group must retain all ten planned E2E trials")
        found[group] = directory
    if set(found) != set(GROUPS.values()):
        raise ValueError("The current run must contain all three planned groups")
    output.mkdir(parents=True)
    for group, directory in found.items():
        shutil.copytree(directory, output / group, symlinks=True)
    result = {"protocol": "readonly-qa-v6", "run_id": run_id, "commit": commit,
              "groups": sorted(found), "planned_e2e_trials": 30,
              "selection": "all three groups; no replacement or success filtering"}
    (output / "assembly-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--root", required=True, type=Path)
    collect = sub.add_parser("assemble")
    collect.add_argument("--downloaded", required=True, type=Path)
    collect.add_argument("--output", required=True, type=Path)
    collect.add_argument("--run-id", required=True)
    collect.add_argument("--commit", required=True)
    args = parser.parse_args(argv)
    if args.command == "scan":
        scan_credentials(args.root)
    else:
        print(json.dumps(assemble(args.downloaded, args.output, run_id=args.run_id, commit=args.commit)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
