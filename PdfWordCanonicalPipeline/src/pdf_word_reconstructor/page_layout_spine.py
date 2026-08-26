from __future__ import annotations

# Canonical maps-first layout entry point.
# v0.7 preserves the v0.6 vector-frame behavior and additionally propagates
# Markdown authoritativeContent.plainText into the builder-facing row text.
from .page_layout_spine_v07 import build_page_layout_spine

__all__ = ["build_page_layout_spine"]
