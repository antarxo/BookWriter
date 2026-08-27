from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

from .common import normalize_text

VERSION = "page-furniture-analysis-0.2"


def _bbox(value: Any) -> list[float] | None:
    if isinstance(value, dict):
        try:
            x0, y0, x1, y1 = [float(value.get(key)) for key in ("x0", "y0", "x1", "y1")]
        except (TypeError, ValueError):
            return None
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            x0, y0, x1, y1 = [float(part) for part in value]
        except (TypeError, ValueError):
            return None
    else:
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


def _intersection(a: list[float], b: list[float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _furniture_key(text: str) -> str:
    norm = normalize_text(text)
    compact = " ".join(norm.split())
    if not compact:
        return ""
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


def _mathpix_page_info(mathpix_line_layout_map: dict[str, Any] | None) -> dict[int, list[dict[str, Any]]]:
    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not mathpix_line_layout_map:
        return by_page
    for page in mathpix_line_layout_map.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        if page_no <= 0:
            continue
        for obj in page.get("objects", []) or []:
            if str(obj.get("type") or "") != "page_info":
                continue
            box = _bbox(obj.get("bbox_pt"))
            text = str(obj.get("text_display") or obj.get("text") or "")
            if box:
                by_page[page_no].append({
                    "id": obj.get("id"),
                    "bbox": box,
                    "text": text,
                    "key": _furniture_key(text),
                })
    return by_page


def _mathpix_witness(
    page_no: int,
    pdf_box: list[float],
    pdf_text: str,
    zone: str | None,
    mathpix_by_page: dict[int, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    if zone not in {"header", "footer"}:
        return None
    pdf_key = _furniture_key(pdf_text)
    best: tuple[float, dict[str, Any]] | None = None
    for item in mathpix_by_page.get(page_no, []):
        item_box = item["bbox"]
        inter = _intersection(pdf_box, item_box)
        overlap_pdf = inter / max(1.0, _area(pdf_box))
        overlap_mpx = inter / max(1.0, _area(item_box))
        text_match = bool(pdf_key and item.get("key") and pdf_key == item.get("key"))
        center_pdf = ((pdf_box[0] + pdf_box[2]) / 2.0, (pdf_box[1] + pdf_box[3]) / 2.0)
        center_mpx = ((item_box[0] + item_box[2]) / 2.0, (item_box[1] + item_box[3]) / 2.0)
        center_distance = math.hypot(center_pdf[0] - center_mpx[0], center_pdf[1] - center_mpx[1])
        score = max(overlap_pdf, overlap_mpx) * 70.0
        if text_match:
            score += 35.0
        score -= min(25.0, center_distance * 0.35)
        if best is None or score > best[0]:
            best = (score, item)
    if best is None or best[0] < 38.0:
        return None
    score, item = best
    return {
        "source": "mathpix-page_info-semantic-witness/pdf-geometry-authority",
        "mathpixObjectId": item.get("id"),
        "mathpixBBoxPtDiagnosticOnly": [round(v, 3) for v in item["bbox"]],
        "score": round(score, 2),
        "textKeyMatch": bool(pdf_key and pdf_key == item.get("key")),
    }


def analyze_page_furniture(
    pdf_analysis: dict[str, Any],
    mathpix_line_layout_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pages = list(pdf_analysis.get("pages", []) or [])
    page_count = len(pages)
    if not page_count:
        return {"version": VERSION, "pageCount": 0, "detectedRegionCount": 0, "patterns": []}

    mathpix_by_page = _mathpix_page_info(mathpix_line_layout_map)
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
    mathpix_witnessed = 0
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
            witness = _mathpix_witness(page_no, box, str(region.get("text") or ""), zone, mathpix_by_page)
            is_mathpix_witnessed = witness is not None
            if is_explicit or is_repeated or is_mathpix_witnessed:
                furniture_zone = str(sem.get("type") or zone or "")
                if furniture_zone not in {"header", "footer"}:
                    furniture_zone = zone or "header"
                sem["type"] = furniture_zone
                sem["alignable"] = False
                sem["flow_zone"] = "page_furniture"
                confidence = 0.96 if is_repeated else (0.95 if is_mathpix_witnessed else 0.93)
                sem["confidence"] = max(float(sem.get("confidence") or 0.0), confidence)
                reasons = sem.setdefault("reasons", [])
                if is_repeated and "repeated page-furniture pattern" not in reasons:
                    reasons.append("repeated page-furniture pattern")
                if is_explicit and "existing header/footer classification" not in reasons:
                    reasons.append("existing header/footer classification")
                if is_mathpix_witnessed and "Mathpix page_info confirms PDF page-furniture role" not in reasons:
                    reasons.append("Mathpix page_info confirms PDF page-furniture role")
                sem["pageFurniture"] = {
                    "detected": True,
                    "zone": furniture_zone,
                    "patternKey": key or None,
                    "repeated": is_repeated,
                    "mathpixWitness": witness,
                    "geometryAuthority": "pdf-native-region-bbox",
                }
                region["semantic"] = sem
                furniture_boxes[furniture_zone].append(box)
                detected += 1
                by_zone[furniture_zone] += 1
                if is_mathpix_witnessed:
                    mathpix_witnessed += 1
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
            "geometryAuthority": "pdf-native-regions",
            "semanticWitnesses": ["pdf-recurrence", "existing-classifier", "mathpix-page_info"],
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
        "mathpixWitnessedRegionCount": mathpix_witnessed,
        "mathpixPageInfoEvidenceCount": sum(len(rows) for rows in mathpix_by_page.values()),
        "detectedByZone": dict(by_zone),
        "patterns": patterns[:120],
        "pages": page_summaries[:400],
        "geometryAuthority": "PDF",
        "policy": (
            "PDF-native region coordinates are authoritative for header/footer/body geometry and inferred margins. "
            "Mathpix page_info may confirm semantic page-furniture role but never supplies final map coordinates."
        ),
    }
    pdf_analysis["pageFurniture"] = summary
    return summary


__all__ = ["analyze_page_furniture"]
