from __future__ import annotations

# Canonical entry point. Before the v0.5 Markdown/PDF spine is built, recover
# missing Markdown page hints from high-confidence monotonic Markdown↔PDF text
# anchors. This is a donorless fallback for Mathpix exports that do not contain
# page-bearing image filenames/URLs.
from typing import Any

from .markdown_pdf_page_alignment import infer_missing_markdown_pages
from .markdown_pdf_spine_v05 import build_markdown_pdf_spine as _build_v05


VERSION = "markdown-pdf-spine-wrapper-0.6"


def build_markdown_pdf_spine(
    markdown_element_map: dict[str, Any] | None,
    pdf_analysis: dict[str, Any],
) -> dict[str, Any]:
    markdown_map = markdown_element_map or {}
    page_alignment = infer_missing_markdown_pages(markdown_map, pdf_analysis)
    result = _build_v05(markdown_map, pdf_analysis)
    result["canonicalWrapperVersion"] = VERSION
    result["pageAlignmentFallback"] = page_alignment
    return result


__all__ = ["build_markdown_pdf_spine"]
