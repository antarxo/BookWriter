from __future__ import annotations

# Canonical entry point. The previous v0.2 matcher is preserved verbatim in
# markdown_pdf_spine_v02.py; v0.3 enriches its matches with authoritative
# Markdown payload plus PDF typography and geometry.
from .markdown_pdf_spine_v03 import build_markdown_pdf_spine

__all__ = ["build_markdown_pdf_spine"]
