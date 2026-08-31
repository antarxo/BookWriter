from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .mathpix_lines_input import build_mathpix_line_layout_map

VERSION = "lines-page-geometry-map-0.1"


def _box(record: dict[str, Any]) -> list[float] | None:
    raw = record.get("bbox_px") if isinstance(record.get("bbox_px"), dict) else None
    if not raw:
        return None
    try:
        values = [float(raw[k]) for k in ("x0", "y0", "x1", "y1")]
    except (KeyError, TypeError, ValueError):
        return None
    return values if values[2] > values[0] and values[3] > values[1] else None


def _text(record: dict[str, Any]) -> str:
    for key in ("text_display", "text", "conversion_output"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_page_geometry_map(lines_path: Path, page_no: int) -> dict[str, Any]:
    """Emit raw Mathpix Lines geometry for one page without layout interpretation.

    No region/rail/sidebar/column semantic is inferred. Every line object with geometry
    is reported with its raw type, parent relation and bbox. Column records are emitted
    only as source envelopes, not as renderer instructions.
    """
    line_map = build_mathpix_line_layout_map(Path(lines_path), None)
    page = next((p for p in line_map.get("pages", []) or [] if int(p.get("page") or 0) == int(page_no)), None)
    if page is None:
        raise ValueError(f"Page {page_no} not found in Mathpix Lines payload")

    records = list(page.get("objects", []) or [])
    objects: list[dict[str, Any]] = []
    for record in records:
        box = _box(record)
        if not box:
            continue
        objects.append({
            "id": str(record.get("id") or ""),
            "line": record.get("line"),
            "type": str(record.get("type") or ""),
            "subtype": record.get("subtype"),
            "parent_id": str(record.get("parent_id") or "") or None,
            "children_ids": [str(v) for v in (record.get("children_ids") or []) if v],
            "bbox_px": [round(v, 3) for v in box],
            "text": _text(record),
            "font_size": record.get("font_size"),
        })

    objects.sort(key=lambda o: (o["bbox_px"][1], o["bbox_px"][0], int(o.get("line") or 10**8)))
    envelopes = [o for o in objects if o["type"] == "column"]
    renderables = [o for o in objects if o["type"] not in {"page_info", "column"}]

    return {
        "version": VERSION,
        "source": str(Path(lines_path)),
        "policy": (
            "Mathpix Lines geometry only. No main/rail/sidebar/multicolumn/Word interpretation. "
            "Column records are preserved only as raw source envelopes."
        ),
        "page": int(page_no),
        "page_width_px": page.get("page_width_px"),
        "page_height_px": page.get("page_height_px"),
        "summary": {
            "objectCountWithGeometry": len(objects),
            "renderableCount": len(renderables),
            "envelopeCount": len(envelopes),
            "types": dict(sorted(Counter(o["type"] for o in objects).items())),
        },
        "envelopes": envelopes,
        "renderables": renderables,
        "objects": objects,
    }


__all__ = ["build_page_geometry_map"]
