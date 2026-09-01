from __future__ import annotations

from typing import Any

VERSION = "canonical-page-recovery-0.4"


def _classify_recovered_zones(
    row: dict[str, Any],
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
        normalized.append({
            **zone,
            "recoveryHeightRatioToFrame": (y1 - y0) / frame_height,
            "recoveryWidthRatioToPage": (x1 - x0) / max(1.0, page_width),
            "extendsLeftOfRecoveryFrame": x0 < float(frame[0]),
            "extendsAboveRecoveryFrame": y0 < float(frame[1]),
            "extendsRightOfRecoveryFrame": x1 > float(frame[2]),
            "extendsBelowRecoveryFrame": y1 > float(frame[3]),
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
    result.update({
        "zoneFrameFit": {
            "leftCount": left_count,
            "aboveCount": above_count,
            "rightCount": right_count,
            "belowCount": below_count,
            "allInsideFrame": left_count == 0 and above_count == 0 and right_count == 0 and below_count == 0,
        },
        "rendererMeaning": "unresolved",
        "crossZoneReadingOrder": "unresolved" if len(normalized) > 1 else "not-applicable",
        "wordRealization": None,
        "policy": (
            "zone relationship is measured against inherited outer-frame evidence only; "
            "confidence is capped at medium and no Word columns/sidebar/reading order are inferred"
        ),
    })
    return result


def recover_blocked_pages(
    report: dict[str, Any],
    line_map: dict[str, Any],
    frame_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach renderer-neutral second-pass recovery evidence.

    Primary body evidence remains untouched. Recovery prefers a dedicated outer
    frame profile learned from trusted raw extents. Robust-body margin profiles
    are not treated as page/text frames because their quantiles intentionally
    shrink observed content envelopes.
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
                frame = [left * width, top * height, right * width, bottom * height]
                if frame[2] > frame[0] and frame[3] > frame[1]:
                    recovery = {
                        "status": "recovered-from-outer-frame-profile",
                        "confidence": "medium",
                        "bodyConstraintPx": frame,
                        "source": "trusted-raw-outer-extents",
                        "frameProfileVersion": (frame_profile or {}).get("version"),
                        "frameProfileSourcePages": family_frame.get("sourcePages"),
                        "frameRatios": ratios,
                        "headerSemanticStatus": "unresolved",
                        "footerSemanticStatus": "unresolved",
                        "crossZoneReadingOrder": "unresolved",
                        "wordRealization": None,
                        "policy": (
                            "outer trusted raw extents constrain recovery geometry; robust-content "
                            "quantiles remain separate and furniture semantics remain unresolved"
                        ),
                    }
                    zone_recovery = _classify_recovered_zones(row, frame, width)
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
            "second-pass geometry only; dedicated outer-frame evidence is separate from robust content envelopes; "
            "primary evidence is preserved and no page_structure mutation or Word realization is permitted"
        ),
    }
    return report


__all__ = ["recover_blocked_pages"]
