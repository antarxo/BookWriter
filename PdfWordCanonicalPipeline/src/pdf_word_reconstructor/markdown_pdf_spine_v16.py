from __future__ import annotations

from collections import defaultdict
from typing import Any

from .common import normalize_text
from .markdown_pdf_spine_v15 import build_markdown_pdf_spine as _build_v15, _skeleton
from .markdown_pdf_spine_v08 import _attach, _placed
from .markdown_pdf_spine_v07 import _item_text, _page_no, _region_lookup
from .markdown_pdf_spine_v02 import _score

VERSION = "markdown-pdf-spine-0.16"

HEADING_TYPES = {"heading", "title"}


def _raw_text_regions(pdf_analysis: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for page in (pdf_analysis or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        if not page_no:
            continue
        for region in page.get("regions", []) or []:
            if str(region.get("type") or "") != "text":
                continue
            text = str(region.get("text") or "").strip()
            bbox = region.get("bbox")
            region_id = str(region.get("id") or "")
            if not region_id or not text or not isinstance(bbox, list) or len(bbox) != 4:
                continue
            semantic = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
            result[page_no].append({
                "page": page_no,
                "id": region_id,
                "rowGranularity": "pdf-region-full",
                "text": text,
                "normalized": normalize_text(text),
                "bbox": bbox,
                "semanticType": semantic.get("type"),
                "flowZone": semantic.get("flow_zone"),
            })
    return result


def _short_heading_exact_recovery(
    items: list[dict[str, Any]],
    raw_regions: dict[int, list[dict[str, Any]]],
    regions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    used = {
        (int(item.get("pdfPage") or 0), str(item.get("pdfRegion") or ""))
        for item in items if item.get("pdfRegion")
    }
    recovered: list[dict[str, Any]] = []

    for item in items:
        kind = str(item.get("type") or "")
        if kind not in HEADING_TYPES or _placed(item):
            continue
        hinted_page = _page_no(item)
        text = _item_text(item)
        source_key = _skeleton(text)
        if not hinted_page or len(source_key) < 4:
            continue

        exact_rows: list[dict[str, Any]] = []
        for page in (hinted_page - 1, hinted_page, hinted_page + 1):
            if page <= 0:
                continue
            for row in raw_regions.get(page, []):
                region_id = str(row.get("id") or "")
                if (page, region_id) in used:
                    continue
                if str(row.get("semanticType") or "") != "heading":
                    continue
                if _skeleton(str(row.get("text") or "")) != source_key:
                    continue
                candidate = dict(row)
                candidate["candidatePage"] = page
                exact_rows.append(candidate)

        # Exact evidence must still be unique within the ±1 page window.
        if len(exact_rows) != 1:
            continue

        row = exact_rows[0]
        target_page = int(row.get("candidatePage") or 0)
        _attach(item, row, target_page, 100.0, regions)
        item["matchMode"] = "adjacent-page-short-heading-exact-skeleton"
        region_id = str(row.get("id") or "")
        used.add((target_page, region_id))
        recovered.append({
            "markdownId": item.get("id"),
            "type": kind,
            "text": text[:120],
            "hintedPage": hinted_page,
            "pdfPage": target_page,
            "pageDelta": target_page - hinted_page,
            "pdfRegion": region_id,
            "pdfText": str(row.get("text") or "")[:120],
            "skeleton": source_key,
        })

    return recovered


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v15(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])
    raw_regions = _raw_text_regions(pdf_analysis)
    regions = _region_lookup(pdf_analysis)

    recovered = _short_heading_exact_recovery(items, raw_regions, regions)

    result["version"] = VERSION
    result["shortHeadingExactRecovery"] = {
        "recoveredCount": len(recovered),
        "items": recovered[:120],
        "policy": (
            "remaining short heading/title items may search hinted PDF page ±1; "
            "binding requires one unique unused PDF semantic heading with an exact cross-script skeleton; "
            "digits remain part of the skeleton"
        ),
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
