from __future__ import annotations

import re
from collections import Counter, defaultdict
from statistics import median
from typing import Any


VERSION = "canonical-page-evidence-0.4"

_EXCLUDED_BODY_TYPES = {
    "page_info",
    "column",
    "table_row",
    "table_column",
    "table_of_contents_container",
    "table_of_contents_row",
    "table_of_contents_number",
}


def _box_px(obj: dict[str, Any]) -> list[float] | None:
    src = obj.get("bbox_px") if isinstance(obj.get("bbox_px"), dict) else None
    if not src:
        return None
    try:
        x0 = float(src.get("x0"))
        y0 = float(src.get("y0"))
        x1 = float(src.get("x1"))
        y1 = float(src.get("y1"))
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    vals = sorted(float(value) for value in values)
    index = min(len(vals) - 1, max(0, round((len(vals) - 1) * fraction)))
    return vals[index]


def _norm_text(obj: dict[str, Any]) -> str:
    value = obj.get("text_display") or obj.get("text") or obj.get("conversion_output") or ""
    return " ".join(str(value).split()).casefold()


def _repeat_signature(text: str) -> str:
    return re.sub(r"\b\d+\b", "#", " ".join(str(text or "").split()).casefold())


def _page_dimensions(page: dict[str, Any]) -> tuple[float, float]:
    try:
        return float(page.get("page_width_px") or 0), float(page.get("page_height_px") or 0)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _frame_family_key(page: dict[str, Any]) -> str:
    width, height = _page_dimensions(page)
    if width <= 0 or height <= 0:
        return "unknown"
    orientation = "landscape" if width > height else "portrait"
    aspect = round(width / height, 3)
    return f"{orientation}:{aspect:.3f}"


def _pages_by_family(pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        grouped[_frame_family_key(page)].append(page)
    return dict(grouped)


def _page_info_records(page: dict[str, Any]) -> list[dict[str, Any]]:
    width, height = _page_dimensions(page)
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
            "y0Ratio": box[1] / height,
            "y1Ratio": box[3] / height,
            "yCenterRatio": ((box[1] + box[3]) / 2.0) / height,
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
    width, height = _page_dimensions(page)
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
        "source": "mathpix-lines-raw-body-envelope",
    }


def _document_furniture_profile(pages: list[dict[str, Any]]) -> dict[str, Any]:
    family_data: dict[str, dict[str, Any]] = {}
    for family, family_pages in _pages_by_family(pages).items():
        signatures = {"top": Counter(), "bottom": Counter()}
        centers = {"top": [], "bottom": []}
        for page in family_pages:
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
        family_data[family] = {
            "pageCount": len(family_pages),
            "topSignatureCounts": dict(signatures["top"]),
            "bottomSignatureCounts": dict(signatures["bottom"]),
            "topMedianCenterRatio": median(centers["top"]) if centers["top"] else None,
            "bottomMedianCenterRatio": median(centers["bottom"]) if centers["bottom"] else None,
            "_signatureCounters": signatures,
        }
    return {
        "families": family_data,
        "familyPolicy": (
            "provisional physical frame families use only page orientation/aspect ratio; "
            "semantic or Word section identity is intentionally not inferred here"
        ),
    }


def _classify_visible_furniture(page: dict[str, Any], document_profile: dict[str, Any]) -> dict[str, Any]:
    family = _frame_family_key(page)
    profile = (document_profile.get("families") or {}).get(family) or {}
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
        accepted = [row for row in buckets[side] if row.get("accepted")]
        if not accepted:
            return None
        box = _union([row["bboxPx"] for row in accepted])
        if box is None:
            return None
        return {
            "bboxPx": box,
            "objectIds": [row.get("id") for row in accepted],
            "confidence": "high" if any(row.get("confidence") == "high" for row in accepted) else "medium",
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
        "frameFamily": family,
        "header": header,
        "footer": footer,
        "headerStatus": status("top", header),
        "footerStatus": status("bottom", footer),
        "middlePageInfo": buckets["middle"],
        "topCandidates": buckets["top"],
        "bottomCandidates": buckets["bottom"],
        "policy": (
            "visible furniture requires edge position plus recurrence or stable family band; "
            "missing page_info never proves absence"
        ),
    }


