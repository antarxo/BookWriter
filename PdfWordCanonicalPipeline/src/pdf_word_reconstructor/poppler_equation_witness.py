from __future__ import annotations

import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


VERSION = "poppler-equation-witness-0.1"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _iter_local(root: ET.Element, name: str):
    for node in root.iter():
        if _local_name(str(node.tag)) == name:
            yield node


def _find_pdftotext() -> Path | None:
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


def _bbox(node: ET.Element) -> list[float] | None:
    try:
        box = [
            float(node.attrib.get("xMin", 0)),
            float(node.attrib.get("yMin", 0)),
            float(node.attrib.get("xMax", 0)),
            float(node.attrib.get("yMax", 0)),
        ]
    except (TypeError, ValueError):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _node_words(node: ET.Element) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for word in node.iter():
        if _local_name(str(word.tag)) != "word":
            continue
        box = _bbox(word)
        text = "".join(word.itertext()).strip()
        if box and text:
            result.append({"text": text, "bbox": box})
    return result


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _extract_page(pdf_path: Path, executable: Path, page_no: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="poppler_eq_") as tmp:
        output = Path(tmp) / f"page-{page_no}.html"
        cmd = [
            str(executable), "-f", str(page_no), "-l", str(page_no),
            "-bbox-layout", "-enc", "UTF-8", str(pdf_path), str(output),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"pdftotext failed on page {page_no}")
        root = ET.parse(output).getroot()

    page_node = next(_iter_local(root, "page"), None)
    width = float(page_node.attrib.get("width", 0)) if page_node is not None else 0.0
    height = float(page_node.attrib.get("height", 0)) if page_node is not None else 0.0
    blocks: list[dict[str, Any]] = []
    block_index = 0
    for block in _iter_local(root, "block"):
        words = _node_words(block)
        if not words:
            continue
        block_index += 1
        lines: list[dict[str, Any]] = []
        for line_index, line in enumerate(
            (node for node in block.iter() if _local_name(str(node.tag)) == "line"), start=1
        ):
            line_words = _node_words(line)
            if not line_words:
                continue
            line_box = _union([row["bbox"] for row in line_words])
            lines.append({
                "index": line_index,
                "text": " ".join(row["text"] for row in line_words),
                "bbox": line_box,
            })
        block_box = _bbox(block) or _union([row["bbox"] for row in words])
        blocks.append({
            "id": f"poppler-p{page_no}-b{block_index:03d}",
            "bbox": block_box,
            "text": " ".join(row["text"] for row in words),
            "lines": lines,
        })
    return {"page": page_no, "width": width, "height": height, "blocks": blocks}


def extract_poppler_equation_witness(pdf_path: Path, pages: list[int]) -> dict[str, Any]:
    executable = _find_pdftotext()
    if executable is None:
        return {
            "version": VERSION,
            "available": False,
            "reason": "pdftotext-not-found",
            "pages": [],
        }
    page_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for page_no in sorted(set(int(page) for page in pages if int(page) > 0)):
        try:
            page_rows.append(_extract_page(Path(pdf_path), executable, page_no))
        except Exception as exc:
            errors.append({"page": page_no, "error": str(exc)})
    return {
        "version": VERSION,
        "available": True,
        "pdftotext": str(executable),
        "pages": page_rows,
        "errors": errors,
    }


__all__ = ["extract_poppler_equation_witness"]
