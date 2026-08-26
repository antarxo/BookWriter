from __future__ import annotations

# Canonical entry point. v0.5 keeps Markdown as content authority, PDF as
# geometry/typography authority, and additionally binds otherwise-unplaced
# display equations only to real PDF regions already classified as equations.
from .markdown_pdf_spine_v05 import build_markdown_pdf_spine

__all__ = ["build_markdown_pdf_spine"]
