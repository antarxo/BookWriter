from __future__ import annotations

from collections import defaultdict
from typing import Any


VERSION = "canonical-page-topology-0.1"


def _bbox_union(boxes: list[list[float]]) -> list[float] | None:
    valid = [b for b in boxes if isinstance(b, list) and len(b) == 4]
    if not valid:
        return None
    return [
        min(float(b[0]) for b in valid),
        min(float(b[1]) for b in valid),
        max(float(b[2]) for b in valid),
        max(float(b[3]) for b in valid),
    ]


def _block_bbox(block: dict[str, Any]) -> list[float] | None:
    geom = block.get("geometry") if isinstance(block.get("geometry"), dict) else {}
    bbox = geom.get("bboxPx")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        return [float(v) for v in bbox]
    except (TypeError, ValueError):
        return None


def build_page_topology(blocks: list[dict[str, Any]], physical_page: int | None) -> dict[str, Any]:
    """Describe page-local geometric flows without assigning Word semantics.

    Zones come only from the Lines-derived ``geometry.zoneId`` already attached
    to canonical blocks.  Local flow is ordered inside each zone by geometric
    top-to-bottom position with Lines line numbers as a deterministic tie-break.

    Critically, this function does *not* infer a semantic reading order between
    independent zones.  Cross-zone reading order remains unresolved unless a
    later evidence layer supplies an explicit witness.
    """
    page_blocks = [
        block for block in blocks
        if physical_page is None
        or int(((block.get("pageAssignment") or {}).get("physicalPage") or 0)) == physical_page
    ]

    by_zone: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unzoned: list[dict[str, Any]] = []
    for block in page_blocks:
        geom = block.get("geometry") if isinstance(block.get("geometry"), dict) else {}
        zone_id = str(geom.get("zoneId") or "").strip()
        if zone_id:
            by_zone[zone_id].append(block)
        else:
            unzoned.append(block)

    zones: list[dict[str, Any]] = []
    for zone_id, members in by_zone.items():
        def key(block: dict[str, Any]) -> tuple[float, float, int, str]:
            bbox = _block_bbox(block) or [0.0, 0.0, 0.0, 0.0]
            geom = block.get("geometry") if isinstance(block.get("geometry"), dict) else {}
            line_numbers = geom.get("lineNumbers") if isinstance(geom.get("lineNumbers"), list) else []
            first_line = min((int(v) for v in line_numbers if isinstance(v, (int, float))), default=10**9)
            return (bbox[1], bbox[0], first_line, str(block.get("id") or ""))

        ordered = sorted(members, key=key)
        boxes = [_block_bbox(block) for block in ordered]
        zones.append({
            "zoneId": zone_id,
            "bboxPx": _bbox_union([b for b in boxes if b]),
            "blockIds": [str(block.get("id") or "") for block in ordered],
            "localFlowOrder": [str(block.get("id") or "") for block in ordered],
            "localFlowSource": "mathpix-lines-zone-and-geometry",
            "localFlowConfidence": "high",
            "wordRealization": None,
        })

    # Geometric zone ordering is diagnostic only.  It must never be interpreted
    # as semantic reading order between zones.
    zones.sort(key=lambda zone: (
        float((zone.get("bboxPx") or [0, 0, 0, 0])[0]),
        float((zone.get("bboxPx") or [0, 0, 0, 0])[1]),
        str(zone.get("zoneId") or ""),
    ))

    unzoned_ids = [str(block.get("id") or "") for block in sorted(
        unzoned,
        key=lambda block: tuple((_block_bbox(block) or [0.0, 0.0, 0.0, 0.0])[:2]),
    )]

    return {
        "version": VERSION,
        "physicalPage": physical_page,
        "source": "mathpix-lines-canonical-block-geometry",
        "zoneCount": len(zones),
        "zones": zones,
        "unzonedBlockIds": unzoned_ids,
        "crossZoneReadingOrder": {
            "status": "unresolved" if len(zones) > 1 else "not-needed",
            "order": None,
            "reason": (
                "multiple-independent-geometric-zones-no-semantic-order-witness"
                if len(zones) > 1
                else "single-zone-or-no-zone"
            ),
            "wordRealization": None,
        },
        "policy": {
            "blocksArrayOrder": "non-authoritative-for-cross-zone-reading-order",
            "zoneLocalFlow": "geometry-supported",
            "crossZoneOrder": "must-not-be-inferred-from-x-position-or-array-order",
            "wordRealization": "forbidden-at-this-stage",
        },
    }


__all__ = ["VERSION", "build_page_topology"]
