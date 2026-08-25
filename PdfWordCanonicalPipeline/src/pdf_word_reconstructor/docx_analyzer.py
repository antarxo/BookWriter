from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from docx import Document
from docx.document import Document as _Document
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn

from .common import compact_text


def iter_block_items(parent: _Document | _Cell) -> Iterator[Paragraph | Table]:
    """Yield paragraphs and tables in document order."""
    parent_elm = parent.element.body if isinstance(parent, _Document) else parent._tc
    for child in parent_elm.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, parent)
        elif child.tag.endswith("}tbl"):
            yield Table(child, parent)


def _paragraph_record(index: int, paragraph: Paragraph, container: str = "body") -> dict[str, Any]:
    runs = []
    for run in paragraph.runs:
        size = run.font.size.pt if run.font.size else None
        runs.append({
            "text": run.text,
            "bold": run.bold,
            "italic": run.italic,
            "underline": bool(run.underline) if run.underline is not None else None,
            "font": run.font.name,
            "size_pt": round(size, 2) if size is not None else None,
        })
    xml = paragraph._p.xml
    omml_count = xml.count("<m:oMath")
    drawing_count = xml.count("<w:drawing") + xml.count("<w:pict")
    compact = compact_text(paragraph.text)
    omml_tokens = [str(node.text or "") for node in paragraph._p.iter() if node.tag == qn("m:t")]
    omml_text = compact_text(" ".join(omml_tokens))
    return {
        "id": f"d-p{index:05d}",
        "index": index,
        "container": container,
        "text": paragraph.text,
        "preview": compact,
        "style": paragraph.style.name if paragraph.style else None,
        "alignment": str(paragraph.alignment) if paragraph.alignment is not None else None,
        "runs": runs,
        "omml_count": omml_count,
        "omml_text": omml_text,
        "drawing_count": drawing_count,
        "has_math": omml_count > 0,
        "is_math_only": omml_count > 0 and not compact,
    }


def analyze_docx(docx_path: Path) -> dict[str, Any]:
    doc = Document(docx_path)
    paragraphs: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    style_counter: Counter[str] = Counter()
    para_index = 0
    table_index = 0

    for item in iter_block_items(doc):
        if isinstance(item, Paragraph):
            record = _paragraph_record(para_index, item)
            paragraphs.append(record)
            style_counter[record["style"] or "(none)"] += 1
            para_index += 1
        else:
            table_index += 1
            rows_out: list[list[str]] = []
            for row in item.rows:
                rows_out.append([cell.text for cell in row.cells])
                for c, cell in enumerate(row.cells):
                    for paragraph in cell.paragraphs:
                        record = _paragraph_record(para_index, paragraph, container=f"table-{table_index}-cell-{c}")
                        paragraphs.append(record)
                        style_counter[record["style"] or "(none)"] += 1
                        para_index += 1
            tables.append({
                "id": f"d-t{table_index:04d}",
                "rows": len(item.rows),
                "cols": len(item.columns),
                "text_preview": compact_text(" | ".join(" / ".join(row) for row in rows_out)),
            })

    sections = []
    for i, section in enumerate(doc.sections):
        sections.append({
            "index": i,
            "page_width_pt": round(section.page_width.pt, 2),
            "page_height_pt": round(section.page_height.pt, 2),
            "top_margin_pt": round(section.top_margin.pt, 2),
            "bottom_margin_pt": round(section.bottom_margin.pt, 2),
            "left_margin_pt": round(section.left_margin.pt, 2),
            "right_margin_pt": round(section.right_margin.pt, 2),
        })

    return {
        "source": str(docx_path),
        "paragraph_count": len(paragraphs),
        "table_count": len(tables),
        "inline_shape_count": len(doc.inline_shapes),
        "section_count": len(doc.sections),
        "paragraphs": paragraphs,
        "tables": tables,
        "sections": sections,
        "style_usage": [{"style": k, "count": v} for k, v in style_counter.most_common()],
    }
