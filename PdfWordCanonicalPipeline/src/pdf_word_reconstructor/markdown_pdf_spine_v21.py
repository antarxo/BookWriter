from __future__ import annotations

from typing import Any

from .common import normalize_text
from .markdown_pdf_spine_v20 import build_markdown_pdf_spine as _build_v20
from .markdown_pdf_spine_v16 import _raw_text_regions
from .markdown_pdf_spine_v08 import _placed
from .markdown_pdf_spine_v07 import _item_text, _page_no, _region_lookup
from .markdown_pdf_spine_v02 import _score
from .markdown_pdf_spine_v03 import _bbox, _typography_from_lines

VERSION = "markdown-pdf-spine-0.21"

PARAGRAPH_TYPES = {"paragraph"}


def _row_order(row: dict[str, Any]) -> tuple[float, float]:
    box = _bbox(row.get("bbox")) or [0.0, 0.0, 0.0, 0.0]
    return float(box[1]), float(box[0])


def _union_bbox(rows: list[dict[str, Any]]) -> list[float] | None:
    boxes = [_bbox(row.get("bbox")) for row in rows]
    boxes = [box for box in boxes if box]
    if not boxes:
        return None
    return [
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    ]


def _attach_composite(
    item: dict[str, Any],
    rows: list[dict[str, Any]],
    page: int,
    score: float,
    regions: dict[str, dict[str, Any]],
) -> None:
    ids = [str(row.get("id") or "") for row in rows]
    bbox = _union_bbox(rows)
    synthetic_id = f"p{page}-composite-" + "__".join(ids)
    combined_text = " ".join(str(row.get("text") or "").strip() for row in rows if str(row.get("text") or "").strip())

    item["pdfPage"] = page
    item["pdfRegion"] = synthetic_id
    item["pdfParentRegion"] = None
    item["pdfRowGranularity"] = "pdf-region-composite"
    item["pdfConstituentRegions"] = ids
    item["bbox"] = bbox
    item["pdfText"] = combined_text
    item["status"] = "medium"
    item["manifestOutcome"] = "pdf-witness-confirmed"
    item["matchMode"] = "contiguous-multi-region-paragraph"
    item["score"] = round(float(score), 2)

    lines: list[dict[str, Any]] = []
    original_boxes: list[list[float]] = []
    for row in rows:
        source = regions.get(str(row.get("id") or "")) or {}
        lines.extend(list(source.get("lines", []) or []))
        original = _bbox(source.get("original_block_bbox"))
        if original:
            original_boxes.append(original)
    item["pdfTypography"] = _typography_from_lines(lines, bbox, "contiguous-multi-region-paragraph")

    geometry = item.get("pdfGeometry") if isinstance(item.get("pdfGeometry"), dict) else {}
    geometry["bbox"] = bbox
    geometry["regionBBox"] = bbox
    if original_boxes:
        geometry["originalBlockBBox"] = [
            min(float(box[0]) for box in original_boxes),
            min(float(box[1]) for box in original_boxes),
            max(float(box[2]) for box in original_boxes),
            max(float(box[3]) for box in original_boxes),
        ]
    geometry["page"] = page
    geometry["constituentRegions"] = ids
    item["pdfGeometry"] = geometry


def _recover_multi_region_paragraphs(
    items: list[dict[str, Any]],
    raw_regions: dict[int, list[dict[str, Any]]],
    regions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    used_atomic: set[tuple[int, str]] = set()
    for item in items:
        page = int(item.get("pdfPage") or 0)
        region = str(item.get("pdfRegion") or "")
        if page and region:
            used_atomic.add((page, region))
        for part in item.get("pdfConstituentRegions") or []:
            if page and part:
                used_atomic.add((page, str(part)))

    recovered: list[dict[str, Any]] = []

    for item in items:
        if str(item.get("type") or "") not in PARAGRAPH_TYPES or _placed(item):
            continue
        hinted_page = _page_no(item)
        source = normalize_text(_item_text(item))
        if not hinted_page or len(source) < 90:
            continue

        ranked: list[tuple[float, int, list[dict[str, Any]]]] = []
        for page in (hinted_page - 1, hinted_page, hinted_page + 1):
            if page <= 0:
                continue
            page_rows = sorted(raw_regions.get(page, []), key=_row_order)
            # Headers/footers are never paragraph constituents.
            page_rows = [
                row for row in page_rows
                if str(row.get("semanticType") or "") not in {"header", "footer"}
            ]
            for start in range(len(page_rows)):
                for size in (2, 3, 4):
                    block = page_rows[start:start + size]
                    if len(block) != size:
                        continue
                    ids = [str(row.get("id") or "") for row in block]
                    if not all(ids) or any((page, region_id) in used_atomic for region_id in ids):
                        continue
                    # Do not bridge huge vertical gaps: the blocks must form a local run.
                    boxes = [_bbox(row.get("bbox")) for row in block]
                    if any(box is None for box in boxes):
                        continue
                    gaps = [float(boxes[i + 1][1]) - float(boxes[i][3]) for i in range(len(boxes) - 1)]
                    heights = [max(1.0, float(box[3]) - float(box[1])) for box in boxes]
                    local_scale = max(heights)
                    if any(gap > max(24.0, local_scale * 2.2) for gap in gaps):
                        continue
                    combined = normalize_text(" ".join(str(row.get("text") or "") for row in block))
                    if len(combined) < 40:
                        continue
                    ranked.append((_score(source, combined), page, block))

        ranked.sort(key=lambda value: value[0], reverse=True)
        if not ranked:
            continue

        best_score, best_page, best_rows = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        margin = best_score - second_score

        # Composite recovery must be stronger than the single-region fuzzy passes.
        if not (best_score >= 94.0 and margin >= 5.0):
            continue

        _attach_composite(item, best_rows, best_page, best_score, regions)
        ids = [str(row.get("id") or "") for row in best_rows]
        for region_id in ids:
            used_atomic.add((best_page, region_id))
        recovered.append({
            "markdownId": item.get("id"),
            "hintedPage": hinted_page,
            "pdfPage": best_page,
            "pageDelta": best_page - hinted_page,
            "constituentRegions": ids,
            "score": round(float(best_score), 2),
            "secondScore": round(float(second_score), 2),
            "margin": round(float(margin), 2),
            "pdfText": " ".join(str(row.get("text") or "") for row in best_rows)[:220],
        })

    return recovered


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v20(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])
    raw_regions = _raw_text_regions(pdf_analysis)
    regions = _region_lookup(pdf_analysis)

    recovered = _recover_multi_region_paragraphs(items, raw_regions, regions)

    result["version"] = VERSION
    result["multiRegionParagraphRecovery"] = {
        "recoveredCount": len(recovered),
        "items": recovered[:120],
        "policy": (
            "remaining long paragraph items may bind to one unused contiguous run of 2-4 full PDF text regions on hinted page ±1; "
            "headers/footers excluded; no ownership changes; score>=94 and margin>=5; Markdown remains content authority"
        ),
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
