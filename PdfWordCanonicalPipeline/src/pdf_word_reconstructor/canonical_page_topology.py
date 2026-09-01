from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


VERSION = "canonical-page-topology-0.2"


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


def _first_line_number(block: dict[str, Any]) -> int:
    geom = block.get("geometry") if isinstance(block.get("geometry"), dict) else {}
    line_numbers = geom.get("lineNumbers") if isinstance(geom.get("lineNumbers"), list) else []
    return min((int(v) for v in line_numbers if isinstance(v, (int, float))), default=10**9)


def build_page_topology(
    blocks: list[dict[str, Any]],
    physical_page: int | None,
    page_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe page-local canonical flows without assigning Word semantics.

    Zone membership comes only from the Lines-derived ``geometry.zoneId`` already
    attached to canonical blocks. Inside a zone, source Lines line numbers are the
    primary local-order witness; geometry is only a deterministic tie-break.

    Optional canonical page evidence may corroborate the physical zone envelope
    and recovered frame. It never changes block membership, semantic type, local
    source order, cross-zone reading order, or Word realization.
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

    physical_zone_by_id = {
        str(zone.get("zoneId") or ""): zone
        for zone in (page_evidence or {}).get("zones", []) or []
        if zone.get("zoneId")
    }

    zones: list[dict[str, Any]] = []
    for zone_id, members in by_zone.items():
        def key(block: dict[str, Any]) -> tuple[int, float, float, str]:
            bbox = _block_bbox(block) or [0.0, 0.0, 0.0, 0.0]
            return (_first_line_number(block), bbox[1], bbox[0], str(block.get("id") or ""))

        ordered = sorted(members, key=key)
        boxes = [_block_bbox(block) for block in ordered]
        semantic_counts = Counter(
            str(((block.get("semantic") or {}).get("type") or "unknown"))
            for block in ordered
        )
        line_numbers = [_first_line_number(block) for block in ordered]
        line_numbers = [value for value in line_numbers if value < 10**9]
        physical_zone = physical_zone_by_id.get(zone_id) or {}
        zones.append({
            "zoneId": zone_id,
            "canonicalCoverageBBoxPx": _bbox_union([b for b in boxes if b]),
            "physicalZoneBBoxPx": physical_zone.get("bboxPx"),
            "physicalZoneSource": physical_zone.get("source"),
            "blockCount": len(ordered),
            "semanticTypeCounts": dict(sorted(semantic_counts.items())),
            "blockIds": [str(block.get("id") or "") for block in ordered],
            "localFlowOrder": [str(block.get("id") or "") for block in ordered],
            "localFlowLineNumberRange": [min(line_numbers), max(line_numbers)] if line_numbers else None,
            "localFlowSource": "mathpix-lines-source-line-numbers-with-geometry-tiebreak",
            "localFlowConfidence": "high" if line_numbers else "medium",
            "wordRealization": None,
        })

    # Zone array order is diagnostic only; it is never cross-zone reading order.
    zones.sort(key=lambda zone: (
        float((zone.get("physicalZoneBBoxPx") or zone.get("canonicalCoverageBBoxPx") or [0, 0, 0, 0])[0]),
        float((zone.get("physicalZoneBBoxPx") or zone.get("canonicalCoverageBBoxPx") or [0, 0, 0, 0])[1]),
        str(zone.get("zoneId") or ""),
    ))

    unzoned_ordered = sorted(
        unzoned,
        key=lambda block: (_first_line_number(block), tuple((_block_bbox(block) or [0.0, 0.0])[:2])),
    )
    unzoned_ids = [str(block.get("id") or "") for block in unzoned_ordered]

    semantic_zone_ids = {str(zone.get("zoneId") or "") for zone in zones}
    physical_zone_ids = set(physical_zone_by_id)
    recovery = (page_evidence or {}).get("profileRecoveryEvidence") or {}
    recovered_relation = (page_evidence or {}).get("recoveryZoneRelationship") or {}

    return {
        "version": VERSION,
        "physicalPage": physical_page,
        "source": "mathpix-lines-canonical-block-zone-provenance",
        "zoneCount": len(zones),
        "zones": zones,
        "unzonedBlockIds": unzoned_ids,
        "zoneCoverageAudit": {
            "semanticZoneIds": sorted(semantic_zone_ids),
            "physicalZoneIds": sorted(physical_zone_ids),
            "semanticZonesMissingPhysicalWitness": sorted(semantic_zone_ids - physical_zone_ids),
            "physicalZonesWithoutCanonicalBlocks": sorted(physical_zone_ids - semantic_zone_ids),
            "allSemanticZonesPhysicallyWitnessed": bool(semantic_zone_ids) and semantic_zone_ids <= physical_zone_ids,
        },
        "recoveredFrameEvidence": {
            "status": recovery.get("status"),
            "bboxPx": recovery.get("bodyConstraintPx"),
            "source": recovery.get("source"),
            "reconciliation": recovery.get("frameReconciliation"),
            "zoneRelationship": recovered_relation.get("classification"),
            "zoneRelationshipConfidence": recovered_relation.get("confidence"),
            "rendererMeaning": recovered_relation.get("rendererMeaning"),
            "wordRealization": None,
        } if page_evidence else None,
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
            "zoneMembership": "preserve-lines-column-ancestor-provenance-no-rematching",
            "zoneLocalFlow": "source-lines-line-numbers-primary-geometry-tiebreak-only",
            "physicalZoneEvidence": "corroboration-only-never-reassigns-canonical-blocks",
            "crossZoneOrder": "must-not-be-inferred-from-x-position-array-order-or-zone-array-order",
            "wordRealization": "forbidden-at-this-stage",
        },
    }


__all__ = ["VERSION", "build_page_topology"]
