from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .lines_first_layout_cluster_probe_v3_contract import build_lines_first_layout_cluster_probe_v3_contract
from .mathpix_lines_input import build_mathpix_line_layout_map

VERSION = "lines-first-side-rail-decomposition-probe-0.1"
_EPS = 0.5


def _box_list_from_raw(record: dict[str, Any], sx: float, sy: float) -> list[float] | None:
    box = record.get("bbox_px") if isinstance(record.get("bbox_px"), dict) else None
    if not box:
        return None
    try:
        x0 = float(box.get("x0")) * sx
        y0 = float(box.get("y0")) * sy
        x1 = float(box.get("x1")) * sx
        y1 = float(box.get("y1")) * sy
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _w(box: list[float]) -> float:
    return max(1.0, box[2] - box[0])


def _h(box: list[float]) -> float:
    return max(1.0, box[3] - box[1])


def _ox(a: list[float], b: list[float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def _oy(a: list[float], b: list[float]) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _text(record: dict[str, Any]) -> str:
    for value in (record.get("text_display"), record.get("text"), record.get("conversion_output")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _rail_box(side_item_box: list[float], main_lane_box: list[float], side: str, active: list[float]) -> list[float]:
    if side == "left":
        x0 = active[0]
        x1 = min(main_lane_box[0] - 1.0, max(side_item_box[2], active[0] + 1.0))
    else:
        x0 = max(main_lane_box[2] + 1.0, min(side_item_box[0], active[2] - 1.0))
        x1 = active[2]
    y0 = min(side_item_box[1], main_lane_box[1])
    y1 = max(side_item_box[3], main_lane_box[3])
    return [x0, y0, x1, y1]


def _belongs_to_rail(box: list[float], rail: list[float]) -> tuple[bool, float, float]:
    ox = _ox(box, rail)
    oy = _oy(box, rail)
    xr = ox / _w(box)
    yr = oy / _h(box)
    accepted = xr >= 0.55 and yr >= 0.30
    return accepted, xr, yr


def _ancestry(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    parent = str(record.get("parent_id") or "")
    while parent and parent not in seen:
        seen.add(parent)
        out.append(parent)
        node = by_id.get(parent)
        if not node:
            break
        parent = str(node.get("parent_id") or "")
    return out


def build_lines_first_side_rail_decomposition_probe_contract(
    lines_path: Path,
    mmd_path: Path,
    page_width_pt: float = 595.276,
) -> dict[str, Any]:
    base = deepcopy(build_lines_first_layout_cluster_probe_v3_contract(
        Path(lines_path), Path(mmd_path), page_width_pt=page_width_pt
    ))
    raw_map = build_mathpix_line_layout_map(Path(lines_path), None)
    raw_pages = {int(p.get("page") or 0): p for p in (raw_map.get("pages", []) or [])}
    struct_pages = {int(p.get("page") or 0): p for p in (base["pageStructure"].get("pages", []) or [])}

    reports: list[dict[str, Any]] = []
    rail_count = 0
    raw_in_rails = 0
    rails_with_multiple_raw_objects = 0

    for page_report in base["layoutClusterEvidence"].get("pageReports", []) or []:
        page_no = int(page_report.get("page") or 0)
        raw_page = raw_pages.get(page_no)
        struct_page = struct_pages.get(page_no)
        if not raw_page or not struct_page:
            continue
        width_px = float(raw_page.get("page_width_px") or 1.0)
        height_px = float(raw_page.get("page_height_px") or 1.0)
        width_pt = float(struct_page.get("width_pt") or page_width_pt)
        height_pt = float(struct_page.get("height_pt") or (width_pt * height_px / max(1.0, width_px)))
        sx = width_pt / max(1.0, width_px)
        sy = height_pt / max(1.0, height_px)
        active = [float(v) for v in (page_report.get("activeArea") or [0.0, 0.0, width_pt, height_pt])]
        flow_by_id = {str(x.get("id") or ""): x for x in (struct_page.get("flow", []) or [])}
        main_by_id = {str(x.get("id") or ""): x for x in (page_report.get("mainLaneClusters", []) or [])}
        raw_objects = list(raw_page.get("objects", []) or [])
        by_id = {str(x.get("id") or ""): x for x in raw_objects if x.get("id")}

        page_rails: list[dict[str, Any]] = []
        for candidate in page_report.get("sidebarCalloutCandidates", []) or []:
            if str(candidate.get("confidence") or "") != "high":
                continue
            side_item = flow_by_id.get(str(candidate.get("sideItemId") or ""))
            main_lane = main_by_id.get(str(candidate.get("mainLaneId") or ""))
            if not side_item or not main_lane:
                continue
            side_box = [float(v) for v in (side_item.get("bbox") or [])]
            main_box = [float(v) for v in (main_lane.get("bbox") or [])]
            if len(side_box) != 4 or len(main_box) != 4:
                continue
            rail = _rail_box(side_box, main_box, str(candidate.get("side") or "left"), active)
            members: list[dict[str, Any]] = []
            for raw in raw_objects:
                box = _box_list_from_raw(raw, sx, sy)
                if box is None:
                    continue
                accepted, xr, yr = _belongs_to_rail(box, rail)
                if not accepted:
                    continue
                raw_id = str(raw.get("id") or "")
                members.append({
                    "id": raw_id,
                    "line": raw.get("line"),
                    "type": raw.get("type"),
                    "subtype": raw.get("subtype"),
                    "parentId": raw.get("parent_id"),
                    "childrenIds": list(raw.get("children_ids") or []),
                    "ancestors": _ancestry(raw, by_id),
                    "column": raw.get("column"),
                    "selectedLabels": list(raw.get("selected_labels") or []),
                    "bboxPt": [round(v, 3) for v in box],
                    "xCoverageOfObject": round(xr, 4),
                    "yCoverageOfObject": round(yr, 4),
                    "textPreview": _text(raw)[:180],
                })
            members.sort(key=lambda x: (x["bboxPt"][1], x["bboxPt"][0], int(x.get("line") or 0)))
            rail_count += 1
            raw_in_rails += len(members)
            if len(members) >= 2:
                rails_with_multiple_raw_objects += 1
            type_counts: dict[str, int] = {}
            for member in members:
                key = str(member.get("type") or "")
                type_counts[key] = type_counts.get(key, 0) + 1
            page_rails.append({
                "side": candidate.get("side"),
                "railBBox": [round(v, 3) for v in rail],
                "sourceSidebarItemId": candidate.get("sideItemId"),
                "mainLaneId": candidate.get("mainLaneId"),
                "rawObjectCount": len(members),
                "rawTypeCounts": dict(sorted(type_counts.items())),
                "rawObjects": members,
                "rawUnionBBox": [round(v, 3) for v in (_union([m["bboxPt"] for m in members]) or rail)],
            })
        reports.append({
            "page": page_no,
            "railCount": len(page_rails),
            "rails": page_rails,
        })

    return {
        "version": VERSION,
        "rendererDecision": "deferred",
        "layoutAuthority": "v3-persistent-main-lane-side-rail",
        "rawLinesAuthorityForRailMembers": True,
        "groupedSidebarIsNotRailContainer": True,
        "pageReports": reports,
        "summary": {
            "pageCount": len(reports),
            "railCount": rail_count,
            "rawObjectsInsideRails": raw_in_rails,
            "railsWithMultipleRawObjects": rails_with_multiple_raw_objects,
            "pagesWithRails": [r["page"] for r in reports if r["railCount"]],
        },
    }


__all__ = ["build_lines_first_side_rail_decomposition_probe_contract"]
