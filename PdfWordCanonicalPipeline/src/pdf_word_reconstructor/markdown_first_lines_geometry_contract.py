from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .build_contract import build_build_contract
from .lines_only_region_contract import build_lines_only_region_contract
from .lines_first_markdown_augmented_contract import _markdown_records, _norm, _row_text

VERSION = "markdown-first-lines-geometry-contract-0.1"
_MAX_SPAN = 8
_MIN_SCORE = 64.0


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


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _span_text(rows: list[dict[str, Any]], start: int, end: int) -> str:
    return " ".join(_row_text(row).strip() for row in rows[start:end] if _row_text(row).strip()).strip()


def _score(a: str, b: str) -> tuple[float, float]:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0, 0.0
    if na == nb:
        return 100.0, 1.0
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb)
    token_f1 = (2.0 * overlap / max(1, len(ta) + len(tb))) if overlap else 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    balance = min(len(na), len(nb)) / max(1, max(len(na), len(nb)))
    score = 100.0 * (0.50 * seq + 0.30 * token_f1 + 0.20 * balance)
    return score, balance


def _semantic(md_type: str) -> str:
    return {
        "heading": "heading",
        "title": "heading",
        "caption": "caption",
        "display_equation": "equation",
        "paragraph": "paragraph",
        "list": "list",
        "latex_list": "list",
    }.get(str(md_type or ""), "paragraph")


def _set_bbox(row: dict[str, Any], box: list[float]) -> None:
    layout = row.get("layout") or {}
    layout["bbox"] = box
    row["layout"] = layout
    pdf_geometry = row.get("pdfGeometry") or {}
    pdf_geometry["bbox"] = box
    pdf_geometry["source"] = "mathpix-lines-witness-union"
    row["pdfGeometry"] = pdf_geometry

    word = row.get("wordParagraph") or {}
    geometry = word.get("geometry") or {}
    geometry["bboxPt"] = box
    geometry["source"] = "mathpix-lines-witness-union"
    word["geometry"] = geometry
    row["wordParagraph"] = word

    contract = row.get("layoutContract") or {}
    cbox = contract.get("box") or {}
    cbox["absolutePt"] = box
    cbox["source"] = "mathpix-lines-witness-union"
    page_box = contract.get("pageBox") or {}
    try:
        width = float(page_box.get("widthPt") or 0.0)
        height = float(page_box.get("heightPt") or 0.0)
    except (TypeError, ValueError):
        width = height = 0.0
    if width > 0 and height > 0:
        cbox["relativePage"] = [round(box[0]/width, 6), round(box[1]/height, 6), round(box[2]/width, 6), round(box[3]/height, 6)]
    contract["box"] = cbox
    cword = contract.get("wordParagraph") or {}
    cgeo = cword.get("geometry") or {}
    cgeo["bboxPt"] = box
    cgeo["source"] = "mathpix-lines-witness-union"
    cword["geometry"] = cgeo
    contract["wordParagraph"] = cword
    row["layoutContract"] = contract


