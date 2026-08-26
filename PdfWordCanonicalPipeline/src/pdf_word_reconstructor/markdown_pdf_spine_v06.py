from __future__ import annotations

from typing import Any

from .markdown_pdf_spine_v05 import build_markdown_pdf_spine as _build_v05
from .markdown_pdf_spine_v03 import _bbox, _typography_from_lines

VERSION = "markdown-pdf-spine-0.6"


def _item_page(item: dict[str, Any]) -> int:
    for key in ("pdfPage", "inferredPage", "markdownPageHint"):
        try:
            value = int(item.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return 0


def _equation_regions_by_page(pdf_analysis: dict[str, Any] | None) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for page in (pdf_analysis or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        rows: list[dict[str, Any]] = []
        for region in page.get("regions", []) or []:
            if region.get("type") != "text":
                continue
            semantic = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
            if str(semantic.get("type") or "") != "equation":
                continue
            if _bbox(region.get("bbox")):
                rows.append(region)
        rows.sort(key=lambda region: ((_bbox(region.get("bbox")) or [0, 0, 0, 0])[1], (_bbox(region.get("bbox")) or [0, 0, 0, 0])[0]))
        if rows:
            result[page_no] = rows
    return result


def _neighbor_window(items: list[dict[str, Any]], index: int) -> tuple[float | None, float | None]:
    before_y: float | None = None
    after_y: float | None = None
    for probe in range(index - 1, -1, -1):
        box = _bbox(items[probe].get("bbox"))
        if box:
            before_y = float(box[3])
            break
    for probe in range(index + 1, len(items)):
        box = _bbox(items[probe].get("bbox"))
        if box:
            after_y = float(box[1])
            break
    return before_y, after_y


def _in_window(region: dict[str, Any], before_y: float | None, after_y: float | None) -> bool:
    box = _bbox(region.get("bbox"))
    if not box:
        return False
    center_y = (float(box[1]) + float(box[3])) / 2.0
    if before_y is not None and center_y < before_y - 2.0:
        return False
    if after_y is not None and center_y > after_y + 2.0:
        return False
    return True


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v05(markdown_element_map, pdf_analysis)
    regions_by_page = _equation_regions_by_page(pdf_analysis)
    used_region_ids = {
        str(item.get("pdfRegion") or "")
        for item in result.get("items", []) or []
        if item.get("pdfRegion")
    }

    items_by_page: dict[int, list[dict[str, Any]]] = {}
    for item in result.get("items", []) or []:
        page_no = _item_page(item)
        if page_no:
            items_by_page.setdefault(page_no, []).append(item)
    for page_items in items_by_page.values():
        page_items.sort(key=lambda item: int(item.get("orderIndex") or 0))

    bound = 0
    ambiguous: list[dict[str, Any]] = []
    for page_no, page_items in items_by_page.items():
        available = [region for region in regions_by_page.get(page_no, []) if str(region.get("id") or "") not in used_region_ids]
        if not available:
            continue
        for index, item in enumerate(page_items):
            if str(item.get("type") or "") != "display_equation":
                continue
            if item.get("bbox") and item.get("pdfRegion"):
                continue
            before_y, after_y = _neighbor_window(page_items, index)
            candidates = [region for region in available if _in_window(region, before_y, after_y)]
            if len(candidates) != 1:
                ambiguous.append({
                    "page": page_no,
                    "markdownId": item.get("id"),
                    "candidateCount": len(candidates),
                    "beforeY": before_y,
                    "afterY": after_y,
                    "policy": "bind-only-single-equation-region-inside-mapped-neighbor-window",
                })
                continue
            region = candidates[0]
            region_id = str(region.get("id") or "")
            box = _bbox(region.get("bbox"))
            lines = list(region.get("lines", []) or [])
            item["pdfPage"] = page_no
            item["pdfRegion"] = region_id
            item["pdfParentRegion"] = None
            item["pdfLineIndex"] = None
            item["pdfRowGranularity"] = "pdf-equation-region-neighbor-window"
            item["bbox"] = box
            item["pdfText"] = str(region.get("text") or "")
            item["status"] = "equation-region"
            item["manifestOutcome"] = "pdf-equation-witness-confirmed"
            item["matchMode"] = "neighbor-bounded-equation-region"
            item["score"] = None
            item["pdfTypography"] = _typography_from_lines(lines, box, "pdf-equation-region")
            geometry = item.get("pdfGeometry") if isinstance(item.get("pdfGeometry"), dict) else {}
            geometry["bbox"] = box
            geometry["regionBBox"] = box
            geometry["originalBlockBBox"] = _bbox(region.get("original_block_bbox"))
            geometry["page"] = page_no
            item["pdfGeometry"] = geometry
            used_region_ids.add(region_id)
            available = [candidate for candidate in available if str(candidate.get("id") or "") != region_id]
            bound += 1

    result["version"] = VERSION
    result["displayEquationNeighborBinding"] = {
        "boundCount": bound,
        "ambiguousCount": len(ambiguous),
        "ambiguous": ambiguous[:80],
        "policy": "bind only one classified PDF equation region inside vertical window bounded by already mapped neighboring Markdown items",
        "source": "pdf-analysis.regions[].semantic.type=equation + mapped Markdown neighbor geometry",
    }
    return result
