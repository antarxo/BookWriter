from __future__ import annotations

from typing import Any

from .donorless_equation_groups import (
    _attach_group,
    _bbox,
    _equation_score,
    _group_equation_text,
    _item_equation_text,
    _item_page,
    _pdf_region_lookup,
)
from .donorless_equation_groups_v05 import (
    _bind_v04,
    _enumerate_alignments,
    _merged_group,
)

VERSION = "donorless-equation-group-binding-0.6"


def _anchor_index(
    page_no: int,
    item: dict[str, Any],
    span: list[dict[str, Any]],
    region_lookup: dict[tuple[int, str], dict[str, Any]],
) -> int:
    source = _item_equation_text(item)
    ranked: list[tuple[float, int]] = []
    for index, group in enumerate(span):
        score = _equation_score(source, _group_equation_text(page_no, group, region_lookup))
        ranked.append((score, index))
    ranked.sort(reverse=True)
    return ranked[0][1] if ranked else 0


def _anchor_signature(
    page_no: int,
    alignment: dict[str, Any],
    region_lookup: dict[tuple[int, str], dict[str, Any]],
) -> tuple[str, ...]:
    signature: list[str] = []
    for row in alignment.get("matches") or []:
        span = list(row.get("groups") or [])
        if not span:
            signature.append("")
            continue
        anchor = span[_anchor_index(page_no, row.get("item") or {}, span, region_lookup)]
        signature.append(str(anchor.get("id") or ""))
    return tuple(signature)


