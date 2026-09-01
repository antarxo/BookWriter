from __future__ import annotations

from collections import defaultdict
from typing import Any


VERSION = "canonical-frame-profile-0.1"


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    vals = sorted(float(v) for v in values)
    idx = min(len(vals) - 1, max(0, round((len(vals) - 1) * fraction)))
    return vals[idx]


def build_canonical_frame_profile(report: dict[str, Any]) -> dict[str, Any]:
    """Learn renderer-neutral outer page/text-frame evidence from trusted raw extents.

    This deliberately does not reuse robust-body margins. Robust body envelopes are
    content observations; the frame profile is learned from outer raw extents of
    trusted pages so sparse/special pages cannot shrink the inherited frame.
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
            "policy": (
                "outer frame uses expansive trusted raw extents; robust content quantiles "
                "remain separate and cannot shrink the inherited frame"
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
