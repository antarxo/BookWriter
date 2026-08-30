from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .lines_only_region_sweep_contract import build_lines_only_region_sweep_contract

VERSION = "lines-only-region-flow-contract-0.1"
_EPS = 0.5


def _column_box(column: dict[str, Any]) -> list[float] | None:
    try:
        box = [float(column[k]) for k in ("x0", "y0", "x1", "y1")]
    except (KeyError, TypeError, ValueError):
        return None
    return box if box[2] > box[0] and box[3] > box[1] else None


def _flow_box(item: dict[str, Any]) -> list[float] | None:
    box = item.get("bbox")
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        values = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    return values if values[2] > values[0] and values[3] > values[1] else None


def _overlap_y(box: list[float], y0: float, y1: float) -> float:
    return max(0.0, min(box[3], y1) - max(box[1], y0))


def _overlap_x(a: list[float], b: list[float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def _active_area(page: dict[str, Any]) -> list[float]:
    main = page.get("main_column") if isinstance(page.get("main_column"), dict) else {}
    try:
        box = [float(main[k]) for k in ("x0", "y0", "x1", "y1")]
        if box[2] > box[0] and box[3] > box[1]:
            return box
    except (KeyError, TypeError, ValueError):
        pass

    flow_boxes = [_flow_box(item) for item in (page.get("flow", []) or []) if item.get("type") == "text"]
    flow_boxes = [box for box in flow_boxes if box]
    if flow_boxes:
        return [
            min(b[0] for b in flow_boxes), min(b[1] for b in flow_boxes),
            max(b[2] for b in flow_boxes), max(b[3] for b in flow_boxes),
        ]
    return [0.0, 0.0, float(page.get("width_pt") or 0.0), float(page.get("height_pt") or 0.0)]


def _flow_aware_regions(page: dict[str, Any]) -> list[dict[str, Any]]:
    page_no = int(page.get("page") or 0)
    active_area = _active_area(page)
    active_width = max(1.0, active_area[2] - active_area[0])

    columns: list[dict[str, Any]] = []
    for index, column in enumerate(page.get("columns", []) or []):
        box = _column_box(column)
        if box:
            columns.append({"index": index, "id": column.get("id"), "box": box})

    flow: list[dict[str, Any]] = []
    for item in page.get("flow", []) or []:
        if item.get("type") != "text":
            continue
        box = _flow_box(item)
        if box:
            flow.append({"id": str(item.get("id") or ""), "box": box, "semantic": item.get("semantic_type")})

    # Boundaries come from both Lines column topology and actual grouped content.
    # This allows spanning/full-width blocks to create their own vertical bands.
    boundaries = {round(active_area[1], 3), round(active_area[3], 3)}
    for column in columns:
        boundaries.add(round(column["box"][1], 3))
        boundaries.add(round(column["box"][3], 3))
    for item in flow:
        boundaries.add(round(item["box"][1], 3))
        boundaries.add(round(item["box"][3], 3))
    ys = sorted(boundaries)

    raw: list[dict[str, Any]] = []
    for y0, y1 in zip(ys, ys[1:]):
        if y1 - y0 <= _EPS:
            continue
        band_height = y1 - y0
        active_columns = [c for c in columns if _overlap_y(c["box"], y0, y1) >= max(_EPS, band_height * 0.5)]
        active_columns.sort(key=lambda c: (c["box"][0], c["box"][2]))
        active_flow = [f for f in flow if _overlap_y(f["box"], y0, y1) > _EPS]
        if not active_columns and not active_flow:
            continue

        full_width_flow: list[str] = []
        spanning_flow: list[str] = []
        column_flow: list[str] = []
        unassigned_flow: list[str] = []

        for item in active_flow:
            box = item["box"]
            item_width = max(1.0, box[2] - box[0])
            active_coverage = _overlap_x(box, active_area) / item_width
            width_ratio = item_width / active_width
            if active_coverage >= 0.85 and width_ratio >= 0.72:
                full_width_flow.append(item["id"])
                continue

            hits = [c for c in active_columns if _overlap_x(box, c["box"]) / item_width >= 0.45]
            if len(hits) >= 2:
                spanning_flow.append(item["id"])
            elif len(hits) == 1:
                column_flow.append(item["id"])
            else:
                unassigned_flow.append(item["id"])

        if full_width_flow and not column_flow and not spanning_flow:
            kind = "full-width-flow-candidate"
        elif len(active_columns) >= 2:
            kind = "multi-column-candidate"
        elif len(active_columns) == 1:
            kind = "single-column-candidate"
        elif active_flow:
            kind = "flow-only-candidate"
        else:
            kind = "unassigned-band"

        signature = (
            kind,
            tuple(c["index"] for c in active_columns),
            bool(full_width_flow),
            bool(spanning_flow),
        )
        raw.append({
            "y0": y0,
            "y1": y1,
            "signature": signature,
            "kind": kind,
            "columns": active_columns,
            "flow": active_flow,
            "fullWidthFlowItemIds": full_width_flow,
            "spanningFlowItemIds": spanning_flow,
            "columnFlowItemIds": column_flow,
            "unassignedFlowItemIds": unassigned_flow,
        })

    # Merge only adjacent bands with exactly the same topology class/signature.
    merged: list[dict[str, Any]] = []
    for band in raw:
        if merged and merged[-1]["signature"] == band["signature"] and abs(merged[-1]["y1"] - band["y0"]) <= _EPS:
            merged[-1]["y1"] = band["y1"]
            for key in ("fullWidthFlowItemIds", "spanningFlowItemIds", "columnFlowItemIds", "unassignedFlowItemIds"):
                merged[-1][key] = list(dict.fromkeys([*(merged[-1].get(key) or []), *(band.get(key) or [])]))
            merged[-1]["flow"] = list({f["id"]: f for f in [*(merged[-1].get("flow") or []), *(band.get("flow") or [])]}.values())
        else:
            merged.append(deepcopy(band))

    regions: list[dict[str, Any]] = []
    for i, band in enumerate(merged):
        cols = band["columns"]
        flow_boxes = [f["box"] for f in band.get("flow", [])]
        boxes = [c["box"] for c in cols] + flow_boxes
        x0 = min((b[0] for b in boxes), default=active_area[0])
        x1 = max((b[2] for b in boxes), default=active_area[2])
        regions.append({
            "id": f"region-{page_no}-{i}",
            "kind": band["kind"],
            "bbox": [round(x0, 3), round(float(band["y0"]), 3), round(x1, 3), round(float(band["y1"]), 3)],
            "columnIndices": [c["index"] for c in cols],
            "columnIds": [c["id"] for c in cols],
            "columnCount": len(cols),
            "flowItemIds": [f["id"] for f in band.get("flow", []) if f.get("id")],
            "fullWidthFlowItemIds": band.get("fullWidthFlowItemIds") or [],
            "spanningFlowItemIds": band.get("spanningFlowItemIds") or [],
            "columnFlowItemIds": band.get("columnFlowItemIds") or [],
            "unassignedFlowItemIds": band.get("unassignedFlowItemIds") or [],
            "source": "mathpix-lines-column-plus-flow-y-sweep",
            "rendererDecision": "deferred",
        })
    return regions


def build_lines_only_region_flow_contract(lines_path: Path, page_width_pt: float = 595.276) -> dict[str, Any]:
    """LINES_ONLY_L2.3: flow-aware region topology, renderer still deferred."""
    result = deepcopy(build_lines_only_region_sweep_contract(Path(lines_path), page_width_pt=page_width_pt))
    page_structure = result["pageStructure"]
    layout_spine = result["pageLayoutSpine"]

    topology_by_page: dict[int, list[dict[str, Any]]] = {}
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        regions = _flow_aware_regions(page)
        topology_by_page[page_no] = regions
        page["region_topology"] = regions
        page["layout_mode"] = "single_column"

    layout_spine["version"] = VERSION
    layout_spine["regionTopologyByPage"] = topology_by_page
    layout_spine["policy"] = (
        "LINES_ONLY_L2.3 preserves L2/L2.1 content and normal-flow rendering. Region boundaries are inferred "
        "from both Mathpix column start/end events and grouped Lines flow boxes. Full-width/spanning evidence is "
        "measured against the Lines-derived active content area. Rendering remains deferred."
    )
    layout_spine["l23Control"] = {
        "rendererUnchangedFromL21": True,
        "positionedFramesDisabled": True,
        "regionInference": "mathpix-column-plus-flow-y-sweep",
        "activeAreaAuthority": "mathpix-lines",
        "regionRenderer": "deferred",
    }

    page_structure["version"] = VERSION
    page_structure["source"] = "mathpix-lines-only-l2.3"
    page_structure["policy"] = (
        "Lines-only L2.3: same grouped content; topology uses column geometry plus flow geometry; no Word layout decisions yet."
    )

    result["version"] = VERSION
    summary = result.get("summary") or {}
    all_regions = [region for regions in topology_by_page.values() for region in regions]
    summary["regionCount"] = len(all_regions)
    summary["multiColumnCandidateCount"] = sum(r.get("kind") == "multi-column-candidate" for r in all_regions)
    summary["fullWidthRegionCount"] = sum(r.get("kind") == "full-width-flow-candidate" for r in all_regions)
    summary["singleColumnRegionCount"] = sum(r.get("kind") == "single-column-candidate" for r in all_regions)
    summary["flowOnlyRegionCount"] = sum(r.get("kind") == "flow-only-candidate" for r in all_regions)
    summary["spanningFlowItemCount"] = sum(len(r.get("spanningFlowItemIds") or []) for r in all_regions)
    summary["fullWidthFlowItemCount"] = len({item for r in all_regions for item in (r.get("fullWidthFlowItemIds") or [])})
    summary["unassignedFlowItemCount"] = len({item for r in all_regions for item in (r.get("unassignedFlowItemIds") or [])})
    summary["regionsByPage"] = {str(page): len(regions) for page, regions in topology_by_page.items()}
    result["summary"] = summary
    return result


__all__ = ["build_lines_only_region_flow_contract"]
