from __future__ import annotations

from typing import Any

from .markdown_pdf_spine_v04 import build_markdown_pdf_spine as _build_v04
from .markdown_pdf_spine_v03 import _bbox, _typography_from_lines

VERSION = "markdown-pdf-spine-0.5"


def _equation_regions_by_page(pdf_analysis: dict[str, Any] | None) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for page in (pdf_analysis or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        rows = []
        for region in page.get("regions", []) or []:
            if region.get("type") != "text":
                continue
            semantic = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
            if str(semantic.get("type") or "") != "equation":
                continue
            box = _bbox(region.get("bbox"))
            if not box:
                continue
            rows.append(region)
        rows.sort(key=lambda region: ((_bbox(region.get("bbox")) or [0, 0, 0, 0])[1], (_bbox(region.get("bbox")) or [0, 0, 0, 0])[0]))
        if rows:
            result[page_no] = rows
    return result


def _item_page(item: dict[str, Any]) -> int:
    for key in ("pdfPage", "inferredPage", "markdownPageHint"):
        try:
            value = int(item.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return 0


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v04(markdown_element_map, pdf_analysis)
    regions_by_page = _equation_regions_by_page(pdf_analysis)
    used_region_ids = {
        str(item.get("pdfRegion") or "")
        for item in result.get("items", []) or []
        if item.get("pdfRegion")
    }
    bound_count = 0
    skipped_mismatch_pages: list[dict[str, Any]] = []

    items_by_page: dict[int, list[dict[str, Any]]] = {}
    for item in result.get("items", []) or []:
        if str(item.get("type") or "") != "display_equation":
            continue
        if item.get("bbox") and item.get("pdfRegion"):
            continue
        page_no = _item_page(item)
        if page_no:
            items_by_page.setdefault(page_no, []).append(item)

    for page_no, items in items_by_page.items():
        items.sort(key=lambda item: int(item.get("orderIndex") or 0))
        available = [
            region for region in regions_by_page.get(page_no, [])
            if str(region.get("id") or "") not in used_region_ids
        ]
        if not items or len(items) != len(available):
            skipped_mismatch_pages.append({
                "page": page_no,
                "unplacedDisplayEquationCount": len(items),
                "availablePdfEquationRegionCount": len(available),
                "policy": "no-bind-count-mismatch",
            })
            continue

        for item, region in zip(items, available):
            region_id = str(region.get("id") or "")
            box = _bbox(region.get("bbox"))
            lines = list(region.get("lines", []) or [])
            item["pdfPage"] = page_no
            item["pdfRegion"] = region_id
            item["pdfParentRegion"] = None
            item["pdfLineIndex"] = None
            item["pdfRowGranularity"] = "pdf-equation-region"
            item["bbox"] = box
            item["pdfText"] = str(region.get("text") or "")
            item["status"] = "equation-region"
            item["manifestOutcome"] = "pdf-equation-witness-confirmed"
            item["matchMode"] = "page-equation-region-order"
            item["score"] = None
            item["pdfTypography"] = _typography_from_lines(lines, box, "pdf-equation-region")
            geometry = item.get("pdfGeometry") if isinstance(item.get("pdfGeometry"), dict) else {}
            geometry["bbox"] = box
            geometry["regionBBox"] = box
            geometry["originalBlockBBox"] = _bbox(region.get("original_block_bbox"))
            geometry["page"] = page_no
            item["pdfGeometry"] = geometry
            used_region_ids.add(region_id)
            bound_count += 1

    result["version"] = VERSION
    result["displayEquationPdfRegionBinding"] = {
        "boundCount": bound_count,
        "policy": "bind-only-when-unplaced-markdown-display-equation-count-equals-available-classified-pdf-equation-region-count-on-page",
        "mismatchPages": skipped_mismatch_pages,
        "source": "pdf-analysis.regions[].semantic.type=equation",
    }
    return result
