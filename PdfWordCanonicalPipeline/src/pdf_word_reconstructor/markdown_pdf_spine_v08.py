from __future__ import annotations

from typing import Any

from .common import normalize_text
from .markdown_pdf_spine_v07 import (
    build_markdown_pdf_spine as _build_v07,
    _item_text,
    _page_no,
    _region_lookup,
)
from .markdown_pdf_spine_v02 import TEXT_TYPES, _pdf_regions, _score
from .markdown_pdf_spine_v03 import _bbox, _typography_from_lines


VERSION = "markdown-pdf-spine-0.8"


def _placed(item: dict[str, Any]) -> bool:
    return bool(item.get("pdfRegion") and _bbox(item.get("bbox")))


def _order(item: dict[str, Any]) -> int:
    try:
        return int(item.get("orderIndex") or 0)
    except (TypeError, ValueError):
        return 0


def _neighbor_bounds(items: list[dict[str, Any]], index: int, page: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    previous = None
    following = None
    for pos in range(index - 1, -1, -1):
        candidate = items[pos]
        if _page_no(candidate) != page:
            continue
        if _placed(candidate):
            previous = candidate
            break
    for pos in range(index + 1, len(items)):
        candidate = items[pos]
        if _page_no(candidate) != page:
            continue
        if _placed(candidate):
            following = candidate
            break
    return previous, following


def _candidate_rows(
    rows: list[dict[str, Any]],
    previous: dict[str, Any],
    following: dict[str, Any],
    used_ids: set[tuple[int, str]],
    page: int,
) -> list[dict[str, Any]]:
    prev_box = _bbox(previous.get("bbox"))
    next_box = _bbox(following.get("bbox"))
    if not prev_box or not next_box:
        return []
    lower = float(prev_box[3]) - 1.5
    upper = float(next_box[1]) + 1.5
    if upper <= lower:
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("rowGranularity") or "") == "pdf-line-cluster":
            continue
        row_id = str(row.get("id") or "")
        if not row_id or (page, row_id) in used_ids:
            continue
        box = _bbox(row.get("bbox"))
        if not box:
            continue
        center_y = (float(box[1]) + float(box[3])) / 2.0
        if lower <= center_y <= upper:
            result.append(row)
    return result


def _attach(
    item: dict[str, Any],
    row: dict[str, Any],
    page: int,
    score: float,
    regions: dict[str, dict[str, Any]],
) -> None:
    row_id = str(row.get("id") or "")
    parent_id = str(row.get("parentRegion") or "")
    bbox = _bbox(row.get("bbox"))
    item["pdfPage"] = page
    item["pdfRegion"] = row_id
    item["pdfParentRegion"] = parent_id or None
    item["pdfLineIndex"] = row.get("lineIndex")
    item["pdfRowGranularity"] = row.get("rowGranularity") or "pdf-region"
    item["bbox"] = bbox
    item["pdfText"] = str(row.get("text") or "")
    item["status"] = "medium" if score >= 70.0 else "neighbor-bounded"
    item["manifestOutcome"] = "pdf-witness-confirmed"
    item["matchMode"] = "neighbor-bounded-page-recovery"
    item["score"] = round(float(score), 2)

    source_region = regions.get(parent_id or row_id) or {}
    lines = list(source_region.get("lines", []) or [])
    if row.get("rowGranularity") == "pdf-line" and row.get("lineIndex"):
        try:
            idx = max(0, int(row.get("lineIndex")) - 1)
        except (TypeError, ValueError):
            idx = 0
        lines = lines[idx:idx + 1]
    item["pdfTypography"] = _typography_from_lines(lines, bbox, "neighbor-bounded-page-recovery")
    geometry = item.get("pdfGeometry") if isinstance(item.get("pdfGeometry"), dict) else {}
    geometry["bbox"] = bbox
    geometry["regionBBox"] = _bbox(source_region.get("bbox"))
    geometry["originalBlockBBox"] = _bbox(source_region.get("original_block_bbox"))
    geometry["page"] = page
    item["pdfGeometry"] = geometry


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v07(markdown_element_map, pdf_analysis)
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
        if not page:
            continue
        previous, following = _neighbor_bounds(items, index, page)
        if previous is None or following is None:
            continue
        candidates = _candidate_rows(rows_by_page.get(page, []), previous, following, used_ids, page)
        if not candidates:
            continue

        text = normalize_text(_item_text(item))
        if len(text) < 4:
            continue
        ranked: list[tuple[float, dict[str, Any]]] = []
        for row in candidates:
            target = str(row.get("normalized") or normalize_text(str(row.get("text") or "")))
            ranked.append((_score(text, target), row))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        best_score, best_row = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0

        unique_parent_ids = {
            str(row.get("parentRegion") or row.get("id") or "")
            for _score_value, row in ranked
        }
        unique_region = len(unique_parent_ids) == 1
        decisive = best_score >= 58.0 and (best_score - second_score >= 10.0 or unique_region)
        structural_singleton = unique_region and best_score >= 42.0
        if not (decisive or structural_singleton):
            continue

        _attach(item, best_row, page, best_score, regions)
        row_id = str(best_row.get("id") or "")
        used_ids.add((page, row_id))
        recovered.append({
            "markdownId": item.get("id"),
            "page": page,
            "pdfRegion": row_id,
            "score": round(float(best_score), 2),
            "secondScore": round(float(second_score), 2),
            "candidateCount": len(candidates),
            "uniqueParentRegionCount": len(unique_parent_ids),
            "previousMarkdownId": previous.get("id"),
            "nextMarkdownId": following.get("id"),
            "outputType": kind,
        })

    result["version"] = VERSION
    result["neighborBoundedRecovery"] = {
        "recoveredCount": len(recovered),
        "policy": "same-page two-sided placed Markdown neighbors; atomic PDF rows only; score>=58 with >=10 margin or one parent region; structural singleton score>=42",
        "items": recovered[:120],
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
