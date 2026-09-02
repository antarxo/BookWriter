from __future__ import annotations

from copy import deepcopy
from typing import Any


VERSION = "preview-recovery-layer-0.3"


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


def _mark_duplicate_as_unresolved(row: dict[str, Any]) -> None:
    """Preview-only: keep duplicate evidence in the spine but never render it.

    One usable row keeps ownership of the physical slot. Additional rows that
    claim the same slot are made explicitly unresolved and retain their source
    identity only for audit/reporting.
    """
    layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
    layout = deepcopy(layout)
    original_slot_id = layout.get("slotId")
    layout["status"] = "preview-duplicate-binding"
    layout["slotId"] = None
    layout["slotSource"] = "preview-unresolved-duplicate"
    row["layout"] = layout

    contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
    contract = deepcopy(contract)
    contract["status"] = "preview-unresolved-duplicate"
    contract["placement"] = "omitted-from-preview"
    row["layoutContract"] = contract

    row["previewRecoveryReason"] = "duplicate-physical-slot-binding"
    row["previewOriginalSlotId"] = original_slot_id


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
    """Preview-only: remove PDF text geometry with no usable MMD binding.

    This never touches visual objects. It prevents raw PDF text fragments from
    being rendered as substitute prose when no authoritative MMD contract owns
    them.
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
    """Prepare a non-inventive diagnostic preview.

    Preview policy is deliberately narrow:
    - one usable row keeps each physical slot;
    - duplicate claimants become unresolved audit records;
    - any other unresolved/ambiguous rows remain unresolved;
    - unresolved content is never converted into synthetic callouts or inserted
      into the DOCX;
    - unbound PDF text fragments are pruned so they cannot become fallback prose.

    The native builder is responsible for recording unresolved items and omitting
    them from preview rendering. Strict reconstruction remains unchanged.
    """
    rows = list(page_layout_spine.get("rows", []) or [])

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
        _mark_duplicate_as_unresolved(row)
        duplicate_rows.append(row)

    unresolved_rows: list[dict[str, Any]] = []
    audit_items: list[dict[str, Any]] = []
    pages_with_unresolved: set[int] = set()
    seen_source_ids: set[str] = set()
    for row in rows:
        contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
        if str(contract.get("status") or "") == "usable":
            continue
        source_id = str(row.get("markdownId") or f"row-{id(row)}")
        if source_id in seen_source_ids:
            continue
        seen_source_ids.add(source_id)
        unresolved_rows.append(row)

        page_no = _row_page(row)
        if page_no > 0:
            pages_with_unresolved.add(page_no)
        source_text = _text_from_row(row)
        audit_items.append({
            "page": page_no or None,
            "sourceMarkdownId": row.get("markdownId"),
            "sourceMarkdownType": row.get("markdownType"),
            "reason": row.get("previewRecoveryReason") or "unresolved-layout-contract",
            "originalSlotId": row.get("previewOriginalSlotId")
                or ((row.get("layout") or {}).get("slotId") if isinstance(row.get("layout"), dict) else None),
            "textLength": len(source_text),
            "rendered": False,
        })

    pruned_slots = _prune_unbound_physical_text_slots(page_structure, rows)

    report = {
        "version": VERSION,
        "duplicateBindingCount": len(duplicate_rows),
        "recoverySourceCount": len(unresolved_rows),
        "syntheticCalloutCount": 0,
        "prunedUnboundPhysicalTextSlotCount": len(pruned_slots),
        "pagesWithRecovery": sorted(pages_with_unresolved),
        "policy": (
            "preview only: unresolved/ambiguous Markdown and duplicate slot claimants are recorded but omitted from DOCX; "
            "no synthetic callouts, no diagnostic text injection, no invented placement; "
            "one canonical usable binding remains per physical slot; unbound PDF text fragments are not rendered as prose; "
            "strict build unchanged"
        ),
        "items": audit_items,
        "prunedPhysicalSlots": pruned_slots,
    }
    page_layout_spine["previewRecoveryLayer"] = report
    page_structure["previewRecoveryLayer"] = report
    return report


__all__ = ["prepare_preview_recovery_layer"]
