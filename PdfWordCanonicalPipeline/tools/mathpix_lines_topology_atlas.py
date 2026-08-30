from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _bbox(obj: dict[str, Any]) -> list[float] | None:
    r = obj.get("region") or {}
    try:
        x0 = float(r["top_left_x"])
        y0 = float(r["top_left_y"])
        w = float(r["width"])
        h = float(r["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return [x0, y0, x0 + w, y0 + h]


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[list[float]]:
    merged: list[list[float]] = []
    for a, b in sorted(intervals):
        if not merged or a > merged[-1][1]:
            merged.append([a, b])
        else:
            merged[-1][1] = max(merged[-1][1], b)
    return merged


def _vertical_segments(children: list[dict[str, Any]]) -> list[list[float]]:
    intervals = []
    for child in children:
        b = _bbox(child)
        if b:
            intervals.append((b[1], b[3]))
    return _merge_intervals(intervals)


def _coverage_and_gaps(children: list[dict[str, Any]], parent_box: list[float]) -> tuple[float, list[float], list[list[float]]]:
    segments = _vertical_segments(children)
    height = max(1.0, parent_box[3] - parent_box[1])
    covered = sum(b - a for a, b in segments)
    gaps = [segments[i + 1][0] - segments[i][1] for i in range(len(segments) - 1)]
    return covered / height, gaps, segments


def _object_summary(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": obj.get("id"),
        "line": obj.get("line"),
        "type": obj.get("type"),
        "subtype": obj.get("subtype"),
        "parentId": obj.get("parent_id"),
        "column": obj.get("column"),
        "fontSize": obj.get("font_size"),
        "bboxPx": _bbox(obj),
        "childCount": len(obj.get("children_ids") or []),
        "selectedLabels": list(obj.get("selected_labels") or []),
        "conversionOutput": obj.get("conversion_output"),
        "textPreview": (obj.get("text") or obj.get("text_display") or "").replace("\n", " ").strip()[:160],
    }


def _container_summary(obj: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    children = [by_id[cid] for cid in obj.get("children_ids") or [] if cid in by_id]
    parent_box = _bbox(obj)
    boxes = [_bbox(c) for c in children]
    boxes = [b for b in boxes if b]
    child_union = _union(boxes)
    coverage = None
    gaps: list[float] = []
    segments: list[list[float]] = []
    if parent_box:
        coverage, gaps, segments = _coverage_and_gaps(children, parent_box)
    return {
        **_object_summary(obj),
        "childTypes": dict(Counter(str(c.get("type") or "unknown") for c in children)),
        "childUnionBBoxPx": child_union,
        "verticalCoverageRatio": round(coverage, 4) if coverage is not None else None,
        "maxVerticalGapPx": round(max(gaps), 3) if gaps else 0.0,
        "largeVerticalGapsPx": [round(g, 3) for g in gaps if g >= 100.0],
        "verticalOccupiedSegmentsPx": [[round(v, 3) for v in s] for s in segments],
        "nestedContainerCount": sum(1 for c in children if c.get("children_ids")),
    }


def build_atlas(lines_path: Path) -> dict[str, Any]:
    payload = json.loads(lines_path.read_text(encoding="utf-8"))
    pages_out = []
    for page in payload.get("pages") or []:
        objects = list(page.get("lines") or page.get("objects") or [])
        by_id = {str(o.get("id")): o for o in objects if o.get("id")}
        top = [o for o in objects if not o.get("parent_id")]
        containers = [o for o in objects if o.get("children_ids")]
        figures = [o for o in objects if o.get("type") == "diagram"]
        selected_relations = []
        for fig in figures:
            for label_id in fig.get("selected_labels") or []:
                label = by_id.get(str(label_id))
                selected_relations.append({
                    "figureId": fig.get("id"),
                    "figureLine": fig.get("line"),
                    "figureBBoxPx": _bbox(fig),
                    "labelId": label_id,
                    "labelLine": label.get("line") if label else None,
                    "labelType": label.get("type") if label else None,
                    "labelBBoxPx": _bbox(label) if label else None,
                })

        page_info_content_containers = [
            _container_summary(o, by_id)
            for o in objects
            if o.get("type") == "page_info" and o.get("children_ids")
        ]
        top_unparented_text = [
            _object_summary(o)
            for o in top
            if o.get("type") in {"text", "section_header", "math"}
        ]
        pages_out.append({
            "page": page.get("page"),
            "imageId": page.get("image_id"),
            "pageWidthPx": page.get("page_width"),
            "pageHeightPx": page.get("page_height"),
            "objectCount": len(objects),
            "typeCounts": dict(Counter(str(o.get("type") or "unknown") for o in objects)),
            "topLevelCount": len(top),
            "topLevelTypeCounts": dict(Counter(str(o.get("type") or "unknown") for o in top)),
            "topLevelObjects": [_object_summary(o) for o in top],
            "topLevelUnparentedTextLike": top_unparented_text,
            "containers": [_container_summary(o, by_id) for o in containers],
            "selectedLabelRelations": selected_relations,
            "pageInfoContentContainers": page_info_content_containers,
        })

    return {
        "version": "mathpix-lines-topology-atlas-0.1",
        "source": str(lines_path),
        "policy": (
            "Diagnostic Lines-only measurements. Container labels such as 'column' and 'page_info' "
            "are recorded as Mathpix evidence and are not interpreted as Word renderer instructions."
        ),
        "pages": pages_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure Mathpix Lines topology without rendering Word.")
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    source = args.lines.resolve()
    if not source.exists():
        raise FileNotFoundError(f"Mathpix Lines not found: {source}")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    atlas = build_atlas(source)
    output.write_text(json.dumps(atlas, ensure_ascii=False, indent=2), encoding="utf-8")

    print("MODE: MATHPIX_LINES_TOPOLOGY_ATLAS")
    print("PDF INPUT       : OFF")
    print("WORD RENDERING  : OFF")
    print("LINES MEASURE   : ON")
    print(f"PAGES           : {len(atlas['pages'])}")
    print(f"OUTPUT          : {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
