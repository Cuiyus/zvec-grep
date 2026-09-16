#!/usr/bin/env python3
"""Deterministic, task-blind OOXML sidecars; originals are never rewritten.

This is a structured text/data extraction, not a visual/OCR conversion. Binary
Office, raster images and PDF stay available as originals and are inventoried.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import time
import xml.etree.ElementTree as ET
import zipfile

VARIANT = "office-markdown-v1"
SUPPORTED = {".docx", ".pptx", ".xlsx"}
LEGACY = {".doc", ".ppt", ".xls"}
PART_BYTES = 900 * 1024
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
TEXT_TAGS = {f"{{{NS[n]}}}t" for n in ("w", "a", "s")}
DATA_TAGS = TEXT_TAGS | {f"{{{NS[n]}}}{t}" for n in ("c", "s") for t in ("v", "f")}
COMMON_NOTICE = ("This workspace has deterministic Markdown sidecars for nonempty DOCX, PPTX and XLSX files, "
    "named <original filename>.md (large outputs use .partNNN.md). Original files and existing TXT files are retained. "
    "Sidecars preserve structured text, tables, chart caches and worksheet cells; images, audio, legacy Office and PDF "
    "are not OCR-transcribed. Both comparison profiles receive exactly the same prepared workspace.\n")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def natural(value: str):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", value)]


def cell(value: str) -> str:
    return value.replace("&", "&amp;").replace("|", "&#124;").replace("\r", "").replace("\n", "<br>")


def table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(map(len, rows))
    # A generated numeric header does not assume that the first source row is a header.
    out = [[f"Column {i + 1}" for i in range(width)], ["---"] * width]
    out.extend(row + [""] * (width - len(row)) for row in rows)
    return "\n".join("| " + " | ".join(cell(x) for x in row) + " |" for row in out)


def relationships(z: zipfile.ZipFile, part: str) -> dict:
    path = PurePosixPath(part)
    rel = str(path.parent / "_rels" / (path.name + ".rels"))
    if rel not in z.namelist():
        return {}
    return {e.attrib["Id"]: (e.attrib.get("Type", "").rsplit("/", 1)[-1],
            e.attrib.get("Target") if e.attrib.get("TargetMode") == "External" else
            posixpath.normpath(posixpath.join(str(path.parent), e.attrib.get("Target", ""))).lstrip("/"),
            e.attrib.get("TargetMode") == "External") for e in ET.fromstring(z.read(rel))}


class Document:
    def __init__(self, z: zipfile.ZipFile):
        self.z = z
        self.parts = []
        self.counts = Counter()

    def render_part(self, name: str, title: str) -> str:
        root = ET.fromstring(self.z.read(name))
        represented = set()

        def take(e):
            represented.add(id(e))
            return e.text or ""

        def text(e):
            return "".join(take(x) if x.tag in TEXT_TAGS else "\n" if local(x.tag) in ("br", "cr")
                           else "\t" if local(x.tag) == "tab" else "" for x in e.iter())

        def walk(e):
            kind = local(e.tag)
            if kind == "tbl":
                self.counts["tables"] += 1
                rows = []
                for tr in e:
                    if local(tr.tag) != "tr":
                        continue
                    row = []
                    for tc in tr:
                        if local(tc.tag) != "tc":
                            continue
                        paras = [text(p) for p in tc.iter() if local(p.tag) == "p"]
                        value = "\n".join(paras)
                        span = tc.find("w:tcPr/w:gridSpan", NS)
                        colspan = span.get(f"{{{NS['w']}}}val", "1") if span is not None else tc.get("gridSpan", "1")
                        merge = tc.find("w:tcPr/w:vMerge", NS)
                        if colspan != "1":
                            value += f" [colspan={colspan}]"
                        if merge is not None:
                            value += " [vMerge=" + merge.get(f"{{{NS['w']}}}val", "continue") + "]"
                        row.append(value)
                    rows.append(row)
                self.counts["table_rows"] += len(rows)
                return table(rows) + "\n\n"
            if kind == "p" and e.tag.startswith(("{" + NS["w"], "{" + NS["a"])):
                return text(e) + "\n\n"
            return "".join(walk(x) for x in e)

        body = walk(root)
        if "/charts/" in name:
            self.counts["charts"] += 1
            # Align by OOXML point index, never zip differently sized arrays.
            for series in root.findall(".//c:ser", NS):
                def points(path):
                    base = series.find(path, NS)
                    return {} if base is None else {p.get("idx"): (p.findtext("c:v", default="", namespaces=NS))
                        for p in base.findall(".//c:pt", NS)}
                categories = points("c:cat") or points("c:xVal")
                values = points("c:val") or points("c:yVal")
                series_name = " / ".join(e.text or "" for e in series.findall("c:tx//c:v", NS))
                body += "### Chart series: " + series_name + "\n\n"
                body += table([["Point index", "Category / X", "Value / Y"]] +
                    [[i, categories.get(i, ""), values.get(i, "")] for i in sorted(categories.keys() | values.keys(), key=natural)]) + "\n\n"
        # Preserve otherwise unhandled text/data, including chart titles, caches,
        # formulas, drawings, SmartArt and text boxes, in package/XML order.
        remaining = [e for e in root.iter() if e.tag in DATA_TAGS and e.text and id(e) not in represented]
        if remaining:
            body += "### Additional source text / data (XML order)\n\n"
            body += "\n".join("- " + local(e.tag) + ": " + cell(take(e)) for e in remaining) + "\n\n"
        atoms = [e for e in root.iter() if e.tag in DATA_TAGS and e.text]
        missing = [e.text for e in atoms if id(e) not in represented]
        self.parts.append({"part": name, "source_atoms": len(atoms), "represented_atoms": len(atoms) - len(missing),
                           "missing_atoms": missing, "source_atoms_sha256": sha(json.dumps([e.text for e in atoms], ensure_ascii=False).encode())})
        return f"## {title}\n\nPackage part: `{name}`\n\n" + body

    def spreadsheet(self, prefix="") -> str:
        z = self.z
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            strings = ["".join(e.text or "" for e in si.iter() if e.tag in TEXT_TAGS)
                       for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
        styles = ET.fromstring(z.read("xl/styles.xml")) if "xl/styles.xml" in z.namelist() else ET.Element("styles")
        custom = {int(e.get("numFmtId")): e.get("formatCode") for e in styles.findall("s:numFmts/s:numFmt", NS)}
        formats = [custom.get(int(e.get("numFmtId", "0")), "builtin:" + e.get("numFmtId", "0"))
                   for e in styles.findall("s:cellXfs/s:xf", NS)]
        rels = relationships(z, "xl/workbook.xml")
        out = []
        for sheet in ET.fromstring(z.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS):
            _, name, external = rels[sheet.get(f"{{{NS['r']}}}id")]
            if external:
                raise ValueError("External worksheet is not a frozen local input")
            root = ET.fromstring(z.read(name))
            rows = [["Cell", "Stored value", "Formula (not recalculated)", "Number format"]]
            for e in root.findall("s:sheetData/s:row/s:c", NS):
                raw = e.findtext("s:v", default="", namespaces=NS)
                value = strings[int(raw)] if e.get("t") == "s" and raw else raw
                if e.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in e.iter() if t.tag in TEXT_TAGS)
                formula = e.find("s:f", NS)
                f = "" if formula is None else (formula.text or "") + (" " + json.dumps(formula.attrib, sort_keys=True) if formula.attrib else "")
                fmt = formats[int(e.get("s", "0"))] if formats else "builtin:0"
                if value or formula is not None:
                    rows.append([e.get("r", ""), value, f, fmt])
            self.counts["sheets"] += 1
            self.counts["worksheet_cells"] += len(rows) - 1
            merges = [e.get("ref", "") for e in root.findall("s:mergeCells/s:mergeCell", NS)]
            out.append(f"## {prefix}Sheet: {sheet.get('name')}\n\nState: {sheet.get('state', 'visible')}. "
                       f"Merged cells: {', '.join(merges) or 'none'}. Stored values are not recalculated.\n\n" + table(rows) + "\n\n")
        return "".join(out)

    def render(self) -> tuple[str, dict]:
        names = self.z.namelist()
        ordered = []
        labels = {}
        if "ppt/presentation.xml" in names:
            rels = relationships(self.z, "ppt/presentation.xml")
            ids = [e.get(f"{{{NS['r']}}}id") for e in ET.fromstring(self.z.read("ppt/presentation.xml")).iter()
                   if local(e.tag) == "sldId"]
            for i, rid in enumerate(ids, 1):
                _, name, external = rels[rid]
                if external:
                    raise ValueError("External slide")
                ordered.append(name)
                labels[name] = f"Slide {i}"
                for kind, target, external in relationships(self.z, name).values():
                    if not external and kind in {"chart", "notesSlide", "diagramData"} and target not in ordered:
                        ordered.append(target)
                        labels[target] = f"Slide {i} · {kind}"
            self.counts["slides"] = len(ids)
        if "word/document.xml" in names:
            ordered.insert(0, "word/document.xml")
        # Include every content-bearing OOXML part, even if it was not linked by
        # a familiar relationship. Templates/masters are labeled, never blended.
        for name in sorted(names, key=natural):
            if name.endswith(".xml") and name.startswith(("word/", "ppt/", "xl/")) and name not in ordered:
                root = ET.fromstring(self.z.read(name))
                if any(e.tag in DATA_TAGS and e.text for e in root.iter()):
                    ordered.append(name)
        out = self.spreadsheet() if "xl/workbook.xml" in names else ""
        for name in dict.fromkeys(ordered):
            out += self.render_part(name, labels.get(name, name))
        embedded = []
        media = []
        for name in sorted(names):
            if "/embeddings/" in name and name.endswith(".xlsx"):
                with zipfile.ZipFile(io.BytesIO(self.z.read(name))) as book:
                    doc = Document(book)
                    text, audit = doc.render()
                    out += f"## Embedded workbook: {name}\n\n" + text
                    embedded.append({"part": name, **audit})
            elif "/media/" in name and not name.endswith("/"):
                media.append({"part": name, "sha256": sha(self.z.read(name)), "size_bytes": self.z.getinfo(name).file_size})
        if media:
            out += "## Media retained in original (not OCR-transcribed)\n\n" + "\n".join("- " + m["part"] for m in media) + "\n"
        counts = dict(self.counts)
        return out, {"counts": counts, "parts": self.parts, "embedded_workbooks": embedded, "media": media,
                     "missing_atoms": sum(len(p["missing_atoms"]) for p in self.parts) + sum(x["missing_atoms"] for x in embedded),
                     "visual_fidelity_claim": False, "ocr": False}


def convert_file(path: Path, relative: str) -> tuple[str, dict]:
    data = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        doc = Document(z)
        body, audit = doc.render()
    heading = f"# {path.name}\n\nOriginal: `{relative}`\n\nConversion: {VARIANT}; structured text/data; media remain in original.\n\n"
    return heading + body, {"path": relative, "source_sha256": sha(data), "source_bytes": len(data), **audit}


def chunks(text: str) -> list[str]:
    # Stable UTF-8 chunks; no truncation and no file exceeds the experiment cap.
    result, current, size = [], [], 0
    for line in text.splitlines(keepends=True):
        for start in range(0, len(line), PART_BYTES // 4):
            bit = line[start:start + PART_BYTES // 4]
            n = len(bit.encode())
            if size + n > PART_BYTES and current:
                result.append("".join(current)); current, size = [], 0
            current.append(bit); size += n
    if current:
        result.append("".join(current))
    return result


def convert_workspace(source: Path, output: Path) -> dict:
    started = time.monotonic()
    rows = []
    originals = sorted(p for p in source.rglob("*") if p.is_file() and ".git" not in p.relative_to(source).parts
                       and p.suffix.lower() in SUPPORTED | LEGACY)
    for i, path in enumerate(originals, 1):
        relative = path.relative_to(source).as_posix()
        if not path.stat().st_size or path.suffix.lower() not in SUPPORTED:
            rows.append({"path": relative, "source_sha256": sha(path.read_bytes()), "source_bytes": path.stat().st_size,
                         "status": "empty_original" if not path.stat().st_size else "legacy_unsupported"})
            continue
        try:
            text, audit = convert_file(path, relative)
            pieces = chunks(text)
            targets = []
            for n, piece in enumerate(pieces, 1):
                suffix = ".md" if n == 1 else f".part{n:03}.md"
                target = path.with_name(path.name + suffix)
                if target.exists():
                    raise FileExistsError("Refusing to replace an original sidecar")
                target.write_text(piece, encoding="utf-8")
                targets.append({"path": target.relative_to(source).as_posix(), "sha256": sha(piece.encode()), "size_bytes": len(piece.encode())})
            audit.update(status="converted", outputs=targets)
            rows.append(audit)
        except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
            # Corrupt/masquerading source documents remain visible in the manifest.
            rows.append({"path": relative, "source_sha256": sha(path.read_bytes()), "status": "conversion_failed", "error": type(exc).__name__})
        if i % 50 == 0:
            print(json.dumps({"phase": "office_markdown", "processed": i, "planned": len(originals)}), flush=True)
    result = {"variant": VARIANT, "converter_sha256": sha(Path(__file__).read_bytes()),
              "scope": "all nonempty .docx/.pptx/.xlsx in full persona, independent of task/rubrics; originals and TXT retained",
              "status_counts": dict(Counter(r["status"] for r in rows)), "files": rows,
              "wall_seconds": time.monotonic() - started, "included_in_agent_metrics": False,
              "limitations": ["No raster OCR or visual layout equivalence", "Legacy .doc/.xls/.ppt and PDF not converted",
                              "Worksheet cached values preserved without recalculation; raw values and number formats reported"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = convert_workspace(args.source, args.output)
    print(json.dumps(result["status_counts"]))


if __name__ == "__main__":
    main()
