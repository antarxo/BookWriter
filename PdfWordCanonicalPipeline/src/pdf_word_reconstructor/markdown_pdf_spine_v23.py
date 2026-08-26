from __future__ import annotations

from copy import deepcopy
from typing import Any

from .markdown_pdf_spine_v21 import build_markdown_pdf_spine as _build_v21
from .markdown_pdf_spine_v20 import _post_v19_diagnostics, _compact
from .markdown_pdf_spine_v16 import _raw_text_regions
from .markdown_pdf_spine_v08 import _placed

VERSION = "markdown-pdf-spine-0.23"


def _is_furniture_semantic(region: dict[str, Any]) -> bool:
    semantic = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
    sem_type = str(semantic.get("type") or "")
    flow_zone = str(semantic.get("flow_zone") or "")
    furniture = semantic.get("pageFurniture") if isinstance(semantic.get("pageFurniture"), dict) else {}
    return (
        sem_type in {"header", "footer"}
        or flow_zone == "page_furniture"
        or bool(furniture.get("detected"))
    )


def _matching_pdf_view(pdf_analysis: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """Copy PDF analysis and remove furniture only for the retry pass.

    The primary v0.21 pass always runs against the complete, original PDF witness.
    Therefore PDF row indices, ordering and all previously accepted body bindings
    remain unchanged.  This filtered copy is used only to retry unresolved items.
    """
    view = deepcopy(pdf_analysis)
    removed_header = 0
    removed_footer = 0
    for page in view.get("pages", []) or []:
        kept: list[dict[str, Any]] = []
        for region in page.get("regions", []) or []:
            if region.get("type") != "text" or not _is_furniture_semantic(region):
                kept.append(region)
                continue
            semantic = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
            if str(semantic.get("type") or "") == "footer":
                removed_footer += 1
            else:
                removed_header += 1
        page["regions"] = kept
    return view, {
        "removedHeaderRegionCount": removed_header,
        "removedFooterRegionCount": removed_footer,
        "removedTotal": removed_header + removed_footer,
    }


def _base_region_id(item: dict[str, Any]) -> str:
    parent = str(item.get("pdfParentRegion") or "")
    if parent:
        return parent
    region = str(item.get("pdfRegion") or "")
    if not region:
        return ""
    for marker in ("-lines", "-line"):
        pos = region.rfind(marker)
        if pos > 0:
            return region[:pos]
    return region


def _occupied_body_regions(items: list[dict[str, Any]]) -> set[tuple[int, str]]:
    occupied: set[tuple[int, str]] = set()
    for item in items:
        if not _placed(item):
            continue
        page = int(item.get("pdfPage") or 0)
        base = _base_region_id(item)
        if page and base:
            occupied.add((page, base))
        for constituent in item.get("pdfConstituentRegions") or []:
            value = str(constituent or "")
            for marker in ("-lines", "-line"):
                pos = value.rfind(marker)
                if pos > 0:
                    value = value[:pos]
                    break
            if page and value:
                occupied.add((page, value))
    return occupied


def _copy_pdf_binding(target: dict[str, Any], source: dict[str, Any]) -> None:
    binding_keys = (
        "status", "manifestOutcome", "score", "matchMode", "pdfPage", "pdfRegion",
        "pdfParentRegion", "pdfLineIndex", "pdfRowGranularity", "bbox", "pdfText",
        "pdfTypography", "pdfGeometry", "pdfConstituentRegions",
    )
    for key in binding_keys:
        if key in source:
            target[key] = deepcopy(source.get(key))
        else:
            target.pop(key, None)


def _final_diagnostics(result: dict[str, Any], filtered_pdf: dict[str, Any]) -> None:
    items = list(result.get("items", []) or [])
    diagnostics = _post_v19_diagnostics(items, _raw_text_regions(filtered_pdf))
    compatibility_items: list[dict[str, Any]] = []
    for row in diagnostics.get("items") or []:
        candidates = row.get("topCandidates") or []
        best = candidates[0] if candidates else {}
        second = candidates[1] if len(candidates) > 1 else {}
        owner = best.get("owner") if isinstance(best.get("owner"), dict) else {}
        owner_text = f"used->{owner.get('markdownId')}" if owner else "unused"
        if best:
            reason = (
                f"post-v23-unresolved best=p{best.get('candidatePage') or 0} "
                f"d={best.get('pageDelta')} {best.get('semanticType') or '∅'} "
                f"{owner_text} exact={bool(best.get('exactSkeleton'))} "
                f"text={_compact(best.get('text'))!r}"
            )
        else:
            reason = "post-v23-unresolved no-body-candidate-in-p±1"
        compatibility_items.append({
            "markdownId": row.get("markdownId"),
            "page": row.get("hintedPage"),
            "reason": reason,
            "candidateCount": len(candidates),
            "bestScore": best.get("score") if best else None,
            "secondScore": second.get("score") if second else None,
        })
    count = int(diagnostics.get("count") or 0)
    result["postV23Diagnostics"] = diagnostics
    result["neighborBoundedDiagnostics"] = {
        "count": count,
        "reasonCounts": {"post-v23-unresolved": count} if count else {},
        "items": compatibility_items,
    }


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    # Primary authority-preserving pass: do not remove or reorder any PDF row.
    result = _build_v21(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])

    # Secondary filtered pass: page furniture is invisible only to retry matching.
    filtered_pdf, exclusion = _matching_pdf_view(pdf_analysis)
    retry = _build_v21(markdown_element_map, filtered_pdf)
    retry_by_id = {
        str(item.get("id") or ""): item
        for item in retry.get("items", []) or []
        if item.get("id")
    }

    occupied = _occupied_body_regions(items)
    recovered: list[dict[str, Any]] = []
    rejected_occupied = 0

    for item in items:
        if _placed(item):
            # Proven body bindings from the primary pass are immutable here.
            continue
        item_id = str(item.get("id") or "")
        candidate = retry_by_id.get(item_id)
        if not candidate or not _placed(candidate):
            continue
        page = int(candidate.get("pdfPage") or 0)
        base = _base_region_id(candidate)
        if not page or not base:
            continue
        if (page, base) in occupied:
            rejected_occupied += 1
            continue
        _copy_pdf_binding(item, candidate)
        occupied.add((page, base))
        for constituent in candidate.get("pdfConstituentRegions") or []:
            value = str(constituent or "")
            for marker in ("-lines", "-line"):
                pos = value.rfind(marker)
                if pos > 0:
                    value = value[:pos]
                    break
            if value:
                occupied.add((page, value))
        recovered.append({
            "markdownId": item_id,
            "pdfPage": page,
            "pdfRegion": candidate.get("pdfRegion"),
            "matchMode": candidate.get("matchMode"),
            "score": candidate.get("score"),
        })

    result["version"] = VERSION
    result["pageFurnitureIsolation"] = {
        **exclusion,
        "recoveredUnresolvedCount": len(recovered),
        "rejectedBecauseBodyRegionAlreadyOwned": rejected_occupied,
        "recovered": recovered[:120],
        "policy": (
            "primary v0.21 matching runs on the complete PDF and all already-placed body bindings remain immutable; "
            "header/footer furniture is removed only from a secondary retry view for still-unresolved items; "
            "retry may use only an otherwise-unowned body region; Markdown ordering and PDF row indexing are never rebuilt for accepted body items"
        ),
    }
    _final_diagnostics(result, filtered_pdf)
    return result


__all__ = ["build_markdown_pdf_spine"]