def _collapsed_safe_alignment(
    page_no: int,
    items: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    region_lookup: dict[tuple[int, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not items or len(groups) < len(items):
        return [], {"reason": "insufficient-groups"}

    alignments = _enumerate_alignments(page_no, items, groups, region_lookup)
    if not alignments:
        return [], {"reason": "no-monotonic-alignment"}

    # Many raw alignments differ only by whether tiny adjacent PDF fragments are
    # included in a 1-3 group span.  Collapse those boundary variants by the
    # strongest single-group anchor chosen for each Markdown equation.
    best_by_anchor: dict[tuple[str, ...], dict[str, Any]] = {}
    for alignment in alignments:
        key = _anchor_signature(page_no, alignment, region_lookup)
        current = best_by_anchor.get(key)
        if current is None or float(alignment.get("total") or 0.0) > float(current.get("total") or 0.0):
            best_by_anchor[key] = alignment

    collapsed = sorted(
        best_by_anchor.items(),
        key=lambda pair: float(pair[1].get("total") or 0.0),
        reverse=True,
    )
    best_key, best = collapsed[0]
    second = collapsed[1][1] if len(collapsed) > 1 else {"total": 0.0, "matches": []}

    scores = [float(row.get("score") or 0.0) for row in (best.get("matches") or [])]
    average = sum(scores) / len(scores) if scores else 0.0
    minimum = min(scores) if scores else 0.0
    margin = float(best.get("total") or 0.0) - float(second.get("total") or 0.0)

    audit = {
        "rawAlignmentCount": len(alignments),
        "distinctAnchorAssignmentCount": len(collapsed),
        "bestAnchorAssignment": list(best_key),
        "bestTotal": round(float(best.get("total") or 0.0), 2),
        "secondDistinctAnchorTotal": round(float(second.get("total") or 0.0), 2),
        "distinctAnchorMargin": round(margin, 2),
        "averageMatchScore": round(average, 2),
        "minimumMatchScore": round(minimum, 2),
    }

    accepted = (
        len(scores) == len(items)
        and minimum >= 44.0
        and average >= 62.0
        and margin >= max(5.0, 2.0 * len(items))
    )
    if not accepted:
        audit["reason"] = "anchor-collapsed-alignment-below-policy"
        return [], audit

    audit["reason"] = "accepted-anchor-collapsed-monotonic-alignment"
    return list(best.get("matches") or []), audit


def bind_display_equations_to_pdf_groups(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
    pdf_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_audit = _bind_v04(markdown_pdf_spine, page_structure, pdf_analysis)
    region_lookup = _pdf_region_lookup(pdf_analysis)

    used_group_ids: set[str] = set()
    remaining_items_by_page: dict[int, list[dict[str, Any]]] = {}
    for item in markdown_pdf_spine.get("items", []) or []:
        if str(item.get("type") or "") != "display_equation":
            continue
        group = item.get("pdfEquationGroup") if isinstance(item.get("pdfEquationGroup"), dict) else {}
        group_id = str(group.get("id") or "")
        if group_id:
            used_group_ids.add(group_id)
        if item.get("pdfRegion") and _bbox(item.get("bbox")):
            continue
        page_no = _item_page(item)
        if page_no:
            remaining_items_by_page.setdefault(page_no, []).append(item)

    groups_by_page: dict[int, list[dict[str, Any]]] = {}
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        groups = [
            group for group in (page.get("visual_groups") or [])
            if str(group.get("kind") or "") == "equation"
            and _bbox(group.get("bbox"))
            and str(group.get("id") or "") not in used_group_ids
        ]
        groups.sort(key=lambda group: (
            (_bbox(group.get("bbox")) or [0, 0, 0, 0])[1],
            (_bbox(group.get("bbox")) or [0, 0, 0, 0])[0],
        ))
        if groups:
            groups_by_page[page_no] = groups

    sequence_bound = 0
    recovery_pages: list[dict[str, Any]] = []

    for page_no in sorted(remaining_items_by_page):
        items = sorted(remaining_items_by_page.get(page_no, []), key=lambda row: int(row.get("orderIndex") or 0))
        groups = groups_by_page.get(page_no, [])
        matches, alignment_audit = _collapsed_safe_alignment(page_no, items, groups, region_lookup)
        page_record = {
            "page": page_no,
            "remainingMarkdownEquationCount": len(items),
            "remainingPdfEquationGroupCount": len(groups),
            **alignment_audit,
            "matches": [],
        }
        for row in matches:
            item = row.get("item") or {}
            span = list(row.get("groups") or [])
            merged = _merged_group(page_no, span)
            if not _bbox(merged.get("bbox")):
                continue
            score = float(row.get("score") or 0.0)
            _attach_group(
                item,
                merged,
                page_no,
                match_mode="page-structure-equation-group-anchor-collapsed-fragment-span",
                score=score,
            )
            constituent_ids = [str(group.get("id") or "") for group in span]
            item["pdfEquationGroup"]["constituentGroupIds"] = constituent_ids
            item["pdfEquationGroup"]["source"] = "contiguous-page-structure-equation-groups"
            for group_id in constituent_ids:
                used_group_ids.add(group_id)
            sequence_bound += 1
            page_record["matches"].append({
                "markdownId": item.get("id"),
                "constituentGroupIds": constituent_ids,
                "score": round(score, 2),
            })
        recovery_pages.append(page_record)

    base_summary = base_audit.get("summary") if isinstance(base_audit.get("summary"), dict) else {}
    base_bound = int(base_audit.get("boundCount") or 0)
    total_markdown = int(base_summary.get("markdownDisplayEquationCount") or 0)
    total_bound = base_bound + sequence_bound

    audit = dict(base_audit)
    audit["version"] = VERSION
    audit["boundCount"] = total_bound
    audit["anchorCollapsedFragmentSpanRecovery"] = {
        "boundCount": sequence_bound,
        "pages": recovery_pages,
        "policy": (
            "v0.4 bindings are immutable; remaining equations may use monotonic 1-3 group spans; "
            "near-equivalent span-boundary variants are collapsed by strongest group-anchor assignment before ambiguity is measured"
        ),
    }
    summary = dict(base_summary)
    summary["anchorCollapsedFragmentSpanBoundCount"] = sequence_bound
    summary["boundDisplayEquationCount"] = total_bound
    summary["bindingCoverage"] = round(total_bound / total_markdown, 5) if total_markdown else 1.0
    audit["summary"] = summary
    audit["policy"] = (
        "v0.4 bindings remain immutable; count mismatches may additionally use conservative monotonic 1-3-group spans with anchor-collapsed ambiguity; raw PDF fragments are never bound directly"
    )
    markdown_pdf_spine["displayEquationGroupBinding"] = audit
    return audit


__all__ = ["bind_display_equations_to_pdf_groups"]
