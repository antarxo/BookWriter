from __future__ import annotations

from collections import defaultdict
from typing import Any


VISUAL_TYPES = {"image", "figure"}


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _union(boxes: list[list[float]]) -> list[float] | None:
    boxes = [box for box in boxes if _bbox(box)]
    if not boxes:
        return None
    return [
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    ]


def _item_page(item: dict[str, Any]) -> int:
    for key in ("pdfPage", "inferredPage", "markdownPageHint"):
        try:
            page = int(item.get(key) or 0)
        except (TypeError, ValueError):
            page = 0
        if page:
            return page
    return 0


def _placed_text(item: dict[str, Any], page_no: int) -> bool:
    if str(item.get("type") or "") in VISUAL_TYPES | {"display_equation"}:
        return False
    return _item_page(item) == page_no and bool(_bbox(item.get("bbox")))


def _neighbour_band(
    ordered_items: list[dict[str, Any]],
    target_index: int,
    page_no: int,
    page_height: float,
) -> tuple[float, float, dict[str, Any]] | None:
    previous: dict[str, Any] | None = None
    following: dict[str, Any] | None = None
    for index in range(target_index - 1, -1, -1):
        candidate = ordered_items[index]
        if _placed_text(candidate, page_no):
            previous = candidate
            break
    for index in range(target_index + 1, len(ordered_items)):
        candidate = ordered_items[index]
        if _placed_text(candidate, page_no):
            following = candidate
            break

    previous_box = _bbox((previous or {}).get("bbox"))
    following_box = _bbox((following or {}).get("bbox"))
    if not previous_box and not following_box:
        return None

    y0 = previous_box[3] if previous_box else 0.0
    y1 = following_box[1] if following_box else page_height
    if y1 <= y0:
        return None
    return y0, y1, {
        "previousMarkdownId": (previous or {}).get("id"),
        "previousBBox": previous_box,
        "nextMarkdownId": (following or {}).get("id"),
        "nextBBox": following_box,
    }


def _bind_item(
    item: dict[str, Any],
    page_no: int,
    group: dict[str, Any],
    mode: str,
) -> None:
    box = _bbox(group.get("bbox"))
    group_id = str(group.get("id") or "")
    item["pdfPage"] = page_no
    item["pdfRegion"] = group_id
    item["pdfParentRegion"] = None
    item["pdfLineIndex"] = None
    item["pdfRowGranularity"] = "pdf-figure-group"
    item["bbox"] = box
    item["status"] = "figure-group"
    item["manifestOutcome"] = "pdf-figure-group-witness-confirmed"
    item["matchMode"] = mode
    item["score"] = None
    geometry = item.get("pdfGeometry") if isinstance(item.get("pdfGeometry"), dict) else {}
    geometry["bbox"] = box
    geometry["regionBBox"] = box
    geometry["page"] = page_no
    item["pdfGeometry"] = geometry
    item["pdfFigureGroup"] = {
        "id": group_id,
        "memberIds": list(group.get("member_ids") or []),
        "memberKinds": list(group.get("member_kinds") or []),
        "source": "page_structure.visual_groups",
    }


