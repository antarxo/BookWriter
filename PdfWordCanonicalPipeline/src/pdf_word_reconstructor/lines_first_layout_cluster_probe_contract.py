from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .lines_first_markdown_span_dedup_contract import build_lines_first_markdown_span_dedup_contract

VERSION = "lines-first-layout-cluster-probe-0.1"
_EPS = 0.5


def _box(item: dict[str, Any]) -> list[float] | None:
    box = item.get("bbox")
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        vals = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    return vals if vals[2] > vals[0] and vals[3] > vals[1] else None


def _w(box: list[float]) -> float:
    return max(1.0, box[2] - box[0])


def _h(box: list[float]) -> float:
    return max(1.0, box[3] - box[1])


def _oy(a: list[float], b: list[float]) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _ox(a: list[float], b: list[float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _active(items: list[dict[str, Any]], page: dict[str, Any]) -> list[float]:
    boxes = [item["box"] for item in items]
    return _union(boxes) or [0.0, 0.0, float(page.get("width_pt") or 0.0), float(page.get("height_pt") or 0.0)]


def _semantic_family(semantic: str) -> str:
    value = str(semantic or "paragraph").lower()
    if value in {"equation", "display_equation"}:
        return "equation"
    if value in {"heading", "title"}:
        return "heading"
    if value in {"caption"}:
        return "caption"
    return "prose"


def _page_items(page: dict[str, Any]) -> tuple[list[dict[str, Any]], list[float]]:
    raw: list[dict[str, Any]] = []
    for item in page.get("flow", []) or []:
        if item.get("type") != "text":
            continue
        box = _box(item)
        if not box:
            continue
        raw.append({
            "id": str(item.get("id") or ""),
            "box": box,
            "semantic": str(item.get("semantic_type") or "paragraph"),
            "family": _semantic_family(str(item.get("semantic_type") or "paragraph")),
            "textPreview": str(item.get("text") or "")[:140],
        })
    raw.sort(key=lambda x: (x["box"][1], x["box"][0]))
    active = _active(raw, page)
    aw = max(1.0, active[2] - active[0])
    ah = max(1.0, active[3] - active[1])
    for item in raw:
        item["widthRatio"] = _w(item["box"]) / aw
        item["heightRatio"] = _h(item["box"]) / ah
        item["topRatio"] = (item["box"][1] - active[1]) / ah
        item["bottomRatio"] = (item["box"][3] - active[1]) / ah
    return raw, active


def _page_top_compositions(items: list[dict[str, Any]], active: list[float]) -> list[dict[str, Any]]:
    ah = max(1.0, active[3] - active[1])
    top_limit = active[1] + 0.16 * ah
    top = [x for x in items if x["box"][1] <= top_limit]
    if len(top) < 2:
        return []
    # Require at least one heading and more than one horizontal position.
    if not any(x["family"] == "heading" for x in top):
        return []
    x_centers = [(x["box"][0] + x["box"][2]) / 2.0 for x in top]
    if max(x_centers) - min(x_centers) < 0.20 * max(1.0, active[2] - active[0]):
        return []
    return [{
        "role": "page-top-composition",
        "itemIds": [x["id"] for x in top],
        "bbox": [round(v, 3) for v in (_union([x["box"] for x in top]) or active)],
        "evidence": ["top-zone", "multiple-horizontal-positions", "contains-heading"],
    }]


def _sidebar_candidates(items: list[dict[str, Any]], active: list[float], top_ids: set[str]) -> list[dict[str, Any]]:
    aw = max(1.0, active[2] - active[0])
    ah = max(1.0, active[3] - active[1])
    mains = [x for x in items if x["widthRatio"] >= 0.55 and x["family"] in {"prose", "heading"}]
    out: list[dict[str, Any]] = []
    for side in items:
        if side["id"] in top_ids:
            continue
        if side["family"] not in {"prose", "caption"}:
            continue
        if side["widthRatio"] > 0.32 or side["heightRatio"] < 0.16:
            continue
        best = None
        for main in mains:
            if main["id"] == side["id"]:
                continue
            if _ox(side["box"], main["box"]) > _EPS:
                continue
            overlap = _oy(side["box"], main["box"])
            persistence = overlap / _h(side["box"])
            if persistence < 0.45:
                continue
            gap = max(0.0, max(side["box"][0], main["box"][0]) - min(side["box"][2], main["box"][2]))
            score = persistence + min(0.5, side["heightRatio"]) + 0.25 * main["heightRatio"]
            cand = (score, main, persistence, gap)
            if best is None or cand[0] > best[0]:
                best = cand
        if best is None:
            continue
        _, main, persistence, gap = best
        side_center = (side["box"][0] + side["box"][2]) / 2.0
        main_center = (main["box"][0] + main["box"][2]) / 2.0
        out.append({
            "role": "sidebar-callout-candidate",
            "sideItemId": side["id"],
            "mainItemId": main["id"],
            "side": "left" if side_center < main_center else "right",
            "sideWidthRatio": round(side["widthRatio"], 4),
            "sideHeightRatio": round(side["heightRatio"], 4),
            "mainWidthRatio": round(main["widthRatio"], 4),
            "mainHeightRatio": round(main["heightRatio"], 4),
            "verticalPersistence": round(persistence, 4),
            "horizontalGapPt": round(gap, 3),
            "confidence": "high" if side["heightRatio"] >= 0.28 and persistence >= 0.65 else "medium",
            "evidence": ["narrow-prose-lane", "x-disjoint-from-main", "persistent-y-overlap", "main-lane-wider"],
        })
    return out


def _lane_components(items: list[dict[str, Any]], active: list[float], excluded_ids: set[str]) -> list[dict[str, Any]]:
    # Build x-coherent prose lanes. Equations/captions/headings cannot create a lane by themselves.
    prose = [x for x in items if x["family"] == "prose" and x["id"] not in excluded_ids and x["widthRatio"] <= 0.55]
    components: list[list[dict[str, Any]]] = []
    for item in prose:
        placed = False
        for comp in components:
            envelope = _union([x["box"] for x in comp])
            assert envelope is not None
            # Same lane if horizontal overlap is substantial relative to the narrower item.
            if _ox(item["box"], envelope) / min(_w(item["box"]), _w(envelope)) >= 0.55:
                comp.append(item)
                placed = True
                break
        if not placed:
            components.append([item])

    lanes: list[dict[str, Any]] = []
    ah = max(1.0, active[3] - active[1])
    aw = max(1.0, active[2] - active[0])
    for idx, comp in enumerate(components):
        envelope = _union([x["box"] for x in comp])
        assert envelope is not None
        lanes.append({
            "id": f"lane-{idx}",
            "itemIds": [x["id"] for x in comp],
            "bbox": envelope,
            "itemCount": len(comp),
            "widthRatio": _w(envelope) / aw,
            "heightRatio": _h(envelope) / ah,
        })
    return lanes


def _true_column_candidates(items: list[dict[str, Any]], active: list[float], excluded_ids: set[str]) -> list[dict[str, Any]]:
    lanes = _lane_components(items, active, excluded_ids)
    out: list[dict[str, Any]] = []
    for i, a in enumerate(lanes):
        for b in lanes[i + 1:]:
            if _ox(a["bbox"], b["bbox"]) > _EPS:
                continue
            balance = min(a["widthRatio"], b["widthRatio"]) / max(a["widthRatio"], b["widthRatio"])
            if balance < 0.65:
                continue
            overlap = _oy(a["bbox"], b["bbox"])
            persistence = overlap / min(_h(a["bbox"]), _h(b["bbox"]))
            if persistence < 0.55:
                continue
            # A true column lane must carry more than a token fragment: >=2 prose blocks or substantial vertical extent.
            a_substantial = a["itemCount"] >= 2 or a["heightRatio"] >= 0.28
            b_substantial = b["itemCount"] >= 2 or b["heightRatio"] >= 0.28
            if not (a_substantial and b_substantial):
                continue
            left, right = (a, b) if a["bbox"][0] <= b["bbox"][0] else (b, a)
            gap = max(0.0, right["bbox"][0] - left["bbox"][2])
            out.append({
                "role": "true-multicolumn-candidate",
                "leftLane": left,
                "rightLane": right,
                "widthBalance": round(balance, 4),
                "verticalPersistence": round(persistence, 4),
                "horizontalGapPt": round(gap, 3),
                "confidence": "high" if balance >= 0.78 and persistence >= 0.70 else "medium",
                "evidence": ["two-prose-lanes", "comparable-widths", "persistent-common-y-range", "nontrivial-lane-content"],
            })
    return out


def _page_report(page: dict[str, Any]) -> dict[str, Any]:
    items, active = _page_items(page)
    top = _page_top_compositions(items, active)
    top_ids = {x for group in top for x in group["itemIds"]}
    sidebars = _sidebar_candidates(items, active, top_ids)
    sidebar_ids = {s["sideItemId"] for s in sidebars}
    true_cols = _true_column_candidates(items, active, top_ids | sidebar_ids)
    return {
        "page": int(page.get("page") or 0),
        "activeArea": [round(v, 3) for v in active],
        "itemCount": len(items),
        "pageTopCompositions": top,
        "sidebarCalloutCandidates": sidebars,
        "trueMulticolumnCandidates": true_cols,
        "summary": {
            "pageTopCompositionCount": len(top),
            "sidebarCalloutCandidateCount": len(sidebars),
            "highConfidenceSidebarCount": sum(x["confidence"] == "high" for x in sidebars),
            "trueMulticolumnCandidateCount": len(true_cols),
            "highConfidenceMulticolumnCount": sum(x["confidence"] == "high" for x in true_cols),
        },
    }


def build_lines_first_layout_cluster_probe_contract(
    lines_path: Path,
    mmd_path: Path,
    page_width_pt: float = 595.276,
) -> dict[str, Any]:
    result = deepcopy(build_lines_first_markdown_span_dedup_contract(Path(lines_path), Path(mmd_path), page_width_pt=page_width_pt))
    reports = [_page_report(page) for page in (result["pageStructure"].get("pages", []) or [])]
    summary = {
        "pageCount": len(reports),
        "pageTopCompositionCount": sum(r["summary"]["pageTopCompositionCount"] for r in reports),
        "sidebarCalloutCandidateCount": sum(r["summary"]["sidebarCalloutCandidateCount"] for r in reports),
        "highConfidenceSidebarCount": sum(r["summary"]["highConfidenceSidebarCount"] for r in reports),
        "trueMulticolumnCandidateCount": sum(r["summary"]["trueMulticolumnCandidateCount"] for r in reports),
        "highConfidenceMulticolumnCount": sum(r["summary"]["highConfidenceMulticolumnCount"] for r in reports),
        "pagesWithSidebarCandidates": [r["page"] for r in reports if r["summary"]["sidebarCalloutCandidateCount"]],
        "pagesWithTrueMulticolumnCandidates": [r["page"] for r in reports if r["summary"]["trueMulticolumnCandidateCount"]],
    }
    result["version"] = VERSION
    result["layoutClusterEvidence"] = {
        "version": VERSION,
        "rendererDecision": "deferred",
        "pairwiseRelationsAreAuthority": False,
        "narrowImpliesFloating": False,
        "singleEquationMayCreateColumn": False,
        "pageReports": reports,
        "summary": summary,
    }
    return result


__all__ = ["build_lines_first_layout_cluster_probe_contract"]
