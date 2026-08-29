from __future__ import annotations

from pathlib import Path
from typing import Any

from .build_contract import build_build_contract
from .mathpix_lines_input import build_mathpix_line_layout_map


VERSION = "lines-only-contract-0.2"
_TEXT_TYPES = {"text", "section_header", "figure_label", "math"}
_SEMANTIC = {
    "text": "paragraph",
    "section_header": "heading",
    "figure_label": "caption",
    "math": "equation",
    "diagram": "figure",
}


def _box_px(record: dict[str, Any]) -> list[float] | None:
    box = record.get("bbox_px") if isinstance(record.get("bbox_px"), dict) else {}
    if not box:
        return None
    try:
        values = [float(box.get(key)) for key in ("x0", "y0", "x1", "y1")]
    except (TypeError, ValueError):
        return None
    if values[2] <= values[0] or values[3] <= values[1]:
        return None
    return values


def _scale_box(box: list[float], sx: float, sy: float) -> list[float]:
    return [
        round(box[0] * sx, 3),
        round(box[1] * sy, 3),
        round(box[2] * sx, 3),
        round(box[3] * sy, 3),
    ]


def _page_size(page: dict[str, Any], width_pt: float) -> tuple[float, float, float, float]:
    width_px = float(page.get("page_width_px") or 1.0)
    height_px = float(page.get("page_height_px") or 1.0)
    height_pt = width_pt * height_px / width_px
    return width_pt, height_pt, width_pt / width_px, height_pt / height_px


