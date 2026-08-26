from __future__ import annotations

from typing import Any

from .common import normalize_text
from .markdown_pdf_spine_v09 import build_markdown_pdf_spine as _build_v09
from .markdown_pdf_spine_v08 import _attach, _placed, _neighbor_bounds, _candidate_rows
from .markdown_pdf_spine_v07 import _item_text, _page_no, _region_lookup
from .markdown_pdf_spine_v02 import TEXT_TYPES, _pdf_regions, _score
from .markdown_pdf_spine_v03 import _bbox

VERSION = "markdown-pdf-spine-0.10"


def _atomic_unused_rows(
    rows: list[dict[str, Any]],
    *,
    page: int,
    used_ids: set[tuple[int, str]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("rowGranularity") or "") == "pdf-line-cluster":
            continue
        row_id = str(row.get("id") or "")
        if not row_id or (page, row_id) in used_ids:
            continue
        if not _bbox(row.get("bbox")):
            continue
        result.append(row)
    return result


def _one_sided_candidates(
    rows: list[dict[str, Any]],
    previous: dict[str, Any] | None,
    following: dict[str, Any] | None,
    *,
    page: int,
    used_ids: set[tuple[int, str]],
) -> list[dict[str, Any]]:
    candidates = _atomic_unused_rows(rows, page=page, used_ids=used_ids)
    if previous is not None and following is None:
        anchor = _bbox(previous.get("bbox"))
        if not anchor:
            return []
        lower = float(anchor[3]) - 1.5
        return [
            row for row in candidates
            if ((_bbox(row.get("bbox")) or [0, 0, 0, 0])[1] + (_bbox(row.get("bbox")) or [0, 0, 0, 0])[3]) / 2.0 >= lower
        ]
    if following is not None and previous is None:
        anchor = _bbox(following.get("bbox"))
        if not anchor:
            return []
        upper = float(anchor[1]) + 1.5
        return [
            row for row in candidates
            if ((_bbox(row.get("bbox")) or [0, 0, 0, 0])[1] + (_bbox(row.get("bbox")) or [0, 0, 0, 0])[3]) / 2.0 <= upper
        ]
    return []


def _rank(text: str, rows: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    norm = normalize_text(text)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        target = str(row.get("normalized") or normalize_text(str(row.get("text") or "")))
        ranked.append((_score(norm, target), row))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v09(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])
    rows_by_page: dict[int, list[dict[str, Any]]] = {}
    for row in _pdf_regions(pdf_analysis):
        rows_by_page.setdefault(int(row.get("page") or 0), []).append(row)

    used_ids = {
        (int(item.get("pdfPage") or 0), str(item.get("pdfRegion") or ""))
        for item in items
        if item.get("pdfRegion")
    }
    regions = _region_lookup(pdf_analysis)
    recovered: list[dict[str, Any]] = []

    for index, item in enumerate(items):
        kind = str(item.get("type") or "")
        if kind not in TEXT_TYPES or _placed(item):
            continue
        page = _page_no(item)
        text = _item_text(item)
        if not page or len(normalize_text(text)) < 4:
            continue

        previous, following = _neighbor_bounds(items, index, page)
        mode = None
        candidates: list[dict[str, Any]] = []
        if previous is not None and following is not None:
            candidates = _candidate_rows(rows_by_page.get(page, []), previous, following, used_ids, page)
            mode = "two-sided-decisive-margin"
        elif previous is not None or following is not None:
            candidates = _one_sided_candidates(
                rows_by_page.get(page, []),
                previous,
                following,
                page=page,
                used_ids=used_ids,
            )
            mode = "one-sided-directional"
        if not candidates:
            continue

        ranked = _rank(text, candidates)
        if not ranked:
            continue
        best_score, best_row = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        margin = best_score - second_score

        if mode == "two-sided-decisive-margin":
            accept = best_score >= 42.0 and margin >= 25.0
        else:
            accept = best_score >= 70.0 or (best_score >= 58.0 and margin >= 18.0)
        if not accept:
            continue

        _attach(item, best_row, page, best_score, regions)
        item["matchMode"] = mode
        row_id = str(best_row.get("id") or "")
        used_ids.add((page, row_id))
        recovered.append({
            "markdownId": item.get("id"),
            "page": page,
            "outputType": kind,
            "mode": mode,
            "pdfRegion": row_id,
            "score": round(float(best_score), 2),
            "secondScore": round(float(second_score), 2),
            "margin": round(float(margin), 2),
            "candidateCount": len(candidates),
            "previousMarkdownId": previous.get("id") if previous else None,
            "nextMarkdownId": following.get("id") if following else None,
        })

    result["version"] = VERSION
    result["directionalRecovery"] = {
        "recoveredCount": len(recovered),
        "policy": "two-sided OCR-confusable recovery requires score>=42 and margin>=25; one-sided directional recovery requires score>=70 or score>=58 with margin>=18; atomic PDF rows only",
        "items": recovered[:120],
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
