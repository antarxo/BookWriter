from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any

from .markdown_pdf_spine_v02 import build_markdown_pdf_spine as _build_v02


VERSION = "markdown-pdf-spine-0.3"


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [round(float(part), 3) for part in value]
    except (TypeError, ValueError):
        return None


def _markdown_records(markdown_element_map: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in (markdown_element_map or {}).get("records", []) or []
        if item.get("id")
    }


def _pdf_pages(pdf_analysis: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(page.get("page") or 0): page
        for page in (pdf_analysis or {}).get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }


def _region_lookup(pdf_analysis: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[dict[str, Any], dict[str, Any], int]]]:
    regions: dict[str, dict[str, Any]] = {}
    lines: dict[str, tuple[dict[str, Any], dict[str, Any], int]] = {}
    for page in (pdf_analysis or {}).get("pages", []) or []:
        for region in page.get("regions", []) or []:
            region_id = str(region.get("id") or "")
            if not region_id:
                continue
            regions[region_id] = region
            for index, line in enumerate(region.get("lines", []) or [], start=1):
                lines[f"{region_id}-line{index:03d}"] = (region, line, index)
    return regions, lines


def _flags_summary(spans: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(max(1, len(str(span.get("text") or "").strip())) for span in spans if str(span.get("text") or "").strip())
    if total <= 0:
        return {
            "boldRatio": None,
            "italicRatio": None,
            "serifRatio": None,
            "monospaceRatio": None,
            "superscriptRatio": None,
        }

    def ratio(bit: int) -> float:
        weight = sum(
            max(1, len(str(span.get("text") or "").strip()))
            for span in spans
            if str(span.get("text") or "").strip() and int(span.get("flags") or 0) & bit
        )
        return round(weight / total, 5)

    return {
        "boldRatio": ratio(16),
        "italicRatio": ratio(2),
        "serifRatio": ratio(4),
        "monospaceRatio": ratio(8),
        "superscriptRatio": ratio(1),
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
        {
            "value": value,
            "weightedChars": weight,
            "ratio": round(weight / total, 5) if total else 0.0,
        }
        for value, weight in counts.most_common()
    ]


def _line_pitch(lines: list[dict[str, Any]]) -> dict[str, Any]:
    boxes = [_bbox(line.get("bbox")) for line in lines]
    boxes = [box for box in boxes if box]
    if not boxes:
        return {"medianPt": None, "samplesPt": []}
    boxes.sort(key=lambda box: (box[1], box[0]))
    samples = [round(boxes[index][1] - boxes[index - 1][1], 3) for index in range(1, len(boxes))]
    samples = [value for value in samples if value > 0]
    return {
        "medianPt": round(float(median(samples)), 3) if samples else None,
        "samplesPt": samples,
    }


def _typography_from_lines(lines: list[dict[str, Any]], bbox: list[float] | None, source: str) -> dict[str, Any]:
    spans = [span for line in lines for span in (line.get("spans", []) or []) if str(span.get("text") or "").strip()]
    font_profile = _weighted_profile(spans, "font")
    size_profile = _weighted_profile(spans, "size_pt")
    color_profile = _weighted_profile(spans, "color")
    direction_profile = Counter(tuple(line.get("dir") or []) for line in lines if line.get("dir"))
    dominant_direction = list(direction_profile.most_common(1)[0][0]) if direction_profile else None
    pitch = _line_pitch(lines)
    ascenders = [float(span.get("ascender")) for span in spans if span.get("ascender") is not None]
    descenders = [float(span.get("descender")) for span in spans if span.get("descender") is not None]
    return {
        "source": source,
        "confidence": "high" if spans else "none",
        "bbox": bbox,
        "lineCount": len(lines),
        "lineBoxes": [_bbox(line.get("bbox")) for line in lines if _bbox(line.get("bbox"))],
        "fontFamily": {
            "dominant": font_profile[0]["value"] if font_profile else None,
            "profile": font_profile,
        },
        "fontSizePt": {
            "dominant": size_profile[0]["value"] if size_profile else None,
            "profile": size_profile,
        },
        "color": {
            "dominant": color_profile[0]["value"] if color_profile else None,
            "profile": color_profile,
        },
        "emphasis": _flags_summary(spans),
        "linePitch": pitch,
        "direction": dominant_direction,
        "ascender": round(float(median(ascenders)), 4) if ascenders else None,
        "descender": round(float(median(descenders)), 4) if descenders else None,
        "spanCount": len(spans),
        "spans": spans,
    }


def _pdf_witness(item: dict[str, Any], regions: dict[str, dict[str, Any]], line_lookup: dict[str, tuple[dict[str, Any], dict[str, Any], int]]) -> dict[str, Any]:
    row_granularity = str(item.get("pdfRowGranularity") or "")
    region_id = str(item.get("pdfRegion") or "")
    parent_id = str(item.get("pdfParentRegion") or "")
    line_index = item.get("pdfLineIndex")

    if row_granularity == "pdf-line" and region_id in line_lookup:
        region, line, _index = line_lookup[region_id]
        return {
            "typography": _typography_from_lines([line], _bbox(line.get("bbox")), "pdf-line"),
            "regionBBox": _bbox(region.get("bbox")),
            "originalBlockBBox": _bbox(region.get("original_block_bbox")),
        }

    if parent_id and parent_id in regions and line_index is not None:
        region = regions[parent_id]
        lines = list(region.get("lines", []) or [])
        try:
            start = max(0, int(line_index) - 1)
        except (TypeError, ValueError):
            start = 0
        if row_granularity == "pdf-line-cluster":
            match_box = _bbox(item.get("bbox"))
            if match_box:
                selected = []
                for line in lines:
                    box = _bbox(line.get("bbox"))
                    if box and match_box[1] - 1.0 <= (box[1] + box[3]) / 2.0 <= match_box[3] + 1.0:
                        selected.append(line)
                lines = selected or lines[start:start + 1]
            else:
                lines = lines[start:start + 1]
        else:
            lines = lines[start:start + 1]
        return {
            "typography": _typography_from_lines(lines, _bbox(item.get("bbox")), row_granularity or "pdf-parent-line"),
            "regionBBox": _bbox(region.get("bbox")),
            "originalBlockBBox": _bbox(region.get("original_block_bbox")),
        }

    target_id = parent_id or region_id
    region = regions.get(target_id)
    if region:
        lines = list(region.get("lines", []) or [])
        return {
            "typography": _typography_from_lines(lines, _bbox(item.get("bbox")) or _bbox(region.get("bbox")), "pdf-region"),
            "regionBBox": _bbox(region.get("bbox")),
            "originalBlockBBox": _bbox(region.get("original_block_bbox")),
        }

    return {
        "typography": {
            "source": "no-text-witness",
            "confidence": "none",
            "bbox": _bbox(item.get("bbox")),
            "lineCount": 0,
            "lineBoxes": [],
            "fontFamily": {"dominant": None, "profile": []},
            "fontSizePt": {"dominant": None, "profile": []},
            "color": {"dominant": None, "profile": []},
            "emphasis": _flags_summary([]),
            "linePitch": {"medianPt": None, "samplesPt": []},
            "direction": None,
            "ascender": None,
            "descender": None,
            "spanCount": 0,
            "spans": [],
        },
        "regionBBox": None,
        "originalBlockBBox": None,
    }


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v02(markdown_element_map, pdf_analysis)
    markdown_by_id = _markdown_records(markdown_element_map)
    regions, line_lookup = _region_lookup(pdf_analysis)
    pages = _pdf_pages(pdf_analysis)
    typography_count = 0

    for item in result.get("items", []) or []:
        markdown_id = str(item.get("id") or "")
        source = markdown_by_id.get(markdown_id) or {}
        authoritative = source.get("authoritativeContent") or {}
        item["authoritativeContent"] = authoritative
        item["rawMarkdown"] = str(source.get("rawMarkdown") or "")
        item["text"] = str(
            authoritative.get("text")
            or source.get("text")
            or source.get("captionText")
            or source.get("alt")
            or source.get("latex")
            or item.get("text")
            or ""
        )
        witness = _pdf_witness(item, regions, line_lookup)
        item["pdfTypography"] = witness["typography"]
        page = pages.get(int(item.get("pdfPage") or 0)) or {}
        item["pdfGeometry"] = {
            "bbox": _bbox(item.get("bbox")),
            "regionBBox": witness.get("regionBBox"),
            "originalBlockBBox": witness.get("originalBlockBBox"),
            "page": item.get("pdfPage"),
            "pageBox": {
                "widthPt": page.get("width_pt"),
                "heightPt": page.get("height_pt"),
                "rotation": page.get("rotation"),
            },
        }
        if str((item.get("pdfTypography") or {}).get("confidence")) == "high":
            typography_count += 1

    result["version"] = VERSION
    result["truthModel"] = "markdown-content-first/pdf-geometry-and-typography-guided/docx-native-donor-only"
    result["authorityContract"] = {
        "content": "markdown-authoritativeContent",
        "geometry": "pdf-analysis",
        "typography": "pdf-analysis-spans",
        "docx": "not-authoritative-here",
    }
    result["typographyWitnessCount"] = typography_count
    result["typographyWitnessCoverage"] = round(typography_count / len(result.get("items", []) or []), 5) if result.get("items") else 1.0
    return result