def _text(record: dict[str, Any]) -> str:
    for value in (record.get("text_display"), record.get("text"), record.get("conversion_output")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _font_size_pt(record: dict[str, Any], sy: float) -> float | None:
    try:
        value = float(record.get("font_size"))
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return round(value * sy, 3)


def _typography(record: dict[str, Any], sy: float) -> dict[str, Any]:
    size = _font_size_pt(record, sy)
    return {
        "source": "mathpix-lines-only",
        "confidence": "medium" if size else "low",
        "fontSizePt": {"dominant": size},
        "fontFamily": {"dominant": None},
        "emphasis": {},
        "color": {"dominant": None},
        "lineCount": 1,
        "lineBoxes": [],
    }


def _layout_contract(
    page: dict[str, Any],
    slot_id: str,
    semantic: str,
    box: list[float],
    order: int,
) -> dict[str, Any]:
    page_no = int(page.get("page") or 0)
    width = float(page.get("width_pt") or 0.0)
    height = float(page.get("height_pt") or 0.0)
    relative = [
        round(box[0] / width, 6),
        round(box[1] / height, 6),
        round(box[2] / width, 6),
        round(box[3] / height, 6),
    ] if width > 0 and height > 0 else None
    role = "math" if semantic == "equation" else semantic
    return {
        "status": "usable",
        "page": page_no,
        "pageBox": {"widthPt": width, "heightPt": height},
        "layoutMode": "lines-only-free-layout",
        "slot": {
            "id": slot_id,
            "source": "lines-only.flow",
            "type": "text",
            "semanticType": semantic,
        },
        "column": {"index": None, "role": "main", "box": None, "spanning": False},
        "box": {"absolutePt": box, "relativePage": relative, "source": "mathpix-lines-region"},
        "placement": "normal-flow",
        "styleHint": {
            "role": role,
            "semanticType": semantic,
            "source": "mathpix-lines-semantic-type",
        },
        "builderUse": {
            "safeForFlowOrdering": True,
            "requiresPositionedFrame": False,
            "requiresVisualPlacement": False,
        },
        "linesOnlyOrder": order,
    }


def _page_setup_estimate(pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pages:
        return None
    widths = [float(page.get("width_pt") or 0.0) for page in pages if float(page.get("width_pt") or 0.0) > 0]
    heights = [float(page.get("height_pt") or 0.0) for page in pages if float(page.get("height_pt") or 0.0) > 0]
    if not widths or not heights:
        return None
    width = sorted(widths)[len(widths) // 2]
    height = sorted(heights)[len(heights) // 2]
    content_boxes = [
        box
        for page in pages
        for item in page.get("flow", []) or []
        for box in [_box_from_value(item.get("bbox"))]
        if box is not None
    ]
    if content_boxes:
        left = max(18.0, min(box[0] for box in content_boxes))
        right = max(18.0, width - max(box[2] for box in content_boxes))
        top = max(24.0, min(box[1] for box in content_boxes))
        bottom = max(24.0, height - max(box[3] for box in content_boxes))
    else:
        left = right = 36.0
        top = bottom = 36.0
    if left + right >= width * 0.78:
        left = right = 36.0
    if top + bottom >= height * 0.55:
        top = bottom = 36.0
    return {
        "pageWidthPt": round(width, 3),
        "pageHeightPt": round(height, 3),
        "marginSource": "mathpix-lines-content-envelope",
        "mirrorMargins": False,
        "insideMarginPt": None,
        "outsideMarginPt": None,
        "leftMarginPt": round(left, 3),
        "rightMarginPt": round(right, 3),
        "topMarginPt": round(top, 3),
        "bottomMarginPt": round(bottom, 3),
        "mainFlowWidthPt": round(max(120.0, width - left - right), 3),
    }


def _box_from_value(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    return box if box[2] > box[0] and box[3] > box[1] else None


def build_lines_only_contract(lines_path: Path, page_width_pt: float = 595.276) -> dict[str, Any]:
    """Adapt raw Mathpix Lines to the existing page_structure/page_layout/build contracts.

    Source-pure by design: no PDF, Markdown or DOCX is read. Geometry and page setup
    are derived only from the Lines page envelope and Lines object regions.
    """
    line_map = build_mathpix_line_layout_map(Path(lines_path), None)
    page_structure_pages: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    layout_order_by_slot: dict[str, int] = {}
    visual_unresolved: list[dict[str, Any]] = []
    global_order = 0

    for line_page in line_map.get("pages", []) or []:
        page_no = int(line_page.get("page") or 0)
        width_pt, height_pt, sx, sy = _page_size(line_page, page_width_pt)
        flow: list[dict[str, Any]] = []
        visuals: list[dict[str, Any]] = []

        records = list(line_page.get("objects", []) or [])
        records.sort(key=lambda record: (int(record.get("line") or 10**9), float((_box_px(record) or [0, 0, 0, 0])[1]), float((_box_px(record) or [0, 0, 0, 0])[0])))

        for record in records:
            record_type = str(record.get("type") or "")
            box_px = _box_px(record)
            if box_px is None:
                continue
            box = _scale_box(box_px, sx, sy)
            record_id = str(record.get("id") or f"lines-{page_no}-{global_order}")
            semantic = _SEMANTIC.get(record_type, record_type or "paragraph")

            if record_type == "diagram":
                visuals.append({
                    "id": record_id,
                    "kind": "figure",
                    "bbox": box,
                    "placement": "floating",
                    "wrap": "none",
                    "source": "mathpix-lines-only",
                })
                visual_unresolved.append({
                    "page": page_no,
                    "id": record_id,
                    "reason": "Lines provides diagram geometry/semantics but no renderable asset bytes in result.lines.json",
                })
                continue

            if record_type not in _TEXT_TYPES:
                continue
            text = _text(record)
            if not text:
                continue

            slot_id = f"flow-{record_id}"
            flow_item = {
                "id": slot_id,
                "type": "text",
                "semantic_type": semantic,
                "bbox": box,
                "text": text,
                "content_source": "mathpix-lines-only",
            }
            flow.append(flow_item)
            layout_order_by_slot[f"{page_no}:{slot_id}"] = global_order

            layout_contract = _layout_contract(
                {"page": page_no, "width_pt": width_pt, "height_pt": height_pt},
                slot_id,
                semantic,
                box,
                global_order,
            )
            typography = _typography(record, sy)
            row = {
                "markdownId": f"lines:{record_id}",
                "markdownType": semantic,
                "markdownOrder": global_order,
                "markdownText": text,
                "authoritativeContent": {
                    "text": text,
                    "plainText": text,
                    "source": "mathpix-lines-only",
                },
                "rawMarkdown": "",
                "pdfTypography": typography,
                "pdfGeometry": {"bbox": box, "source": "mathpix-lines-region-scaled"},
                "pdfWitness": {},
                "layout": {
                    "status": "layout-slot",
                    "matchMode": "lines-only-direct",
                    "score": 100.0,
                    "page": page_no,
                    "slotId": slot_id,
                    "slotSource": "lines-only.flow",
                    "slotType": "text",
                    "semanticType": semantic,
                    "bbox": box,
                    "columnIndex": None,
                    "columnRole": "main",
                    "spanning": False,
                    "flowOrder": len(flow) - 1,
                    "wordFlowOrder": global_order,
                },
                "layoutContract": layout_contract,
                "wordParagraph": {
                    "geometry": {
                        "source": "mathpix-lines-only",
                        "bboxPt": box,
                        "columnBoxPt": None,
                        "leftIndentPt": None,
                        "rightIndentPt": None,
                        "firstLineIndentPt": 0.0,
                        "hangingIndentPt": 0.0,
                        "alignment": {"value": "left", "confidence": "low", "source": "lines-only-default"},
                        "lineHeightPt": None,
                        "lineHeightSource": None,
                        "lineCount": 1,
                        "lineBoxes": [],
                    },
                    "typography": typography,
                    "spacing": {},
                    "pageColumns": {"layoutMode": "lines-only-free-layout", "columns": [], "columnCount": 1, "source": "lines-only"},
                    "placement": "normal-flow",
                    "sourcePolicy": {
                        "content": "mathpix-lines",
                        "geometry": "mathpix-lines",
                        "typography": "mathpix-lines",
                        "nativeDonor": None,
                    },
                },
                "docxDonor": None,
            }
            layout_contract["wordParagraph"] = row["wordParagraph"]
            layout_contract["authoritativeContent"] = row["authoritativeContent"]
            row["layoutContract"] = layout_contract
            rows.append(row)
            global_order += 1

        page_structure_pages.append({
            "page": page_no,
            "width_pt": round(width_pt, 3),
            "height_pt": round(height_pt, 3),
            "layout_mode": "single_column",
            "main_column": {"x0": 0.0, "y0": 0.0, "x1": round(width_pt, 3), "y1": round(height_pt, 3)},
            "columns": [],
            "flow": flow,
            "visual_groups": visuals,
            "callouts": [],
            "source": "mathpix-lines-only",
        })

    page_structure = {
        "version": VERSION,
        "source": "mathpix-lines-only",
        "pages": page_structure_pages,
        "policy": "No PDF, Markdown or DOCX evidence. Lines text/type/order/region are adapted directly to the canonical page_structure shape.",
    }
    page_setup = _page_setup_estimate(page_structure_pages)
    layout_spine = {
        "version": VERSION,
        "policy": "Lines-only adapter emits the same builder-facing row/layout contract shape used by the existing maps-first boundary.",
        "layoutPreflight": {
            "version": VERSION,
            "source": "mathpix-lines-page-envelope-and-content-envelope",
            "pageCount": len(page_structure_pages),
            "pageSetupEstimate": page_setup,
            "columnProfile": {
                "twoColumnPageCount": 0,
                "singleColumnPageCount": len(page_structure_pages),
                "twoColumnPageRatio": 0.0,
                "medianColumnWidthPt": None,
                "medianGutterPt": None,
                "policy": "L0 does not infer Word columns",
            },
            "localTypographyPolicy": {
                "fontSize": "mathpix-lines-font-size-scaled-with-page-envelope",
                "lineHeight": "unresolved-in-L0",
                "scope": "flow-item-local",
            },
        },
        "layoutOrderBySlot": layout_order_by_slot,
        "rows": rows,
        "linesOnly": {
            "source": str(Path(lines_path)),
            "visualUnresolved": visual_unresolved,
            "policy": "Diagram objects are preserved geometrically but remain unresolved until a later route supplies renderable assets.",
        },
    }
    build_contract = build_build_contract(layout_spine)
    build_contract["sourceAuthority"] = {
        "content": "mathpix-lines",
        "layout": "mathpix-lines",
        "typography": "mathpix-lines",
        "nativeDonor": None,
    }

    return {
        "version": VERSION,
        "lineLayoutMap": line_map,
        "pageStructure": page_structure,
        "pageLayoutSpine": layout_spine,
        "buildContract": build_contract,
        "summary": {
            "pageCount": len(page_structure_pages),
            "textRowCount": len(rows),
            "visualUnresolvedCount": len(visual_unresolved),
            "buildReadyCount": int((build_contract.get("summary") or {}).get("readyCount") or 0),
            "buildUnresolvedCount": int((build_contract.get("summary") or {}).get("unresolvedCount") or 0),
        },
    }


__all__ = ["build_lines_only_contract"]
