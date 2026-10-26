"""Credential setup and allowlisted evidence export for manual GitHub Actions runs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from .config import DEFAULT_ARTIFACTS_DIR, DEFAULT_CONFIG_PATH, load_config

SECRET_ENV = ("OPENAI_API_KEY", "CODEX_AUTH_JSON", "ZVEC_GREP_API_KEY", "HF_TOKEN")
AUTH_FIELDS = {"access_token", "refresh_token", "id_token", "OPENAI_API_KEY"}


def _auth_secrets(value: object) -> set[str]:
    if not isinstance(value, dict):
        return set()
    result = set()
    for key, item in value.items():
        if key in AUTH_FIELDS and isinstance(item, str) and item:
            result.add(item)
        result.update(_auth_secrets(item))
    return result


def configure_auth(home: Path) -> None:
    if not os.environ.get("ZVEC_GREP_API_KEY"):
        raise ValueError("QWEN_EMBEDDING_API_KEY Actions secret is required")
    if not os.environ.get("ZVEC_GREP_ENDPOINT"):
        raise ValueError("QWEN_EMBEDDING_ENDPOINT Actions variable is required")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    auth_json = os.environ.get("CODEX_AUTH_JSON", "")
    if bool(api_key) == bool(auth_json):
        raise ValueError("configure exactly one of OPENAI_API_KEY or CODEX_AUTH_JSON")
    # Fail before the expensive full-corpus preparation if the pinned CLI is incompatible.
    help_text = subprocess.run(
        ["codex", "exec", "--help"], check=True, capture_output=True, text=True
    ).stdout
    for flag in ("--ignore-rules", "--ephemeral", "--output-schema"):
        if flag not in help_text:
            raise ValueError(f"installed Codex does not support required flag {flag}")
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.chmod(0o700)
    if auth_json:
        try:
            auth = json.loads(auth_json)
        except ValueError:
            raise ValueError(
                "CODEX_AUTH_JSON must be a valid auth.json object"
            ) from None
        if not isinstance(auth, dict) or not _auth_secrets(auth):
            raise ValueError("CODEX_AUTH_JSON contains no supported credentials")
        for value in _auth_secrets(auth):
            # GitHub masks the whole JSON secret automatically, but not each nested token.
            escaped = (
                value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            )
            print(f"::add-mask::{escaped}")
        auth_path = home / "auth.json"
        auth_path.touch(mode=0o600, exist_ok=False)
        auth_path.write_text(json.dumps(auth), encoding="utf-8")
    else:
        subprocess.run(
            [
                "codex",
                "-c",
                'cli_auth_credentials_store="file"',
                "login",
                "--with-api-key",
            ],
            input=api_key + "\n",
            text=True,
            check=True,
            env={**os.environ, "CODEX_HOME": str(home)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        (home / "auth.json").chmod(0o600)


def verify_model() -> None:
    """Make one small model call before paying for a full-corpus index build."""
    config = load_config()
    with tempfile.TemporaryDirectory(prefix="browse-comp-plus-preflight-") as directory:
        response = Path(directory) / "response.txt"
        result = subprocess.run(
            [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--model",
                config.run.model,
                "-c",
                f'model_reasoning_effort="{config.run.reasoning_effort}"',
                "-c",
                'web_search="disabled"',
                "-C",
                directory,
                "-o",
                str(response),
                "Reply exactly OK. Do not use tools.",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if (
            result.returncode != 0
            or not response.is_file()
            or not response.read_text().strip()
        ):
            raise ValueError(
                f"Codex preflight failed; verify credential access to {config.run.model} "
                "and service availability before preparing the corpus"
            )


def secret_values(home: Path, profile_auth: tuple[Path, ...] = ()) -> set[bytes]:
    values = {os.environ[name] for name in SECRET_ENV if os.environ.get(name)}
    auth_json = os.environ.get("CODEX_AUTH_JSON")
    if auth_json:
        values.update(_auth_secrets(json.loads(auth_json)))
    # Include refreshed account tokens, not just the original Actions secret.
    for auth_path in (home / "auth.json", *profile_auth):
        if auth_path.is_file():
            values.update(
                _auth_secrets(json.loads(auth_path.read_text(encoding="utf-8")))
            )
    values.update(json.dumps(value)[1:-1] for value in tuple(values))
    return {value.encode() for value in values if value}


def _contains_secret(path: Path, secrets: set[bytes]) -> bool:
    overlap = max((len(value) for value in secrets), default=1) - 1
    tail = b""
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            window = tail + chunk
            if any(value in window for value in secrets):
                return True
            tail = window[-overlap:] if overlap else b""
    return False


def _copy_evidence(source: Path, destination: Path, secrets: set[bytes]) -> None:
    if source.is_symlink():
        raise ValueError("refusing to export a symlink in benchmark evidence")
    if not source.exists():
        return
    if source.is_dir():
        for child in sorted(source.iterdir()):
            _copy_evidence(child, destination / child.name, secrets)
        return
    if not stat.S_ISREG(source.stat().st_mode):
        raise ValueError("refusing to export a non-regular benchmark file")
    if source.name == "auth.json" or _contains_secret(source, secrets):
        raise ValueError(
            "credential material detected; benchmark evidence will not be uploaded"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def export_evidence(
    artifacts: Path, output: Path, home: Path, manifest: Path, plan: Path | None = None
) -> None:
    if output.exists():
        raise ValueError("evidence output must be a new directory")
    # A CLI token refresh may replace a profile's auth symlink with a regular file.
    profile_auth = tuple((artifacts / "runs").glob("*/profiles/*/codex-home/auth.json"))
    secrets = secret_values(home, profile_auth)
    output.mkdir(parents=True)
    try:
        _copy_evidence(DEFAULT_CONFIG_PATH, output / "benchmark.toml", secrets)
        if not manifest.is_file():
            raise ValueError("candidate manifest is missing")
        _copy_evidence(manifest, output / "candidate-manifest.json", secrets)
        for name in ("dataset.json", "corpus.json", "index.json", "runtime.json"):
            _copy_evidence(artifacts / "state" / name, output / "state" / name, secrets)
        _copy_evidence(artifacts / "logs", output / "logs", secrets)
        runs = artifacts / "runs"
        if runs.is_symlink():
            raise ValueError("refusing to export a symlink in benchmark evidence")
        for run in sorted(runs.iterdir()) if runs.is_dir() else []:
            if run.is_symlink():
                raise ValueError("refusing to export a symlink in benchmark evidence")
            if not run.is_dir():
                continue
            # Never traverse profiles (auth symlinks), runtime, or the corpus.
            for name in ("run.json", "report", "cases", "evaluation", "shards"):
                _copy_evidence(run / name, output / "runs" / run.name / name, secrets)
        identity = {
            "candidate_commit": os.environ.get("BROWSE_CANDIDATE_COMMIT", ""),
            "harness_commit": os.environ.get("BROWSE_HARNESS_COMMIT", ""),
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "case_id": os.environ.get("BROWSE_CASE", ""),
        }
        if plan:
            from .artifacts import sha256_file

            identity["plan_sha256"] = sha256_file(plan)
            _copy_evidence(plan, output / "plan.json", secrets)
        (output / "ci.json").write_text(
            json.dumps(identity, indent=2) + "\n", encoding="utf-8"
        )
    except BaseException:
        shutil.rmtree(output)
        raise
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("## BrowseComp-Plus CI\n\n")
            summary.write(f"Candidate: `{identity['candidate_commit']}`  \n")
            summary.write(f"Harness: `{identity['harness_commit']}`\n\n")
            reports = sorted(output.glob("runs/*/report/summary.md"))
            if not reports:
                summary.write(
                    "No paired report was produced. See failed steps and diagnostic artifacts.\n"
                )
            summary.writelines(
                report.read_text(encoding="utf-8") + "\n" for report in reports
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("auth")
    commands.add_parser("preflight")
    export = commands.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--candidate-manifest", type=Path, required=True)
    export.add_argument("--plan", type=Path)
    args = parser.parse_args()
    try:
        home = Path(os.environ["CODEX_HOME"])
        if args.command == "auth":
            configure_auth(home)
        elif args.command == "preflight":
            verify_model()
        else:
            export_evidence(
                DEFAULT_ARTIFACTS_DIR,
                args.output,
                home,
                args.candidate_manifest,
                args.plan,
            )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"BrowseComp-Plus CI failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
