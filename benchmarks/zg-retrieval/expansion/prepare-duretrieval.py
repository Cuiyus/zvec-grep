"""Materialize the pinned C-MTEB DuRetrieval corpus without changing its text."""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as parquet


ID = re.compile(r"[0-9a-f]{32}\Z")
MAX_FILE_BYTES = 1_000_000


def rows(path):
    source = parquet.ParquetFile(path)
    for batch in source.iter_batches(batch_size=4096):
        yield from batch.to_pylist()


def prepare(lock, source, root):
    queries = {}
    for row in rows(source / "queries.parquet"):
        qid, query = row["id"], row["text"]
        assert ID.fullmatch(qid) and isinstance(query, str) and query.strip()
        assert qid not in queries, f"duplicate query {qid}"
        queries[qid] = query
    assert len(queries) == lock["source"]["query_count"]

    judgments = defaultdict(list)
    for row in rows(source / "qrels.parquet"):
        qid, pid, score = row["qid"], row["pid"], row["score"]
        assert qid in queries and ID.fullmatch(pid) and score == 1
        judgments[qid].append({"document_id": pid, "relevance": score})
    assert sum(map(len, judgments.values())) == lock["source"]["qrel_count"]
    assert set(judgments) == set(queries), "missing query judgments"

    qids = sorted(queries)
    selected = [qids[index * len(qids) // 10] for index in range(10)]
    assert selected == [task["id"] for task in lock["tasks"]], "selection changed"
    for task in lock["tasks"]:
        assert task["query"] == queries[task["id"]], f"changed query {task['id']}"
        assert task["qrels"] == judgments[task["id"]], f"changed qrels {task['id']}"

    documents = root / "docs"
    documents.mkdir(parents=True)
    seen = set()
    for row in rows(source / "corpus.parquet"):
        pid, text = row["id"], row["text"]
        assert ID.fullmatch(pid) and isinstance(text, str) and text.strip()
        assert pid not in seen, f"duplicate passage {pid}"
        assert len(text.encode("utf-8")) <= MAX_FILE_BYTES, f"oversized passage {pid}"
        (documents / f"{pid}.md").write_text(text, encoding="utf-8")
        seen.add(pid)
    assert len(seen) == lock["corpus_documents"], "incomplete corpus"
    for task in lock["tasks"]:
        for judgment in task["qrels"]:
            assert judgment["document_id"] in seen, "gold passage missing from corpus"
    print(f"Prepared {len(seen)} passages and {len(selected)} unchanged queries")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    prepare(json.loads(args.lock.read_text(encoding="utf-8")), args.source, args.root)


if __name__ == "__main__":
    main()
