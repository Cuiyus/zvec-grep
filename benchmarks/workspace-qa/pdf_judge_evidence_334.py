"""Freeze candidate-blind original annual-report pages for Task 334 judging.

The deterministic page predicate is evaluator-only. It never enters either
agent workspace or index, and it is fixed before candidate answers exist.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import judge
from pdf_text import extract_pages, render_pages, sha

PROTOCOL = "pdf-334-keyword-evidence-v1"
TERMS = ("利润分配", "不分配", "未分配利润", "风险提示", "净利润", "每10股",
         "总股本", "重要提示", "主要会计数据", "营业收入", "经营情况讨论")


def selection_for_pages(pages: list[str]) -> list[int]:
    return [number for number, body in enumerate(pages, 1)
            if any(term in body for term in TERMS)]


def prepare(metadata_path: Path, task_dir: Path, output: Path) -> Path:
    if output.exists() and any(output.iterdir()):
        raise ValueError("PDF evidence destination must be new/empty")
    output.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(metadata_path.read_text())
    if metadata.get("absolute_id") != 334:
        raise ValueError("Task 334 evidence selector used for another task")
    sources = []
    for ident, item in enumerate(metadata["data_manifest"]):
        source = (task_dir / PurePosixPath(item["stored_relpath"])).resolve()
        if not source.is_relative_to(task_dir.resolve()):
            raise ValueError("PDF source path escapes task directory")
        raw = source.read_bytes()
        pages = extract_pages(source)
        selected = selection_for_pages(pages)
        if not selected:
            raise ValueError("One annual report has no frozen evidence pages")
        body = render_pages(item["filename"], pages, selected)
        row = {"id": ident, **item, "sha256": sha(raw), "bytes": len(raw),
               "page_count": len(pages), "full_text_sha256": sha(render_pages(item["filename"], pages).encode()),
               "selected_pages": selected, "text": body, "text_bytes": len(body.encode()),
               "text_sha256": sha(body.encode())}
        sources.append(row)
    packet = {"metadata": metadata, "metadata_sha256": sha(metadata_path.read_bytes()),
              "sources": sources, "source_bytes": sum(row["text_bytes"] for row in sources),
              "source_selection": {"protocol": PROTOCOL, "candidate_blind": True,
                                   "prepared_before_qa": True, "terms": TERMS,
                                   "full_source_count": len(sources),
                                   "selected_page_count": sum(len(row["selected_pages"]) for row in sources)}}
    if packet["source_bytes"] > judge.MAX_SOURCE_BYTES:
        raise ValueError("Frozen Task 334 original pages exceed judge source ceiling")
    judge.build_messages(packet, "preflight", [], max_prompt_bytes=judge.MAX_PROMPT_BYTES - 100_000)
    target = output / "evidence.json"
    judge.write_json(target, packet)
    (output / "summary.md").write_text(
        f"Task 334 judge source packet: {len(sources)} complete original reports; "
        f"{packet['source_selection']['selected_page_count']} selected original pages; "
        f"{packet['source_bytes']} UTF-8 bytes. Deterministic and candidate-blind.\n")
    return target
