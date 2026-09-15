"""Bind a continuation's permitted code changes to committed Git file bytes.

The source configuration is data. This gate checks an explicit file allowlist;
it does not claim to replace review of the changes' behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


PROTOCOL = "workspace-qa-qoder-native-install-v3"
FROZEN_NAMES = {"lock.json", "selection.json", "Dockerfile", "native_session.py",
                "native_index.py", "seed_cache.py", "uv.lock"}


def git(repo: Path, *arguments: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, check=False)
    if result.returncode:
        raise ValueError("Git evidence could not be read: " + result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def canonical_path(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(character) < 32 or character in "*?[]" for character in value)
            or PurePosixPath(value).is_absolute()
            or any(part in {"", ".", "..", ".git"} for part in value.split("/"))):
        raise ValueError("Allowed and changed paths must be exact canonical relative filenames")
    return value


def frozen_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return parts[-1] in FROZEN_NAMES or "runtime" in parts


def require_clean_tracked(repo: Path) -> None:
    # Untracked downloaded artifacts are expected in CI. Porcelain includes both
    # staged and unstaged changes, even when they cancel in a diff against HEAD.
    if git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=no"):
        raise ValueError("Continuation code review requires unchanged tracked working files and index")


def blob_sha256(repo: Path, commit: str, path: str) -> str | None:
    entry = git(repo, "ls-tree", "-z", "--full-tree", commit, "--", path)
    if not entry:
        return None
    entries = entry.rstrip(b"\0").split(b"\0")
    if len(entries) != 1:
        raise ValueError("Changed path did not resolve to one committed file")
    header, name = entries[0].split(b"\t", 1)
    mode, kind, _ = header.split(b" ")
    if name.decode("utf-8") != path or kind != b"blob" or mode not in {b"100644", b"100755"}:
        raise ValueError("Changed paths must be regular committed files, not symlinks or submodules")
    raw = git(repo, "show", "--no-ext-diff", "--no-textconv", f"{commit}:{path}")
    return hashlib.sha256(raw).hexdigest()


def build_review(repo: Path, config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("Continuation source configuration must be an object")
    base = config.get("source_commit")
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("Continuation source_commit must be a complete lowercase Git commit SHA")
    run_id = config.get("source_run_id")
    if not ((type(run_id) is int and run_id > 0)
            or (isinstance(run_id, str) and re.fullmatch(r"[1-9][0-9]*", run_id))):
        raise ValueError("Continuation source_run_id must identify the original GitHub run")
    protocols = [config[key] for key in ("protocol", "source_protocol") if key in config]
    if not protocols or any(value != PROTOCOL for value in protocols):
        raise ValueError("Continuation source must use the native installation protocol")
    allowed = config.get("allowed_changed_paths")
    if not isinstance(allowed, list) or not allowed:
        raise ValueError("Continuation requires an explicit nonempty allowed_changed_paths list")
    allowed = [canonical_path(path) for path in allowed]
    if len(set(allowed)) != len(allowed):
        raise ValueError("Continuation allowed_changed_paths contains duplicates")
    repo = Path(git(Path(repo), "rev-parse", "--show-toplevel").decode("utf-8").strip())
    require_clean_tracked(repo)
    head = git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    if git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").decode("ascii").strip() != base:
        raise ValueError("Continuation source_commit did not resolve to the configured commit")
    raw_paths = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z", f"{base}..{head}", "--")
    paths = [canonical_path(value.decode("utf-8")) for value in raw_paths.rstrip(b"\0").split(b"\0")] if raw_paths else []
    forbidden = [path for path in paths if frozen_path(path)]
    if forbidden:
        raise ValueError("Continuation changed frozen experiment files: " + ", ".join(forbidden))
    unexpected = sorted(set(paths) - set(allowed))
    if unexpected:
        raise ValueError("Continuation changed files outside the exact allowlist: " + ", ".join(unexpected))
    changed = [{"path": path, "before_sha256": blob_sha256(repo, base, path),
                "after_sha256": blob_sha256(repo, head, path)} for path in sorted(paths)]
    require_clean_tracked(repo)
    if git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip() != head:
        raise ValueError("Repository HEAD changed while building continuation code evidence")
    return {"base_commit": base, "head_commit": head, "status": "verified", "changed_files": changed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.source_config.read_text(encoding="utf-8"))
        review = build_review(args.repo, config)
        repo = Path(git(args.repo, "rev-parse", "--show-toplevel").decode("utf-8").strip())
        output = args.output.resolve()
        if output.is_relative_to(repo) and git(repo, "ls-files", "-z", "--", output.relative_to(repo).as_posix()):
            raise ValueError("Code review output cannot overwrite a tracked file")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, UnicodeError) as exc:
        parser.exit(2, f"continuation code review failed: {exc}\n")
    print(f"Verified {len(review['changed_files'])} allowed changed files from {review['base_commit']} to {review['head_commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
