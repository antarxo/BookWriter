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


VERSION = "docx-analysis-0.2"


def iter_block_items(parent: _Document | _Cell) -> Iterator[Paragraph | Table]:
    """Yield paragraphs and tables in document order."""
    parent_elm = parent.element.body if isinstance(parent, _Document) else parent._tc
    for child in parent_elm.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, parent)
        elif child.tag.endswith("}tbl"):
            yield Table(child, parent)


def _pt(value: Any) -> float | None:
    try:
        return round(float(value.pt), 3) if value is not None else None
    except Exception:
        return None


def _numbering_record(paragraph: Paragraph) -> dict[str, Any] | None:
    p_pr = paragraph._p.pPr
    num_pr = p_pr.numPr if p_pr is not None else None
    if num_pr is None:
        return None
    num_id = num_pr.numId.val if num_pr.numId is not None else None
    ilvl = num_pr.ilvl.val if num_pr.ilvl is not None else None
    return {
        "numId": int(num_id) if num_id is not None else None,
        "level": int(ilvl) if ilvl is not None else None,
        "source": "word-numPr",
    }


def _paragraph_format_record(paragraph: Paragraph) -> dict[str, Any]:
    fmt = paragraph.paragraph_format
    line_spacing = fmt.line_spacing
    if hasattr(line_spacing, "pt"):
        line_spacing_value: float | None = _pt(line_spacing)
        line_spacing_kind = "points"
    elif isinstance(line_spacing, (int, float)):
        line_spacing_value = round(float(line_spacing), 4)
        line_spacing_kind = "multiple"
    else:
        line_spacing_value = None
        line_spacing_kind = None
    return {
        "alignment": str(paragraph.alignment) if paragraph.alignment is not None else None,
        "leftIndentPt": _pt(fmt.left_indent),
        "rightIndentPt": _pt(fmt.right_indent),
        "firstLineIndentPt": _pt(fmt.first_line_indent),
        "spaceBeforePt": _pt(fmt.space_before),
        "spaceAfterPt": _pt(fmt.space_after),
        "lineSpacing": line_spacing_value,
        "lineSpacingKind": line_spacing_kind,
        "keepTogether": fmt.keep_together,
        "keepWithNext": fmt.keep_with_next,
        "pageBreakBefore": fmt.page_break_before,
        "widowControl": fmt.widow_control,
    }


def _run_record(index: int, run) -> dict[str, Any]:
    size = run.font.size.pt if run.font.size else None
    rel_ids: list[str] = []
    for node in run._r.iter():
        for key, value in node.attrib.items():
            if key.endswith("}embed") or key.endswith("}link"):
                rel_ids.append(str(value))
    return {
        "index": index,
        "text": run.text,
        "bold": run.bold,
        "italic": run.italic,
        "underline": bool(run.underline) if run.underline is not None else None,
        "font": run.font.name,
        "size_pt": round(size, 2) if size is not None else None,
        "style": run.style.name if run.style else None,
        "hasDrawing": bool(run._r.xpath(".//w:drawing | .//w:pict")),
        "hasOmml": bool(run._r.xpath(".//m:oMath | .//m:oMathPara")),
        "relationshipIds": sorted(set(rel_ids)),
    }


def _paragraph_record(index: int, paragraph: Paragraph, container: str = "body") -> dict[str, Any]:
    runs = [_run_record(run_index, run) for run_index, run in enumerate(paragraph.runs)]
    xml = paragraph._p.xml
    omml_count = xml.count("<m:oMath")
    drawing_count = xml.count("<w:drawing") + xml.count("<w:pict")
    compact = compact_text(paragraph.text)
    omml_tokens = [str(node.text or "") for node in paragraph._p.iter() if node.tag == qn("m:t")]
    omml_text = " ".join(token for token in omml_tokens if token).strip()
    drawing_rel_ids = sorted({rid for run in runs for rid in run.get("relationshipIds", [])})
    return {
        "id": f"d-p{index:05d}",
        "index": index,
        "container": container,
        "locator": {"kind": "paragraph", "paragraphIndex": index, "container": container},
        "text": paragraph.text,
        "preview": compact,
        "style": paragraph.style.name if paragraph.style else None,
        "paragraph_format": _paragraph_format_record(paragraph),
        "numbering": _numbering_record(paragraph),
        "runs": runs,
        "omml_count": omml_count,
        "omml_text": omml_text,
        "drawing_count": drawing_count,
        "drawing_relationship_ids": drawing_rel_ids,
        "has_math": omml_count > 0,
        "is_math_only": omml_count > 0 and not compact,
        "native_flags": {
            "hasOmml": omml_count > 0,
            "hasDrawing": drawing_count > 0,
            "hasNumbering": _numbering_record(paragraph) is not None,
        },
    }


def _table_record(table_index: int, table: Table) -> tuple[dict[str, Any], list[tuple[Paragraph, str]]]:
    rows_out: list[list[str]] = []
    cells_out: list[dict[str, Any]] = []
    paragraph_refs: list[tuple[Paragraph, str]] = []
    for r, row in enumerate(table.rows):
        row_text: list[str] = []
        for c, cell in enumerate(row.cells):
            row_text.append(cell.text)
            cell_id = f"d-t{table_index:04d}-r{r:03d}-c{c:03d}"
            paragraphs = list(cell.paragraphs)
            cells_out.append({
                "id": cell_id,
                "row": r,
                "col": c,
                "text": cell.text,
                "widthPt": _pt(cell.width),
                "paragraphCount": len(paragraphs),
            })
            container = f"table-{table_index}-row-{r}-cell-{c}"
            paragraph_refs.extend((paragraph, container) for paragraph in paragraphs)
        rows_out.append(row_text)
    table_widths = [_pt(column.width) for column in table.columns]
    return ({
        "id": f"d-t{table_index:04d}",
        "index": table_index,
        "locator": {"kind": "table", "tableIndex": table_index},
        "rows": len(table.rows),
        "cols": len(table.columns),
        "style": table.style.name if table.style else None,
        "columnWidthsPt": table_widths,
        "cells": cells_out,
        "text": "\n".join("\t".join(row) for row in rows_out),
        "text_preview": compact_text(" | ".join(" / ".join(row) for row in rows_out)),
    }, paragraph_refs)


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
            table_record, paragraph_refs = _table_record(table_index, item)
            table_record["paragraphIds"] = []
            for paragraph, container in paragraph_refs:
                record = _paragraph_record(para_index, paragraph, container=container)
                paragraphs.append(record)
                table_record["paragraphIds"].append(record["id"])
                style_counter[record["style"] or "(none)"] += 1
                para_index += 1
            tables.append(table_record)

    sections = []
    for i, section in enumerate(doc.sections):
        sections.append({
            "index": i,
            "locator": {"kind": "section", "sectionIndex": i},
            "page_width_pt": round(section.page_width.pt, 2),
            "page_height_pt": round(section.page_height.pt, 2),
            "top_margin_pt": round(section.top_margin.pt, 2),
            "bottom_margin_pt": round(section.bottom_margin.pt, 2),
            "left_margin_pt": round(section.left_margin.pt, 2),
            "right_margin_pt": round(section.right_margin.pt, 2),
            "header_distance_pt": _pt(section.header_distance),
            "footer_distance_pt": _pt(section.footer_distance),
            "start_type": str(section.start_type),
        })

    return {
        "version": VERSION,
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
