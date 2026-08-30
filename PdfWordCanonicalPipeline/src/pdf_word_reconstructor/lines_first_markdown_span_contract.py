from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .build_contract import build_build_contract
from .lines_only_region_contract import build_lines_only_region_contract
from .lines_first_markdown_augmented_contract import _markdown_records, _norm, _row_text

VERSION = "lines-first-markdown-span-contract-0.1"
_MAX_SPAN = 8
_MIN_SCORE = 66.0


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _strict_score(a: str, b: str) -> tuple[float, float]:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0, 0.0
    if na == nb:
        return 100.0, 1.0
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb)
    token_f1 = (2.0 * overlap / max(1, len(ta) + len(tb))) if overlap else 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    length_balance = min(len(na), len(nb)) / max(1, max(len(na), len(nb)))
    score = 100.0 * (0.45 * seq + 0.35 * token_f1 + 0.20 * length_balance)
    return score, length_balance


def _page(row: dict[str, Any]) -> int:
    return int((row.get("layout") or {}).get("page") or 0)


def _bbox(row: dict[str, Any]) -> list[float] | None:
    box = (row.get("layout") or {}).get("bbox") or (row.get("pdfGeometry") or {}).get("bbox")
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        vals = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    return vals if vals[2] > vals[0] and vals[3] > vals[1] else None


def _span_text(rows: list[dict[str, Any]], start: int, end: int) -> str:
    return " ".join(_row_text(row).strip() for row in rows[start:end] if _row_text(row).strip()).strip()


