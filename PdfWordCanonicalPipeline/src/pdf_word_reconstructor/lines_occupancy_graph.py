from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

VERSION = "lines-occupancy-graph-0.2"


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


def _descendant_leaves(root: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    child_ids = list(root.get("children_ids") or [])
    if not child_ids:
        return [root]
    out: list[dict[str, Any]] = []
    stack = [by_id[cid] for cid in reversed(child_ids) if cid in by_id]
    seen: set[str] = set()
    while stack:
        obj = stack.pop()
        oid = str(obj.get("id") or "")
        if oid and oid in seen:
            continue
        if oid:
            seen.add(oid)
        kids = list(obj.get("children_ids") or [])
        if kids:
            for cid in reversed(kids):
                child = by_id.get(str(cid))
                if child is not None:
                    stack.append(child)
        else:
            out.append(obj)
    return out


def _segment_objects(objects: list[dict[str, Any]], page_height: float) -> list[dict[str, Any]]:
    items = [(o, _bbox(o)) for o in objects]
    items = [(o, b) for o, b in items if b]
    items.sort(key=lambda ob: (ob[1][1], ob[1][0], ob[0].get("line") or 0))
    if not items:
        return []
    heights = sorted(b[3] - b[1] for _, b in items)
    typical_h = heights[len(heights) // 2]
    split_gap = max(100.0, typical_h * 2.5, page_height * 0.035)
    groups: list[list[tuple[dict[str, Any], list[float]]]] = [[items[0]]]
    current_bottom = items[0][1][3]
    for item in items[1:]:
        _, box = item
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


def _compatible(a: dict[str, Any], b: dict[str, Any], page_w: float, page_h: float) -> bool:
    ba = a.get("bboxPx"); bb = b.get("bboxPx")
    if not ba or not bb:
        return False
    xo = _x_overlap(ba, bb); yo = _y_overlap(ba, bb)
    vg = _vertical_gap(ba, bb); hg = _horizontal_gap(ba, bb)
    # Same vertical stream: strong horizontal overlap and modest vertical gap.
    if xo >= 0.45 and vg <= max(85.0, page_h * 0.03):
        return True
    # Same local horizontal band: strong vertical overlap and a small horizontal gap.
    if yo >= 0.55 and hg <= max(55.0, page_w * 0.03):
        return True
    return False


def _cluster_raw_segments(raw: list[dict[str, Any]], page_w: float, page_h: float) -> list[list[dict[str, Any]]]:
    n = len(raw)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        for j in range(i + 1, n):
            if _compatible(raw[i], raw[j], page_w, page_h):
                union(i, j)

    groups: dict[int, list[dict[str, Any]]] = {}
    for i, node in enumerate(raw):
        groups.setdefault(find(i), []).append(node)
    return list(groups.values())


def _classify_spatial_node(node: dict[str, Any], page_w: float, page_h: float) -> dict[str, Any]:
    b = node.get("bboxPx")
    if not b:
        return {"roleCandidate": "unknown", "confidence": 0.0}
    width_ratio = (b[2] - b[0]) / max(1.0, page_w)
    height_ratio = (b[3] - b[1]) / max(1.0, page_h)
    x_center = (b[0] + b[2]) / 2 / max(1.0, page_w)
    tc = Counter(node.get("typeCounts") or {})
    text_like = tc.get("text", 0) + tc.get("section_header", 0) + tc.get("math", 0) + tc.get("figure_label", 0)
    diagrams = tc.get("diagram", 0)
    if width_ratio >= 0.50 and text_like >= 3:
        role, conf = "main-stream", 0.78
    elif width_ratio <= 0.38 and (x_center <= 0.34 or x_center >= 0.66):
        role, conf = "ancillary-stream", 0.74
    elif diagrams and text_like <= 4:
        role, conf = "visual-stream", 0.66
    else:
        role, conf = "mixed-stream", 0.48
    return {
        "roleCandidate": role,
        "confidence": conf,
        "widthRatio": round(width_ratio, 4),
        "heightRatio": round(height_ratio, 4),
    }


def build_lines_occupancy_graph(lines_path: Path) -> dict[str, Any]:
    payload = json.loads(Path(lines_path).read_text(encoding="utf-8"))
    pages_out = []
    total_nodes = 0

    for page in payload.get("pages") or []:
        objects = list(page.get("lines") or [])
        by_id = {str(o.get("id")): o for o in objects if o.get("id")}
        page_w = float(page.get("page_width") or 1)
        page_h = float(page.get("page_height") or 1)
        top = [o for o in objects if not o.get("parent_id")]

        hierarchy_edges = []
        semantic_edges = []
        for obj in objects:
            pid = obj.get("parent_id")
            if pid:
                hierarchy_edges.append({"type": "parent-child", "from": pid, "to": obj.get("id")})
            for label_id in obj.get("selected_labels") or []:
                if str(label_id) in by_id:
                    semantic_edges.append({"type": "figure-label", "from": obj.get("id"), "to": label_id})

        raw_segments: list[dict[str, Any]] = []
        root_records: list[dict[str, Any]] = []
        for root in top:
            rid = str(root.get("id"))
            leaves = _descendant_leaves(root, by_id)
            segments = _segment_objects(leaves, page_h)
            seg_ids = []
            for seg in segments:
                sid = f"root:{rid}:seg:{seg['segmentIndex']}"
                seg_ids.append(sid)
                raw_segments.append({
                    "id": sid,
                    "rootId": rid,
                    "rootType": root.get("type"),
                    **seg,
                })
            root_records.append({
                "id": rid,
                "line": root.get("line"),
                "mathpixType": root.get("type"),
                "bboxPx": _bbox(root),
                "leafCount": len(leaves),
                "rawSegmentIds": seg_ids,
                "rawSegmentCount": len(seg_ids),
            })

        clusters = _cluster_raw_segments(raw_segments, page_w, page_h)
        spatial_nodes = []
        for idx, group in enumerate(sorted(clusters, key=lambda grp: min((g.get("firstLine") or 10**9) for g in grp))):
            boxes = [g["bboxPx"] for g in group if g.get("bboxPx")]
            tc = Counter()
            object_ids: list[str] = []
            object_lines: list[int] = []
            root_ids: list[str] = []
            raw_ids: list[str] = []
            for g in group:
                tc.update(g.get("typeCounts") or {})
                object_ids.extend(str(v) for v in g.get("objectIds") or [] if v)
                object_lines.extend(int(v) for v in g.get("objectLines") or [] if isinstance(v, int))
                root_ids.append(str(g.get("rootId")))
                raw_ids.append(str(g.get("id")))
            node = {
                "id": f"spatial:{idx}",
                "bboxPx": _union(boxes),
                "rootIds": sorted(set(root_ids)),
                "rawSegmentIds": raw_ids,
                "objectIds": list(dict.fromkeys(object_ids)),
                "objectLines": sorted(set(object_lines)),
                "typeCounts": dict(tc),
                "firstLine": min(object_lines) if object_lines else None,
            }
            node.update(_classify_spatial_node(node, page_w, page_h))
            spatial_nodes.append(node)

        spatial_edges = []
        for i, a in enumerate(spatial_nodes):
            ba = a.get("bboxPx")
            if not ba:
                continue
            for b in spatial_nodes[i + 1:]:
                bb = b.get("bboxPx")
                if not bb:
                    continue
                xo = _x_overlap(ba, bb); yo = _y_overlap(ba, bb)
                hg = _horizontal_gap(ba, bb); vg = _vertical_gap(ba, bb)
                if yo >= 0.35 and hg <= page_w * 0.08:
                    left, right = (a, b) if ba[0] <= bb[0] else (b, a)
                    spatial_edges.append({"type": "side-by-side", "from": left["id"], "to": right["id"], "verticalOverlap": round(yo, 4), "gapPx": round(hg, 3)})
                if xo >= 0.35 and vg <= page_h * 0.04:
                    upper, lower = (a, b) if ba[1] <= bb[1] else (b, a)
                    spatial_edges.append({"type": "vertical-neighbor", "from": upper["id"], "to": lower["id"], "horizontalOverlap": round(xo, 4), "gapPx": round(vg, 3)})

        total_nodes += len(spatial_nodes)
        pages_out.append({
            "page": page.get("page"),
            "pageWidthPx": page.get("page_width"),
            "pageHeightPx": page.get("page_height"),
            "topLevelRootCount": len(top),
            "roots": root_records,
            "rawRootSegments": raw_segments,
            "spatialNodes": spatial_nodes,
            "hierarchyEdges": hierarchy_edges,
            "semanticEdges": semantic_edges,
            "spatialEdges": spatial_edges,
            "roleCounts": dict(Counter(n.get("roleCandidate") for n in spatial_nodes)),
        })

    return {
        "version": VERSION,
        "source": str(Path(lines_path)),
        "policy": (
            "Lines-only diagnostic graph with two layers. Spatial nodes are derived once from top-level roots and their leaf occupancy; "
            "nested Mathpix containers are preserved only in hierarchy evidence and are not duplicated as spatial nodes. "
            "Role candidates are diagnostic and do not drive Word rendering."
        ),
        "summary": {"pageCount": len(pages_out), "spatialNodeCount": total_nodes},
        "pages": pages_out,
    }


__all__ = ["build_lines_occupancy_graph"]
