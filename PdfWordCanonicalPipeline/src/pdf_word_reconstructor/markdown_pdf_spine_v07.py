from __future__ import annotations

from typing import Any

from .markdown_pdf_spine_v05 import build_markdown_pdf_spine as _build_v05
from .markdown_pdf_spine_v02 import TEXT_TYPES, _pdf_regions, _reading_order_key, _score
from .markdown_pdf_spine_v03 import _bbox, _typography_from_lines

VERSION = "markdown-pdf-spine-0.7"


def _region_lookup(pdf_analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for page in (pdf_analysis or {}).get("pages", []) or []:
        for region in page.get("regions", []) or []:
            region_id = str(region.get("id") or "")
            if region_id:
                result[region_id] = region
    return result


def _item_text(item: dict[str, Any]) -> str:
    authoritative = item.get("authoritativeContent") if isinstance(item.get("authoritativeContent"), dict) else {}
    return str(
        item.get("text")
        or authoritative.get("text")
        or authoritative.get("plainText")
        or item.get("rawMarkdown")
        or ""
    ).strip()


def _page_no(item: dict[str, Any]) -> int:
    for key in ("pdfPage", "inferredPage", "markdownPageHint"):
        try:
            value = int(item.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return 0


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v05(markdown_element_map, pdf_analysis)
    pdf_rows = _pdf_regions(pdf_analysis)
    rows_by_page: dict[int, list[dict[str, Any]]] = {}
    for row in pdf_rows:
        rows_by_page.setdefault(int(row.get("page") or 0), []).append(row)
    for rows in rows_by_page.values():
        rows.sort(key=_reading_order_key)

    used_keys = {
        (int(item.get("pdfPage") or 0), str(item.get("pdfRegion") or ""))
        for item in result.get("items", []) or []
        if item.get("pdfRegion")
    }
    regions = _region_lookup(pdf_analysis)
    recovered: list[dict[str, Any]] = []

    for item in result.get("items", []) or []:
        kind = str(item.get("type") or "")
        if kind not in TEXT_TYPES:
            continue
        if item.get("bbox") and item.get("pdfRegion"):
            continue
        page = _page_no(item)
        if not page:
            continue
        text = _item_text(item)
        if len(text.strip()) < 4:
            continue

        best: tuple[float, dict[str, Any]] | None = None
        for row in rows_by_page.get(page, []) or []:
            key = (page, str(row.get("id") or ""))
            if key in used_keys:
                continue
            score = _score(text, str(row.get("text") or row.get("normalized") or ""))
            if best is None or score > best[0]:
                best = (score, row)
        if not best or best[0] < 70.0:
            continue

        score, row = best
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
        item["status"] = "strong" if score >= 84.0 else "medium"
        item["manifestOutcome"] = "pdf-witness-confirmed"
        item["matchMode"] = "page-scoped-unplaced-recovery"
        item["score"] = round(float(score), 2)

        source_region = regions.get(parent_id or row_id) or {}
        lines = list(source_region.get("lines", []) or [])
        if row.get("rowGranularity") == "pdf-line" and row.get("lineIndex"):
            try:
                idx = max(0, int(row.get("lineIndex")) - 1)
            except (TypeError, ValueError):
                idx = 0
            lines = lines[idx:idx + 1]
        item["pdfTypography"] = _typography_from_lines(lines, bbox, "page-scoped-unplaced-recovery")
        geometry = item.get("pdfGeometry") if isinstance(item.get("pdfGeometry"), dict) else {}
        geometry["bbox"] = bbox
        geometry["regionBBox"] = _bbox(source_region.get("bbox"))
        geometry["originalBlockBBox"] = _bbox(source_region.get("original_block_bbox"))
        geometry["page"] = page
        item["pdfGeometry"] = geometry
        used_keys.add((page, row_id))
        recovered.append({
            "markdownId": item.get("id"),
            "page": page,
            "pdfRegion": row_id,
            "score": round(float(score), 2),
            "outputType": kind,
        })

    result["version"] = VERSION
    result["pageScopedRecovery"] = {
        "recoveredCount": len(recovered),
        "policy": "same-page unused PDF row; score>=70; uniqueness keyed by (page, pdfRegion)",
        "items": recovered[:120],
    }
    return result
