from __future__ import annotations

from typing import Any

from .donorless_equation_groups import (
    bind_display_equations_to_pdf_groups as _bind_v04,
    _attach_group,
    _bbox,
    _equation_score,
    _group_equation_text,
    _item_equation_text,
    _item_page,
    _pdf_region_lookup,
)

VERSION = "donorless-equation-group-binding-0.5"


def _union_bbox(groups: list[dict[str, Any]]) -> list[float] | None:
    boxes = [_bbox(group.get("bbox")) for group in groups]
    boxes = [box for box in boxes if box]
    if not boxes:
        return None
    return [
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    ]


def _merged_group(page_no: int, groups: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [str(group.get("id") or "") for group in groups]
    member_ids: list[str] = []
    member_kinds: list[str] = []
    for group in groups:
        member_ids.extend(str(value) for value in (group.get("member_ids") or []) if value)
        member_kinds.extend(str(value) for value in (group.get("member_kinds") or []) if value)
    return {
        "id": f"p{page_no}-equation-span-" + "__".join(ids),
        "kind": "equation",
        "bbox": _union_bbox(groups),
        "member_ids": member_ids,
        "member_kinds": member_kinds,
        "constituent_group_ids": ids,
        "source": "contiguous-page-structure-equation-groups",
    }


def _span_score(
    page_no: int,
    item: dict[str, Any],
    groups: list[dict[str, Any]],
    region_lookup: dict[tuple[int, str], dict[str, Any]],
) -> float:
    text = " ".join(_group_equation_text(page_no, group, region_lookup) for group in groups)
    score = _equation_score(_item_equation_text(item), text)
    # Prefer the smallest span that explains the Markdown equation; this is a
    # weak regularizer, not a substitute for text evidence.
    return max(0.0, score - 1.5 * max(0, len(groups) - 1))


def _enumerate_alignments(
    page_no: int,
    items: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    region_lookup: dict[tuple[int, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def visit(item_index: int, group_index: int, matches: list[dict[str, Any]], total: float) -> None:
        remaining_items = len(items) - item_index
        remaining_groups = len(groups) - group_index
        if remaining_items == 0:
            results.append({"total": total, "matches": list(matches)})
            return
        if remaining_groups < remaining_items:
            return

        # Skip one extra PDF equation group. This is how overfragmentation/noise
        # can be ignored while preserving monotonic order.
        if remaining_groups - 1 >= remaining_items:
            visit(item_index, group_index + 1, matches, total)

        item = items[item_index]
        for span_len in (1, 2, 3):
            end = group_index + span_len
            if end > len(groups):
                break
            if len(groups) - end < remaining_items - 1:
                continue
            span = groups[group_index:end]
            score = _span_score(page_no, item, span, region_lookup)
            matches.append({
                "item": item,
                "groups": span,
                "score": score,
            })
            visit(item_index + 1, end, matches, total + score)
            matches.pop()

    visit(0, 0, [], 0.0)
    results.sort(key=lambda row: float(row.get("total") or 0.0), reverse=True)
    return results


def _safe_alignment(
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

    best = alignments[0]
    second = alignments[1] if len(alignments) > 1 else {"total": 0.0, "matches": []}
    scores = [float(row.get("score") or 0.0) for row in (best.get("matches") or [])]
    average = sum(scores) / len(scores) if scores else 0.0
    minimum = min(scores) if scores else 0.0
    margin = float(best.get("total") or 0.0) - float(second.get("total") or 0.0)

    audit = {
        "candidateAlignmentCount": len(alignments),
        "bestTotal": round(float(best.get("total") or 0.0), 2),
        "secondTotal": round(float(second.get("total") or 0.0), 2),
        "alignmentMargin": round(margin, 2),
        "averageMatchScore": round(average, 2),
        "minimumMatchScore": round(minimum, 2),
    }

    # This recovery is deliberately conservative. It must explain every
    # remaining Markdown equation in order, with no individually weak match,
    # and the whole sequence must beat the runner-up alignment clearly.
    accepted = (
        len(scores) == len(items)
        and minimum >= 44.0
        and average >= 62.0
        and margin >= max(6.0, 2.5 * len(items))
    )
    if not accepted:
        audit["reason"] = "alignment-below-policy"
        return [], audit

    audit["reason"] = "accepted"
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
            for constituent in group.get("constituentGroupIds") or []:
                if constituent:
                    used_group_ids.add(str(constituent))
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
    page_recovery: list[dict[str, Any]] = []

    for page_no in sorted(remaining_items_by_page):
        items = sorted(
            remaining_items_by_page.get(page_no, []),
            key=lambda row: int(row.get("orderIndex") or 0),
        )
        groups = groups_by_page.get(page_no, [])
        matches, alignment_audit = _safe_alignment(page_no, items, groups, region_lookup)
        page_record = {
            "page": page_no,
            "remainingMarkdownEquationCount": len(items),
            "remainingPdfEquationGroupCount": len(groups),
            **alignment_audit,
            "matches": [],
        }
        for row in matches:
            item = row["item"]
            span = list(row["groups"])
            merged = _merged_group(page_no, span)
            if not _bbox(merged.get("bbox")):
                continue
            score = float(row.get("score") or 0.0)
            _attach_group(
                item,
                merged,
                page_no,
                match_mode="page-structure-equation-group-monotonic-fragment-span",
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
        page_recovery.append(page_record)

    base_summary = base_audit.get("summary") if isinstance(base_audit.get("summary"), dict) else {}
    base_bound = int(base_audit.get("boundCount") or 0)
    total_markdown = int(base_summary.get("markdownDisplayEquationCount") or 0)
    total_bound = base_bound + sequence_bound

    audit = dict(base_audit)
    audit["version"] = VERSION
    audit["boundCount"] = total_bound
    audit["fragmentSpanRecovery"] = {
        "boundCount": sequence_bound,
        "pages": page_recovery,
        "policy": (
            "after v0.4 bindings are frozen, remaining Markdown equations may map monotonically to spans of 1-3 contiguous unused PDF equation groups; "
            "extra groups may be skipped; every equation must be explained; minimum score>=44, average>=62, and sequence margin must be decisive"
        ),
    }
    summary = dict(base_summary)
    summary["fragmentSpanBoundCount"] = sequence_bound
    summary["boundDisplayEquationCount"] = total_bound
    summary["bindingCoverage"] = round(total_bound / total_markdown, 5) if total_markdown else 1.0
    audit["summary"] = summary
    audit["policy"] = (
        "v0.4 bindings remain immutable; count mismatches may additionally use conservative monotonic 1-3-group fragmentation spans; raw PDF fragments are never bound directly"
    )
    markdown_pdf_spine["displayEquationGroupBinding"] = audit
    return audit


__all__ = ["bind_display_equations_to_pdf_groups"]
