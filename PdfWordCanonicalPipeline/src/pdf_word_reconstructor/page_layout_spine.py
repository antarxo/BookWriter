from __future__ import annotations

# Canonical maps-first layout entry point.
# Before v0.7 builds layout contracts, bind unplaced Markdown image/figure items
# to real PDF figure groups when per-page counts agree. This keeps visual
# geometry PDF-authoritative and avoids synthetic placement.
from typing import Any

from .donorless_visual_groups import bind_visuals_to_pdf_groups
from .page_layout_spine_v07 import build_page_layout_spine as _build_v07


VERSION = "page-layout-spine-wrapper-0.8"


def build_page_layout_spine(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
    docx_donor_map: dict[str, Any],
) -> dict[str, Any]:
    visual_binding = bind_visuals_to_pdf_groups(markdown_pdf_spine, page_structure)
    result = _build_v07(markdown_pdf_spine, page_structure, docx_donor_map)
    result["canonicalWrapperVersion"] = VERSION
    result["visualGroupBinding"] = visual_binding
    return result


__all__ = ["build_page_layout_spine"]
