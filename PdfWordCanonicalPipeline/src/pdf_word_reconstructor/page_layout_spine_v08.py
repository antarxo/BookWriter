from __future__ import annotations

from typing import Any

from .page_layout_spine_v07 import build_page_layout_spine as _build_v07

VERSION = "page-layout-spine-0.8.2"
TEXT_TYPES = {"paragraph", "heading", "title", "author", "caption", "list", "latex_list", "table", "latex_table"}
RENDER_TEXT_TYPES = {"paragraph", "heading", "title", "author", "caption", "list", "latex_list"}


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _page_lookup(page_structure: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    return {
        int(page.get("page") or 0): page
        for page in (page_structure or {}).get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }


def _column_index(page: dict[str, Any] | None, box: list[float]) -> int | None:
    columns = list((page or {}).get("columns", []) or [])
    if len(columns) < 2:
        return None
    cx = (box[0] + box[2]) / 2.0
    best: tuple[float, int] | None = None
    for index, col in enumerate(columns):
        try:
            ccx = (float(col.get("x0")) + float(col.get("x1"))) / 2.0
        except (TypeError, ValueError):
            continue
        distance = abs(cx - ccx)
        if best is None or distance < best[0]:
            best = (distance, index)
    return best[1] if best is not None else None


def _relative(box: list[float], page: dict[str, Any] | None) -> list[float] | None:
    try:
        width = float((page or {}).get("width_pt") or 0.0)
        height = float((page or {}).get("height_pt") or 0.0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return [round(box[0] / width, 6), round(box[1] / height, 6), round(box[2] / width, 6), round(box[3] / height, 6)]


def _region_is_text_witness(page: dict[str, Any] | None, region: str, item: dict[str, Any]) -> bool:
    granularity = str(item.get("pdfRowGranularity") or "")
    if granularity in {"pdf-line", "pdf-line-cluster", "mathpix-line", "mathpix-lines-text-object"}:
        return True
    for flow_item in (page or {}).get("flow", []) or []:
        if str(flow_item.get("type") or "") != "text":
            continue
        if str(flow_item.get("id") or "") == region:
            return True
    return False


def _materialize_renderer_text_slots(
    result: dict[str, Any],
    page_structure: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bridge usable text contracts into the mature renderer's flow schema.

    The legacy renderer consumes page_structure.pages[].flow. Package-first runs can
    have usable Markdown/PDF text contracts without a corresponding physical text
    flow object. In that case create only the missing renderer slot, using the
    already-resolved contract page+bbox. No geometry or placement is invented.
    """
    pages = _page_lookup(page_structure)
    created: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    order_map = result.setdefault("layoutOrderBySlot", {})

    for row in result.get("rows", []) or []:
        kind = str(row.get("markdownType") or "")
        if kind not in RENDER_TEXT_TYPES:
            continue
        contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
        layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
        if str(contract.get("status") or "") != "usable":
            continue
        try:
            page_no = int(layout.get("page") or contract.get("page") or 0)
        except (TypeError, ValueError):
            page_no = 0
        box = _bbox(layout.get("bbox")) or _bbox((contract.get("box") or {}).get("absolutePt"))
        page = pages.get(page_no)
        if page_no <= 0 or page is None or box is None:
            rejected.append({
                "markdownId": row.get("markdownId"),
                "page": page_no or None,
                "reason": "usable-text-contract-lacks-page-or-bbox",
            })
            continue

        current_slot_id = str(layout.get("slotId") or "")
        matching_flow = None
        for flow_item in page.get("flow", []) or []:
            if str(flow_item.get("type") or "") != "text":
                continue
            if current_slot_id and str(flow_item.get("id") or "") == current_slot_id:
                matching_flow = flow_item
                break
        if matching_flow is not None:
            existing.append({
                "markdownId": row.get("markdownId"),
                "page": page_no,
                "slotId": current_slot_id,
            })
            continue

        markdown_id = str(row.get("markdownId") or "")
        if not markdown_id:
            rejected.append({"markdownId": None, "page": page_no, "reason": "missing-markdown-id"})
            continue
        renderer_slot_id = f"contract-text-{markdown_id}"
        if any(str(item.get("id") or "") == renderer_slot_id for item in page.get("flow", []) or []):
            rejected.append({
                "markdownId": markdown_id,
                "page": page_no,
                "slotId": renderer_slot_id,
                "reason": "renderer-slot-id-collision",
            })
            continue

        col_index = layout.get("columnIndex")
        page.setdefault("flow", []).append({
            "id": renderer_slot_id,
            "type": "text",
            "semantic_type": "heading" if kind in {"heading", "title"} else ("caption" if kind == "caption" else "body"),
            "bbox": box,
            "column_index": col_index,
            "spanning": bool(layout.get("spanning")),
            "region_ids": [],
            "content_source": "page-layout-spine-contract-adapter",
            "markdown_id": markdown_id,
        })

        old_slot_id = current_slot_id or None
        layout["sourceSlotId"] = old_slot_id
        layout["slotId"] = renderer_slot_id
        layout["slotSource"] = "page_layout_spine.contract_text_slot"
        layout["slotType"] = "text"
        layout["semanticType"] = kind
        row["layout"] = layout

        slot = contract.get("slot") if isinstance(contract.get("slot"), dict) else {}
        slot["sourceSlotId"] = old_slot_id
        slot["id"] = renderer_slot_id
        slot["source"] = "page_layout_spine.contract_text_slot"
        slot["type"] = "text"
        slot["semanticType"] = kind
        contract["slot"] = slot
        row["layoutContract"] = contract

        flow_order = layout.get("wordFlowOrder")
        if flow_order is None:
            flow_order = layout.get("flowOrder")
        if flow_order is None:
            flow_order = row.get("markdownOrder")
        try:
            order_map[f"{page_no}:{renderer_slot_id}"] = int(flow_order)
        except (TypeError, ValueError):
            pass

        created.append({
            "markdownId": markdown_id,
            "page": page_no,
            "slotId": renderer_slot_id,
            "sourceSlotId": old_slot_id,
            "bbox": box,
            "type": kind,
        })

    audit = {
        "version": "renderer-text-slot-adapter-0.1",
        "createdCount": len(created),
        "existingCount": len(existing),
        "rejectedCount": len(rejected),
        "policy": "materialize only usable text contracts with resolved page+bbox into page_structure.flow; no invented geometry, no renderer changes",
        "created": created[:160],
        "rejected": rejected[:120],
    }
    result["rendererTextSlotAdapter"] = audit
    result.setdefault("summary", {})["rendererTextSlotAdapter"] = {
        "createdCount": len(created),
        "existingCount": len(existing),
        "rejectedCount": len(rejected),
    }
    if isinstance(page_structure, dict):
        page_structure["rendererTextSlotAdapter"] = audit
    return audit


def build_page_layout_spine(
    markdown_pdf_spine: dict[str, Any] | None,
    page_structure: dict[str, Any] | None,
    docx_donor_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = _build_v07(markdown_pdf_spine, page_structure, docx_donor_map)
    source_by_id = {
        str(item.get("id") or ""): item
        for item in (markdown_pdf_spine or {}).get("items", []) or []
        if item.get("id")
    }
    pages = _page_lookup(page_structure)
    recovered: list[dict[str, Any]] = []
    rejected_non_text_regions: list[dict[str, Any]] = []

    for row in result.get("rows", []) or []:
        layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
        if str(layout.get("status") or "") != "no-layout-slot":
            continue
        item = source_by_id.get(str(row.get("markdownId") or "")) or {}
        kind = str(item.get("type") or "")
        if kind not in TEXT_TYPES:
            continue
        region = str(item.get("pdfRegion") or "")
        box = _bbox(item.get("bbox"))
        try:
            page_no = int(item.get("pdfPage") or 0)
        except (TypeError, ValueError):
            page_no = 0
        if not region or not box or page_no <= 0:
            continue

        page = pages.get(page_no)
        if not _region_is_text_witness(page, region, item):
            rejected_non_text_regions.append({
                "markdownId": row.get("markdownId"),
                "page": page_no,
                "pdfRegion": region,
                "type": kind,
                "pdfRowGranularity": item.get("pdfRowGranularity"),
                "reason": "direct-text-recovery-rejected-non-text-region",
            })
            continue

        col_index = _column_index(page, box)
        two_columns = str((page or {}).get("layout_mode") or "") == "two_columns" and col_index is not None
        placement = "word-column-flow" if two_columns else "normal-flow"
        column_role = f"col-{col_index}" if col_index is not None else "main"

        layout.update({
            "status": "layout-slot",
            "matchMode": "direct-pdf-text-witness",
            "score": 100.0,
            "page": page_no,
            "slotId": region,
            "parentSlotId": item.get("pdfParentRegion"),
            "slotSource": "markdown_pdf_spine.pdf_region",
            "slotType": "text",
            "semanticType": kind,
            "bbox": box,
            "columnIndex": col_index,
            "columnRole": column_role,
            "spanning": False,
            "flowOrder": item.get("pdfLineIndex") or item.get("orderIndex"),
        })
        row["layout"] = layout

        contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
        contract["status"] = "usable"
        contract["page"] = page_no
        contract["layoutMode"] = (page or {}).get("layout_mode")
        contract["slot"] = {
            "id": region,
            "source": "markdown_pdf_spine.pdf_region",
            "type": "text",
            "semanticType": kind,
        }
        contract["box"] = {
            "absolutePt": box,
            "relativePage": _relative(box, page),
            "source": "pdf-witness",
        }
        contract["placement"] = placement
        column = contract.get("column") if isinstance(contract.get("column"), dict) else {}
        column.update({"index": col_index, "role": column_role, "spanning": False})
        contract["column"] = column
        builder_use = contract.get("builderUse") if isinstance(contract.get("builderUse"), dict) else {}
        builder_use.update({
            "safeForFlowOrdering": False,
            "requiresPositionedFrame": False,
            "requiresVisualPlacement": False,
        })
        contract["builderUse"] = builder_use
        row["layoutContract"] = contract

        word = row.get("wordParagraph") if isinstance(row.get("wordParagraph"), dict) else {}
        word["placement"] = placement
        row["wordParagraph"] = word
        recovered.append({
            "markdownId": row.get("markdownId"),
            "page": page_no,
            "pdfRegion": region,
            "type": kind,
            "placement": placement,
        })

    _materialize_renderer_text_slots(result, page_structure)

    result["version"] = VERSION
    result["directPdfWitnessRecovery"] = {
        "recoveredCount": len(recovered),
        "rejectedNonTextRegionCount": len(rejected_non_text_regions),
        "policy": "recover only no-layout-slot text rows whose pdfRegion is independently text-granular; visual/image regions cannot be promoted to text slots",
        "items": recovered[:120],
        "rejected": rejected_non_text_regions[:120],
    }
    summary = result.setdefault("summary", {})
    summary["directPdfWitnessRecoveryCount"] = len(recovered)
    summary["directPdfWitnessRejectedNonTextRegionCount"] = len(rejected_non_text_regions)
    return result


__all__ = ["build_page_layout_spine"]
