from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .lines_first_markdown_span_dedup_contract import build_lines_first_markdown_span_dedup_contract

VERSION = "lines-first-layout-cluster-probe-0.3"
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


def _semantic_family(semantic: str) -> str:
    value = str(semantic or "paragraph").lower()
    if value in {"equation", "display_equation"}:
        return "equation"
    if value in {"heading", "title"}:
        return "heading"
    if value == "caption":
        return "caption"
    return "prose"


def _page_items(page: dict[str, Any]) -> tuple[list[dict[str, Any]], list[float]]:
    items: list[dict[str, Any]] = []
    for item in page.get("flow", []) or []:
        if item.get("type") != "text":
            continue
        box = _box(item)
        if not box:
            continue
        items.append({
            "id": str(item.get("id") or ""),
            "box": box,
            "semantic": str(item.get("semantic_type") or "paragraph"),
            "family": _semantic_family(str(item.get("semantic_type") or "paragraph")),
            "textPreview": str(item.get("text") or "")[:140],
        })
    items.sort(key=lambda x: (x["box"][1], x["box"][0]))
    boxes = [x["box"] for x in items]
    active = _union(boxes) or [0.0, 0.0, float(page.get("width_pt") or 0.0), float(page.get("height_pt") or 0.0)]
    aw = max(1.0, active[2] - active[0])
    ah = max(1.0, active[3] - active[1])
    for item in items:
        item["widthRatio"] = _w(item["box"]) / aw
        item["heightRatio"] = _h(item["box"]) / ah
        item["topRatio"] = (item["box"][1] - active[1]) / ah
        item["bottomRatio"] = (item["box"][3] - active[1]) / ah
    return items, active


def _page_top_compositions(items: list[dict[str, Any]], active: list[float]) -> list[dict[str, Any]]:
    aw = max(1.0, active[2] - active[0])
    ah = max(1.0, active[3] - active[1])
    top_start_limit = active[1] + 0.16 * ah
    candidates = [x for x in items if x["box"][1] <= top_start_limit and x["bottomRatio"] <= 0.22]
    if len(candidates) < 2 or not any(x["family"] == "heading" for x in candidates):
        return []
    env = _union([x["box"] for x in candidates])
    if env is None or _h(env) / ah > 0.18:
        return []
    x_centers = [(x["box"][0] + x["box"][2]) / 2.0 for x in candidates]
    if max(x_centers) - min(x_centers) < 0.20 * aw:
        return []
    return [{
        "role": "page-top-composition",
        "itemIds": [x["id"] for x in candidates],
        "bbox": [round(v, 3) for v in env],
        "heightRatio": round(_h(env) / ah, 4),
        "evidence": ["top-zone", "shallow-cluster", "multiple-horizontal-positions", "contains-heading"],
    }]


def _x_alignment(a: list[float], b: list[float]) -> float:
    inter = _ox(a, b)
    return inter / min(_w(a), _w(b))


def _main_lane_clusters(items: list[dict[str, Any]], active: list[float], excluded_ids: set[str]) -> list[dict[str, Any]]:
    ah = max(1.0, active[3] - active[1])
    aw = max(1.0, active[2] - active[0])
    candidates = [
        x for x in items
        if x["id"] not in excluded_ids
        and x["family"] in {"prose", "heading"}
        and x["widthRatio"] >= 0.40
    ]
    candidates.sort(key=lambda x: (x["box"][1], x["box"][0]))

    clusters: list[list[dict[str, Any]]] = []
    for item in candidates:
        best_index = None
        best_score = -1.0
        for idx, comp in enumerate(clusters):
            env = _union([x["box"] for x in comp])
            assert env is not None
            horizontal_alignment = _x_alignment(item["box"], env)
            gap = item["box"][1] - env[3]
            # Allow a main lane to continue through normal paragraph gaps and small
            # intervening structures, but require consistent horizontal lane geometry.
            if horizontal_alignment < 0.58:
                continue
            if gap > 0.14 * ah:
                continue
            score = horizontal_alignment - max(0.0, gap) / ah
            if score > best_score:
                best_score = score
                best_index = idx
        if best_index is None:
            clusters.append([item])
        else:
            clusters[best_index].append(item)

    out: list[dict[str, Any]] = []
    for idx, comp in enumerate(clusters):
        env = _union([x["box"] for x in comp])
        assert env is not None
        prose_count = sum(x["family"] == "prose" for x in comp)
        out.append({
            "id": f"main-lane-{idx}",
            "itemIds": [x["id"] for x in comp],
            "bbox": env,
            "itemCount": len(comp),
            "proseCount": prose_count,
            "widthRatio": _w(env) / aw,
            "heightRatio": _h(env) / ah,
        })
    return out


