from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any

from .common import compact_text


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _overlap(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return intersection / max(1.0, min(_area(a), _area(b)))


def _column_role(slot: dict[str, Any]) -> str:
    if slot.get("spanning"):
        return "span"
    column = slot.get("columnIndex")
    if column is None:
        return "main"
    return f"col-{column}"


def _page_lookup(page_structure: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for page in (page_structure or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        result[page_no] = {
            "page": page_no,
            "widthPt": page.get("width_pt"),
            "heightPt": page.get("height_pt"),
            "layoutMode": page.get("layout_mode"),
            "mainColumn": page.get("main_column"),
            "columns": page.get("columns") or [],
        }
    return result


def _safe_median(values: list[float], default: float = 0.0) -> float:
    return float(median(values)) if values else float(default)


def _layout_preflight(page_structure: dict[str, Any] | None) -> dict[str, Any]:
    pages = list((page_structure or {}).get("pages", []) or [])
    widths = [float(page.get("width_pt") or 0.0) for page in pages if page.get("width_pt")]
    heights = [float(page.get("height_pt") or 0.0) for page in pages if page.get("height_pt")]
    page_width = _safe_median(widths, 595.276)
    page_height = _safe_median(heights, 841.89)
    main_columns = [
        {"page": int(page.get("page") or 0), **(page.get("main_column") or {})}
        for page in pages
        if isinstance(page.get("main_column"), dict)
    ]
    odd_columns = [col for col in main_columns if int(col.get("page") or 0) % 2 == 1]
    even_columns = [col for col in main_columns if int(col.get("page") or 0) % 2 == 0]
    mirror = bool(odd_columns and even_columns)
    if mirror:
        outside = _safe_median(
            [max(18.0, float(col.get("x0") or 72.0)) for col in even_columns]
            + [max(18.0, page_width - float(col.get("x1") or (page_width - 72.0))) for col in odd_columns],
            36.0,
        )
        inside = _safe_median(
            [max(18.0, page_width - float(col.get("x1") or (page_width - 72.0))) for col in even_columns]
            + [max(18.0, float(col.get("x0") or 72.0)) for col in odd_columns],
            72.0,
        )
        left = inside
        right = outside
        margin_source = "pdf-main-flow-mirror-margins"
    else:
        left = _safe_median([max(18.0, float(col.get("x0") or 72.0)) for col in main_columns], 72.0)
        right = _safe_median(
            [max(18.0, page_width - float(col.get("x1") or (page_width - 72.0))) for col in main_columns],
            72.0,
        )
        inside = None
        outside = None
        margin_source = "pdf-main-flow-margins"
    top = _safe_median([max(36.0, float(col.get("y0") or 72.0)) for col in main_columns], 72.0)
    bottom = _safe_median(
        [max(34.0, page_height - float(col.get("y1") or (page_height - 72.0))) for col in main_columns],
        72.0,
    )
    two_column_pages = [page for page in pages if page.get("layout_mode") == "two_columns"]
    column_widths = [
        max(0.0, float(column.get("x1") or 0.0) - float(column.get("x0") or 0.0))
        for page in two_column_pages
        for column in (page.get("columns") or [])
        if isinstance(column, dict)
    ]
    gutters = []
    for page in two_column_pages:
        columns = list(page.get("columns") or [])
        if len(columns) == 2:
            gutters.append(max(0.0, float(columns[1].get("x0") or 0.0) - float(columns[0].get("x1") or 0.0)))
    return {
        "version": "layout-preflight-0.1",
        "source": "page_structure-before-word-build",
        "pageCount": len(pages),
        "pageSetupEstimate": {
            "pageWidthPt": round(page_width, 3),
            "pageHeightPt": round(page_height, 3),
            "marginSource": margin_source,
            "mirrorMargins": mirror,
            "insideMarginPt": round(float(inside), 3) if inside is not None else None,
            "outsideMarginPt": round(float(outside), 3) if outside is not None else None,
            "leftMarginPt": round(left, 3),
            "rightMarginPt": round(right, 3),
            "topMarginPt": round(top, 3),
            "bottomMarginPt": round(bottom, 3),
            "mainFlowWidthPt": round(max(120.0, page_width - left - right), 3),
        },
        "columnProfile": {
            "twoColumnPageCount": len(two_column_pages),
            "singleColumnPageCount": len(pages) - len(two_column_pages),
            "twoColumnPageRatio": round(len(two_column_pages) / len(pages), 5) if pages else 0.0,
            "medianColumnWidthPt": round(_safe_median(column_widths), 3) if column_widths else None,
            "medianGutterPt": round(_safe_median(gutters), 3) if gutters else None,
            "policy": "preserve-pdf-column-slots-in-flow-contract",
        },
        "localTypographyPolicy": {
            "fontSize": "pdf-span-dominant-size-per-flow-item",
            "lineHeight": "pdf-line-pitch-per-flow-item",
            "scope": "flow-item-local-not-global-document-default",
        },
    }


def _relative_bbox(box: list[float] | None, page_info: dict[str, Any] | None) -> list[float] | None:
    if not box or not page_info:
        return None
    try:
        width = float(page_info.get("widthPt") or 0.0)
        height = float(page_info.get("heightPt") or 0.0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return [
        round(float(box[0]) / width, 6),
        round(float(box[1]) / height, 6),
        round(float(box[2]) / width, 6),
        round(float(box[3]) / height, 6),
    ]


def _column_box(page_info: dict[str, Any] | None, column_index: Any, spanning: bool) -> dict[str, Any] | None:
    if not page_info:
        return None
    if spanning:
        columns = page_info.get("columns") or []
        if columns:
            return {
                "x0": round(min(float(col.get("x0", 0.0)) for col in columns), 3),
                "x1": round(max(float(col.get("x1", 0.0)) for col in columns), 3),
                "y0": round(min(float(col.get("y0", 0.0)) for col in columns), 3),
                "y1": round(max(float(col.get("y1", 0.0)) for col in columns), 3),
            }
        return page_info.get("mainColumn")
    try:
        index = int(column_index)
    except (TypeError, ValueError):
        return page_info.get("mainColumn")
    columns = page_info.get("columns") or []
    if 0 <= index < len(columns):
        return columns[index]
    return page_info.get("mainColumn")


def _placement_policy(slot: dict[str, Any] | None, page_info: dict[str, Any] | None) -> str:
    if not slot:
        return "unmapped"
    source = str(slot.get("source") or "")
    if source == "page_structure.callout":
        return "positioned-text-frame"
    if source == "page_structure.visual_group":
        return "floating-visual" if slot.get("placement") == "floating" else "inline-visual"
    if slot.get("spanning"):
        return "spanning-text-frame"
    if page_info and page_info.get("layoutMode") == "two_columns" and slot.get("columnIndex") is not None:
        return "word-column-flow"
    return "normal-flow"


def _style_hint(markdown_type: Any, slot: dict[str, Any] | None, docx_donor: dict[str, Any] | None) -> dict[str, Any]:
    semantic = (slot or {}).get("semanticType")
    donor_type = (docx_donor or {}).get("donorType")
    docx_style = (docx_donor or {}).get("style")
    if semantic == "heading" or donor_type in {"heading", "title"}:
        role = "heading"
    elif semantic == "caption":
        role = "caption"
    elif semantic == "equation" or donor_type in {"math-omml", "mixed-omml"}:
        role = "math"
    elif (slot or {}).get("source") == "page_structure.callout":
        role = "callout"
    elif (slot or {}).get("type") == "visual":
        role = "visual"
    else:
        role = "body"
    return {
        "role": role,
        "markdownType": markdown_type,
        "semanticType": semantic,
        "docxStyle": docx_style,
        "docxDonorType": donor_type,
        "source": "semantic-docx-donor-hint" if docx_style or donor_type else "semantic-hint",
    }


def _layout_contract(
    item: dict[str, Any],
    slot: dict[str, Any] | None,
    page_info: dict[str, Any] | None,
    docx_donor: dict[str, Any] | None,
) -> dict[str, Any]:
    page_no = (slot or {}).get("page") or item.get("pdfPage") or item.get("inferredPage") or item.get("markdownPageHint")
    bbox = _bbox((slot or {}).get("bbox")) or _bbox(item.get("bbox"))
    column_index = (slot or {}).get("columnIndex")
    spanning = bool((slot or {}).get("spanning"))
    placement = _placement_policy(slot, page_info)
    safe_for_flow = bool(
        slot
        and (slot.get("source") == "page_structure.flow")
        and not spanning
        and placement in {"normal-flow", "word-column-flow"}
    )
    return {
        "status": "usable" if slot else "missing-layout-slot",
        "page": page_no,
        "pageBox": {
            "widthPt": (page_info or {}).get("widthPt"),
            "heightPt": (page_info or {}).get("heightPt"),
        },
        "layoutMode": (page_info or {}).get("layoutMode"),
        "slot": {
            "id": (slot or {}).get("slotId"),
            "source": (slot or {}).get("source"),
            "type": (slot or {}).get("type"),
            "semanticType": (slot or {}).get("semanticType"),
        },
        "column": {
            "index": column_index,
            "role": _column_role(slot or {}),
            "box": _column_box(page_info, column_index, spanning),
            "spanning": spanning,
        },
        "box": {
            "absolutePt": bbox,
            "relativePage": _relative_bbox(bbox, page_info),
            "source": "page_structure-slot" if slot and slot.get("bbox") else "pdf-witness",
        },
        "placement": placement,
        "styleHint": _style_hint(item.get("type"), slot, docx_donor),
        "builderUse": {
            "safeForFlowOrdering": safe_for_flow,
            "requiresPositionedFrame": placement in {"positioned-text-frame", "spanning-text-frame"},
            "requiresVisualPlacement": placement in {"floating-visual", "inline-visual"},
        },
    }


def _layout_slots(page_structure: dict[str, Any] | None) -> tuple[dict[int, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    by_page: dict[int, list[dict[str, Any]]] = {}
    by_member: dict[str, list[dict[str, Any]]] = {}
    def add_member(member_id: Any, slot: dict[str, Any]) -> None:
        if member_id:
            by_member.setdefault(str(member_id), []).append(slot)

    for page in (page_structure or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        slots: list[dict[str, Any]] = []
        for order, item in enumerate(page.get("flow", []) or []):
            slot = {
                "slotId": item.get("id"),
                "source": "page_structure.flow",
                "page": page_no,
                "order": order,
                "type": item.get("type"),
                "semanticType": item.get("semantic_type"),
                "bbox": item.get("bbox"),
                "columnIndex": item.get("column_index"),
                "spanning": bool(item.get("spanning")),
                "visualGroupId": item.get("visual_group_id"),
            }
            slots.append(slot)
            add_member(item.get("id"), slot)
            add_member(item.get("visual_group_id"), slot)
        for order, group in enumerate(page.get("visual_groups", []) or []):
            slot = {
                "slotId": group.get("id"),
                "source": "page_structure.visual_group",
                "page": page_no,
                "order": order,
                "type": "visual",
                "semanticType": group.get("kind"),
                "bbox": group.get("bbox"),
                "columnIndex": None,
                "spanning": group.get("placement") == "floating",
                "visualGroupId": group.get("id"),
                "placement": group.get("placement"),
            }
            slots.append(slot)
            add_member(group.get("id"), slot)
            for member_id in group.get("member_ids", []) or []:
                add_member(member_id, slot)
        for order, callout in enumerate(page.get("callouts", []) or []):
            slot = {
                "slotId": callout.get("id"),
                "source": "page_structure.callout",
                "page": page_no,
                "order": order,
                "type": "callout",
                "semanticType": "callout",
                "bbox": callout.get("bbox"),
                "columnIndex": None,
                "spanning": True,
            }
            slots.append(slot)
            add_member(callout.get("id"), slot)
        by_page[page_no] = slots
    return by_page, by_member


def _member_slot(slots_by_member: dict[str, list[dict[str, Any]]], member_id: str, page_no: int) -> dict[str, Any] | None:
    candidates = slots_by_member.get(member_id) or []
    page_candidates = [slot for slot in candidates if int(slot.get("page") or 0) == page_no]
    if len(page_candidates) == 1:
        return page_candidates[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _slot_compatible(item: dict[str, Any], slot: dict[str, Any] | None) -> bool:
    if not slot:
        return False
    kind = str(item.get("type") or "").strip().lower()
    slot_type = str(slot.get("type") or "").strip().lower()
    source = str(slot.get("source") or "")
    text_kinds = {"paragraph", "heading", "title", "author", "caption", "list", "latex_list", "table", "latex_table"}
    visual_kinds = {"image", "figure"}
    if kind in text_kinds:
        return slot_type in {"text", "callout"} and source != "page_structure.visual_group"
    if kind in visual_kinds:
        return slot_type == "visual" or source == "page_structure.visual_group"
    if kind == "display_equation":
        return slot_type in {"math", "visual"} or str(slot.get("semanticType") or "").lower() == "equation"
    return True


def _slot_column_index(slot: dict[str, Any], page_info: dict[str, Any] | None) -> int:
    column_index = slot.get("columnIndex")
    try:
        return int(column_index)
    except (TypeError, ValueError):
        pass
    if not page_info:
        return 0
    columns = page_info.get("columns") or []
    box = _bbox(slot.get("bbox"))
    if not box or len(columns) < 2:
        return 0
    center_x = (box[0] + box[2]) / 2.0
    best_index = 0
    best_distance: float | None = None
    for index, column in enumerate(columns):
        try:
            col_center = (float(column.get("x0")) + float(column.get("x1"))) / 2.0
        except (TypeError, ValueError):
            continue
        distance = abs(center_x - col_center)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_index = index
    return best_index


def _word_flow_order_by_slot(
    slots_by_page: dict[int, list[dict[str, Any]]],
    pages_by_no: dict[int, dict[str, Any]],
) -> dict[str, int]:
    order_by_slot: dict[str, int] = {}
    order = 0
    for page_no in sorted(slots_by_page):
        page_info = pages_by_no.get(page_no)
        page_slots = [
            slot for slot in slots_by_page.get(page_no, [])
            if slot.get("source") == "page_structure.flow" and not slot.get("spanning")
        ]

        def key(slot: dict[str, Any]) -> tuple[int, float, float, int]:
            box = _bbox(slot.get("bbox")) or [0.0, 0.0, 0.0, 0.0]
            if page_info and page_info.get("layoutMode") == "two_columns":
                column_index = _slot_column_index(slot, page_info)
            else:
                column_index = 0
            return (column_index, float(box[1]), float(box[0]), int(slot.get("order") or 0))

        for slot in sorted(page_slots, key=key):
            slot_id = slot.get("slotId")
            if not slot_id:
                continue
            qualified = f"{page_no}:{slot_id}"
            if qualified not in order_by_slot:
                order_by_slot[qualified] = order
                order += 1
    return order_by_slot


def _line_slot_from_parent(item: dict[str, Any], parent: dict[str, Any] | None, page_no: int) -> dict[str, Any] | None:
    if item.get("pdfRowGranularity") not in {"pdf-line", "pdf-line-cluster"}:
        return None
    bbox = _bbox(item.get("bbox"))
    pdf_region = str(item.get("pdfRegion") or "")
    if not bbox or not pdf_region:
        return None
    return {
        "slotId": pdf_region,
        "source": "markdown_pdf_spine.pdf_line",
        "page": page_no,
        "order": item.get("pdfLineIndex"),
        "type": "text",
        "semanticType": (parent or {}).get("semanticType") or item.get("type"),
        "bbox": bbox,
        "columnIndex": (parent or {}).get("columnIndex"),
        "spanning": bool((parent or {}).get("spanning")),
        "parentSlotId": (parent or {}).get("slotId"),
    }


def _markdown_position_slot(item: dict[str, Any], page_no: int) -> dict[str, Any] | None:
    if item.get("pdfRowGranularity") != "markdown-position":
        return None
    bbox = _bbox(item.get("bbox"))
    pdf_region = str(item.get("pdfRegion") or "")
    if not bbox or not pdf_region:
        return None
    kind = str(item.get("type") or "")
    return {
        "slotId": pdf_region,
        "source": "markdown_element_map.position",
        "page": page_no,
        "order": item.get("orderIndex"),
        "type": "visual" if kind in {"image", "figure", "display_equation"} else "text",
        "semanticType": "figure" if kind in {"image", "figure"} else ("equation" if kind == "display_equation" else kind),
        "bbox": bbox,
        "columnIndex": None,
        "spanning": True,
    }


def _best_slot(
    item: dict[str, Any],
    slots_by_page: dict[int, list[dict[str, Any]]],
    slots_by_member: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str, float]:
    page = item.get("pdfPage") or item.get("inferredPage") or item.get("markdownPageHint")
    try:
        page_no = int(page)
    except Exception:
        return None, "no-page", 0.0
    pdf_region = str(item.get("pdfRegion") or "")
    if pdf_region:
        slot = _member_slot(slots_by_member, pdf_region, page_no)
        if slot and _slot_compatible(item, slot):
            return slot, "pdf-region-id", 100.0
    pdf_parent = str(item.get("pdfParentRegion") or "")
    parent_slot = _member_slot(slots_by_member, pdf_parent, page_no) if pdf_parent else None
    line_slot = _line_slot_from_parent(item, parent_slot, page_no)
    if line_slot:
        return line_slot, "pdf-line-subslot", 100.0
    item_box = _bbox(item.get("bbox"))
    best: tuple[float, dict[str, Any]] | None = None
    for slot in slots_by_page.get(page_no, []) or []:
        if not _slot_compatible(item, slot):
            continue
        score = _overlap(item_box, _bbox(slot.get("bbox")))
        if best is None or score > best[0]:
            best = (score, slot)
    if best and best[0] >= 0.20:
        return best[1], "bbox-overlap", round(best[0] * 100.0, 2)
    position_slot = _markdown_position_slot(item, page_no)
    if position_slot:
        return position_slot, "markdown-position-slot", 100.0
    return None, "unplaced-layout-slot", round((best[0] if best else 0.0) * 100.0, 2)


def _docx_donor_for_markdown(markdown_id: str, docx_donor_map: dict[str, Any] | None) -> dict[str, Any] | None:
    for paragraph in (docx_donor_map or {}).get("paragraphs", []) or []:
        for link in paragraph.get("markdownLinks", []) or []:
            if str(link.get("markdownId") or "") == markdown_id:
                return {
                    "paragraphId": paragraph.get("id"),
                    "paragraphIndex": paragraph.get("index"),
                    "style": paragraph.get("style"),
                    "donorType": paragraph.get("donorType"),
                    "ommlCount": paragraph.get("ommlCount"),
                    "score": link.get("score"),
                    "status": link.get("status"),
                }
    return None


def build_page_layout_spine(
    markdown_pdf_spine: dict[str, Any] | None,
    page_structure: dict[str, Any] | None,
    docx_donor_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    layout_preflight = _layout_preflight(page_structure)
    pages_by_no = _page_lookup(page_structure)
    slots_by_page, slots_by_member = _layout_slots(page_structure)
    order_by_slot = _word_flow_order_by_slot(slots_by_page, pages_by_no)
    rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    slot_source_counts: Counter[str] = Counter()
    placement_counts: Counter[str] = Counter()
    column_role_counts: Counter[str] = Counter()
    layout_mode_counts: Counter[str] = Counter()
    style_role_counts: Counter[str] = Counter()
    builder_use_counts: Counter[str] = Counter()
    for index, item in enumerate((markdown_pdf_spine or {}).get("items", []) or []):
        markdown_id = str(item.get("id") or "")
        slot, match_mode, score = _best_slot(item, slots_by_page, slots_by_member)
        status = "layout-slot" if slot else "no-layout-slot"
        status_counts[status] += 1
        if slot:
            slot_source_counts[str(slot.get("source") or "")] += 1
        page_no = (slot or {}).get("page") or item.get("pdfPage") or item.get("inferredPage") or item.get("markdownPageHint")
        try:
            page_info = pages_by_no.get(int(page_no))
        except Exception:
            page_info = None
        docx_donor = _docx_donor_for_markdown(markdown_id, docx_donor_map)
        contract = _layout_contract(item, slot, page_info, docx_donor)
        placement_counts[str(contract.get("placement") or "unknown")] += 1
        column_role_counts[str((contract.get("column") or {}).get("role") or "unknown")] += 1
        layout_mode_counts[str(contract.get("layoutMode") or "unknown")] += 1
        style_role_counts[str(((contract.get("styleHint") or {}).get("role")) or "unknown")] += 1
        for key, enabled in (contract.get("builderUse") or {}).items():
            if enabled:
                builder_use_counts[key] += 1
        row = {
            "markdownId": markdown_id,
            "markdownType": item.get("type"),
            "markdownOrder": item.get("orderIndex", index),
            "markdownText": compact_text(str(item.get("text") or ""), 420),
            "pdfWitness": {
                "page": item.get("pdfPage"),
                "region": item.get("pdfRegion"),
                "parentRegion": item.get("pdfParentRegion"),
                "lineIndex": item.get("pdfLineIndex"),
                "rowGranularity": item.get("pdfRowGranularity"),
                "status": item.get("status"),
                "score": item.get("score"),
                "bbox": item.get("bbox"),
                "text": compact_text(str(item.get("pdfText") or ""), 420),
            },
            "layout": {
                "status": status,
                "matchMode": match_mode,
                "score": score,
                "page": (slot or {}).get("page") or item.get("pdfPage"),
                "slotId": (slot or {}).get("slotId"),
                "parentSlotId": (slot or {}).get("parentSlotId"),
                "slotSource": (slot or {}).get("source"),
                "slotType": (slot or {}).get("type"),
                "semanticType": (slot or {}).get("semanticType"),
                "bbox": (slot or {}).get("bbox"),
                "columnIndex": (slot or {}).get("columnIndex"),
                "columnRole": _column_role(slot or {}),
                "spanning": bool((slot or {}).get("spanning")),
                "flowOrder": (slot or {}).get("order"),
                "wordFlowOrder": order_by_slot.get(f"{(slot or {}).get('page')}:{(slot or {}).get('slotId')}"),
            },
            "layoutContract": contract,
            "docxDonor": docx_donor,
        }
        rows.append(row)
    rows.sort(key=lambda row: (
        int((row.get("layout") or {}).get("page") or 0),
        2 if (row.get("layout") or {}).get("columnIndex") is None else int((row.get("layout") or {}).get("columnIndex") or 0),
        float(((row.get("layout") or {}).get("bbox") or [0, 0, 0, 0])[1]),
        float(((row.get("layout") or {}).get("bbox") or [0, 0, 0, 0])[0]),
        int(row.get("markdownOrder") or 0),
    ))
    total = len(rows)
    placed = int(status_counts.get("layout-slot", 0))
    usable_contracts = sum(1 for row in rows if ((row.get("layoutContract") or {}).get("status") == "usable"))
    return {
        "version": "page-layout-spine-0.3.1",
        "policy": "Pagination/layout starts from Markdown/PDF witnesses mapped to semantically compatible page_structure slots. Text Markdown cannot bind to visual/image slots and visual Markdown cannot bind to text flow slots. layoutOrderBySlot is a complete page_structure.flow Word-flow order derived from PDF geometry and columns; Markdown rows use it as a witness, not as a partial reorder fallback.",
        "summary": {
            "rowCount": total,
            "layoutSlotCount": placed,
            "unplacedLayoutSlotCount": int(status_counts.get("no-layout-slot", 0)),
            "coverage": round(placed / total, 5) if total else 1.0,
            "contractUsableCount": usable_contracts,
            "contractCoverage": round(usable_contracts / total, 5) if total else 1.0,
            "safeFlowOrderingSlotCount": len(order_by_slot),
            "statusCounts": dict(status_counts),
            "slotSourceCounts": dict(slot_source_counts),
            "placementCounts": dict(placement_counts),
            "columnRoleCounts": dict(column_role_counts),
            "layoutModeCounts": dict(layout_mode_counts),
            "styleRoleCounts": dict(style_role_counts),
            "builderUseCounts": dict(builder_use_counts),
            "layoutPreflight": layout_preflight,
        },
        "layoutPreflight": layout_preflight,
        "layoutOrderBySlot": order_by_slot,
        "rows": rows,
    }
