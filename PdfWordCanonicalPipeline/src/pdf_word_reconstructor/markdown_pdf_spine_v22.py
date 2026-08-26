from __future__ import annotations

from copy import deepcopy
from typing import Any

from .markdown_pdf_spine_v21 import build_markdown_pdf_spine as _build_v21

VERSION = "markdown-pdf-spine-0.22"


def _matching_pdf_view(pdf_analysis: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """Return a PDF-analysis view in which page furniture cannot enter text matching.

    Header/footer regions remain present in the authoritative ``pdf_analysis`` object
    used by page geometry and later native header/footer reconstruction.  Only the
    matching view removes them.  This enforces the architectural rule that page
    furniture is not body content and must not participate in body-flow matching.
    """
    view = deepcopy(pdf_analysis)
    removed_header = 0
    removed_footer = 0

    for page in view.get("pages", []) or []:
        kept: list[dict[str, Any]] = []
        for region in page.get("regions", []) or []:
            if region.get("type") != "text":
                kept.append(region)
                continue
            semantic = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
            sem_type = str(semantic.get("type") or "")
            flow_zone = str(semantic.get("flow_zone") or "")
            furniture = semantic.get("pageFurniture") if isinstance(semantic.get("pageFurniture"), dict) else {}
            is_furniture = sem_type in {"header", "footer"} or flow_zone == "page_furniture" or bool(furniture.get("detected"))
            if not is_furniture:
                kept.append(region)
                continue
            if sem_type == "footer" or str(furniture.get("zone") or "") == "footer":
                removed_footer += 1
            else:
                removed_header += 1
        page["regions"] = kept

    return view, {
        "removedHeaderRegionCount": removed_header,
        "removedFooterRegionCount": removed_footer,
        "removedTotal": removed_header + removed_footer,
    }


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    matching_view, exclusion = _matching_pdf_view(pdf_analysis)
    result = _build_v21(markdown_element_map, matching_view)
    result["version"] = VERSION
    result["pageFurnitureExclusion"] = {
        **exclusion,
        "policy": (
            "header/footer page-furniture regions are excluded from every Markdown/PDF text-matching and recovery pass; "
            "the authoritative PDF analysis still retains them for page geometry and later native Word header/footer reconstruction"
        ),
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
