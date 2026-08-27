from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


VERSION = "mathpix-exact-layout-recovery-0.3"
_TABLE_TYPES = {"table", "simple_cell", "cell", "tabular", "table_row"}
_TEXT_TYPES = {"text", "paragraph", "list_item", "section_header", "heading"}


def _normalize(value: Any) -> str:
    text = str(value or "")
    # Mathpix occasionally emits Cyrillic lookalikes for Greek option labels.
    # Canonicalize those identities before stripping punctuation/markup.
    text = text.replace("Г", "Γ").replace("г", "γ")
    text = text.replace("\\Delta", "Δ").replace("\\delta", "δ")
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


def _union_bbox(records: list[dict[str, Any]]) -> list[float] | None:
    boxes = [box for box in (_bbox(record) for record in records) if box]
    if not boxes:
        return None
    return [
        round(min(box[0] for box in boxes), 3),
        round(min(box[1] for box in boxes), 3),
        round(max(box[2] for box in boxes), 3),
        round(max(box[3] for box in boxes), 3),
    ]


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


def _reading_key(record: dict[str, Any]) -> tuple[float, float, int]:
    return (
        float(((record.get("bbox_pt") or {}).get("y0") or 0.0)),
        float(((record.get("bbox_pt") or {}).get("x0") or 0.0)),
        int(record.get("line") or 0),
    )


def _exact_sequence_matches(
    *,
    markdown_type: str,
    normalized_source: str,
    page_records: list[dict[str, Any]],
    used_ids: set[str],
    max_span: int | None = 8,
) -> list[list[dict[str, Any]]]:
    """Return exact contiguous Mathpix-lines sequences for one Markdown block.

    Only compatible, unused objects in reading order participate. Prefix pruning
    keeps long-block search deterministic even when max_span is unlimited.
    """
    if markdown_type not in {"paragraph", "heading", "title", "caption", "list", "latex_list"}:
        return []
    eligible = [
        record for record in page_records
        if str(record.get("id") or "") not in used_ids
        and _compatible(markdown_type, str(record.get("type") or ""))
        and _record_text(record)
        and _bbox(record)
    ]
    eligible.sort(key=_reading_key)
    matches: list[list[dict[str, Any]]] = []
    for start in range(len(eligible)):
        joined = ""
        span: list[dict[str, Any]] = []
        stop = len(eligible) if max_span is None else min(len(eligible), start + max_span)
        for index in range(start, stop):
            record = eligible[index]
            piece = _record_text(record)
            if not piece:
                break
            joined += piece
            span.append(record)
            if joined == normalized_source:
                matches.append(list(span))
                break
            if len(joined) >= len(normalized_source) or not normalized_source.startswith(joined):
                break
    return matches


def _exact_sequence_match(
    *,
    markdown_type: str,
    normalized_source: str,
    page_records: list[dict[str, Any]],
    used_ids: set[str],
    max_span: int | None = 8,
) -> list[dict[str, Any]] | None:
    matches = _exact_sequence_matches(
        markdown_type=markdown_type,
        normalized_source=normalized_source,
        page_records=page_records,
        used_ids=used_ids,
        max_span=max_span,
    )
    return matches[0] if len(matches) == 1 else None


def _unique_cross_page_sequence(
    *,
    markdown_type: str,
    normalized_source: str,
    records_by_page: dict[int, list[dict[str, Any]]],
    used_ids: set[str],
) -> tuple[int, list[dict[str, Any]]] | None:
    """Correct a bad Markdown page hint only from one unique exact lines identity."""
    if len(normalized_source) < 24:
        return None
    matches: list[tuple[int, list[dict[str, Any]]]] = []
    for page_no, records in sorted(records_by_page.items()):
        page_matches = _exact_sequence_matches(
            markdown_type=markdown_type,
            normalized_source=normalized_source,
            page_records=records,
            used_ids=used_ids,
            max_span=None,
        )
        for span in page_matches:
            matches.append((page_no, span))
            if len(matches) > 1:
                return None
    return matches[0] if len(matches) == 1 else None


