from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


VERSION = "mathpix-lines-input-0.1"


def find_mathpix_lines_json(package_dir: Path) -> Path | None:
    """Find the Mathpix Files API lines payload in an extracted package."""
    candidates = sorted(Path(package_dir).rglob("*.lines.json"))
    if not candidates:
        candidates = sorted(
            path
            for path in Path(package_dir).rglob("*.json")
            if path.name.lower() in {"result.lines.json", "lines.json"}
        )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("pages"), list):
            return path
    return None


def load_mathpix_lines(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        raise ValueError(f"Not a Mathpix lines JSON payload: {path}")
    return data


def _box(item: dict[str, Any]) -> dict[str, Any] | None:
    region = item.get("region") if isinstance(item.get("region"), dict) else {}
    if region:
        try:
            x = float(region.get("top_left_x") or 0)
            y = float(region.get("top_left_y") or 0)
            width = float(region.get("width") or 0)
            height = float(region.get("height") or 0)
        except (TypeError, ValueError):
            return None
        if width > 0 and height > 0:
            return {
                "x0": x,
                "y0": y,
                "x1": x + width,
                "y1": y + height,
                "width": width,
                "height": height,
                "source": "region",
            }
    points = [point for point in (item.get("cnt") or []) if isinstance(point, list) and len(point) >= 2]
    if not points:
        return None
    try:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
    except (TypeError, ValueError):
        return None
    if max(xs) <= min(xs) or max(ys) <= min(ys):
        return None
    return {
        "x0": min(xs),
        "y0": min(ys),
        "x1": max(xs),
        "y1": max(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
        "source": "cnt",
    }


def _scale_box(box: dict[str, Any] | None, scale: float | None) -> dict[str, Any] | None:
    if box is None or scale is None:
        return None
    return {
        key: round(float(box[key]) * scale, 3)
        for key in ("x0", "y0", "x1", "y1", "width", "height")
    } | {"source": f"{box.get('source')}-scaled-to-pdf-points"}


def _page_size_lookup(pdf_analysis: dict[str, Any] | None) -> dict[int, tuple[float, float]]:
    result: dict[int, tuple[float, float]] = {}
    for page in (pdf_analysis or {}).get("pages", []) or []:
        try:
            page_no = int(page.get("page") or 0)
            width = float(page.get("width_pt") or 0)
            height = float(page.get("height_pt") or 0)
        except (TypeError, ValueError):
            continue
        if page_no > 0 and width > 0 and height > 0:
            result[page_no] = (width, height)
    return result


def _line_record(item: dict[str, Any], page_scale: float | None) -> dict[str, Any]:
    box_px = _box(item)
    record = {
        "id": item.get("id"),
        "line": item.get("line"),
        "type": item.get("type"),
        "subtype": item.get("subtype"),
        "parent_id": item.get("parent_id"),
        "children_ids": list(item.get("children_ids") or []),
        "column": item.get("column"),
        "conversion_output": item.get("conversion_output"),
        "font_size": item.get("font_size"),
        "confidence": item.get("confidence"),
        "confidence_rate": item.get("confidence_rate"),
        "selected_labels": list(item.get("selected_labels") or []),
        "is_printed": item.get("is_printed"),
        "is_handwritten": item.get("is_handwritten"),
        "cell": {
            "row": item.get("cell_row"),
            "column": item.get("cell_column"),
            "row_span": item.get("cell_row_span"),
            "col_span": item.get("cell_col_span"),
        },
        "text": item.get("text"),
        "text_display": item.get("text_display"),
        "bbox_px": box_px,
        "bbox_pt": _scale_box(box_px, page_scale),
        "raw": item,
    }
    return record


def build_mathpix_line_layout_map(path: Path, pdf_analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """Preserve and normalize every Mathpix line object for map enrichment.

    The `raw` entry keeps the original Mathpix object intact. The sibling fields
    expose stable geometry/style/hierarchy names for the existing map pipeline.
    """
    data = load_mathpix_lines(path)
    pdf_sizes = _page_size_lookup(pdf_analysis)
    pages: list[dict[str, Any]] = []
    type_counts: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    object_count = 0

    for page in data.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        try:
            page_no = int(page.get("page") or 0)
            page_width_px = float(page.get("page_width") or 0)
            page_height_px = float(page.get("page_height") or 0)
        except (TypeError, ValueError):
            continue
        page_width_pt, page_height_pt = pdf_sizes.get(page_no, (0.0, 0.0))
        scale = page_width_pt / page_width_px if page_width_px > 0 and page_width_pt > 0 else None
        records: list[dict[str, Any]] = []
        by_type: dict[str, list[str]] = {}
        children_by_parent: dict[str, list[str]] = {}
        for item in page.get("lines", []) or []:
            if not isinstance(item, dict):
                continue
            record = _line_record(item, scale)
            records.append(record)
            object_count += 1
            item_type = str(record.get("type") or "")
            type_counts[item_type] += 1
            field_counts.update(str(key) for key in item.keys())
            if record.get("id"):
                by_type.setdefault(item_type, []).append(str(record["id"]))
            parent = record.get("parent_id")
            if parent and record.get("id"):
                children_by_parent.setdefault(str(parent), []).append(str(record["id"]))
        pages.append({
            "page": page_no,
            "page_width_px": page_width_px,
            "page_height_px": page_height_px,
            "page_width_pt": page_width_pt or None,
            "page_height_pt": page_height_pt or None,
            "scale_pt_per_px": scale,
            "objects": sorted(records, key=lambda item: (float(((item.get("bbox_px") or {}).get("y0") or 0)), float(((item.get("bbox_px") or {}).get("x0") or 0)), int(item.get("line") or 0))),
            "objectIdsByType": {key: value for key, value in sorted(by_type.items())},
            "childrenByParent": {key: value for key, value in sorted(children_by_parent.items())},
        })
    return {
        "version": "mathpix-line-layout-map-0.1",
        "source": str(Path(path)),
        "policy": "all Mathpix line fields are preserved under objects[].raw and normalized beside it for map enrichment",
        "summary": {
            "pageCount": len(pages),
            "lineObjectCount": object_count,
            "lineTypes": dict(sorted(type_counts.items())),
            "observedFields": sorted(field_counts),
        },
        "pages": pages,
    }


def summarize_mathpix_lines(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "version": VERSION,
            "available": False,
            "reason": "result.lines.json not found in extracted Mathpix package",
        }
    data = load_mathpix_lines(path)
    type_counts: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    pages: list[dict[str, Any]] = []
    all_font_sizes: list[float] = []
    for page in data.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        line_items = [item for item in page.get("lines", []) or [] if isinstance(item, dict)]
        page_types: Counter[str] = Counter()
        page_font_sizes: list[float] = []
        for item in line_items:
            item_type = str(item.get("type") or "")
            type_counts[item_type] += 1
            page_types[item_type] += 1
            field_counts.update(str(key) for key in item.keys())
            try:
                size = float(item.get("font_size"))
            except (TypeError, ValueError):
                continue
            page_font_sizes.append(size)
            all_font_sizes.append(size)
        pages.append({
            "page": page.get("page"),
            "page_width": page.get("page_width"),
            "page_height": page.get("page_height"),
            "lineObjectCount": len(line_items),
            "lineTypes": dict(sorted(page_types.items())),
            "fontSizes": sorted(set(page_font_sizes)),
        })
    return {
        "version": VERSION,
        "available": True,
        "path": str(Path(path)),
        "pageCount": len(pages),
        "lineObjectCount": sum(page["lineObjectCount"] for page in pages),
        "lineTypes": dict(sorted(type_counts.items())),
        "observedFields": sorted(field_counts),
        "fontSizes": sorted(set(all_font_sizes)),
        "pages": pages,
    }
