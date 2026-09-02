from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any


VERSION = "mathpix-margin-model-0.2"

_EXCLUDED_BODY_TYPES = {
    "page_info",
    "column",
    "table_row",
    "table_column",
    "table_of_contents_container",
    "table_of_contents_row",
    "table_of_contents_number",
}


def _box(obj: dict[str, Any]) -> list[float] | None:
    src = obj.get("bbox_pt") if isinstance(obj.get("bbox_pt"), dict) else None
    if not src:
        return None
    try:
        x0 = float(src.get("x0")); y0 = float(src.get("y0"))
        x1 = float(src.get("x1")); y1 = float(src.get("y1"))
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _body_boxes(page: dict[str, Any]) -> list[list[float]]:
    boxes: list[list[float]] = []
    for obj in page.get("objects", []) or []:
        if str(obj.get("type") or "") in _EXCLUDED_BODY_TYPES:
            continue
        box = _box(obj)
        if box:
            boxes.append(box)
    return boxes


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, max(0, round((len(values) - 1) * fraction)))
    return float(values[index])


def _robust_body_envelope(page: dict[str, Any]) -> dict[str, Any] | None:
    boxes = _body_boxes(page)
    if not boxes:
        return None
    x0 = _quantile([b[0] for b in boxes], 0.08)
    y0 = _quantile([b[1] for b in boxes], 0.04)
    x1 = _quantile([b[2] for b in boxes], 0.92)
    y1 = _quantile([b[3] for b in boxes], 0.96)
    if None in {x0, y0, x1, y1} or float(x1) <= float(x0) or float(y1) <= float(y0):
        return None
    return {
        "bbox": [float(x0), float(y0), float(x1), float(y1)],
        "rawEnvelope": [
            min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes),
        ],
        "objectCount": len(boxes),
    }


def _furniture_inside_margin_check(
    body_bbox: list[float],
    header: dict[str, Any] | None,
    footer: dict[str, Any] | None,
) -> dict[str, Any]:
    top_ok = True
    bottom_ok = True
    header_box = (header or {}).get("bbox")
    footer_box = (footer or {}).get("bbox")
    if header_box:
        top_ok = float(header_box[3]) <= float(body_bbox[1]) + 2.0
    if footer_box:
        bottom_ok = float(footer_box[1]) >= float(body_bbox[3]) - 2.0
    return {
        "headerInsideTopMargin": top_ok,
        "footerInsideBottomMargin": bottom_ok,
        "valid": top_ok and bottom_ok,
        "policy": "header/footer are page furniture inside the top/bottom margins; they do not add to margin size",
    }


def _horizontal_pattern(left: float, right: float, tolerance: float = 3.0) -> str:
    if left > right + tolerance:
        return "left-larger"
    if right > left + tolerance:
        return "right-larger"
    return "balanced"


