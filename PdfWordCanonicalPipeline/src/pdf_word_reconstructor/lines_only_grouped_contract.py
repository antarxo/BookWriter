from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

from .build_contract import build_build_contract
from .mathpix_lines_input import build_mathpix_line_layout_map

VERSION = "lines-only-grouped-contract-0.1"
_SEMANTIC = {
    "text": "paragraph",
    "section_header": "heading",
    "figure_label": "caption",
    "math": "equation",
    "diagram": "figure",
}
_TEXT_TYPES = {"text", "section_header", "figure_label", "math"}


def _box_px(record: dict[str, Any]) -> list[float] | None:
    box = record.get("bbox_px") if isinstance(record.get("bbox_px"), dict) else {}
    if not box:
        return None
    try:
        values = [float(box.get(key)) for key in ("x0", "y0", "x1", "y1")]
    except (TypeError, ValueError):
        return None
    return values if values[2] > values[0] and values[3] > values[1] else None


def _scale_box(box: list[float], sx: float, sy: float) -> list[float]:
    return [round(box[0] * sx, 3), round(box[1] * sy, 3), round(box[2] * sx, 3), round(box[3] * sy, 3)]


def _union(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _page_size(page: dict[str, Any], width_pt: float) -> tuple[float, float, float, float]:
    width_px = float(page.get("page_width_px") or 1.0)
    height_px = float(page.get("page_height_px") or 1.0)
    height_pt = width_pt * height_px / width_px
    return width_pt, height_pt, width_pt / width_px, height_pt / height_px


def _text(record: dict[str, Any]) -> str:
    for value in (record.get("text_display"), record.get("text"), record.get("conversion_output")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _ancestors(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
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


def _parent_group_key(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """Group only text siblings explicitly sharing a Lines parent and column.

    Semantic objects (headings, captions, math) stay independent. Text without an
    explicit parent also stays independent; L2 does not guess paragraph boundaries.
    """
    rtype = str(record.get("type") or "")
    rid = str(record.get("id") or "")
    if rtype != "text":
        return (f"self:{rid}", rtype)
    parent_id = str(record.get("parent_id") or "")
    if not parent_id:
        return (f"self:{rid}", rtype)
    col = _column_ancestor(record, by_id)
    col_id = str((col or {}).get("id") or "")
    return (f"parent:{parent_id}|col:{col_id}", rtype)


def _ordered_members(records: list[dict[str, Any]], parent: dict[str, Any] | None) -> list[dict[str, Any]]:
    if parent:
        children = [str(x) for x in (parent.get("children_ids") or []) if x]
        rank = {cid: i for i, cid in enumerate(children)}
    else:
        rank = {}
    return sorted(records, key=lambda r: (rank.get(str(r.get("id") or ""), 10**8), int(r.get("line") or 10**8), (_box_px(r) or [0,0,0,0])[1], (_box_px(r) or [0,0,0,0])[0]))


def _join_text(records: list[dict[str, Any]]) -> str:
    # Preserve source tokens, but collapse Lines line boundaries into spaces.
    return " ".join(_text(r) for r in records if _text(r)).strip()


def _font_size_pt(records: list[dict[str, Any]], sy: float) -> float | None:
    values = []
    for record in records:
        try:
            value = float(record.get("font_size"))
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.append(value * sy)
    return round(float(median(values)), 3) if values else None


def _typography(records: list[dict[str, Any]], sy: float) -> dict[str, Any]:
    size = _font_size_pt(records, sy)
    return {
        "source": "mathpix-lines-only-l2-grouped",
        "confidence": "medium" if size else "low",
        "fontSizePt": {"dominant": size},
        "fontFamily": {"dominant": None},
        "emphasis": {},
        "color": {"dominant": None},
        "lineCount": len(records),
        "lineBoxes": [],
    }


def _relative(box: list[float], width: float, height: float) -> list[float]:
    return [round(box[0]/width,6), round(box[1]/height,6), round(box[2]/width,6), round(box[3]/height,6)]


def build_lines_only_grouped_contract(lines_path: Path, page_width_pt: float = 595.276) -> dict[str, Any]:
    """LINES_ONLY_L2: source-pure Lines with explicit paragraph grouping and active-area inference.

    No PDF, Markdown or DOCX evidence is read. Paragraph grouping uses only explicit
    Lines parent/children relations. Page margins are inferred from the occupied Lines
    geometry rather than fixed renderer defaults. Output schema remains builder-compatible.
    """
    line_map = build_mathpix_line_layout_map(Path(lines_path), None)
    pages_out: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    layout_order_by_slot: dict[str, int] = {}
    visual_unresolved: list[dict[str, Any]] = []
    global_order = 0
    raw_text_lines = 0
    grouped_units = 0
    grouped_multi = 0
    page_margins: list[tuple[float,float,float,float]] = []

    for line_page in line_map.get("pages", []) or []:
        page_no = int(line_page.get("page") or 0)
        width_pt, height_pt, sx, sy = _page_size(line_page, page_width_pt)
        records = list(line_page.get("objects", []) or [])
        by_id = {str(r.get("id")): r for r in records if r.get("id")}

        column_records = [r for r in records if str(r.get("type") or "") == "column" and _box_px(r)]
        column_records.sort(key=lambda r: (_box_px(r) or [0,0,0,0])[0])
        column_index = {str(r.get("id")): i for i, r in enumerate(column_records) if r.get("id")}
        columns = []
        for r in column_records:
            b = _scale_box(_box_px(r), sx, sy)
            columns.append({"id": r.get("id"), "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3], "source": "mathpix-lines-column"})

        candidate_boxes = [_scale_box(_box_px(r), sx, sy) for r in records if _box_px(r) and str(r.get("type") or "") not in {"page_info", "column"}]
        active = _union(candidate_boxes) if candidate_boxes else [0.0, 0.0, width_pt, height_pt]
        left = max(0.0, active[0]); right = max(0.0, width_pt-active[2]); top = max(0.0, active[1]); bottom = max(0.0, height_pt-active[3])
        page_margins.append((left,right,top,bottom))

        groups: dict[tuple[str,str], list[dict[str, Any]]] = defaultdict(list)
        diagrams: list[dict[str, Any]] = []
        for r in records:
            rtype = str(r.get("type") or "")
            if rtype == "diagram" and _box_px(r):
                diagrams.append(r)
            elif rtype in _TEXT_TYPES and _box_px(r) and _text(r):
                groups[_parent_group_key(r, by_id)].append(r)
                raw_text_lines += 1

        units: list[dict[str, Any]] = []
        for (key, rtype), members in groups.items():
            parent = None
            if key.startswith("parent:"):
                parent_id = key.split("|",1)[0].split(":",1)[1]
                parent = by_id.get(parent_id)
            members = _ordered_members(members, parent)
            boxes = [_scale_box(_box_px(r), sx, sy) for r in members if _box_px(r)]
            if not boxes:
                continue
            box = _union(boxes)
            first = members[0]
            col = _column_ancestor(first, by_id)
            col_id = str((col or {}).get("id") or "")
            col_idx = column_index.get(col_id) if col_id else None
            col_box = _scale_box(_box_px(col), sx, sy) if col is not None and _box_px(col) else None
            narrow = bool(col_box and (col_box[2]-col_box[0]) < width_pt*0.48)
            units.append({"key": key, "members": members, "rtype": rtype, "semantic": _SEMANTIC.get(rtype, "paragraph"), "text": _join_text(members), "bbox": box, "columnIndex": col_idx, "columnBox": col_box, "positioned": narrow, "order": min(int(r.get("line") or 10**8) for r in members)})
            grouped_units += 1
            if len(members) > 1:
                grouped_multi += 1

        units.sort(key=lambda u: (u["order"], u["bbox"][1], u["bbox"][0]))
        flow: list[dict[str, Any]] = []
        visuals = []
        for d in diagrams:
            b = _scale_box(_box_px(d), sx, sy)
            did = str(d.get("id") or f"diagram-{page_no}-{len(visuals)}")
            visuals.append({"id": did, "kind": "figure", "bbox": b, "placement": "floating", "wrap": "none", "source": "mathpix-lines-only-l2"})
            visual_unresolved.append({"page": page_no, "id": did, "reason": "Lines diagram has geometry/semantics but no asset bytes"})

        for unit in units:
            member_ids = [str(r.get("id") or "") for r in unit["members"]]
            base_id = member_ids[0] or f"l2-{page_no}-{global_order}"
            slot_id = f"flow-{base_id}"
            placement = "positioned-text-frame" if unit["positioned"] else "normal-flow"
            spanning = bool(unit["positioned"])
            flow_item = {"id": slot_id, "type": "text", "semantic_type": unit["semantic"], "bbox": unit["bbox"], "text": unit["text"], "content_source": "mathpix-lines-only-l2", "column_index": unit["columnIndex"], "spanning": spanning, "lines_member_ids": member_ids}
            flow.append(flow_item)
            layout_order_by_slot[f"{page_no}:{slot_id}"] = global_order
            typography = _typography(unit["members"], sy)
            col_box_dict = None
            if unit["columnBox"]:
                cb = unit["columnBox"]
                col_box_dict = {"x0":cb[0],"y0":cb[1],"x1":cb[2],"y1":cb[3]}
            word = {
                "geometry": {"source":"mathpix-lines-only-l2","bboxPt":unit["bbox"],"columnBoxPt":unit["columnBox"],"leftIndentPt":0.0 if spanning else None,"rightIndentPt":0.0 if spanning else None,"firstLineIndentPt":0.0,"hangingIndentPt":0.0,"alignment":{"value":"left","confidence":"low","source":"lines-only-l2"},"lineHeightPt":None,"lineHeightSource":None,"lineCount":len(unit["members"]),"lineBoxes":[]},
                "typography": typography, "spacing": {},
                "pageColumns": {"layoutMode":"lines-grouped-layout","columns":columns,"columnCount":len(columns) if columns else 1,"source":"mathpix-lines"},
                "placement": placement,
                "sourcePolicy":{"content":"mathpix-lines","geometry":"mathpix-lines","typography":"mathpix-lines","nativeDonor":None},
            }
            role = "math" if unit["semantic"] == "equation" else unit["semantic"]
            content = {"text":unit["text"],"plainText":unit["text"],"source":"mathpix-lines-only-l2"}
            contract = {"status":"usable","page":page_no,"pageBox":{"widthPt":width_pt,"heightPt":height_pt},"layoutMode":"lines-grouped-layout","slot":{"id":slot_id,"source":"lines-only-l2.flow","type":"text","semanticType":unit["semantic"]},"column":{"index":unit["columnIndex"],"role":f"col-{unit['columnIndex']}" if unit["columnIndex"] is not None else "main","box":col_box_dict,"spanning":spanning},"box":{"absolutePt":unit["bbox"],"relativePage":_relative(unit["bbox"],width_pt,height_pt),"source":"mathpix-lines-region-union"},"placement":placement,"styleHint":{"role":role,"semanticType":unit["semantic"],"source":"mathpix-lines-semantic-type"},"builderUse":{"safeForFlowOrdering":not spanning,"requiresPositionedFrame":spanning,"requiresVisualPlacement":False},"wordParagraph":word,"authoritativeContent":content}
            rows.append({"markdownId":f"lines-group:{base_id}","markdownType":unit["semantic"],"markdownOrder":global_order,"markdownText":unit["text"],"authoritativeContent":content,"rawMarkdown":"","pdfTypography":typography,"pdfGeometry":{"bbox":unit["bbox"],"source":"mathpix-lines-region-union"},"pdfWitness":{},"layout":{"status":"layout-slot","matchMode":"lines-only-l2-direct","score":100.0,"page":page_no,"slotId":slot_id,"slotSource":"lines-only-l2.flow","slotType":"text","semanticType":unit["semantic"],"bbox":unit["bbox"],"columnIndex":unit["columnIndex"],"columnRole":f"col-{unit['columnIndex']}" if unit["columnIndex"] is not None else "main","spanning":spanning,"flowOrder":len(flow)-1,"wordFlowOrder":global_order},"layoutContract":contract,"wordParagraph":word,"docxDonor":None})
            global_order += 1

        pages_out.append({"page":page_no,"width_pt":round(width_pt,3),"height_pt":round(height_pt,3),"layout_mode":"two_columns" if len(columns)>=2 else "single_column","main_column":{"x0":round(active[0],3),"y0":round(active[1],3),"x1":round(active[2],3),"y1":round(active[3],3)},"section_margins":{"left":round(left,3),"right":round(right,3),"top":round(top,3),"bottom":round(bottom,3),"mirror":False},"columns":columns,"flow":flow,"visual_groups":visuals,"callouts":[],"source":"mathpix-lines-only-l2"})

    if page_margins:
        left = median(x[0] for x in page_margins); right = median(x[1] for x in page_margins); top = median(x[2] for x in page_margins); bottom = median(x[3] for x in page_margins)
    else:
        left=right=top=bottom=0.0
    if pages_out:
        page_setup = {"pageWidthPt":pages_out[0]["width_pt"],"pageHeightPt":pages_out[0]["height_pt"],"leftMarginPt":round(left,3),"rightMarginPt":round(right,3),"topMarginPt":round(top,3),"bottomMarginPt":round(bottom,3),"marginSource":"mathpix-lines-occupied-geometry-median","mirrorMargins":False}
    else:
        page_setup = None

    page_structure = {"version":VERSION,"source":"mathpix-lines-only-l2","pages":pages_out,"policy":"Lines-only: explicit parent/children paragraph grouping, explicit column ancestry, font_size and occupied-geometry page margins; no PDF/Markdown/DOCX evidence."}
    layout_spine = {"version":VERSION,"policy":"LINES_ONLY_L2 emits builder-compatible grouped text units and Lines-derived active-area margins.","layoutPreflight":{"version":VERSION,"source":"mathpix-lines-occupied-geometry","pageCount":len(pages_out),"pageSetupEstimate":page_setup},"layoutOrderBySlot":layout_order_by_slot,"rows":rows,"linesOnly":{"source":str(Path(lines_path)),"visualUnresolved":visual_unresolved}}
    build_contract = build_build_contract(layout_spine)
    build_contract["sourceAuthority"] = {"content":"mathpix-lines","layout":"mathpix-lines","typography":"mathpix-lines","nativeDonor":None}
    return {"version":VERSION,"lineLayoutMap":line_map,"pageStructure":page_structure,"pageLayoutSpine":layout_spine,"buildContract":build_contract,"summary":{"pageCount":len(pages_out),"rawTextLineCount":raw_text_lines,"groupedTextUnitCount":grouped_units,"multiLineGroupedUnitCount":grouped_multi,"visualUnresolvedCount":len(visual_unresolved),"buildReadyCount":int((build_contract.get("summary") or {}).get("readyCount") or 0),"buildUnresolvedCount":int((build_contract.get("summary") or {}).get("unresolvedCount") or 0),"pageSetupEstimate":page_setup}}


__all__ = ["build_lines_only_grouped_contract"]
