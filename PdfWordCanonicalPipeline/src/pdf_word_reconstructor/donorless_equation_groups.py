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


def _pdf_region_lookup(pdf_analysis: dict[str, Any] | None) -> dict[tuple[int, str], dict[str, Any]]:
    lookup: dict[tuple[int, str], dict[str, Any]] = {}
    for page in (pdf_analysis or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for region in page.get("regions", []) or []:
            region_id = str(region.get("id") or "")
            if region_id:
                lookup[(page_no, region_id)] = region
    return lookup


def _group_audit_record(
    page_no: int,
    group: dict[str, Any],
    region_lookup: dict[tuple[int, str], dict[str, Any]],
) -> dict[str, Any]:
    members: list[dict[str, Any]] = []
    for member_id in group.get("member_ids", []) or []:
        region = region_lookup.get((page_no, str(member_id))) or {}
        semantic = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
        members.append({
            "id": member_id,
            "bbox": region.get("bbox"),
            "text": str(region.get("text") or "")[:260],
            "semanticType": semantic.get("type"),
            "semanticConfidence": semantic.get("confidence"),
            "semanticReasons": list(semantic.get("reasons") or []),
        })
    return {
        "id": group.get("id"),
        "bbox": group.get("bbox"),
        "memberCount": len(group.get("member_ids") or []),
        "memberIds": list(group.get("member_ids") or []),
        "members": members,
    }


def bind_display_equations_to_pdf_groups(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
    pdf_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind unplaced Markdown display equations to clustered PDF equation groups.

    Raw PDF equation fragments are never bound directly. page_structure owns the
    geometric clustering. Per-page binding is allowed only when counts agree.
    The audit keeps the underlying PDF member evidence so count mismatches can be
    inspected before any matching policy is relaxed.
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

    region_lookup = _pdf_region_lookup(pdf_analysis)
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
            "groupDelta": len(groups) - len(items),
            "groupMemberCounts": [len(group.get("member_ids") or []) for group in groups],
            "groups": [_group_audit_record(page_no, group, region_lookup) for group in groups],
            "markdownItems": [
                {
                    "id": item.get("id"),
                    "orderIndex": item.get("orderIndex"),
                    "text": str(item.get("text") or "")[:320],
                }
                for item in items
            ],
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

    equation_pages = [row for row in pages if int(row.get("unplacedMarkdownDisplayEquationCount") or 0) > 0]
    mismatch_pages = [row for row in equation_pages if row.get("policy") == "no-bind-count-mismatch"]
    markdown_equation_count = sum(int(row.get("unplacedMarkdownDisplayEquationCount") or 0) for row in equation_pages)
    pdf_group_count = sum(int(row.get("pdfEquationGroupCount") or 0) for row in equation_pages)
    extra_group_count = sum(max(0, int(row.get("groupDelta") or 0)) for row in equation_pages)
    missing_group_count = sum(max(0, -int(row.get("groupDelta") or 0)) for row in equation_pages)

    audit = {
        "version": "donorless-equation-group-binding-0.3",
        "source": "page_structure.visual_groups[kind=equation]",
        "boundCount": bound,
        "policy": "never-bind-raw-pdf-equation-fragments; bind clustered groups only when per-page counts agree",
        "summary": {
            "equationPageCount": len(equation_pages),
            "mismatchPageCount": len(mismatch_pages),
            "mismatchPageRate": round(len(mismatch_pages) / len(equation_pages), 5) if equation_pages else 0.0,
            "markdownDisplayEquationCount": markdown_equation_count,
            "pdfEquationGroupCount": pdf_group_count,
            "boundDisplayEquationCount": bound,
            "bindingCoverage": round(bound / markdown_equation_count, 5) if markdown_equation_count else 1.0,
            "extraPdfEquationGroupCount": extra_group_count,
            "missingPdfEquationGroupCount": missing_group_count,
            "extraGroupPerMarkdownEquation": round(extra_group_count / markdown_equation_count, 5) if markdown_equation_count else 0.0,
        },
        "pages": pages,
    }
    markdown_pdf_spine["displayEquationGroupBinding"] = audit
    return audit
