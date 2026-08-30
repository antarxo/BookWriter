from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from pdf_word_reconstructor.lines_occupancy_graph import build_lines_occupancy_graph

VERSION = "lines-page-frame-visual-0.1"


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


def _descendant_leaves(root: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    kids = list(root.get("children_ids") or [])
    if not kids:
        return [root]
    out: list[dict[str, Any]] = []
    stack = [by_id[cid] for cid in reversed(kids) if cid in by_id]
    seen: set[str] = set()
    while stack:
        obj = stack.pop()
        oid = str(obj.get("id") or "")
        if oid and oid in seen:
            continue
        if oid:
            seen.add(oid)
        child_ids = list(obj.get("children_ids") or [])
        if child_ids:
            for cid in reversed(child_ids):
                child = by_id.get(str(cid))
                if child is not None:
                    stack.append(child)
        else:
            out.append(obj)
    return out


def _edge_zone(box: list[float], page_w: float, page_h: float) -> str | None:
    x0, y0, x1, y1 = box
    h = y1 - y0
    w = x1 - x0
    if y1 <= page_h * 0.09 and h <= page_h * 0.05:
        return "top-edge"
    if y0 >= page_h * 0.84 and h <= page_h * 0.06:
        return "bottom-edge"
    if x1 <= page_w * 0.08 and w <= page_w * 0.05:
        return "left-edge"
    if x0 >= page_w * 0.92 and w <= page_w * 0.05:
        return "right-edge"
    return None


def _decoration_candidate(obj: dict[str, Any], box: list[float], page_w: float, page_h: float) -> dict[str, Any]:
    zone = _edge_zone(box, page_w, page_h)
    wr = (box[2] - box[0]) / max(1.0, page_w)
    hr = (box[3] - box[1]) / max(1.0, page_h)
    mathpix_type = str(obj.get("type") or "unknown")
    score = 0.0
    evidence: list[str] = []
    if zone:
        score += 0.42
        evidence.append(f"edge-zone:{zone}")
    if wr <= 0.20 and hr <= 0.035:
        score += 0.28
        evidence.append("small-edge-object")
    if mathpix_type == "page_info":
        score += 0.15
        evidence.append("mathpix-page_info")
    if wr <= 0.03 or hr <= 0.015:
        score += 0.15
        evidence.append("very-small")
    return {
        "candidate": score >= 0.55,
        "score": round(min(score, 1.0), 3),
        "zone": zone,
        "evidence": evidence,
    }


def _visual_entities(objects: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for obj in objects:
        if obj.get("type") not in {"diagram", "figure", "image"}:
            continue
        b = _bbox(obj)
        if not b:
            continue
        labels = []
        for lid in obj.get("selected_labels") or []:
            label = by_id.get(str(lid))
            if label:
                labels.append({"id": lid, "type": label.get("type"), "bboxPx": _bbox(label)})
        out.append({
            "id": obj.get("id"),
            "line": obj.get("line"),
            "type": obj.get("type"),
            "bboxPx": b,
            "parentId": obj.get("parent_id"),
            "selectedLabels": labels,
            "assetBinding": "deferred",
            "topologyRole": "occupied-visual-box",
        })
    return out


def _body_frame_candidate(
    page: dict[str, Any],
    objects: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    decorations: set[str],
    page_w: float,
    page_h: float,
) -> dict[str, Any]:
    boxes: list[list[float]] = []
    body_ids: list[str] = []
    for obj in objects:
        oid = str(obj.get("id") or "")
        if oid in decorations:
            continue
        typ = str(obj.get("type") or "")
        if typ in {"page_info", "column"} and obj.get("children_ids"):
            leaves = _descendant_leaves(obj, by_id)
            for leaf in leaves:
                b = _bbox(leaf)
                if b:
                    boxes.append(b)
                    if leaf.get("id"):
                        body_ids.append(str(leaf.get("id")))
            continue
        if obj.get("children_ids"):
            continue
        b = _bbox(obj)
        if b:
            boxes.append(b)
            if oid:
                body_ids.append(oid)
    u = _union(boxes)
    if not u:
        return {"bboxPx": None, "marginsPx": None, "confidence": 0.0, "evidenceObjectIds": []}

    # Robust inner body bounds: do not let a single sidebar/visual define Word margins.
    lefts = sorted(b[0] for b in boxes)
    rights = sorted(b[2] for b in boxes)
    tops = sorted(b[1] for b in boxes)
    bottoms = sorted(b[3] for b in boxes)
    n = len(boxes)
    q = max(0, int(n * 0.12))
    left = median(lefts[q:min(n, q + max(1, n // 4))]) if n >= 8 else u[0]
    right_slice = rights[max(0, n - q - max(1, n // 4)): n - q if q else n]
    right = median(right_slice) if right_slice else u[2]
    top = min(tops)
    bottom = max(bottoms)

    # Keep candidate inside physical page and never narrower than half page.
    if right - left < page_w * 0.50:
        left, right = u[0], u[2]
    candidate = [max(0.0, left), max(0.0, top), min(page_w, right), min(page_h, bottom)]
    margins = {
        "left": round(candidate[0], 2),
        "top": round(candidate[1], 2),
        "right": round(page_w - candidate[2], 2),
        "bottom": round(page_h - candidate[3], 2),
    }
    return {
        "bboxPx": [round(v, 2) for v in candidate],
        "occupiedUnionBBoxPx": [round(v, 2) for v in u],
        "marginsPx": margins,
        "confidence": 0.58,
        "policy": "candidate-only; excludes likely page decorations but does not let isolated sidebars define Word section margins",
        "evidenceObjectIds": body_ids,
    }


def build_page_frame_visual(lines_path: Path) -> dict[str, Any]:
    lines_path = Path(lines_path)
    payload = json.loads(lines_path.read_text(encoding="utf-8"))
    occupancy = build_lines_occupancy_graph(lines_path)
    occ_by_page = {p.get("page"): p for p in occupancy.get("pages") or []}

    pages_out = []
    all_edge_records: list[dict[str, Any]] = []

    for page in payload.get("pages") or []:
        objects = list(page.get("lines") or [])
        by_id = {str(o.get("id")): o for o in objects if o.get("id")}
        page_w = float(page.get("page_width") or 1)
        page_h = float(page.get("page_height") or 1)

        decorations = []
        decoration_ids: set[str] = set()
        for obj in objects:
            b = _bbox(obj)
            if not b:
                continue
            d = _decoration_candidate(obj, b, page_w, page_h)
            if d["candidate"]:
                oid = str(obj.get("id") or "")
                decoration_ids.add(oid)
                rec = {
                    "id": oid,
                    "line": obj.get("line"),
                    "type": obj.get("type"),
                    "bboxPx": b,
                    **d,
                }
                decorations.append(rec)
                all_edge_records.append({"page": page.get("page"), **rec})

        visuals = _visual_entities(objects, by_id)
        body_frame = _body_frame_candidate(page, objects, by_id, decoration_ids, page_w, page_h)
        occ_page = occ_by_page.get(page.get("page")) or {}

        pages_out.append({
            "page": page.get("page"),
            "physicalPage": {"bboxPx": [0.0, 0.0, page_w, page_h], "widthPx": page_w, "heightPx": page_h},
            "bodyFrameCandidate": body_frame,
            "pageDecorationCandidates": decorations,
            "visualEntities": visuals,
            "occupancyGraphRef": {
                "spatialNodeCount": len(occ_page.get("spatialNodes") or []),
                "spatialNodeIds": [n.get("id") for n in occ_page.get("spatialNodes") or []],
            },
        })

    # Cross-page repetition evidence for edge decorations.
    signatures = Counter()
    for r in all_edge_records:
        b = r["bboxPx"]
        page = next((p for p in pages_out if p["page"] == r["page"]), None)
        if not page:
            continue
        pw = page["physicalPage"]["widthPx"]; ph = page["physicalPage"]["heightPx"]
        sig = (
            r.get("zone"),
            round(((b[0] + b[2]) / 2) / max(1.0, pw), 1),
            round(((b[1] + b[3]) / 2) / max(1.0, ph), 1),
            str(r.get("type") or "unknown"),
        )
        signatures[sig] += 1

    repeat = [
        {"signature": list(sig), "pageCount": count, "repeated": count >= 2}
        for sig, count in signatures.items()
        if count >= 2
    ]

    return {
        "version": VERSION,
        "source": str(lines_path),
        "policy": (
            "Lines-only diagnostic page-frame layer. Physical page bounds are authoritative from Lines page dimensions. "
            "Body margins, header/footer/page-decoration labels are candidates only. Visual assets are not loaded, but their bbox occupancy and label relations are preserved."
        ),
        "summary": {
            "pageCount": len(pages_out),
            "visualEntityCount": sum(len(p["visualEntities"]) for p in pages_out),
            "decorationCandidateCount": sum(len(p["pageDecorationCandidates"]) for p in pages_out),
            "repeatedEdgeSignatureCount": len(repeat),
        },
        "repeatedEdgeEvidence": repeat,
        "pages": pages_out,
    }


__all__ = ["build_page_frame_visual"]