def _match_markdown(md: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    row_cursor = 0
    for md_i, item in enumerate(md):
        best: dict[str, Any] | None = None
        max_start = min(len(rows), row_cursor + 18)
        starts = list(range(row_cursor, max_start))
        hinted_page = item.get("page")
        if isinstance(hinted_page, int):
            starts.extend(i for i in range(row_cursor, len(rows)) if _page(rows[i]) == hinted_page and i not in starts)

        for start in starts:
            page = _page(rows[start])
            same_page_end = start
            while same_page_end < len(rows) and _page(rows[same_page_end]) == page:
                same_page_end += 1
            for end in range(start + 1, min(same_page_end, start + _MAX_SPAN) + 1):
                line_text = _span_text(rows, start, end)
                score, balance = _score(str(item.get("text") or ""), line_text)
                if hinted_page == page:
                    score += 2.0
                if balance < 0.34:
                    score -= 12.0
                if start > row_cursor + 8:
                    score -= min(10.0, (start - row_cursor - 8) * 0.7)
                candidate = {
                    "markdownIndex": md_i,
                    "markdownId": item.get("id"),
                    "rowStart": start,
                    "rowEnd": end,
                    "spanLength": end - start,
                    "page": page,
                    "score": round(score, 3),
                    "lengthBalance": round(balance, 4),
                    "accepted": score >= _MIN_SCORE,
                }
                if best is None or candidate["score"] > best["score"]:
                    best = candidate

        if best and best["accepted"]:
            matches.append(best)
            row_cursor = int(best["rowEnd"])
        else:
            matches.append({
                "markdownIndex": md_i,
                "markdownId": item.get("id"),
                "rowStart": None,
                "rowEnd": None,
                "spanLength": 0,
                "page": hinted_page,
                "score": round(float(best["score"]), 3) if best else 0.0,
                "lengthBalance": float(best["lengthBalance"]) if best else 0.0,
                "accepted": False,
            })
    return matches


def build_markdown_first_lines_geometry_contract(
    lines_path: Path,
    mmd_path: Path,
    page_width_pt: float = 595.276,
) -> dict[str, Any]:
    """MMD-first document skeleton with Lines-only geometry witnesses.

    MMD owns block identity, text, semantic type and order. Lines may supply only a
    contiguous same-page geometry/typography witness. Unmatched MMD blocks are reported
    and are not invented into Word geometry in this diagnostic probe.
    """
    base = deepcopy(build_lines_only_region_contract(Path(lines_path), page_width_pt=page_width_pt))
    md = _markdown_records(Path(mmd_path))
    source_rows = list((base.get("pageLayoutSpine") or {}).get("rows", []) or [])
    matches = _match_markdown(md, source_rows)

    pages = {int(page.get("page") or 0): page for page in (base.get("pageStructure") or {}).get("pages", []) or []}
    for page in pages.values():
        page["flow"] = [item for item in (page.get("flow", []) or []) if item.get("type") != "text"]

    output_rows: list[dict[str, Any]] = []
    matched = 0
    consumed_lines = 0
    span_lengths: list[int] = []
    matched_md_ids: list[str] = []

    for match in matches:
        if not match["accepted"]:
            continue
        start, end = int(match["rowStart"]), int(match["rowEnd"])
        witness = source_rows[start:end]
        if not witness:
            continue
        item = md[int(match["markdownIndex"])]
        row = deepcopy(witness[0])
        page_no = _page(row)
        boxes = [box for box in (_bbox(r) for r in witness) if box]
        union_box = _union(boxes)
        text = str(item.get("text") or "")
        semantic = _semantic(str(item.get("type") or ""))
        slot_id = f"mmd-flow-{int(match['markdownIndex']):05d}"

        row["markdownId"] = f"mmd:{item.get('id')}"
        row["markdownType"] = semantic
        row["markdownText"] = text
        row["rawMarkdown"] = item.get("raw") or ""
        row["authoritativeContent"] = {"text": text, "plainText": text, "source": "mathpix-mmd-authority"}
        row["markdownFirstLinesMatch"] = {**match, "witnessLineSlots": [str((r.get("layout") or {}).get("slotId") or "") for r in witness]}

        layout = row.get("layout") or {}
        layout["slotId"] = slot_id
        layout["slotSource"] = "markdown-first.lines-witness"
        layout["spanning"] = False
        layout["matchMode"] = "markdown-first-lines-geometry"
        row["layout"] = layout
        if union_box:
            _set_bbox(row, union_box)

        contract = row.get("layoutContract") or {}
        contract["placement"] = "normal-flow"
        contract["authoritativeContent"] = row["authoritativeContent"]
        slot = contract.get("slot") or {}
        slot["id"] = slot_id
        slot["source"] = "markdown-first.lines-witness"
        slot["semanticType"] = semantic
        contract["slot"] = slot
        style = contract.get("styleHint") or {}
        style["role"] = "math" if semantic == "equation" else semantic
        style["semanticType"] = semantic
        style["source"] = "mathpix-mmd-authority"
        contract["styleHint"] = style
        builder_use = contract.get("builderUse") or {}
        builder_use["safeForFlowOrdering"] = True
        builder_use["requiresPositionedFrame"] = False
        contract["builderUse"] = builder_use
        row["layoutContract"] = contract

        word = row.get("wordParagraph") or {}
        word["placement"] = "normal-flow"
        row["wordParagraph"] = word

        flow_item = {
            "id": slot_id,
            "type": "text",
            "semantic_type": semantic,
            "bbox": union_box or _bbox(witness[0]),
            "text": text,
            "content_source": "mathpix-mmd-authority",
            "column_index": (layout.get("columnIndex")),
            "spanning": False,
            "lines_witness_slot_ids": row["markdownFirstLinesMatch"]["witnessLineSlots"],
        }
        pages[page_no].setdefault("flow", []).append(flow_item)

        matched += 1
        consumed_lines += len(witness)
        span_lengths.append(len(witness))
        matched_md_ids.append(str(item.get("id") or ""))
        output_rows.append(row)

    output_rows.sort(key=lambda r: int(next((i for i, item in enumerate(md) if f"mmd:{item.get('id')}" == r.get("markdownId")), 10**9)))
    for order, row in enumerate(output_rows):
        row["markdownOrder"] = order
        layout = row.get("layout") or {}
        layout["wordFlowOrder"] = order
        row["layout"] = layout

    for page in pages.values():
        page["flow"].sort(key=lambda item: ((item.get("bbox") or [0, 0, 0, 0])[1], (item.get("bbox") or [0, 0, 0, 0])[0]))
        page["layout_mode"] = "single_column"

    spine = base["pageLayoutSpine"]
    spine["rows"] = output_rows
    spine["version"] = VERSION
    spine["policy"] = (
        "MARKDOWN_FIRST_LINES_GEOMETRY uses MMD as block/content/semantic/order authority. Lines provide only contiguous "
        "same-page geometry and typography witnesses. Unmatched MMD blocks are reported and omitted rather than invented."
    )
    spine["markdownFirstLinesGeometry"] = {
        "markdownDocumentSkeletonAuthority": True,
        "markdownContentAuthority": True,
        "markdownSemanticAuthority": True,
        "linesGeometryWitnessOnly": True,
        "markdownMayCreateGeometry": False,
        "positionedFramesDisabled": True,
        "matchThreshold": _MIN_SCORE,
        "maxLinesSpan": _MAX_SPAN,
        "matches": matches,
    }

    build_contract = build_build_contract(spine)
    build_contract["sourceAuthority"] = {
        "content": "mathpix-mmd",
        "layout": "mathpix-lines-witness",
        "typography": "mathpix-lines-witness",
        "nativeDonor": None,
    }
    base["buildContract"] = build_contract
    base["version"] = VERSION
    base["markdownElements"] = md

    unmatched = [item for item, match in zip(md, matches) if not match["accepted"]]
    summary = base.get("summary") or {}
    summary.update({
        "markdownElementCount": len(md),
        "markdownMatchedBlockCount": matched,
        "markdownUnmatchedBlockCount": len(unmatched),
        "markdownMatchCoverage": round(matched / len(md), 5) if md else 1.0,
        "linesWitnessUnitCount": consumed_lines,
        "averageLinesWitnessSpanLength": round(sum(span_lengths) / len(span_lengths), 3) if span_lengths else 0.0,
        "outputUnitCount": len(output_rows),
        "buildReadyCount": int((build_contract.get("summary") or {}).get("readyCount") or 0),
        "buildUnresolvedCount": int((build_contract.get("summary") or {}).get("unresolvedCount") or 0),
        "unmatchedMarkdownSamples": [{"id": item.get("id"), "type": item.get("type"), "page": item.get("page"), "text": str(item.get("text") or "")[:160]} for item in unmatched[:12]],
    })
    base["summary"] = summary
    return base


__all__ = ["build_markdown_first_lines_geometry_contract"]