def _match_spans(rows: list[dict[str, Any]], md: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    row_i = 0
    md_cursor = 0
    while row_i < len(rows):
        page = _page(rows[row_i])
        same_page_end = row_i
        while same_page_end < len(rows) and _page(rows[same_page_end]) == page:
            same_page_end += 1

        candidate_indices = list(range(md_cursor, min(len(md), md_cursor + 14)))
        same_page_md = [i for i in range(md_cursor, len(md)) if md[i].get("page") == page]
        for i in same_page_md:
            if i not in candidate_indices:
                candidate_indices.append(i)

        best: dict[str, Any] | None = None
        max_end = min(same_page_end, row_i + _MAX_SPAN)
        for end in range(row_i + 1, max_end + 1):
            line_text = _span_text(rows, row_i, end)
            if not line_text:
                continue
            for md_i in candidate_indices:
                item = md[md_i]
                score, balance = _strict_score(line_text, str(item.get("text") or ""))
                if item.get("page") == page:
                    score += 2.0
                if balance < 0.34:
                    score -= 12.0
                candidate = {
                    "rowStart": row_i,
                    "rowEnd": end,
                    "spanLength": end - row_i,
                    "page": page,
                    "markdownIndex": md_i,
                    "markdownId": item.get("id"),
                    "score": round(score, 3),
                    "lengthBalance": round(balance, 4),
                    "accepted": score >= _MIN_SCORE,
                }
                if best is None or candidate["score"] > best["score"]:
                    best = candidate

        if best and best["accepted"]:
            matches.append(best)
            row_i = int(best["rowEnd"])
            md_cursor = int(best["markdownIndex"]) + 1
        else:
            matches.append({
                "rowStart": row_i,
                "rowEnd": row_i + 1,
                "spanLength": 1,
                "page": page,
                "markdownIndex": None,
                "markdownId": None,
                "score": round(float(best["score"]), 3) if best else 0.0,
                "lengthBalance": float(best["lengthBalance"]) if best else 0.0,
                "accepted": False,
            })
            row_i += 1
    return matches


def _set_bbox(row: dict[str, Any], box: list[float]) -> None:
    layout = row.get("layout") or {}
    layout["bbox"] = box
    row["layout"] = layout
    pdf_geometry = row.get("pdfGeometry") or {}
    pdf_geometry["bbox"] = box
    pdf_geometry["source"] = "mathpix-lines-span-union"
    row["pdfGeometry"] = pdf_geometry

    word = row.get("wordParagraph") or {}
    geometry = word.get("geometry") or {}
    geometry["bboxPt"] = box
    geometry["source"] = "mathpix-lines-span-union"
    word["geometry"] = geometry
    row["wordParagraph"] = word

    contract = row.get("layoutContract") or {}
    contract_box = contract.get("box") or {}
    contract_box["absolutePt"] = box
    contract_box["source"] = "mathpix-lines-span-union"
    page_box = contract.get("pageBox") or {}
    try:
        width = float(page_box.get("widthPt") or 0.0)
        height = float(page_box.get("heightPt") or 0.0)
    except (TypeError, ValueError):
        width = height = 0.0
    if width > 0 and height > 0:
        contract_box["relativePage"] = [
            round(box[0] / width, 6), round(box[1] / height, 6),
            round(box[2] / width, 6), round(box[3] / height, 6),
        ]
    contract["box"] = contract_box
    contract_word = contract.get("wordParagraph") or {}
    contract_geometry = contract_word.get("geometry") or {}
    contract_geometry["bboxPt"] = box
    contract_geometry["source"] = "mathpix-lines-span-union"
    contract_word["geometry"] = contract_geometry
    contract["wordParagraph"] = contract_word
    row["layoutContract"] = contract


def build_lines_first_markdown_span_contract(
    lines_path: Path,
    mmd_path: Path,
    page_width_pt: float = 595.276,
) -> dict[str, Any]:
    """Lines-first reconstruction where MMD may merge adjacent Lines units.

    Lines remain authoritative for page membership and geometry. MMD may only merge
    contiguous Lines units on the same page when their combined text strongly matches
    one MMD semantic block. The merged geometry is the union of the Lines boxes.
    """
    result = deepcopy(build_lines_only_region_contract(Path(lines_path), page_width_pt=page_width_pt))
    md = _markdown_records(Path(mmd_path))
    spine = result["pageLayoutSpine"]
    rows = list(spine.get("rows", []) or [])
    matches = _match_spans(rows, md)

    pages = {int(p.get("page") or 0): p for p in (result["pageStructure"].get("pages", []) or [])}
    flow_by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for page_no, page in pages.items():
        for item in page.get("flow", []) or []:
            if item.get("type") == "text" and item.get("id"):
                flow_by_key[(page_no, str(item["id"]))] = item

    consumed_slot_ids_by_page: dict[int, set[str]] = {}
    new_rows: list[dict[str, Any]] = []
    accepted = 0
    absorbed = 0
    semantic_changes = 0
    text_changes = 0
    span_lengths: list[int] = []

    for match in matches:
        start, end = int(match["rowStart"]), int(match["rowEnd"])
        span = rows[start:end]
        if not span:
            continue
        row = deepcopy(span[0])
        if not match["accepted"]:
            new_rows.append(row)
            continue

        item = md[int(match["markdownIndex"])]
        old_text = _span_text(rows, start, end)
        new_text = str(item.get("text") or "")
        old_sem = str(row.get("markdownType") or "paragraph")
        new_sem = str(item.get("semantic") or old_sem)
        boxes = [box for box in (_bbox(r) for r in span) if box]
        union_box = _union(boxes)
        page_no = _page(row)
        slot_ids = [str((r.get("layout") or {}).get("slotId") or "") for r in span]
        slot_ids = [slot for slot in slot_ids if slot]

        row["markdownId"] = f"mmd:{item.get('id')}"
        row["markdownType"] = new_sem
        row["markdownText"] = new_text
        row["rawMarkdown"] = item.get("raw") or ""
        row["authoritativeContent"] = {"text": new_text, "plainText": new_text, "source": "mathpix-mmd-span-augmentation"}
        row["linesFirstMarkdownSpanMatch"] = {**match, "mergedSlotIds": slot_ids}
        if union_box:
            _set_bbox(row, union_box)

        layout = row.get("layout") or {}
        first_slot = str(layout.get("slotId") or "")
        flow_item = flow_by_key.get((page_no, first_slot))
        if flow_item is not None:
            flow_item["text"] = new_text
            flow_item["semantic_type"] = new_sem
            flow_item["content_source"] = "mathpix-mmd-via-lines-span-match"
            flow_item["lines_merged_slot_ids"] = slot_ids
            if union_box:
                flow_item["bbox"] = union_box

        for extra_slot in slot_ids[1:]:
            consumed_slot_ids_by_page.setdefault(page_no, set()).add(extra_slot)

        contract = row.get("layoutContract") or {}
        contract["authoritativeContent"] = row["authoritativeContent"]
        style = contract.get("styleHint") or {}
        style["role"] = "math" if new_sem == "equation" else new_sem
        style["semanticType"] = new_sem
        style["source"] = "mathpix-mmd-semantic-via-lines-span-match"
        contract["styleHint"] = style
        slot = contract.get("slot") or {}
        slot["semanticType"] = new_sem
        slot["mergedSlotIds"] = slot_ids
        contract["slot"] = slot
        row["layoutContract"] = contract

        if _norm(old_text) != _norm(new_text):
            text_changes += 1
        if new_sem != old_sem:
            semantic_changes += 1
        accepted += 1
        absorbed += max(0, len(span) - 1)
        span_lengths.append(len(span))
        new_rows.append(row)

    for page_no, slots in consumed_slot_ids_by_page.items():
        page = pages.get(page_no)
        if page is not None:
            page["flow"] = [item for item in (page.get("flow", []) or []) if str(item.get("id") or "") not in slots]

    for order, row in enumerate(new_rows):
        row["markdownOrder"] = order
        layout = row.get("layout") or {}
        layout["wordFlowOrder"] = order
        row["layout"] = layout

    spine["rows"] = new_rows
    spine["version"] = VERSION
    spine["policy"] = (
        "LINES_FIRST_MMD_SPAN keeps Lines page/geometry authority. MMD may merge only contiguous same-page Lines units "
        "when the combined Lines text strongly matches one MMD semantic block; geometry is the Lines bbox union."
    )
    spine["linesFirstMarkdownSpan"] = {
        "linesGeometryAuthority": True,
        "markdownMayMergeAdjacentLinesUnits": True,
        "markdownMayCreateGeometry": False,
        "markdownMaySplitLinesUnit": False,
        "positionedFramesDisabled": True,
        "maxSpanLength": _MAX_SPAN,
        "matchThreshold": _MIN_SCORE,
        "matches": matches,
    }

    build_contract = build_build_contract(spine)
    build_contract["sourceAuthority"] = {
        "content": "mathpix-lines-first+mmd-span-augmentation",
        "layout": "mathpix-lines",
        "typography": "mathpix-lines",
        "nativeDonor": None,
    }
    result["buildContract"] = build_contract
    result["version"] = VERSION
    result["markdownElements"] = md

    summary = result.get("summary") or {}
    summary.update({
        "originalLinesUnitCount": len(rows),
        "outputUnitCount": len(new_rows),
        "markdownElementCount": len(md),
        "markdownMatchedSpanCount": accepted,
        "linesUnitsAbsorbedByMerges": absorbed,
        "averageMatchedSpanLength": round(sum(span_lengths) / len(span_lengths), 3) if span_lengths else 0.0,
        "markdownSemanticChangeCount": semantic_changes,
        "markdownTextChangeCount": text_changes,
        "buildReadyCount": int((build_contract.get("summary") or {}).get("readyCount") or 0),
        "buildUnresolvedCount": int((build_contract.get("summary") or {}).get("unresolvedCount") or 0),
    })
    result["summary"] = summary
    return result


__all__ = ["build_lines_first_markdown_span_contract"]
