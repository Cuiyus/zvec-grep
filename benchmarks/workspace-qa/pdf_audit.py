"""Independent PDFium coverage gate for the five frozen Task 192 reports."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import shutil

from pdf_text import INDEX_CAP, extract_pages, sha


def coverage(reference: str, text: str) -> dict:
    # Order is checked by per-page comparison and reviewed rendered tables, not
    # by this character-multiset check. Whitespace differs between PDF engines.
    a, b = (Counter(re.sub(r"\s+", "", value)) for value in (reference, text))
    return {"reference_characters": sum(a.values()), "extracted_characters": sum(b.values()),
            "coverage": sum((a & b).values()) / sum(a.values()) if a else 1.0,
            "missing_characters": dict(a - b)}


def verify(source: Path, manifest: dict, output: Path, review: dict) -> dict:
    import pypdfium2 as pdfium
    output.mkdir(parents=True, exist_ok=True)
    errors, checked = [], []
    for expected in review["files"]:
        matches = [r for r in manifest["files"] if r["source_sha256"] == expected["source_sha256"]]
        if not matches:
            errors.append("Missing required source: " + expected["filename"])
        for row in matches:
            failures = []
            if row["status"] != "converted" or row.get("empty_text_pages"):
                errors.append("Required PDF has incomplete text extraction: " + row["path"])
                continue
            path = source / row["path"]
            if sha(path.read_bytes()) != expected["source_sha256"]:
                failures.append("source hash changed")
            pages = extract_pages(path)
            if len(pages) != expected["page_count"]:
                failures.append("physical page count differs")
            doc = pdfium.PdfDocument(path)
            checks = []
            if len(doc) != len(pages):
                failures.append("independent page count differs")
            else:
                for i, text in enumerate(pages):
                    page = doc[i]; textpage = page.get_textpage()
                    ref = textpage.get_text_range()
                    textpage.close(); page.close()
                    check = {"page": i + 1, **coverage(ref, text)}
                    if check["coverage"] < expected.get("minimum_page_character_coverage", 0.995):
                        failures.append(f"page {i + 1}: low independent text coverage")
                    checks.append(check)
            doc.close()
            for item in row["outputs"]:
                body = (source / item["path"]).read_bytes()
                if sha(body) != item["sha256"] or len(body) > INDEX_CAP:
                    failures.append("sidecar hash or size gate failed")
                shutil.copyfile(source / item["path"], output / Path(item["path"]).name)
            for sample in expected.get("reviewed_samples", []):
                clean = re.sub(r"\s+", "", pages[sample["page"] - 1])
                if any(re.sub(r"\s+", "", s) not in clean for s in sample["anchors"]):
                    failures.append(f"reviewed table/text anchor missing on page {sample['page']}")
            errors.extend(row["path"] + ": " + e for e in failures)
            checked.append({"path": row["path"], "source_sha256": row["source_sha256"],
                "page_count": len(pages), "status": "failed" if failures else "passed",
                "page_checks": checks, "sidecar_bytes": sum(o["size_bytes"] for o in row["outputs"])})
    result = {"task_id": "192", "status": "failed" if errors else "passed", "files": checked,
        "errors": errors, "scope": "all physical pages; independent non-whitespace character coverage and reviewed numeric/table samples; no OCR or full visual-equivalence claim",
        "corpus_status_counts": manifest["status_counts"], "visual_review": review.get("visual_review")}
    (output / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Task 192 PDF text conversion gate", "", f"Status: **{result['status']}**.", "",
        "| Original | Pages | Text bytes | Minimum page character coverage |", "|---|---:|---:|---:|"]
    for row in checked:
        lines.append(f"| {Path(row['path']).name} | {row['page_count']} | {row['sidecar_bytes']} | "
                     f"{min(c['coverage'] for c in row['page_checks']):.5%} |")
    lines += ["", result["scope"], "", "Corpus status counts: " + json.dumps(manifest["status_counts"]),
              "", f"Oversize sidecars retained but not indexed: {manifest['oversize_text_files']}.", "", *errors]
    (output / "summary.md").write_text("\n".join(lines) + "\n")
    if errors:
        raise ValueError("Task 192 PDF text conversion gate failed; inspect pdf-audit/verification.json")
    return result
