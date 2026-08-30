from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .build_contract import build_build_contract
from .mathpix_lines_input import build_mathpix_line_layout_map

VERSION = "lines-only-hierarchy-contract-0.1"

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
    return [round(box[0] * sx, 3), round(box[1] * sy, 3), round(box[2] * sx, 3), round(box[3] * sy, 3)]


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
    return round(value * sy, 3) if value > 0 else None


def _typography(record: dict[str, Any], sy: float) -> dict[str, Any]:
    size = _font_size_pt(record, sy)
    return {
        "source": "mathpix-lines-only-l1",
        "confidence": "medium" if size else "low",
        "fontSizePt": {"dominant": size},
        "fontFamily": {"dominant": None},
        "emphasis": {},
        "color": {"dominant": None},
        "lineCount": 1,
        "lineBoxes": [],
    }


def _ancestor_chain(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    parent_id = str(record.get("parent_id") or "")
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            break
        result.append(parent)
        parent_id = str(parent.get("parent_id") or "")
    return result


def _column_ancestor(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if str(record.get("type") or "") == "column":
        return record
    return next((item for item in _ancestor_chain(record, by_id) if str(item.get("type") or "") == "column"), None)


def _hierarchy_rank(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> tuple[int, ...]:
    chain = list(reversed(_ancestor_chain(record, by_id)))
    ranks: list[int] = []
    parent: dict[str, Any] | None = None
    for node in [*chain, record]:
        if parent is None:
            ranks.append(int(node.get("line") or 10**8))
        else:
            children = [str(value) for value in (parent.get("children_ids") or []) if value]
            node_id = str(node.get("id") or "")
            ranks.append(children.index(node_id) if node_id in children else int(node.get("line") or 10**8))
        parent = node
    return tuple(ranks)


def _relative(box: list[float], width: float, height: float) -> list[float]:
    return [round(box[0] / width, 6), round(box[1] / height, 6), round(box[2] / width, 6), round(box[3] / height, 6)]


def _word_paragraph(box: list[float], typography: dict[str, Any], placement: str, column_box: list[float] | None) -> dict[str, Any]:
    return {
        "geometry": {
            "source": "mathpix-lines-only-l1",
            "bboxPt": box,
            "columnBoxPt": column_box,
            "leftIndentPt": 0.0 if placement == "positioned-text-frame" else None,
            "rightIndentPt": 0.0 if placement == "positioned-text-frame" else None,
            "firstLineIndentPt": 0.0,
            "hangingIndentPt": 0.0,
            "alignment": {"value": "left", "confidence": "low", "source": "lines-only-l1"},
            "lineHeightPt": None,
            "lineHeightSource": None,
            "lineCount": 1,
            "lineBoxes": [],
        },
        "typography": typography,
        "spacing": {},
        "pageColumns": {"layoutMode": "lines-hierarchy-layout", "columns": [], "columnCount": 1, "source": "mathpix-lines"},
        "placement": placement,
        "sourcePolicy": {"content": "mathpix-lines", "geometry": "mathpix-lines", "typography": "mathpix-lines", "nativeDonor": None},
    }


def build_lines_only_hierarchy_contract(lines_path: Path, page_width_pt: float = 595.276) -> dict[str, Any]:
    """LINES_ONLY_L1: use Lines hierarchy, explicit column ancestry and font sizes only.

    No PDF, Markdown or DOCX evidence is read. The output remains compatible with the
    same page_structure/page_layout/build-contract boundary consumed by the common builder.
    Narrow explicit Lines columns are represented as positioned text regions so that the
    existing renderer can preserve their physical ownership without a Lines-specific renderer.
    """
    line_map = build_mathpix_line_layout_map(Path(lines_path), None)
    pages_out: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    layout_order_by_slot: dict[str, int] = {}
    visual_unresolved: list[dict[str, Any]] = []
    global_order = 0
    hierarchy_used = 0
    column_owned = 0
    positioned_column_text = 0

    for line_page in line_map.get("pages", []) or []:
        page_no = int(line_page.get("page") or 0)
        width_pt, height_pt, sx, sy = _page_size(line_page, page_width_pt)
        records = list(line_page.get("objects", []) or [])
        by_id = {str(record.get("id")): record for record in records if record.get("id")}

        column_records = [record for record in records if str(record.get("type") or "") == "column" and _box_px(record)]
        column_records.sort(key=lambda record: (_box_px(record) or [0, 0, 0, 0])[0])
        column_index_by_id = {str(record.get("id")): index for index, record in enumerate(column_records) if record.get("id")}

        flow: list[dict[str, Any]] = []
        visuals: list[dict[str, Any]] = []
        ordered_records = sorted(records, key=lambda record: (_hierarchy_rank(record, by_id), int(record.get("line") or 10**9), float((_box_px(record) or [0, 0, 0, 0])[1]), float((_box_px(record) or [0, 0, 0, 0])[0])))

        for record in ordered_records:
            record_type = str(record.get("type") or "")
            box_px = _box_px(record)
            if box_px is None:
                continue
            box = _scale_box(box_px, sx, sy)
            record_id = str(record.get("id") or f"lines-l1-{page_no}-{global_order}")

            if record_type == "diagram":
                visuals.append({"id": record_id, "kind": "figure", "bbox": box, "placement": "floating", "wrap": "none", "source": "mathpix-lines-only-l1"})
                visual_unresolved.append({"page": page_no, "id": record_id, "reason": "Lines diagram has geometry/semantics but no asset bytes"})
                continue
            if record_type not in _TEXT_TYPES:
                continue
            text = _text(record)
            if not text:
                continue

            semantic = _SEMANTIC.get(record_type, record_type or "paragraph")
            ancestor = _column_ancestor(record, by_id)
            column_index = None
            column_box = None
            narrow_column = False
            if ancestor is not None:
                ancestor_id = str(ancestor.get("id") or "")
                column_index = column_index_by_id.get(ancestor_id)
                ancestor_box_px = _box_px(ancestor)
                if ancestor_box_px:
                    column_box = _scale_box(ancestor_box_px, sx, sy)
                    narrow_column = (column_box[2] - column_box[0]) < width_pt * 0.48
                column_owned += 1

            # Existing common renderer has one document-flow section. For L1, explicit
            # narrow column ownership is expressed through the common positioned-frame
            # contract rather than silently flattening it into main flow.
            positioned = bool(narrow_column)
            placement = "positioned-text-frame" if positioned else "normal-flow"
            spanning = positioned
            if positioned:
                positioned_column_text += 1

            slot_id = f"flow-{record_id}"
            flow_item = {
                "id": slot_id,
                "type": "text",
                "semantic_type": semantic,
                "bbox": box,
                "text": text,
                "content_source": "mathpix-lines-only-l1",
                "column_index": column_index,
                "spanning": spanning,
                "lines_parent_id": record.get("parent_id"),
                "lines_children_ids": list(record.get("children_ids") or []),
            }
            if record.get("parent_id") or record.get("children_ids"):
                hierarchy_used += 1
            flow.append(flow_item)
            layout_order_by_slot[f"{page_no}:{slot_id}"] = global_order

            typography = _typography(record, sy)
            word = _word_paragraph(box, typography, placement, column_box)
            role = "math" if semantic == "equation" else semantic
            contract = {
                "status": "usable",
                "page": page_no,
                "pageBox": {"widthPt": width_pt, "heightPt": height_pt},
                "layoutMode": "lines-hierarchy-layout",
                "slot": {"id": slot_id, "source": "lines-only-l1.flow", "type": "text", "semanticType": semantic},
                "column": {"index": column_index, "role": f"col-{column_index}" if column_index is not None else "main", "box": {"x0": column_box[0], "y0": column_box[1], "x1": column_box[2], "y1": column_box[3]} if column_box else None, "spanning": spanning},
                "box": {"absolutePt": box, "relativePage": _relative(box, width_pt, height_pt), "source": "mathpix-lines-region"},
                "placement": placement,
                "styleHint": {"role": role, "semanticType": semantic, "source": "mathpix-lines-semantic-type"},
                "builderUse": {"safeForFlowOrdering": not positioned, "requiresPositionedFrame": positioned, "requiresVisualPlacement": False},
                "wordParagraph": word,
                "authoritativeContent": {"text": text, "plainText": text, "source": "mathpix-lines-only-l1"},
            }
            row = {
                "markdownId": f"lines:{record_id}",
                "markdownType": semantic,
                "markdownOrder": global_order,
                "markdownText": text,
                "authoritativeContent": contract["authoritativeContent"],
                "rawMarkdown": "",
                "pdfTypography": typography,
                "pdfGeometry": {"bbox": box, "source": "mathpix-lines-region-scaled"},
                "pdfWitness": {},
                "layout": {
                    "status": "layout-slot", "matchMode": "lines-only-l1-direct", "score": 100.0,
                    "page": page_no, "slotId": slot_id, "slotSource": "lines-only-l1.flow", "slotType": "text",
                    "semanticType": semantic, "bbox": box, "columnIndex": column_index,
                    "columnRole": f"col-{column_index}" if column_index is not None else "main",
                    "spanning": spanning, "flowOrder": len(flow) - 1, "wordFlowOrder": global_order,
                },
                "layoutContract": contract,
                "wordParagraph": word,
                "docxDonor": None,
            }
            rows.append(row)
            global_order += 1

        # Preserve explicit Lines column containers in the canonical page map. They
        # are evidence-bearing output even when the common renderer materializes narrow
        # owned text as positioned frames for this experiment.
        columns = []
        for record in column_records:
            box_px = _box_px(record)
            if not box_px:
                continue
            box = _scale_box(box_px, sx, sy)
            columns.append({"id": record.get("id"), "x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3], "source": "mathpix-lines-column"})

        pages_out.append({
            "page": page_no,
            "width_pt": round(width_pt, 3),
            "height_pt": round(height_pt, 3),
            "layout_mode": "two_columns" if len(columns) >= 2 else "single_column",
            "main_column": {"x0": 0.0, "y0": 0.0, "x1": round(width_pt, 3), "y1": round(height_pt, 3)},
            "columns": columns,
            "flow": flow,
            "visual_groups": visuals,
            "callouts": [],
            "source": "mathpix-lines-only-l1",
        })

    page_structure = {
        "version": VERSION,
        "source": "mathpix-lines-only-l1",
        "pages": pages_out,
        "policy": "Lines hierarchy, explicit column ancestry, semantic type, geometry and font_size only; no PDF/Markdown/DOCX evidence.",
    }
    page_setup = None
    if pages_out:
        first = pages_out[0]
        page_setup = {
            "pageWidthPt": first["width_pt"], "pageHeightPt": first["height_pt"],
            "leftMarginPt": 18.0, "rightMarginPt": 18.0, "topMarginPt": 36.0, "bottomMarginPt": 34.0,
            "marginSource": "lines-page-envelope-minimal-renderer-carrier", "mirrorMargins": False,
        }
    layout_spine = {
        "version": VERSION,
        "policy": "LINES_ONLY_L1 emits the same builder-facing contract; hierarchy and explicit column ownership are preserved before rendering.",
        "layoutPreflight": {"version": VERSION, "source": "mathpix-lines-page-envelope", "pageCount": len(pages_out), "pageSetupEstimate": page_setup},
        "layoutOrderBySlot": layout_order_by_slot,
        "rows": rows,
        "linesOnly": {"source": str(Path(lines_path)), "visualUnresolved": visual_unresolved},
    }
    build_contract = build_build_contract(layout_spine)
    build_contract["sourceAuthority"] = {"content": "mathpix-lines", "layout": "mathpix-lines-hierarchy", "typography": "mathpix-lines", "nativeDonor": None}
    return {
        "version": VERSION,
        "lineLayoutMap": line_map,
        "pageStructure": page_structure,
        "pageLayoutSpine": layout_spine,
        "buildContract": build_contract,
        "summary": {
            "pageCount": len(pages_out), "textRowCount": len(rows), "visualUnresolvedCount": len(visual_unresolved),
            "hierarchyBearingTextCount": hierarchy_used, "columnOwnedTextCount": column_owned,
            "positionedColumnTextCount": positioned_column_text,
            "buildReadyCount": int((build_contract.get("summary") or {}).get("readyCount") or 0),
            "buildUnresolvedCount": int((build_contract.get("summary") or {}).get("unresolvedCount") or 0),
        },
    }


__all__ = ["build_lines_only_hierarchy_contract"]
