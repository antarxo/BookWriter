from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .build_contract import build_build_contract
from .lines_only_grouped_contract import build_lines_only_grouped_contract

VERSION = "lines-only-region-contract-0.1"


def _column_box(column: dict[str, Any]) -> list[float] | None:
    try:
        box = [float(column[k]) for k in ("x0", "y0", "x1", "y1")]
    except (KeyError, TypeError, ValueError):
        return None
    return box if box[2] > box[0] and box[3] > box[1] else None


def _vertical_overlap(a: list[float], b: list[float]) -> float:
    overlap = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    denom = max(1.0, min(a[3] - a[1], b[3] - b[1]))
    return overlap / denom


def _infer_region_topology(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe Lines column topology without turning columns into Word frames.

    L2.1 is intentionally diagnostic.  A Mathpix ``column`` is retained as layout
    evidence, but is not assumed to be a floating Word text box.  Columns that
    substantially overlap vertically are grouped into one candidate region.
    """
    columns = []
    for index, column in enumerate(page.get("columns", []) or []):
        box = _column_box(column)
        if box:
            columns.append({"index": index, "id": column.get("id"), "box": box})

    groups: list[list[dict[str, Any]]] = []
    for column in columns:
        attached = None
        for group in groups:
            if any(_vertical_overlap(column["box"], other["box"]) >= 0.55 for other in group):
                attached = group
                break
        if attached is None:
            groups.append([column])
        else:
            attached.append(column)

    regions = []
    for i, group in enumerate(groups):
        group.sort(key=lambda item: item["box"][0])
        boxes = [item["box"] for item in group]
        bbox = [
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        ]
        regions.append({
            "id": f"region-{page.get('page')}-{i}",
            "kind": "multi-column-candidate" if len(group) >= 2 else "single-column-candidate",
            "bbox": [round(v, 3) for v in bbox],
            "columnIndices": [item["index"] for item in group],
            "columnIds": [item["id"] for item in group],
            "columnCount": len(group),
            "source": "mathpix-lines-column-topology",
            "rendererDecision": "deferred",
        })
    return regions


def _normalise_renderer_decisions(artifacts: dict[str, Any]) -> dict[str, Any]:
    """Remove L2's narrow-column => positioned-frame inference.

    This is the controlled L2.1 test: preserve content, grouping, geometry, font size,
    active-area margins and column evidence, but render every text unit in normal flow.
    Region topology is emitted for diagnosis and is not yet converted into Word sections.
    """
    result = deepcopy(artifacts)
    page_structure = result["pageStructure"]
    layout_spine = result["pageLayoutSpine"]

    topology_by_page: dict[int, list[dict[str, Any]]] = {}
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        topology = _infer_region_topology(page)
        topology_by_page[page_no] = topology
        page["region_topology"] = topology

        # Critical L2.1 control: columns remain evidence only.  The old native builder
        # must not interpret page-level column count or narrow columns as floating text.
        page["layout_mode"] = "single_column"
        for item in page.get("flow", []) or []:
            if item.get("type") == "text":
                item["spanning"] = False
                item["region_topology_only"] = True

    changed = 0
    for row in layout_spine.get("rows", []) or []:
        layout = row.get("layout") or {}
        contract = row.get("layoutContract") or {}
        word = row.get("wordParagraph") or contract.get("wordParagraph") or {}

        if layout.get("spanning") or contract.get("placement") == "positioned-text-frame":
            changed += 1
        layout["spanning"] = False
        layout["matchMode"] = "lines-only-l2.1-region-topology"

        contract["layoutMode"] = "lines-region-topology"
        contract["placement"] = "normal-flow"
        column = contract.get("column") or {}
        column["spanning"] = False
        contract["column"] = column
        builder_use = contract.get("builderUse") or {}
        builder_use["safeForFlowOrdering"] = True
        builder_use["requiresPositionedFrame"] = False
        contract["builderUse"] = builder_use

        if isinstance(word, dict):
            word["placement"] = "normal-flow"
            geometry = word.get("geometry") or {}
            geometry["leftIndentPt"] = None
            geometry["rightIndentPt"] = None
            word["geometry"] = geometry
            page_columns = word.get("pageColumns") or {}
            page_columns["layoutMode"] = "lines-region-topology-evidence-only"
            word["pageColumns"] = page_columns

    layout_spine["version"] = VERSION
    layout_spine["policy"] = (
        "LINES_ONLY_L2.1 keeps Mathpix column geometry as region-topology evidence, "
        "but removes the L2 narrow-column => positioned-text-frame inference. "
        "All text is rendered in normal flow for this controlled probe."
    )
    layout_spine["regionTopologyByPage"] = topology_by_page
    layout_spine["l21Control"] = {
        "positionedFramesDisabled": True,
        "pageLevelColumnsDisabledForRenderer": True,
        "columnEvidencePreserved": True,
        "regionTopologyRenderer": "deferred",
        "formerPositionedUnitCount": changed,
    }

    page_structure["version"] = VERSION
    page_structure["source"] = "mathpix-lines-only-l2.1"
    page_structure["policy"] = (
        "Lines-only L2.1: L2 grouping, geometry, margins and font-size retained; "
        "Mathpix columns are topology evidence, not automatic Word floating frames."
    )

    build_contract = build_build_contract(layout_spine)
    build_contract["sourceAuthority"] = {
        "content": "mathpix-lines",
        "layout": "mathpix-lines-region-topology",
        "typography": "mathpix-lines",
        "nativeDonor": None,
    }
    result["buildContract"] = build_contract
    result["version"] = VERSION
    summary = result.get("summary") or {}
    summary["formerPositionedUnitCount"] = changed
    summary["regionCount"] = sum(len(v) for v in topology_by_page.values())
    summary["multiColumnCandidateCount"] = sum(
        1 for regions in topology_by_page.values() for region in regions
        if region.get("kind") == "multi-column-candidate"
    )
    summary["buildReadyCount"] = int((build_contract.get("summary") or {}).get("readyCount") or 0)
    summary["buildUnresolvedCount"] = int((build_contract.get("summary") or {}).get("unresolvedCount") or 0)
    result["summary"] = summary
    return result


def build_lines_only_region_contract(lines_path: Path, page_width_pt: float = 595.276) -> dict[str, Any]:
    l2 = build_lines_only_grouped_contract(Path(lines_path), page_width_pt=page_width_pt)
    return _normalise_renderer_decisions(l2)


__all__ = ["build_lines_only_region_contract"]
