from __future__ import annotations

from typing import Any

from .layout_contract_normalizer import normalize_layout_contracts
from .page_layout_spine_v07 import build_page_layout_spine as _build_v07

VERSION = "page-layout-spine-0.8"
TEXT_TYPES = {"paragraph", "heading", "title", "author", "caption", "list", "latex_list", "table", "latex_table"}


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

    # Canonical boundary: renderer-facing fields are completed only from evidence
    # already frozen in page_structure.  No downstream PDF/DOCX/Lines reread and
    # no fabricated defaults are allowed.
    normalization = normalize_layout_contracts(result, page_structure or {})

    result["version"] = VERSION
    result["directPdfWitnessRecovery"] = {
        "recoveredCount": len(recovered),
        "policy": "recover only no-layout-slot text rows that already have pdfPage+pdfRegion+bbox; no new matching",
        "items": recovered[:120],
    }
    result["layoutContractNormalization"] = normalization
    summary = result.setdefault("summary", {})
    summary["directPdfWitnessRecoveryCount"] = len(recovered)
    return result


__all__ = ["build_page_layout_spine"]
