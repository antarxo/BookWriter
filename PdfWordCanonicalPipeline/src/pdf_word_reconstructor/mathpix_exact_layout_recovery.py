from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


VERSION = "mathpix-exact-layout-recovery-0.1"
_TABLE_TYPES = {"table", "simple_cell", "cell", "tabular", "table_row"}
_TEXT_TYPES = {"text", "paragraph", "list_item", "section_header", "heading"}


def _normalize(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\\begin\{[^{}]+\}|\\end\{[^{}]+\}", " ", text)
    text = re.sub(r"\\(?:hline|cline\{[^{}]+\}|multicolumn\{[^{}]+\}\{[^{}]+\})", " ", text)
    text = text.replace("\\\\", " ").replace("&", " ")
    text = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol|emph|textbf|textit)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+", " ", text)
    text = re.sub(r"[{}$|]", " ", text)
    text = re.sub(r"[^0-9A-Za-zΑ-Ωα-ωΆ-Ώά-ώ]+", "", text.casefold())
    return text


def _source_text(item: dict[str, Any]) -> str:
    authoritative = item.get("authoritativeContent") if isinstance(item.get("authoritativeContent"), dict) else {}
    for value in (
        authoritative.get("rawMarkdown"),
        item.get("rawMarkdown"),
        authoritative.get("text"),
        authoritative.get("plainText"),
        item.get("text"),
    ):
        normalized = _normalize(value)
        if normalized:
            return normalized
    return ""


def _record_text(record: dict[str, Any]) -> str:
    for value in (record.get("text_display"), record.get("text")):
        normalized = _normalize(value)
        if normalized:
            return normalized
    raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
    for key in ("text_display", "text", "latex", "value"):
        normalized = _normalize(raw.get(key))
        if normalized:
            return normalized
    return ""


def _bbox(record: dict[str, Any]) -> list[float] | None:
    box = record.get("bbox_pt") if isinstance(record.get("bbox_pt"), dict) else {}
    try:
        x0, y0, x1, y1 = (float(box.get(k)) for k in ("x0", "y0", "x1", "y1"))
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]


def _compatible(markdown_type: str, record_type: str) -> bool:
    mt = markdown_type.strip().lower()
    rt = record_type.strip().lower()
    if mt in {"table", "latex_table"}:
        return rt in _TABLE_TYPES
    if mt in {"paragraph", "heading", "title", "caption", "list", "latex_list"}:
        return rt in _TEXT_TYPES
    return False


