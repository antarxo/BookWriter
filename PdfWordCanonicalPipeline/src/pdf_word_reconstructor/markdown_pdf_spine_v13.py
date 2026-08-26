from __future__ import annotations

from typing import Any

from .markdown_pdf_spine_v12 import build_markdown_pdf_spine as _build_v12
from .markdown_pdf_spine_v08 import _placed
from .markdown_pdf_spine_v10 import _rank
from .markdown_pdf_spine_v07 import _item_text, _page_no
from .markdown_pdf_spine_v02 import TEXT_TYPES, _pdf_regions

VERSION = "markdown-pdf-spine-0.13"


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v12(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])

    rows_by_page: dict[int, list[dict[str, Any]]] = {}
    for row in _pdf_regions(pdf_analysis):
        rows_by_page.setdefault(int(row.get("page") or 0), []).append(row)

    owner_by_region: dict[tuple[int, str], dict[str, Any]] = {}
    for item in items:
        page = int(item.get("pdfPage") or 0)
        region = str(item.get("pdfRegion") or "")
        if not page or not region:
            continue
        owner_by_region[(page, region)] = {
            "markdownId": item.get("id"),
            "type": item.get("type"),
            "matchMode": item.get("matchMode"),
            "score": item.get("score"),
            "text": _item_text(item)[:180],
        }

    diagnostics: list[dict[str, Any]] = []
    for item in items:
        kind = str(item.get("type") or "")
        if kind not in TEXT_TYPES or _placed(item):
            continue
        page = _page_no(item)
        if not page:
            continue

        page_rows = [
            row for row in rows_by_page.get(page, [])
            if str(row.get("rowGranularity") or "") != "pdf-line-cluster"
        ]
        ranked = _rank(_item_text(item), page_rows) if page_rows else []

        top_rows: list[dict[str, Any]] = []
        for score, row in ranked[:5]:
            region = str(row.get("id") or "")
            owner = owner_by_region.get((page, region))
            top_rows.append({
                "score": round(float(score), 2),
                "pdfRegion": region,
                "semanticType": row.get("semanticType"),
                "flowZone": row.get("flowZone"),
                "rowGranularity": row.get("rowGranularity"),
                "text": str(row.get("text") or "")[:180],
                "used": owner is not None,
                "owner": owner,
            })

        diagnostics.append({
            "markdownId": item.get("id"),
            "page": page,
            "type": kind,
            "text": _item_text(item)[:180],
            "topPageRows": top_rows,
        })

    result["version"] = VERSION
    result["pageWideConflictDiagnostics"] = {
        "count": len(diagnostics),
        "items": diagnostics[:120],
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
