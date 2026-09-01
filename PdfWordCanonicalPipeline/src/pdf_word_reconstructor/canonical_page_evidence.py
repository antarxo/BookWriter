from __future__ import annotations

import re
from collections import Counter
from statistics import median
from typing import Any


VERSION = "canonical-page-evidence-0.1"


# This module deliberately consumes the already-normalized Mathpix Lines map and
# produces observations only.  It does not mutate page_structure, does not emit
# Word constructs, and does not decide main/sidebar/columns as renderer facts.


def _box_px(obj: dict[str, Any]) -> list[float] | None:
    src = obj.get("bbox_px") if isinstance(obj.get("bbox_px"), dict) else None
    if not src:
        return None
    try:
        x0 = float(src.get("x0")); y0 = float(src.get("y0"))
        x1 = float(src.get("x1")); y1 = float(src.get("y1"))
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _norm_text(obj: dict[str, Any]) -> str:
    value = obj.get("text_display") or obj.get("text") or obj.get("conversion_output") or ""
    return " ".join(str(value).split()).casefold()


def _repeat_signature(text: str) -> str:
    text = " ".join(str(text or "").split()).casefold()
    return re.sub(r"\b\d+\b", "#", text)


def _page_info_records(page: dict[str, Any]) -> list[dict[str, Any]]:
    width = float(page.get("page_width_px") or 0)
    height = float(page.get("page_height_px") or 0)
    if width <= 0 or height <= 0:
        return []
    rows: list[dict[str, Any]] = []
    for obj in page.get("objects", []) or []:
        if str(obj.get("type") or "") != "page_info":
            continue
        box = _box_px(obj)
        if box is None:
            continue
        text = _norm_text(obj)
        rows.append({
            "id": obj.get("id"),
            "bboxPx": box,
            "text": text,
            "signature": _repeat_signature(text),
            "x0Ratio": box[0] / width,
            "x1Ratio": box[2] / width,
            "yCenterRatio": ((box[1] + box[3]) / 2.0) / height,
        })
    return rows


def _document_furniture_profile(pages: list[dict[str, Any]]) -> dict[str, Any]:
    signatures = {"top": Counter(), "bottom": Counter()}
    centers = {"top": [], "bottom": []}
    for page in pages:
        for row in _page_info_records(page):
            ratio = float(row["yCenterRatio"])
            if ratio <= 0.16:
                side = "top"
            elif ratio >= 0.82:
                side = "bottom"
            else:
                continue
            if row["signature"]:
                signatures[side][row["signature"]] += 1
            centers[side].append(ratio)
    return {
        "topSignatureCounts": dict(signatures["top"]),
        "bottomSignatureCounts": dict(signatures["bottom"]),
        "topMedianCenterRatio": median(centers["top"]) if centers["top"] else None,
        "bottomMedianCenterRatio": median(centers["bottom"]) if centers["bottom"] else None,
        "_signatureCounters": signatures,
    }


