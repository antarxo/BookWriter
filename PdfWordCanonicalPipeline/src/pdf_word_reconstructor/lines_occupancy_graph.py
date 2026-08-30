from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

VERSION = "lines-occupancy-graph-0.1"


def _bbox(obj: dict[str, Any]) -> list[float] | None:
    r = obj.get("region") or {}
    try:
        x0 = float(r["top_left_x"]); y0 = float(r["top_left_y"])
        w = float(r["width"]); h = float(r["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return [x0, y0, x0 + w, y0 + h]


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _x_overlap(a: list[float], b: list[float]) -> float:
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    return overlap / max(1.0, min(a[2] - a[0], b[2] - b[0]))


def _y_overlap(a: list[float], b: list[float]) -> float:
    overlap = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return overlap / max(1.0, min(a[3] - a[1], b[3] - b[1]))


def _horizontal_gap(a: list[float], b: list[float]) -> float:
    if a[2] < b[0]: return b[0] - a[2]
    if b[2] < a[0]: return a[0] - b[2]
    return 0.0


def _vertical_gap(a: list[float], b: list[float]) -> float:
    if a[3] < b[1]: return b[1] - a[3]
    if b[3] < a[1]: return a[1] - b[3]
    return 0.0


def _segment_children(children: list[dict[str, Any]], page_height: float) -> list[dict[str, Any]]:
    items = [(c, _bbox(c)) for c in children]
    items = [(c, b) for c, b in items if b]
    items.sort(key=lambda cb: (cb[1][1], cb[1][0], cb[0].get("line") or 0))
    if not items:
        return []
    typical_h = sorted([b[3] - b[1] for _, b in items])[len(items)//2]
    split_gap = max(100.0, typical_h * 2.5, page_height * 0.035)
    groups: list[list[tuple[dict[str, Any], list[float]]]] = [[items[0]]]
    current_bottom = items[0][1][3]
    for item in items[1:]:
        obj, box = item
        gap = box[1] - current_bottom
        if gap >= split_gap:
            groups.append([item])
        else:
            groups[-1].append(item)
        current_bottom = max(current_bottom, box[3])
    out = []
    for i, group in enumerate(groups):
        boxes = [b for _, b in group]
        out.append({
            "segmentIndex": i,
            "bboxPx": _union(boxes),
            "objectIds": [o.get("id") for o, _ in group],
            "objectLines": [o.get("line") for o, _ in group],
            "typeCounts": dict(Counter(str(o.get("type") or "unknown") for o, _ in group)),
            "firstLine": min((o.get("line") or 10**9) for o, _ in group),
            "splitGapThresholdPx": round(split_gap, 3),
        })
    return out


def _classify_stream(container: dict[str, Any], segments: list[dict[str, Any]], page_width: float, page_height: float) -> dict[str, Any]:
    b = _bbox(container)
    if not b:
        return {"roleCandidate": "unknown", "confidence": 0.0}
    child_union = _union([s["bboxPx"] for s in segments if s.get("bboxPx")])
    if not child_union:
        return {"roleCandidate": "unknown", "confidence": 0.0}
    width_ratio = (child_union[2] - child_union[0]) / max(1.0, page_width)
    height_ratio = (child_union[3] - child_union[1]) / max(1.0, page_height)
    x_center = (child_union[0] + child_union[2]) / 2 / max(1.0, page_width)
    type_counts = Counter()
    for s in segments:
        type_counts.update(s.get("typeCounts") or {})
    has_diagram = type_counts.get("diagram", 0) > 0
    text_like = type_counts.get("text", 0) + type_counts.get("section_header", 0) + type_counts.get("math", 0)
    if width_ratio >= 0.52 and text_like >= 3:
        return {"roleCandidate": "main-stream", "confidence": 0.75, "widthRatio": round(width_ratio, 4), "heightRatio": round(height_ratio, 4)}
    if width_ratio <= 0.38 and (x_center <= 0.32 or x_center >= 0.68):
        return {"roleCandidate": "ancillary-stream", "confidence": 0.72, "widthRatio": round(width_ratio, 4), "heightRatio": round(height_ratio, 4)}
    if has_diagram and text_like <= 4:
        return {"roleCandidate": "visual-stream", "confidence": 0.62, "widthRatio": round(width_ratio, 4), "heightRatio": round(height_ratio, 4)}
    return {"roleCandidate": "mixed-stream", "confidence": 0.45, "widthRatio": round(width_ratio, 4), "heightRatio": round(height_ratio, 4)}


def build_lines_occupancy_graph(lines_path: Path) -> dict[str, Any]:
    payload = json.loads(Path(lines_path).read_text(encoding="utf-8"))
    pages_out = []
    total_segments = 0
    for page in payload.get("pages") or []:
        objects = list(page.get("lines") or [])
        by_id = {str(o.get("id")): o for o in objects if o.get("id")}
        page_w = float(page.get("page_width") or 1)
        page_h = float(page.get("page_height") or 1)
        containers_out = []
        segment_nodes = []
        edges = []
        for obj in objects:
            child_ids = list(obj.get("children_ids") or [])
            if not child_ids:
                continue
            children = [by_id[cid] for cid in child_ids if cid in by_id]
            segments = _segment_children(children, page_h)
            role = _classify_stream(obj, segments, page_w, page_h)
            container_id = str(obj.get("id"))
            seg_ids = []
            for seg in segments:
                seg_id = f"{container_id}:seg:{seg['segmentIndex']}"
                seg_ids.append(seg_id)
                node = {"id": seg_id, "containerId": container_id, **seg, **role}
                segment_nodes.append(node)
                edges.append({"type": "container-segment", "from": container_id, "to": seg_id})
            containers_out.append({
                "id": container_id,
                "line": obj.get("line"),
                "mathpixType": obj.get("type"),
                "bboxPx": _bbox(obj),
                "childCount": len(children),
                "segmentIds": seg_ids,
                "segmentCount": len(segments),
                **role,
            })
        for obj in objects:
            for label_id in obj.get("selected_labels") or []:
                if str(label_id) in by_id:
                    edges.append({"type": "figure-label", "from": obj.get("id"), "to": label_id})
        for obj in objects:
            pid = obj.get("parent_id")
            if pid:
                edges.append({"type": "parent-child", "from": pid, "to": obj.get("id")})
        # spatial edges between occupancy segments only, not raw container bboxes
        for i, a in enumerate(segment_nodes):
            ba = a.get("bboxPx")
            if not ba: continue
            for b in segment_nodes[i+1:]:
                bb = b.get("bboxPx")
                if not bb: continue
                xo = _x_overlap(ba, bb); yo = _y_overlap(ba, bb)
                hg = _horizontal_gap(ba, bb); vg = _vertical_gap(ba, bb)
                if yo >= 0.35 and hg <= page_w * 0.08:
                    left, right = (a, b) if ba[0] <= bb[0] else (b, a)
                    edges.append({"type": "side-by-side", "from": left["id"], "to": right["id"], "verticalOverlap": round(yo, 4), "gapPx": round(hg, 3)})
                if xo >= 0.35 and vg <= page_h * 0.04:
                    upper, lower = (a, b) if ba[1] <= bb[1] else (b, a)
                    edges.append({"type": "vertical-neighbor", "from": upper["id"], "to": lower["id"], "horizontalOverlap": round(xo, 4), "gapPx": round(vg, 3)})
        total_segments += len(segment_nodes)
        pages_out.append({
            "page": page.get("page"),
            "pageWidthPx": page.get("page_width"),
            "pageHeightPx": page.get("page_height"),
            "containers": containers_out,
            "segments": segment_nodes,
            "edges": edges,
            "roleCounts": dict(Counter(s.get("roleCandidate") for s in segment_nodes)),
        })
    return {
        "version": VERSION,
        "source": str(Path(lines_path)),
        "policy": (
            "Lines-only diagnostic graph. Mathpix containers are decomposed into occupied child segments; "
            "roles are candidates only and do not drive Word rendering."
        ),
        "summary": {"pageCount": len(pages_out), "segmentCount": total_segments},
        "pages": pages_out,
    }


__all__ = ["build_lines_occupancy_graph"]
