from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .mathpix_lines_input import build_mathpix_line_layout_map

VERSION = "lines-side-rail-constituents-0.1"
STRUCTURAL_TYPES = {"page_info", "column"}
TEXT_TYPES = {"text", "section_header", "figure_label", "math"}


def _box(record: dict[str, Any]) -> list[float] | None:
    raw = record.get("bbox_px") if isinstance(record.get("bbox_px"), dict) else None
    if not raw:
        return None
    try:
        box = [float(raw[k]) for k in ("x0", "y0", "x1", "y1")]
    except (KeyError, TypeError, ValueError):
        return None
    return box if box[2] > box[0] and box[3] > box[1] else None


def _union(boxes: list[list[float]]) -> list[float]:
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _intersection_fraction(inner: list[float], outer: list[float]) -> float:
    x0 = max(inner[0], outer[0]); y0 = max(inner[1], outer[1])
    x1 = min(inner[2], outer[2]); y1 = min(inner[3], outer[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area = max(1.0, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return inter / area


def _text(record: dict[str, Any]) -> str:
    for key in ("text_display", "text", "conversion_output"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _ancestors(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    parent_id = str(record.get("parent_id") or "")
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if not parent:
            break
        result.append(parent)
        parent_id = str(parent.get("parent_id") or "")
    return result


def _column_ancestor(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    return next((a for a in _ancestors(record, by_id) if str(a.get("type") or "") == "column"), None)


def _classify_constituent(records: list[dict[str, Any]]) -> str:
    types = Counter(str(r.get("type") or "") for r in records)
    if types.get("diagram"):
        return "rail-figure"
    if types.get("figure_label"):
        return "rail-caption"
    if types.get("math"):
        return "rail-math-block"
    if types.get("section_header"):
        return "rail-heading"
    if types.get("text"):
        return "rail-text-block"
    return "rail-other"


def _group_key(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    rtype = str(record.get("type") or "")
    rid = str(record.get("id") or "")
    parent = str(record.get("parent_id") or "")
    # Diagrams and semantic singleton types remain separate renderer candidates.
    if rtype in {"diagram", "figure_label", "math", "section_header"}:
        return f"self:{rid or id(record)}"
    # Text siblings sharing an explicit non-column parent form one candidate block.
    if rtype == "text" and parent:
        p = by_id.get(parent)
        if p and str(p.get("type") or "") != "column":
            return f"parent:{parent}"
    return f"self:{rid or id(record)}"


def _ordered(records: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    def key(r: dict[str, Any]) -> tuple[Any, ...]:
        parent = by_id.get(str(r.get("parent_id") or ""))
        rank = 10**8
        if parent:
            children = [str(v) for v in (parent.get("children_ids") or []) if v]
            try:
                rank = children.index(str(r.get("id") or ""))
            except ValueError:
                pass
        b = _box(r) or [0, 0, 0, 0]
        return (rank, int(r.get("line") or 10**8), b[1], b[0])
    return sorted(records, key=key)


def _detect_side_rails(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect narrow edge columns as side-rail evidence only.

    This is diagnostic geometry, not a Word renderer decision. A rail must be a
    narrow Mathpix column near a page edge. It is deliberately stricter than L2's
    former narrow=>frame rule and never implies a floating frame by itself.
    """
    width = float(page.get("page_width_px") or 0.0)
    if width <= 0:
        return []
    columns = [r for r in page.get("objects", []) or [] if str(r.get("type") or "") == "column" and _box(r)]
    rails: list[dict[str, Any]] = []
    for column in columns:
        b = _box(column)
        assert b is not None
        fraction = (b[2] - b[0]) / width
        center = (b[0] + b[2]) / (2.0 * width)
        side = "left" if center < 0.5 else "right"
        edge_gap = b[0] / width if side == "left" else (width - b[2]) / width
        if fraction <= 0.34 and edge_gap <= 0.20:
            rails.append({
                "id": str(column.get("id") or f"rail-{len(rails)}"),
                "side": side,
                "bbox_px": [round(v, 3) for v in b],
                "width_fraction": round(fraction, 6),
                "source": "mathpix-lines-column-edge-geometry",
            })
    rails.sort(key=lambda r: (r["bbox_px"][1], r["bbox_px"][0]))
    return rails


def build_side_rail_constituent_report(lines_path: Path) -> dict[str, Any]:
    line_map = build_mathpix_line_layout_map(Path(lines_path), None)
    pages_out: list[dict[str, Any]] = []
    total_rails = 0
    total_raw = 0
    total_constituents = 0
    class_counts: Counter[str] = Counter()

    for page in line_map.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        records = list(page.get("objects", []) or [])
        by_id = {str(r.get("id")): r for r in records if r.get("id")}
        rails = _detect_side_rails(page)
        page_rails: list[dict[str, Any]] = []

        for rail in rails:
            rail_box = rail["bbox_px"]
            members: list[dict[str, Any]] = []
            for record in records:
                rtype = str(record.get("type") or "")
                b = _box(record)
                if not b or rtype == "page_info":
                    continue
                col = _column_ancestor(record, by_id)
                col_match = str((col or {}).get("id") or "") == rail["id"]
                spatial_match = _intersection_fraction(b, rail_box) >= 0.60
                if col_match or spatial_match:
                    members.append(record)

            # Keep structural envelopes in raw diagnostics but exclude them from
            # constituent grouping.
            renderable = [r for r in members if str(r.get("type") or "") not in STRUCTURAL_TYPES]
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for record in renderable:
                grouped[_group_key(record, by_id)].append(record)

            constituents: list[dict[str, Any]] = []
            for key, group in grouped.items():
                group = _ordered(group, by_id)
                boxes = [_box(r) for r in group if _box(r)]
                if not boxes:
                    continue
                cbox = _union([b for b in boxes if b is not None])
                kind = _classify_constituent(group)
                class_counts[kind] += 1
                constituents.append({
                    "id": key,
                    "class": kind,
                    "bbox_px": [round(v, 3) for v in cbox],
                    "member_ids": [str(r.get("id") or "") for r in group],
                    "member_types": dict(sorted(Counter(str(r.get("type") or "") for r in group).items())),
                    "parent_ids": sorted({str(r.get("parent_id") or "") for r in group if r.get("parent_id")}),
                    "text": " ".join(_text(r) for r in group if _text(r)).strip(),
                    "rendererDecision": "deferred",
                })

            constituents.sort(key=lambda c: (c["bbox_px"][1], c["bbox_px"][0]))
            type_counts = Counter(str(r.get("type") or "") for r in members)
            total_raw += len(members)
            total_constituents += len(constituents)
            page_rails.append({
                **rail,
                "rawObjectCount": len(members),
                "rawObjectTypes": dict(sorted(type_counts.items())),
                "renderableObjectCount": len(renderable),
                "constituentCount": len(constituents),
                "constituents": constituents,
            })

        if page_rails:
            total_rails += len(page_rails)
            pages_out.append({"page": page_no, "rails": page_rails})

    return {
        "version": VERSION,
        "source": str(Path(lines_path)),
        "policy": (
            "Mathpix Lines only. Side rails are diagnostic edge-column zones. "
            "Raw objects are decomposed into renderer-neutral constituent candidates; "
            "no Word frame/section decision is made."
        ),
        "summary": {
            "pageCountWithRails": len(pages_out),
            "sideRailCount": total_rails,
            "rawObjectsInRails": total_raw,
            "constituentCount": total_constituents,
            "constituentClasses": dict(sorted(class_counts.items())),
        },
        "pages": pages_out,
    }


__all__ = ["build_side_rail_constituent_report"]
