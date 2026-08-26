from __future__ import annotations

# Canonical maps-first layout entry point.
# v0.3 is preserved in page_layout_spine_v03.py.
# page_layout_spine_v04.py currently implements the v0.5 builder-ready paragraph/frame contract.
# v0.6 adds PDF-vector border/fill evidence without re-running matching.
from .page_layout_spine_v06 import build_page_layout_spine

__all__ = ["build_page_layout_spine"]
