from __future__ import annotations

from copy import deepcopy
from typing import Any


VERSION = "preview-recovery-layer-0.2"


def _text_from_row(row: dict[str, Any]) -> str:
    for container_key in ("contentContract", "authoritativeContent"):
        container = row.get(container_key)
        if isinstance(container, dict):
            for key in ("rawMarkdown", "text", "plainText"):
                value = str(container.get(key) or "").strip()
                if value:
                    return value
    for key in ("rawMarkdown", "markdownText", "text"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _row_page(row: dict[str, Any]) -> int:
    layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
    contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
    for value in (
        layout.get("page"),
        contract.get("page"),
        row.get("page"),
        row.get("pdfPage"),
        row.get("inferredPage"),
        row.get("markdownPageHint"),
    ):
        try:
            page = int(value or 0)
        except (TypeError, ValueError):
            page = 0
        if page > 0:
            return page
    return 0


def _callout_bbox(page: dict[str, Any], ordinal: int, text: str) -> list[float]:
    width = float(page.get("width_pt") or 595.0)
    height = float(page.get("height_pt") or 842.0)
    main = page.get("main_column") if isinstance(page.get("main_column"), dict) else {}
    x0 = float(main.get("x0") or 40.0)
    x1 = float(main.get("x1") or (width - 40.0))
    top = float(main.get("y0") or 72.0)
    estimated_lines = max(3, min(16, (len(text) // 85) + 2))
    box_height = max(72.0, min(180.0, 18.0 + estimated_lines * 9.0))
    y0 = top + 6.0 + ordinal * 18.0
    y1 = min(height - 24.0, y0 + box_height)
    if y1 - y0 < 54.0:
        y0 = max(24.0, height - 24.0 - box_height)
        y1 = min(height - 24.0, y0 + box_height)
    return [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]


def _mark_duplicate_as_recovery_source(row: dict[str, Any]) -> None:
    layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
    layout = deepcopy(layout)
    layout["status"] = "preview-duplicate-binding"
    layout["slotId"] = None
    layout["slotSource"] = "preview-recovery-source"
    row["layout"] = layout
    contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
    contract = deepcopy(contract)
    contract["status"] = "preview-recovery-source"
    contract["placement"] = "recovery-layer"
    row["layoutContract"] = contract
    row["previewRecoveryReason"] = "duplicate-physical-slot-binding"


def _synthetic_row(
    *,
    recovery_id: str,
    page_no: int,
    bbox: list[float],
    text: str,
    source: dict[str, Any],
    order_index: int,
) -> dict[str, Any]:
    markdown_id = f"preview-{recovery_id}"
    return {
        "markdownId": markdown_id,
        "markdownType": "callout",
        "markdownOrder": order_index,
        "rawMarkdown": text,
        "authoritativeContent": {
            "text": text,
            "plainText": text,
            "rawMarkdown": text,
            "source": "preview-recovery-layer",
        },
        "contentContract": {
            "text": text,
            "plainText": text,
            "rawMarkdown": text,
            "source": "preview-recovery-layer",
        },
        "layout": {
            "status": "layout-slot",
            "page": page_no,
            "slotId": recovery_id,
            "parentSlotId": None,
            "slotSource": "preview-recovery-layer",
            "slotType": "text",
            "semanticType": "callout",
            "bbox": bbox,
            "spanning": False,
            "flowOrder": order_index,
            "wordFlowOrder": order_index,
        },
        "layoutContract": {
            "status": "usable",
            "page": page_no,
            "placement": "positioned-frame",
            "slot": {
                "id": recovery_id,
                "source": "preview-recovery-layer",
                "type": "text",
                "semanticType": "callout",
            },
            "box": {
                "absolutePt": bbox,
                "relativePage": None,
                "source": "diagnostic-preview",
            },
            "column": {"index": None, "role": "overlay", "spanning": False},
            "builderUse": {
                "safeForFlowOrdering": False,
                "requiresPositionedFrame": True,
                "requiresVisualPlacement": False,
            },
            "styleHint": {
                "role": "callout",
                "markdownType": "callout",
                "semanticType": "callout",
                "source": "preview-recovery-layer",
            },
            "evidence": {
                "sourceMarkdownId": source.get("markdownId"),
                "sourceMarkdownType": source.get("markdownType"),
                "reason": source.get("previewRecoveryReason") or "unresolved-layout-contract",
            },
        },
        "wordParagraph": {
            "geometry": {
                "alignment": "left",
                "leftIndentPt": 0.0,
                "rightIndentPt": 0.0,
                "firstLineIndentPt": 0.0,
                "hangingIndentPt": 0.0,
                "lineHeightPt": 8.6,
            },
            "spacing": {"spaceBeforePt": 0.0, "spaceAfterPt": 0.0},
            "frame": {"bboxPt": bbox, "source": "preview-recovery-layer"},
        },
        "pdfTypography": {
            "confidence": "preview",
            "source": "preview-recovery-layer",
            "fontFamily": {"dominant": "Times New Roman"},
            "fontSizePt": {"dominant": 8.0},
            "emphasis": {},
            "color": {"dominant": "000000"},
        },
        "previewRecovery": {
            "sourceMarkdownId": source.get("markdownId"),
            "sourceMarkdownType": source.get("markdownType"),
            "reason": source.get("previewRecoveryReason") or "unresolved-layout-contract",
        },
    }


def _usable_slot_keys(rows: list[dict[str, Any]]) -> dict[int, set[str]]:
    result: dict[int, set[str]] = {}
    for row in rows:
        contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
        layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
        if str(contract.get("status") or "") != "usable":
            continue
        page_no = _row_page(row)
        slot_id = str(layout.get("slotId") or "")
        if page_no and slot_id:
            result.setdefault(page_no, set()).add(slot_id)
    return result


def _item_claimed(item: dict[str, Any], claimed: set[str]) -> bool:
    item_id = str(item.get("id") or "")
    if item_id in claimed:
        return True
    if item_id.startswith("flow-") and item_id[5:] in claimed:
        return True
    visual_group_id = str(item.get("visual_group_id") or "")
    return bool(visual_group_id and visual_group_id in claimed)


def _prune_unbound_physical_text_slots(
    page_structure: dict[str, Any],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preview-only: remove PDF text geometry that has no independent MMD binding.

    These slots are physical fragments, often already absorbed by a larger MMD
    semantic record. Rendering their PDF prose would violate Markdown authority
    and can duplicate content. Visual objects are never pruned here.
    """
    claimed_by_page = _usable_slot_keys(rows)
    removed: list[dict[str, Any]] = []
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        claimed = claimed_by_page.get(page_no, set())

        new_flow: list[dict[str, Any]] = []
        for item in page.get("flow", []) or []:
            if str(item.get("type") or "") != "text" or _item_claimed(item, claimed):
                new_flow.append(item)
                continue
            removed.append({
                "page": page_no,
                "collection": "flow",
                "slotId": item.get("id"),
                "semanticType": item.get("semantic_type"),
                "reason": "physical-text-slot-without-independent-mmd-contract",
            })
        page["flow"] = new_flow

        new_callouts: list[dict[str, Any]] = []
        for item in page.get("callouts", []) or []:
            if _item_claimed(item, claimed):
                new_callouts.append(item)
                continue
            removed.append({
                "page": page_no,
                "collection": "callouts",
                "slotId": item.get("id"),
                "semanticType": item.get("semantic_type") or "callout",
                "reason": "physical-callout-slot-without-independent-mmd-contract",
            })
        page["callouts"] = new_callouts
    return removed


def prepare_preview_recovery_layer(
    page_layout_spine: dict[str, Any],
    page_structure: dict[str, Any],
) -> dict[str, Any]:
    """Preserve uncertain MMD content as editable overlay callouts in preview.

    Strict reconstruction is untouched. In preview, one canonical row keeps each
    physical slot. Extra claimants and already-unresolved rows become synthetic
    callout contracts anchored to their referenced source page. Physical PDF text
    fragments with no independent MMD contract are omitted from preview rendering
    rather than being filled with PDF prose.
    """
    rows = list(page_layout_spine.get("rows", []) or [])
    page_by_no = {
        int(page.get("page") or 0): page
        for page in page_structure.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }

    seen_slots: dict[tuple[int, str], dict[str, Any]] = {}
    duplicate_rows: list[dict[str, Any]] = []
    for row in rows:
        contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
        layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
        if str(contract.get("status") or "") != "usable":
            continue
        page_no = _row_page(row)
        slot_id = str(layout.get("slotId") or "")
        if not page_no or not slot_id:
            continue
        key = (page_no, slot_id)
        if key not in seen_slots:
            seen_slots[key] = row
            continue
        _mark_duplicate_as_recovery_source(row)
        duplicate_rows.append(row)

    recovery_sources: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    for row in rows:
        contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
        if str(contract.get("status") or "") == "usable":
            continue
        text = _text_from_row(row)
        page_no = _row_page(row)
        if not text or page_no not in page_by_no:
            continue
        source_id = str(row.get("markdownId") or f"row-{id(row)}")
        if source_id in seen_source_ids:
            continue
        seen_source_ids.add(source_id)
        recovery_sources.append(row)

    per_page_count: dict[int, int] = {}
    synthetic_rows: list[dict[str, Any]] = []
    audit_items: list[dict[str, Any]] = []
    for index, source in enumerate(recovery_sources, start=1):
        page_no = _row_page(source)
        page = page_by_no[page_no]
        ordinal = per_page_count.get(page_no, 0)
        per_page_count[page_no] = ordinal + 1
        source_text = _text_from_row(source)
        reason = str(source.get("previewRecoveryReason") or "unresolved-layout-contract")
        source_id = str(source.get("markdownId") or "unknown")
        source_type = str(source.get("markdownType") or "unknown")
        header = f"[PREVIEW RECOVERY | {reason} | {source_id} | {source_type}]"
        diagnostic_text = f"{header}\n{source_text}"
        bbox = _callout_bbox(page, ordinal, diagnostic_text)
        recovery_id = f"preview-recovery-p{page_no}-{index:04d}"

        page.setdefault("callouts", []).append({
            "id": recovery_id,
            "type": "callout",
            "semantic_type": "callout",
            "bbox": bbox,
            "text": diagnostic_text,
            "contained_visual_groups": [],
            "preview_recovery": True,
            "source_markdown_id": source_id,
            "source_markdown_type": source_type,
            "recovery_reason": reason,
        })
        synthetic = _synthetic_row(
            recovery_id=recovery_id,
            page_no=page_no,
            bbox=bbox,
            text=diagnostic_text,
            source=source,
            order_index=10_000_000 + index,
        )
        synthetic_rows.append(synthetic)
        audit_items.append({
            "recoveryId": recovery_id,
            "page": page_no,
            "bbox": bbox,
            "sourceMarkdownId": source_id,
            "sourceMarkdownType": source_type,
            "reason": reason,
            "textLength": len(source_text),
        })

    page_layout_spine.setdefault("rows", []).extend(synthetic_rows)
    final_rows = list(page_layout_spine.get("rows", []) or [])
    pruned_slots = _prune_unbound_physical_text_slots(page_structure, final_rows)

    report = {
        "version": VERSION,
        "duplicateBindingCount": len(duplicate_rows),
        "recoverySourceCount": len(recovery_sources),
        "syntheticCalloutCount": len(synthetic_rows),
        "prunedUnboundPhysicalTextSlotCount": len(pruned_slots),
        "pagesWithRecovery": sorted(per_page_count),
        "policy": (
            "preview only: preserve all unresolved/ambiguous Markdown content as editable positioned callouts; "
            "one canonical binding remains in normal flow per physical slot; unbound PDF text fragments are not rendered as prose; "
            "strict build unchanged"
        ),
        "items": audit_items,
        "prunedPhysicalSlots": pruned_slots,
    }
    page_layout_spine["previewRecoveryLayer"] = report
    page_structure["previewRecoveryLayer"] = report
    return report


__all__ = ["prepare_preview_recovery_layer"]
