from __future__ import annotations

from collections import defaultdict
from typing import Any

from .markdown_pdf_spine_v16 import build_markdown_pdf_spine as _build_v16, _raw_text_regions
from .markdown_pdf_spine_v15 import _skeleton
from .markdown_pdf_spine_v08 import _placed
from .markdown_pdf_spine_v07 import _item_text, _page_no
from .markdown_pdf_spine_v02 import TEXT_TYPES, _score
from .common import normalize_text

VERSION = "markdown-pdf-spine-0.17"


def _post_v16_diagnostics(items: list[dict[str, Any]], raw_regions: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    owner_by_region: dict[tuple[int, str], dict[str, Any]] = {}
    for item in items:
        page = int(item.get("pdfPage") or 0)
        region = str(item.get("pdfRegion") or "")
        if page and region:
            owner_by_region[(page, region)] = {
                "markdownId": item.get("id"),
                "type": item.get("type"),
                "matchMode": item.get("matchMode"),
                "text": _item_text(item)[:140],
            }

    diagnostics: list[dict[str, Any]] = []
    kind_counts: dict[str, int] = defaultdict(int)

    for item in items:
        kind = str(item.get("type") or "")
        if kind not in TEXT_TYPES or _placed(item):
            continue
        hinted_page = _page_no(item)
        text = _item_text(item)
        source_norm = normalize_text(text)
        source_skeleton = _skeleton(text)
        kind_counts[kind] += 1

        rows: list[dict[str, Any]] = []
        if hinted_page:
            for page in (hinted_page - 1, hinted_page, hinted_page + 1):
                if page <= 0:
                    continue
                for row in raw_regions.get(page, []):
                    candidate = dict(row)
                    candidate["candidatePage"] = page
                    rows.append(candidate)

        ranked: list[tuple[float, dict[str, Any], bool]] = []
        for row in rows:
            target_text = str(row.get("text") or "")
            exact_skeleton = bool(source_skeleton and source_skeleton == _skeleton(target_text))
            score = 100.0 if exact_skeleton else _score(source_norm, str(row.get("normalized") or normalize_text(target_text)))
            ranked.append((score, row, exact_skeleton))
        ranked.sort(key=lambda value: value[0], reverse=True)

        top_rows: list[dict[str, Any]] = []
        for score, row, exact_skeleton in ranked[:5]:
            page = int(row.get("candidatePage") or 0)
            region_id = str(row.get("id") or "")
            owner = owner_by_region.get((page, region_id))
            top_rows.append({
                "score": round(float(score), 2),
                "candidatePage": page,
                "pageDelta": (page - hinted_page) if hinted_page else None,
                "pdfRegion": region_id,
                "semanticType": row.get("semanticType"),
                "flowZone": row.get("flowZone"),
                "exactSkeleton": exact_skeleton,
                "candidateSkeleton": _skeleton(str(row.get("text") or "")),
                "text": str(row.get("text") or "")[:180],
                "used": owner is not None,
                "owner": owner,
            })

        diagnostics.append({
            "markdownId": item.get("id"),
            "type": kind,
            "hintedPage": hinted_page,
            "text": text[:180],
            "sourceSkeleton": source_skeleton,
            "topCandidates": top_rows,
        })

    return {
        "count": len(diagnostics),
        "byType": dict(sorted(kind_counts.items())),
        "items": diagnostics[:120],
    }


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v16(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])
    raw_regions = _raw_text_regions(pdf_analysis)

    diagnostics = _post_v16_diagnostics(items, raw_regions)

    result["version"] = VERSION
    result["postV16Diagnostics"] = diagnostics
    # Compatibility key: make the server's existing text-diagnostics slot reflect
    # the current post-v16 unresolved set instead of stale pre-recovery snapshots.
    reason_counts = {"post-v16-unresolved": int(diagnostics.get("count") or 0)} if diagnostics.get("count") else {}
    result["neighborBoundedDiagnostics"] = {
        "count": diagnostics.get("count") or 0,
        "reasonCounts": reason_counts,
        "items": [
            {
                "markdownId": row.get("markdownId"),
                "page": row.get("hintedPage"),
                "reason": "post-v16-unresolved",
                "candidateCount": len(row.get("topCandidates") or []),
                "bestScore": ((row.get("topCandidates") or [{}])[0]).get("score") if row.get("topCandidates") else None,
                "secondScore": ((row.get("topCandidates") or [{}, {}])[1]).get("score") if len(row.get("topCandidates") or []) > 1 else None,
            }
            for row in (diagnostics.get("items") or [])
        ],
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
