from __future__ import annotations

from typing import Any

from .markdown_pdf_spine_v11 import build_markdown_pdf_spine as _build_v11
from .markdown_pdf_spine_v08 import _placed, _neighbor_bounds, _candidate_rows
from .markdown_pdf_spine_v10 import _one_sided_candidates, _rank
from .markdown_pdf_spine_v07 import _item_text, _page_no
from .markdown_pdf_spine_v02 import TEXT_TYPES, _pdf_regions

VERSION = "markdown-pdf-spine-0.12"


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v11(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])
    rows_by_page: dict[int, list[dict[str, Any]]] = {}
    for row in _pdf_regions(pdf_analysis):
        rows_by_page.setdefault(int(row.get("page") or 0), []).append(row)

    used_ids = {
        (int(item.get("pdfPage") or 0), str(item.get("pdfRegion") or ""))
        for item in items
        if item.get("pdfRegion")
    }

    diagnostics: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        kind = str(item.get("type") or "")
        if kind not in TEXT_TYPES or _placed(item):
            continue
        page = _page_no(item)
        if not page:
            continue
        previous, following = _neighbor_bounds(items, index, page)
        if previous is not None and following is not None:
            candidates = _candidate_rows(rows_by_page.get(page, []), previous, following, used_ids, page)
            mode = "two-sided"
        elif previous is not None or following is not None:
            candidates = _one_sided_candidates(
                rows_by_page.get(page, []), previous, following, page=page, used_ids=used_ids
            )
            mode = "one-sided"
        else:
            candidates = []
            mode = "no-neighbor"

        ranked = _rank(_item_text(item), candidates) if candidates else []
        diagnostics.append({
            "markdownId": item.get("id"),
            "page": page,
            "type": kind,
            "text": _item_text(item)[:180],
            "candidateMode": mode,
            "candidateCount": len(candidates),
            "topCandidates": [
                {
                    "score": round(float(score), 2),
                    "pdfRegion": row.get("id"),
                    "semanticType": row.get("semanticType"),
                    "flowZone": row.get("flowZone"),
                    "rowGranularity": row.get("rowGranularity"),
                    "text": str(row.get("text") or "")[:180],
                }
                for score, row in ranked[:3]
            ],
        })

    result["version"] = VERSION
    result["semanticCandidateDiagnostics"] = {
        "count": len(diagnostics),
        "items": diagnostics[:120],
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
