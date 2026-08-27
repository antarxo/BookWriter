from __future__ import annotations

from collections import Counter
from typing import Any


VERSION = "page-text-style-map-0.2"


def _box(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]


def _area(box: list[float] | None) -> float:
    if not box:
        return 0.0
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _contains(outer: list[float] | None, inner: list[float] | None, tolerance: float = 1.5) -> bool:
    if not outer or not inner:
        return False
    return (
        outer[0] <= inner[0] + tolerance
        and outer[1] <= inner[1] + tolerance
        and outer[2] >= inner[2] - tolerance
        and outer[3] >= inner[3] - tolerance
    )


def _flag_style(flags: Any) -> dict[str, bool]:
    try:
        value = int(flags or 0)
    except (TypeError, ValueError):
        value = 0
    return {
        "superscript": bool(value & 1),
        "italic": bool(value & 2),
        "serif": bool(value & 4),
        "monospaced": bool(value & 8),
        "bold": bool(value & 16),
    }


def _region_runs(region: dict[str, Any]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    lines = list(region.get("lines", []) or [])
    for line_index, line in enumerate(lines):
        for span_index, span in enumerate(line.get("spans", []) or []):
            text = str(span.get("text") or "")
            if not text:
                continue
            flags = int(span.get("flags") or 0)
            runs.append({
                "text": text,
                "bboxPt": _box(span.get("bbox")),
                "fontFamily": str(span.get("font") or "") or None,
                "fontSizePt": float(span.get("size_pt") or 0.0) or None,
                "color": span.get("color"),
                "flags": flags,
                "style": _flag_style(flags),
                "ascender": span.get("ascender"),
                "descender": span.get("descender"),
                "sourceRegionId": region.get("id"),
                "sourceLineIndex": line_index,
                "sourceSpanIndex": span_index,
            })
        if line_index < len(lines) - 1:
            runs.append({
                "text": "\n",
                "lineBreak": True,
                "sourceRegionId": region.get("id"),
                "sourceLineIndex": line_index,
            })
    return runs


def _style_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    weighted: Counter[tuple[Any, ...]] = Counter()
    fonts: Counter[str] = Counter()
    sizes: Counter[float] = Counter()
    colors: Counter[str] = Counter()
    for run in runs:
        if run.get("lineBreak"):
            continue
        text = str(run.get("text") or "")
        weight = max(1, len(text.strip())) if text.strip() else 0
        if not weight:
            continue
        family = run.get("fontFamily")
        size = run.get("fontSizePt")
        color = run.get("color")
        flags = run.get("flags")
        weighted[(family, size, color, flags)] += weight
        if family:
            fonts[str(family)] += weight
        if size is not None:
            sizes[float(size)] += weight
        if color:
            colors[str(color)] += weight
    dominant = weighted.most_common(1)[0][0] if weighted else (None, None, None, None)
    return {
        "dominant": {
            "fontFamily": dominant[0],
            "fontSizePt": dominant[1],
            "color": dominant[2],
            "flags": dominant[3],
            "style": _flag_style(dominant[3]),
        },
        "fontFamilies": sorted(fonts),
        "fontSizesPt": sorted(sizes),
        "colors": sorted(colors),
        "mixedFontFamily": len(fonts) > 1,
        "mixedFontSize": len(sizes) > 1,
        "mixedColor": len(colors) > 1,
        "runCount": sum(1 for run in runs if not run.get("lineBreak")),
    }


def _background_evidence(item_box: list[float] | None, drawings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not item_box:
        return []
    item_area = max(1.0, _area(item_box))
    matches: list[dict[str, Any]] = []
    for drawing in drawings:
        fill = drawing.get("fillColor")
        if fill is None:
            continue
        drawing_box = _box(drawing.get("bbox"))
        if not drawing_box:
            continue
        intersection = _intersection(item_box, drawing_box)
        text_coverage = intersection / item_area
        drawing_area = max(1.0, _area(drawing_box))
        area_ratio = drawing_area / item_area
        contains = _contains(drawing_box, item_box)
        if text_coverage < 0.45 and not contains:
            continue
        if area_ratio > 6.0:
            continue
        matches.append({
            "drawingId": drawing.get("id"),
            "bboxPt": drawing_box,
            "fillColor": fill,
            "fillOpacity": drawing.get("fillOpacity"),
            "strokeColor": drawing.get("strokeColor"),
            "strokeWidthPt": drawing.get("strokeWidthPt"),
            "textCoverage": round(text_coverage, 4),
            "areaRatio": round(area_ratio, 4),
            "containsTextBox": contains,
            "source": "pdf-native-drawing",
        })
    matches.sort(key=lambda row: (-float(row.get("textCoverage") or 0.0), float(row.get("areaRatio") or 999.0)))
    return matches


def _region_lookup(pdf_page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(region.get("id")): region
        for region in pdf_page.get("regions", []) or []
        if region.get("type") == "text" and region.get("id")
    }


def _resolve_region_id(region_id: str, regions: dict[str, dict[str, Any]]) -> str | None:
    if region_id in regions:
        return region_id
    for suffix in ("-span-title", "-left", "-right", "-span"):
        if region_id.endswith(suffix):
            base = region_id[:-len(suffix)]
            if base in regions:
                return base
    return None


def _source_regions(item: dict[str, Any], regions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    ids: list[str] = []
    for key in ("region_ids", "member_ids"):
        value = item.get(key)
        if isinstance(value, list):
            ids.extend(str(region_id) for region_id in value if region_id)
    for key in ("source_region_id", "id"):
        value = item.get(key)
        if value:
            ids.append(str(value))
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    for region_id in ids:
        resolved = _resolve_region_id(region_id, regions)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        found.append(regions[resolved])
    return found


def _enrich_text_item(item: dict[str, Any], regions: dict[str, dict[str, Any]], drawings: list[dict[str, Any]]) -> bool:
    source_regions = _source_regions(item, regions)
    if not source_regions:
        return False
    runs: list[dict[str, Any]] = []
    for region_index, region in enumerate(source_regions):
        if region_index and runs and not runs[-1].get("lineBreak"):
            runs.append({"text": "\n", "lineBreak": True})
        runs.extend(_region_runs(region))
    item["textStyleMap"] = {
        "version": VERSION,
        "authority": "pdf-native-spans",
        "runs": runs,
        "summary": _style_summary(runs),
        "backgroundEvidence": _background_evidence(_box(item.get("bbox")), drawings),
        "sourceRegionIds": [region.get("id") for region in source_regions],
        "policy": "Preserve PDF-native span typography and local fill evidence in the page map; downstream builders consume this map and do not re-read PDF typography.",
    }
    return True


def enrich_page_text_styles(page_structure: dict[str, Any], pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    pdf_pages = {
        int(page.get("page") or 0): page
        for page in pdf_analysis.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    text_item_count = 0
    mapped_item_count = 0
    background_item_count = 0
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        pdf_page = pdf_pages.get(page_no) or {}
        regions = _region_lookup(pdf_page)
        drawings = list(pdf_page.get("drawings", []) or [])
        collections = [
            page.get("flow", []) or [],
            page.get("headers", []) or [],
            page.get("footers", []) or [],
            page.get("callouts", []) or [],
            page.get("banners", []) or [],
        ]
        for collection in collections:
            for item in collection:
                if item.get("type") == "visual":
                    continue
                text_item_count += 1
                if _enrich_text_item(item, regions, drawings):
                    mapped_item_count += 1
                    if (item.get("textStyleMap") or {}).get("backgroundEvidence"):
                        background_item_count += 1
    summary = {
        "version": VERSION,
        "textItemCount": text_item_count,
        "mappedTextItemCount": mapped_item_count,
        "unmappedTextItemCount": text_item_count - mapped_item_count,
        "backgroundEvidenceItemCount": background_item_count,
        "authority": "PDF",
        "policy": "Typography and local text/container background evidence are frozen into page_structure before Word construction.",
    }
    page_structure["textStyleMapSummary"] = summary
    return summary


__all__ = ["enrich_page_text_styles"]
