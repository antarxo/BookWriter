from __future__ import annotations

# Canonical entry point. v0.3 is preserved verbatim in page_layout_spine_v03.py.
# v0.4 enriches the mapped rows into builder-ready Word paragraph contracts.
from .page_layout_spine_v04 import build_page_layout_spine

__all__ = ["build_page_layout_spine"]