def _seed_body_from_visible_furniture(
    page: dict[str, Any],
    visible: dict[str, Any],
) -> dict[str, Any]:
    width, height = _page_dimensions(page)
    if width <= 0 or height <= 0:
        return {"status": "blocked", "reason": "missing-page-dimensions"}
    if not str(visible.get("headerStatus") or "").startswith("present-"):
        return {"status": "blocked", "reason": "header-not-visibly-resolved"}
    if not str(visible.get("footerStatus") or "").startswith("present-"):
        return {"status": "blocked", "reason": "footer-not-visibly-resolved"}

    header_box = (visible.get("header") or {}).get("bboxPx")
    footer_box = (visible.get("footer") or {}).get("bboxPx")
    if not header_box or not footer_box:
        return {"status": "blocked", "reason": "visible-furniture-bounds-missing"}
    header_end = float(header_box[3])
    footer_start = float(footer_box[1])
    if footer_start <= header_end:
        return {"status": "blocked", "reason": "invalid-visible-furniture-bounds"}

    rows = [
        row
        for row in _body_objects(page)
        if row["bboxPx"][3] > header_end and row["bboxPx"][1] < footer_start
    ]
    if not rows:
        return {"status": "blocked", "reason": "no-body-objects-after-visible-furniture-exclusion"}

    x0 = _quantile([row["bboxPx"][0] for row in rows], 0.08)
    y0 = _quantile([row["bboxPx"][1] for row in rows], 0.04)
    x1 = _quantile([row["bboxPx"][2] for row in rows], 0.92)
    y1 = _quantile([row["bboxPx"][3] for row in rows], 0.96)
    if None in {x0, y0, x1, y1}:
        return {"status": "blocked", "reason": "insufficient-seed-body-envelope"}
    bbox = [
        float(x0),
        max(float(y0), header_end),
        float(x1),
        min(float(y1), footer_start),
    ]
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return {"status": "blocked", "reason": "invalid-seed-body-envelope"}

    return {
        "status": "observed",
        "bboxPx": bbox,
        "objectCount": len(rows),
        "confidence": "high" if len(rows) >= 8 else "medium",
        "source": "visible-furniture-seed-body",
    }


