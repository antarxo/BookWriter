from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .lines_only_region_contract import build_lines_only_region_contract

VERSION = "lines-only-region-sweep-contract-0.1"
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


def _region_kind(active: list[dict[str, Any]], page_width: float) -> str:
    if len(active) >= 2:
        return "multi-column-candidate"
    if not active:
        return "unassigned-band"
    box = active[0]["box"]
    width = box[2] - box[0]
    if width >= page_width * 0.68:
        return "full-width-flow-candidate"
    return "single-narrow-column-candidate"


def _sweep_regions(page: dict[str, Any]) -> list[dict[str, Any]]:
    page_no = int(page.get("page") or 0)
    page_width = float(page.get("width_pt") or 0.0)
    columns: list[dict[str, Any]] = []
    for index, column in enumerate(page.get("columns", []) or []):
        box = _column_box(column)
        if box:
            columns.append({
                "index": index,
                "id": column.get("id"),
                "box": box,
            })

    if not columns:
        return []

    # A region boundary is a real Lines column start/end event.  Unlike L2.1,
    # no transitive overlap clustering is allowed.
    boundaries = sorted({round(c["box"][1], 3) for c in columns} | {round(c["box"][3], 3) for c in columns})
    raw_bands: list[dict[str, Any]] = []
    for y0, y1 in zip(boundaries, boundaries[1:]):
        if y1 - y0 <= _EPS:
            continue
        mid = (y0 + y1) / 2.0
        active = [c for c in columns if c["box"][1] - _EPS <= mid <= c["box"][3] + _EPS]
        if not active:
            continue
        active.sort(key=lambda c: (c["box"][0], c["box"][2]))
        signature = tuple(c["index"] for c in active)
        raw_bands.append({
            "y0": y0,
            "y1": y1,
            "signature": signature,
            "columns": active,
        })

    merged: list[dict[str, Any]] = []
    for band in raw_bands:
        if merged and merged[-1]["signature"] == band["signature"] and abs(merged[-1]["y1"] - band["y0"]) <= _EPS:
            merged[-1]["y1"] = band["y1"]
        else:
            merged.append(dict(band))

    flow = [item for item in (page.get("flow", []) or []) if item.get("type") == "text" and _flow_box(item)]
    regions: list[dict[str, Any]] = []
    for i, band in enumerate(merged):
        active = band["columns"]
        x0 = min(c["box"][0] for c in active)
        x1 = max(c["box"][2] for c in active)
        y0 = float(band["y0"])
        y1 = float(band["y1"])

        member_ids: list[str] = []
        spanning_ids: list[str] = []
        for item in flow:
            box = _flow_box(item)
            if box is None:
                continue
            center_y = (box[1] + box[3]) / 2.0
            if y0 - _EPS <= center_y <= y1 + _EPS:
                item_id = str(item.get("id") or "")
                if item_id:
                    member_ids.append(item_id)
                # A text block crossing the horizontal union of multiple active
                # columns is evidence of a spanning block, not a third column.
                if len(active) >= 2:
                    covered = sum(max(0.0, min(box[2], c["box"][2]) - max(box[0], c["box"][0])) for c in active)
                    item_width = max(1.0, box[2] - box[0])
                    if covered / item_width >= 0.75 and box[0] <= active[0]["box"][0] + 4.0 and box[2] >= active[-1]["box"][2] - 4.0:
                        spanning_ids.append(item_id)

        regions.append({
            "id": f"region-{page_no}-{i}",
            "kind": _region_kind(active, page_width),
            "bbox": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)],
            "columnIndices": [c["index"] for c in active],
            "columnIds": [c["id"] for c in active],
            "columnCount": len(active),
            "topologySignature": [c["index"] for c in active],
            "flowItemIds": member_ids,
            "spanningFlowItemIds": spanning_ids,
            "source": "mathpix-lines-column-y-sweep",
            "rendererDecision": "deferred",
        })
    return regions


def build_lines_only_region_sweep_contract(lines_path: Path, page_width_pt: float = 595.276) -> dict[str, Any]:
    """LINES_ONLY_L2.2 topology probe.

    Preserve the successful L2.1 normal-flow control and replace only region
    inference.  Regions are derived by a vertical sweep over Mathpix column
    start/end events.  The output is diagnostic: no region is yet converted into
    a Word section, Word column set, or floating frame.
    """
    result = deepcopy(build_lines_only_region_contract(Path(lines_path), page_width_pt=page_width_pt))
    page_structure = result["pageStructure"]
    layout_spine = result["pageLayoutSpine"]

    topology_by_page: dict[int, list[dict[str, Any]]] = {}
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        regions = _sweep_regions(page)
        topology_by_page[page_no] = regions
        page["region_topology"] = regions
        page["layout_mode"] = "single_column"

    layout_spine["version"] = VERSION
    layout_spine["regionTopologyByPage"] = topology_by_page
    layout_spine["policy"] = (
        "LINES_ONLY_L2.2 preserves L2.1 normal-flow rendering and replaces transitive "
        "column-overlap clustering with a y-sweep over actual Mathpix column start/end events. "
        "Region rendering remains deferred."
    )
    layout_spine["l22Control"] = {
        "rendererUnchangedFromL21": True,
        "positionedFramesDisabled": True,
        "regionInference": "mathpix-column-y-sweep",
        "transitiveOverlapGroupingDisabled": True,
        "regionRenderer": "deferred",
    }

    page_structure["version"] = VERSION
    page_structure["source"] = "mathpix-lines-only-l2.2"
    page_structure["policy"] = (
        "Lines-only L2.2: L2.1 content/grouping/normal-flow renderer preserved; "
        "region boundaries come only from Mathpix column topology events."
    )

    result["version"] = VERSION
    summary = result.get("summary") or {}
    summary["regionCount"] = sum(len(v) for v in topology_by_page.values())
    summary["multiColumnCandidateCount"] = sum(
        1 for regions in topology_by_page.values() for region in regions
        if region.get("kind") == "multi-column-candidate"
    )
    summary["fullWidthRegionCount"] = sum(
        1 for regions in topology_by_page.values() for region in regions
        if region.get("kind") == "full-width-flow-candidate"
    )
    summary["narrowSingleRegionCount"] = sum(
        1 for regions in topology_by_page.values() for region in regions
        if region.get("kind") == "single-narrow-column-candidate"
    )
    summary["spanningFlowItemCount"] = sum(
        len(region.get("spanningFlowItemIds") or [])
        for regions in topology_by_page.values() for region in regions
    )
    result["summary"] = summary
    return result


__all__ = ["build_lines_only_region_sweep_contract"]
