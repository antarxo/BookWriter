from __future__ import annotations

from pathlib import Path
from typing import Any

from .native_builder_canonical import build_canonical_native_document


VERSION = "canonical-word-bridge-0.2"


def _validate_canonical(canonical: dict[str, Any], target_page: int) -> dict[str, Any]:
    topology = canonical.get("pageTopology") or {}
    page_evidence = canonical.get("pageEvidence") or {}
    blocks = [
        block for block in canonical.get("blocks") or []
        if int((block.get("pageAssignment") or {}).get("physicalPage") or 0) == int(target_page)
    ]
    reasons: list[str] = []
    if not blocks:
        reasons.append("no-canonical-blocks-for-target-page")
    if int(topology.get("physicalPage") or 0) != int(target_page):
        reasons.append("page-topology-missing-or-wrong-page")
    if not topology.get("zones"):
        reasons.append("canonical-zones-missing")
    cross = topology.get("crossZoneReadingOrder") or {}
    if cross.get("status") not in {"resolved-by-markdown-record-order", "not-needed"}:
        reasons.append("cross-zone-reading-order-unresolved")
    recovered = topology.get("recoveredFrameEvidence") or {}
    if not recovered.get("bboxPx"):
        reasons.append("recovered-frame-missing")
    if not page_evidence:
        reasons.append("canonical-page-evidence-missing")
    block_conflicts = [
        str(block.get("id") or "")
        for block in blocks
        if (block.get("evidence") or {}).get("conflicts")
    ]
    if block_conflicts:
        reasons.append("canonical-block-conflicts-present")
    return {
        "status": "ready" if not reasons else "blocked",
        "targetPage": target_page,
        "canonicalBlockCount": len(blocks),
        "zoneCount": len(topology.get("zones") or []),
        "crossZoneReadingOrder": cross,
        "recoveredFrame": recovered,
        "conflictBlockIds": block_conflicts,
        "reasons": reasons,
        "policy": {
            "bridgeRole": "validation-and-handoff-only",
            "matching": "forbidden",
            "semanticReinterpretation": "forbidden",
            "layoutInference": "forbidden",
            "marginInference": "forbidden",
            "columnInference": "forbidden",
            "legacyFallback": "forbidden",
        },
    }


def build_canonical_word_document(
    canonical: dict[str, Any],
    *,
    target_page: int,
    output_path: Path,
    package_root: Path | None = None,
) -> dict[str, Any]:
    """Validate the completed canonical link and hand it unchanged to the canonical builder."""
    validation = _validate_canonical(canonical, target_page)
    if validation["status"] != "ready":
        raise RuntimeError(f"Canonical Word bridge blocked: {validation['reasons']}")
    build_report = build_canonical_native_document(
        canonical,
        output_path=Path(output_path),
        target_page=target_page,
        package_root=Path(package_root) if package_root is not None else None,
    )
    return {
        "version": VERSION,
        "validation": validation,
        "buildReport": build_report,
    }


__all__ = ["VERSION", "build_canonical_word_document"]
