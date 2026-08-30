from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .build_contract import build_build_contract
from .lines_only_region_contract import build_lines_only_region_contract


VERSION = "lines-only-region-order-contract-0.1"


def _bbox(item: dict[str, Any]) -> list[float] | None:
    box = item.get("bbox")
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        values = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    return values if values[2] > values[0] and values[3] > values[1] else None


def _region_for_item(item: dict[str, Any], regions: list[dict[str, Any]]) -> dict[str, Any] | None:
    col = item.get("column_index")
    if col is None:
        return None
    for region in regions:
        if col in (region.get("columnIndices") or []):
            return region
    return None


def _reorder_page_flow(page: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Order page flow by region bundles, without yet rendering native Word columns.

    L2.2 keeps L2.1's no-floating-frame policy.  The new controlled change is only
    ordering: a Mathpix multi-column topology region becomes one vertical bundle in
    the page sequence.  Inside the bundle, reading order is column-major
    (left column top-to-bottom, then the next column), while non-column content stays
    in geometric page order around those bundles.
    """
    flow = list(page.get("flow", []) or [])
    regions = list(page.get("region_topology", []) or [])
    region_by_id = {str(r.get("id")): r for r in regions if r.get("id")}

    bundles: dict[str, dict[str, Any]] = {}
    standalone: list[dict[str, Any]] = []

    for item in flow:
        box = _bbox(item)
        region = _region_for_item(item, regions)
        if region is None or box is None:
            standalone.append({
                "kind": "standalone",
                "anchor": (box[1], box[0]) if box else (10**9, 10**9),
                "items": [item],
            })
            continue

        rid = str(region.get("id"))
        bundle = bundles.setdefault(
            rid,
            {
                "kind": "region",
                "region": region,
                "anchor": (
                    float((region.get("bbox") or [0, 10**9, 0, 0])[1]),
                    float((region.get("bbox") or [10**9, 0, 0, 0])[0]),
                ),
                "items": [],
            },
        )
        item["region_id"] = rid
        item["region_order_mode"] = "column-major"
        bundle["items"].append(item)

    all_bundles = standalone + list(bundles.values())
    all_bundles.sort(key=lambda b: b["anchor"])

    ordered: list[dict[str, Any]] = []
    topology_order: list[dict[str, Any]] = []
    for bundle_index, bundle in enumerate(all_bundles):
        items = list(bundle["items"])
        if bundle["kind"] == "region":
            region = bundle["region"]
            col_order = {col: i for i, col in enumerate(region.get("columnIndices") or [])}
            items.sort(
                key=lambda item: (
                    col_order.get(item.get("column_index"), 10**6),
                    (_bbox(item) or [0, 10**9, 0, 0])[1],
                    (_bbox(item) or [10**9, 0, 0, 0])[0],
                )
            )
            topology_order.append({
                "bundleIndex": bundle_index,
                "regionId": region.get("id"),
                "kind": region.get("kind"),
                "bbox": region.get("bbox"),
                "columnIndices": region.get("columnIndices"),
                "flowIds": [item.get("id") for item in items],
                "rendererDecision": "ordered-bundle-normal-flow",
            })
        for item in items:
            item["region_bundle_index"] = bundle_index
            ordered.append(item)

    return ordered, topology_order


def _apply_region_order(artifacts: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(artifacts)
    page_structure = result["pageStructure"]
    layout_spine = result["pageLayoutSpine"]

    page_slot_order: dict[tuple[int, str], int] = {}
    topology_order_by_page: dict[int, list[dict[str, Any]]] = {}

    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        ordered, topology_order = _reorder_page_flow(page)
        page["flow"] = ordered
        page["region_order"] = topology_order
        page["layout_mode"] = "region_ordered_single_column"
        topology_order_by_page[page_no] = topology_order
        for local_order, item in enumerate(ordered):
            slot_id = str(item.get("id") or "")
            if slot_id:
                page_slot_order[(page_no, slot_id)] = local_order

    rows = list(layout_spine.get("rows", []) or [])
    for row in rows:
        layout = row.get("layout") or {}
        page_no = int(layout.get("page") or row.get("page") or 0)
        slot_id = str(layout.get("slotId") or "")
        local_order = page_slot_order.get((page_no, slot_id), 10**8)
        layout["flowOrder"] = local_order
        layout["matchMode"] = "lines-only-l2.2-region-ordered"
        layout["regionOrdered"] = True
        row["layout"] = layout

        contract = row.get("layoutContract") or {}
        contract["layoutMode"] = "lines-region-ordered"
        builder_use = contract.get("builderUse") or {}
        builder_use["safeForFlowOrdering"] = True
        builder_use["requiresPositionedFrame"] = False
        contract["builderUse"] = builder_use
        row["layoutContract"] = contract

    rows.sort(
        key=lambda row: (
            int((row.get("layout") or {}).get("page") or 0),
            int((row.get("layout") or {}).get("flowOrder") or 10**8),
        )
    )

    layout_order_by_slot: dict[str, int] = {}
    global_order = 0
    current_page = None
    for row in rows:
        layout = row.get("layout") or {}
        page_no = int(layout.get("page") or 0)
        slot_id = str(layout.get("slotId") or "")
        if slot_id:
            layout_order_by_slot[f"{page_no}:{slot_id}"] = global_order
        layout["wordFlowOrder"] = global_order
        row["markdownOrder"] = global_order
        global_order += 1
        current_page = page_no

    layout_spine["rows"] = rows
    layout_spine["layoutOrderBySlot"] = layout_order_by_slot
    layout_spine["regionOrderByPage"] = topology_order_by_page
    layout_spine["version"] = VERSION
    layout_spine["policy"] = (
        "LINES_ONLY_L2.2 retains L2.1 normal-flow rendering, but reconstructs page "
        "reading order from region bundles. Multi-column regions are ordered as one "
        "page-positioned bundle and read column-major internally. No PDF/Markdown/DOCX evidence."
    )

    page_structure["version"] = VERSION
    page_structure["source"] = "mathpix-lines-only-l2.2"
    page_structure["policy"] = (
        "Lines-only L2.2: region topology controls flow order; native Word multi-column "
        "rendering remains deferred and floating frames remain disabled."
    )

    build_contract = build_build_contract(layout_spine)
    build_contract["sourceAuthority"] = {
        "content": "mathpix-lines",
        "layout": "mathpix-lines-region-order",
        "typography": "mathpix-lines",
        "nativeDonor": None,
    }
    result["buildContract"] = build_contract
    result["version"] = VERSION

    summary = result.get("summary") or {}
    summary["regionOrderedPageCount"] = len(topology_order_by_page)
    summary["regionBundleCount"] = sum(len(v) for v in topology_order_by_page.values())
    summary["buildReadyCount"] = int((build_contract.get("summary") or {}).get("readyCount") or 0)
    summary["buildUnresolvedCount"] = int((build_contract.get("summary") or {}).get("unresolvedCount") or 0)
    result["summary"] = summary
    return result


def build_lines_only_region_order_contract(lines_path: Path, page_width_pt: float = 595.276) -> dict[str, Any]:
    l21 = build_lines_only_region_contract(Path(lines_path), page_width_pt=page_width_pt)
    return _apply_region_order(l21)


__all__ = ["build_lines_only_region_order_contract"]
