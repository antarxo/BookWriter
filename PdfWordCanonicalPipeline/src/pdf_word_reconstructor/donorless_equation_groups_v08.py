from __future__ import annotations

from collections import defaultdict
from typing import Any

from .donorless_equation_groups import _attach_group, _bbox, _equation_score, _item_equation_text, _item_page
from .donorless_equation_groups_v07 import (
    _bind_v06,
    _neighbor_band,
    _order,
    _placed,
    _union,
    bind_display_equations_to_pdf_groups as _bind_v07,
)


VERSION = "donorless-equation-group-binding-0.8"


def _page_lines(poppler_witness: dict[str, Any] | None) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for page in (poppler_witness or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        rows: list[dict[str, Any]] = []
        for block in page.get("blocks", []) or []:
            block_id = str(block.get("id") or "")
            for index, line in enumerate(block.get("lines", []) or [], start=1):
                box = _bbox(line.get("bbox"))
                text = str(line.get("text") or "").strip()
                if not box or not text:
                    continue
                rows.append({
                    "id": f"{block_id}:l{index:03d}",
                    "blockId": block_id,
                    "bbox": box,
                    "text": text,
                })
        rows.sort(key=lambda row: (
            (_bbox(row.get("bbox")) or [0, 0, 0, 0])[1],
            (_bbox(row.get("bbox")) or [0, 0, 0, 0])[0],
        ))
        if page_no:
            result[page_no] = rows
    return result


def _line_overlaps_band(line: dict[str, Any], y0: float, y1: float) -> bool:
    box = _bbox(line.get("bbox"))
    if not box:
        return False
    overlap = max(0.0, min(box[3], y1) - max(box[1], y0))
    height = max(0.001, box[3] - box[1])
    return overlap / height >= 0.25


def _math_signal_ratio(text: str) -> float:
    signal = sum(ch.isdigit() or ch in "=+-−·⋅×/^()[]{}λΔΕωπν" for ch in text)
    return signal / max(1, len(text))


def _recover_line_level(
    page_no: int,
    unresolved: list[dict[str, Any]],
    page_items: list[dict[str, Any]],
    lines: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    bound = 0
    diagnostics: list[dict[str, Any]] = []
    band_owners: dict[tuple[float, float], list[str]] = defaultdict(list)
    prepared: list[dict[str, Any]] = []

    for item in unresolved:
        band = _neighbor_band(item, page_items)
        if band is None:
            diagnostics.append({
                "markdownId": item.get("id"),
                "reason": "missing-two-sided-neighbor-band",
            })
            continue
        y0, y1, prev_id, next_id = band
        key = (round(y0, 2), round(y1, 2))
        band_owners[key].append(str(item.get("id") or ""))
        prepared.append({
            "item": item,
            "markdownId": item.get("id"),
            "band": [y0, y1],
            "bandKey": key,
            "previousMarkdownId": prev_id,
            "nextMarkdownId": next_id,
        })

    used_line_ids: set[str] = set()
    for row in prepared:
        item = row["item"]
        key = row["bandKey"]
        if len(band_owners.get(key, [])) != 1:
            diagnostics.append({
                "markdownId": row.get("markdownId"),
                "reason": "shared-band-with-multiple-unresolved-equations",
                "band": row.get("band"),
            })
            continue

        y0, y1 = [float(v) for v in row["band"]]
        selected = [
            line for line in lines
            if str(line.get("id") or "") not in used_line_ids
            and _line_overlaps_band(line, y0, y1)
        ]
        if not selected:
            diagnostics.append({
                "markdownId": row.get("markdownId"),
                "reason": "no-poppler-lines-in-neighbor-band",
                "band": row.get("band"),
            })
            continue

        boxes = [_bbox(line.get("bbox")) for line in selected]
        merged_box = _union([box for box in boxes if box])
        merged_text = " ".join(str(line.get("text") or "") for line in selected).strip()
        if not merged_box or not merged_text:
            diagnostics.append({
                "markdownId": row.get("markdownId"),
                "reason": "invalid-poppler-line-band",
                "band": row.get("band"),
            })
            continue

        score = _equation_score(_item_equation_text(item), merged_text)
        signal_ratio = _math_signal_ratio(merged_text)
        accepted = score >= 32.0 and (signal_ratio >= 0.08 or score >= 58.0)
        if not accepted:
            diagnostics.append({
                "markdownId": row.get("markdownId"),
                "reason": "poppler-line-band-text-guard-rejected",
                "band": row.get("band"),
                "score": round(score, 2),
                "mathSignalRatio": round(signal_ratio, 4),
                "popplerText": merged_text[:500],
            })
            continue

        line_ids = [str(line.get("id") or "") for line in selected]
        synthetic = {
            "id": f"poppler-line-band:{page_no}:{str(item.get('id') or '')}",
            "bbox": merged_box,
            "member_ids": line_ids,
            "member_kinds": ["poppler-line"] * len(line_ids),
        }
        _attach_group(
            item,
            synthetic,
            page_no,
            match_mode="poppler-neighbor-bounded-equation-line-geometry",
            score=score,
        )
        item["pdfRowGranularity"] = "poppler-equation-line-band"
        item["manifestOutcome"] = "poppler-equation-line-geometry-witness-confirmed"
        item["pdfEquationGroup"]["source"] = "poppler-bbox-layout-neighbor-line-band"
        item["pdfEquationGroup"]["popplerLineIds"] = line_ids
        item["pdfEquationGroup"]["popplerText"] = merged_text[:1000]
        item["pdfGeometry"]["witness"] = "poppler-bbox-layout-lines"
        for line_id in line_ids:
            used_line_ids.add(line_id)
        bound += 1
        diagnostics.append({
            "markdownId": row.get("markdownId"),
            "reason": "accepted",
            "band": row.get("band"),
            "score": round(score, 2),
            "mathSignalRatio": round(signal_ratio, 4),
            "popplerLineIds": line_ids,
            "bbox": merged_box,
            "popplerText": merged_text[:500],
        })

    return bound, diagnostics


def bind_display_equations_to_pdf_groups(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
    pdf_analysis: dict[str, Any] | None = None,
    poppler_witness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_audit = _bind_v07(markdown_pdf_spine, page_structure, pdf_analysis, poppler_witness)
    lines_by_page = _page_lines(poppler_witness)

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
            bound, diagnostics = _recover_line_level(
                page_no,
                unresolved,
                page_items,
                lines_by_page.get(page_no, []),
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
    audit["popplerLineLevelRecovery"] = {
        "boundCount": total_bound,
        "pages": pages,
        "available": bool((poppler_witness or {}).get("available")),
        "policy": (
            "v0.7 bindings are immutable; only still-unresolved display equations with a unique two-sided Markdown neighbor band "
            "may use Poppler line-level bbox geometry; no one-sided/page-edge inference"
        ),
    }
    summary = dict(base_audit.get("summary") or {})
    summary["popplerLineLevelEquationCount"] = total_bound
    summary["boundDisplayEquationCount"] = int(summary.get("boundDisplayEquationCount") or previous_bound) + total_bound
    total_markdown = int(summary.get("markdownDisplayEquationCount") or 0)
    summary["bindingCoverage"] = round(summary["boundDisplayEquationCount"] / total_markdown, 5) if total_markdown else 1.0
    audit["summary"] = summary
    audit["policy"] = (
        "v0.7 bindings remain immutable; unresolved equations may additionally use conservative Poppler line-level geometry inside unique two-sided Markdown bands"
    )
    markdown_pdf_spine["displayEquationGroupBinding"] = audit
    return audit


__all__ = ["bind_display_equations_to_pdf_groups"]
