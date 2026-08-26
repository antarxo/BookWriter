from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any

from .page_layout_spine_v03 import build_page_layout_spine as _build_v03


VERSION = "page-layout-spine-0.5"


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return [round(part, 3) for part in box]


def _page_lookup(page_structure: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    return {
        int(page.get("page") or 0): page
        for page in (page_structure or {}).get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }


def _markdown_pdf_items(markdown_pdf_spine: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in (markdown_pdf_spine or {}).get("items", []) or []
        if item.get("id")
    }


def _donor_lookup(docx_donor_map: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    paragraphs = {
        str(item.get("id")): item
        for item in (docx_donor_map or {}).get("paragraphs", []) or []
        if item.get("id")
    }
    tables = {
        str(item.get("id")): item
        for item in (docx_donor_map or {}).get("tables", []) or []
        if item.get("id")
    }
    return paragraphs, tables


def _native_donor(markdown_id: str, docx_donor_map: dict[str, Any] | None, paragraphs: dict[str, dict[str, Any]], tables: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    association = ((docx_donor_map or {}).get("associationByMarkdownId") or {}).get(markdown_id)
    if not isinstance(association, dict):
        return None
    selected = association.get("selected") if isinstance(association.get("selected"), dict) else None
    if not selected:
        return {
            "status": association.get("status") or "unresolved",
            "selected": None,
            "contentAuthority": False,
            "layoutAuthority": False,
        }
    paragraph_id = str(selected.get("paragraphId") or "")
    table_id = str(selected.get("tableId") or "")
    donor = paragraphs.get(paragraph_id) if paragraph_id else tables.get(table_id)
    return {
        "status": association.get("status"),
        "selected": selected,
        "kind": "paragraph" if paragraph_id else ("table" if table_id else None),
        "record": donor,
        "contentAuthority": False,
        "layoutAuthority": False,
        "allowedUses": list(((docx_donor_map or {}).get("policy") or {}).get("allowedUses") or []),
    }


def _column_box(layout_contract: dict[str, Any]) -> list[float] | None:
    box = ((layout_contract.get("column") or {}).get("box"))
    if isinstance(box, dict):
        try:
            return [float(box.get("x0")), float(box.get("y0")), float(box.get("x1")), float(box.get("y1"))]
        except (TypeError, ValueError):
            return None
    return _bbox(box)


def _first_line_indent(line_boxes: list[list[float]]) -> dict[str, Any]:
    boxes = [_bbox(box) for box in line_boxes]
    boxes = [box for box in boxes if box]
    if len(boxes) < 2:
        return {
            "firstLineIndentPt": 0.0,
            "hangingIndentPt": 0.0,
            "confidence": "low",
            "source": "insufficient-pdf-lines",
        }
    rest_x = [box[0] for box in boxes[1:]]
    baseline = float(median(rest_x))
    delta = round(float(boxes[0][0]) - baseline, 3)
    if abs(delta) < 1.5:
        delta = 0.0
    return {
        "firstLineIndentPt": max(0.0, delta),
        "hangingIndentPt": max(0.0, -delta),
        "confidence": "high" if len(boxes) >= 3 else "medium",
        "source": "pdf-line-x0-delta",
    }


def _alignment(box: list[float] | None, line_boxes: list[list[float]], column_box: list[float] | None) -> dict[str, Any]:
    if not box or not column_box:
        return {"value": None, "confidence": "none", "source": "missing-pdf-geometry"}
    left_gap = max(0.0, box[0] - column_box[0])
    right_gap = max(0.0, column_box[2] - box[2])
    width = max(1.0, column_box[2] - column_box[0])
    boxes = [_bbox(value) for value in line_boxes]
    boxes = [value for value in boxes if value]
    full_width_lines = sum(1 for value in boxes[:-1] if (value[2] - value[0]) / width >= 0.88) if len(boxes) > 1 else 0
    if len(boxes) >= 3 and full_width_lines >= max(1, len(boxes) - 2) and left_gap <= 5.0:
        return {"value": "justify", "confidence": "medium", "source": "pdf-line-width-pattern"}
    if abs(left_gap - right_gap) <= max(5.0, width * 0.035) and left_gap > 8.0:
        return {"value": "center", "confidence": "medium", "source": "pdf-symmetric-side-gaps"}
    if right_gap <= 4.0 and left_gap > 12.0:
        return {"value": "right", "confidence": "medium", "source": "pdf-right-edge-fit"}
    return {"value": "left", "confidence": "high" if left_gap <= 6.0 else "medium", "source": "pdf-left-edge-fit"}


def _paragraph_geometry(row: dict[str, Any], source_item: dict[str, Any]) -> dict[str, Any]:
    layout_contract = row.get("layoutContract") or {}
    typography = source_item.get("pdfTypography") if isinstance(source_item.get("pdfTypography"), dict) else {}
    box = _bbox(((layout_contract.get("box") or {}).get("absolutePt"))) or _bbox((row.get("layout") or {}).get("bbox")) or _bbox(source_item.get("bbox"))
    col_box = _column_box(layout_contract)
    line_boxes = list(typography.get("lineBoxes") or [])
    indent = _first_line_indent(line_boxes)
    positioned_frame = layout_contract.get("placement") == "positioned-text-frame"
    if positioned_frame:
        left_indent = 0.0
        right_indent = 0.0
    else:
        left_indent = max(0.0, (box[0] - col_box[0])) if box and col_box else None
        right_indent = max(0.0, (col_box[2] - box[2])) if box and col_box else None
    return {
        "source": "pdf-geometry",
        "bboxPt": box,
        "columnBoxPt": col_box,
        "leftIndentPt": round(left_indent, 3) if left_indent is not None else None,
        "rightIndentPt": round(right_indent, 3) if right_indent is not None else None,
        "firstLineIndentPt": indent["firstLineIndentPt"],
        "hangingIndentPt": indent["hangingIndentPt"],
        "indentConfidence": indent["confidence"],
        "alignment": _alignment(box, line_boxes, box if positioned_frame else col_box),
        "lineHeightPt": ((typography.get("linePitch") or {}).get("medianPt")),
        "lineHeightSource": "pdf-line-pitch",
        "lineCount": typography.get("lineCount"),
        "lineBoxes": line_boxes,
    }


def _frame_contract(row: dict[str, Any]) -> dict[str, Any] | None:
    layout = row.get("layout") or {}
    layout_contract = row.get("layoutContract") or {}
    if layout_contract.get("placement") != "positioned-text-frame":
        return None
    box = _bbox(((layout_contract.get("box") or {}).get("absolutePt"))) or _bbox(layout.get("bbox"))
    return {
        "kind": "word-paragraph-frame",
        "source": "pdf-layout-slot",
        "bboxPt": box,
        "anchor": {"horizontal": "page", "vertical": "page"},
        "wrap": "around",
        "sizeRule": "exact",
        "lockAnchor": True,
        "textInsetsPt": {
            "left": 0.0,
            "right": 0.0,
            "top": 0.0,
            "bottom": 0.0,
            "source": "text-region-bbox-no-extra-inset-evidence",
        },
        "border": {
            "status": "unresolved-not-extracted",
            "source": None,
            "color": None,
            "widthPt": None,
            "style": None,
        },
        "fill": {
            "status": "unresolved-not-extracted",
            "source": None,
            "color": None,
        },
        "rendererPolicy": "do-not-invent-border-or-fill",
    }


def _page_columns(page: dict[str, Any] | None) -> dict[str, Any]:
    columns = [dict(column) for column in (page or {}).get("columns", []) or [] if isinstance(column, dict)]
    gutter = None
    if len(columns) == 2:
        try:
            gutter = round(max(0.0, float(columns[1].get("x0")) - float(columns[0].get("x1"))), 3)
        except (TypeError, ValueError):
            gutter = None
    return {
        "layoutMode": (page or {}).get("layout_mode"),
        "columns": columns,
        "columnCount": len(columns) if columns else 1,
        "gutterPt": gutter,
        "source": "pdf-page-structure",
    }


def _spacing(rows: list[dict[str, Any]]) -> None:
    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        layout = row.get("layout") or {}
        page = int(layout.get("page") or 0)
        role = str(layout.get("columnRole") or "main")
        groups.setdefault((page, role), []).append(row)
    for group in groups.values():
        group.sort(key=lambda row: (
            float((((row.get("wordParagraph") or {}).get("geometry") or {}).get("bboxPt") or [0, 0, 0, 0])[1]),
            int(row.get("markdownOrder") or 0),
        ))
        for index, row in enumerate(group):
            geometry = ((row.get("wordParagraph") or {}).get("geometry") or {})
            box = _bbox(geometry.get("bboxPt"))
            previous_box = _bbox((((group[index - 1].get("wordParagraph") or {}).get("geometry") or {}).get("bboxPt"))) if index > 0 else None
            next_box = _bbox((((group[index + 1].get("wordParagraph") or {}).get("geometry") or {}).get("bboxPt"))) if index + 1 < len(group) else None
            gap_before = max(0.0, box[1] - previous_box[3]) if box and previous_box else None
            gap_after = max(0.0, next_box[1] - box[3]) if box and next_box else None
            row["wordParagraph"]["spacing"] = {
                "source": "pdf-neighbour-gap",
                "observedGapBeforePt": round(gap_before, 3) if gap_before is not None else None,
                "observedGapAfterPt": round(gap_after, 3) if gap_after is not None else None,
                "spaceBeforePt": 0.0,
                "spaceAfterPt": round(gap_after, 3) if gap_after is not None else 0.0,
                "policy": "encode-each-interparagraph-gap-once-as-space-after",
            }


def build_page_layout_spine(
    markdown_pdf_spine: dict[str, Any] | None,
    page_structure: dict[str, Any] | None,
    docx_donor_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = _build_v03(markdown_pdf_spine, page_structure, docx_donor_map)
    source_by_id = _markdown_pdf_items(markdown_pdf_spine)
    pages = _page_lookup(page_structure)
    donor_paragraphs, donor_tables = _donor_lookup(docx_donor_map)
    typography_ready = 0
    geometry_ready = 0
    donor_ready = 0
    frame_count = 0

    for row in result.get("rows", []) or []:
        markdown_id = str(row.get("markdownId") or "")
        source_item = source_by_id.get(markdown_id) or {}
        authoritative = source_item.get("authoritativeContent") if isinstance(source_item.get("authoritativeContent"), dict) else {}
        raw_markdown = str(source_item.get("rawMarkdown") or "")
        row["authoritativeContent"] = authoritative
        row["rawMarkdown"] = raw_markdown
        row["markdownText"] = str(authoritative.get("text") or source_item.get("text") or row.get("markdownText") or "")
        row["pdfTypography"] = source_item.get("pdfTypography") or {}
        row["pdfGeometry"] = source_item.get("pdfGeometry") or {}
        row["nativeDonor"] = _native_donor(markdown_id, docx_donor_map, donor_paragraphs, donor_tables)
        if row["nativeDonor"] and row["nativeDonor"].get("record"):
            donor_ready += 1
        if (row["pdfTypography"] or {}).get("confidence") not in {None, "none"}:
            typography_ready += 1

        geometry = _paragraph_geometry(row, source_item)
        if geometry.get("bboxPt"):
            geometry_ready += 1
        page_no = int((row.get("layout") or {}).get("page") or 0)
        row["wordParagraph"] = {
            "geometry": geometry,
            "typography": row["pdfTypography"],
            "spacing": {},
            "pageColumns": _page_columns(pages.get(page_no)),
            "placement": (row.get("layoutContract") or {}).get("placement"),
            "sourcePolicy": {
                "content": "markdown",
                "geometry": "pdf",
                "typography": "pdf",
                "nativeDonor": "docx-secondary",
            },
        }
        frame = _frame_contract(row)
        if frame:
            row["wordParagraph"]["frame"] = frame
            frame_count += 1
        contract = row.get("layoutContract") or {}
        contract["wordParagraph"] = row["wordParagraph"]
        contract["authoritativeContent"] = authoritative
        contract["nativeDonor"] = row["nativeDonor"]
        row["layoutContract"] = contract

    _spacing(result.get("rows", []) or [])
    result["version"] = VERSION
    result["policy"] = (
        "Builder-ready maps-first layout spine. Markdown owns content; PDF owns page geometry, columns, paragraph geometry and typography; "
        "DOCX contributes only explicitly associated native donors. Paragraph spacing is derived once from observed PDF neighbour gaps. "
        "Positioned callouts receive explicit Word frame geometry; border/fill remain unresolved until independently extracted and must not be invented by the renderer."
    )
    summary = result.setdefault("summary", {})
    total = len(result.get("rows", []) or [])
    summary["builderReady"] = {
        "rowCount": total,
        "typographyReadyCount": typography_ready,
        "geometryReadyCount": geometry_ready,
        "nativeDonorResolvedCount": donor_ready,
        "positionedFrameCount": frame_count,
        "typographyCoverage": round(typography_ready / total, 5) if total else 1.0,
        "geometryCoverage": round(geometry_ready / total, 5) if total else 1.0,
        "authority": {
            "content": "markdown",
            "geometry": "pdf",
            "typography": "pdf",
            "nativeDonor": "docx-secondary",
            "frameBorderFill": "unresolved-until-extracted",
        },
    }
    return result