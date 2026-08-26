from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

from .common import normalize_text

VERSION = "page-furniture-analysis-0.1"


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        round(min(box[0] for box in boxes), 3),
        round(min(box[1] for box in boxes), 3),
        round(max(box[2] for box in boxes), 3),
        round(max(box[3] for box in boxes), 3),
    ]


def _furniture_key(text: str) -> str:
    norm = normalize_text(text)
    compact = " ".join(norm.split())
    if not compact:
        return ""
    # Preserve real words but canonicalize obvious per-page counters/dates so
    # repeated furniture can still be recognized when one token changes.
    if re.fullmatch(r"\d{1,4}\s*/\s*\d{1,4}", compact):
        return "<page-counter>"
    if re.fullmatch(r"\d{1,4}", compact):
        return "<page-number>"
    date_like = re.sub(r"\b\d{1,2}[/:.-]\d{1,2}[/:.-]\d{2,4}\b", "<date>", compact)
    date_like = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", "<time>", date_like)
    return date_like


def _zone(box: list[float], page_height: float) -> str | None:
    if page_height <= 0:
        return None
    y0, y1 = box[1] / page_height, box[3] / page_height
    if y1 <= 0.16:
        return "header"
    if y0 >= 0.82:
        return "footer"
    return None


def analyze_page_furniture(pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    pages = list(pdf_analysis.get("pages", []) or [])
    page_count = len(pages)
    if not page_count:
        return {"version": VERSION, "pageCount": 0, "detectedRegionCount": 0, "patterns": []}

    occurrences: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    explicit_ids: set[tuple[int, str]] = set()

    for page in pages:
        page_no = int(page.get("page") or 0)
        page_height = float(page.get("height_pt") or 842.0)
        for region in page.get("regions", []) or []:
            if region.get("type") != "text":
                continue
            box = _bbox(region.get("bbox"))
            if not box:
                continue
            sem = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
            sem_type = str(sem.get("type") or "")
            region_id = str(region.get("id") or "")
            if sem_type in {"header", "footer"}:
                explicit_ids.add((page_no, region_id))
            zone = _zone(box, page_height)
            key = _furniture_key(str(region.get("text") or ""))
            if not zone or not key:
                continue
            occurrences[(zone, key)].append({
                "page": page_no,
                "regionId": region_id,
                "bbox": box,
                "text": str(region.get("text") or ""),
            })

    repeat_threshold = max(3, int(math.ceil(page_count * 0.25)))
    repeated_keys: set[tuple[str, str]] = set()
    patterns: list[dict[str, Any]] = []
    for (zone, key), rows in occurrences.items():
        distinct_pages = sorted({int(row["page"]) for row in rows})
        if len(distinct_pages) < repeat_threshold:
            continue
        repeated_keys.add((zone, key))
        patterns.append({
            "zone": zone,
            "key": key,
            "pageCount": len(distinct_pages),
            "pages": distinct_pages[:40],
            "sampleText": rows[0].get("text"),
        })

    detected = 0
    by_zone: Counter[str] = Counter()
    page_summaries: list[dict[str, Any]] = []

    for page in pages:
        page_no = int(page.get("page") or 0)
        width = float(page.get("width_pt") or 595.0)
        height = float(page.get("height_pt") or 842.0)
        furniture_boxes: dict[str, list[list[float]]] = {"header": [], "footer": []}
        body_boxes: list[list[float]] = []
        body_text_boxes: list[list[float]] = []

        for region in page.get("regions", []) or []:
            box = _bbox(region.get("bbox"))
            if not box:
                continue
            if region.get("type") != "text":
                body_boxes.append(box)
                continue

            sem = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
            region_id = str(region.get("id") or "")
            zone = _zone(box, height)
            key = _furniture_key(str(region.get("text") or ""))
            is_explicit = (page_no, region_id) in explicit_ids
            is_repeated = bool(zone and key and (zone, key) in repeated_keys)
            if is_explicit or is_repeated:
                furniture_zone = str(sem.get("type") or zone or "")
                if furniture_zone not in {"header", "footer"}:
                    furniture_zone = zone or "header"
                sem["type"] = furniture_zone
                sem["alignable"] = False
                sem["flow_zone"] = "page_furniture"
                sem["confidence"] = max(float(sem.get("confidence") or 0.0), 0.96 if is_repeated else 0.93)
                reasons = sem.setdefault("reasons", [])
                reason = "repeated page-furniture pattern" if is_repeated else "existing header/footer classification"
                if reason not in reasons:
                    reasons.append(reason)
                sem["pageFurniture"] = {
                    "detected": True,
                    "zone": furniture_zone,
                    "patternKey": key or None,
                    "repeated": is_repeated,
                }
                region["semantic"] = sem
                furniture_boxes[furniture_zone].append(box)
                detected += 1
                by_zone[furniture_zone] += 1
                continue

            sem_type = str(sem.get("type") or "body")
            if sem_type != "noise":
                body_boxes.append(box)
                body_text_boxes.append(box)

        page_content_boxes = [
            box for region in page.get("regions", []) or []
            for box in [_bbox(region.get("bbox"))]
            if box
        ]
        content_bbox = _union(page_content_boxes)
        body_bbox = _union(body_boxes)
        body_text_bbox = _union(body_text_boxes)
        header_bbox = _union(furniture_boxes["header"])
        footer_bbox = _union(furniture_boxes["footer"])

        body_top = float(body_bbox[1]) if body_bbox else 0.0
        body_bottom = float(body_bbox[3]) if body_bbox else height
        body_left = float(body_bbox[0]) if body_bbox else 0.0
        body_right = float(body_bbox[2]) if body_bbox else width
        geometry = {
            "version": VERSION,
            "pageBBox": [0.0, 0.0, round(width, 3), round(height, 3)],
            "pageContentBBox": content_bbox,
            "bodyBBox": body_bbox,
            "bodyTextBBox": body_text_bbox,
            "headerBBox": header_bbox,
            "footerBBox": footer_bbox,
            "bodyBand": [round(body_top, 3), round(body_bottom, 3)] if body_bbox else None,
            "inferredMarginsPt": {
                "top": round(max(0.0, body_top), 3),
                "bottom": round(max(0.0, height - body_bottom), 3),
                "left": round(max(0.0, body_left), 3),
                "right": round(max(0.0, width - body_right), 3),
            } if body_bbox else None,
        }
        page["pageGeometry"] = geometry
        page_summaries.append({
            "page": page_no,
            "bodyBBox": body_bbox,
            "headerBBox": header_bbox,
            "footerBBox": footer_bbox,
            "inferredMarginsPt": geometry.get("inferredMarginsPt"),
        })

    summary = {
        "version": VERSION,
        "pageCount": page_count,
        "repeatThresholdPages": repeat_threshold,
        "detectedRegionCount": detected,
        "detectedByZone": dict(by_zone),
        "patterns": patterns[:120],
        "pages": page_summaries[:400],
        "policy": (
            "header/footer furniture is inferred from repeated normalized text in stable top/bottom page zones, "
            "plus existing high-confidence classifier labels; furniture is excluded from bodyBBox but retained as PDF evidence"
        ),
    }
    pdf_analysis["pageFurniture"] = summary
    return summary


__all__ = ["analyze_page_furniture"]
