from __future__ import annotations

from typing import Any

from .page_structure_legacy import *  # noqa: F401,F403
from .page_structure_legacy import build_page_structure as _build_legacy


VERSION = "page-structure-frame-evidence-0.1"


def _box(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: list[float], b: list[float]) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _contains(outer: list[float], inner: list[float], tolerance: float = 2.5) -> bool:
    return (
        outer[0] <= inner[0] + tolerance
        and outer[1] <= inner[1] + tolerance
        and outer[2] >= inner[2] - tolerance
        and outer[3] >= inner[3] - tolerance
    )


def _rect_primitive(drawing: dict[str, Any]) -> bool:
    return any(str(item.get("kind") or "") == "re" for item in drawing.get("items", []) or [])


def _candidate_score(callout_box: list[float], drawing: dict[str, Any]) -> tuple[float, dict[str, Any]] | None:
    drawing_box = _box(drawing.get("bbox"))
    if drawing_box is None:
        return None
    callout_area = max(1.0, _area(callout_box))
    drawing_area = max(1.0, _area(drawing_box))
    overlap = _intersection(callout_box, drawing_box) / callout_area
    contains = _contains(drawing_box, callout_box)
    if overlap < 0.72 and not contains:
        return None

    # A frame should be only moderately larger than its text container. Reject
    # page decorations / large panels that happen to contain the callout.
    area_ratio = drawing_area / callout_area
    if area_ratio > 8.0:
        return None
    edge_delta = (
        abs(callout_box[0] - drawing_box[0])
        + abs(callout_box[1] - drawing_box[1])
        + abs(callout_box[2] - drawing_box[2])
        + abs(callout_box[3] - drawing_box[3])
    )
    rectangular = _rect_primitive(drawing)
    styled = bool(drawing.get("strokeColor") or drawing.get("fillColor"))
    score = overlap * 55.0
    score += 24.0 if contains else 0.0
    score += 12.0 if rectangular else 0.0
    score += 6.0 if styled else 0.0
    score -= min(24.0, edge_delta * 0.35)
    score -= min(18.0, max(0.0, area_ratio - 1.0) * 4.0)
    return score, {
        "overlapRatio": round(overlap, 4),
        "areaRatio": round(area_ratio, 4),
        "edgeDeltaPt": round(edge_delta, 3),
        "containsTextBox": contains,
        "rectPrimitive": rectangular,
    }


def _frame_evidence(callout: dict[str, Any], drawings: list[dict[str, Any]]) -> dict[str, Any]:
    callout_box = _box(callout.get("bbox"))
    if callout_box is None:
        return {
            "status": "unresolved",
            "reason": "missing-callout-bbox",
            "source": "pdf-vector-drawings",
        }
    best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    for drawing in drawings:
        candidate = _candidate_score(callout_box, drawing)
        if candidate is None:
            continue
        score, evidence = candidate
        if best is None or score > best[0]:
            best = (score, drawing, evidence)
    if best is None:
        return {
            "status": "unresolved",
            "reason": "no-enclosing-pdf-vector-drawing",
            "source": "pdf-vector-drawings",
        }

    score, drawing, evidence = best
    if score >= 72.0:
        confidence = "high"
    elif score >= 52.0:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "status": "matched" if confidence in {"high", "medium"} else "review",
        "source": "pdf-vector-drawings",
        "drawingId": drawing.get("id"),
        "drawingBBoxPt": drawing.get("bbox"),
        "score": round(score, 2),
        "confidence": confidence,
        "stroke": {
            "color": drawing.get("strokeColor"),
            "widthPt": drawing.get("strokeWidthPt"),
            "opacity": drawing.get("strokeOpacity"),
            "dashes": drawing.get("dashes"),
            "status": "extracted" if drawing.get("strokeColor") is not None else "none-or-not-painted",
        },
        "fill": {
            "color": drawing.get("fillColor"),
            "opacity": drawing.get("fillOpacity"),
            "status": "extracted" if drawing.get("fillColor") is not None else "none-or-not-painted",
        },
        "path": {
            "type": drawing.get("type"),
            "closePath": drawing.get("closePath"),
            "rectPrimitive": evidence.get("rectPrimitive"),
        },
        "matchEvidence": evidence,
    }


def build_page_structure(
    pdf_analysis: dict[str, Any],
    work_dir,
    asset_dir,
    reference_docx=None,
    external_asset_paths=None,
    equation_donor_path=None,
) -> dict[str, Any]:
    result = _build_legacy(
        pdf_analysis,
        work_dir,
        asset_dir,
        reference_docx=reference_docx,
        external_asset_paths=external_asset_paths,
        equation_donor_path=equation_donor_path,
    )
    pdf_pages = {
        int(page.get("page") or 0): page
        for page in pdf_analysis.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    matched = 0
    review = 0
    unresolved = 0
    for page in result.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        drawings = list((pdf_pages.get(page_no) or {}).get("drawings", []) or [])
        page["pdf_drawings"] = drawings
        page["drawing_count"] = len(drawings)
        for callout in page.get("callouts", []) or []:
            evidence = _frame_evidence(callout, drawings)
            callout["frame_evidence"] = evidence
            status = str(evidence.get("status") or "unresolved")
            if status == "matched":
                matched += 1
            elif status == "review":
                review += 1
            else:
                unresolved += 1

    result["version"] = VERSION
    result["frameEvidenceSummary"] = {
        "source": "pdf_analysis.pages[].drawings",
        "matchedCalloutCount": matched,
        "reviewCalloutCount": review,
        "unresolvedCalloutCount": unresolved,
        "policy": "callout border/fill may be reconstructed only from matched PDF vector evidence",
    }
    return result
