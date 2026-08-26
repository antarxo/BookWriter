from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any

from .markdown_pdf_spine_v03 import build_markdown_pdf_spine as _build_v03

VERSION = "markdown-pdf-spine-0.4"

_TEXT_TYPES = {
    "paragraph", "heading", "title", "caption", "callout", "list", "latex_list",
    "list_item", "ordered_list", "unordered_list", "text",
}


def _weighted_profile(spans: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    counts: Counter[Any] = Counter()
    for span in spans:
        text = str(span.get("text") or "")
        if not text.strip():
            continue
        value = span.get(key)
        if value is None or value == "":
            continue
        counts[value] += max(1, len(text.strip()))
    total = sum(counts.values())
    return [
        {"value": value, "weightedChars": weight, "ratio": round(weight / total, 5) if total else 0.0}
        for value, weight in counts.most_common()
    ]


def _page_typography_profiles(pdf_analysis: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for page in (pdf_analysis or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        lines = [
            line
            for region in page.get("regions", []) or []
            if region.get("type") == "text"
            for line in region.get("lines", []) or []
        ]
        spans = [
            span
            for line in lines
            for span in line.get("spans", []) or []
            if str(span.get("text") or "").strip()
        ]
        if not spans:
            continue
        font_profile = _weighted_profile(spans, "font")
        size_profile = _weighted_profile(spans, "size_pt")
        color_profile = _weighted_profile(spans, "color")
        line_boxes = [line.get("bbox") for line in lines if isinstance(line.get("bbox"), (list, tuple)) and len(line.get("bbox")) == 4]
        y_values = sorted(float(box[1]) for box in line_boxes)
        pitches = [round(y_values[i] - y_values[i - 1], 3) for i in range(1, len(y_values)) if y_values[i] > y_values[i - 1]]
        result[page_no] = {
            "source": "pdf-page-text-profile",
            "confidence": "medium",
            "bbox": None,
            "lineCount": len(lines),
            "lineBoxes": [],
            "fontFamily": {"dominant": font_profile[0]["value"] if font_profile else None, "profile": font_profile},
            "fontSizePt": {"dominant": size_profile[0]["value"] if size_profile else None, "profile": size_profile},
            "color": {"dominant": color_profile[0]["value"] if color_profile else None, "profile": color_profile},
            "emphasis": {"boldRatio": None, "italicRatio": None, "serifRatio": None, "monospaceRatio": None, "superscriptRatio": None},
            "linePitch": {"medianPt": round(float(median(pitches)), 3) if pitches else None, "samplesPt": pitches[:200]},
            "direction": None,
            "ascender": None,
            "descender": None,
            "spanCount": len(spans),
            "spans": [],
        }
    return result


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v03(markdown_element_map, pdf_analysis)
    page_profiles = _page_typography_profiles(pdf_analysis)
    fallback_count = 0

    for item in result.get("items", []) or []:
        authoritative = item.get("authoritativeContent") if isinstance(item.get("authoritativeContent"), dict) else {}
        if not str(item.get("text") or ""):
            item["text"] = str(authoritative.get("text") or authoritative.get("plainText") or "")
        typography = item.get("pdfTypography") if isinstance(item.get("pdfTypography"), dict) else {}
        item_type = str(item.get("type") or "").strip().lower()
        if str(typography.get("confidence") or "none") == "none" and item_type in _TEXT_TYPES:
            try:
                page_no = int(item.get("pdfPage") or item.get("inferredPage") or item.get("markdownPageHint") or 0)
            except (TypeError, ValueError):
                page_no = 0
            fallback = page_profiles.get(page_no)
            if fallback:
                item["pdfTypography"] = dict(fallback)
                item["pdfTypography"]["fallbackReason"] = "no-local-text-witness"
                fallback_count += 1

    result["version"] = VERSION
    result["pageTypographyFallbackCount"] = fallback_count
    result["authorityContract"] = {
        "content": "markdown-authoritativeContent including plainText",
        "geometry": "pdf-analysis",
        "typography": "local pdf spans, else same-page pdf text profile",
        "docx": "not-authoritative-here",
    }
    return result
