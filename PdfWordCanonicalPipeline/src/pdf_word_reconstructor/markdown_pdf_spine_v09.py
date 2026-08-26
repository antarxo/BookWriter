from __future__ import annotations

from typing import Any

from .common import normalize_text
from .markdown_pdf_spine_v08 import build_markdown_pdf_spine as _build_v08, _placed, _neighbor_bounds, _candidate_rows
from .markdown_pdf_spine_v07 import _item_text, _page_no
from .markdown_pdf_spine_v02 import TEXT_TYPES, _pdf_regions, _score

VERSION = "markdown-pdf-spine-0.9"


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v08(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])
    rows_by_page: dict[int, list[dict[str, Any]]] = {}
    for row in _pdf_regions(pdf_analysis):
        rows_by_page.setdefault(int(row.get("page") or 0), []).append(row)

    used_ids = {
        (int(item.get("pdfPage") or 0), str(item.get("pdfRegion") or ""))
        for item in items if item.get("pdfRegion")
    }
    diagnostics: list[dict[str, Any]] = []

    for index, item in enumerate(items):
        kind = str(item.get("type") or "")
        if kind not in TEXT_TYPES or _placed(item):
            continue
        page = _page_no(item)
        diag: dict[str, Any] = {
            "markdownId": item.get("id"),
            "page": page or None,
            "type": kind,
            "text": _item_text(item)[:180],
        }
        if not page:
            diag["reason"] = "missing-page"
            diagnostics.append(diag)
            continue

        previous, following = _neighbor_bounds(items, index, page)
        diag["previousMarkdownId"] = previous.get("id") if previous else None
        diag["nextMarkdownId"] = following.get("id") if following else None
        if previous is None or following is None:
            diag["reason"] = "missing-two-sided-neighbor"
            diagnostics.append(diag)
            continue

        candidates = _candidate_rows(rows_by_page.get(page, []), previous, following, used_ids, page)
        diag["candidateCount"] = len(candidates)
        if not candidates:
            diag["reason"] = "empty-neighbor-band"
            diagnostics.append(diag)
            continue

        text = normalize_text(_item_text(item))
        ranked = []
        for row in candidates:
            target = str(row.get("normalized") or normalize_text(str(row.get("text") or "")))
            ranked.append((_score(text, target), row))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        best_score, best_row = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        diag.update({
            "reason": "ambiguous-or-low-score",
            "bestScore": round(float(best_score), 2),
            "secondScore": round(float(second_score), 2),
            "bestPdfRegion": best_row.get("id"),
            "bestPdfText": str(best_row.get("text") or "")[:180],
            "uniqueParentRegionCount": len({
                str(row.get("parentRegion") or row.get("id") or "")
                for _score_value, row in ranked
            }),
        })
        diagnostics.append(diag)

    reason_counts: dict[str, int] = {}
    for diag in diagnostics:
        reason = str(diag.get("reason") or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    result["version"] = VERSION
    result["neighborBoundedDiagnostics"] = {
        "count": len(diagnostics),
        "reasonCounts": reason_counts,
        "items": diagnostics[:120],
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
