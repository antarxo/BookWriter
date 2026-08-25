from __future__ import annotations

import re
from collections import Counter
from typing import Any

from rapidfuzz import fuzz

from .common import compact_text, normalize_text


TEXT_TYPES = {"paragraph", "heading", "title", "author", "caption", "list", "latex_list", "table", "latex_table"}
VISUAL_TYPES = {"image", "figure", "display_equation"}


def _markdown_text(element: dict[str, Any]) -> str:
    for key in ("text", "captionText", "alt", "latex"):
        value = str(element.get(key) or "").strip()
        if value:
            return value
    preview = str(element.get("textPreview") or "").strip()
    preview = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", preview)
    preview = re.sub(r"\\(?:section\*?|subsection\*?|subsubsection\*?|title|author|caption)\s*\{([^{}]*)\}", r"\1", preview)
    preview = re.sub(r"\\(?:begin|end)\{[^{}]+\}", " ", preview)
    preview = re.sub(r"\\(?:item)(?:\[[^\]]*\])?", " ", preview)
    preview = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol|emph|textbf|textit)\s*\{([^{}]*)\}", r"\1", preview)
    preview = re.sub(r"\\[a-zA-Z]+", " ", preview)
    preview = re.sub(r"[{}$]", " ", preview)
    return re.sub(r"\s+", " ", preview).strip()


def _line_text(line: dict[str, Any]) -> str:
    return "".join(str(span.get("text", "")) for span in line.get("spans", [])).strip()


def _union_boxes(boxes: list[Any]) -> list[float] | None:
    parsed = [box for box in (_bbox(item) for item in boxes) if box]
    if not parsed:
        return None
    return [
        min(box[0] for box in parsed),
        min(box[1] for box in parsed),
        max(box[2] for box in parsed),
        max(box[3] for box in parsed),
    ]


