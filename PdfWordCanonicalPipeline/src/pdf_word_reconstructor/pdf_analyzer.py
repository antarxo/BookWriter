from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

import fitz  # PyMuPDF

from .common import compact_text


def _color_hex(value: int) -> str:
    return f"#{value & 0xFFFFFF:06x}"


def _pdf_rgb_hex(value: Any) -> str | None:
    """Normalize a PyMuPDF stroke/fill color to #rrggbb without guessing."""
    if value is None:
        return None
    if isinstance(value, int):
        return _color_hex(value)
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            channels = [float(value[index]) for index in range(3)]
        except (TypeError, ValueError):
            return None
        # PyMuPDF drawing colors are normally floats in [0,1]. Tolerate an
        # already expanded RGB triple as well, but do not invent missing channels.
        if all(0.0 <= channel <= 1.0 for channel in channels):
            rgb = [round(channel * 255.0) for channel in channels]
        elif all(0.0 <= channel <= 255.0 for channel in channels):
            rgb = [round(channel) for channel in channels]
        else:
            return None
        return "#" + "".join(f"{max(0, min(255, channel)):02x}" for channel in rgb)
    return None


def _point(value: Any) -> list[float] | None:
    try:
        return [round(float(value.x), 3), round(float(value.y), 3)]
    except Exception:
        pass
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return [round(float(value[0]), 3), round(float(value[1]), 3)]
        except (TypeError, ValueError):
            return None
    return None


def _drawing_item(item: Any) -> dict[str, Any] | None:
    """Serialize PyMuPDF path primitives into JSON-safe diagnostic geometry."""
    if not isinstance(item, (list, tuple)) or not item:
        return None
    kind = str(item[0])
    out: dict[str, Any] = {"kind": kind}
    if kind == "re" and len(item) >= 2:
        rect = _bbox(item[1])
        if rect:
            out["rect"] = [round(value, 3) for value in rect]
        if len(item) >= 3:
            try:
                out["orientation"] = int(item[2])
            except (TypeError, ValueError):
                pass
    elif kind == "l" and len(item) >= 3:
        p1, p2 = _point(item[1]), _point(item[2])
        if p1:
            out["p1"] = p1
        if p2:
            out["p2"] = p2
    elif kind == "c" and len(item) >= 5:
        points = [_point(value) for value in item[1:5]]
        out["points"] = [point for point in points if point]
    elif kind == "qu" and len(item) >= 2:
        quad = item[1]
        points = []
        for attr in ("ul", "ur", "lr", "ll"):
            point = _point(getattr(quad, attr, None))
            if point:
                points.append(point)
        if points:
            out["points"] = points
    else:
        points = [_point(value) for value in item[1:]]
        points = [point for point in points if point]
        if points:
            out["points"] = points
    return out


def _drawing_records(page: fitz.Page, page_no: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for index, drawing in enumerate(drawings, start=1):
        rect = _bbox(drawing.get("rect"))
        if rect is None:
            continue
        items = []
        for raw_item in drawing.get("items", []) or []:
            item = _drawing_item(raw_item)
            if item:
                items.append(item)
        width = drawing.get("width")
        try:
            width_pt = round(float(width), 3) if width is not None else None
        except (TypeError, ValueError):
            width_pt = None
        fill_opacity = drawing.get("fill_opacity")
        stroke_opacity = drawing.get("stroke_opacity")
        try:
            fill_opacity = round(float(fill_opacity), 4) if fill_opacity is not None else None
        except (TypeError, ValueError):
            fill_opacity = None
        try:
            stroke_opacity = round(float(stroke_opacity), 4) if stroke_opacity is not None else None
        except (TypeError, ValueError):
            stroke_opacity = None
        records.append({
            "id": f"p{page_no}-d{index:03d}",
            "bbox": [round(value, 3) for value in rect],
            "type": str(drawing.get("type") or ""),
            "strokeColor": _pdf_rgb_hex(drawing.get("color")),
            "fillColor": _pdf_rgb_hex(drawing.get("fill")),
            "strokeWidthPt": width_pt,
            "strokeOpacity": stroke_opacity,
            "fillOpacity": fill_opacity,
            "dashes": str(drawing.get("dashes") or "") or None,
            "closePath": bool(drawing.get("closePath")),
            "evenOdd": bool(drawing.get("even_odd")),
            "layer": drawing.get("layer"),
            "seqno": drawing.get("seqno"),
            "items": items,
            "source": "pymupdf-page.get_drawings",
        })
    return records


def _union_bbox(boxes: list[list[float]]) -> list[float]:
    if not boxes:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        round(min(b[0] for b in boxes), 3),
        round(min(b[1] for b in boxes), 3),
        round(max(b[2] for b in boxes), 3),
        round(max(b[3] for b in boxes), 3),
    ]


