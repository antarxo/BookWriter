from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from .common import normalize_text
from .markdown_pdf_spine_v13 import build_markdown_pdf_spine as _build_v13
from .markdown_pdf_spine_v08 import _attach, _placed
from .markdown_pdf_spine_v07 import _item_text, _page_no, _region_lookup
from .markdown_pdf_spine_v02 import _score

VERSION = "markdown-pdf-spine-0.14.1"

HEADING_TYPES = {"heading", "title"}
PARAGRAPH_TYPES = {"paragraph"}

# Visual homoglyphs that commonly appear when Greek text is extracted through
# mixed Cyrillic/Latin fonts. Canonicalize only characters with an essentially
# identical glyph; do not transliterate ordinary Greek text.
_HOMOGLYPHS = str.maketrans({
    "А": "A", "а": "a", "В": "B", "Е": "E", "е": "e", "К": "K", "М": "M",
    "Н": "H", "О": "O", "о": "o", "Р": "P", "р": "p", "С": "C", "с": "c",
    "Т": "T", "Х": "X", "х": "x", "Υ": "Y", "Α": "A", "Β": "B", "Ε": "E",
    "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Χ": "X", "ι": "i", "ο": "o", "ρ": "p", "χ": "x",
})


def _heading_key(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).translate(_HOMOGLYPHS).casefold()
    value = re.sub(r"[^\w\d]+", "", value, flags=re.UNICODE)
    return value


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


def _exact_heading_repair(
    items: list[dict[str, Any]],
    raw_regions: dict[int, list[dict[str, Any]]],
    regions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover only unresolved headings into currently unused PDF regions.

    This stage is intentionally monotonic: it may add a binding, but it must
    never remove or replace an earlier resolved binding. Later recovery passes
    are fallbacks, not authorities over already accepted evidence.
    """
    markdown_by_page_key: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    pdf_by_page_key: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)

    used_regions = {
        (int(item.get("pdfPage") or 0), str(item.get("pdfRegion") or ""))
        for item in items
        if item.get("pdfRegion")
    }

    for item in items:
        if str(item.get("type") or "") not in HEADING_TYPES or _placed(item):
            continue
        page = _page_no(item)
        key = _heading_key(_item_text(item))
        if page and key and len(key) >= 4:
            markdown_by_page_key[(page, key)].append(item)

    for page, rows in raw_regions.items():
        for row in rows:
            if str(row.get("semanticType") or "") != "heading":
                continue
            region_id = str(row.get("id") or "")
            if (page, region_id) in used_regions:
                continue
            key = _heading_key(str(row.get("text") or ""))
            if key and len(key) >= 4:
                pdf_by_page_key[(page, key)].append(row)

    repairs: list[dict[str, Any]] = []
    for page_key, md_items in markdown_by_page_key.items():
        pdf_rows = pdf_by_page_key.get(page_key) or []
        if len(md_items) != 1 or len(pdf_rows) != 1:
            continue
        page, key = page_key
        item = md_items[0]
        row = pdf_rows[0]
        region_id = str(row.get("id") or "")
        if (page, region_id) in used_regions:
            continue

        _attach(item, row, page, 100.0, regions)
        item["matchMode"] = "exact-heading-homoglyph-key-unused-only"
        used_regions.add((page, region_id))
        repairs.append({
            "markdownId": item.get("id"),
            "page": page,
            "key": key,
            "pdfRegion": region_id,
            "pdfText": str(row.get("text") or "")[:180],
        })
    return repairs


def _full_region_paragraph_recovery(
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
        if str(item.get("type") or "") not in PARAGRAPH_TYPES or _placed(item):
            continue
        page = _page_no(item)
        source = normalize_text(_item_text(item))
        if not page or len(source) < 60:
            continue
        candidates = [
            row for row in raw_regions.get(page, [])
            if (page, str(row.get("id") or "")) not in used
            and str(row.get("flowZone") or "") not in {"header", "footer"}
            and str(row.get("semanticType") or "") in {"body", "paragraph", "text", ""}
        ]
        ranked = [(_score(source, str(row.get("normalized") or "")), row) for row in candidates]
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        if not ranked:
            continue
        best_score, best_row = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        margin = best_score - second_score
        if not (best_score >= 82.0 or (best_score >= 74.0 and margin >= 14.0)):
            continue
        _attach(item, best_row, page, best_score, regions)
        item["matchMode"] = "full-pdf-region-paragraph"
        region_id = str(best_row.get("id") or "")
        used.add((page, region_id))
        recovered.append({
            "markdownId": item.get("id"),
            "page": page,
            "pdfRegion": region_id,
            "score": round(float(best_score), 2),
            "secondScore": round(float(second_score), 2),
            "margin": round(float(margin), 2),
            "pdfText": str(best_row.get("text") or "")[:180],
        })
    return recovered


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v13(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])
    raw_regions = _raw_text_regions(pdf_analysis)
    regions = _region_lookup(pdf_analysis)

    heading_repairs = _exact_heading_repair(items, raw_regions, regions)
    paragraph_recoveries = _full_region_paragraph_recovery(items, raw_regions, regions)

    result["version"] = VERSION
    result["structuralTextRecovery"] = {
        "headingExactRepairCount": len(heading_repairs),
        "fullRegionParagraphRecoveryCount": len(paragraph_recoveries),
        "headingRepairs": heading_repairs[:120],
        "paragraphRecoveries": paragraph_recoveries[:120],
        "policy": (
            "unresolved unique exact heading keys may bind only to unused PDF semantic headings; "
            "existing bindings are never cleared or reassigned; long paragraphs may bind only to unused full PDF text regions with high score/margin; headers/footers excluded"
        ),
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