def _pdf_regions(pdf_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for page in pdf_analysis.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for region in page.get("regions", []) or []:
            if region.get("type") != "text":
                continue
            norm = normalize_text(region.get("text", ""))
            if len(norm) < 4:
                continue
            semantic = region.get("semantic") or {}
            lines = [
                line for line in (region.get("lines") or [])
                if normalize_text(_line_text(line))
            ]
            if len(lines) >= 2:
                for line_index, line in enumerate(lines, start=1):
                    line_text = _line_text(line)
                    line_norm = normalize_text(line_text)
                    if len(line_norm) < 4:
                        continue
                    regions.append({
                        "page": page_no,
                        "id": f"{region.get('id')}-line{line_index:03d}",
                        "parentRegion": region.get("id"),
                        "lineIndex": line_index,
                        "rowGranularity": "pdf-line",
                        "text": line_text,
                        "normalized": line_norm,
                        "bbox": line.get("bbox"),
                        "semanticType": semantic.get("type"),
                        "flowZone": semantic.get("flow_zone"),
                    })
                for start in range(0, len(lines)):
                    for span in (2, 3, 4):
                        chunk = lines[start:start + span]
                        if len(chunk) != span:
                            continue
                        chunk_text = "\n".join(_line_text(line) for line in chunk if _line_text(line))
                        chunk_norm = normalize_text(chunk_text)
                        if len(chunk_norm) < 18:
                            continue
                        regions.append({
                            "page": page_no,
                            "id": f"{region.get('id')}-lines{start+1:03d}-{start+span:03d}",
                            "parentRegion": region.get("id"),
                            "lineIndex": start + 1,
                            "lineSpan": span,
                            "rowGranularity": "pdf-line-cluster",
                            "text": chunk_text,
                            "normalized": chunk_norm,
                            "bbox": _union_boxes([line.get("bbox") for line in chunk]),
                            "semanticType": semantic.get("type"),
                            "flowZone": semantic.get("flow_zone"),
                        })
                continue
            regions.append({
                "page": page_no,
                "id": region.get("id"),
                "rowGranularity": "pdf-region",
                "text": region.get("text", ""),
                "normalized": norm,
                "bbox": region.get("bbox"),
                "semanticType": semantic.get("type"),
                "flowZone": semantic.get("flow_zone"),
            })
    return regions


def _score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 12 and shorter in longer:
        coverage = len(shorter) / max(1, len(longer))
        return min(99.0, 78.0 + 21.0 * coverage)
    ratio = float(fuzz.ratio(a, b))
    partial = float(fuzz.partial_ratio(a, b))
    token = float(fuzz.token_set_ratio(a, b))
    return min(100.0, 0.30 * ratio + 0.35 * partial + 0.35 * token)


def _status(score: float, kind: str) -> str:
    if kind in VISUAL_TYPES:
        return "page-hint"
    if score >= 84.0:
        return "strong"
    if score >= 70.0:
        return "medium"
    if score >= 58.0:
        return "weak"
    return "unplaced"


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _flow_zone_rank(row: dict[str, Any]) -> tuple[int, float]:
    zone = str(row.get("flowZone") or "")
    box = _bbox(row.get("bbox")) or [0.0, 0.0, 0.0, 0.0]
    if zone == "left_sidebar":
        return (0, box[0])
    if zone == "main":
        return (1, box[0])
    if zone == "right_sidebar":
        return (2, box[0])
    return (1, box[0])


def _reading_order_key(row: dict[str, Any]) -> tuple[int, float, float, str]:
    box = _bbox(row.get("bbox")) or [0.0, 0.0, 0.0, 0.0]
    zone_rank, x_rank = _flow_zone_rank(row)
    return (zone_rank, box[1], x_rank, str(row.get("id") or ""))


def _position_page_hint(element: dict[str, Any]) -> int | None:
    position = element.get("markdownPosition") if isinstance(element.get("markdownPosition"), dict) else {}
    if position.get("confidence") not in {"high", "medium"}:
        return None
    page = position.get("page")
    return int(page) if isinstance(page, int) else None


def _element_page_hint(element: dict[str, Any], selected_pages: set[int]) -> int | None:
    position_page = _position_page_hint(element)
    if position_page in selected_pages:
        return int(position_page)
    original_page_hint = element.get("page") if isinstance(element.get("page"), int) else None
    inferred_page = element.get("_spineInferredPage") if isinstance(element.get("_spineInferredPage"), int) else None
    if original_page_hint in selected_pages:
        return int(original_page_hint)
    if inferred_page in selected_pages:
        return int(inferred_page)
    return None


def _element_page_window(element: dict[str, Any], selected_pages: set[int]) -> list[int]:
    value = element.get("pageWindow")
    if isinstance(value, list) and len(value) == 2:
        try:
            start, end = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            start = end = 0
        if start and end and start <= end:
            return [page for page in range(start, end + 1) if page in selected_pages]
    page = _element_page_hint(element, selected_pages)
    return [page] if page is not None else []


def _page_infos(pdf_analysis: dict[str, Any]) -> dict[int, dict[str, Any]]:
    infos: dict[int, dict[str, Any]] = {}
    for page in (pdf_analysis or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        if page_no:
            infos[page_no] = page
    return infos


def _bbox_px_to_pt(box: Any, page_info: dict[str, Any] | None) -> list[float] | None:
    source_box = _bbox(box)
    if not source_box or not page_info:
        return None
    try:
        width_pt = float(page_info.get("width_pt") or page_info.get("widthPt") or 0.0)
    except (TypeError, ValueError):
        return None
    if width_pt <= 0:
        return None
    scale = width_pt / 2048.0
    return [round(value * scale, 3) for value in source_box]


def _markdown_position_window(element: dict[str, Any], page_info: dict[str, Any] | None) -> dict[str, Any] | None:
    position = element.get("markdownPosition") if isinstance(element.get("markdownPosition"), dict) else {}
    if not position:
        return None
    kind = str(position.get("kind") or "")
    exact = _bbox_px_to_pt(position.get("bboxPx"), page_info)
    if exact:
        return {"kind": kind or "image-anchor", "bbox": exact, "source": "markdown-position-anchor"}
    page_no = page_info.get("page") if isinstance(page_info, dict) else None
    if position.get("page") is not None and page_no is not None and int(position.get("page")) != int(page_no):
        return None
    before = position.get("before") if isinstance(position.get("before"), dict) else {}
    after = position.get("after") if isinstance(position.get("after"), dict) else {}
    before_box = _bbox_px_to_pt(before.get("bboxPx"), page_info)
    after_box = _bbox_px_to_pt(after.get("bboxPx"), page_info)
    try:
        width_pt = float(page_info.get("width_pt") or page_info.get("widthPt") or 0.0)
        height_pt = float(page_info.get("height_pt") or page_info.get("heightPt") or 0.0)
    except (TypeError, ValueError):
        width_pt = 0.0
        height_pt = 0.0
    if kind == "before-image-anchor" and after_box:
        y0 = 36.0
        y1 = max(y0 + 6.0, after_box[1])
        return {
            "kind": "before-image-anchor",
            "bbox": [0.0, round(y0, 3), round(width_pt or after_box[2], 3), round(min(y1, height_pt or y1), 3)],
            "source": "markdown-position-anchor-band",
        }
    if kind == "after-image-anchor" and before_box:
        y0 = before_box[3]
        y1 = max(y0 + 6.0, (height_pt - 40.0) if height_pt else y0 + 72.0)
        return {
            "kind": "after-image-anchor",
            "bbox": [0.0, round(y0, 3), round(width_pt or before_box[2], 3), round(y1, 3)],
            "source": "markdown-position-anchor-band",
        }
    if kind != "between-image-anchors":
        return None
    if not before_box or not after_box:
        return None
    y0 = min(after_box[1], max(before_box[3], before_box[1]))
    y1 = max(y0, after_box[1])
    if y1 - y0 < 6.0:
        return None
    return {
        "kind": "between-image-anchors",
        "bbox": [0.0, round(y0, 3), round(width_pt or max(before_box[2], after_box[2]), 3), round(y1, 3)],
        "source": "markdown-position-anchor-band",
    }


def _row_inside_position_window(row: dict[str, Any], window: dict[str, Any] | None) -> bool:
    if not window:
        return True
    row_box = _bbox(row.get("bbox"))
    window_box = _bbox(window.get("bbox"))
    if not row_box or not window_box:
        return True
    center_y = (row_box[1] + row_box[3]) / 2.0
    return window_box[1] - 3.0 <= center_y <= window_box[3] + 3.0


def _page_local_monotonic_matches(
    elements: list[dict[str, Any]],
    pdf_by_page: dict[int, list[dict[str, Any]]],
    selected_pages: set[int],
    page_infos: dict[int, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    threshold = 58.0
    for page in sorted(selected_pages):
        page_elements: list[tuple[int, dict[str, Any], str]] = []
        for index, element in enumerate(elements):
            kind = str(element.get("type") or "")
            if kind not in TEXT_TYPES:
                continue
            if _element_page_hint(element, selected_pages) != page:
                continue
            norm = normalize_text(_markdown_text(element))
            if len(norm) < 6:
                continue
            page_elements.append((index, element, norm))
        rows = sorted(pdf_by_page.get(page, []), key=_reading_order_key)
        if not page_elements or not rows:
            continue

        m, n = len(page_elements), len(rows)
        scores = [[0.0 for _ in range(n)] for _ in range(m)]
        for i, (_, _element, norm) in enumerate(page_elements):
            position_window = _markdown_position_window(_element, page_infos.get(page))
            for j, row in enumerate(rows):
                if position_window and not _row_inside_position_window(row, position_window):
                    scores[i][j] = 0.0
                    continue
                score = _score(norm, str(row.get("normalized") or ""))
                if position_window:
                    score = min(100.0, score + 4.0)
                scores[i][j] = score

        dp = [[0.0 for _ in range(n + 1)] for _ in range(m + 1)]
        action: list[list[tuple[str, int, int] | None]] = [[None for _ in range(n + 1)] for _ in range(m + 1)]
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                best = dp[i - 1][j]
                act: tuple[str, int, int] = ("skip-element", i - 1, j)
                if dp[i][j - 1] > best:
                    best = dp[i][j - 1]
                    act = ("skip-pdf", i, j - 1)
                score = scores[i - 1][j - 1]
                if score >= threshold and dp[i - 1][j - 1] + score > best:
                    best = dp[i - 1][j - 1] + score
                    act = ("match", i - 1, j - 1)
                dp[i][j] = best
                action[i][j] = act

        i, j = m, n
        while i > 0 and j > 0:
            act = action[i][j]
            if not act:
                break
            kind, prev_i, prev_j = act
            if kind == "match":
                _, element, _norm = page_elements[prev_i]
                record_id = str(element.get("id") or "")
                if record_id:
                    matches[record_id] = {
                        "score": scores[prev_i][prev_j],
                        "row": rows[prev_j],
                        "matchMode": "page-local-monotonic",
                        "elementIndex": prev_i,
                        "rowIndex": prev_j,
                    }
            i, j = prev_i, prev_j

        used_rows = {
            int(match.get("rowIndex"))
            for match in matches.values()
            if isinstance(match.get("rowIndex"), int)
        }
        matched_element_indexes = {
            int(match.get("elementIndex"))
            for match in matches.values()
            if isinstance(match.get("elementIndex"), int)
        }
        for element_index, (_global_index, element, norm) in enumerate(page_elements):
            if element_index in matched_element_indexes:
                continue
            previous_row = -1
            next_row = n
            for match in matches.values():
                match_element_index = match.get("elementIndex")
                match_row_index = match.get("rowIndex")
                if not isinstance(match_element_index, int) or not isinstance(match_row_index, int):
                    continue
                if match_element_index < element_index:
                    previous_row = max(previous_row, match_row_index)
                elif match_element_index > element_index:
                    next_row = min(next_row, match_row_index)
            position_window = _markdown_position_window(element, page_infos.get(page))
            best: tuple[float, int, dict[str, Any]] | None = None
            for row_index, row in enumerate(rows):
                if row_index in used_rows or not (previous_row < row_index < next_row):
                    continue
                if position_window and not _row_inside_position_window(row, position_window):
                    continue
                score = _score(norm, str(row.get("normalized") or ""))
                if best is None or score > best[0]:
                    best = (score, row_index, row)
            if best and best[0] >= 84.0:
                record_id = str(element.get("id") or "")
                if record_id:
                    matches[record_id] = {
                        "score": best[0],
                        "row": best[2],
                        "matchMode": "page-local-exact-recovery",
                        "elementIndex": element_index,
                        "rowIndex": best[1],
                    }
                    used_rows.add(best[1])
                    matched_element_indexes.add(element_index)
    used_row_keys = {
        (int((match.get("row") or {}).get("page") or 0), str((match.get("row") or {}).get("id") or ""))
        for match in matches.values()
    }
    for element_index, element in enumerate(elements):
        record_id = str(element.get("id") or "")
        if not record_id or record_id in matches:
            continue
        kind = str(element.get("type") or "")
        if kind not in TEXT_TYPES:
            continue
        page_window = _element_page_window(element, selected_pages)
        if len(page_window) < 2:
            continue
        norm = normalize_text(_markdown_text(element))
        if len(norm) < 6:
            continue
        best: tuple[float, dict[str, Any]] | None = None
        for page in page_window:
            for row in pdf_by_page.get(page, []) or []:
                row_key = (int(row.get("page") or 0), str(row.get("id") or ""))
                if row_key in used_row_keys:
                    continue
                score = _score(norm, str(row.get("normalized") or ""))
                if best is None or score > best[0]:
                    best = (score, row)
        if best and best[0] >= 82.0:
            row = best[1]
            matches[record_id] = {
                "score": best[0],
                "row": row,
                "matchMode": "markdown-page-window-recovery",
                "elementIndex": element_index,
                "rowIndex": None,
            }
            used_row_keys.add((int(row.get("page") or 0), str(row.get("id") or "")))
    return matches


def _best_rejected_match(
    element: dict[str, Any],
    pdf_rows: list[dict[str, Any]],
    page_hint: int | None,
) -> dict[str, Any] | None:
    kind = str(element.get("type") or "")
    if kind not in TEXT_TYPES:
        return None
    norm = normalize_text(_markdown_text(element))
    if len(norm) < 6:
        return None
    search_rows = [row for row in pdf_rows if page_hint is None or int(row.get("page") or 0) == page_hint]
    best: tuple[float, dict[str, Any]] | None = None
    for row in search_rows:
        score = _score(norm, str(row.get("normalized") or ""))
        if best is None or score > best[0]:
            best = (score, row)
    if not best:
        return None
    return {
        "score": round(max(0.0, best[0]), 2),
        "pdfPage": best[1].get("page"),
        "pdfRegion": best[1].get("id"),
        "pdfText": compact_text(str(best[1].get("text") or ""), 220),
    }


def _has_usable_page_hints(records: list[dict[str, Any]], selected_pages: set[int]) -> bool:
    hinted = 0
    in_scope = 0
    for item in records:
        page = item.get("page")
        if not isinstance(page, int):
            continue
        hinted += 1
        if page in selected_pages:
            in_scope += 1
    return hinted > 0 and in_scope >= 3


def _candidate_records(records: list[dict[str, Any]], selected_pages: set[int]) -> tuple[str, list[dict[str, Any]], str | None]:
    if _has_usable_page_hints(records, selected_pages):
        candidates: list[dict[str, Any]] = []
        previous_pages: list[int | None] = []
        last_page: int | None = None
        for item in records:
            page = item.get("page")
            if isinstance(page, int):
                last_page = page
            previous_pages.append(last_page)
        next_pages: list[int | None] = [None] * len(records)
        next_page: int | None = None
        for index in range(len(records) - 1, -1, -1):
            page = records[index].get("page")
            if isinstance(page, int):
                next_page = page
            next_pages[index] = next_page
        for index, item in enumerate(records):
            page = item.get("page")
            if page in selected_pages:
                row = dict(item)
                row["_spinePageSource"] = "markdown-page-hint"
                candidates.append(row)
                continue
            neighbours = [
                page_no for page_no in (previous_pages[index], next_pages[index])
                if page_no in selected_pages
            ]
            if not neighbours:
                continue
            row = dict(item)
            row["_spineNeighborPages"] = sorted(set(int(page_no) for page_no in neighbours))
            if len(set(neighbours)) == 1:
                row["_spineInferredPage"] = int(neighbours[0])
                row["_spinePageSource"] = "neighbor-same-page"
            else:
                row["_spinePageSource"] = "neighbor-page-window"
            candidates.append(row)
        return (
            "selected-markdown-page-hints",
            candidates,
            None,
        )
    return (
        "recovered-by-pdf-text-match",
        records,
        "Τα Markdown page hints δεν δείχνουν αξιόπιστα στο επιλεγμένο PDF εύρος. Το spine ανακτά στοιχεία με απευθείας text match στο PDF και δεν μπορεί ακόμη να αποδείξει όσα δεν έχουν κείμενο.",
    )


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    records = list((markdown_element_map or {}).get("records", []) or [])
    selected_pages = {int(page) for page in (pdf_analysis or {}).get("selected_pages", []) or []}
    pdf_rows = _pdf_regions(pdf_analysis)
    pdf_by_page: dict[int, list[dict[str, Any]]] = {}
    for row in pdf_rows:
        pdf_by_page.setdefault(int(row["page"]), []).append(row)
    page_infos = _page_infos(pdf_analysis)

    scope, candidates, warning = _candidate_records(records, selected_pages)
    monotonic_matches = _page_local_monotonic_matches(candidates, pdf_by_page, selected_pages, page_infos)
    items: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    match_mode_counts: Counter[str] = Counter()

    for element in candidates:
        kind = str(element.get("type") or "")
        if kind not in TEXT_TYPES and kind not in VISUAL_TYPES:
            continue
        text = _markdown_text(element)
        norm = normalize_text(text)
        original_page_hint = element.get("page") if isinstance(element.get("page"), int) else None
        inferred_page = element.get("_spineInferredPage") if isinstance(element.get("_spineInferredPage"), int) else None
        position_page_hint = _position_page_hint(element)
        page_hint = position_page_hint if position_page_hint in selected_pages else (original_page_hint if original_page_hint in selected_pages else inferred_page)
        record_id = str(element.get("id") or "")
        allocated = monotonic_matches.get(record_id)
        if allocated:
            score = float(allocated.get("score") or 0.0)
            pdf_match = allocated.get("row") or {}
            match_mode = allocated.get("matchMode")
            rejected = None
        else:
            score = 0.0
            position_window = _markdown_position_window(element, page_infos.get(int(page_hint or 0)))
            if position_window and page_hint in selected_pages:
                pdf_match = {
                    "page": page_hint,
                    "id": f"{record_id}-markdown-position",
                    "rowGranularity": "markdown-position",
                    "bbox": position_window.get("bbox"),
                    "text": "",
                }
                match_mode = "markdown-position-hint"
            else:
                pdf_match = {}
                match_mode = "page-local-monotonic-unplaced" if kind in TEXT_TYPES else "page-hint"
            rejected = _best_rejected_match(element, pdf_rows, page_hint)

        status = _status(score, kind) if allocated else (
            "page-hint" if kind in VISUAL_TYPES and page_hint in selected_pages
            else ("position-hint" if pdf_match and pdf_match.get("rowGranularity") == "markdown-position" else "unplaced")
        )
        item = {
            "id": element.get("id"),
            "orderIndex": element.get("orderIndex"),
            "type": kind,
            "markdownPageHint": original_page_hint,
            "markdownPosition": element.get("markdownPosition") or None,
            "markdownPositionWindow": _markdown_position_window(element, page_infos.get(int(page_hint or 0))),
            "inferredPage": inferred_page,
            "pageHintSource": element.get("_spinePageSource") or "none",
            "neighborPages": element.get("_spineNeighborPages") or [],
            "markdownPageConfidence": element.get("pageConfidence"),
            "markdownPageWindow": element.get("pageWindow") or [],
            "line": element.get("line"),
            "status": status,
            "manifestOutcome": (
                "pdf-witness-confirmed"
                if status in {"strong", "medium", "page-hint", "position-hint"}
                else ("pdf-witness-weak" if status == "weak" else "pdf-witness-unplaced")
            ),
            "requiresUserDecisionByItself": False,
            "score": round(max(0.0, score), 2) if allocated else None,
            "matchMode": match_mode,
            "text": compact_text(text, 420),
            "pdfPage": pdf_match.get("page") or (page_hint if page_hint in selected_pages else None),
            "pdfRegion": pdf_match.get("id"),
            "pdfParentRegion": pdf_match.get("parentRegion"),
            "pdfLineIndex": pdf_match.get("lineIndex"),
            "pdfRowGranularity": pdf_match.get("rowGranularity"),
            "bbox": pdf_match.get("bbox"),
            "pdfText": compact_text(str(pdf_match.get("text") or ""), 420) if pdf_match else "",
            "bestRejectedMatch": rejected,
            "docxEvidence": element.get("docxEvidence") or None,
        }
        items.append(item)
        status_counts[status] += 1
        type_counts[kind] += 1
        match_mode_counts[str(match_mode or "none")] += 1

    placed = sum(status_counts.get(key, 0) for key in ("strong", "medium", "page-hint", "position-hint"))
    total = len(items)
    return {
        "version": "markdown-pdf-spine-0.2",
        "truthModel": "markdown-first/pdf-guided/docx-secondary",
        "manifestPolicy": "Every selected Markdown candidate is retained in the spine. Weak or unplaced PDF witness is diagnostic only and does not become a user decision unless output/content survival also fails.",
        "scope": scope,
        "scopeWarning": warning,
        "selectedPages": sorted(selected_pages),
        "markdownRecordCount": len(records),
        "candidateCount": len(candidates),
        "itemCount": total,
        "placedCount": placed,
        "weakCount": int(status_counts.get("weak", 0)),
        "unplacedCount": int(status_counts.get("unplaced", 0)),
        "coverage": round(placed / total, 5) if total else 1.0,
        "statusCounts": dict(status_counts),
        "typeCounts": dict(type_counts),
        "matchModeCounts": dict(match_mode_counts),
        "items": items,
    }
