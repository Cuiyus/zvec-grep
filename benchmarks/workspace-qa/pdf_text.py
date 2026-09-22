"""Uniform PDF text-layer sidecars; no task/question/rubric-dependent extraction."""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import time

VARIANT = "pdf-text-v1"
INDEX_CAP = 1048576
COMMON_NOTICE = (
    "This workspace has deterministic text-layer sidecars for readable PDF files, named "
    "<original filename>.txt. Each sidecar preserves all extracted pages in order with physical "
    "PDF page numbers and layout spacing. Original files and existing text files are retained. "
    "Images and scanned pages are not OCR-transcribed; extraction limitations are marked. "
    "Both comparison profiles receive exactly the same prepared workspace."
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_pages(path: Path) -> list[str]:
    if os.environ.get("WORKSPACE_QA_PDF_ENGINE") == "pdfium":
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(path)
        pages = []
        try:
            for page in doc:
                textpage = page.get_textpage()
                try:
                    pages.append(textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n"))
                finally:
                    textpage.close()
                    page.close()
        finally:
            doc.close()
        if not pages or any("\x00" in text for text in pages):
            raise ValueError("PDF has no pages or contains NUL in extracted text")
        return pages
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("Encrypted PDF requires a password")
    pages = [page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False)
             .replace("\r\n", "\n").replace("\r", "\n") for page in reader.pages]
    if not pages or any("\x00" in text for text in pages):
        raise ValueError("PDF has no pages or contains NUL in extracted text")
    return pages


def render_pages(name: str, pages: list[str], selected: list[int] | None = None) -> str:
    numbers = list(range(1, len(pages) + 1)) if selected is None else selected
    return f"Original PDF: {name}\nConversion: {VARIANT}; physical page numbers; no OCR.\n\n" + "\n\n".join(
        f"=== PDF page {i} / {len(pages)} ===\n" +
        (pages[i - 1] if pages[i - 1].strip() else "[No extractable text on this page; see original PDF.]")
        for i in numbers) + "\n"


def versions() -> dict:
    return {name: importlib.metadata.version(name) for name in ("pypdf", "pypdfium2")}


def convert_workspace(source: Path, output: Path) -> dict:
    started = time.monotonic()
    inputs = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"
                    and not any(part in {".git", ".zvec-grep"} for part in p.relative_to(source).parts))
    # Check every destination first: never overwrite an existing source TXT.
    for path in inputs:
        if path.with_name(path.name + ".txt").exists():
            raise FileExistsError(f"PDF sidecar collision: {path.relative_to(source)}")
    rows, cache = [], {}
    for index, path in enumerate(inputs):
        data = path.read_bytes()
        relative = path.relative_to(source).as_posix()
        row = {"path": relative, "source_sha256": sha(data), "source_bytes": len(data), "outputs": []}
        if not data:
            row["status"] = "empty_original"
        elif not data.lstrip().startswith(b"%PDF-"):
            row["status"] = "not_pdf_original"
        else:
            try:
                pages = cache.get(row["source_sha256"])
                if pages is None:
                    pages = extract_pages(path)
                    cache[row["source_sha256"]] = pages
                row.update(page_count=len(pages), empty_text_pages=[i + 1 for i, p in enumerate(pages) if not p.strip()])
                if not any(p.strip() for p in pages):
                    row["status"] = "no_text_layer"
                else:
                    body = render_pages(relative, pages).encode("utf-8")
                    target = path.with_name(path.name + ".txt")
                    target.write_bytes(body)
                    row.update(status="converted", outputs=[{"path": target.relative_to(source).as_posix(),
                        "sha256": sha(body), "size_bytes": len(body), "index_eligible_by_size": len(body) <= INDEX_CAP}],
                        pages=[{"page": i + 1, "text_sha256": sha(p.encode()), "text_chars": len(p)} for i, p in enumerate(pages)])
            except Exception as exc:
                row.update(status="conversion_failed", error_type=type(exc).__name__, error=str(exc)[:300])
        rows.append(row)
        print(json.dumps({"phase": "pdf_conversion", "completed": index + 1, "total": len(inputs),
                          "path": relative, "status": row["status"]}, ensure_ascii=False), flush=True)
    result = {"schema_version": 1, "variant": VARIANT, "converter_sha256": sha(Path(__file__).read_bytes()),
        "engine": os.environ.get("WORKSPACE_QA_PDF_ENGINE", "pypdf"),
        "versions": versions(), "policy": "all PDF originals in full persona; no source truncation or splitting; no OCR",
        "status_counts": dict(Counter(r["status"] for r in rows)), "files": rows,
        "oversize_text_files": sum(not o["index_eligible_by_size"] for r in rows for o in r["outputs"]),
        "wall_seconds": time.monotonic() - started}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result