def _reserved_zone_profiles(
    pages: list[dict[str, Any]],
    visible_by_page: dict[int, dict[str, Any]],
    seed_body_by_page: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {"families": {}}
    for family, family_pages in _pages_by_family(pages).items():
        header_ends: list[float] = []
        footer_starts: list[float] = []
        body_starts: list[float] = []
        body_ends: list[float] = []
        seed_pages: list[int] = []
        for page in family_pages:
            page_no = int(page.get("page") or 0)
            _width, height = _page_dimensions(page)
            if height <= 0:
                continue
            visible = visible_by_page.get(page_no) or {}
            seed = seed_body_by_page.get(page_no) or {}
            header = visible.get("header") or {}
            footer = visible.get("footer") or {}
            if visible.get("headerStatus") == "present-high" and header.get("bboxPx"):
                header_ends.append(float(header["bboxPx"][3]) / height)
            if visible.get("footerStatus") == "present-high" and footer.get("bboxPx"):
                footer_starts.append(float(footer["bboxPx"][1]) / height)
            if seed.get("status") == "observed" and seed.get("bboxPx"):
                body_starts.append(float(seed["bboxPx"][1]) / height)
                body_ends.append(float(seed["bboxPx"][3]) / height)
                seed_pages.append(page_no)
        result["families"][family] = {
            "pageCount": len(family_pages),
            "headerReservedEndRatio": median(header_ends) if header_ends else None,
            "footerReservedStartRatio": median(footer_starts) if footer_starts else None,
            "typicalSeedBodyStartRatio": median(body_starts) if body_starts else None,
            "typicalSeedBodyEndRatio": median(body_ends) if body_ends else None,
            "seedBodyPages": seed_pages,
            "sourceCounts": {
                "highHeaderPages": len(header_ends),
                "highFooterPages": len(footer_starts),
                "seedBodyPages": len(seed_pages),
            },
        }
    result["policy"] = (
        "reserved-zone profiles learn body start/end only from pages whose visible "
        "header and footer were already resolved; special/unresolved pages cannot train the profile"
    )
    return result


def _resolve_reserved_zones(
    page: dict[str, Any],
    visible: dict[str, Any],
    raw_body: dict[str, Any],
    reserved_profiles: dict[str, Any],
) -> dict[str, Any]:
    family = _frame_family_key(page)
    profile = (reserved_profiles.get("families") or {}).get(family) or {}
    header_status = str(visible.get("headerStatus") or "")
    footer_status = str(visible.get("footerStatus") or "")
    raw_start = raw_body.get("startRatio") if raw_body.get("status") == "observed" else None
    raw_end = raw_body.get("endRatio") if raw_body.get("status") == "observed" else None
    typical_start = profile.get("typicalSeedBodyStartRatio")
    typical_end = profile.get("typicalSeedBodyEndRatio")

    header_zone = "visible-furniture" if header_status.startswith("present-") else "unresolved"
    footer_zone = "visible-furniture" if footer_status.startswith("present-") else "unresolved"
    tolerance = 0.025

    if not header_status.startswith("present-") and raw_start is not None and typical_start is not None:
        if float(raw_start) >= float(typical_start) - tolerance:
            header_zone = "absent-by-layout"
    if not footer_status.startswith("present-") and raw_end is not None and typical_end is not None:
        if float(raw_end) <= float(typical_end) + tolerance:
            footer_zone = "absent-by-layout"

    return {
        "frameFamily": family,
        "headerObjectStatus": header_status or None,
        "footerObjectStatus": footer_status or None,
        "headerReservedZoneStatus": header_zone,
        "footerReservedZoneStatus": footer_zone,
        "learnedHeaderReservedEndRatio": profile.get("headerReservedEndRatio"),
        "learnedFooterReservedStartRatio": profile.get("footerReservedStartRatio"),
        "bodyInferencePermission": (
            "allowed" if header_zone != "unresolved" and footer_zone != "unresolved" else "blocked"
        ),
        "policy": (
            "absence of a visible object is distinct from absence of its reserved zone; "
            "an absent-by-layout page uses the learned family boundary only as an exclusion boundary"
        ),
    }


def _effective_vertical_boundaries(
    page: dict[str, Any],
    visible: dict[str, Any],
    reserved: dict[str, Any],
) -> tuple[float, float] | None:
    _width, height = _page_dimensions(page)
    if height <= 0:
        return None
    header_box = (visible.get("header") or {}).get("bboxPx")
    footer_box = (visible.get("footer") or {}).get("bboxPx")

    if header_box:
        header_end = float(header_box[3])
        header_source = "visible-header"
    elif reserved.get("headerReservedZoneStatus") == "absent-by-layout" and reserved.get("learnedHeaderReservedEndRatio") is not None:
        header_end = float(reserved["learnedHeaderReservedEndRatio"]) * height
        header_source = "learned-reserved-header"
    else:
        return None

    if footer_box:
        footer_start = float(footer_box[1])
        footer_source = "visible-footer"
    elif reserved.get("footerReservedZoneStatus") == "absent-by-layout" and reserved.get("learnedFooterReservedStartRatio") is not None:
        footer_start = float(reserved["learnedFooterReservedStartRatio"]) * height
        footer_source = "learned-reserved-footer"
    else:
        return None

    if footer_start <= header_end:
        return None
    reserved["effectiveHeaderBoundarySource"] = header_source
    reserved["effectiveFooterBoundarySource"] = footer_source
    return header_end, footer_start


def _infer_robust_body(
    page: dict[str, Any],
    visible: dict[str, Any],
    reserved: dict[str, Any],
) -> dict[str, Any]:
    width, height = _page_dimensions(page)
    if width <= 0 or height <= 0:
        return {"status": "blocked", "reason": "missing-page-dimensions"}
    if reserved.get("bodyInferencePermission") != "allowed":
        return {"status": "blocked", "reason": "reserved-page-zones-unresolved"}

    boundaries = _effective_vertical_boundaries(page, visible, reserved)
    if boundaries is None:
        return {"status": "blocked", "reason": "reserved-boundary-coordinate-unavailable"}
    header_end, footer_start = boundaries

    rows = [
        row
        for row in _body_objects(page)
        if row["bboxPx"][3] > header_end and row["bboxPx"][1] < footer_start
    ]
    if not rows:
        return {"status": "blocked", "reason": "no-body-objects-after-furniture-exclusion"}

    x0 = _quantile([row["bboxPx"][0] for row in rows], 0.08)
    y0 = _quantile([row["bboxPx"][1] for row in rows], 0.04)
    x1 = _quantile([row["bboxPx"][2] for row in rows], 0.92)
    y1 = _quantile([row["bboxPx"][3] for row in rows], 0.96)
    if None in {x0, y0, x1, y1}:
        return {"status": "blocked", "reason": "insufficient-body-envelope"}

    robust = [
        float(x0),
        max(float(y0), header_end),
        float(x1),
        min(float(y1), footer_start),
    ]
    if robust[2] <= robust[0] or robust[3] <= robust[1]:
        return {"status": "blocked", "reason": "invalid-robust-body-envelope"}

    raw = _union([row["bboxPx"] for row in rows])
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
        "policy": (
            "robust body is a page observation constrained by visible or learned reserved-zone boundaries; "
            "it is not itself an inherited page frame"
        ),
    }


