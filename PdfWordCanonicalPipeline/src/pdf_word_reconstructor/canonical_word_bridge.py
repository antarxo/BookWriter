from __future__ import annotations

from pathlib import Path
from typing import Any

from .source_neutral_builder_adapter import build_source_neutral_document


VERSION = "canonical-word-bridge-0.1"


def _pt_scale(page_evidence: dict[str, Any] | None) -> tuple[float, float]:
    page_evidence = page_evidence or {}
    page_size = page_evidence.get("pageSizePx") or page_evidence.get("pageDimensionsPx") or {}
    width_px = float(page_size.get("width") or page_size.get("widthPx") or 2067.0)
    height_px = float(page_size.get("height") or page_size.get("heightPx") or 2924.0)
    return 595.276 / width_px, 841.89 / height_px


def _bbox_pt(bbox_px: list[float] | tuple[float, ...] | None, sx: float, sy: float) -> list[float] | None:
    if not bbox_px or len(bbox_px) != 4:
        return None
    return [
        round(float(bbox_px[0]) * sx, 3),
        round(float(bbox_px[1]) * sy, 3),
        round(float(bbox_px[2]) * sx, 3),
        round(float(bbox_px[3]) * sy, 3),
    ]


def build_canonical_word_artifacts(canonical: dict[str, Any], *, target_page: int) -> dict[str, Any]:
    """Translate already-resolved canonical evidence to the existing source-neutral builder contract.

    This adapter is intentionally non-inferential: it performs no MMD↔Lines rematching, no
    margin/column rediscovery, and no fallback to legacy Lines column interpretation.
    """
    topology = canonical.get("pageTopology") or {}
    page_evidence = canonical.get("pageEvidence") or {}
    sx, sy = _pt_scale(page_evidence)

    zones = {str(z.get("zoneId")): z for z in topology.get("zones") or [] if z.get("zoneId")}
    cross_order = (topology.get("crossZoneReadingOrder") or {}).get("order") or []
    zone_rank = {str(zone_id): i for i, zone_id in enumerate(cross_order)}

    blocks = [
        b for b in canonical.get("blocks") or []
        if int((b.get("pageAssignment") or {}).get("physicalPage") or 0) == int(target_page)
    ]
    blocks.sort(key=lambda b: (
        zone_rank.get(str((b.get("geometry") or {}).get("zoneId")), 10**6),
        ((b.get("geometry") or {}).get("bboxPx") or [0, 0, 0, 0])[1],
        ((b.get("geometry") or {}).get("bboxPx") or [0, 0, 0, 0])[0],
    ))

    flow_items: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        block_id = str(block.get("id") or f"canonical-{index:05d}")
        geom = block.get("geometry") or {}
        bbox = _bbox_pt(geom.get("bboxPx"), sx, sy)
        zone_id = str(geom.get("zoneId") or "")
        semantic = str((block.get("semantic") or {}).get("type") or "paragraph")
        content = block.get("content") or {}
        text = str(content.get("text") or "")
        if bbox is None or not zone_id:
            unresolved.append({"blockId": block_id, "reason": "missing-canonical-geometry-or-zone"})
            continue

        output_kind = {
            "heading": "heading",
            "equation": "equation",
            "figure": "figure",
        }.get(semantic, "paragraph")
        item_id = f"canonical-word-{index:04d}"
        placement = {
            "page": target_page,
            "bbox": bbox,
            "zoneId": zone_id,
            "source": "canonical-evidence-bridge",
        }
        rows.append({
            "id": item_id,
            "markdownId": ((content.get("markdownIds") or [None])[0]),
            "markdownType": semantic,
            "markdownText": text,
            "status": "build-ready",
            "outputKind": output_kind,
            "placement": placement,
            "pdfGeometry": {"bbox": bbox, "source": "canonical-lines-geometry"},
            "layout": {"mode": "canonical-zone-flow", "zoneId": zone_id},
            "canonicalBlockId": block_id,
        })
        flow_items.append({
            "id": item_id,
            "type": output_kind,
            "semantic": semantic,
            "text": text,
            "bbox": bbox,
            "zoneId": zone_id,
            "canonicalBlockId": block_id,
            "source": "canonical-evidence-bridge",
        })

    recovered = topology.get("recoveredFrameEvidence") or {}
    frame_pt = _bbox_pt(recovered.get("bboxPx"), sx, sy)
    page_structure = {
        "version": VERSION,
        "policy": {
            "matching": "forbidden",
            "layoutInference": "forbidden",
            "legacyLinesColumnsFallback": "forbidden",
            "source": "canonical-evidence-only",
        },
        "pages": [{
            "page": target_page,
            "width_pt": 595.276,
            "height_pt": 841.89,
            "layout_mode": "canonical-zones",
            "canonicalFrameBBoxPt": frame_pt,
            "zones": [
                {
                    "id": zone_id,
                    "bbox": _bbox_pt((zone.get("physicalZoneBBoxPx") or zone.get("canonicalCoverageBBoxPx")), sx, sy),
                    "source": "canonical-page-topology",
                }
                for zone_id, zone in zones.items()
            ],
            "flow": flow_items,
        }],
    }
    page_layout_spine = {
        "version": VERSION,
        "rows": rows,
        "summary": {
            "rowCount": len(rows),
            "unresolvedCount": len(unresolved),
            "source": "canonical-evidence-only",
        },
    }
    return {
        "version": VERSION,
        "pageStructure": page_structure,
        "pageLayoutSpine": page_layout_spine,
        "unresolved": unresolved,
        "summary": {
            "targetPage": target_page,
            "canonicalBlockCount": len(blocks),
            "buildReadyCount": len(rows),
            "unresolvedCount": len(unresolved),
            "zoneCount": len(zones),
            "crossZoneOrder": cross_order,
            "frameBBoxPt": frame_pt,
        },
    }


def build_canonical_word_document(
    canonical: dict[str, Any],
    *,
    target_page: int,
    output_path: Path,
    body_size_override: float | None = None,
) -> dict[str, Any]:
    artifacts = build_canonical_word_artifacts(canonical, target_page=target_page)
    if artifacts["unresolved"]:
        raise RuntimeError(f"Canonical Word bridge unresolved: {artifacts['unresolved']}")
    report = build_source_neutral_document(
        page_structure=artifacts["pageStructure"],
        page_layout_spine=artifacts["pageLayoutSpine"],
        output_path=Path(output_path),
        body_size_override=body_size_override,
    )
    if isinstance(report, dict):
        report["canonicalWordBridge"] = artifacts["summary"]
    return {"artifacts": artifacts, "buildReport": report}
