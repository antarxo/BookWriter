from __future__ import annotations

import re
from collections import Counter
from statistics import median
from typing import Any


VERSION = "canonical-page-evidence-0.2"


# Renderer-neutral page evidence. This module deliberately does not mutate the
# production page_structure and never emits Word constructs. It reconstructs the
# useful logic of the older Mathpix geometry/margin experiments as observations,
# document profiles and explicit unresolved states.

_EXCLUDED_BODY_TYPES = {
    "page_info", "column", "table_row", "table_column",
    "table_of_contents_container", "table_of_contents_row",
    "table_of_contents_number",
}


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
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    ]


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    vals = sorted(float(v) for v in values)
    idx = min(len(vals) - 1, max(0, round((len(vals) - 1) * fraction)))
    return vals[idx]


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
            "y0Ratio": box[1] / height,
            "y1Ratio": box[3] / height,
        })
    return rows


def _body_objects(page: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for obj in page.get("objects", []) or []:
        typ = str(obj.get("type") or "")
        if typ in _EXCLUDED_BODY_TYPES:
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


def _raw_body_observation(page: dict[str, Any]) -> dict[str, Any]:
    width = float(page.get("page_width_px") or 0)
    height = float(page.get("page_height_px") or 0)
    rows = _body_objects(page)
    if width <= 0 or height <= 0 or not rows:
        return {"status": "unavailable"}
    raw = _union([row["bboxPx"] for row in rows])
    if raw is None:
        return {"status": "unavailable"}
    return {
        "status": "observed",
        "bboxPx": raw,
        "objectCount": len(rows),
        "startRatio": raw[1] / height,
        "endRatio": raw[3] / height,
        "leftRatio": raw[0] / width,
        "rightRatio": raw[2] / width,
        "rawBottomWhitespaceRatio": max(0.0, height - raw[3]) / height,
        "verticalOccupancyRatio": max(0.0, raw[3] - raw[1]) / height,
    }


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


def _classify_visible_furniture(page: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
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
        return {
            "bboxPx": box,
            "objectIds": [r.get("id") for r in accepted],
            "confidence": "high" if any(r.get("confidence") == "high" for r in accepted) else "medium",
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

    return {
        "header": header,
        "footer": footer,
        "headerStatus": status("top", header),
        "footerStatus": status("bottom", footer),
        "middlePageInfo": buckets["middle"],
        "topCandidates": buckets["top"],
        "bottomCandidates": buckets["bottom"],
        "policy": "visible furniture requires edge position plus recurrence or a stable document band; missing page_info never proves absence",
    }


def _reserved_zone_profile(
    pages: list[dict[str, Any]],
    visible_by_page: dict[int, dict[str, Any]],
    raw_body_by_page: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    header_ends: list[float] = []
    footer_starts: list[float] = []
    body_starts: list[float] = []
    body_ends: list[float] = []
    for page in pages:
        page_no = int(page.get("page") or 0)
        height = float(page.get("page_height_px") or 0)
        if height <= 0:
            continue
        furniture = visible_by_page.get(page_no) or {}
        header = furniture.get("header") or {}
        footer = furniture.get("footer") or {}
        raw = raw_body_by_page.get(page_no) or {}
        if furniture.get("headerStatus") == "present-high" and header.get("bboxPx"):
            header_ends.append(float(header["bboxPx"][3]) / height)
        if furniture.get("footerStatus") == "present-high" and footer.get("bboxPx"):
            footer_starts.append(float(footer["bboxPx"][1]) / height)
        if raw.get("status") == "observed":
            body_starts.append(float(raw["startRatio"]))
            body_ends.append(float(raw["endRatio"]))
    return {
        "headerReservedEndRatio": median(header_ends) if header_ends else None,
        "footerReservedStartRatio": median(footer_starts) if footer_starts else None,
        "typicalRawBodyStartRatio": median(body_starts) if body_starts else None,
        "typicalRawBodyEndRatio": median(body_ends) if body_ends else None,
        "sourceCounts": {
            "highHeaderPages": len(header_ends),
            "highFooterPages": len(footer_starts),
            "rawBodyPages": len(body_starts),
        },
        "policy": "visible furniture may be absent while its reserved document/page-family zone remains active",
    }


def _resolve_reserved_zones(
    page: dict[str, Any],
    visible: dict[str, Any],
    raw_body: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    header_status = str(visible.get("headerStatus") or "")
    footer_status = str(visible.get("footerStatus") or "")
    raw_start = raw_body.get("startRatio") if raw_body.get("status") == "observed" else None
    raw_end = raw_body.get("endRatio") if raw_body.get("status") == "observed" else None
    typical_start = profile.get("typicalRawBodyStartRatio")
    typical_end = profile.get("typicalRawBodyEndRatio")

    header_zone = "visible-furniture" if header_status.startswith("present-") else "unresolved"
    footer_zone = "visible-furniture" if footer_status.startswith("present-") else "unresolved"

    # Equivalent to the old generous ~18pt tolerance, expressed proportionally
    # so this observation pass does not require PDF-point scaling.
    tolerance = 0.025
    if not header_status.startswith("present-") and raw_start is not None and typical_start is not None:
        if float(raw_start) >= float(typical_start) - tolerance:
            header_zone = "absent-by-layout"
    if not footer_status.startswith("present-") and raw_end is not None and typical_end is not None:
        if float(raw_end) <= float(typical_end) + tolerance:
            footer_zone = "absent-by-layout"

    return {
        "headerObjectStatus": header_status or None,
        "footerObjectStatus": footer_status or None,
        "headerReservedZoneStatus": header_zone,
        "footerReservedZoneStatus": footer_zone,
        "bodyInferencePermission": "allowed" if header_zone != "unresolved" and footer_zone != "unresolved" else "blocked",
        "policy": "absence of a visible object is distinguished from absence of the reserved page-furniture zone",
    }


def _infer_robust_body(page: dict[str, Any], visible: dict[str, Any], reserved: dict[str, Any]) -> dict[str, Any]:
    width = float(page.get("page_width_px") or 0)
    height = float(page.get("page_height_px") or 0)
    if width <= 0 or height <= 0:
        return {"status": "blocked", "reason": "missing-page-dimensions"}
    if reserved.get("bodyInferencePermission") != "allowed":
        return {"status": "blocked", "reason": "reserved-page-zones-unresolved"}

    header_box = (visible.get("header") or {}).get("bboxPx")
    footer_box = (visible.get("footer") or {}).get("bboxPx")
    header_end = float(header_box[3]) if header_box else float((0.0 if profile_none(reserved) else 0.0))
    footer_start = float(footer_box[1]) if footer_box else height
    rows = [
        r for r in _body_objects(page)
        if r["bboxPx"][3] > header_end and r["bboxPx"][1] < footer_start
    ]
    if not rows:
        return {"status": "blocked", "reason": "no-body-objects-after-furniture-exclusion"}

    x0 = _quantile([r["bboxPx"][0] for r in rows], 0.08)
    y0 = _quantile([r["bboxPx"][1] for r in rows], 0.04)
    x1 = _quantile([r["bboxPx"][2] for r in rows], 0.92)
    y1 = _quantile([r["bboxPx"][3] for r in rows], 0.96)
    if None in {x0, y0, x1, y1}:
        return {"status": "blocked", "reason": "insufficient-body-envelope"}
    robust = [float(x0), max(float(y0), header_end), float(x1), min(float(y1), footer_start)]
    if robust[2] <= robust[0] or robust[3] <= robust[1]:
        return {"status": "blocked", "reason": "invalid-robust-body-envelope"}
    raw = _union([r["bboxPx"] for r in rows])
    return {
        "status": "observed",
        "bboxPx": robust,
        "rawEnvelopePx": raw,
        "objectCount": len(rows),
        "normalizedMargins": {
            "left": robust[0] / width,
            "right": (width - robust[2]) / width,
            "top": robust[1] / height,
            "bottom": (height - robust[3]) / height,
        },
        "rawBottomWhitespaceRatio": (height - raw[3]) / height if raw else None,
        "confidence": "high" if len(rows) >= 8 else "medium",
        "source": "mathpix-lines-robust-body-observation",
    }


def profile_none(_reserved: dict[str, Any]) -> None:
    # Kept as a named sentinel helper to make it explicit that no learned
    # reserved coordinate is silently substituted into a page observation.
    return None


def _furniture_inside_body_validation(
    body: dict[str, Any], visible: dict[str, Any], page: dict[str, Any]
) -> dict[str, Any]:
    box = body.get("bboxPx") if body.get("status") == "observed" else None
    if not box:
        return {"status": "unavailable", "valid": False}
    height = float(page.get("page_height_px") or 0)
    tolerance = max(2.0, height * 0.0025)
    header_box = (visible.get("header") or {}).get("bboxPx")
    footer_box = (visible.get("footer") or {}).get("bboxPx")
    header_ok = not header_box or float(header_box[3]) <= float(box[1]) + tolerance
    footer_ok = not footer_box or float(footer_box[1]) >= float(box[3]) - tolerance
    return {
        "status": "checked",
        "headerInsideTopMargin": header_ok,
        "footerInsideBottomMargin": footer_ok,
        "valid": header_ok and footer_ok,
        "policy": "header/footer are witnesses inside full margins; they are never additive to margin size",
    }


def _build_margin_profile(page_rows: list[dict[str, Any]]) -> dict[str, Any]:
    trusted = []
    for row in page_rows:
        body = row.get("bodyEvidence") or {}
        reserved = row.get("reservedZoneEvidence") or {}
        validation = row.get("furnitureValidation") or {}
        if (
            body.get("status") == "observed"
            and body.get("confidence") == "high"
            and int(body.get("objectCount") or 0) >= 8
            and reserved.get("headerReservedZoneStatus") != "unresolved"
            and reserved.get("footerReservedZoneStatus") != "unresolved"
            and validation.get("valid")
        ):
            trusted.append(row)
    profile = {}
    for side in ("left", "right", "top", "bottom"):
        values = [float((row["bodyEvidence"].get("normalizedMargins") or {})[side]) for row in trusted]
        profile[side] = median(values) if values else None
    profile["sourcePageCount"] = len(trusted)
    profile["sourcePages"] = [int(row.get("page") or 0) for row in trusted]
    profile["policy"] = "median normalized margins from trusted pages only; sparse/special pages cannot redefine the document/page-family frame"
    return profile


def _resolve_margin_evidence(row: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    body = row.get("bodyEvidence") or {}
    reserved = row.get("reservedZoneEvidence") or {}
    validation = row.get("furnitureValidation") or {}
    trusted = (
        body.get("status") == "observed"
        and body.get("confidence") == "high"
        and int(body.get("objectCount") or 0) >= 8
        and reserved.get("headerReservedZoneStatus") != "unresolved"
        and reserved.get("footerReservedZoneStatus") != "unresolved"
        and validation.get("valid")
    )
    if trusted:
        return {
            "status": "resolved-observation",
            "source": "page-evidence",
            "normalizedMargins": dict(body.get("normalizedMargins") or {}),
            "confidence": "high",
            "wordRealization": None,
        }
    if all(profile.get(side) is not None for side in ("left", "right", "top", "bottom")):
        return {
            "status": "resolved-observation",
            "source": "document-profile",
            "normalizedMargins": {side: float(profile[side]) for side in ("left", "right", "top", "bottom")},
            "confidence": "medium",
            "wordRealization": None,
            "reason": "page-specific margin evidence is unsafe; stable document/page-family profile retained",
        }
    return {
        "status": "unresolved",
        "source": None,
        "normalizedMargins": None,
        "confidence": "none",
        "wordRealization": None,
        "reason": "no-trusted-document-margin-profile",
    }


def _zone_rows(page: dict[str, Any], body: dict[str, Any]) -> list[dict[str, Any]]:
    width = float(page.get("page_width_px") or 0)
    height = float(page.get("page_height_px") or 0)
    body_box = body.get("bboxPx") if body.get("status") == "observed" else None
    body_height = (body_box[3] - body_box[1]) if body_box else height
    by_id = {str(o.get("id")): o for o in page.get("objects", []) or [] if o.get("id")}
    rows = []
    for obj in page.get("objects", []) or []:
        if str(obj.get("type") or "") != "column":
            continue
        box = _box_px(obj)
        if box is None or width <= 0 or height <= 0:
            continue
        child_ids = list(obj.get("children_ids") or [])
        child_types = Counter(str((by_id.get(str(cid)) or {}).get("type") or "unknown") for cid in child_ids)
        rows.append({
            "zoneId": str(obj.get("id") or ""),
            "bboxPx": box,
            "widthRatio": (box[2] - box[0]) / width,
            "heightRatioToBody": (box[3] - box[1]) / max(1.0, body_height),
            "x0Ratio": box[0] / width,
            "x1Ratio": box[2] / width,
            "parentId": obj.get("parent_id"),
            "childrenIds": child_ids,
            "childTypeCounts": dict(sorted(child_types.items())),
            "source": "mathpix-lines-column-envelope",
        })
    return sorted(rows, key=lambda r: (r["bboxPx"][0], r["bboxPx"][1]))


def _classify_zone_relationship(page: dict[str, Any], body: dict[str, Any], zones: list[dict[str, Any]]) -> dict[str, Any]:
    width = float(page.get("page_width_px") or 0)
    if not zones:
        return {"classification": "no-explicit-zones", "confidence": "low", "rendererMeaning": "unresolved"}
    long_lived = [z for z in zones if z["heightRatioToBody"] >= 0.45 and z["widthRatio"] >= 0.16]
    if len(long_lived) == 2:
        left, right = long_lived
        gap_ratio = max(0.0, right["bboxPx"][0] - left["bboxPx"][2]) / max(1.0, width)
        width_similarity = min(left["widthRatio"], right["widthRatio"]) / max(left["widthRatio"], right["widthRatio"])
        if width_similarity >= 0.72 and gap_ratio <= 0.12:
            classification, confidence = "balanced-parallel-zones", "high"
        elif min(left["widthRatio"], right["widthRatio"]) <= 0.28 and max(left["widthRatio"], right["widthRatio"]) >= 0.48:
            classification, confidence = "asymmetric-parallel-zones", "high"
        else:
            classification, confidence = "ambiguous-parallel-zones", "medium"
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
        return {"classification": "single-long-lived-zone", "confidence": "medium", "zoneIds": [long_lived[0]["zoneId"]], "rendererMeaning": "unresolved"}
    if len(long_lived) > 2:
        return {"classification": "multiple-long-lived-zones", "confidence": "medium", "zoneIds": [z["zoneId"] for z in long_lived], "rendererMeaning": "unresolved"}
    return {"classification": "local-zones-only", "confidence": "medium", "zoneIds": [z["zoneId"] for z in zones], "rendererMeaning": "unresolved"}


def build_canonical_page_evidence(line_map: dict[str, Any]) -> dict[str, Any]:
    pages = list(line_map.get("pages", []) or [])
    furniture_profile = _document_furniture_profile(pages)
    visible_by_page = {int(p.get("page") or 0): _classify_visible_furniture(p, furniture_profile) for p in pages}
    raw_body_by_page = {int(p.get("page") or 0): _raw_body_observation(p) for p in pages}
    reserved_profile = _reserved_zone_profile(pages, visible_by_page, raw_body_by_page)

    first_pass: list[dict[str, Any]] = []
    for page in pages:
        page_no = int(page.get("page") or 0)
        visible = visible_by_page.get(page_no) or {}
        raw = raw_body_by_page.get(page_no) or {}
        reserved = _resolve_reserved_zones(page, visible, raw, reserved_profile)
        body = _infer_robust_body(page, visible, reserved)
        validation = _furniture_inside_body_validation(body, visible, page)
        first_pass.append({
            "page": page_no,
            "pageSizePx": [float(page.get("page_width_px") or 0), float(page.get("page_height_px") or 0)],
            "visibleFurnitureEvidence": visible,
            "reservedZoneEvidence": reserved,
            "rawBodyEvidence": raw,
            "bodyEvidence": body,
            "furnitureValidation": validation,
        })

    margin_profile = _build_margin_profile(first_pass)
    page_by_no = {int(page.get("page") or 0): page for page in pages}
    out: list[dict[str, Any]] = []
    counts = Counter()
    for row in first_pass:
        page_no = int(row.get("page") or 0)
        page = page_by_no.get(page_no) or {}
        margin = _resolve_margin_evidence(row, margin_profile)
        zones = _zone_rows(page, row.get("bodyEvidence") or {})
        relation = _classify_zone_relationship(page, row.get("bodyEvidence") or {}, zones)
        counts[relation.get("classification") or "unknown"] += 1
        conflicts = []
        if (row.get("furnitureValidation") or {}).get("status") == "checked" and not (row.get("furnitureValidation") or {}).get("valid"):
            conflicts.append({
                "attribute": "page-frame-vs-visible-furniture",
                "status": "explicit-conflict",
                "policy": "do not silently repair body/margins from contradictory furniture evidence",
            })
        out.append({
            **row,
            "marginEvidence": margin,
            "zones": zones,
            "zoneRelationship": relation,
            "conflicts": conflicts,
            "crossZoneReadingOrder": {
                "status": "unresolved" if len(zones) > 1 else "not-applicable",
                "source": None,
                "policy": "no global reading order is inferred from geometric zone position alone",
            },
            "wordRealization": None,
        })

    dense_values = [
        float((p.get("rawBodyEvidence") or {}).get("rawBottomWhitespaceRatio"))
        for p in out if (p.get("rawBodyEvidence") or {}).get("rawBottomWhitespaceRatio") is not None
    ]
    fullness_profile = {
        "rawBottomWhitespaceRatio": {
            "min": min(dense_values) if dense_values else None,
            "p10": _quantile(dense_values, 0.10),
            "p25": _quantile(dense_values, 0.25),
            "median": _quantile(dense_values, 0.50),
        },
        "policy": "fullest pages constrain the bottom frame; short pages may increase whitespace but cannot establish a larger bottom margin",
    }

    serial_furniture_profile = {k: v for k, v in furniture_profile.items() if not k.startswith("_")}
    return {
        "version": VERSION,
        "status": "renderer-neutral-observation-only",
        "policy": {
            "mutatesPageStructure": False,
            "wordDecisions": "forbidden",
            "zoneNames": "geometric-only-no-main-sidebar-column-labels",
            "visibleFurniture": "recurrence-plus-edge-band-observation",
            "reservedZones": "document-profile-can-distinguish-suppressed-visible-furniture-from-missing-reserved-zone",
            "body": "robust-observation-after-reserved-zone-resolution",
            "margins": "trusted-page-observation-or-document-profile-never-direct-Word-override",
            "bottomMargin": "full-page-density-evidence-retained-separately",
            "crossZoneReadingOrder": "never-inferred-from-x-y-position-alone",
        },
        "documentFurnitureProfile": serial_furniture_profile,
        "documentReservedZoneProfile": reserved_profile,
        "documentMarginProfile": margin_profile,
        "documentFullnessProfile": fullness_profile,
        "summary": {
            "pageCount": len(out),
            "zoneRelationshipCounts": dict(sorted(counts.items())),
            "observedBodyPageCount": sum(1 for p in out if (p.get("bodyEvidence") or {}).get("status") == "observed"),
            "blockedBodyPageCount": sum(1 for p in out if (p.get("bodyEvidence") or {}).get("status") != "observed"),
            "pageMarginEvidenceCount": sum(1 for p in out if (p.get("marginEvidence") or {}).get("source") == "page-evidence"),
            "profileMarginEvidenceCount": sum(1 for p in out if (p.get("marginEvidence") or {}).get("source") == "document-profile"),
            "unresolvedMarginCount": sum(1 for p in out if (p.get("marginEvidence") or {}).get("status") == "unresolved"),
            "conflictCount": sum(len(p.get("conflicts") or []) for p in out),
            "wordDecisionCount": 0,
        },
        "pages": out,
    }


__all__ = ["build_canonical_page_evidence"]
