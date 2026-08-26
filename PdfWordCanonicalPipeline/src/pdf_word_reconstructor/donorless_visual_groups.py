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


def _item_page(item: dict[str, Any]) -> int:
    for key in ("pdfPage", "inferredPage", "markdownPageHint"):
        try:
            page = int(item.get(key) or 0)
        except (TypeError, ValueError):
            page = 0
        if page:
            return page
    return 0


def bind_visuals_to_pdf_groups(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
) -> dict[str, Any]:
    """Bind unplaced Markdown image/figure items to clustered PDF figure groups.

    Binding is intentionally strict: on each page, all currently unplaced Markdown
    visuals are bound only when their count equals the count of available PDF
    figure groups. Geometry comes exclusively from page_structure.visual_groups.
    """
    items_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in markdown_pdf_spine.get("items", []) or []:
        if str(item.get("type") or "") not in VISUAL_TYPES:
            continue
        if item.get("pdfRegion") and _bbox(item.get("bbox")):
            continue
        page_no = _item_page(item)
        if page_no:
            items_by_page[page_no].append(item)

    groups_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for group in page.get("visual_groups", []) or []:
            if str(group.get("kind") or "") != "figure":
                continue
            if not _bbox(group.get("bbox")):
                continue
            groups_by_page[page_no].append(group)

    bound = 0
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
            "policy": None,
        }
        if not items:
            page_record["policy"] = "no-unplaced-markdown-visuals"
            pages.append(page_record)
            continue
        if len(items) != len(groups):
            page_record["policy"] = "no-bind-count-mismatch"
            pages.append(page_record)
            continue

        for item, group in zip(items, groups):
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
            item["matchMode"] = "page-structure-figure-group-order"
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
            bound += 1
            page_record["boundCount"] += 1
        page_record["policy"] = "bound-by-equal-count-and-vertical-order"
        pages.append(page_record)

    visual_pages = [row for row in pages if int(row.get("unplacedMarkdownVisualCount") or 0) > 0]
    mismatch_pages = [row for row in visual_pages if row.get("policy") == "no-bind-count-mismatch"]
    markdown_visual_count = sum(int(row.get("unplacedMarkdownVisualCount") or 0) for row in visual_pages)
    pdf_group_count = sum(int(row.get("pdfFigureGroupCount") or 0) for row in visual_pages)
    extra_group_count = sum(max(0, int(row.get("groupDelta") or 0)) for row in visual_pages)
    missing_group_count = sum(max(0, -int(row.get("groupDelta") or 0)) for row in visual_pages)

    audit = {
        "version": "donorless-visual-group-binding-0.1",
        "source": "page_structure.visual_groups[kind=figure]",
        "policy": "bind clustered figure groups only when per-page counts agree",
        "boundCount": bound,
        "summary": {
            "visualPageCount": len(visual_pages),
            "mismatchPageCount": len(mismatch_pages),
            "mismatchPageRate": round(len(mismatch_pages) / len(visual_pages), 5) if visual_pages else 0.0,
            "markdownVisualCount": markdown_visual_count,
            "pdfFigureGroupCount": pdf_group_count,
            "boundVisualCount": bound,
            "bindingCoverage": round(bound / markdown_visual_count, 5) if markdown_visual_count else 1.0,
            "extraPdfFigureGroupCount": extra_group_count,
            "missingPdfFigureGroupCount": missing_group_count,
        },
        "pages": pages,
    }
    markdown_pdf_spine["visualGroupBinding"] = audit
    return audit


__all__ = ["bind_visuals_to_pdf_groups"]
