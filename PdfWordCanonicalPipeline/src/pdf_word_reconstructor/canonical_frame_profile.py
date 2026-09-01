from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any


VERSION = "canonical-frame-profile-0.2"


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    vals = sorted(float(v) for v in values)
    idx = min(len(vals) - 1, max(0, round((len(vals) - 1) * fraction)))
    return vals[idx]


def _structural_zone_envelope(row: dict[str, Any]) -> list[float] | None:
    zones = []
    for zone in row.get("zones", []) or []:
        box = zone.get("bboxPx") or []
        if len(box) != 4:
            continue
        try:
            x0, y0, x1, y1 = [float(v) for v in box]
            width_ratio = float(zone.get("widthRatio") or 0.0)
            height_ratio = float(zone.get("heightRatioToBody") or 0.0)
        except (TypeError, ValueError):
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        if width_ratio < 0.16 or height_ratio < 0.45:
            continue
        zones.append([x0, y0, x1, y1])
    if not zones:
        return None
    return [
        min(box[0] for box in zones),
        min(box[1] for box in zones),
        max(box[2] for box in zones),
        max(box[3] for box in zones),
    ]


def build_canonical_frame_profile(report: dict[str, Any]) -> dict[str, Any]:
    """Learn renderer-neutral outer frame evidence and structural-container overhang.

    Robust body envelopes remain content observations only. The base outer frame is
    learned from trusted raw content extents. Separately, long-lived Mathpix zone
    envelopes on those same trusted pages are compared with raw extents so any
    systematic container padding/overhang is measured rather than guessed.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in report.get("pages", []) or []:
        margin = row.get("marginEvidence") or {}
        raw = row.get("rawBodyEvidence") or {}
        validation = row.get("furnitureValidation") or {}
        if (
            margin.get("source") == "page-evidence"
            and margin.get("confidence") == "high"
            and raw.get("status") == "observed"
            and validation.get("valid")
            and not (row.get("conflicts") or [])
        ):
            grouped[str(row.get("frameFamily") or "unknown")].append(row)

    families: dict[str, Any] = {}
    for family, rows in grouped.items():
        lefts = [float((r.get("rawBodyEvidence") or {}).get("leftRatio")) for r in rows]
        rights = [float((r.get("rawBodyEvidence") or {}).get("rightRatio")) for r in rows]
        tops = [float((r.get("rawBodyEvidence") or {}).get("startRatio")) for r in rows]
        bottoms = [float((r.get("rawBodyEvidence") or {}).get("endRatio")) for r in rows]

        left = _quantile(lefts, 0.10)
        right = _quantile(rights, 0.90)
        top = _quantile(tops, 0.10)
        bottom = _quantile(bottoms, 0.90)
        if None in {left, right, top, bottom}:
            continue
        if float(right) <= float(left) or float(bottom) <= float(top):
            continue

        overhang_rows: list[dict[str, Any]] = []
        for row in rows:
            raw = row.get("rawBodyEvidence") or {}
            raw_box = raw.get("bboxPx") or []
            zone_box = _structural_zone_envelope(row)
            page_size = row.get("pageSizePx") or []
            if len(raw_box) != 4 or zone_box is None or len(page_size) != 2:
                continue
            try:
                raw_x0, raw_y0, raw_x1, raw_y1 = [float(v) for v in raw_box]
                width, height = [float(v) for v in page_size]
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            overhang_rows.append({
                "page": int(row.get("page") or 0),
                "leftRatio": max(0.0, raw_x0 - zone_box[0]) / width,
                "topRatio": max(0.0, raw_y0 - zone_box[1]) / height,
                "rightRatio": max(0.0, zone_box[2] - raw_x1) / width,
                "bottomRatio": max(0.0, zone_box[3] - raw_y1) / height,
                "zoneEnvelopePx": zone_box,
                "rawEnvelopePx": list(raw_box),
            })

        overhang_stats: dict[str, Any] = {
            "sourcePageCount": len(overhang_rows),
            "sourcePages": [row["page"] for row in overhang_rows],
            "policy": (
                "diagnostic only: structural-zone overhang is measured on trusted pages; "
                "it does not expand the canonical outer frame in this version"
            ),
        }
        for side in ("left", "top", "right", "bottom"):
            values = [float(row[f"{side}Ratio"]) for row in overhang_rows]
            overhang_stats[f"{side}MedianRatio"] = median(values) if values else None
            overhang_stats[f"{side}P90Ratio"] = _quantile(values, 0.90) if values else None
            overhang_stats[f"{side}MaxRatio"] = max(values) if values else None
        overhang_stats["pages"] = overhang_rows

        families[family] = {
            "status": "resolved",
            "confidence": "medium",
            "source": "trusted-raw-outer-extents",
            "sourcePageCount": len(rows),
            "sourcePages": [int(r.get("page") or 0) for r in rows],
            "frameRatios": {
                "left": float(left),
                "top": float(top),
                "right": float(right),
                "bottom": float(bottom),
            },
            "edgeStatistics": {
                "leftP10": float(left),
                "topP10": float(top),
                "rightP90": float(right),
                "bottomP90": float(bottom),
            },
            "structuralZoneOverhang": overhang_stats,
            "policy": (
                "outer frame uses expansive trusted raw extents; robust content quantiles remain separate. "
                "Structural Mathpix container overhang is measured independently before any frame expansion is allowed"
            ),
        }

    return {
        "version": VERSION,
        "families": families,
        "policy": (
            "renderer-neutral outer frame evidence only; no page_structure mutation, "
            "no header/footer semantics and no Word realization"
        ),
    }


__all__ = ["build_canonical_frame_profile"]
