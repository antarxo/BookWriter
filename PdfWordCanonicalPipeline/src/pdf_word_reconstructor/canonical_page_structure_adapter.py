from __future__ import annotations

from copy import deepcopy
from statistics import median
from typing import Any


VERSION = "canonical-page-structure-adapter-0.2"
_TEXT_SEMANTICS = {"paragraph", "heading", "caption", "list"}
_NON_TEXT_CANONICAL_TYPES = {"figure": "image", "equation": "display_equation"}
_ORIGINAL_TEXT_TYPES = {"paragraph", "heading", "title", "author", "caption", "list", "latex_list"}


def _box(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _intersection_fraction(inner: list[float], outer: list[float] | None) -> float:
    if outer is None:
        return 0.0
    x0 = max(inner[0], outer[0])
    y0 = max(inner[1], outer[1])
    x1 = min(inner[2], outer[2])
    y1 = min(inner[3], outer[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    intersection = (x1 - x0) * (y1 - y0)
    area = max(1.0, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return intersection / area


def _line_records_by_id(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    line_page = page.get("mathpixLinePageMap") if isinstance(page.get("mathpixLinePageMap"), dict) else {}
    return {
        str(record.get("id")): record
        for record in line_page.get("objects", []) or []
        if record.get("id")
    }


def _line_box(record: dict[str, Any] | None) -> list[float] | None:
    bbox = (record or {}).get("bbox_pt") if isinstance((record or {}).get("bbox_pt"), dict) else {}
    if not bbox:
        return None
    return _box([bbox.get("x0"), bbox.get("y0"), bbox.get("x1"), bbox.get("y1")])


def _block_box(page: dict[str, Any], block: dict[str, Any]) -> list[float] | None:
    geometry = block.get("geometry") if isinstance(block.get("geometry"), dict) else {}
    line_ids = [str(value) for value in geometry.get("lineIds", []) or [] if value]
    records = _line_records_by_id(page)
    boxes = [_line_box(records.get(line_id)) for line_id in line_ids]
    return _union([value for value in boxes if value is not None])


def _active_column_index(page: dict[str, Any], box: list[float]) -> tuple[int | None, bool]:
    columns = [column for column in page.get("columns", []) or [] if isinstance(column, dict)]
    if len(columns) != 2 or str(page.get("layout_mode") or "") != "two_columns":
        return None, False
    left = _box([columns[0].get("x0"), columns[0].get("y0"), columns[0].get("x1"), columns[0].get("y1")])
    right = _box([columns[1].get("x0"), columns[1].get("y0"), columns[1].get("x1"), columns[1].get("y1")])
    if left is None or right is None:
        return None, False
    if box[0] < left[2] and box[2] > right[0]:
        return None, True
    center_x = (box[0] + box[2]) / 2.0
    left_center = (left[0] + left[2]) / 2.0
    right_center = (right[0] + right[2]) / 2.0
    return (0 if abs(center_x - left_center) <= abs(center_x - right_center) else 1), False


def _dominant_font_size_pt(page: dict[str, Any], block: dict[str, Any]) -> float | None:
    geometry = block.get("geometry") if isinstance(block.get("geometry"), dict) else {}
    line_ids = [str(value) for value in geometry.get("lineIds", []) or [] if value]
    records = _line_records_by_id(page)
    sizes: list[float] = []
    for line_id in line_ids:
        record = records.get(line_id) or {}
        try:
            font_size = float(record.get("font_size"))
            scale = float(((page.get("mathpixLinePageMap") or {}).get("scale_pt_per_px")) or 0.0)
        except (TypeError, ValueError):
            continue
        if font_size > 0 and scale > 0:
            sizes.append(font_size * scale)
    return round(float(median(sizes)), 3) if sizes else None


def _canonical_slot_id(block: dict[str, Any]) -> str:
    return f"canonical-{block.get('id')}"


def apply_canonical_evidence_to_page_structure(
    page_structure: dict[str, Any],
    canonical_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Materialize resolved canonical text blocks into the existing page map.

    Canonical fusion owns text semantics/content, Mathpix Lines own local text
    geometry, and page_structure owns global page topology plus PDF visual objects.
    The adapter does not rematch, invent geometry, or create visual/equation objects.
    """
    pages = {
        int(page.get("page") or 0): page
        for page in page_structure.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }

    materialized: list[dict[str, Any]] = []
    rail_materialized: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []

    # Package-first text authority is canonical MMD+Lines. Preserve PDF-owned
    # visuals, but replace any pre-existing ordinary text flow with canonical text.
    for page in pages.values():
        page["flow"] = [item for item in page.get("flow", []) or [] if str(item.get("type") or "") != "text"]
        page["callouts"] = [
            item for item in page.get("callouts", []) or []
            if str(item.get("content_source") or "") != "canonical-evidence-fusion"
        ]

    for block in canonical_evidence.get("blocks", []) or []:
        semantic = str(((block.get("semantic") or {}).get("type")) or "")
        if semantic not in _TEXT_SEMANTICS:
            unsupported.append({
                "canonicalId": block.get("id"),
                "semanticType": semantic,
                "reason": "non-text-block-owned-by-existing-visual-or-equation-path",
            })
            continue
        page_no = int(((block.get("pageAssignment") or {}).get("physicalPage")) or 0)
        page = pages.get(page_no)
        geometry = block.get("geometry") if isinstance(block.get("geometry"), dict) else {}
        line_ids = [str(value) for value in geometry.get("lineIds", []) or [] if value]
        box = _block_box(page or {}, block) if page is not None else None
        text = str(((block.get("content") or {}).get("text")) or "").strip()
        if page is None or box is None or not text:
            unresolved.append({
                "canonicalId": block.get("id"),
                "page": page_no or None,
                "semanticType": semantic,
                "lineIds": line_ids,
                "reason": "missing-page-lines-bbox-or-content",
            })
            continue

        item_id = _canonical_slot_id(block)
        rail_box = _box(page.get("outer_rail_box"))
        in_outer_rail = _intersection_fraction(box, rail_box) >= 0.65
        common = {
            "id": item_id,
            "bbox": [round(value, 3) for value in box],
            "text": text,
            "content_source": "canonical-evidence-fusion",
            "canonical_block_id": block.get("id"),
            "markdown_ids": list(((block.get("content") or {}).get("markdownIds")) or []),
            "mathpix_line_ids": line_ids,
            "mathpix_line_numbers": list(geometry.get("lineNumbers") or []),
            "typography_evidence": block.get("typographyEvidence") or {},
        }
        if in_outer_rail:
            page.setdefault("callouts", []).append({
                **common,
                "semantic": {"type": semantic, "source": "canonical-evidence-fusion"},
                "semantic_type": semantic,
                "contained_visual_groups": [],
                "canonical_outer_rail": True,
            })
            rail_materialized.append({"canonicalId": block.get("id"), "page": page_no, "slotId": item_id})
            continue

        column_index, spanning = _active_column_index(page, box)
        flow_item = {
            **common,
            "type": "text",
            "semantic_type": semantic,
            "region_ids": line_ids,
        }
        if column_index is not None:
            flow_item["column_index"] = column_index
            flow_item["spanning"] = False
        elif spanning:
            flow_item["column_index"] = None
            flow_item["spanning"] = True
        page.setdefault("flow", []).append(flow_item)
        materialized.append({"canonicalId": block.get("id"), "page": page_no, "slotId": item_id})

    for page in pages.values():
        columns = list(page.get("columns", []) or [])
        if len(columns) == 2 and str(page.get("layout_mode") or "") == "two_columns":
            page["flow"] = sorted(
                page.get("flow", []) or [],
                key=lambda item: (
                    2 if item.get("column_index") is None else int(item.get("column_index") or 0),
                    float((item.get("bbox") or [0, 0, 0, 0])[1]),
                    float((item.get("bbox") or [0, 0, 0, 0])[0]),
                ),
            )
        else:
            page["flow"] = sorted(
                page.get("flow", []) or [],
                key=lambda item: (
                    float((item.get("bbox") or [0, 0, 0, 0])[1]),
                    float((item.get("bbox") or [0, 0, 0, 0])[0]),
                ),
            )

    report = {
        "version": VERSION,
        "materializedTextFlowCount": len(materialized),
        "materializedOuterRailTextCount": len(rail_materialized),
        "unresolvedTextBlockCount": len(unresolved),
        "nonTextBlockCount": len(unsupported),
        "policy": (
            "canonical MMD+Lines blocks materialize text only; PDF/page_structure retains global topology and visual ownership; "
            "outer-rail text uses existing outer_rail_box; no rematching, invented geometry, or visual duplication"
        ),
        "materialized": materialized,
        "outerRail": rail_materialized,
        "unresolved": unresolved,
        "nonText": unsupported,
    }
    page_structure["canonicalEvidenceAdapter"] = report
    return report


def canonicalize_markdown_pdf_spine(
    original_spine: dict[str, Any],
    page_structure: dict[str, Any],
    canonical_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Replace matched MMD records with the already-aligned canonical blocks.

    Text blocks use the exact slot IDs materialized above, so page-layout-spine can
    bind by identity rather than rematching. Matched figure/equation blocks carry
    canonical page+bbox evidence but remain owned by the existing visual/equation
    binding paths. Only unmatched original MMD records are retained unchanged.
    """
    pages = {
        int(page.get("page") or 0): page
        for page in page_structure.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    original_items = [deepcopy(item) for item in original_spine.get("items", []) or []]
    original_by_id = {
        str(item.get("id")): item
        for item in original_items
        if item.get("id")
    }
    accounted_markdown_ids: set[str] = set()
    canonical_items: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for order_index, block in enumerate(canonical_evidence.get("blocks", []) or []):
        semantic = str(((block.get("semantic") or {}).get("type")) or "")
        page_no = int(((block.get("pageAssignment") or {}).get("physicalPage")) or 0)
        page = pages.get(page_no)
        box = _block_box(page or {}, block) if page is not None else None
        content = block.get("content") if isinstance(block.get("content"), dict) else {}
        text = str(content.get("text") or "")
        markdown_ids = [str(value) for value in content.get("markdownIds", []) or [] if value]
        accounted_markdown_ids.update(markdown_ids)
        source_item = next((original_by_id.get(markdown_id) for markdown_id in markdown_ids if markdown_id in original_by_id), None) or {}

        if page is None or box is None:
            unresolved.append({
                "canonicalId": block.get("id"),
                "semanticType": semantic,
                "page": page_no or None,
                "markdownIds": markdown_ids,
                "reason": "canonical-block-lacks-pdf-point-bbox",
            })
            continue

        if semantic in _TEXT_SEMANTICS:
            item_type = "heading" if semantic == "heading" else ("caption" if semantic == "caption" else ("list" if semantic == "list" else "paragraph"))
            pdf_region = _canonical_slot_id(block)
            row_granularity = "canonical-mmd-lines-text-block"
        elif semantic in _NON_TEXT_CANONICAL_TYPES:
            item_type = _NON_TEXT_CANONICAL_TYPES[semantic]
            geometry = block.get("geometry") if isinstance(block.get("geometry"), dict) else {}
            line_ids = [str(value) for value in geometry.get("lineIds", []) or [] if value]
            pdf_region = line_ids[0] if len(line_ids) == 1 else str(block.get("id") or "")
            row_granularity = "canonical-mmd-lines-nontext-block"
        else:
            # Preserve uncommon semantics as paragraphs only when they carry text;
            # otherwise retain the original MMD record through the unmatched path.
            if not text.strip():
                continue
            item_type = str(source_item.get("type") or semantic or "paragraph")
            pdf_region = _canonical_slot_id(block)
            row_granularity = "canonical-mmd-lines-generic-block"

        font_size = _dominant_font_size_pt(page, block)
        typography = {
            "confidence": "medium" if font_size is not None else "none",
            "source": "mathpix-lines-via-canonical-evidence",
            "fontFamily": {"dominant": None},
            "fontSizePt": {"dominant": font_size},
            "emphasis": {},
            "color": {"dominant": None},
        }
        geometry = block.get("geometry") if isinstance(block.get("geometry"), dict) else {}
        authoritative = {
            "text": text,
            "plainText": text,
            "rawMarkdown": str(content.get("rawMarkdown") or ""),
            "source": str(content.get("source") or "mathpix-markdown-primary"),
        }
        if item_type == "display_equation":
            authoritative["latex"] = str(content.get("rawMarkdown") or text)

        canonical_items.append({
            "id": str(block.get("id") or f"canonical-{order_index:05d}"),
            "type": item_type,
            "orderIndex": order_index,
            "text": text,
            "rawMarkdown": str(content.get("rawMarkdown") or ""),
            "authoritativeContent": authoritative,
            "contentContract": authoritative,
            "sourceMarkdownIds": markdown_ids,
            "pdfPage": page_no,
            "pdfRegion": pdf_region,
            "pdfParentRegion": None,
            "pdfLineIndex": min([int(value) for value in geometry.get("lineNumbers", []) or []], default=None),
            "pdfRowGranularity": row_granularity,
            "bbox": [round(value, 3) for value in box],
            "status": "canonical-mmd-lines-evidence",
            "manifestOutcome": "canonical-mmd-lines-aligned",
            "matchMode": "canonical-mmd-lines-global-alignment",
            "score": float(((block.get("evidence") or {}).get("alignmentScore")) or 0.0),
            "pdfTypography": typography,
            "canonicalEvidence": block,
        })

    retained_original = [
        item for item in original_items
        if str(item.get("id") or "") not in accounted_markdown_ids
    ]
    next_order = len(canonical_items)
    for item in retained_original:
        item["orderIndex"] = next_order
        next_order += 1

    result = deepcopy(original_spine)
    result["items"] = canonical_items + retained_original
    result["canonicalEvidenceSpine"] = {
        "version": VERSION,
        "canonicalItemCount": len(canonical_items),
        "retainedUnmatchedOriginalItemCount": len(retained_original),
        "accountedOriginalMarkdownIdCount": len(accounted_markdown_ids),
        "unresolvedCanonicalBlockCount": len(unresolved),
        "policy": (
            "matched MMD content is represented by canonical MMD+Lines blocks; text binds by canonical slot identity; "
            "non-text blocks keep canonical page/bbox evidence for existing visual/equation binders; unmatched MMD remains explicit"
        ),
        "unresolved": unresolved,
    }
    result["coverage"] = round(len(canonical_items) / max(1, len(canonical_items) + len(retained_original)), 5)
    return result


__all__ = [
    "VERSION",
    "apply_canonical_evidence_to_page_structure",
    "canonicalize_markdown_pdf_spine",
]
