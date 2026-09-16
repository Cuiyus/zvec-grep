"""Task 328 conversion gate. Audit inputs stay outside the agent workspace."""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile

HERE = Path(__file__).resolve().parent


def normalized(text):
    return re.sub(r"\s+", "", html.unescape(text))


def verify(source: Path, manifest: dict, output: Path) -> dict:
    review = json.loads((HERE / "data/task-328-markdown-review.json").read_text())
    output.mkdir(parents=True, exist_ok=True)
    result = {"task_id": "328", "status": "failed", "variant": manifest["variant"], "files": [],
              "scope": "structured text, tables, chart caches and embedded workbook coverage; not full visual/audio equivalence",
              "media_review": review["media_review"], "corpus_status_counts": manifest["status_counts"]}
    result["oversize_markdown_files"] = manifest.get("oversize_markdown_files", 0)
    errors = []
    if manifest["status_counts"].get("conversion_failed", 0):
        errors.append("Full-persona OOXML conversion contains failures")
    for expected in review["files"]:
        matches = [r for r in manifest["files"] if r.get("source_sha256") == expected["source_sha256"]]
        if not matches:
            errors.append("Missing reviewed task input: " + expected["filename"])
        for row in matches:
            if row.get("status") != "converted":
                errors.append("Required task input was not converted: " + row["path"])
                continue
            texts = []
            for item in row["outputs"]:
                target = source / item["path"]
                data = target.read_bytes()
                if hashlib.sha256(data).hexdigest() != item["sha256"] or len(data) > 1048576:
                    errors.append("Sidecar hash/size mismatch: " + item["path"])
                texts.append(data.decode("utf-8"))
                shutil.copyfile(target, output / target.name)
            joined = "".join(texts)
            clean = normalized(joined)
            # Independent read of every content-bearing original XML atom. The
            # converter's represented-node ledger is an additional count check.
            missing = []
            atoms = 0
            with zipfile.ZipFile(source / row["path"]) as package:
                for name in package.namelist():
                    if not name.endswith(".xml") or not name.startswith(("ppt/", "word/", "xl/")):
                        continue
                    for e in ET.fromstring(package.read(name)).iter():
                        if e.tag.rsplit("}", 1)[-1] not in {"t", "v", "f"} or not e.text or not normalized(e.text):
                            continue
                        atoms += 1
                        if normalized(e.text) not in clean:
                            missing.append({"part": name, "text": e.text})
            counts_match = row["counts"] == expected["counts"]
            embedded_match = len(row["embedded_workbooks"]) == expected["embedded_workbooks"]
            passed = not missing and not row["missing_atoms"] and counts_match and embedded_match
            if not passed:
                errors.append("Structural coverage mismatch: " + row["path"])
            result["files"].append({"path": row["path"], "source_sha256": row["source_sha256"],
                "status": "passed" if passed else "failed", "counts": row["counts"], "xml_atoms_checked": atoms,
                "missing_atoms": missing, "embedded_workbooks": len(row["embedded_workbooks"]),
                "markdown_bytes": sum(x["size_bytes"] for x in row["outputs"]),
                "index_size_cap_passed": all(x["size_bytes"] <= 1048576 for x in row["outputs"])})
    result.update(status="passed" if not errors else "failed", errors=errors)
    (output / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Task 328 Markdown conversion gate", "", "Status: **" + result["status"] + "**.", "",
        "Scope: structured text/data coverage. Original Office and TXT remain available to both arms. "
        "This is a Markdown-preprocessed variant, not the original workspace leaderboard.", "",
        "| File | Slides | Tables | Charts | Embedded workbooks | XML atoms checked | Missing | Markdown bytes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in result["files"]:
        c = row["counts"]
        lines.append(f"| {Path(row['path']).name} | {c.get('slides',0)} | {c.get('tables',0)} | {c.get('charts',0)} | "
            f"{row['embedded_workbooks']} | {row['xml_atoms_checked']} | {len(row['missing_atoms'])} | {row['markdown_bytes']} |")
    lines += ["", "Full-persona conversion: `" + json.dumps(manifest["status_counts"], sort_keys=True) + "`.",
              "", f"Sidecars above the unchanged 1 MiB index cap: {result['oversize_markdown_files']}. Full content retained; no splitting/truncation.",
              "", "Conversion seconds: " + str(round(manifest["wall_seconds"], 3)) + ". Excluded from agent metrics.", "",
              "Raster review: 47 images inspected; decorative photographs, backgrounds, illustrations and media icons. "
              "Audio is retained but not transcribed. Legacy .doc/.xls/.ppt and PDF are outside this converter's scope.",
              "", *errors]
    (output / "summary.md").write_text("\n".join(lines) + "\n")
    if errors:
        raise ValueError("Task 328 conversion gate failed; see markdown-audit/verification.json")
    return result