def _sidebar_candidates(items: list[dict[str, Any]], active: list[float], top_ids: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ah = max(1.0, active[3] - active[1])
    main_lanes = _main_lane_clusters(items, active, top_ids)
    out: list[dict[str, Any]] = []

    for side in items:
        if side["id"] in top_ids or side["family"] not in {"prose", "caption"}:
            continue
        if side["widthRatio"] > 0.32:
            continue
        side_h_ratio = _h(side["box"]) / ah
        if side_h_ratio < 0.14:
            continue

        best = None
        for main in main_lanes:
            if side["id"] in main["itemIds"]:
                continue
            if _ox(side["box"], main["bbox"]) > _EPS:
                continue
            overlap = _oy(side["box"], main["bbox"])
            if overlap <= _EPS:
                continue
            side_persistence = overlap / _h(side["box"])
            main_persistence = overlap / _h(main["bbox"])
            if side_persistence < 0.60:
                continue
            gap = max(0.0, max(side["box"][0], main["bbox"][0]) - min(side["box"][2], main["bbox"][2]))
            score = 1.5 * side_persistence + min(0.6, side_h_ratio) + 0.25 * main["heightRatio"] + 0.08 * main["proseCount"]
            cand = (score, main, side_persistence, main_persistence, gap)
            if best is None or cand[0] > best[0]:
                best = cand
        if best is None:
            continue

        _, main, side_persistence, main_persistence, gap = best
        side_center = (side["box"][0] + side["box"][2]) / 2.0
        main_center = (main["bbox"][0] + main["bbox"][2]) / 2.0
        confidence = "high" if side_h_ratio >= 0.24 and side_persistence >= 0.75 and main["proseCount"] >= 1 else "medium"
        out.append({
            "role": "sidebar-callout-candidate",
            "sideItemId": side["id"],
            "mainLaneId": main["id"],
            "mainItemIds": main["itemIds"],
            "side": "left" if side_center < main_center else "right",
            "sideWidthRatio": round(side["widthRatio"], 4),
            "sideHeightRatio": round(side_h_ratio, 4),
            "mainWidthRatio": round(main["widthRatio"], 4),
            "mainHeightRatio": round(main["heightRatio"], 4),
            "mainItemCount": main["itemCount"],
            "mainProseCount": main["proseCount"],
            "sideVerticalPersistence": round(side_persistence, 4),
            "mainVerticalPersistence": round(main_persistence, 4),
            "horizontalGapPt": round(gap, 3),
            "confidence": confidence,
            "evidence": [
                "narrow-prose-lane",
                "x-disjoint-from-main-lane-cluster",
                "side-mostly-runs-beside-main-cluster",
                "persistent-main-lane-envelope",
            ],
        })
    return out, main_lanes


def _lane_components(items: list[dict[str, Any]], active: list[float], excluded_ids: set[str]) -> list[dict[str, Any]]:
    prose = [x for x in items if x["family"] == "prose" and x["id"] not in excluded_ids and x["widthRatio"] <= 0.55]
    components: list[list[dict[str, Any]]] = []
    for item in prose:
        for comp in components:
            env = _union([x["box"] for x in comp])
            assert env is not None
            if _ox(item["box"], env) / min(_w(item["box"]), _w(env)) >= 0.55:
                comp.append(item)
                break
        else:
            components.append([item])
    ah = max(1.0, active[3] - active[1])
    aw = max(1.0, active[2] - active[0])
    lanes: list[dict[str, Any]] = []
    for idx, comp in enumerate(components):
        env = _union([x["box"] for x in comp])
        assert env is not None
        lanes.append({
            "id": f"lane-{idx}",
            "itemIds": [x["id"] for x in comp],
            "bbox": env,
            "itemCount": len(comp),
            "widthRatio": _w(env) / aw,
            "heightRatio": _h(env) / ah,
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
            if balance < 0.70:
                continue
            overlap = _oy(a["bbox"], b["bbox"])
            persistence = overlap / min(_h(a["bbox"]), _h(b["bbox"]))
            if persistence < 0.65:
                continue
            if not (a["itemCount"] >= 2 and a["heightRatio"] >= 0.20 and b["itemCount"] >= 2 and b["heightRatio"] >= 0.20):
                continue
            left, right = (a, b) if a["bbox"][0] <= b["bbox"][0] else (b, a)
            out.append({
                "role": "true-multicolumn-candidate",
                "leftLane": left,
                "rightLane": right,
                "widthBalance": round(balance, 4),
                "verticalPersistence": round(persistence, 4),
                "horizontalGapPt": round(max(0.0, right["bbox"][0] - left["bbox"][2]), 3),
                "confidence": "high" if balance >= 0.80 and persistence >= 0.75 else "medium",
                "evidence": ["two-substantial-prose-lanes", "comparable-widths", "persistent-common-y-range"],
            })
    return out


def _page_report(page: dict[str, Any]) -> dict[str, Any]:
    items, active = _page_items(page)
    top = _page_top_compositions(items, active)
    top_ids = {x for g in top for x in g["itemIds"]}
    sidebars, main_lanes = _sidebar_candidates(items, active, top_ids)
    sidebar_ids = {s["sideItemId"] for s in sidebars}
    true_cols = _true_column_candidates(items, active, top_ids | sidebar_ids)
    return {
        "page": int(page.get("page") or 0),
        "activeArea": [round(v, 3) for v in active],
        "itemCount": len(items),
        "pageTopCompositions": top,
        "mainLaneClusters": main_lanes,
        "sidebarCalloutCandidates": sidebars,
        "trueMulticolumnCandidates": true_cols,
        "summary": {
            "pageTopCompositionCount": len(top),
            "mainLaneClusterCount": len(main_lanes),
            "sidebarCalloutCandidateCount": len(sidebars),
            "highConfidenceSidebarCount": sum(x["confidence"] == "high" for x in sidebars),
            "trueMulticolumnCandidateCount": len(true_cols),
            "highConfidenceMulticolumnCount": sum(x["confidence"] == "high" for x in true_cols),
        },
    }


def build_lines_first_layout_cluster_probe_v3_contract(lines_path: Path, mmd_path: Path, page_width_pt: float = 595.276) -> dict[str, Any]:
    result = deepcopy(build_lines_first_markdown_span_dedup_contract(Path(lines_path), Path(mmd_path), page_width_pt=page_width_pt))
    reports = [_page_report(page) for page in (result["pageStructure"].get("pages", []) or [])]
    summary = {
        "pageCount": len(reports),
        "pageTopCompositionCount": sum(r["summary"]["pageTopCompositionCount"] for r in reports),
        "mainLaneClusterCount": sum(r["summary"]["mainLaneClusterCount"] for r in reports),
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
        "mainLaneClustersAreAuthorityForSidebarComparison": True,
        "narrowImpliesFloating": False,
        "singleEquationMayCreateColumn": False,
        "pageTopMustBeShallow": True,
        "sidebarMayStartInsideMainLane": True,
        "pageReports": reports,
        "summary": summary,
    }
    return result


__all__ = ["build_lines_first_layout_cluster_probe_v3_contract"]
