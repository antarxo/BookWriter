from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from .common import normalize_text
from .markdown_pdf_spine_v14 import build_markdown_pdf_spine as _build_v14
from .markdown_pdf_spine_v08 import _attach, _placed
from .markdown_pdf_spine_v07 import _item_text, _page_no, _region_lookup
from .markdown_pdf_spine_v02 import TEXT_TYPES, _score

VERSION = "markdown-pdf-spine-0.15"

# Extend the v0.14 visual-homoglyph idea with Greek Pi / Cyrillic Pe, which is
# a frequent OCR substitution in Greek uppercase headings (ΠΡΑΞΗ / ПРАΞН).
_HOMOGLYPHS = str.maketrans({
    "Π": "P", "π": "p", "П": "P", "п": "p",
    "А": "A", "а": "a", "В": "B", "Е": "E", "е": "e", "К": "K", "М": "M",
    "Н": "H", "О": "O", "о": "o", "Р": "P", "р": "p", "С": "C", "с": "c",
    "Т": "T", "Х": "X", "х": "x", "Υ": "Y", "Α": "A", "Β": "B", "Ε": "E",
    "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Χ": "X", "ι": "i", "ο": "o", "ρ": "p", "χ": "x",
})


def _skeleton(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).translate(_HOMOGLYPHS).casefold()
    return re.sub(r"[^\w\d]+", "", value, flags=re.UNICODE)


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


def _adjacent_page_recovery(
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
        if kind not in TEXT_TYPES or _placed(item):
            continue
        hinted_page = _page_no(item)
        text = _item_text(item)
        source = normalize_text(text)
        if not hinted_page or len(source) < 20:
            continue

        candidates: list[dict[str, Any]] = []
        for page in (hinted_page - 1, hinted_page, hinted_page + 1):
            if page <= 0:
                continue
            for row in raw_regions.get(page, []):
                region_id = str(row.get("id") or "")
                if (page, region_id) in used:
                    continue
                semantic = str(row.get("semanticType") or "")
                if semantic in {"header", "footer"}:
                    continue
                candidate = dict(row)
                candidate["candidatePage"] = page
                candidates.append(candidate)

        if not candidates:
            continue

        ranked: list[tuple[float, dict[str, Any], bool]] = []
        source_skeleton = _skeleton(text)
        for row in candidates:
            target = str(row.get("normalized") or "")
            score = _score(source, target)
            exact_skeleton = bool(source_skeleton and len(source_skeleton) >= 4 and source_skeleton == _skeleton(str(row.get("text") or "")))
            ranked.append((100.0 if exact_skeleton else score, row, exact_skeleton))
        ranked.sort(key=lambda value: value[0], reverse=True)

        best_score, best_row, exact_skeleton = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        margin = best_score - second_score
        target_page = int(best_row.get("candidatePage") or 0)

        # Adjacent-page moves are deliberately stricter than same-page recovery.
        # Exact skeleton is sufficient; otherwise require a long text and a very
        # strong unique fuzzy match.
        accept = exact_skeleton or (
            len(source) >= 60 and best_score >= 88.0 and margin >= 12.0
        )
        if not accept:
            continue

        _attach(item, best_row, target_page, best_score, regions)
        item["matchMode"] = "adjacent-page-exact-text" if exact_skeleton else "adjacent-page-full-region"
        region_id = str(best_row.get("id") or "")
        used.add((target_page, region_id))
        recovered.append({
            "markdownId": item.get("id"),
            "type": kind,
            "hintedPage": hinted_page,
            "pdfPage": target_page,
            "pageDelta": target_page - hinted_page,
            "pdfRegion": region_id,
            "score": round(float(best_score), 2),
            "secondScore": round(float(second_score), 2),
            "margin": round(float(margin), 2),
            "exactSkeleton": exact_skeleton,
            "pdfText": str(best_row.get("text") or "")[:180],
        })
    return recovered


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v14(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])
    raw_regions = _raw_text_regions(pdf_analysis)
    regions = _region_lookup(pdf_analysis)

    recovered = _adjacent_page_recovery(items, raw_regions, regions)

    result["version"] = VERSION
    result["adjacentPageTextRecovery"] = {
        "recoveredCount": len(recovered),
        "items": recovered[:120],
        "policy": (
            "search unresolved text in hinted page ±1 using full PDF text regions; "
            "accept exact cross-script skeleton or long-text score>=88 with margin>=12"
        ),
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
