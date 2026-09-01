from __future__ import annotations

from typing import Any

VERSION = "canonical-page-recovery-0.6"

_EXCLUDED_STRUCTURAL_TYPES = {
    "page_info",
    "column",
    "table_row",
    "table_column",
    "table_of_contents_container",
    "table_of_contents_row",
    "table_of_contents_number",
}


def _page_object_map(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(obj.get("id")): obj
        for obj in page.get("objects", []) or []
        if obj.get("id")
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


def _descendant_content_extent(page: dict[str, Any], zone: dict[str, Any]) -> dict[str, Any]:
    by_id = _page_object_map(page)
    pending = [str(value) for value in (zone.get("childrenIds") or [])]
    seen: set[str] = set()
    boxes: list[list[float]] = []
    content_ids: list[str] = []

    while pending:
        object_id = pending.pop()
        if not object_id or object_id in seen:
            continue
        seen.add(object_id)
        obj = by_id.get(object_id)
        if not obj:
            continue
        pending.extend(str(value) for value in (obj.get("children_ids") or []))
        typ = str(obj.get("type") or "")
        if typ in _EXCLUDED_STRUCTURAL_TYPES:
            continue
        box = _box_px(obj)
        if box:
            boxes.append(box)
            content_ids.append(object_id)

    if not boxes:
        return {
            "status": "unavailable",
            "contentObjectCount": 0,
            "contentObjectIds": [],
            "bboxPx": None,
        }

    return {
        "status": "observed",
        "contentObjectCount": len(boxes),
        "contentObjectIds": content_ids,
        "bboxPx": [
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        ],
    }


def _reconcile_frame_outward(
    inherited_frame: list[float],
    raw_body: dict[str, Any],
    page_width: float,
    page_height: float,
) -> dict[str, Any]:
    """Expand an inherited frame only when direct page content proves it is too small."""
    frame = [float(value) for value in inherited_frame]
    raw_box = raw_body.get("bboxPx") if raw_body.get("status") == "observed" else None
    if not raw_box or len(raw_box) != 4:
        return {
            "status": "unchanged-no-page-content-envelope",
            "inheritedFramePx": frame,
            "reconciledFramePx": frame,
            "rawContentEnvelopePx": raw_box,
            "expandedSides": [],
            "source": "outer-frame-profile-only",
        }

    try:
        rx0, ry0, rx1, ry1 = [float(value) for value in raw_box]
    except (TypeError, ValueError):
        return {
            "status": "unchanged-invalid-page-content-envelope",
            "inheritedFramePx": frame,
            "reconciledFramePx": frame,
            "rawContentEnvelopePx": raw_box,
            "expandedSides": [],
            "source": "outer-frame-profile-only",
        }

    reconciled = [
        max(0.0, min(frame[0], rx0)),
        max(0.0, min(frame[1], ry0)),
        min(page_width, max(frame[2], rx1)),
        min(page_height, max(frame[3], ry1)),
    ]
    expanded: list[str] = []
    if reconciled[0] < frame[0]: expanded.append("left")
    if reconciled[1] < frame[1]: expanded.append("top")
    if reconciled[2] > frame[2]: expanded.append("right")
    if reconciled[3] > frame[3]: expanded.append("bottom")

    return {
        "status": "expanded-by-direct-page-content" if expanded else "unchanged-content-inside-prior",
        "inheritedFramePx": frame,
        "reconciledFramePx": reconciled,
        "rawContentEnvelopePx": [rx0, ry0, rx1, ry1],
        "expandedSides": expanded,
        "source": "outer-frame-prior-plus-direct-raw-content",
        "policy": (
            "family outer frame is a prior, never a clipping boundary; direct page-specific raw content "
            "may expand it outward only and can never shrink it inward"
        ),
    }


def _classify_recovered_zones(
    row: dict[str, Any],
    page: dict[str, Any],
    frame: list[float],
    page_width: float,
) -> dict[str, Any]:
    zones = list(row.get("zones") or [])
    if not zones:
        return {
            "status": "observed",
            "classification": "no-explicit-zones",
            "confidence": "low",
            "rendererMeaning": "unresolved",
            "policy": "recovery frame supplies geometry only; absence of Mathpix zones has no Word meaning",
        }

    frame_height = max(1.0, float(frame[3]) - float(frame[1]))
    normalized: list[dict[str, Any]] = []
    for zone in zones:
        box = zone.get("bboxPx") or []
        if len(box) != 4:
            continue
        try:
            x0, y0, x1, y1 = [float(value) for value in box]
        except (TypeError, ValueError):
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        descendant = _descendant_content_extent(page, zone)
        descendant_box = descendant.get("bboxPx") or []
        if len(descendant_box) == 4:
            dx0, dy0, dx1, dy1 = [float(value) for value in descendant_box]
            descendant_fit = {
                "extendsLeftOfRecoveryFrame": dx0 < float(frame[0]),
                "extendsAboveRecoveryFrame": dy0 < float(frame[1]),
                "extendsRightOfRecoveryFrame": dx1 > float(frame[2]),
                "extendsBelowRecoveryFrame": dy1 > float(frame[3]),
            }
        else:
            descendant_fit = {
                "extendsLeftOfRecoveryFrame": None,
                "extendsAboveRecoveryFrame": None,
                "extendsRightOfRecoveryFrame": None,
                "extendsBelowRecoveryFrame": None,
            }

        normalized.append({
            **zone,
            "recoveryHeightRatioToFrame": (y1 - y0) / frame_height,
            "recoveryWidthRatioToPage": (x1 - x0) / max(1.0, page_width),
            "extendsLeftOfRecoveryFrame": x0 < float(frame[0]),
            "extendsAboveRecoveryFrame": y0 < float(frame[1]),
            "extendsRightOfRecoveryFrame": x1 > float(frame[2]),
            "extendsBelowRecoveryFrame": y1 > float(frame[3]),
            "descendantContentExtent": descendant,
            "descendantContentFrameFit": descendant_fit,
        })

    long_lived = [
        zone
        for zone in normalized
        if zone["recoveryHeightRatioToFrame"] >= 0.45
        and zone["recoveryWidthRatioToPage"] >= 0.16
    ]

    if len(long_lived) == 2:
        left, right = sorted(long_lived, key=lambda zone: float(zone["bboxPx"][0]))
        left_width = float(left["recoveryWidthRatioToPage"])
        right_width = float(right["recoveryWidthRatioToPage"])
        width_similarity = min(left_width, right_width) / max(left_width, right_width)
        gap_ratio = max(0.0, float(right["bboxPx"][0]) - float(left["bboxPx"][2])) / max(1.0, page_width)
        if width_similarity >= 0.72 and gap_ratio <= 0.12:
            classification = "balanced-parallel-zones"
        elif min(left_width, right_width) <= 0.28 and max(left_width, right_width) >= 0.48:
            classification = "asymmetric-parallel-zones"
        else:
            classification = "ambiguous-parallel-zones"
        result: dict[str, Any] = {
            "status": "observed-from-recovered-frame",
            "classification": classification,
            "confidence": "medium",
            "zoneIds": [left.get("zoneId"), right.get("zoneId")],
            "widthSimilarity": round(width_similarity, 4),
            "gapRatio": round(gap_ratio, 4),
        }
    elif len(long_lived) == 1:
        result = {
            "status": "observed-from-recovered-frame",
            "classification": "single-long-lived-zone",
            "confidence": "medium",
            "zoneIds": [long_lived[0].get("zoneId")],
        }
    elif len(long_lived) > 2:
        result = {
            "status": "observed-from-recovered-frame",
            "classification": "multiple-long-lived-zones",
            "confidence": "medium",
            "zoneIds": [zone.get("zoneId") for zone in long_lived],
        }
    else:
        result = {
            "status": "observed-from-recovered-frame",
            "classification": "local-zones-only",
            "confidence": "medium",
            "zoneIds": [zone.get("zoneId") for zone in normalized],
        }

    left_count = sum(1 for zone in normalized if zone.get("extendsLeftOfRecoveryFrame"))
    above_count = sum(1 for zone in normalized if zone.get("extendsAboveRecoveryFrame"))
    right_count = sum(1 for zone in normalized if zone.get("extendsRightOfRecoveryFrame"))
    below_count = sum(1 for zone in normalized if zone.get("extendsBelowRecoveryFrame"))

    descendant_rows = [
        zone for zone in normalized
        if (zone.get("descendantContentExtent") or {}).get("status") == "observed"
    ]
    d_left = sum(1 for zone in descendant_rows if (zone.get("descendantContentFrameFit") or {}).get("extendsLeftOfRecoveryFrame"))
    d_above = sum(1 for zone in descendant_rows if (zone.get("descendantContentFrameFit") or {}).get("extendsAboveRecoveryFrame"))
    d_right = sum(1 for zone in descendant_rows if (zone.get("descendantContentFrameFit") or {}).get("extendsRightOfRecoveryFrame"))
    d_below = sum(1 for zone in descendant_rows if (zone.get("descendantContentFrameFit") or {}).get("extendsBelowRecoveryFrame"))

    result.update({
        "zoneFrameFit": {
            "leftCount": left_count,
            "aboveCount": above_count,
            "rightCount": right_count,
            "belowCount": below_count,
            "allInsideFrame": left_count == 0 and above_count == 0 and right_count == 0 and below_count == 0,
        },
        "descendantContentFrameFit": {
            "observedZoneCount": len(descendant_rows),
            "leftCount": d_left,
            "aboveCount": d_above,
            "rightCount": d_right,
            "belowCount": d_below,
            "allObservedDescendantsInsideFrame": (
                len(descendant_rows) > 0 and d_left == 0 and d_above == 0 and d_right == 0 and d_below == 0
            ),
            "zoneDiagnostics": [
                {
                    "zoneId": zone.get("zoneId"),
                    "zoneBoxPx": zone.get("bboxPx"),
                    "descendantContentExtent": zone.get("descendantContentExtent"),
                    "descendantContentFrameFit": zone.get("descendantContentFrameFit"),
                }
                for zone in normalized
            ],
        },
        "rendererMeaning": "unresolved",
        "crossZoneReadingOrder": "unresolved" if len(normalized) > 1 else "not-applicable",
        "wordRealization": None,
        "policy": (
            "zone relationship is measured against reconciled renderer-neutral frame evidence only; "
            "container fit and descendant-content fit remain distinct, confidence is capped at medium, "
            "and no Word columns/sidebar/reading order are inferred"
        ),
    })
    return result


def recover_blocked_pages(
    report: dict[str, Any],
    line_map: dict[str, Any],
    frame_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach renderer-neutral second-pass recovery evidence.

    Primary body evidence remains untouched. Recovery starts from a dedicated
    outer-frame prior learned from trusted raw extents, then reconciles that prior
    outward with direct raw content evidence from the selected page. Direct page
    evidence may expand the inherited frame but can never shrink it.
    """
    page_map = {
        int(page.get("page") or 0): page
        for page in line_map.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    frame_families = (frame_profile or {}).get("families") or {}
    recovered = 0
    zone_recovered = 0

    for row in report.get("pages", []) or []:
        body = row.get("bodyEvidence") or {}
        recovery: dict[str, Any] = {"status": "not-needed", "wordRealization": None}
        zone_recovery: dict[str, Any] = {"status": "not-needed", "wordRealization": None}

        if body.get("status") != "observed":
            recovery = {"status": "blocked", "wordRealization": None}
            zone_recovery = {"status": "blocked", "wordRealization": None}
            family = str(row.get("frameFamily") or "unknown")
            family_frame = frame_families.get(family) or {}
            eligible = (
                family_frame.get("status") == "resolved"
                and family_frame.get("source") == "trusted-raw-outer-extents"
                and family_frame.get("confidence") == "medium"
                and not (row.get("conflicts") or [])
            )
            page = page_map.get(int(row.get("page") or 0)) or {}
            try:
                width = float(page.get("page_width_px") or 0)
                height = float(page.get("page_height_px") or 0)
                ratios = family_frame.get("frameRatios") or {}
                left = float(ratios["left"])
                top = float(ratios["top"])
                right = float(ratios["right"])
                bottom = float(ratios["bottom"])
            except (KeyError, TypeError, ValueError):
                eligible = False
                width = height = 0.0

            if eligible and width > 0 and height > 0:
                inherited_frame = [left * width, top * height, right * width, bottom * height]
                if inherited_frame[2] > inherited_frame[0] and inherited_frame[3] > inherited_frame[1]:
                    reconciliation = _reconcile_frame_outward(
                        inherited_frame,
                        row.get("rawBodyEvidence") or {},
                        width,
                        height,
                    )
                    frame = list(reconciliation.get("reconciledFramePx") or inherited_frame)
                    recovery = {
                        "status": "recovered-from-reconciled-outer-frame",
                        "confidence": "medium",
                        "bodyConstraintPx": frame,
                        "inheritedOuterFramePx": inherited_frame,
                        "source": "outer-frame-prior-plus-direct-raw-content",
                        "frameReconciliation": reconciliation,
                        "frameProfileVersion": (frame_profile or {}).get("version"),
                        "frameProfileSourcePages": family_frame.get("sourcePages"),
                        "frameRatios": ratios,
                        "headerSemanticStatus": "unresolved",
                        "footerSemanticStatus": "unresolved",
                        "crossZoneReadingOrder": "unresolved",
                        "wordRealization": None,
                        "policy": (
                            "trusted family outer frame is a prior; direct page-specific raw content may expand "
                            "that prior outward only; furniture semantics remain unresolved"
                        ),
                    }
                    zone_recovery = _classify_recovered_zones(row, page, frame, width)
                    recovered += 1
                    if zone_recovery.get("status") == "observed-from-recovered-frame":
                        zone_recovered += 1

        row["profileRecoveryEvidence"] = recovery
        row["recoveryZoneRelationship"] = zone_recovery

    report["profileRecovery"] = {
        "version": VERSION,
        "frameProfileVersion": (frame_profile or {}).get("version"),
        "recoveredPageCount": recovered,
        "zoneRecoveredPageCount": zone_recovered,
        "policy": (
            "second-pass geometry only; family outer frame is a prior reconciled outward with direct raw page content; "
            "primary evidence remains untouched and no page_structure mutation or Word realization is permitted"
        ),
    }
    return report


__all__ = ["recover_blocked_pages"]
