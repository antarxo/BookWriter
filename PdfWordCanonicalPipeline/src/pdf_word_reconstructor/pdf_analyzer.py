from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

import fitz  # PyMuPDF

from .common import compact_text


def _color_hex(value: int) -> str:
    return f"#{value & 0xFFFFFF:06x}"


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
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(part) for part in value)
    except (TypeError, ValueError):
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
    """Split one PDF text block when a figure or large blank gap interrupts it.

    PDF generators often put text above and below a diagram in one block whose
    bbox spans the entire diagram. That destroys page-zone detection. We use
    actual line bboxes and split on a clearly abnormal vertical gap.
    """
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
            "page_fullness": _page_fullness(float(page.rect.width), float(page.rect.height), regions),
            "text_region_count": sum(1 for r in regions if r["type"] == "text"),
            "image_region_count": sum(1 for r in regions if r["type"] == "image"),
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
    }