def bind_visuals_to_pdf_groups(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
) -> dict[str, Any]:
    """Bind Markdown visuals to real PDF figure geometry.

    First use exact per-page count agreement. If the PDF has fragmented one visual
    into several figure groups, recover only when the Markdown visual lies between
    confirmed neighbouring Markdown/PDF text witnesses; all PDF figure groups whose
    vertical centres lie in that evidence band are consolidated into one synthetic
    page_structure group. No raster-coordinate guessing is used.
    """
    all_items = sorted(
        list(markdown_pdf_spine.get("items", []) or []),
        key=lambda row: int(row.get("orderIndex") or 0),
    )
    item_index = {str(item.get("id") or ""): index for index, item in enumerate(all_items)}

    items_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in all_items:
        if str(item.get("type") or "") not in VISUAL_TYPES:
            continue
        if item.get("pdfRegion") and _bbox(item.get("bbox")):
            continue
        page_no = _item_page(item)
        if page_no:
            items_by_page[page_no].append(item)

    page_lookup = {
        int(page.get("page") or 0): page
        for page in page_structure.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    groups_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for page_no, page in page_lookup.items():
        for group in page.get("visual_groups", []) or []:
            if str(group.get("kind") or "") != "figure":
                continue
            if _bbox(group.get("bbox")):
                groups_by_page[page_no].append(group)

    bound = 0
    consolidated = 0
    pages: list[dict[str, Any]] = []
    for page_no in sorted(set(items_by_page) | set(groups_by_page)):
        items = sorted(items_by_page.get(page_no, []), key=lambda row: int(row.get("orderIndex") or 0))
        groups = sorted(
            groups_by_page.get(page_no, []),
            key=lambda group: (
                (_bbox(group.get("bbox")) or [0, 0, 0, 0])[1],
                (_bbox(group.get("bbox")) or [0, 0, 0, 0])[0],
            ),
        )
        page_record = {
            "page": page_no,
            "unplacedMarkdownVisualCount": len(items),
            "pdfFigureGroupCount": len(groups),
            "groupDelta": len(groups) - len(items),
            "markdownItems": [
                {
                    "id": item.get("id"),
                    "type": item.get("type"),
                    "orderIndex": item.get("orderIndex"),
                    "text": str(item.get("text") or item.get("rawMarkdown") or "")[:320],
                }
                for item in items
            ],
            "groups": [
                {
                    "id": group.get("id"),
                    "bbox": group.get("bbox"),
                    "memberIds": list(group.get("member_ids") or []),
                    "memberKinds": list(group.get("member_kinds") or []),
                }
                for group in groups
            ],
            "boundCount": 0,
            "consolidatedCount": 0,
            "recoveries": [],
            "policy": None,
        }
        if not items:
            page_record["policy"] = "no-unplaced-markdown-visuals"
            pages.append(page_record)
            continue

        if len(items) == len(groups):
            for item, group in zip(items, groups):
                _bind_item(item, page_no, group, "page-structure-figure-group-order")
                bound += 1
                page_record["boundCount"] += 1
            page_record["policy"] = "bound-by-equal-count-and-vertical-order"
            pages.append(page_record)
            continue

        page_info = page_lookup.get(page_no) or {}
        try:
            page_height = float(page_info.get("height_pt") or page_info.get("heightPt") or 0.0)
        except (TypeError, ValueError):
            page_height = 0.0
        if page_height <= 0:
            page_height = 10000.0

        claimed_ids: set[str] = set()
        for item in items:
            index = item_index.get(str(item.get("id") or ""))
            if index is None:
                continue
            band = _neighbour_band(all_items, index, page_no, page_height)
            if not band:
                continue
            y0, y1, evidence = band
            candidates: list[dict[str, Any]] = []
            for group in groups:
                group_id = str(group.get("id") or "")
                if group_id in claimed_ids:
                    continue
                box = _bbox(group.get("bbox"))
                if not box:
                    continue
                center_y = (box[1] + box[3]) / 2.0
                if y0 - 2.0 <= center_y <= y1 + 2.0:
                    candidates.append(group)
            if not candidates:
                continue

            # If another unplaced Markdown visual sits between the same two text
            # witnesses, the band does not uniquely identify ownership. Leave it
            # unresolved instead of splitting PDF fragments by guesswork.
            ambiguous_visuals = 0
            for other in items:
                if other is item:
                    continue
                other_index = item_index.get(str(other.get("id") or ""))
                if other_index is None:
                    continue
                other_band = _neighbour_band(all_items, other_index, page_no, page_height)
                if other_band and abs(other_band[0] - y0) < 0.5 and abs(other_band[1] - y1) < 0.5:
                    ambiguous_visuals += 1
            if ambiguous_visuals:
                page_record["recoveries"].append({
                    "markdownId": item.get("id"),
                    "status": "not-bound-shared-neighbour-band",
                    "candidateGroupCount": len(candidates),
                    "band": [round(y0, 3), round(y1, 3)],
                    **evidence,
                })
                continue

            member_ids: list[str] = []
            member_kinds: set[str] = set()
            for group in candidates:
                member_ids.extend(str(value) for value in (group.get("member_ids") or []) if value)
                member_kinds.update(str(value) for value in (group.get("member_kinds") or []) if value)
            union_box = _union([_bbox(group.get("bbox")) for group in candidates if _bbox(group.get("bbox"))])
            if not union_box:
                continue
            synthetic_id = f"mdvg-{item.get('id')}"
            synthetic = {
                "id": synthetic_id,
                "kind": "figure",
                "bbox": [round(value, 3) for value in union_box],
                "member_ids": member_ids,
                "member_kinds": sorted(member_kinds),
                "placement": "floating" if any(str(group.get("placement") or "") == "floating" for group in candidates) else "inline",
                "source": "markdown-neighbour-band-consolidation",
                "source_group_ids": [str(group.get("id") or "") for group in candidates],
            }
            page_info.setdefault("visual_groups", []).append(synthetic)
            _bind_item(item, page_no, synthetic, "page-structure-figure-groups-between-text-neighbours")
            claimed_ids.update(str(group.get("id") or "") for group in candidates)
            bound += 1
            consolidated += 1
            page_record["boundCount"] += 1
            page_record["consolidatedCount"] += 1
            page_record["recoveries"].append({
                "markdownId": item.get("id"),
                "status": "bound-consolidated-fragments",
                "candidateGroupCount": len(candidates),
                "sourceGroupIds": synthetic["source_group_ids"],
                "band": [round(y0, 3), round(y1, 3)],
                "bbox": synthetic["bbox"],
                **evidence,
            })

        page_record["policy"] = (
            "fragmented-groups-recovered-by-confirmed-text-neighbour-band"
            if page_record["boundCount"]
            else "no-bind-count-mismatch-no-unique-neighbour-band"
        )
        pages.append(page_record)

    visual_pages = [row for row in pages if int(row.get("unplacedMarkdownVisualCount") or 0) > 0]
    mismatch_pages = [row for row in visual_pages if int(row.get("boundCount") or 0) < int(row.get("unplacedMarkdownVisualCount") or 0)]
    markdown_visual_count = sum(int(row.get("unplacedMarkdownVisualCount") or 0) for row in visual_pages)
    pdf_group_count = sum(int(row.get("pdfFigureGroupCount") or 0) for row in visual_pages)
    extra_group_count = sum(max(0, int(row.get("groupDelta") or 0)) for row in visual_pages)
    missing_group_count = sum(max(0, -int(row.get("groupDelta") or 0)) for row in visual_pages)

    audit = {
        "version": "donorless-visual-group-binding-0.2",
        "source": "page_structure.visual_groups[kind=figure] + confirmed neighbouring text geometry",
        "policy": "equal-count binding first; fragmented groups consolidated only inside a unique Markdown-neighbour PDF text band",
        "boundCount": bound,
        "consolidatedVisualCount": consolidated,
        "summary": {
            "visualPageCount": len(visual_pages),
            "mismatchPageCount": len(mismatch_pages),
            "mismatchPageRate": round(len(mismatch_pages) / len(visual_pages), 5) if visual_pages else 0.0,
            "markdownVisualCount": markdown_visual_count,
            "pdfFigureGroupCount": pdf_group_count,
            "boundVisualCount": bound,
            "consolidatedVisualCount": consolidated,
            "bindingCoverage": round(bound / markdown_visual_count, 5) if markdown_visual_count else 1.0,
            "extraPdfFigureGroupCount": extra_group_count,
            "missingPdfFigureGroupCount": missing_group_count,
        },
        "pages": pages,
    }
    markdown_pdf_spine["visualGroupBinding"] = audit
    return audit


__all__ = ["bind_visuals_to_pdf_groups"]
