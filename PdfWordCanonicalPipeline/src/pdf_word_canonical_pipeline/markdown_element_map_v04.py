from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import markdown_element_map as _base
from .markdown_element_map_v03 import extract_markdown_element_map as _extract_v03


VERSION = "markdown-element-map-0.4"

# Mathpix exports are not consistent about zero-padding page numbers in image
# filenames. Accept 1-3 digits for both page-only and page+geometry forms while
# keeping the rest of the filename contract unchanged.
_IMG_PAGE_RE = re.compile(
    r"-(\d{1,3})(?=_[0-9]+_[0-9]+_[0-9]+_[0-9]+\.(?:jpg|jpeg|png|webp)\b)|"
    r"-(\d{1,3})(?=\.(?:jpg|jpeg|png|webp)\b)",
    re.I,
)
_IMG_GEOMETRY_RE = re.compile(
    r"-(\d{1,3})_(\d+)_(\d+)_(\d+)_(\d+)\.(?:jpg|jpeg|png|webp)\b",
    re.I,
)


def extract_markdown_element_map(
    markdown_files: list[Path],
    out_path: Path,
    docx_path: Path | None = None,
    attach_docx_evidence: bool = False,
) -> dict[str, Any]:
    # v0.2 owns parsing; its helper functions read these regexes from module
    # globals at runtime, so patching the accepted page-token width here keeps
    # the parser logic single-sourced rather than copying it.
    previous_page_re = _base.IMG_PAGE_RE
    previous_geometry_re = _base.IMG_GEOMETRY_RE
    _base.IMG_PAGE_RE = _IMG_PAGE_RE
    _base.IMG_GEOMETRY_RE = _IMG_GEOMETRY_RE
    try:
        result = _extract_v03(
            markdown_files,
            out_path,
            docx_path=docx_path,
            attach_docx_evidence=attach_docx_evidence,
        )
    finally:
        _base.IMG_PAGE_RE = previous_page_re
        _base.IMG_GEOMETRY_RE = previous_geometry_re

    result["version"] = VERSION
    result["imageGeometryPolicy"] = {
        "pageTokenDigits": "1-3",
        "source": "Mathpix image filename/CDN geometry",
        "role": "page-and-position witness only",
    }
    out_path.write_text(__import__("json").dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


__all__ = ["extract_markdown_element_map"]
