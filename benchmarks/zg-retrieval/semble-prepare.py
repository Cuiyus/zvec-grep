"""Prepare the unmodified Semble package/model/index outside the search corpus."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import sys
from pathlib import Path


class EvidenceError(Exception):
    """The evaluator could not validate product output."""


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["model", "index", "environment"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-directory")
    parser.add_argument("--revision")
    parser.add_argument("--repo")
    args = parser.parse_args()
    if args.action == "environment":
        import semble

        distribution = importlib.metadata.distribution("semble")
        installed = []
        for relative in distribution.files or []:
            if str(relative).startswith("semble/") and str(relative).endswith(".py"):
                path = distribution.locate_file(relative)
                installed.append({"path": str(relative), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        write(args.output, {
            "version": distribution.version,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "installed_source": sorted(installed, key=lambda x: x["path"]),
            "module_path": str(Path(semble.__file__).resolve()),
            "distribution_module_path": str(Path(distribution.locate_file("semble/__init__.py")).resolve()),
            "dependencies": sorted([
                {"name": item.metadata["Name"], "version": item.version}
                for item in importlib.metadata.distributions()
            ], key=lambda x: x["name"].lower()),
        })
    elif args.action == "model":
        from huggingface_hub import HfApi, snapshot_download

        model = "minishlab/potion-code-16M-v2"
        revision = args.revision or HfApi().model_info(model).sha
        snapshot = snapshot_download(model, revision=revision, allow_patterns=["*.json", "*.safetensors"])
        target = Path(args.model_directory)
        if target.exists():
            raise ValueError("Model output must be new")
        shutil.copytree(snapshot, target, symlinks=False)
        write(args.output, {"model": model, "revision": revision, "snapshot": snapshot, "directory": str(target.resolve())})
    else:
        from semble.cache import find_index_from_cache_folder, save_index_to_cache
        from semble.index import SembleIndex
        from semble.types import ContentType

        content = tuple(ContentType)
        cache = find_index_from_cache_folder(args.repo, content)
        if cache.exists():
            raise ValueError("Index cache must not exist before preparation")
        index = SembleIndex.from_path(args.repo, content=content)
        if index.loaded_from_disk:
            raise ValueError("Expected a fresh index")
        sources = {}
        root = Path(args.repo).resolve()
        for chunk in index.chunks:
            path = (root / chunk.file_path).resolve()
            if not path.is_relative_to(root):
                raise EvidenceError("Indexed file escapes corpus")
            if chunk.file_path not in sources:
                with path.open(encoding="utf-8", errors="replace") as handle:
                    sources[chunk.file_path] = handle.readlines()
            span = "".join(sources[chunk.file_path][chunk.start_line - 1:chunk.end_line])
            if chunk.content not in span:
                raise EvidenceError(f"Chunk does not map to source: {chunk.file_path}:{chunk.start_line}")
        save_index_to_cache(index, args.repo)
        write(args.output, {
            "index_directory": str(cache.resolve()),
            "loaded_from_disk": index.loaded_from_disk,
            "chunk_count": len(index.chunks),
            "source_mapping_verified": True,
            "indexed_files": sorted({chunk.file_path for chunk in index.chunks}),
            "content": [entry.value for entry in content],
            "index_timing_scope": "Python process startup, model load, fresh build and persistence; model download prepared separately",
        })


if __name__ == "__main__":
    try:
        main()
    except EvidenceError as exc:
        print(f"Evaluator evidence error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
