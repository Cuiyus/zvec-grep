"""Materialize pinned BEIR corpora and validate original queries and qrels."""

import argparse
import csv
import hashlib
import json
import os
import re
from pathlib import Path

import pyarrow.parquet as pq


def digest(data):
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def rows(path):
    return pq.read_table(path).to_pylist()


def judgments(path):
    result = {}
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        assert reader.fieldnames == ["query-id", "corpus-id", "score"]
        for row in reader:
            result.setdefault(row["query-id"], []).append(
                {"document_id": row["corpus-id"], "relevance": int(row["score"])}
            )
    return result


def prepare_dataset(dataset, source, corpus_root, scifact_qrels):
    name = dataset["id"]
    part = source / name
    documents = rows(part / "corpus-00000-of-00001.parquet")
    queries = rows(part / "queries-00000-of-00001.parquet")
    assert len(documents) == dataset["corpus_documents"], name
    assert len(queries) == dataset["query_rows"], name
    identity = "".join(
        f"{document['_id']}\t{digest(document['title'] + chr(0) + document['text'])}\n"
        for document in sorted(documents, key=lambda row: row["_id"])
    )
    assert digest(identity) == dataset["mirror"]["corpus_sha256"], name
    query_by_id = {str(row["_id"]): row["text"] for row in queries}
    assert len(query_by_id) == len(queries), name
    qrel_path = scifact_qrels if name == "scifact" else part / "test.tsv"
    assert hashlib.sha256(qrel_path.read_bytes()).hexdigest() == dataset["mirror"]["qrels_test_sha256"]
    qrels = judgments(qrel_path)

    root = corpus_root / name
    docs = root / "docs"
    docs.mkdir(parents=True)
    document_ids = set()
    for document in documents:
        identifier = str(document["_id"])
        assert re.fullmatch(r"[A-Za-z0-9._-]+", identifier), identifier
        assert identifier not in document_ids, identifier
        document_ids.add(identifier)
        (docs / f"{identifier}.md").write_text(
            f"{document['title']}\n\n{document['text']}\n", encoding="utf-8"
        )

    tasks = []
    for task in dataset["tasks"]:
        identifier = task["id"]
        assert query_by_id[identifier] == task["query"], identifier
        assert qrels[identifier] == task["qrels"], identifier
        assert all(row["document_id"] in document_ids for row in task["qrels"]), identifier
        tasks.append(
            {
                "id": f"{name}/{identifier}",
                "query": task["query"],
                "dataset": name,
                "targets": [
                    {"path": f"docs/{row['document_id']}.md"} for row in task["qrels"]
                ],
            }
        )

    if name != "arguana":
        return [
            {
                "id": name,
                "root": str(root),
                "tasks": tasks,
                "indexGlob": "*.md",
                "expectedIndexedFiles": len(documents),
            }
        ]

    groups = []
    for index, (source_task, task) in enumerate(zip(dataset["tasks"], tasks), 1):
        own_id = source_task["id"]
        assert own_id in document_ids
        assert all(row["document_id"] != own_id for row in source_task["qrels"])
        task_root = corpus_root / "arguana-queries" / str(index)
        task_docs = task_root / "docs"
        task_docs.mkdir(parents=True)
        for identifier in sorted(document_ids):
            if identifier != own_id:
                os.link(docs / f"{identifier}.md", task_docs / f"{identifier}.md")
        assert not (task_docs / f"{own_id}.md").exists()
        groups.append(
            {
                "id": f"arguana-{index}",
                "root": str(task_root),
                "tasks": [task],
                "indexGlob": "*.md",
                "expectedIndexedFiles": len(documents) - 1,
            }
        )
    return groups


def main():
    parser = argparse.ArgumentParser()
    for option in ("lock", "source", "scifact-qrels", "corpus", "output"):
        parser.add_argument(f"--{option}", required=True, type=Path)
    parser.add_argument("--dataset", required=True)
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    dataset = next(row for row in lock["datasets"] if row["id"] == args.dataset)
    groups = prepare_dataset(dataset, args.source, args.corpus, args.scifact_qrels)
    args.output.write_text(json.dumps(groups, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
