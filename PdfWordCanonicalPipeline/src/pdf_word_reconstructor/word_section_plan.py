from __future__ import annotations

import re
from collections import Counter
from typing import Any

VERSION = "word-section-plan-0.1"


def _round_pt(value: Any, step: float = 0.5) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number / step) * step


def _normalize_furniture_text(text: str) -> str:
    value = " ".join(str(text or "").split()).casefold()
    value = re.sub(r"\b\d{1,4}\s*/\s*\d{1,4}\b", "<page-counter>", value)
    value = re.sub(r"\b\d{1,4}\b", "<n>", value)
    return value


def _furniture_signature(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    values = []
    for row in rows or []:
        text = _normalize_furniture_text(str(row.get("text") or ""))
        if text:
            values.append(text)
    return tuple(sorted(values))


def _page_signature(page: dict[str, Any]) -> dict[str, Any]:
    geometry = page.get("pageGeometry") if isinstance(page.get("pageGeometry"), dict) else {}
    blank = not bool(geometry.get("bodyBBox"))
    columns = list(page.get("columns") or [])
    column_count = 2 if str(page.get("layout_mode") or "") == "two_columns" and len(columns) == 2 else 1
    gutter = None
    if column_count == 2:
        try:
            gutter = _round_pt(float(columns[1]["x0"]) - float(columns[0]["x1"]), 0.5)
        except (KeyError, TypeError, ValueError):
            gutter = None

    return {
        "pageWidthPt": _round_pt(page.get("width_pt"), 0.5),
        "pageHeightPt": _round_pt(page.get("height_pt"), 0.5),
        "blank": blank,
        "columnCount": column_count,
        "columnGutterPt": gutter,
        "hasHeader": bool(page.get("headers")),
        "hasFooter": bool(page.get("footers")),
        "headerSignature": _furniture_signature(list(page.get("headers") or [])),
        "footerSignature": _furniture_signature(list(page.get("footers") or [])),
    }


def _hard_layout_key(signature: dict[str, Any]) -> tuple[Any, ...]:
    # Only properties that require a physical Word section break belong here.
    # Header/footer text is retained as evidence but does not automatically split
    # the document; running furniture can legitimately vary within a layout family.
    return (
        signature.get("pageWidthPt"),
        signature.get("pageHeightPt"),
        signature.get("columnCount"),
        signature.get("columnGutterPt") if signature.get("columnCount") == 2 else None,
    )


def build_word_section_plan(page_structure: dict[str, Any]) -> dict[str, Any]:
    pages = list(page_structure.get("pages") or [])
    rows: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []

    current: dict[str, Any] | None = None
    previous_page_no: int | None = None
    for page in pages:
        page_no = int(page.get("page") or 0)
        signature = _page_signature(page)
        hard_key = _hard_layout_key(signature)
        row = {
            "page": page_no,
            "signature": signature,
            "hardLayoutKey": list(hard_key),
        }
        rows.append(row)

        starts_new = (
            current is None
            or current.get("hardLayoutKey") != hard_key
            or (previous_page_no is not None and page_no != previous_page_no + 1)
        )
        if starts_new:
            current = {
                "index": len(sections),
                "startPage": page_no,
                "endPage": page_no,
                "pageCount": 1,
                "pages": [page_no],
                "hardLayoutKey": hard_key,
                "pageWidthPt": signature.get("pageWidthPt"),
                "pageHeightPt": signature.get("pageHeightPt"),
                "columnCount": signature.get("columnCount"),
                "columnGutterPt": signature.get("columnGutterPt"),
                "blankPageCount": 1 if signature.get("blank") else 0,
                "headerPresenceCount": 1 if signature.get("hasHeader") else 0,
                "footerPresenceCount": 1 if signature.get("hasFooter") else 0,
                "headerSignatures": Counter([signature.get("headerSignature")]) if signature.get("headerSignature") else Counter(),
                "footerSignatures": Counter([signature.get("footerSignature")]) if signature.get("footerSignature") else Counter(),
            }
            sections.append(current)
        else:
            current["endPage"] = page_no
            current["pageCount"] += 1
            current["pages"].append(page_no)
            current["blankPageCount"] += 1 if signature.get("blank") else 0
            current["headerPresenceCount"] += 1 if signature.get("hasHeader") else 0
            current["footerPresenceCount"] += 1 if signature.get("hasFooter") else 0
            if signature.get("headerSignature"):
                current["headerSignatures"][signature.get("headerSignature")] += 1
            if signature.get("footerSignature"):
                current["footerSignatures"][signature.get("footerSignature")] += 1
        previous_page_no = page_no

    serial_sections: list[dict[str, Any]] = []
    for section in sections:
        serial_sections.append({
            **{key: value for key, value in section.items() if key not in {"hardLayoutKey", "headerSignatures", "footerSignatures"}},
            "hardLayoutKey": list(section["hardLayoutKey"]),
            "headerSignatures": [
                {"signature": list(signature), "pageCount": count}
                for signature, count in section["headerSignatures"].most_common()
            ],
            "footerSignatures": [
                {"signature": list(signature), "pageCount": count}
                for signature, count in section["footerSignatures"].most_common()
            ],
            "policy": "physical section family from contiguous page size + Word column configuration; furniture retained as evidence for later header/footer assignment",
        })

    return {
        "version": VERSION,
        "pageCount": len(rows),
        "sectionCount": len(serial_sections),
        "sections": serial_sections,
        "pages": rows,
        "policy": {
            "source": "existing per-page page_structure map",
            "pageMapIsAuthority": True,
            "blankPagesPreserved": True,
            "hardBreakProperties": ["page size", "column count", "two-column gutter"],
            "notYetHardBreakProperties": ["header/footer presence", "header/footer text", "observed per-page body extent"],
            "marginPolicy": "do not derive Word section breaks from sparse-page body extents; margins require stable page-family inference",
        },
    }


__all__ = ["build_word_section_plan"]