def _bbox(value: Any) -> list[float] | None:
    if hasattr(value, "x0") and hasattr(value, "y0") and hasattr(value, "x1") and hasattr(value, "y1"):
        try:
            x0, y0, x1, y1 = float(value.x0), float(value.y0), float(value.x1), float(value.y1)
        except (TypeError, ValueError):
            return None
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            x0, y0, x1, y1 = (float(part) for part in value)
        except (TypeError, ValueError):
            return None
    else:
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _page_fullness(width_pt: float, height_pt: float, regions: list[dict[str, Any]]) -> dict[str, Any]:
    page_area = max(1.0, width_pt * height_pt)
    content_boxes: list[list[float]] = []
    text_boxes: list[list[float]] = []
    image_boxes: list[list[float]] = []
    for region in regions:
        box = _bbox(region.get("bbox"))
        if box is None:
            continue
        content_boxes.append(box)
        if region.get("type") == "text":
            text_boxes.append(box)
        elif region.get("type") == "image":
            image_boxes.append(box)
    if not content_boxes:
        return {
            "policy": "first-pdf-pass-region-density",
            "score": 0.0,
            "verticalCoverage": 0.0,
            "areaRatio": 0.0,
            "textRegionCount": 0,
            "imageRegionCount": 0,
            "contentBBox": None,
        }
    content_bbox = _union_bbox(content_boxes)
    vertical_coverage = max(0.0, min(1.0, (content_bbox[3] - content_bbox[1]) / max(1.0, height_pt)))
    area_ratio = max(
        0.0,
        min(1.0, sum((box[2] - box[0]) * (box[3] - box[1]) for box in content_boxes) / page_area),
    )
    text_weight = min(1.0, len(text_boxes) / 12.0)
    image_weight = min(1.0, len(image_boxes) / 4.0)
    score = round((vertical_coverage * 0.62) + (area_ratio * 0.16) + (text_weight * 0.16) + (image_weight * 0.06), 4)
    return {
        "policy": "first-pdf-pass-region-density",
        "score": score,
        "verticalCoverage": round(vertical_coverage, 4),
        "areaRatio": round(area_ratio, 4),
        "textRegionCount": len(text_boxes),
        "imageRegionCount": len(image_boxes),
        "contentBBox": content_bbox,
    }


