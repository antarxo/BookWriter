from __future__ import annotations

# Canonical maps-first layout entry point.
# Before the builder-ready layout spine runs, bind unplaced Markdown visuals to
# real PDF figure groups. Then allow already-confirmed PDF text witnesses to
# survive as direct layout witnesses when page_structure has no matching slot.
from typing import Any

from .donorless_visual_groups import bind_visuals_to_pdf_groups
from .page_layout_spine_v08 import build_page_layout_spine as _build_v08


VERSION = "page-layout-spine-wrapper-0.9"


def build_page_layout_spine(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
    docx_donor_map: dict[str, Any],
) -> dict[str, Any]:
    visual_binding = bind_visuals_to_pdf_groups(markdown_pdf_spine, page_structure)
    result = _build_v08(markdown_pdf_spine, page_structure, docx_donor_map)
    result["canonicalWrapperVersion"] = VERSION
    result["visualGroupBinding"] = visual_binding
    return result


__all__ = ["build_page_layout_spine"]
