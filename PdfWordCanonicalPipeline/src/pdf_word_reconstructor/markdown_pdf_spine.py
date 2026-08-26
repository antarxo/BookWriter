from __future__ import annotations

# Canonical entry point. v0.4 preserves Markdown authoritative content including
# plainText and uses local PDF typography where available, otherwise a same-page
# PDF text profile. DOCX is never typography authority here.
from .markdown_pdf_spine_v04 import build_markdown_pdf_spine

__all__ = ["build_markdown_pdf_spine"]