def _split_line_groups(lines: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split one PDF text block when a figure or large blank gap interrupts it."""
    if not lines:
        return []
    heights = [max(1.0, float(line["bbox"][3]) - float(line["bbox"][1])) for line in lines]
    normal_height = median(heights) if heights else 12.0
    threshold = max(8.0, normal_height * 1.65)

    groups: list[list[dict[str, Any]]] = [[lines[0]]]
    previous = lines[0]
    for line in lines[1:]:
        gap = float(line["bbox"][1]) - float(previous["bbox"][3])
        if gap > threshold:
            groups.append([line])
        else:
            groups[-1].append(line)
        previous = line
    return groups


def _line_record(line: dict[str, Any], font_counter: Counter[tuple[str, float, str, int]]) -> dict[str, Any] | None:
    spans_out: list[dict[str, Any]] = []
    line_parts: list[str] = []
    meaningful_boxes: list[list[float]] = []
    for span in line.get("spans", []):
        text = str(span.get("text", ""))
        if not text:
            continue
        font = str(span.get("font", ""))
        size = round(float(span.get("size", 0.0)), 2)
        color = _color_hex(int(span.get("color", 0)))
        flags = int(span.get("flags", 0))
        bbox = [round(float(v), 3) for v in span.get("bbox", (0, 0, 0, 0))]
        if text.strip():
            font_counter[(font, size, color, flags)] += max(1, len(text.strip()))
            meaningful_boxes.append(bbox)
        spans_out.append({
            "text": text,
            "bbox": bbox,
            "font": font,
            "size_pt": size,
            "color": color,
            "flags": flags,
            "ascender": round(float(span.get("ascender", 0.0)), 3),
            "descender": round(float(span.get("descender", 0.0)), 3),
        })
        line_parts.append(text)

    line_text = "".join(line_parts)
    if not line_text.strip():
        return None
    bbox = _union_bbox(meaningful_boxes) if meaningful_boxes else [round(float(v), 3) for v in line.get("bbox", (0, 0, 0, 0))]
    return {
        "bbox": bbox,
        "dir": [round(float(v), 4) for v in line.get("dir", (1, 0))],
        "text": line_text,
        "spans": spans_out,
    }


def analyze_pdf(pdf_path: Path, pages_1based: list[int], work_dir: Path, dpi: int = 160) -> dict[str, Any]:
    doc = fitz.open(pdf_path)
    page_results: list[dict[str, Any]] = []
    font_counter: Counter[tuple[str, float, str, int]] = Counter()
    work_pages = work_dir / "pages"
    work_images = work_dir / "images"
    work_pages.mkdir(parents=True, exist_ok=True)
    work_images.mkdir(parents=True, exist_ok=True)

    for page_no in pages_1based:
        page = doc[page_no - 1]
        raw = page.get_text("dict", sort=True)
        regions: list[dict[str, Any]] = []
        image_index = 0
        text_index = 0

        for block in raw.get("blocks", []):
            block_type = block.get("type")
            original_bbox = [round(float(v), 3) for v in block.get("bbox", (0, 0, 0, 0))]
            if block_type == 0:
                lines_out: list[dict[str, Any]] = []
                for line in block.get("lines", []):
                    record = _line_record(line, font_counter)
                    if record is not None:
                        lines_out.append(record)

                for line_group in _split_line_groups(lines_out):
                    block_text = "\n".join(line["text"] for line in line_group).strip()
                    if not block_text:
                        continue
                    text_index += 1
                    bbox = _union_bbox([line["bbox"] for line in line_group])
                    regions.append({
                        "id": f"p{page_no}-t{text_index:03d}",
                        "type": "text",
                        "bbox": bbox,
                        "original_block_bbox": original_bbox,
                        "text": block_text,
                        "preview": compact_text(block_text),
                        "lines": line_group,
                    })
            elif block_type == 1:
                image_index += 1
                ext = str(block.get("ext") or "png").lower()
                image_name = f"p{page_no}-img{image_index:03d}.{ext}"
                image_path = work_images / image_name
                image_bytes = block.get("image")
                if image_bytes:
                    image_path.write_bytes(image_bytes)
                regions.append({
                    "id": f"p{page_no}-i{image_index:03d}",
                    "type": "image",
                    "bbox": original_bbox,
                    "width": block.get("width"),
                    "height": block.get("height"),
                    "ext": ext,
                    "path": str(image_path.relative_to(work_dir)) if image_bytes else None,
                })

        drawings = _drawing_records(page, page_no)
        scale = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        render_path = work_pages / f"page-{page_no}.png"
        pix.save(render_path)

        page_results.append({
            "page": page_no,
            "width_pt": round(page.rect.width, 3),
            "height_pt": round(page.rect.height, 3),
            "rotation": page.rotation,
            "render": str(render_path.relative_to(work_dir)),
            "regions": regions,
            "drawings": drawings,
            "page_fullness": _page_fullness(float(page.rect.width), float(page.rect.height), regions),
            "text_region_count": sum(1 for r in regions if r["type"] == "text"),
            "image_region_count": sum(1 for r in regions if r["type"] == "image"),
            "drawing_count": len(drawings),
        })

    fonts = [
        {
            "font": key[0],
            "size_pt": key[1],
            "color": key[2],
            "flags": key[3],
            "weighted_chars": count,
        }
        for key, count in font_counter.most_common()
    ]
    return {
        "source": str(pdf_path),
        "page_count": doc.page_count,
        "selected_pages": pages_1based,
        "pages": page_results,
        "font_usage": fonts,
        "drawing_summary": {
            "source": "pymupdf-page.get_drawings",
            "pageCount": len(page_results),
            "drawingCount": sum(int(page.get("drawing_count") or 0) for page in page_results),
        },
    }