def _furniture_inside_body_validation(
    body: dict[str, Any],
    visible: dict[str, Any],
    page: dict[str, Any],
) -> dict[str, Any]:
    box = body.get("bboxPx") if body.get("status") == "observed" else None
    if not box:
        return {"status": "unavailable", "valid": False}
    _width, height = _page_dimensions(page)
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
        "policy": "visible header/footer must lie inside full margins; they never add to margin size",
    }


def _trusted_page_row(row: dict[str, Any]) -> bool:
    body = row.get("bodyEvidence") or {}
    reserved = row.get("reservedZoneEvidence") or {}
    validation = row.get("furnitureValidation") or {}
    return bool(
        body.get("status") == "observed"
        and body.get("confidence") == "high"
        and int(body.get("objectCount") or 0) >= 8
        and reserved.get("headerReservedZoneStatus") != "unresolved"
        and reserved.get("footerReservedZoneStatus") != "unresolved"
        and validation.get("valid")
    )


def _build_family_margin_profiles(page_rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in page_rows:
        if _trusted_page_row(row):
            grouped[str(row.get("frameFamily") or "unknown")].append(row)

    families: dict[str, Any] = {}
    for family, trusted in grouped.items():
        median_margins: dict[str, float | None] = {}
        for side in ("left", "right", "top", "bottom"):
            values = [
                float((row.get("bodyEvidence") or {}).get("normalizedMargins", {}).get(side))
                for row in trusted
                if (row.get("bodyEvidence") or {}).get("normalizedMargins", {}).get(side) is not None
            ]
            median_margins[side] = median(values) if values else None

        raw_bottom_rows = [
            row for row in trusted
            if (row.get("rawBodyEvidence") or {}).get("rawBottomWhitespaceRatio") is not None
        ]
        raw_bottom_values = [
            float((row.get("rawBodyEvidence") or {}).get("rawBottomWhitespaceRatio"))
            for row in raw_bottom_rows
        ]
        dense_threshold = _quantile(raw_bottom_values, 0.25)
        dense_rows = [
            row for row in raw_bottom_rows
            if dense_threshold is not None
            and float((row.get("rawBodyEvidence") or {}).get("rawBottomWhitespaceRatio")) <= dense_threshold
        ]
        dense_robust_bottom = [
            float((row.get("bodyEvidence") or {}).get("normalizedMargins", {}).get("bottom"))
            for row in dense_rows
            if (row.get("bodyEvidence") or {}).get("normalizedMargins", {}).get("bottom") is not None
        ]
        families[family] = {
            "trustedSourcePageCount": len(trusted),
            "trustedSourcePages": [int(row.get("page") or 0) for row in trusted],
            "medianNormalizedMargins": median_margins,
            "denseBottomConstraint": {
                "status": "observed" if dense_rows else "unresolved",
                "rawBottomWhitespaceP25": dense_threshold,
                "sourcePages": [int(row.get("page") or 0) for row in dense_rows],
                "medianRobustBottomMargin": median(dense_robust_bottom) if dense_robust_bottom else None,
                "policy": (
                    "fullest trusted pages constrain bottom-frame evidence; "
                    "short or unresolved pages cannot enlarge the inferred bottom frame"
                ),
            },
        }

    return {
        "families": families,
        "policy": (
            "family profiles are built from trusted page observations only; "
            "page-specific observation and inherited family frame remain distinct"
        ),
    }


def _resolve_margin_evidence(row: dict[str, Any], family_profiles: dict[str, Any]) -> dict[str, Any]:
    family = str(row.get("frameFamily") or "unknown")
    profile = (family_profiles.get("families") or {}).get(family) or {}
    if _trusted_page_row(row):
        return {
            "status": "resolved-observation",
            "source": "page-evidence",
            "normalizedMargins": dict((row.get("bodyEvidence") or {}).get("normalizedMargins") or {}),
            "confidence": "high",
            "wordRealization": None,
        }

    median_margins = profile.get("medianNormalizedMargins") or {}
    if all(median_margins.get(side) is not None for side in ("left", "right", "top", "bottom")):
        return {
            "status": "resolved-inherited-frame",
            "source": "frame-family-profile",
            "normalizedMargins": {
                side: float(median_margins[side]) for side in ("left", "right", "top", "bottom")
            },
            "denseBottomConstraint": profile.get("denseBottomConstraint"),
            "confidence": "medium",
            "wordRealization": None,
            "reason": "page-specific frame evidence is unsafe; compatible provisional frame-family profile retained",
        }

    return {
        "status": "unresolved",
        "source": None,
        "normalizedMargins": None,
        "confidence": "none",
        "wordRealization": None,
        "reason": "no-trusted-compatible-frame-family-profile",
    }


def _zone_rows(page: dict[str, Any], body: dict[str, Any]) -> list[dict[str, Any]]:
    width, height = _page_dimensions(page)
    body_box = body.get("bboxPx") if body.get("status") == "observed" else None
    body_height = (body_box[3] - body_box[1]) if body_box else height
    by_id = {
        str(obj.get("id")): obj
        for obj in page.get("objects", []) or []
        if obj.get("id")
    }
    rows: list[dict[str, Any]] = []
    for obj in page.get("objects", []) or []:
        if str(obj.get("type") or "") != "column":
            continue
        box = _box_px(obj)
        if box is None or width <= 0 or height <= 0:
            continue
        child_ids = list(obj.get("children_ids") or [])
        child_types = Counter(
            str((by_id.get(str(child_id)) or {}).get("type") or "unknown")
            for child_id in child_ids
        )
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
    return sorted(rows, key=lambda row: (row["bboxPx"][0], row["bboxPx"][1]))


def _classify_zone_relationship(
    page: dict[str, Any],
    body: dict[str, Any],
    zones: list[dict[str, Any]],
) -> dict[str, Any]:
    width, _height = _page_dimensions(page)
    if not zones:
        return {
            "classification": "no-explicit-zones",
            "confidence": "low",
            "rendererMeaning": "unresolved",
        }

    long_lived = [
        zone
        for zone in zones
        if zone["heightRatioToBody"] >= 0.45 and zone["widthRatio"] >= 0.16
    ]
    if len(long_lived) == 2:
        left, right = long_lived
        gap_ratio = max(0.0, right["bboxPx"][0] - left["bboxPx"][2]) / max(1.0, width)
        width_similarity = min(left["widthRatio"], right["widthRatio"]) / max(
            left["widthRatio"], right["widthRatio"]
        )
        if width_similarity >= 0.72 and gap_ratio <= 0.12:
            classification, confidence = "balanced-parallel-zones", "high"
        elif min(left["widthRatio"], right["widthRatio"]) <= 0.28 and max(
            left["widthRatio"], right["widthRatio"]
        ) >= 0.48:
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
            "policy": (
                "geometric relationship only; balanced does not mean Word columns "
                "and asymmetric does not mean sidebar"
            ),
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
            "zoneIds": [zone["zoneId"] for zone in long_lived],
            "rendererMeaning": "unresolved",
        }
    return {
        "classification": "local-zones-only",
        "confidence": "medium",
        "zoneIds": [zone["zoneId"] for zone in zones],
        "rendererMeaning": "unresolved",
    }