def _page(item: dict[str, Any]) -> int:
    for key in ("pdfPage", "inferredPage", "markdownPageHint"):
        try:
            value = int(item.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0


def recover_exact_mathpix_layouts(
    page_layout_spine: dict[str, Any],
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
) -> dict[str, Any]:
    """Recover only still-unmapped rows by exact same-page MMD↔lines identity."""
    source_by_id = {
        str(item.get("id") or ""): item
        for item in markdown_pdf_spine.get("items", []) or []
        if item.get("id")
    }
    page_by_no = {
        int(page.get("page") or 0): page
        for page in page_structure.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }

    candidates: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for page_no, page in page_by_no.items():
        line_page = page.get("mathpixLinePageMap") if isinstance(page.get("mathpixLinePageMap"), dict) else {}
        for record in line_page.get("objects", []) or []:
            box = _bbox(record)
            norm = _record_text(record)
            record_type = str(record.get("type") or "").strip().lower()
            if box and norm:
                candidates[(page_no, record_type, norm)].append(record)
    for rows in candidates.values():
        rows.sort(key=lambda row: (
            float(((row.get("bbox_pt") or {}).get("y0") or 0.0)),
            float(((row.get("bbox_pt") or {}).get("x0") or 0.0)),
            int(row.get("line") or 0),
        ))

    used_ids = {
        str((row.get("layout") or {}).get("slotId") or "")
        for row in page_layout_spine.get("rows", []) or []
        if str((row.get("layoutContract") or {}).get("status") or "") == "usable"
    }
    recovered: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for row in page_layout_spine.get("rows", []) or []:
        if str((row.get("layoutContract") or {}).get("status") or "") == "usable":
            continue
        markdown_id = str(row.get("markdownId") or "")
        source = source_by_id.get(markdown_id) or {}
        markdown_type = str(row.get("markdownType") or source.get("type") or "").strip().lower()
        if markdown_type not in {"latex_table", "table", "paragraph", "heading", "title", "caption", "list", "latex_list"}:
            continue
        page_no = _page(source)
        norm = _source_text(source)
        matches: list[dict[str, Any]] = []
        if page_no and norm:
            for (candidate_page, record_type, candidate_norm), records in candidates.items():
                if candidate_page != page_no or candidate_norm != norm or not _compatible(markdown_type, record_type):
                    continue
                matches.extend(record for record in records if str(record.get("id") or "") not in used_ids)
        matches.sort(key=lambda record: (
            float(((record.get("bbox_pt") or {}).get("y0") or 0.0)),
            float(((record.get("bbox_pt") or {}).get("x0") or 0.0)),
            int(record.get("line") or 0),
        ))
        if not matches:
            unresolved.append({"markdownId": markdown_id, "markdownType": markdown_type, "page": page_no or None, "reason": "no-unused-exact-compatible-lines-match"})
            continue
        record = matches[0]
        line_id = str(record.get("id") or "")
        box = _bbox(record)
        if not line_id or not box:
            continue
        used_ids.add(line_id)
        page = page_by_no.get(page_no) or {}
        try:
            width = float(page.get("width_pt") or 0.0)
            height = float(page.get("height_pt") or 0.0)
        except (TypeError, ValueError):
            width = height = 0.0
        relative = [round(box[0]/width, 6), round(box[1]/height, 6), round(box[2]/width, 6), round(box[3]/height, 6)] if width > 0 and height > 0 else None
        semantic = "table" if markdown_type in {"table", "latex_table"} else ("heading" if markdown_type in {"heading", "title"} else "text")
        placement = "normal-flow"
        layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
        layout.update({
            "status": "layout-slot",
            "matchMode": "exact-mmd-lines-identity",
            "score": 100.0,
            "page": page_no,
            "slotId": line_id,
            "slotSource": "mathpix-lines-object",
            "slotType": "table" if semantic == "table" else "text",
            "semanticType": semantic,
            "bbox": box,
            "spanning": False,
            "flowOrder": source.get("orderIndex"),
        })
        row["layout"] = layout
        row["layoutContract"] = {
            "status": "usable",
            "page": page_no,
            "layoutMode": page.get("layout_mode"),
            "slot": {"id": line_id, "source": "mathpix-lines-object", "type": layout["slotType"], "semanticType": semantic},
            "box": {"absolutePt": box, "relativePage": relative, "source": "mathpix-lines-scaled-to-pdf-points"},
            "placement": placement,
            "column": {"index": None, "role": "main", "spanning": False},
            "builderUse": {"safeForFlowOrdering": True, "requiresPositionedFrame": False, "requiresVisualPlacement": False},
            "styleHint": {"role": "body" if semantic == "text" else semantic, "markdownType": markdown_type, "semanticType": semantic, "source": "exact-mmd-lines-identity"},
            "evidence": {"lineId": line_id, "type": record.get("type"), "subtype": record.get("subtype"), "bboxPt": box, "source": "page_structure.mathpixLinePageMap"},
        }
        recovered.append({"markdownId": markdown_id, "markdownType": markdown_type, "page": page_no, "lineId": line_id, "bboxPt": box})

    audit = {
        "version": VERSION,
        "recoveredCount": len(recovered),
        "unresolvedCount": len(unresolved),
        "policy": "remaining layout rows only; exact normalized same-page MMD↔Mathpix-lines identity; compatible structural type; no fuzzy/PDF search",
        "recovered": recovered,
        "unresolved": unresolved,
    }
    page_layout_spine["mathpixExactLayoutRecovery"] = audit
    page_layout_spine.setdefault("summary", {})["mathpixExactLayoutRecovery"] = {"recoveredCount": len(recovered), "unresolvedCount": len(unresolved)}
    return audit


__all__ = ["recover_exact_mathpix_layouts"]
