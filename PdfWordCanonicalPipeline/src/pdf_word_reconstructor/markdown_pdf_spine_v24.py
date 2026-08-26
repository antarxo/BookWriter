from __future__ import annotations

from typing import Any

from .common import normalize_text
from .markdown_pdf_spine_v23 import build_markdown_pdf_spine as _build_v23
from .markdown_pdf_spine_v08 import _attach, _placed
from .markdown_pdf_spine_v07 import _item_text, _page_no, _region_lookup
from .markdown_pdf_spine_v02 import _pdf_regions, _score
from .markdown_pdf_spine_v03 import _bbox

VERSION = "markdown-pdf-spine-0.24"

_ALLOWED_SEMANTICS = {"body", "heading", "caption", "callout", "banner"}


def _base_region_id(row_or_item: dict[str, Any]) -> str:
    parent = str(row_or_item.get("parentRegion") or row_or_item.get("pdfParentRegion") or "")
    if parent:
        return parent
    region = str(row_or_item.get("id") or row_or_item.get("pdfRegion") or "")
    if not region:
        return ""
    for marker in ("-lines", "-line"):
        pos = region.rfind(marker)
        if pos > 0:
            return region[:pos]
    return region


def _nearest_placed_neighbors(items: list[dict[str, Any]], index: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    previous = None
    following = None
    for pos in range(index - 1, -1, -1):
        if _placed(items[pos]):
            previous = items[pos]
            break
    for pos in range(index + 1, len(items)):
        if _placed(items[pos]):
            following = items[pos]
            break
    return previous, following


def _page_bracket(previous: dict[str, Any] | None, following: dict[str, Any] | None) -> tuple[int | None, int | None]:
    prev_page = int(previous.get("pdfPage") or 0) if previous else 0
    next_page = int(following.get("pdfPage") or 0) if following else 0
    return (prev_page or None, next_page or None)


def _candidate_allowed_page(page: int, hinted: int, prev_page: int | None, next_page: int | None) -> bool:
    if page not in {hinted - 1, hinted, hinted + 1}:
        return False
    if prev_page is not None and page < prev_page:
        return False
    if next_page is not None and page > next_page:
        return False
    return True


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v23(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])

    rows_by_page: dict[int, list[dict[str, Any]]] = {}
    for row in _pdf_regions(pdf_analysis):
        page = int(row.get("page") or 0)
        if not page:
            continue
        if str(row.get("rowGranularity") or "") == "pdf-line-cluster":
            continue
        if str(row.get("flowZone") or "") == "page_furniture":
            continue
        if str(row.get("semanticType") or "") in {"header", "footer", "noise"}:
            continue
        if str(row.get("semanticType") or "") not in _ALLOWED_SEMANTICS:
            continue
        if not _bbox(row.get("bbox")):
            continue
        rows_by_page.setdefault(page, []).append(row)

    occupied: set[tuple[int, str]] = set()
    for placed in items:
        if not _placed(placed):
            continue
        page = int(placed.get("pdfPage") or 0)
        base = _base_region_id(placed)
        if page and base:
            occupied.add((page, base))
        for constituent in placed.get("pdfConstituentRegions") or []:
            value = str(constituent or "")
            for marker in ("-lines", "-line"):
                pos = value.rfind(marker)
                if pos > 0:
                    value = value[:pos]
                    break
            if page and value:
                occupied.add((page, value))

    regions = _region_lookup(pdf_analysis)
    recovered: list[dict[str, Any]] = []

    for index, item in enumerate(items):
        if str(item.get("type") or "") != "paragraph" or _placed(item):
            continue
        hinted = _page_no(item)
        source = normalize_text(_item_text(item))
        if not hinted or len(source) < 70:
            continue

        previous, following = _nearest_placed_neighbors(items, index)
        prev_page, next_page = _page_bracket(previous, following)
        if prev_page is None and next_page is None:
            continue
        if prev_page is not None and next_page is not None and prev_page > next_page:
            continue

        ranked: list[tuple[float, dict[str, Any]]] = []
        for page in (hinted - 1, hinted, hinted + 1):
            if page <= 0 or not _candidate_allowed_page(page, hinted, prev_page, next_page):
                continue
            for row in rows_by_page.get(page, []) or []:
                base = _base_region_id(row)
                if not base or (page, base) in occupied:
                    continue
                target = str(row.get("normalized") or normalize_text(str(row.get("text") or "")))
                ranked.append((_score(source, target), row))

        ranked.sort(key=lambda pair: pair[0], reverse=True)
        if not ranked:
            continue
        best_score, best_row = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        margin = best_score - second_score
        best_page = int(best_row.get("page") or 0)
        best_base = _base_region_id(best_row)

        if best_score < 85.0 or margin < 7.0:
            continue
        if not best_page or not best_base:
            continue

        _attach(item, best_row, best_page, best_score, regions)
        item["matchMode"] = "adjacent-page-sequence-bracket-paragraph"
        occupied.add((best_page, best_base))
        recovered.append({
            "markdownId": item.get("id"),
            "hintedPage": hinted,
            "pdfPage": best_page,
            "pageDelta": best_page - hinted,
            "pdfRegion": best_row.get("id"),
            "semanticType": best_row.get("semanticType"),
            "score": round(float(best_score), 2),
            "secondScore": round(float(second_score), 2),
            "margin": round(float(margin), 2),
            "previousMarkdownId": previous.get("id") if previous else None,
            "previousPdfPage": prev_page,
            "nextMarkdownId": following.get("id") if following else None,
            "nextPdfPage": next_page,
        })

    result["version"] = VERSION
    result["adjacentPageSequenceBracketRecovery"] = {
        "recoveredCount": len(recovered),
        "items": recovered[:120],
        "policy": (
            "remaining long paragraphs may bind only to an unused non-furniture atomic PDF region on hinted page ±1; "
            "candidate page must be monotonic between the nearest already-placed Markdown neighbors; score>=85 and margin>=7; "
            "existing bindings are immutable and Markdown remains content authority"
        ),
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