def _classify_furniture(page: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    counters = profile.get("_signatureCounters") or {"top": Counter(), "bottom": Counter()}
    medians = {
        "top": profile.get("topMedianCenterRatio"),
        "bottom": profile.get("bottomMedianCenterRatio"),
    }
    buckets: dict[str, list[dict[str, Any]]] = {"top": [], "bottom": [], "middle": []}
    for row in _page_info_records(page):
        ratio = float(row["yCenterRatio"])
        if ratio <= 0.16:
            side = "top"
        elif ratio >= 0.82:
            side = "bottom"
        else:
            buckets["middle"].append({**row, "status": "middle-page-info"})
            continue
        repetition = int((counters.get(side) or Counter()).get(row.get("signature") or "", 0))
        median_y = medians.get(side)
        band_delta = abs(ratio - float(median_y)) if median_y is not None else None
        repeated = bool(row.get("signature")) and repetition >= 2
        stable_band = band_delta is not None and band_delta <= 0.035
        accepted = bool(row.get("text")) and (repeated or stable_band)
        confidence = "high" if accepted and repeated and stable_band else ("medium" if accepted else "low")
        buckets[side].append({
            **row,
            "repetitionCount": repetition,
            "bandDeltaRatio": round(band_delta, 5) if band_delta is not None else None,
            "accepted": accepted,
            "confidence": confidence,
        })

    def make_band(side: str) -> dict[str, Any] | None:
        accepted = [r for r in buckets[side] if r.get("accepted")]
        if not accepted:
            return None
        box = _union([r["bboxPx"] for r in accepted])
        if box is None:
            return None
        high = any(r.get("confidence") == "high" for r in accepted)
        return {
            "bboxPx": box,
            "objectIds": [r.get("id") for r in accepted],
            "confidence": "high" if high else "medium",
            "source": "mathpix-page-info-recurrence",
        }

    header = make_band("top")
    footer = make_band("bottom")

    def status(side: str, band: dict[str, Any] | None) -> str:
        if band:
            return "present-high" if band.get("confidence") == "high" else "present-medium"
        if buckets[side]:
            return "unresolved-candidates"
        return "no-page-info-evidence"

    header_status = status("top", header)
    footer_status = status("bottom", footer)
    return {
        "header": header,
        "footer": footer,
        "headerStatus": header_status,
        "footerStatus": footer_status,
        "middlePageInfo": buckets["middle"],
        "topCandidates": buckets["top"],
        "bottomCandidates": buckets["bottom"],
        "bodyInferencePermission": (
            "allowed" if header_status.startswith("present-") and footer_status.startswith("present-")
            else "blocked"
        ),
        "policy": "missing page_info never proves absence; body inference is gated by positively resolved top and bottom furniture",
    }


def _body_objects(page: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = {
        "page_info", "column", "table_row", "table_column",
        "table_of_contents_container", "table_of_contents_row", "table_of_contents_number",
    }
    rows = []
    for obj in page.get("objects", []) or []:
        typ = str(obj.get("type") or "")
        if typ in excluded:
            continue
        box = _box_px(obj)
        if box is None:
            continue
        rows.append({
            "id": obj.get("id"),
            "type": typ,
            "bboxPx": box,
            "parentId": obj.get("parent_id"),
        })
    return rows


def _quantile(values: list[float], fraction: float) -> float:
    vals = sorted(float(v) for v in values)
    idx = min(len(vals) - 1, max(0, round((len(vals) - 1) * fraction)))
    return vals[idx]


def _infer_body(page: dict[str, Any], furniture: dict[str, Any]) -> dict[str, Any]:
    width = float(page.get("page_width_px") or 0)
    height = float(page.get("page_height_px") or 0)
    if width <= 0 or height <= 0:
        return {"status": "blocked", "reason": "missing-page-dimensions"}
    if furniture.get("bodyInferencePermission") != "allowed":
        return {"status": "blocked", "reason": "page-furniture-unresolved"}

    header_box = (furniture.get("header") or {}).get("bboxPx") or [0, 0, 0, 0]
    footer_box = (furniture.get("footer") or {}).get("bboxPx") or [0, height, 0, height]
    header_end = float(header_box[3])
    footer_start = float(footer_box[1])
    rows = [
        r for r in _body_objects(page)
        if r["bboxPx"][3] > header_end and r["bboxPx"][1] < footer_start
    ]
    if not rows:
        return {"status": "blocked", "reason": "no-body-objects-after-furniture-exclusion"}

    x0s = [r["bboxPx"][0] for r in rows]
    x1s = [r["bboxPx"][2] for r in rows]
    y0s = [r["bboxPx"][1] for r in rows]
    y1s = [r["bboxPx"][3] for r in rows]
    robust = [
        _quantile(x0s, 0.08),
        max(_quantile(y0s, 0.04), header_end),
        _quantile(x1s, 0.92),
        min(_quantile(y1s, 0.96), footer_start),
    ]
    if robust[2] <= robust[0] or robust[3] <= robust[1]:
        return {"status": "blocked", "reason": "invalid-robust-body-envelope"}
    return {
        "status": "observed",
        "bboxPx": robust,
        "rawEnvelopePx": _union([r["bboxPx"] for r in rows]),
        "objectCount": len(rows),
        "normalizedMargins": {
            "left": robust[0] / width,
            "right": (width - robust[2]) / width,
            "top": robust[1] / height,
            "bottom": (height - robust[3]) / height,
        },
        "confidence": "high" if len(rows) >= 8 else "medium",
        "source": "mathpix-lines-robust-body-observation",
        "policy": "observation only; does not overwrite page_structure or Word margins",
    }


def _zone_rows(page: dict[str, Any], body: dict[str, Any]) -> list[dict[str, Any]]:
    width = float(page.get("page_width_px") or 0)
    height = float(page.get("page_height_px") or 0)
    body_box = body.get("bboxPx") if body.get("status") == "observed" else None
    body_height = (body_box[3] - body_box[1]) if body_box else height
    rows = []
    for obj in page.get("objects", []) or []:
        if str(obj.get("type") or "") != "column":
            continue
        box = _box_px(obj)
        if box is None or width <= 0 or height <= 0:
            continue
        rows.append({
            "zoneId": str(obj.get("id") or ""),
            "bboxPx": box,
            "widthRatio": (box[2] - box[0]) / width,
            "heightRatioToBody": (box[3] - box[1]) / max(1.0, body_height),
            "x0Ratio": box[0] / width,
            "x1Ratio": box[2] / width,
            "parentId": obj.get("parent_id"),
            "childrenIds": list(obj.get("children_ids") or []),
            "source": "mathpix-lines-column-envelope",
        })
    return sorted(rows, key=lambda r: (r["bboxPx"][0], r["bboxPx"][1]))


def _classify_zone_relationship(page: dict[str, Any], body: dict[str, Any], zones: list[dict[str, Any]]) -> dict[str, Any]:
    width = float(page.get("page_width_px") or 0)
    if not zones:
        return {
            "classification": "no-explicit-zones",
            "confidence": "low",
            "rendererMeaning": "unresolved",
        }

    long_lived = [z for z in zones if z["heightRatioToBody"] >= 0.45 and z["widthRatio"] >= 0.16]
    if len(long_lived) == 2:
        left, right = long_lived
        gap_ratio = max(0.0, right["bboxPx"][0] - left["bboxPx"][2]) / max(1.0, width)
        width_similarity = min(left["widthRatio"], right["widthRatio"]) / max(left["widthRatio"], right["widthRatio"])
        if width_similarity >= 0.72 and gap_ratio <= 0.12:
            classification = "balanced-parallel-zones"
            confidence = "high"
        elif min(left["widthRatio"], right["widthRatio"]) <= 0.28 and max(left["widthRatio"], right["widthRatio"]) >= 0.48:
            classification = "asymmetric-parallel-zones"
            confidence = "high"
        else:
            classification = "ambiguous-parallel-zones"
            confidence = "medium"
        return {
            "classification": classification,
            "confidence": confidence,
            "zoneIds": [left["zoneId"], right["zoneId"]],
            "widthSimilarity": round(width_similarity, 4),
            "gapRatio": round(gap_ratio, 4),
            "rendererMeaning": "unresolved",
            "policy": "geometric relationship only; balanced does not mean Word columns and asymmetric does not mean sidebar",
        }
    if len(long_lived) == 1:
        return {
            "classification": "single-long-lived-zone",
            "confidence": "medium",
            "zoneIds": [long_lived[0]["zoneId"]],
            "rendererMeaning": "unresolved",
        }
    if len(long_lived) > 2:
        return {
            "classification": "multiple-long-lived-zones",
            "confidence": "medium",
            "zoneIds": [z["zoneId"] for z in long_lived],
            "rendererMeaning": "unresolved",
        }
    return {
        "classification": "local-zones-only",
        "confidence": "medium",
        "zoneIds": [z["zoneId"] for z in zones],
        "rendererMeaning": "unresolved",
    }


def build_canonical_page_evidence(line_map: dict[str, Any]) -> dict[str, Any]:
    pages = list(line_map.get("pages", []) or [])
    profile = _document_furniture_profile(pages)
    serial_profile = {k: v for k, v in profile.items() if not k.startswith("_")}
    out = []
    counts = Counter()

    for page in pages:
        furniture = _classify_furniture(page, profile)
        body = _infer_body(page, furniture)
        zones = _zone_rows(page, body)
        relation = _classify_zone_relationship(page, body, zones)
        counts[relation.get("classification") or "unknown"] += 1
        out.append({
            "page": int(page.get("page") or 0),
            "pageSizePx": [float(page.get("page_width_px") or 0), float(page.get("page_height_px") or 0)],
            "furnitureEvidence": furniture,
            "bodyEvidence": body,
            "zones": zones,
            "zoneRelationship": relation,
            "crossZoneReadingOrder": {
                "status": "unresolved" if len(zones) > 1 else "not-applicable",
                "source": None,
                "policy": "no global reading order is inferred from geometric zone position alone",
            },
            "wordRealization": None,
        })

    return {
        "version": VERSION,
        "status": "renderer-neutral-observation-only",
        "policy": {
            "mutatesPageStructure": False,
            "wordDecisions": "forbidden",
            "zoneNames": "geometric-only-no-main-sidebar-column-labels",
            "furniture": "recurrence-plus-edge-band-observation",
            "body": "gated-robust-observation",
            "margins": "normalized-observation-only",
            "crossZoneReadingOrder": "never-inferred-from-x-y-position-alone",
        },
        "documentFurnitureProfile": serial_profile,
        "summary": {
            "pageCount": len(out),
            "zoneRelationshipCounts": dict(sorted(counts.items())),
            "observedBodyPageCount": sum(1 for p in out if (p.get("bodyEvidence") or {}).get("status") == "observed"),
            "blockedBodyPageCount": sum(1 for p in out if (p.get("bodyEvidence") or {}).get("status") != "observed"),
            "wordDecisionCount": 0,
        },
        "pages": out,
    }


__all__ = ["build_canonical_page_evidence"]
