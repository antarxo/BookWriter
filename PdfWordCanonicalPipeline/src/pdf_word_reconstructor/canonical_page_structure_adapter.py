from __future__ import annotations

from typing import Any


VERSION = "canonical-page-structure-adapter-0.1"
_TEXT_SEMANTICS = {"paragraph", "heading", "caption"}


def _box(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _intersection_fraction(inner: list[float], outer: list[float] | None) -> float:
    if outer is None:
        return 0.0
    x0 = max(inner[0], outer[0])
    y0 = max(inner[1], outer[1])
    x1 = min(inner[2], outer[2])
    y1 = min(inner[3], outer[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    intersection = (x1 - x0) * (y1 - y0)
    area = max(1.0, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return intersection / area


def _line_boxes_by_id(page: dict[str, Any]) -> dict[str, list[float]]:
    line_page = page.get("mathpixLinePageMap") if isinstance(page.get("mathpixLinePageMap"), dict) else {}
    result: dict[str, list[float]] = {}
    for record in line_page.get("objects", []) or []:
        record_id = str(record.get("id") or "")
        bbox = record.get("bbox_pt") if isinstance(record.get("bbox_pt"), dict) else {}
        if not record_id or not bbox:
            continue
        box = _box([bbox.get("x0"), bbox.get("y0"), bbox.get("x1"), bbox.get("y1")])
        if box is not None:
            result[record_id] = box
    return result


def _active_column_index(page: dict[str, Any], box: list[float]) -> tuple[int | None, bool]:
    columns = [column for column in page.get("columns", []) or [] if isinstance(column, dict)]
    if len(columns) != 2 or str(page.get("layout_mode") or "") != "two_columns":
        return None, False
    left = _box([columns[0].get("x0"), columns[0].get("y0"), columns[0].get("x1"), columns[0].get("y1")])
    right = _box([columns[1].get("x0"), columns[1].get("y0"), columns[1].get("x1"), columns[1].get("y1")])
    if left is None or right is None:
        return None, False
    if box[0] < left[2] and box[2] > right[0]:
        return None, True
    center_x = (box[0] + box[2]) / 2.0
    left_center = (left[0] + left[2]) / 2.0
    right_center = (right[0] + right[2]) / 2.0
    return (0 if abs(center_x - left_center) <= abs(center_x - right_center) else 1), False


def apply_canonical_evidence_to_page_structure(
    page_structure: dict[str, Any],
    canonical_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Materialize already-resolved canonical text blocks into page_structure.

    Canonical fusion owns text semantics/content, Mathpix Lines own local text
    geometry, and the existing page_structure owns global page topology and PDF
    visual objects. This adapter does not rematch, infer new geometry, or create
    visual/equation objects.
    """
    pages = {
        int(page.get("page") or 0): page
        for page in page_structure.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    line_boxes = {page_no: _line_boxes_by_id(page) for page_no, page in pages.items()}

    materialized: list[dict[str, Any]] = []
    rail_materialized: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []

    # Package-first text authority is canonical MMD+Lines. Preserve all existing
    # visual flow objects, but replace PDF-derived text flow/callouts with the
    # canonical text blocks to avoid mixed authorities and duplicate prose.
    for page in pages.values():
        page["flow"] = [item for item in page.get("flow", []) or [] if str(item.get("type") or "") != "text"]
        page["callouts"] = [
            item for item in page.get("callouts", []) or []
            if str(item.get("content_source") or "") != "canonical-evidence-fusion"
        ]

    for block in canonical_evidence.get("blocks", []) or []:
        semantic = str(((block.get("semantic") or {}).get("type")) or "")
        if semantic not in _TEXT_SEMANTICS:
            unsupported.append({
                "canonicalId": block.get("id"),
                "semanticType": semantic,
                "reason": "non-text-block-owned-by-existing-visual-or-equation-path",
            })
            continue
        page_no = int(((block.get("pageAssignment") or {}).get("physicalPage")) or 0)
        page = pages.get(page_no)
        geometry = block.get("geometry") if isinstance(block.get("geometry"), dict) else {}
        line_ids = [str(value) for value in geometry.get("lineIds", []) or [] if value]
        boxes = [line_boxes.get(page_no, {}).get(line_id) for line_id in line_ids]
        box = _union([value for value in boxes if value is not None])
        text = str(((block.get("content") or {}).get("text")) or "").strip()
        if page is None or box is None or not text:
            unresolved.append({
                "canonicalId": block.get("id"),
                "page": page_no or None,
                "semanticType": semantic,
                "lineIds": line_ids,
                "reason": "missing-page-lines-bbox-or-content",
            })
            continue

        item_id = f"canonical-{block.get('id')}"
        rail_box = _box(page.get("outer_rail_box"))
        in_outer_rail = _intersection_fraction(box, rail_box) >= 0.65
        common = {
            "id": item_id,
            "bbox": [round(value, 3) for value in box],
            "text": text,
            "content_source": "canonical-evidence-fusion",
            "canonical_block_id": block.get("id"),
            "markdown_ids": list(((block.get("content") or {}).get("markdownIds")) or []),
            "mathpix_line_ids": line_ids,
            "mathpix_line_numbers": list(geometry.get("lineNumbers") or []),
            "typography_evidence": block.get("typographyEvidence") or {},
        }
        if in_outer_rail:
            page.setdefault("callouts", []).append({
                **common,
                "semantic": {"type": semantic, "source": "canonical-evidence-fusion"},
                "semantic_type": semantic,
                "contained_visual_groups": [],
                "canonical_outer_rail": True,
            })
            rail_materialized.append({"canonicalId": block.get("id"), "page": page_no, "slotId": item_id})
            continue

        column_index, spanning = _active_column_index(page, box)
        flow_item = {
            **common,
            "type": "text",
            "semantic_type": semantic,
            "region_ids": line_ids,
        }
        if column_index is not None:
            flow_item["column_index"] = column_index
            flow_item["spanning"] = False
        elif spanning:
            flow_item["column_index"] = None
            flow_item["spanning"] = True
        page.setdefault("flow", []).append(flow_item)
        materialized.append({"canonicalId": block.get("id"), "page": page_no, "slotId": item_id})

    for page in pages.values():
        columns = list(page.get("columns", []) or [])
        if len(columns) == 2 and str(page.get("layout_mode") or "") == "two_columns":
            page["flow"] = sorted(
                page.get("flow", []) or [],
                key=lambda item: (
                    2 if item.get("column_index") is None else int(item.get("column_index") or 0),
                    float((item.get("bbox") or [0, 0, 0, 0])[1]),
                    float((item.get("bbox") or [0, 0, 0, 0])[0]),
                ),
            )
        else:
            page["flow"] = sorted(
                page.get("flow", []) or [],
                key=lambda item: (
                    float((item.get("bbox") or [0, 0, 0, 0])[1]),
                    float((item.get("bbox") or [0, 0, 0, 0])[0]),
                ),
            )

    report = {
        "version": VERSION,
        "materializedTextFlowCount": len(materialized),
        "materializedOuterRailTextCount": len(rail_materialized),
        "unresolvedTextBlockCount": len(unresolved),
        "nonTextBlockCount": len(unsupported),
        "policy": (
            "canonical MMD+Lines blocks materialize text only; PDF/page_structure retains global topology and visual ownership; "
            "outer-rail text uses existing mature outer_rail_box; no rematching, invented geometry, or visual duplication"
        ),
        "materialized": materialized,
        "outerRail": rail_materialized,
        "unresolved": unresolved,
        "nonText": unsupported,
    }
    page_structure["canonicalEvidenceAdapter"] = report
    return report


__all__ = ["VERSION", "apply_canonical_evidence_to_page_structure"]