def _materialize_layout(
    *,
    row: dict[str, Any],
    source: dict[str, Any],
    page: dict[str, Any],
    page_no: int,
    markdown_type: str,
    records: list[dict[str, Any]],
    match_mode: str,
) -> dict[str, Any] | None:
    if not records:
        return None
    box = _union_bbox(records)
    ids = [str(record.get("id") or "") for record in records if record.get("id")]
    if not box or not ids:
        return None
    slot_id = ids[0] if len(ids) == 1 else f"mathpix-lines-seq:{'+'.join(ids)}"
    try:
        width = float(page.get("width_pt") or 0.0)
        height = float(page.get("height_pt") or 0.0)
    except (TypeError, ValueError):
        width = height = 0.0
    relative = [
        round(box[0] / width, 6),
        round(box[1] / height, 6),
        round(box[2] / width, 6),
        round(box[3] / height, 6),
    ] if width > 0 and height > 0 else None
    semantic = "table" if markdown_type in {"table", "latex_table"} else ("heading" if markdown_type in {"heading", "title"} else "text")
    layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
    layout.update({
        "status": "layout-slot",
        "matchMode": match_mode,
        "score": 100.0,
        "page": page_no,
        "slotId": slot_id,
        "slotSource": "mathpix-lines-object" if len(ids) == 1 else "mathpix-lines-object-sequence",
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
        "slot": {
            "id": slot_id,
            "source": layout["slotSource"],
            "type": layout["slotType"],
            "semanticType": semantic,
        },
        "box": {
            "absolutePt": box,
            "relativePage": relative,
            "source": "mathpix-lines-scaled-to-pdf-points",
        },
        "placement": "normal-flow",
        "column": {"index": None, "role": "main", "spanning": False},
        "builderUse": {
            "safeForFlowOrdering": True,
            "requiresPositionedFrame": False,
            "requiresVisualPlacement": False,
        },
        "styleHint": {
            "role": "body" if semantic == "text" else semantic,
            "markdownType": markdown_type,
            "semanticType": semantic,
            "source": match_mode,
        },
        "evidence": {
            "lineIds": ids,
            "types": [record.get("type") for record in records],
            "subtypes": [record.get("subtype") for record in records],
            "bboxPt": box,
            "source": "page_structure.mathpixLinePageMap",
        },
    }
    return {
        "markdownId": row.get("markdownId"),
        "markdownType": markdown_type,
        "page": page_no,
        "lineIds": ids,
        "bboxPt": box,
        "matchMode": match_mode,
    }


def recover_exact_mathpix_layouts(
    page_layout_spine: dict[str, Any],
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
) -> dict[str, Any]:
    """Recover still-unmapped rows from exact MMD↔Mathpix-lines identity."""
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
    records_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for page_no, page in page_by_no.items():
        line_page = page.get("mathpixLinePageMap") if isinstance(page.get("mathpixLinePageMap"), dict) else {}
        for record in line_page.get("objects", []) or []:
            records_by_page[page_no].append(record)
            box = _bbox(record)
            norm = _record_text(record)
            record_type = str(record.get("type") or "").strip().lower()
            if box and norm:
                candidates[(page_no, record_type, norm)].append(record)
    for rows in candidates.values():
        rows.sort(key=_reading_key)
    for rows in records_by_page.values():
        rows.sort(key=_reading_key)

    used_ids = {
        str((row.get("layout") or {}).get("slotId") or "")
        for row in page_layout_spine.get("rows", []) or []
        if str((row.get("layoutContract") or {}).get("status") or "") == "usable"
    }
    recovered: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    corrected_page_hints: list[dict[str, Any]] = []

    for row in page_layout_spine.get("rows", []) or []:
        if str((row.get("layoutContract") or {}).get("status") or "") == "usable":
            continue
        markdown_id = str(row.get("markdownId") or "")
        source = source_by_id.get(markdown_id) or {}
        markdown_type = str(row.get("markdownType") or source.get("type") or "").strip().lower()
        if markdown_type not in {"latex_table", "table", "paragraph", "heading", "title", "caption", "list", "latex_list"}:
            continue
        original_page_no = _page(source)
        page_no = original_page_no
        norm = _source_text(source)
        matches: list[dict[str, Any]] = []
        if page_no and norm:
            for (candidate_page, record_type, candidate_norm), records in candidates.items():
                if candidate_page != page_no or candidate_norm != norm or not _compatible(markdown_type, record_type):
                    continue
                matches.extend(record for record in records if str(record.get("id") or "") not in used_ids)
        matches.sort(key=_reading_key)

        selected_records: list[dict[str, Any]] | None = [matches[0]] if matches else None
        match_mode = "exact-mmd-lines-identity"
        if selected_records is None and page_no and norm:
            selected_records = _exact_sequence_match(
                markdown_type=markdown_type,
                normalized_source=norm,
                page_records=records_by_page.get(page_no, []),
                used_ids=used_ids,
                max_span=8,
            )
            if selected_records:
                match_mode = "exact-mmd-lines-contiguous-sequence"

        # A sufficiently long block may carry a bad image-anchor-derived page hint.
        # Correct it only when one and only one exact Mathpix-lines sequence exists
        # across the selected page maps. This is identity recovery, not fuzzy search.
        if selected_records is None and norm:
            cross_page = _unique_cross_page_sequence(
                markdown_type=markdown_type,
                normalized_source=norm,
                records_by_page=records_by_page,
                used_ids=used_ids,
            )
            if cross_page:
                page_no, selected_records = cross_page
                match_mode = "exact-mmd-lines-unique-cross-page-sequence"
                corrected_page_hints.append({
                    "markdownId": markdown_id,
                    "fromPage": original_page_no or None,
                    "toPage": page_no,
                    "normalizedLength": len(norm),
                })
                source["pdfPage"] = page_no
                source["inferredPage"] = page_no
                source["pageHintCorrection"] = {
                    "fromPage": original_page_no or None,
                    "toPage": page_no,
                    "source": "unique-exact-mmd-lines-sequence",
                }

        if not selected_records:
            unresolved.append({
                "markdownId": markdown_id,
                "markdownType": markdown_type,
                "page": original_page_no or None,
                "reason": "no-unused-exact-compatible-lines-match-or-unique-exact-sequence",
            })
            continue

        materialized = _materialize_layout(
            row=row,
            source=source,
            page=page_by_no.get(page_no) or {},
            page_no=page_no,
            markdown_type=markdown_type,
            records=selected_records,
            match_mode=match_mode,
        )
        if not materialized:
            unresolved.append({
                "markdownId": markdown_id,
                "markdownType": markdown_type,
                "page": page_no or None,
                "reason": "exact-match-found-but-layout-materialization-failed",
            })
            continue
        for record in selected_records:
            line_id = str(record.get("id") or "")
            if line_id:
                used_ids.add(line_id)
        recovered.append(materialized)

    audit = {
        "version": VERSION,
        "recoveredCount": len(recovered),
        "unresolvedCount": len(unresolved),
        "pageHintCorrectionCount": len(corrected_page_hints),
        "policy": (
            "remaining layout rows only; exact normalized same-page MMD↔Mathpix-lines identity first; "
            "then unique exact same-page sequence; long blocks may correct a page hint only from one unique exact cross-page sequence; "
            "Greek/Cyrillic option-label lookalikes and Delta are canonicalized; no fuzzy/PDF search"
        ),
        "recovered": recovered,
        "pageHintCorrections": corrected_page_hints,
        "unresolved": unresolved,
    }
    page_layout_spine["mathpixExactLayoutRecovery"] = audit
    page_layout_spine.setdefault("summary", {})["mathpixExactLayoutRecovery"] = {
        "recoveredCount": len(recovered),
        "unresolvedCount": len(unresolved),
        "pageHintCorrectionCount": len(corrected_page_hints),
    }
    return audit


__all__ = ["recover_exact_mathpix_layouts"]
