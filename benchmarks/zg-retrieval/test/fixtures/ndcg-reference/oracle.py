"""Run unmodified pinned upstream metric functions using only Python's stdlib.

SearchResult is used only as an annotation by upstream metrics.py, so a module
stub is sufficient; actual result/chunk values are SimpleNamespace instances.
Postponed annotations let the unchanged source run on system Python 3.9 too.
No Semble package installation, model, corpus, or retrieval call is involved.
"""

import __future__
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


FIXTURE = Path(__file__).parent
manifest = json.loads((FIXTURE / "source.json").read_text())
assert manifest["commit"] == "0051e000fcaac69a9c5d081ebbc8d4cb8508160b"
for entry in manifest["files"]:
    assert hashlib.sha256((FIXTURE / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]

for name in ("benchmarks", "semble"):
    package = ModuleType(name)
    package.__path__ = []
    sys.modules[name] = package
types_module = ModuleType("semble.types")
types_module.SearchResult = SimpleNamespace
sys.modules["semble.types"] = types_module


def load_upstream(name, filename):
    module = ModuleType(name)
    module.__file__ = str(FIXTURE / filename)
    sys.modules[name] = module
    code = compile(
        (FIXTURE / filename).read_bytes(),
        module.__file__,
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )
    exec(code, module.__dict__)
    return module


data = load_upstream("benchmarks.data", "data.py")
metrics = load_upstream("benchmarks.metrics", "metrics.py")


def score(case):
    results = []
    for index, item in enumerate(case["items"], 1):
        assert item["rank"] == index
        span = item.get("range", {})
        results.append(SimpleNamespace(chunk=SimpleNamespace(
            file_path=item["path"],
            start_line=span.get("start_line", 0),
            end_line=span.get("end_line", 0),
        )))
    targets = [data.Target(**target) for target in case["targets"]]
    ranks = [metrics.target_rank(results, target) for target in targets]
    relevant_ranks = [rank for rank in ranks if rank is not None]
    return {
        "ndcg_at_5": metrics.ndcg_at_k(relevant_ranks, len(targets), 5),
        "ndcg_at_10": metrics.ndcg_at_k(relevant_ranks, len(targets), 10),
        "target_ranks": ranks,
        "n_relevant": len(targets),
        "relevant_ranks": relevant_ranks,
    }


request = json.load(sys.stdin)
json.dump({
    "path_matches": [data.path_matches(*case) for case in request.get("paths", [])],
    "target_matches_location": [data.target_matches_location(
        case["file_path"], case["start_line"], case["end_line"], data.Target(**case["target"])
    ) for case in request.get("locations", [])],
    "scores": [score(case) for case in request.get("scores", [])],
    "ndcg_at_k": [metrics.ndcg_at_k(case["ranks"], case["n_relevant"], case["k"])
                  for case in request.get("ndcg", [])],
}, sys.stdout, allow_nan=False)
sys.stdout.write("\n")
