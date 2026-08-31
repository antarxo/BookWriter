from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .lines_first_markdown_span_dedup_contract import build_lines_first_markdown_span_dedup_contract

VERSION = "lines-first-layout-role-probe-0.1"
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


def _overlap_y(a: list[float], b: list[float]) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _overlap_x(a: list[float], b: list[float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def _height(box: list[float]) -> float:
    return max(1.0, box[3] - box[1])


def _width(box: list[float]) -> float:
    return max(1.0, box[2] - box[0])


def _active_area(items: list[dict[str, Any]], page: dict[str, Any]) -> list[float]:
    boxes = [item["box"] for item in items if item.get("box")]
    if boxes:
        return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return [0.0, 0.0, float(page.get("width_pt") or 0.0), float(page.get("height_pt") or 0.0)]


def _pair_relation(a: dict[str, Any], b: dict[str, Any], active: list[float]) -> dict[str, Any] | None:
    abox, bbox = a["box"], b["box"]
    oy = _overlap_y(abox, bbox)
    if oy <= _EPS:
        return None
    vertical_overlap_ratio = oy / min(_height(abox), _height(bbox))
    if vertical_overlap_ratio < 0.30:
        return None

    active_width = max(1.0, active[2] - active[0])
    aw, bw = _width(abox), _width(bbox)
    ar, br = aw / active_width, bw / active_width
    horizontal_overlap = _overlap_x(abox, bbox)
    disjoint_x = horizontal_overlap <= _EPS
    gap = max(0.0, max(abox[0], bbox[0]) - min(abox[2], bbox[2])) if disjoint_x else 0.0
    width_balance = min(aw, bw) / max(aw, bw)

    relation = None
    confidence = "low"
    evidence: list[str] = []

    if disjoint_x and ar <= 0.48 and br <= 0.48 and width_balance >= 0.62:
        relation = "parallel-comparable-lanes"
        confidence = "high" if vertical_overlap_ratio >= 0.65 and width_balance >= 0.75 else "medium"
        evidence.extend(["x-disjoint", "comparable-widths", "substantial-y-overlap"])
    elif disjoint_x and min(ar, br) <= 0.38 and max(ar, br) >= 0.46:
        relation = "narrow-beside-wider-flow"
        confidence = "high" if vertical_overlap_ratio >= 0.55 and min(ar, br) <= 0.30 else "medium"
        evidence.extend(["x-disjoint", "asymmetric-widths", "substantial-y-overlap"])
    else:
        return None

    left, right = (a, b) if abox[0] <= bbox[0] else (b, a)
    return {
        "relation": relation,
        "confidence": confidence,
        "leftItemId": left["id"],
        "rightItemId": right["id"],
        "leftWidthRatio": round(_width(left["box"]) / active_width, 4),
        "rightWidthRatio": round(_width(right["box"]) / active_width, 4),
        "verticalOverlapRatio": round(vertical_overlap_ratio, 4),
        "horizontalGapPt": round(gap, 3),
        "widthBalance": round(width_balance, 4),
        "evidence": evidence,
    }


def _page_roles(page: dict[str, Any]) -> dict[str, Any]:
    page_no = int(page.get("page") or 0)
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
            "semantic": item.get("semantic_type"),
            "textPreview": str(item.get("text") or "")[:120],
        })
    items.sort(key=lambda item: (item["box"][1], item["box"][0], item["box"][3], item["box"][2]))
    active = _active_area(items, page)
    active_width = max(1.0, active[2] - active[0])

    item_roles: list[dict[str, Any]] = []
    for item in items:
        box = item["box"]
        width_ratio = _width(box) / active_width
        if width_ratio >= 0.72:
            role = "wide-flow-or-spanning-candidate"
        elif width_ratio <= 0.34:
            center = (box[0] + box[2]) / 2.0
            mid = (active[0] + active[2]) / 2.0
            role = "left-narrow-candidate" if center < mid else "right-narrow-candidate"
        else:
            role = "medium-flow-candidate"
        item_roles.append({
            "id": item["id"],
            "role": role,
            "widthRatio": round(width_ratio, 4),
            "bbox": [round(v, 3) for v in box],
            "semantic": item.get("semantic"),
            "textPreview": item.get("textPreview"),
        })

    relations: list[dict[str, Any]] = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            relation = _pair_relation(a, b, active)
            if relation:
                relations.append(relation)

    # Group relation evidence into connected components; these are layout groups,
    # not renderer decisions.
    adjacency: dict[str, set[str]] = {}
    for rel in relations:
        a, b = str(rel["leftItemId"]), str(rel["rightItemId"])
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    groups: list[list[str]] = []
    seen: set[str] = set()
    for node in adjacency:
        if node in seen:
            continue
        stack = [node]
        component: list[str] = []
        seen.add(node)
        while stack:
            current = stack.pop()
            component.append(current)
            for nxt in adjacency.get(current, set()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        groups.append(sorted(component))

    return {
        "page": page_no,
        "activeArea": [round(v, 3) for v in active],
        "itemCount": len(items),
        "itemRoles": item_roles,
        "relations": relations,
        "relationGroups": groups,
        "summary": {
            "wideFlowCandidateCount": sum(r["role"] == "wide-flow-or-spanning-candidate" for r in item_roles),
            "narrowCandidateCount": sum(r["role"] in {"left-narrow-candidate", "right-narrow-candidate"} for r in item_roles),
            "parallelComparableLaneRelationCount": sum(r["relation"] == "parallel-comparable-lanes" for r in relations),
            "sidebarRelationCount": sum(r["relation"] == "narrow-beside-wider-flow" for r in relations),
            "relationGroupCount": len(groups),
        },
    }


def build_lines_first_layout_role_probe_contract(
    lines_path: Path,
    mmd_path: Path,
    page_width_pt: float = 595.276,
) -> dict[str, Any]:
    """Diagnostic layout-role evidence on top of the stable Lines-first content model.

    No Word layout decisions are made. Narrow width alone never implies floating.
    Pairwise x/y geometry is reported as evidence for either comparable parallel lanes
    (possible true multi-column structure) or asymmetric narrow-beside-wide relations
    (possible sidebar/callout structure).
    """
    result = deepcopy(build_lines_first_markdown_span_dedup_contract(
        Path(lines_path), Path(mmd_path), page_width_pt=page_width_pt
    ))
    page_reports = [_page_roles(page) for page in (result["pageStructure"].get("pages", []) or [])]

    aggregate = {
        "pageCount": len(page_reports),
        "wideFlowCandidateCount": sum(p["summary"]["wideFlowCandidateCount"] for p in page_reports),
        "narrowCandidateCount": sum(p["summary"]["narrowCandidateCount"] for p in page_reports),
        "parallelComparableLaneRelationCount": sum(p["summary"]["parallelComparableLaneRelationCount"] for p in page_reports),
        "sidebarRelationCount": sum(p["summary"]["sidebarRelationCount"] for p in page_reports),
        "relationGroupCount": sum(p["summary"]["relationGroupCount"] for p in page_reports),
        "pagesWithParallelLaneEvidence": [p["page"] for p in page_reports if p["summary"]["parallelComparableLaneRelationCount"]],
        "pagesWithSidebarEvidence": [p["page"] for p in page_reports if p["summary"]["sidebarRelationCount"]],
    }
    result["version"] = VERSION
    result["layoutRoleEvidence"] = {
        "version": VERSION,
        "rendererDecision": "deferred",
        "narrowImpliesFloating": False,
        "pageReports": page_reports,
        "summary": aggregate,
    }
    return result


__all__ = ["build_lines_first_layout_role_probe_contract"]
