"""Fixed-pool score-component checks; never replace observed search results."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .case_analysis import write_json


def rank(candidates: list[dict], variant="observed") -> list[str]:
    remaining = list(candidates)
    counts, output = {}, []
    single_file = len({h["path"] for h in candidates}) == 1

    def score(hit):
        trace = hit["trace"]["ranking"]
        static = trace["static_score"]
        if variant == "no_identifier_contribution":
            # Freeze the captured file-support contribution and all other terms.
            before = min(trace["symbol_bonus"] + trace["definition_bonus"] + trace["path_bonus"], 1.25)
            after = min(trace["path_bonus"], 1.25)
            static -= (before - after) * trace["path_factor"]
        if variant == "rrf_only":
            return hit["trace"]["fusion"]["score"]
        count = 0 if single_file or variant == "no_diversity" else counts.get(hit["path"], 0)
        return static / (1 + .5 * count)

    while remaining:
        best = min(remaining, key=lambda h: (-score(h), h["trace"]["fusion"]["rank"], h["entity_id"]))
        remaining.remove(best)
        output.append(best["entity_id"])
        counts[best["path"]] = counts.get(best["path"], 0) + 1
    return output


def analyze(replay: Path) -> dict:
    summary = json.loads((replay / "summary.json").read_text())
    output = []
    for query in summary["queries"]:
        with (replay / "diagnostic/stages.jsonl").open("rb") as stream:
            start, end = query["stage_byte_range"]
            stream.seek(start)
            stages = [json.loads(line) for line in stream.read(end - start).splitlines()]
        plans = []
        for stage in stages:
            if stage["stage"] == "plan":
                plans.append({"plan": stage["plan"], "stages": []})
            else:
                plans[-1]["stages"].append(stage)
        for plan in plans:
            candidates = next(s["candidates"] for s in plan["stages"] if s["stage"] == "ranking_continuation")
            variants = {v: rank(candidates, v) for v in ["observed", "no_diversity", "no_identifier_contribution", "rrf_only"]}
            observed = [h["entity_id"] for h in candidates]
            parity = variants["observed"] == observed
            if not parity:
                raise RuntimeError("offline scorer does not reproduce captured full ranking")
            targets = []
            for hit in candidates:
                if hit["path"] not in {"pylint/pyreverse/utils.py", "pylint/pyreverse/inspector.py", "pylint/checkers/utils.py", "pylint/checkers/typecheck.py"}:
                    continue
                if not any('symbol_name: Some("' + name + '")' in hit["metadata"] for name in
                           ["get_annotation", "infer_node", "is_attribute_typed_annotation", "_get_all_attribute_assignments", "_emit_no_member", "AbstractAssociationHandler", "AggregationsHandler", "OtherAssociationsHandler", "handle_assignattr_type"]):
                    continue
                probes = []
                for stage in plan["stages"]:
                    if stage["stage"] == "recall_probe":
                        matching = [h["rank"] for h in stage["hits"] if h["entity_id"] == hit["entity_id"]]
                        probes.append({"route": stage["route"], "probe_depth": stage["depth"], "rank": min(matching) if matching else None})
                targets.append({"entity_id": hit["entity_id"], "path": hit["path"], "range": hit["range"], "metadata": hit["metadata"],
                                "recall": hit["trace"]["recall"], "tail_probes": probes,
                                "fusion": hit["trace"]["fusion"], "score_components": hit["trace"]["ranking"],
                                "fixed_pool_ranks": {v: ids.index(hit["entity_id"]) + 1 for v, ids in variants.items()}})
            output.append({"query_id": query["query_id"], "plan": plan["plan"], "candidate_count": len(candidates),
                           "offline_rank_parity": parity, "targets": targets})
    result = {"warning": "Ranks beyond the requested limit are diagnostic continuations. Component controls freeze recall and other captured terms; these are not proposed product fixes.", "plans": output}
    write_json(replay / "ranking-analysis.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.replay)
    print(json.dumps({"plans": len(result["plans"]), "offline_rank_parity": True}))
