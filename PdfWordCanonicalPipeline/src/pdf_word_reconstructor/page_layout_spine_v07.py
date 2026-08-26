from __future__ import annotations

from typing import Any

from .page_layout_spine_v06 import build_page_layout_spine as _build_v06

VERSION = "page-layout-spine-0.7"


def build_page_layout_spine(
    markdown_pdf_spine: dict[str, Any] | None,
    page_structure: dict[str, Any] | None,
    docx_donor_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = _build_v06(markdown_pdf_spine, page_structure, docx_donor_map)
    fixed = 0
    aliased = 0
    for row in result.get("rows", []) or []:
        authoritative = dict(row.get("authoritativeContent") or {}) if isinstance(row.get("authoritativeContent"), dict) else {}
        if not authoritative.get("text") and authoritative.get("plainText"):
            authoritative["text"] = authoritative.get("plainText")
            row["authoritativeContent"] = authoritative
            contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
            contract["authoritativeContent"] = authoritative
            row["layoutContract"] = contract
            aliased += 1
        existing = str(row.get("markdownText") or "")
        text = str(authoritative.get("text") or authoritative.get("plainText") or existing or "")
        if text != existing:
            row["markdownText"] = text
            fixed += 1
    result["version"] = VERSION
    summary = result.setdefault("summary", {})
    summary["plainTextPropagationCount"] = fixed
    summary["plainTextBuilderAliasCount"] = aliased
    return result
