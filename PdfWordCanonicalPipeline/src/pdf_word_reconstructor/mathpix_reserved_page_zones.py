from __future__ import annotations

from statistics import median
from typing import Any


VERSION = "mathpix-reserved-page-zones-0.1"


def _box(obj: dict[str, Any]) -> list[float] | None:
    src = obj.get("bbox_pt") if isinstance(obj.get("bbox_pt"), dict) else None
    if not src:
        return None
    try:
        box = [float(src.get("x0")), float(src.get("y0")), float(src.get("x1")), float(src.get("y1"))]
    except (TypeError, ValueError):
        return None
    return box if box[2] > box[0] and box[3] > box[1] else None


def _body_objects(page: dict[str, Any]) -> list[list[float]]:
    excluded = {
        "page_info", "column", "table_row", "table_column",
        "table_of_contents_container", "table_of_contents_row", "table_of_contents_number",
    }
    boxes = []
    for obj in page.get("objects", []) or []:
        if str(obj.get("type") or "") in excluded:
            continue
        box = _box(obj)
        if box:
            boxes.append(box)
    return boxes


def build_reserved_page_zone_profile(
    line_map: dict[str, Any],
    geometry_map: dict[str, Any],
) -> dict[str, Any]:
    """Learn document-level reserved header/footer zones from resolved pages.

    A page may suppress the visible header/footer (chapter opener, special page)
    while retaining the same reserved page-furniture zone and body origin. This
    profile separates 'object absent' from 'reserved zone absent'.
    """
    line_pages = {
        int(page.get("page") or 0): page
        for page in line_map.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    header_ends: list[float] = []
    footer_starts: list[float] = []
    body_starts: list[float] = []
    body_ends: list[float] = []

    for row in geometry_map.get("pages", []) or []:
        furniture = row.get("headerFooterClassification") or {}
        header = row.get("headerBand") or {}
        footer = row.get("footerBand") or {}
        body = row.get("bodyBox") or {}
        if furniture.get("headerStatus") == "present-high" and header.get("bbox"):
            header_ends.append(float(header["bbox"][3]))
        if furniture.get("footerStatus") == "present-high" and footer.get("bbox"):
            footer_starts.append(float(footer["bbox"][1]))
        if body.get("bbox"):
            body_starts.append(float(body["bbox"][1]))
            body_ends.append(float(body["bbox"][3]))

    profile = {
        "version": VERSION,
        "headerReservedEndPt": median(header_ends) if header_ends else None,
        "footerReservedStartPt": median(footer_starts) if footer_starts else None,
        "typicalBodyStartPt": median(body_starts) if body_starts else None,
        "typicalBodyEndPt": median(body_ends) if body_ends else None,
        "sourceCounts": {
            "highHeaderPages": len(header_ends),
            "highFooterPages": len(footer_starts),
            "bodyPages": len(body_starts),
        },
        "policy": "visible page furniture may be suppressed while its document/section reserved zone remains active",
    }

    page_results = []
    typical_body_start = profile.get("typicalBodyStartPt")
    typical_body_end = profile.get("typicalBodyEndPt")
    for row in geometry_map.get("pages", []) or []:
        page_no = int(row.get("page") or 0)
        furniture = row.get("headerFooterClassification") or {}
        line_page = line_pages.get(page_no) or {}
        boxes = _body_objects(line_page)
        raw_start = min((box[1] for box in boxes), default=None)
        raw_end = max((box[3] for box in boxes), default=None)

        header_status = furniture.get("headerStatus")
        footer_status = furniture.get("footerStatus")
        header_zone_status = "visible-furniture"
        footer_zone_status = "visible-furniture"

        # Tolerance is intentionally generous because chapter-open titles may
        # begin lower than normal body text. What matters is that they do not
        # intrude upward into the learned header zone.
        if header_status in {"no-page-info-evidence", "unresolved-candidates"}:
            if raw_start is not None and typical_body_start is not None and raw_start >= typical_body_start - 18.0:
                header_zone_status = "absent-by-layout"
            else:
                header_zone_status = "unresolved"

        if footer_status in {"no-page-info-evidence", "unresolved-candidates"}:
            if raw_end is not None and typical_body_end is not None and raw_end <= typical_body_end + 18.0:
                footer_zone_status = "absent-by-layout"
            else:
                footer_zone_status = "unresolved"

        page_results.append({
            "page": page_no,
            "headerObjectStatus": header_status,
            "footerObjectStatus": footer_status,
            "headerReservedZoneStatus": header_zone_status,
            "footerReservedZoneStatus": footer_zone_status,
            "rawBodyStartPt": raw_start,
            "rawBodyEndPt": raw_end,
        })

    profile["pages"] = page_results
    profile["summary"] = {
        "headerAbsentByLayoutCount": sum(1 for row in page_results if row["headerReservedZoneStatus"] == "absent-by-layout"),
        "footerAbsentByLayoutCount": sum(1 for row in page_results if row["footerReservedZoneStatus"] == "absent-by-layout"),
        "headerUnresolvedCount": sum(1 for row in page_results if row["headerReservedZoneStatus"] == "unresolved"),
        "footerUnresolvedCount": sum(1 for row in page_results if row["footerReservedZoneStatus"] == "unresolved"),
    }
    return profile
