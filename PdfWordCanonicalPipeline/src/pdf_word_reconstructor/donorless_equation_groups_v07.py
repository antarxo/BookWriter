from __future__ import annotations

from collections import defaultdict
from typing import Any

from .donorless_equation_groups import _attach_group, _bbox, _equation_score, _item_equation_text, _item_page
from .donorless_equation_groups_v06 import bind_display_equations_to_pdf_groups as _bind_v06


VERSION = "donorless-equation-group-binding-0.7"


def _placed(item: dict[str, Any]) -> bool:
    return bool(item.get("pdfRegion") and _bbox(item.get("bbox")))


def _order(item: dict[str, Any]) -> int:
    try:
        return int(item.get("orderIndex") or 0)
    except (TypeError, ValueError):
        return 0


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _page_blocks(poppler_witness: dict[str, Any] | None) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for page in (poppler_witness or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        blocks = [row for row in (page.get("blocks") or []) if _bbox(row.get("bbox"))]
        blocks.sort(key=lambda row: ((_bbox(row.get("bbox")) or [0, 0, 0, 0])[1], (_bbox(row.get("bbox")) or [0, 0, 0, 0])[0]))
        if page_no:
            result[page_no] = blocks
    return result


def _neighbor_band(
    item: dict[str, Any],
    page_items: list[dict[str, Any]],
) -> tuple[float, float, str, str] | None:
    target_order = _order(item)
    previous: dict[str, Any] | None = None
    following: dict[str, Any] | None = None
    for candidate in page_items:
        if candidate is item or not _placed(candidate):
            continue
        candidate_order = _order(candidate)
        if candidate_order < target_order:
            if previous is None or candidate_order > _order(previous):
                previous = candidate
        elif candidate_order > target_order:
            if following is None or candidate_order < _order(following):
                following = candidate
    if previous is None or following is None:
        return None
    prev_box = _bbox(previous.get("bbox"))
    next_box = _bbox(following.get("bbox"))
    if not prev_box or not next_box:
        return None
    y0 = float(prev_box[3])
    y1 = float(next_box[1])
    if y1 <= y0:
        return None
    height = y1 - y0
    if height < 4.0 or height > 180.0:
        return None
    return y0, y1, str(previous.get("id") or ""), str(following.get("id") or "")


def _blocks_in_band(blocks: list[dict[str, Any]], y0: float, y1: float) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for block in blocks:
        box = _bbox(block.get("bbox"))
        if not box:
            continue
        center = (box[1] + box[3]) / 2.0
        if y0 <= center <= y1:
            selected.append(block)
    return selected


def _recover_page(
    page_no: int,
    unresolved: list[dict[str, Any]],
    page_items: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    band_owners: dict[tuple[float, float], list[str]] = defaultdict(list)
    for item in unresolved:
        band = _neighbor_band(item, page_items)
        if band is None:
            candidates.append({"markdownId": item.get("id"), "reason": "missing-two-sided-neighbor-band"})
            continue
        y0, y1, prev_id, next_id = band
        key = (round(y0, 2), round(y1, 2))
        band_owners[key].append(str(item.get("id") or ""))
        candidates.append({
            "item": item,
            "markdownId": item.get("id"),
            "band": [y0, y1],
            "bandKey": key,
            "previousMarkdownId": prev_id,
            "nextMarkdownId": next_id,
        })

    used_block_ids: set[str] = set()
    bound = 0
    diagnostics: list[dict[str, Any]] = []
    for row in candidates:
        item = row.get("item")
        if not isinstance(item, dict):
            diagnostics.append(row)
            continue
        key = row.get("bandKey")
        if len(band_owners.get(key, [])) != 1:
            row["reason"] = "shared-band-with-multiple-unresolved-equations"
            diagnostics.append({k: v for k, v in row.items() if k != "item"})
            continue
        y0, y1 = row.get("band") or [0.0, 0.0]
        selected = [
            block for block in _blocks_in_band(blocks, float(y0), float(y1))
            if str(block.get("id") or "") not in used_block_ids
        ]
        if not selected:
            row["reason"] = "no-poppler-blocks-in-neighbor-band"
            diagnostics.append({k: v for k, v in row.items() if k != "item"})
            continue
        boxes = [_bbox(block.get("bbox")) for block in selected]
        merged_box = _union([box for box in boxes if box])
        if not merged_box:
            row["reason"] = "invalid-poppler-band-bbox"
            diagnostics.append({k: v for k, v in row.items() if k != "item"})
            continue
        merged_text = " ".join(str(block.get("text") or "") for block in selected).strip()
        score = _equation_score(_item_equation_text(item), merged_text)
        # Geometry is the primary evidence here; the text score is a guard against
        # a band that accidentally contains only prose or unrelated content.
        math_signal = sum(ch.isdigit() or ch in "=+-−·⋅×/^()[]{}λΔΕωπν" for ch in merged_text)
        signal_ratio = math_signal / max(1, len(merged_text))
        accepted = score >= 32.0 and (signal_ratio >= 0.08 or score >= 58.0)
        if not accepted:
            row.update({
                "reason": "poppler-band-text-guard-rejected",
                "score": round(score, 2),
                "mathSignalRatio": round(signal_ratio, 4),
                "popplerText": merged_text[:500],
            })
            diagnostics.append({k: v for k, v in row.items() if k != "item"})
            continue

        synthetic = {
            "id": f"poppler-band:{page_no}:{str(item.get('id') or '')}",
            "bbox": merged_box,
            "member_ids": [str(block.get("id") or "") for block in selected],
            "member_kinds": ["poppler-block"] * len(selected),
        }
        _attach_group(
            item,
            synthetic,
            page_no,
            match_mode="poppler-neighbor-bounded-equation-geometry",
            score=score,
        )
        item["pdfRowGranularity"] = "poppler-equation-band"
        item["manifestOutcome"] = "poppler-equation-geometry-witness-confirmed"
        item["pdfEquationGroup"]["source"] = "poppler-bbox-layout-neighbor-band"
        item["pdfEquationGroup"]["popplerBlockIds"] = [str(block.get("id") or "") for block in selected]
        item["pdfEquationGroup"]["popplerText"] = merged_text[:1000]
        item["pdfGeometry"]["witness"] = "poppler-bbox-layout"
        for block in selected:
            used_block_ids.add(str(block.get("id") or ""))
        bound += 1
        row.update({
            "reason": "accepted",
            "score": round(score, 2),
            "mathSignalRatio": round(signal_ratio, 4),
            "popplerBlockIds": [str(block.get("id") or "") for block in selected],
            "bbox": merged_box,
            "popplerText": merged_text[:500],
        })
        diagnostics.append({k: v for k, v in row.items() if k != "item"})
    return bound, diagnostics


def bind_display_equations_to_pdf_groups(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
    pdf_analysis: dict[str, Any] | None = None,
    poppler_witness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_audit = _bind_v06(markdown_pdf_spine, page_structure, pdf_analysis)
    blocks_by_page = _page_blocks(poppler_witness)

    items_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    unresolved_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in markdown_pdf_spine.get("items", []) or []:
        page_no = _item_page(item)
        if not page_no:
            continue
        items_by_page[page_no].append(item)
        if str(item.get("type") or "") == "display_equation" and not _placed(item):
            unresolved_by_page[page_no].append(item)

    total_bound = 0
    pages: list[dict[str, Any]] = []
    if not (poppler_witness or {}).get("available"):
        pages.append({"reason": "poppler-unavailable"})
    else:
        for page_no in sorted(unresolved_by_page):
            unresolved = sorted(unresolved_by_page[page_no], key=_order)
            page_items = sorted(items_by_page.get(page_no, []), key=_order)
            bound, diagnostics = _recover_page(
                page_no,
                unresolved,
                page_items,
                blocks_by_page.get(page_no, []),
            )
            total_bound += bound
            pages.append({
                "page": page_no,
                "unresolvedBefore": len(unresolved),
                "boundCount": bound,
                "items": diagnostics,
            })

    audit = dict(base_audit)
    audit["version"] = VERSION
    previous_bound = int(base_audit.get("boundCount") or 0)
    audit["boundCount"] = previous_bound + total_bound
    audit["popplerNeighborBoundedRecovery"] = {
        "boundCount": total_bound,
        "pages": pages,
        "available": bool((poppler_witness or {}).get("available")),
        "pdftotext": (poppler_witness or {}).get("pdftotext"),
        "policy": (
            "v0.6 bindings are immutable; only still-unresolved display equations may use Poppler bbox-layout; "
            "requires unique two-sided resolved Markdown neighbors on the same page and a math/text guard"
        ),
    }
    summary = dict(base_audit.get("summary") or {})
    summary["popplerNeighborBoundedEquationCount"] = total_bound
    summary["boundDisplayEquationCount"] = int(summary.get("boundDisplayEquationCount") or previous_bound) + total_bound
    total_markdown = int(summary.get("markdownDisplayEquationCount") or 0)
    summary["bindingCoverage"] = round(summary["boundDisplayEquationCount"] / total_markdown, 5) if total_markdown else 1.0
    audit["summary"] = summary
    audit["policy"] = (
        "v0.6 bindings remain immutable; unresolved equations may additionally use conservative, neighbor-bounded Poppler geometry"
    )
    markdown_pdf_spine["displayEquationGroupBinding"] = audit
    return audit


__all__ = ["bind_display_equations_to_pdf_groups"]
