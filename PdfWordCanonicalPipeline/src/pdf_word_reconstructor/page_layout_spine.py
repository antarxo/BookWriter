from __future__ import annotations

# Canonical maps-first layout entry point.
# Before the builder-ready layout spine runs, bind unplaced Markdown visuals to
# real PDF figure groups. Then allow already-confirmed PDF text witnesses to
# survive as direct layout witnesses when page_structure has no matching slot.
from typing import Any

from .donorless_visual_groups import bind_visuals_to_pdf_groups
from .mathpix_lines_input import build_mathpix_line_layout_map, summarize_mathpix_lines
from .page_layout_spine_v08 import build_page_layout_spine as _build_v08


VERSION = "page-layout-spine-wrapper-0.10"


def _line_map_from_page_structure(page_structure: dict[str, Any]) -> dict[str, Any] | None:
    meta = page_structure.get("mathpixLineLayoutMap")
    page_maps = [
        page.get("mathpixLinePageMap")
        for page in page_structure.get("pages", []) or []
        if isinstance(page.get("mathpixLinePageMap"), dict)
    ]
    if not isinstance(meta, dict) and not page_maps:
        return None
    return {
        "version": (meta or {}).get("version"),
        "source": (meta or {}).get("source"),
        "policy": (meta or {}).get("policy"),
        "summary": (meta or {}).get("summary") or page_structure.get("mathpixLinesSummary") or {},
        "rawTopLevel": (meta or {}).get("rawTopLevel") or {},
        "pages": page_maps,
    }


def build_page_layout_spine(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
    docx_donor_map: dict[str, Any],
    mathpix_lines_path=None,
) -> dict[str, Any]:
    visual_binding = bind_visuals_to_pdf_groups(markdown_pdf_spine, page_structure)
    result = _build_v08(markdown_pdf_spine, page_structure, docx_donor_map)
    result["canonicalWrapperVersion"] = VERSION
    result["visualGroupBinding"] = visual_binding

    line_map = _line_map_from_page_structure(page_structure)
    if line_map is None and mathpix_lines_path:
        # Compatibility fallback. The preferred path is the page_structure map,
        # because it already contains bbox_pt scaled against pdf_analysis.
        line_map = build_mathpix_line_layout_map(mathpix_lines_path)
    if line_map:
        result["mathpixLinesSummary"] = line_map.get("summary") or summarize_mathpix_lines(mathpix_lines_path)
        result["mathpixLineLayoutMap"] = line_map
        result.setdefault("summary", {})["mathpixLinesAvailable"] = True
    else:
        result["mathpixLinesSummary"] = {"available": False, "reason": "mathpix lines evidence not provided"}
        result.setdefault("summary", {})["mathpixLinesAvailable"] = False

    package_map = page_structure.get("mathpixPackageMap")
    if isinstance(package_map, dict):
        result["mathpixPackageSummary"] = page_structure.get("mathpixPackageSummary") or {}
        result["mathpixPackageMap"] = package_map
        result.setdefault("summary", {})["mathpixPackageAvailable"] = True
        result.setdefault("summary", {})["mathpixPackageAuditStatus"] = (package_map.get("audit") or {}).get("status")
    else:
        result["mathpixPackageSummary"] = {"available": False, "reason": "package map not present in page_structure"}
        result.setdefault("summary", {})["mathpixPackageAvailable"] = False

    return result


__all__ = ["build_page_layout_spine"]
