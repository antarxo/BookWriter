from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import fitz

VERSION = "pdf-extraction-probe-0.2"


def _parse_pages(spec: str, max_pages: int) -> list[int]:
    result: set[int] = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
            if end < start:
                start, end = end, start
            result.update(range(start, end + 1))
        else:
            result.add(int(part))
    pages = sorted(page for page in result if 1 <= page <= max_pages)
    if not pages:
        raise ValueError("Δεν δόθηκαν έγκυρες σελίδες.")
    return pages


def _find_pdftotext(explicit: str | None = None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.exists():
            return path
    found = shutil.which("pdftotext") or shutil.which("pdftotext.exe")
    if found:
        return Path(found)
    candidates = [
        Path(r"C:\Program Files\poppler\Library\bin\pdftotext.exe"),
        Path(r"C:\Program Files\poppler\bin\pdftotext.exe"),
        Path(r"C:\poppler\Library\bin\pdftotext.exe"),
        Path(r"C:\poppler\bin\pdftotext.exe"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _iter_local(root: ET.Element, name: str):
    for node in root.iter():
        if _local_name(str(node.tag)) == name:
            yield node


def _children_local(node: ET.Element, name: str):
    for child in node.iter():
        if child is node:
            continue
        if _local_name(str(child.tag)) == name:
            yield child


def _pymupdf_page(page: fitz.Page, page_no: int) -> dict[str, Any]:
    raw = page.get_text("dict", sort=True)
    blocks_out: list[dict[str, Any]] = []
    line_count = 0
    span_count = 0
    char_count = 0
    for block_index, block in enumerate(raw.get("blocks", []) or [], start=1):
        if block.get("type") != 0:
            continue
        lines_out: list[dict[str, Any]] = []
        for line_index, line in enumerate(block.get("lines", []) or [], start=1):
            spans_out: list[dict[str, Any]] = []
            text_parts: list[str] = []
            for span_index, span in enumerate(line.get("spans", []) or [], start=1):
                text = str(span.get("text") or "")
                if not text:
                    continue
                span_count += 1
                char_count += len(text)
                text_parts.append(text)
                spans_out.append({
                    "index": span_index,
                    "text": text,
                    "bbox": [round(float(v), 3) for v in span.get("bbox", (0, 0, 0, 0))],
                    "font": str(span.get("font") or ""),
                    "size": round(float(span.get("size") or 0.0), 3),
                    "flags": int(span.get("flags") or 0),
                })
            if not spans_out:
                continue
            line_count += 1
            lines_out.append({
                "index": line_index,
                "text": "".join(text_parts),
                "bbox": [round(float(v), 3) for v in line.get("bbox", (0, 0, 0, 0))],
                "spans": spans_out,
            })
        if not lines_out:
            continue
        blocks_out.append({
            "index": block_index,
            "bbox": [round(float(v), 3) for v in block.get("bbox", (0, 0, 0, 0))],
            "lines": lines_out,
        })
    return {
        "page": page_no,
        "width": round(float(page.rect.width), 3),
        "height": round(float(page.rect.height), 3),
        "blockCount": len(blocks_out),
        "lineCount": line_count,
        "spanCount": span_count,
        "charCount": char_count,
        "blocks": blocks_out,
    }


def _poppler_page(pdf: Path, pdftotext: Path, page_no: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="poppler_probe_") as tmp:
        xml_path = Path(tmp) / f"page-{page_no}.html"
        cmd = [
            str(pdftotext),
            "-f", str(page_no),
            "-l", str(page_no),
            "-bbox-layout",
            "-enc", "UTF-8",
            str(pdf),
            str(xml_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"pdftotext απέτυχε στη σελίδα {page_no}: {proc.stderr.strip()}")
        if not xml_path.exists():
            raise RuntimeError(f"Το pdftotext δεν παρήγαγε XHTML για τη σελίδα {page_no}.")
        root = ET.parse(xml_path).getroot()

    page_node = next(_iter_local(root, "page"), None)
    words: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    for line_index, line_node in enumerate(_iter_local(root, "line"), start=1):
        line_words: list[dict[str, Any]] = []
        for word_node in _children_local(line_node, "word"):
            text = "".join(word_node.itertext())
            try:
                bbox = [
                    float(word_node.attrib.get("xMin", 0)),
                    float(word_node.attrib.get("yMin", 0)),
                    float(word_node.attrib.get("xMax", 0)),
                    float(word_node.attrib.get("yMax", 0)),
                ]
            except ValueError:
                bbox = [0.0, 0.0, 0.0, 0.0]
            row = {"text": text, "bbox": [round(v, 3) for v in bbox]}
            words.append(row)
            line_words.append(row)
        if line_words:
            boxes = [row["bbox"] for row in line_words]
            lines.append({
                "index": line_index,
                "text": " ".join(row["text"] for row in line_words),
                "bbox": [
                    min(box[0] for box in boxes),
                    min(box[1] for box in boxes),
                    max(box[2] for box in boxes),
                    max(box[3] for box in boxes),
                ],
                "words": line_words,
            })
    width = float(page_node.attrib.get("width", 0)) if page_node is not None else 0.0
    height = float(page_node.attrib.get("height", 0)) if page_node is not None else 0.0
    return {
        "page": page_no,
        "width": round(width, 3),
        "height": round(height, 3),
        "lineCount": len(lines),
        "wordCount": len(words),
        "lines": lines,
        "words": words,
    }


def _write_text_report(data: dict[str, Any], path: Path) -> None:
    out: list[str] = []
    out.append("PDF EXTRACTION COMPARISON")
    out.append(f"PDF: {data['pdf']}")
    out.append(f"Poppler: {data['pdftotext']}")
    out.append("")
    for page in data["pages"]:
        py = page["pymupdf"]
        po = page["poppler"]
        out.append(f"=== PAGE {page['page']} ===")
        out.append(
            f"PyMuPDF: blocks={py['blockCount']} lines={py['lineCount']} spans={py['spanCount']} chars={py['charCount']}"
        )
        out.append(f"Poppler: lines={po['lineCount']} words={po['wordCount']}")
        out.append("")
        out.append("-- PyMuPDF lines --")
        for block in py["blocks"]:
            for line in block["lines"]:
                out.append(f"{line['bbox']} | {line['text']}")
        out.append("")
        out.append("-- Poppler lines --")
        for line in po["lines"]:
            out.append(f"{line['bbox']} | {line['text']}")
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")


def run_probe(pdf: Path, pages_spec: str, output: Path, pdftotext_path: str | None = None) -> dict[str, Any]:
    pdf = Path(pdf).expanduser().resolve()
    if not pdf.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε PDF: {pdf}")
    pdftotext = _find_pdftotext(pdftotext_path)
    if pdftotext is None:
        raise FileNotFoundError("Δεν βρέθηκε pdftotext.exe (Poppler).")
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    with fitz.open(pdf) as doc:
        pages = _parse_pages(pages_spec, doc.page_count)
        result_pages: list[dict[str, Any]] = []
        for page_no in pages:
            py = _pymupdf_page(doc[page_no - 1], page_no)
            po = _poppler_page(pdf, pdftotext, page_no)
            result_pages.append({"page": page_no, "pymupdf": py, "poppler": po})

    data = {
        "version": VERSION,
        "pdf": str(pdf),
        "pagesSpec": pages_spec,
        "pdftotext": str(pdftotext),
        "pages": result_pages,
    }
    json_path = output / "PDF_EXTRACTION_COMPARISON.json"
    txt_path = output / "PDF_EXTRACTION_COMPARISON.txt"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_text_report(data, txt_path)
    return {
        "data": data,
        "json": json_path,
        "txt": txt_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare PyMuPDF and Poppler PDF text extraction.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", default="20,26,29")
    parser.add_argument("--output", type=Path, default=Path("pdf_extraction_probe_output"))
    parser.add_argument("--pdftotext", default=None)
    args = parser.parse_args()
    try:
        result = run_probe(args.pdf, args.pages, args.output, args.pdftotext)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"OK: {result['json']}")
    print(f"OK: {result['txt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