def build_canonical_page_evidence(line_map: dict[str, Any]) -> dict[str, Any]:
    pages = list(line_map.get("pages", []) or [])
    furniture_profile = _document_furniture_profile(pages)
    visible_by_page = {
        int(page.get("page") or 0): _classify_visible_furniture(page, furniture_profile)
        for page in pages
    }
    raw_body_by_page = {
        int(page.get("page") or 0): _raw_body_observation(page)
        for page in pages
    }
    seed_body_by_page = {
        int(page.get("page") or 0): _seed_body_from_visible_furniture(
            page,
            visible_by_page.get(int(page.get("page") or 0)) or {},
        )
        for page in pages
    }
    reserved_profiles = _reserved_zone_profiles(pages, visible_by_page, seed_body_by_page)

    first_pass: list[dict[str, Any]] = []
    for page in pages:
        page_no = int(page.get("page") or 0)
        family = _frame_family_key(page)
        visible = visible_by_page.get(page_no) or {}
        raw = raw_body_by_page.get(page_no) or {}
        reserved = _resolve_reserved_zones(page, visible, raw, reserved_profiles)
        body = _infer_robust_body(page, visible, reserved)
        validation = _furniture_inside_body_validation(body, visible, page)
        first_pass.append({
            "page": page_no,
            "frameFamily": family,
            "pageSizePx": list(_page_dimensions(page)),
            "visibleFurnitureEvidence": visible,
            "reservedZoneEvidence": reserved,
            "rawBodyEvidence": raw,
            "seedBodyEvidence": seed_body_by_page.get(page_no) or {},
            "bodyEvidence": body,
            "furnitureValidation": validation,
        })

    margin_profiles = _build_family_margin_profiles(first_pass)
    page_by_no = {
        int(page.get("page") or 0): page
        for page in pages
    }

    out: list[dict[str, Any]] = []
    relationship_counts: Counter[str] = Counter()
    for row in first_pass:
        page_no = int(row.get("page") or 0)
        page = page_by_no.get(page_no) or {}
        margin = _resolve_margin_evidence(row, margin_profiles)
        zones = _zone_rows(page, row.get("bodyEvidence") or {})
        relationship = _classify_zone_relationship(
            page,
            row.get("bodyEvidence") or {},
            zones,
        )
        relationship_counts[relationship.get("classification") or "unknown"] += 1

        conflicts: list[dict[str, Any]] = []
        validation = row.get("furnitureValidation") or {}
        if validation.get("status") == "checked" and not validation.get("valid"):
            conflicts.append({
                "attribute": "page-frame-vs-visible-furniture",
                "status": "explicit-conflict",
                "policy": "do not silently repair body/frame from contradictory furniture evidence",
            })

        out.append({
            **row,
            "marginEvidence": margin,
            "zones": zones,
            "zoneRelationship": relationship,
            "conflicts": conflicts,
            "crossZoneReadingOrder": {
                "status": "unresolved" if len(zones) > 1 else "not-applicable",
                "source": None,
                "policy": "no global reading order is inferred from geometric zone position alone",
            },
            "wordRealization": None,
        })

    serial_furniture = {
        "families": {
            family: {
                key: value
                for key, value in data.items()
                if not key.startswith("_")
            }
            for family, data in (furniture_profile.get("families") or {}).items()
        },
        "familyPolicy": furniture_profile.get("familyPolicy"),
    }

    return {
        "version": VERSION,
        "status": "renderer-neutral-observation-only",
        "policy": {
            "mutatesPageStructure": False,
            "wordDecisions": "forbidden",
            "frameFamilies": "provisional-page-shape-only-no-semantic-section-claim",
            "visibleFurniture": "recurrence-plus-edge-band-within-frame-family",
            "reservedZones": "seed-visible-pages-learn-family-boundaries-before-special-page-inheritance",
            "bodyObservation": "robust-page-observation-constrained-by-visible-or-reserved-boundaries",
            "frameEvidence": "trusted-page-observation-or-compatible-frame-family-profile",
            "bottomFrame": "dense-bottom-constraint-from-trusted-pages-only",
            "zoneNames": "geometric-only-no-main-sidebar-column-labels",
            "crossZoneReadingOrder": "never-inferred-from-x-y-position-alone",
        },
        "documentFurnitureProfile": serial_furniture,
        "documentReservedZoneProfiles": reserved_profiles,
        "documentFrameProfiles": margin_profiles,
        "summary": {
            "pageCount": len(out),
            "frameFamilyCount": len((margin_profiles.get("families") or {})),
            "zoneRelationshipCounts": dict(sorted(relationship_counts.items())),
            "observedBodyPageCount": sum(
                1 for row in out if (row.get("bodyEvidence") or {}).get("status") == "observed"
            ),
            "blockedBodyPageCount": sum(
                1 for row in out if (row.get("bodyEvidence") or {}).get("status") != "observed"
            ),
            "pageMarginEvidenceCount": sum(
                1 for row in out if (row.get("marginEvidence") or {}).get("source") == "page-evidence"
            ),
            "familyFrameEvidenceCount": sum(
                1 for row in out
                if (row.get("marginEvidence") or {}).get("source") == "frame-family-profile"
            ),
            "unresolvedMarginCount": sum(
                1 for row in out if (row.get("marginEvidence") or {}).get("status") == "unresolved"
            ),
            "conflictCount": sum(len(row.get("conflicts") or []) for row in out),
            "wordDecisionCount": 0,
        },
        "pages": out,
    }


__all__ = ["build_canonical_page_evidence"]
