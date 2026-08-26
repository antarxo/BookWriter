from __future__ import annotations

from collections import defaultdict
from typing import Any


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
            value = int(item.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return 0


def bind_display_equations_to_pdf_groups(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
) -> dict[str, Any]:
    """Bind unplaced Markdown display equations to existing clustered PDF equation groups.

    This does not inspect raw equation fragments. Clustering is owned by page_structure,
    which already groups nearby PDF equation fragments and splits groups across prose
    barriers. Binding is allowed only when counts agree on a page; otherwise nothing is
    inferred for that page.
    """
    items_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in markdown_pdf_spine.get("items", []) or []:
        if str(item.get("type") or "") != "display_equation":
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
            if str(group.get("kind") or "") != "equation":
                continue
            if not _bbox(group.get("bbox")):
                continue
            groups_by_page[page_no].append(group)

    bound = 0
    pages: list[dict[str, Any]] = []
    used_group_ids: set[str] = set()
    for page_no in sorted(set(items_by_page) | set(groups_by_page)):
        items = sorted(items_by_page.get(page_no, []), key=lambda row: int(row.get("orderIndex") or 0))
        groups = [group for group in groups_by_page.get(page_no, []) if str(group.get("id") or "") not in used_group_ids]
        groups.sort(key=lambda group: ((_bbox(group.get("bbox")) or [0, 0, 0, 0])[1], (_bbox(group.get("bbox")) or [0, 0, 0, 0])[0]))
        page_record = {
            "page": page_no,
            "unplacedMarkdownDisplayEquationCount": len(items),
            "pdfEquationGroupCount": len(groups),
            "groupMemberCounts": [len(group.get("member_ids") or []) for group in groups],
            "boundCount": 0,
            "policy": None,
        }
        if not items:
            page_record["policy"] = "no-unplaced-markdown-equations"
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
            item["pdfRowGranularity"] = "pdf-equation-group"
            item["bbox"] = box
            item["status"] = "equation-group"
            item["manifestOutcome"] = "pdf-equation-group-witness-confirmed"
            item["matchMode"] = "page-structure-equation-group-order"
            item["score"] = None
            geometry = item.get("pdfGeometry") if isinstance(item.get("pdfGeometry"), dict) else {}
            geometry["bbox"] = box
            geometry["regionBBox"] = box
            geometry["page"] = page_no
            item["pdfGeometry"] = geometry
            item["pdfEquationGroup"] = {
                "id": group_id,
                "memberIds": list(group.get("member_ids") or []),
                "memberKinds": list(group.get("member_kinds") or []),
                "source": "page_structure.visual_groups",
            }
            used_group_ids.add(group_id)
            bound += 1
            page_record["boundCount"] += 1
        page_record["policy"] = "bound-by-equal-count-and-vertical-order"
        pages.append(page_record)

    audit = {
        "version": "donorless-equation-group-binding-0.1",
        "source": "page_structure.visual_groups[kind=equation]",
        "boundCount": bound,
        "policy": "never-bind-raw-pdf-equation-fragments; bind clustered groups only when per-page counts agree",
        "pages": pages,
    }
    markdown_pdf_spine["displayEquationGroupBinding"] = audit
    return audit