def _build_horizontal_family_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Detect a mirrored odd/even horizontal-margin family before using it.

    Physical-page parity is only a candidate family label. It becomes usable for
    inheritance only when at least two observed pages exist in each family and
    the dominant left/right asymmetry is opposite between the two families.
    """
    groups: dict[str, list[dict[str, float]]] = {"odd": [], "even": []}
    patterns: dict[str, Counter[str]] = {"odd": Counter(), "even": Counter()}
    for row in rows:
        page_no = int(row.get("page") or 0)
        observed = row.get("observedMarginsPt") or {}
        try:
            left = float(observed.get("left"))
            right = float(observed.get("right"))
        except (TypeError, ValueError):
            continue
        family = "odd" if page_no % 2 else "even"
        groups[family].append({"left": left, "right": right})
        patterns[family][_horizontal_pattern(left, right)] += 1

    def dominant(family: str) -> str | None:
        directional = {
            "left-larger": patterns[family]["left-larger"],
            "right-larger": patterns[family]["right-larger"],
        }
        best = max(directional, key=directional.get)
        return best if directional[best] > directional["right-larger" if best == "left-larger" else "left-larger"] else None

    odd_dom = dominant("odd")
    even_dom = dominant("even")
    mirrored = (
        len(groups["odd"]) >= 2
        and len(groups["even"]) >= 2
        and odd_dom in {"left-larger", "right-larger"}
        and even_dom in {"left-larger", "right-larger"}
        and odd_dom != even_dom
    )

    families: dict[str, Any] = {}
    for family, values in groups.items():
        families[family] = {
            "sourcePageCount": len(values),
            "leftPt": median([v["left"] for v in values]) if values else None,
            "rightPt": median([v["right"] for v in values]) if values else None,
            "patternCounts": dict(sorted(patterns[family].items())),
            "dominantPattern": dominant(family),
        }

    return {
        "status": "resolved-mirrored-page-families" if mirrored else "not-resolved",
        "usableForInheritance": mirrored,
        "familyLabel": "physical-page-parity-candidate",
        "families": families,
        "policy": (
            "odd/even labels are evidence-derived family candidates, not renderer instructions; "
            "family-specific horizontal margins may be inherited only when opposite mirrored asymmetry is supported"
        ),
    }


def build_mathpix_margin_model(
    line_map: dict[str, Any],
    geometry_map: dict[str, Any],
    reserved_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    line_pages = {
        int(page.get("page") or 0): page
        for page in line_map.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    geometry_pages = {
        int(page.get("page") or 0): page
        for page in geometry_map.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    reserved_pages = {
        int(page.get("page") or 0): page
        for page in (reserved_profile or {}).get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }

    provisional: list[dict[str, Any]] = []
    trusted_top: list[float] = []
    trusted_bottom: list[float] = []
    trusted_left: list[float] = []
    trusted_right: list[float] = []

    for page_no, line_page in sorted(line_pages.items()):
        try:
            width = float(line_page.get("page_width_pt") or 0)
            height = float(line_page.get("page_height_pt") or 0)
        except (TypeError, ValueError):
            width = height = 0.0
        envelope = _robust_body_envelope(line_page)
        geom = geometry_pages.get(page_no) or {}
        furniture = geom.get("headerFooterClassification") or {}
        header = geom.get("headerBand")
        footer = geom.get("footerBand")
        reserved = reserved_pages.get(page_no) or {}

        if not envelope or width <= 0 or height <= 0:
            provisional.append({"page": page_no, "status": "unresolved", "reason": "missing-body-envelope"})
            continue

        body_bbox = list(envelope["bbox"])
        margins = {
            "left": body_bbox[0],
            "right": width - body_bbox[2],
            "top": body_bbox[1],
            "bottom": height - body_bbox[3],
        }
        validation = _furniture_inside_margin_check(body_bbox, header, footer)
        hs = str(furniture.get("headerStatus") or "")
        fs = str(furniture.get("footerStatus") or "")
        hz = str(reserved.get("headerReservedZoneStatus") or "")
        fz = str(reserved.get("footerReservedZoneStatus") or "")

        header_resolved = hs.startswith("present-") or hz == "absent-by-layout"
        footer_resolved = fs.startswith("present-") or fz == "absent-by-layout"
        trusted = header_resolved and footer_resolved and validation["valid"] and envelope["objectCount"] >= 8

        if trusted:
            trusted_left.append(margins["left"])
            trusted_right.append(margins["right"])
            trusted_top.append(margins["top"])
            trusted_bottom.append(margins["bottom"])

        provisional.append({
            "page": page_no,
            "status": "trusted-page-evidence" if trusted else "needs-document-profile",
            "bodyEnvelope": envelope,
            "pageWidthPt": width,
            "pageHeightPt": height,
            "observedMarginsPt": {k: round(v, 3) for k, v in margins.items()},
            "headerObjectStatus": hs or None,
            "footerObjectStatus": fs or None,
            "headerReservedZoneStatus": hz or None,
            "footerReservedZoneStatus": fz or None,
            "furnitureValidation": validation,
        })

    profile = {
        "leftPt": median(trusted_left) if trusted_left else None,
        "rightPt": median(trusted_right) if trusted_right else None,
        "topPt": median(trusted_top) if trusted_top else None,
        "bottomPt": median(trusted_bottom) if trusted_bottom else None,
        "sourcePageCount": len(trusted_top),
    }
    horizontal_family_profile = _build_horizontal_family_profile(
        [row for row in provisional if row.get("status") != "unresolved"]
    )

    pages: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for row in provisional:
        page_no = int(row.get("page") or 0)
        if row.get("status") == "unresolved":
            status_counts["unresolved"] += 1
            pages.append(row)
            continue
        observed = row.get("observedMarginsPt") or {}
        body = (row.get("bodyEnvelope") or {}).get("bbox")
        width = float(row.get("pageWidthPt") or 0)
        height = float(row.get("pageHeightPt") or 0)

        if row.get("status") == "trusted-page-evidence":
            final_margins = dict(observed)
            source = "page-evidence"
            confidence = "high"
        elif all(profile.get(k) is not None for k in ("leftPt", "rightPt", "topPt", "bottomPt")):
            horizontal = None
            family = "odd" if page_no % 2 else "even"
            if horizontal_family_profile.get("usableForInheritance"):
                candidate = (horizontal_family_profile.get("families") or {}).get(family) or {}
                if candidate.get("leftPt") is not None and candidate.get("rightPt") is not None:
                    horizontal = candidate

            final_margins = {
                "left": float(horizontal["leftPt"] if horizontal else profile["leftPt"]),
                "right": float(horizontal["rightPt"] if horizontal else profile["rightPt"]),
                "top": float(profile["topPt"]),
                "bottom": float(profile["bottomPt"]),
            }
            body = [
                final_margins["left"],
                final_margins["top"],
                width - final_margins["right"],
                height - final_margins["bottom"],
            ]
            source = "mirrored-page-family-profile" if horizontal else "document-profile"
            confidence = "medium"
        else:
            status_counts["unresolved"] += 1
            pages.append({**row, "status": "unresolved", "reason": "no-trusted-margin-profile"})
            continue

        status = "resolved"
        status_counts[status] += 1
        pages.append({
            **row,
            "status": status,
            "confidence": confidence,
            "source": source,
            "bodyBox": body,
            "marginsPt": {k: round(float(v), 3) for k, v in final_margins.items()},
            "horizontalPageFamilyCandidate": "odd" if page_no % 2 else "even",
            "modelPolicy": "body box defines full margins; header/footer are internal margin witnesses and never additive",
        })

    return {
        "version": VERSION,
        "policy": (
            "infer body box/full margins after furniture; detect mirrored horizontal page families before inheritance; "
            "chapter-open/special pages may inherit only evidence-supported document/page-family profiles"
        ),
        "documentMarginProfile": profile,
        "horizontalPageFamilyProfile": horizontal_family_profile,
        "summary": {
            "pageCount": len(pages),
            "trustedProfilePageCount": profile["sourcePageCount"],
            "statusCounts": dict(sorted(status_counts.items())),
            "documentMarginProfile": profile,
            "horizontalPageFamilyProfileStatus": horizontal_family_profile.get("status"),
        },
        "pages": pages,
    }


def apply_mathpix_margin_model(page_structure: dict[str, Any], margin_model: dict[str, Any]) -> dict[str, Any]:
    by_page = {
        int(row.get("page") or 0): row
        for row in margin_model.get("pages", []) or []
        if int(row.get("page") or 0) > 0
    }
    applied = 0
    for page in page_structure.get("pages", []) or []:
        row = by_page.get(int(page.get("page") or 0))
        if not row:
            continue
        page["mathpixMarginEvidence"] = row
        if row.get("status") != "resolved" or not row.get("bodyBox"):
            continue
        page["body_box"] = list(row["bodyBox"])
        page["margins"] = dict(row.get("marginsPt") or {})
        page["margin_source"] = row.get("source")
        page["horizontal_page_family_candidate"] = row.get("horizontalPageFamilyCandidate")
        applied += 1

    page_structure["mathpixMarginModel"] = margin_model
    page_structure["mathpixMarginApplication"] = {
        "version": VERSION,
        "appliedPageCount": applied,
        "policy": "header/footer remain inside margins; only resolved body-box evidence/profile may update page margins",
    }
    return page_structure
