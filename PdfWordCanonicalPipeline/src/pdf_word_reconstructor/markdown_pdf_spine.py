from __future__ import annotations

# Canonical entry point. First recover missing Markdown page hints from
# high-confidence monotonic Markdown↔PDF text anchors. Then build the normal
# Markdown/PDF spine and apply page-scoped, neighbor-bounded, conservative
# directional, conflict-aware, structural, adjacent-page, short-heading,
# strict short-paragraph exact, conservative contiguous multi-region paragraph
# recovery, non-destructive page-furniture isolation, conservative adjacent-page
# sequence-bracket paragraph recovery, and finally recompute all unresolved-text
# diagnostics against a header/footer-free PDF witness.
from typing import Any

from .markdown_pdf_page_alignment import infer_missing_markdown_pages
from .markdown_pdf_spine_v25 import build_markdown_pdf_spine as _build_v25


VERSION = "markdown-pdf-spine-wrapper-0.25"


def build_markdown_pdf_spine(
    markdown_element_map: dict[str, Any] | None,
    pdf_analysis: dict[str, Any],
) -> dict[str, Any]:
    markdown_map = markdown_element_map or {}
    page_alignment = infer_missing_markdown_pages(markdown_map, pdf_analysis)
    result = _build_v25(markdown_map, pdf_analysis)
    result["canonicalWrapperVersion"] = VERSION
    result["pageAlignmentFallback"] = page_alignment
    return result


__all__ = ["build_markdown_pdf_spine"]
