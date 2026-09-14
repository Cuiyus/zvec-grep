#!/usr/bin/env python3
"""Freeze original full-persona workspaces; never select corpus by gold dependencies."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from urllib.parse import quote
import zipfile

HERE = Path(__file__).resolve().parent
PERSONA_ROOTS = {
    "Researcher": "Research_Workdir",
    "Backend Developer": "BackendDeveloper_Workdir",
    "Product Manager": "ProductManager_Workdir",
    "Operations Manager": "OperationsManager_Workdir",
    "Logistics Manager": "LogisticsManager_Workdir",
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_relative(name: str) -> Path:
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or "\x00" in name:
        raise ValueError(f"Unsafe dataset path: {name!r}")
    return Path(*p.parts)


def download(url: str, destination: Path, expected: str | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and expected and digest(destination) == expected:
        return
    temporary = destination.with_suffix(destination.suffix + ".partial")
    subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location", "--retry", "3",
                    "--connect-timeout", "30", "--max-time", "300", "--output", str(temporary), url], check=True)
    if expected and digest(temporary) != expected:
        temporary.unlink()
        raise ValueError(f"Dataset checksum mismatch: {destination.name}")
    temporary.replace(destination)


class RangeArchive(io.RawIOBase):
    """Bounded-memory ZIP reader with validated, cached 16 MiB HTTP ranges.

    Fetch whole persona contents from the immutable 18.9 GB archive without
    downloading unrelated personas. ZIP CRC validates every extracted member;
    full-archive SHA is provenance only, not claimed as locally verified.
    """
    def __init__(self, url: str, size: int, block_size: int = 16 * 1024 * 1024):
        self.url, self.size, self.block_size, self.position = url, size, block_size, 0
        self.cache: dict[int, bytes] = {}
        self.fetched = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        if position < 0:
            raise ValueError("Negative archive offset")
        self.position = position
        return position

    def read(self, size=-1):
        end = self.size if size < 0 else min(self.size, self.position + size)
        result = bytearray()
        while self.position < end:
            start = self.position // self.block_size * self.block_size
            if start not in self.cache:
                stop = min(start + self.block_size, self.size) - 1
                with tempfile.TemporaryDirectory(prefix="workspace-range-") as tmp:
                    body, headers = Path(tmp) / "body", Path(tmp) / "headers"
                    # Separate URL cache keys prevent proxies reusing a different range.
                    url = self.url + f"?range_block={start}"
                    subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location",
                                    "--retry", "3", "--connect-timeout", "30", "--max-time", "300",
                                    "--range", f"{start}-{stop}", "--dump-header", str(headers),
                                    "--output", str(body), url], check=True)
                    ranges = re.findall(r"content-range:\s*bytes (\d+)-(\d+)/(\d+)", headers.read_text(), re.I)
                    if not ranges or tuple(map(int, ranges[-1])) != (start, stop, self.size):
                        raise RuntimeError("Archive server did not honor exact byte range")
                    block = body.read_bytes()
                if len(block) != stop - start + 1:
                    raise RuntimeError("Truncated archive range")
                if len(self.cache) >= 2:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[start] = block
                self.fetched += len(block)
                if self.fetched // self.block_size % 32 == 1:
                    print(json.dumps({"phase": "workspace_download", "downloaded_bytes": self.fetched}), flush=True)
            count = min(end - self.position, len(self.cache[start]) - (self.position - start))
            result.extend(self.cache[start][self.position - start:self.position - start + count])
            self.position += count
        return bytes(result)


def extract_persona(archive: zipfile.ZipFile, persona: str, destination: Path) -> dict:
    expected = PERSONA_ROOTS[persona]
    # Upstream archives have used both filesys_cn/<persona> and <persona> roots.
    prefixes = {"/".join(i.filename.split("/")[:i.filename.split("/").index(expected) + 1]) + "/"
                for i in archive.infolist() if expected in i.filename.split("/")}
    if len(prefixes) != 1:
        raise ValueError(f"Expected one original persona root {expected}; observed {sorted(prefixes)}")
    prefix = next(iter(prefixes))
    all_members = [i for i in archive.infolist() if i.filename.startswith(prefix) and not i.is_dir()]
    # Raw personas contain multi-GB nested Git databases. They are version-control
    # metadata, not working files; exclude uniformly before freezing our own repo.
    excluded_vcs = [i for i in all_members if ".git" in PurePosixPath(i.filename[len(prefix):]).parts]
    members = sorted((i for i in all_members if i not in excluded_vcs),
                     key=lambda item: item.header_offset)
    if not members:
        raise ValueError("Persona workspace is empty")
    required = sum(i.file_size for i in members)
    if shutil.disk_usage(destination.parent).free < required * 2 + 2 * 1024**3:
        raise RuntimeError(f"Insufficient disk for full corpus and git snapshot: {required} source bytes")
    manifest = []
    for item in members:
        relative = safe_relative(item.filename[len(prefix):])
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"Unsupported symlink in corpus: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(item) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
        manifest.append({"path": relative.as_posix(), "size_bytes": item.file_size,
                         "zip_crc32": item.CRC, "sha256": digest(target)})
    return {"persona": persona, "archive_prefix": prefix, "file_count": len(members),
            "source_bytes": required, "files": manifest,
            "excluded_vcs_metadata_files": len(excluded_vcs),
            "excluded_vcs_metadata_bytes": sum(i.file_size for i in excluded_vcs)}


def prepare(lock: dict, task_id: str, destination: Path, upstream: Path) -> dict:
    task = next(task for task in lock["tasks"] if task["task_id"] == task_id)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Preparation destination must be new/empty")
    destination.mkdir(parents=True, exist_ok=True)
    source = destination / "source"
    task_dir = destination / "tasks" / task_id
    dataset = lock["dataset"]
    base = f"https://huggingface.co/datasets/{dataset['repo']}/resolve/{dataset['revision']}/task_lite_clean_cn/{task_id}/"
    download(base + "metadata.json", task_dir / "metadata.json", task["metadata_sha256"])
    for item in task["inputs"]:
        download(base + quote(item["stored_relpath"]), task_dir / safe_relative(item["stored_relpath"]), item["sha256"])
    observed_commit = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    if observed_commit != lock["upstream"]["commit"]:
        raise ValueError("Upstream task patch revision mismatch")
    # Apply reviewed official corrections (notably task 127's eight Python files).
    import importlib.util
    module_spec = importlib.util.spec_from_file_location("workspace_task_patches", upstream / "evaluation/src/task_patches.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    patched = module.apply_task_patches(destination / "tasks", kind="lite", language="cn")
    metadata = json.loads((task_dir / "metadata.json").read_text())
    if metadata["output_files"] != [task["answer_filename"]]:
        raise ValueError("Locked output file differs from source metadata")
    (destination / "question.txt").write_text(metadata["task"], encoding="utf-8")
    workspace = lock["workspace"]
    url = f"https://huggingface.co/datasets/{workspace['repo']}/resolve/{workspace['revision']}/{workspace['archive']}"
    with RangeArchive(url, workspace.get("size_bytes", 18861940415)) as remote:
        with zipfile.ZipFile(remote) as archive:
            manifest = extract_persona(archive, task["persona"], source)
        manifest["downloaded_bytes"] = remote.fetched
    # Verify every provided task input exists unchanged in the FULL workspace.
    # Never create a gold-driven subset or silently add relevance hints.
    source_paths = list(source.rglob("*"))
    matched = []
    for item in task["inputs"]:
        candidates = [p for p in source_paths if p.is_file() and p.name == item["filename"]]
        matches = [p for p in candidates if digest(p) == item["sha256"]]
        if not matches:
            raise RuntimeError(f"Original full workspace lacks matching input: {item['filename']}")
        matched.append({"filename": item["filename"], "paths": [p.relative_to(source).as_posix() for p in matches]})
    for argv in (["init", str(source)], ["-C", str(source), "add", "--force", "."],
                 ["-C", str(source), "-c", "user.name=Workspace QA", "-c", "user.email=benchmark@localhost",
                  "commit", "--quiet", "-m", f"Frozen original CN persona workspace {workspace['revision']}"]):
        subprocess.run(["git", *argv], check=True, stdout=subprocess.DEVNULL)
    (source / ".zvec-grep").mkdir(exist_ok=True)
    manifest.update(task_id=task_id, dataset=dataset, workspace=workspace, upstream=lock["upstream"],
                    corpus_policy="all_original_persona_working_files_excluding_git_metadata", matched_inputs=matched,
                    source_metadata_sha256=task["metadata_sha256"], effective_metadata_sha256=digest(task_dir / "metadata.json"),
                    official_task_patches=patched, archive_sha256_verification="not_full_download; revision + member CRC + file SHA256",
                    answer_filename=task["answer_filename"], slice=task["slice"])
    (destination / "dataset-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=HERE / "data/lock.json")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(json.loads(args.lock.read_text()), args.task_id, args.output.resolve(), args.upstream.resolve())
    print(json.dumps({k: result[k] for k in ("task_id", "file_count", "source_bytes", "answer_filename")}))


if __name__ == "__main__":
    main()
