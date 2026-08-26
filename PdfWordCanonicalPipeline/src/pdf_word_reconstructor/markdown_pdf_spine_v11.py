from __future__ import annotations

from typing import Any

from .common import normalize_text
from .markdown_pdf_spine_v10 import build_markdown_pdf_spine as _build_v10
from .markdown_pdf_spine_v08 import _placed, _neighbor_bounds, _candidate_rows
from .markdown_pdf_spine_v10 import _one_sided_candidates, _rank
from .markdown_pdf_spine_v07 import _item_text, _page_no
from .markdown_pdf_spine_v02 import TEXT_TYPES, _pdf_regions

VERSION = "markdown-pdf-spine-0.11"


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v10(markdown_element_map, pdf_analysis)
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
        text = _item_text(item)
        diag: dict[str, Any] = {
            "markdownId": item.get("id"),
            "page": page or None,
            "type": kind,
            "text": text[:180],
        }
        if not page:
            diag["reason"] = "missing-page"
            diagnostics.append(diag)
            continue
        if len(normalize_text(text)) < 4:
            diag["reason"] = "text-too-short"
            diagnostics.append(diag)
            continue

        previous, following = _neighbor_bounds(items, index, page)
        diag["previousMarkdownId"] = previous.get("id") if previous else None
        diag["nextMarkdownId"] = following.get("id") if following else None

        if previous is not None and following is not None:
            candidates = _candidate_rows(rows_by_page.get(page, []), previous, following, used_ids, page)
            diag["candidateMode"] = "two-sided"
        elif previous is not None or following is not None:
            candidates = _one_sided_candidates(
                rows_by_page.get(page, []),
                previous,
                following,
                page=page,
                used_ids=used_ids,
            )
            diag["candidateMode"] = "one-sided"
        else:
            candidates = []
            diag["candidateMode"] = "no-neighbor"

        diag["candidateCount"] = len(candidates)
        if not candidates:
            if previous is None and following is None:
                diag["reason"] = "no-placed-neighbor"
            elif previous is None or following is None:
                diag["reason"] = "empty-one-sided-candidates"
            else:
                diag["reason"] = "empty-two-sided-band"
            diagnostics.append(diag)
            continue

        ranked = _rank(text, candidates)
        best_score, best_row = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        margin = best_score - second_score
        diag.update({
            "reason": "score-or-margin-below-policy",
            "bestScore": round(float(best_score), 2),
            "secondScore": round(float(second_score), 2),
            "margin": round(float(margin), 2),
            "bestPdfRegion": best_row.get("id"),
            "bestPdfText": str(best_row.get("text") or "")[:180],
        })
        diagnostics.append(diag)

    reason_counts: dict[str, int] = {}
    for diag in diagnostics:
        reason = str(diag.get("reason") or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    result["version"] = VERSION
    result["postDirectionalDiagnostics"] = {
        "count": len(diagnostics),
        "reasonCounts": reason_counts,
        "items": diagnostics[:120],
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
