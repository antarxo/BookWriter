from __future__ import annotations

from copy import deepcopy
from typing import Any

from .markdown_pdf_spine_v24 import build_markdown_pdf_spine as _build_v24
from .markdown_pdf_spine_v20 import _post_v19_diagnostics, _compact
from .markdown_pdf_spine_v16 import _raw_text_regions
from .markdown_pdf_spine_v23 import _matching_pdf_view
from .markdown_pdf_spine_v08 import _placed, _neighbor_bounds, _candidate_rows
from .markdown_pdf_spine_v10 import _one_sided_candidates, _rank
from .markdown_pdf_spine_v07 import _item_text, _page_no
from .markdown_pdf_spine_v02 import TEXT_TYPES, _pdf_regions

VERSION = "markdown-pdf-spine-0.25"


def _owner_by_region(items: list[dict[str, Any]]) -> dict[tuple[int, str], dict[str, Any]]:
    owners: dict[tuple[int, str], dict[str, Any]] = {}
    for item in items:
        if not _placed(item):
            continue
        page = int(item.get("pdfPage") or 0)
        region = str(item.get("pdfRegion") or "")
        if not page or not region:
            continue
        owners[(page, region)] = {
            "markdownId": item.get("id"),
            "type": item.get("type"),
            "matchMode": item.get("matchMode"),
            "score": item.get("score"),
            "text": _item_text(item)[:180],
        }
    return owners


def _final_neighbor_diagnostics(
    items: list[dict[str, Any]],
    filtered_pdf: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
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
                f"post-v25-unresolved best=p{best.get('candidatePage') or 0} "
                f"d={best.get('pageDelta')} {best.get('semanticType') or '∅'} "
                f"{owner_text} exact={bool(best.get('exactSkeleton'))} "
                f"text={_compact(best.get('text'))!r}"
            )
        else:
            reason = "post-v25-unresolved no-body-candidate-in-p±1"
        compatibility_items.append({
            "markdownId": row.get("markdownId"),
            "page": row.get("hintedPage"),
            "reason": reason,
            "candidateCount": len(candidates),
            "bestScore": best.get("score") if best else None,
            "secondScore": second.get("score") if second else None,
        })
    count = int(diagnostics.get("count") or 0)
    compatibility = {
        "count": count,
        "reasonCounts": {"post-v25-unresolved": count} if count else {},
        "items": compatibility_items,
    }
    return diagnostics, compatibility


def _final_semantic_diagnostics(
    items: list[dict[str, Any]],
    filtered_pdf: dict[str, Any],
) -> dict[str, Any]:
    rows_by_page: dict[int, list[dict[str, Any]]] = {}
    for row in _pdf_regions(filtered_pdf):
        rows_by_page.setdefault(int(row.get("page") or 0), []).append(row)

    used_ids = {
        (int(item.get("pdfPage") or 0), str(item.get("pdfRegion") or ""))
        for item in items
        if item.get("pdfRegion")
    }

    diagnostics: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        kind = str(item.get("type") or "")
        if kind not in TEXT_TYPES or _placed(item):
            continue
        page = _page_no(item)
        if not page:
            continue
        previous, following = _neighbor_bounds(items, index, page)
        if previous is not None and following is not None:
            candidates = _candidate_rows(rows_by_page.get(page, []), previous, following, used_ids, page)
            mode = "two-sided"
        elif previous is not None or following is not None:
            candidates = _one_sided_candidates(
                rows_by_page.get(page, []), previous, following, page=page, used_ids=used_ids
            )
            mode = "one-sided"
        else:
            candidates = []
            mode = "no-neighbor"

        ranked = _rank(_item_text(item), candidates) if candidates else []
        diagnostics.append({
            "markdownId": item.get("id"),
            "page": page,
            "type": kind,
            "text": _item_text(item)[:180],
            "candidateMode": mode,
            "candidateCount": len(candidates),
            "topCandidates": [
                {
                    "score": round(float(score), 2),
                    "pdfRegion": row.get("id"),
                    "semanticType": row.get("semanticType"),
                    "flowZone": row.get("flowZone"),
                    "rowGranularity": row.get("rowGranularity"),
                    "text": str(row.get("text") or "")[:180],
                }
                for score, row in ranked[:3]
            ],
        })
    return {"count": len(diagnostics), "items": diagnostics[:120]}


def _final_pagewide_diagnostics(
    items: list[dict[str, Any]],
    filtered_pdf: dict[str, Any],
) -> dict[str, Any]:
    rows_by_page: dict[int, list[dict[str, Any]]] = {}
    for row in _pdf_regions(filtered_pdf):
        rows_by_page.setdefault(int(row.get("page") or 0), []).append(row)
    owners = _owner_by_region(items)

    diagnostics: list[dict[str, Any]] = []
    for item in items:
        kind = str(item.get("type") or "")
        if kind not in TEXT_TYPES or _placed(item):
            continue
        page = _page_no(item)
        if not page:
            continue
        page_rows = [
            row for row in rows_by_page.get(page, [])
            if str(row.get("rowGranularity") or "") != "pdf-line-cluster"
        ]
        ranked = _rank(_item_text(item), page_rows) if page_rows else []
        top_rows: list[dict[str, Any]] = []
        for score, row in ranked[:5]:
            region = str(row.get("id") or "")
            owner = owners.get((page, region))
            top_rows.append({
                "score": round(float(score), 2),
                "pdfRegion": region,
                "semanticType": row.get("semanticType"),
                "flowZone": row.get("flowZone"),
                "rowGranularity": row.get("rowGranularity"),
                "text": str(row.get("text") or "")[:180],
                "used": owner is not None,
                "owner": deepcopy(owner) if owner else None,
            })
        diagnostics.append({
            "markdownId": item.get("id"),
            "page": page,
            "type": kind,
            "text": _item_text(item)[:180],
            "topPageRows": top_rows,
        })
    return {"count": len(diagnostics), "items": diagnostics[:120]}


def build_markdown_pdf_spine(
    markdown_element_map: dict[str, Any] | None,
    pdf_analysis: dict[str, Any],
) -> dict[str, Any]:
    result = _build_v24(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])
    filtered_pdf, exclusion = _matching_pdf_view(pdf_analysis)

    post, neighbor = _final_neighbor_diagnostics(items, filtered_pdf)
    semantic = _final_semantic_diagnostics(items, filtered_pdf)
    pagewide = _final_pagewide_diagnostics(items, filtered_pdf)

    result["version"] = VERSION
    result["postV25Diagnostics"] = post
    result["neighborBoundedDiagnostics"] = neighbor
    result["semanticCandidateDiagnostics"] = semantic
    result["pageWideConflictDiagnostics"] = pagewide
    result["finalDiagnosticFurnitureExclusion"] = {
        **exclusion,
        "policy": (
            "all final unresolved-text diagnostics are recomputed after v0.24 against a header/footer-free PDF witness; "
            "page furniture remains available only in authoritative PDF geometry and never appears as a body candidate"
        ),
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
