"""Prepare the unmodified Semble package/model/index outside the search corpus."""

import argparse
import hashlib
import importlib.metadata
import json
import os
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
    parser.add_argument("action", choices=["model", "index", "environment", "sdk-replay"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-directory")
    parser.add_argument("--revision")
    parser.add_argument("--repo")
    parser.add_argument("--content", choices=["code", "docs", "config", "all"], default="code")
    parser.add_argument("--index-directory")
    parser.add_argument("--queries")
    parser.add_argument("--top-k", type=int, default=10)
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
    elif args.action == "sdk-replay":
        from semble.index import SembleIndex

        index_directory = Path(args.index_directory).resolve()
        metadata = json.loads((index_directory / "metadata.json").read_text())
        queries = json.loads(Path(args.queries).read_text())
        root = Path(args.repo).resolve()
        if Path(metadata["root_path"]).resolve() != root:
            raise EvidenceError("SDK replay corpus differs from persisted index")
        if metadata["model_path"] != os.environ["SEMBLE_MODEL_NAME"]:
            raise EvidenceError("SDK replay model differs from frozen model")
        if metadata["content_type"] != [args.content]:
            raise EvidenceError("SDK replay content differs from frozen protocol")
        if args.top_k != 10 or not isinstance(queries, list) or not queries:
            raise EvidenceError("SDK replay requires original queries and top_k=10")
        if len({row["task_id"] for row in queries}) != len(queries):
            raise EvidenceError("SDK replay repeats a task")
        # Load the already prepared index directly; never from_path or reindex.
        index = SembleIndex.load_from_disk(index_directory)
        if not index.loaded_from_disk:
            raise EvidenceError("SDK replay did not load the persisted index")
        results = []
        for row in queries:
            if not isinstance(row["query"], str) or not row["query"]:
                raise EvidenceError("SDK replay query is missing")
            ranked = index.search(row["query"], top_k=args.top_k)
            results.append({
                "task_id": row["task_id"],
                "query": row["query"],
                "results": [{
                    "file_path": result.chunk.file_path,
                    "start_line": result.chunk.start_line,
                    "end_line": result.chunk.end_line,
                    "score": float(result.score),
                    "content": result.chunk.content,
                } for result in ranked],
            })
        write(args.output, {
            "schema_version": 1,
            "engine": "semble",
            "index_directory": str(index_directory),
            "corpus_root": str(root),
            "model_path": metadata["model_path"],
            "loaded_from_disk": index.loaded_from_disk,
            "content": [entry.value for entry in index.content],
            "parameters": {
                "top_k": args.top_k, "alpha": None, "rerank": None,
                "filter_languages": None, "filter_paths": None,
                "max_snippet_lines": None,
            },
            "queries": results,
        })
    else:
        from semble.cache import find_index_from_cache_folder, save_index_to_cache
        from semble.index import SembleIndex
        from semble.types import ContentType

        content = tuple(ContentType) if args.content == "all" else (ContentType(args.content),)
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
            source_lines = sources[chunk.file_path]
            span = "".join(source_lines[chunk.start_line - 1:chunk.end_line])
            content_lines = chunk.content.split("\n")
            if chunk.content.endswith("\n"):
                content_lines.pop()
            if (not chunk.content or len(content_lines) != chunk.end_line - chunk.start_line + 1
                    or chunk.content not in span):
                raise EvidenceError(f"Chunk does not map to source: {chunk.file_path}:{chunk.start_line}")
            for offset, line in enumerate(content_lines):
                original = source_lines[chunk.start_line + offset - 1].removesuffix("\n")
                first = offset == 0
                last = offset == len(content_lines) - 1
                partial_end = last and not chunk.content.endswith("\n")
                if not (original == line or (first and original.endswith(line))
                        or (partial_end and original.startswith(line))
                        or (first and partial_end and line in original)):
                    raise EvidenceError(f"Chunk line mapping differs: {chunk.file_path}:{chunk.start_line + offset}")
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
