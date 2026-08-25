from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from typing import Any

IMG_PAGE_RE = re.compile(
    r"-(\d{3})(?=_[0-9]+_[0-9]+_[0-9]+_[0-9]+\.(?:jpg|jpeg|png|webp)\b)|"
    r"-(\d{3})(?=\.(?:jpg|jpeg|png|webp)\b)",
    re.I,
)
IMG_GEOMETRY_RE = re.compile(r"-(\d{3})_(\d+)_(\d+)_(\d+)_(\d+)\.(?:jpg|jpeg|png|webp)\b", re.I)
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+['\"][^)]*['\"])?\)", re.S)
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc=['\"]([^'\"]+)['\"][^>]*>", re.I)
INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}", re.S)
DISPLAY_PATTERNS = [
    ("display_equation", re.compile(r"\$\$(.+?)\$\$", re.S)),
    ("display_equation", re.compile(r"\\\[(.+?)\\\]", re.S)),
    ("display_equation", re.compile(r"\\begin\{(?:equation\*?|aligned|align\*?|gather\*?)\}(.+?)\\end\{(?:equation\*?|aligned|align\*?|gather\*?)\}", re.S)),
]
ENVIRONMENT_PATTERNS = [
    ("figure", re.compile(r"\\begin\{figure\}(.+?)\\end\{figure\}", re.S)),
    ("latex_list", re.compile(r"\\begin\{(itemize|enumerate)\}(.+?)\\end\{\1\}", re.S)),
    ("latex_table", re.compile(r"\\begin\{tabular\}(?:\[[^\]]*\])?\{[^{}]*\}(.+?)\\end\{tabular\}", re.S)),
    ("latex_table", re.compile(r"\\begin\{tabular\}(?:\[[^\]]*\])?(.+?)\\end\{tabular\}", re.S)),
]
BRACED_COMMAND_TYPES = {
    "section": "heading",
    "section*": "heading",
    "subsection": "heading",
    "subsection*": "heading",
    "subsubsection": "heading",
    "subsubsection*": "heading",
    "title": "title",
    "author": "author",
    "caption": "caption",
}


def _plain_latex(latex: str) -> str:
    text = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol)\s*\{([^{}]*)\}", r"\1", latex)
    text = re.sub(r"\\(?:left|right|displaystyle|quad|qquad|,|;|!)", "", text)
    text = re.sub(r"\\(?:frac)\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", text)
    text = re.sub(r"\\([A-Za-z]+)", r"\1", text)
    text = re.sub(r"[{}\s]", "", text)
    return text.casefold()


def _strip_latex_markup(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\\(?:section\*?|subsection\*?|subsubsection\*?|title|author|caption)\s*\{", "", text)
    text = text.replace("\\\\", " ")
    text = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol|emph|textbf|textit)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+", "", text)
    text = re.sub(r"[{}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _match_text(value: str) -> str:
    text = _strip_latex_markup(value)
    text = re.sub(r"[^0-9a-zα-ωά-ώ]+", " ", text.casefold())
    return re.sub(r"\s+", " ", text).strip()


def _find_matching_brace(text: str, open_index: int) -> int | None:
    depth = 0
    escaped = False
    for index in range(open_index, len(text)):
        ch = text[index]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _braced_command_spans(markdown: str) -> list[tuple[int, int, str, dict[str, Any]]]:
    spans: list[tuple[int, int, str, dict[str, Any]]] = []
    command_re = re.compile(r"\\(section\*?|subsection\*?|subsubsection\*?|title|author|caption)\s*\{")
    for match in command_re.finditer(markdown):
        end = _find_matching_brace(markdown, match.end() - 1)
        if end is None:
            continue
        command = match.group(1)
        body = markdown[match.end():end]
        kind = BRACED_COMMAND_TYPES.get(command, "paragraph")
        payload: dict[str, Any] = {
            "command": command,
            "text": _strip_latex_markup(body),
        }
        if kind == "heading":
            payload["level"] = 1 if command.startswith("section") else 2
        spans.append((match.start(), end + 1, kind, payload))
    return spans


def _environment_payload(kind: str, body: str, match: re.Match[str]) -> dict[str, Any]:
    if kind == "figure":
        images = [item.strip() for item in INCLUDEGRAPHICS_RE.findall(body)]
        captions = []
        for caption in _braced_command_spans(body):
            if caption[2] == "caption":
                captions.append(str(caption[3].get("text") or ""))
        return {
            "imageTargets": images,
            "captionText": " ".join(c for c in captions if c).strip(),
            "imageCount": len(images),
        }
    if kind == "latex_list":
        env_name = match.group(1)
        list_body = match.group(2)
        return {
            "environment": env_name,
            "itemCount": len(re.findall(r"\\item(?:\[[^\]]*\])?", list_body)),
        }
    if kind == "latex_table":
        return {
            "environment": "tabular",
            "rowCount": len(re.findall(r"\\\\", body)) + (1 if "\\hline" in body or "&" in body else 0),
        }
    return {}


def _page_hint(text: str, offset: int, anchors: list[tuple[int, int]]) -> dict[str, Any]:
    before = [anchor for anchor in anchors if anchor[0] <= offset]
    after = [anchor for anchor in anchors if anchor[0] > offset]
    page = None
    confidence = "none"
    if before and after and before[-1][1] == after[0][1]:
        page = before[-1][1]
        confidence = "high"
    elif before and after and before[-1][1] < after[0][1]:
        start_offset, start_page = before[-1]
        end_offset, end_page = after[0]
        span = max(1, end_offset - start_offset)
        ratio = max(0.0, min(1.0, (offset - start_offset) / span))
        page = int(round(start_page + ratio * (end_page - start_page)))
        page = max(start_page, min(end_page, page))
        confidence = "interpolated"
        return {"page": page, "pageConfidence": confidence, "pageWindow": [start_page, end_page]}
    elif before and (not after or offset - before[-1][0] <= after[0][0] - offset):
        page = before[-1][1]
        confidence = "medium"
    elif after:
        page = after[0][1]
        confidence = "medium"
    return {"page": page, "pageConfidence": confidence}


def _image_page_anchors(markdown: str) -> list[tuple[int, int]]:
    anchors: list[tuple[int, int]] = []
    for match in IMG_PAGE_RE.finditer(markdown):
        value = match.group(1) or match.group(2)
        if value:
            anchors.append((match.start(), int(value)))
    return anchors


def _parse_image_geometry(target: str) -> dict[str, Any] | None:
    value = str(target or "")
    match = IMG_GEOMETRY_RE.search(value)
    if not match:
        parsed = urlparse(value)
        query = parse_qs(parsed.query)
        page_match = re.search(r"-(\d{1,3})(?:\.(?:jpg|jpeg|png|webp)\b)", parsed.path, re.I)
        try:
            page = int(page_match.group(1)) if page_match else None
            height = int((query.get("height") or [""])[0])
            width = int((query.get("width") or [""])[0])
            top = int((query.get("top_left_y") or [""])[0])
            left = int((query.get("top_left_x") or [""])[0])
        except (TypeError, ValueError, AttributeError):
            return None
        if page is None:
            return None
        return {
            "page": page,
            "bboxPx": [left, top, left + width, top + height],
            "sourceGeometry": "mathpix-cdn-query-geometry",
        }
    page, height, width, top, left = map(int, match.groups())
    return {
        "page": page,
        "bboxPx": [left, top, left + width, top + height],
        "sourceGeometry": "mathpix-image-filename",
    }


def _image_position_anchors(markdown: str) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    patterns = [
        ("markdown-image", IMAGE_RE),
        ("html-image", HTML_IMAGE_RE),
        ("includegraphics", INCLUDEGRAPHICS_RE),
    ]
    for syntax, pattern in patterns:
        for match in pattern.finditer(markdown):
            if syntax == "markdown-image":
                target = match.group(2)
            else:
                target = match.group(1)
            geometry = _parse_image_geometry(target)
            if not geometry:
                continue
            anchors.append({
                "offset": match.start(),
                "line": _line_number(markdown, match.start()),
                "syntax": syntax,
                "target": target,
                **geometry,
            })
    anchors.sort(key=lambda item: int(item.get("offset") or 0))
    return anchors


def _anchor_summary(anchor: dict[str, Any] | None) -> dict[str, Any] | None:
    if not anchor:
        return None
    return {
        "page": anchor.get("page"),
        "line": anchor.get("line"),
        "offset": anchor.get("offset"),
        "bboxPx": anchor.get("bboxPx"),
        "target": anchor.get("target"),
        "syntax": anchor.get("syntax"),
    }


def _position_hint(
    start: int,
    end: int,
    anchors: list[dict[str, Any]],
    page_hint: dict[str, Any],
) -> dict[str, Any] | None:
    if not anchors:
        return None
    inside = [anchor for anchor in anchors if start <= int(anchor.get("offset") or -1) < end]
    if inside:
        anchor = inside[0]
        return {
            "kind": "image-anchor",
            "page": anchor.get("page"),
            "confidence": "high",
            "bboxPx": anchor.get("bboxPx"),
            "anchor": _anchor_summary(anchor),
        }
    before = [anchor for anchor in anchors if int(anchor.get("offset") or -1) < start]
    after = [anchor for anchor in anchors if int(anchor.get("offset") or -1) >= end]
    previous = before[-1] if before else None
    next_anchor = after[0] if after else None
    if previous and next_anchor and previous.get("page") == next_anchor.get("page"):
        return {
            "kind": "between-image-anchors",
            "page": previous.get("page"),
            "confidence": "medium",
            "before": _anchor_summary(previous),
            "after": _anchor_summary(next_anchor),
        }
    hinted_page = page_hint.get("page")
    if isinstance(hinted_page, int):
        if previous and previous.get("page") == hinted_page:
            return {
                "kind": "after-image-anchor",
                "page": previous.get("page"),
                "confidence": "low",
                "before": _anchor_summary(previous),
            }
        if next_anchor and next_anchor.get("page") == hinted_page:
            return {
                "kind": "before-image-anchor",
                "page": next_anchor.get("page"),
                "confidence": "low",
                "after": _anchor_summary(next_anchor),
            }
    if previous:
        return {
            "kind": "after-image-anchor",
            "page": previous.get("page"),
            "confidence": "low",
            "before": _anchor_summary(previous),
        }
    if next_anchor:
        return {
            "kind": "before-image-anchor",
            "page": next_anchor.get("page"),
            "confidence": "low",
            "after": _anchor_summary(next_anchor),
        }
    return None


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _add_element(
    elements: list[dict[str, Any]],
    source: Path,
    markdown: str,
    anchors: list[tuple[int, int]],
    position_anchors: list[dict[str, Any]],
    kind: str,
    start: int,
    end: int,
    payload: dict[str, Any] | None = None,
) -> None:
    raw = markdown[start:end]
    hint = _page_hint(markdown, start, anchors)
    item: dict[str, Any] = {
        "id": f"mdel-{len(elements)+1:05d}",
        "source": str(source),
        "orderIndex": len(elements),
        "type": kind,
        "offset": start,
        "endOffset": end,
        "line": _line_number(markdown, start),
        "page": hint["page"],
        "pageConfidence": hint["pageConfidence"],
    }
    if hint.get("pageWindow"):
        item["pageWindow"] = hint.get("pageWindow")
    if payload:
        item.update(payload)
    position = _position_hint(start, end, position_anchors, hint)
    if position:
        item["markdownPosition"] = position
    text = re.sub(r"\s+", " ", raw).strip()
    if text:
        item["textPreview"] = text[:240]
    elements.append(item)


def _atomic_spans(markdown: str) -> list[tuple[int, int, str, dict[str, Any]]]:
    spans: list[tuple[int, int, str, dict[str, Any]]] = []
    for kind, pattern in ENVIRONMENT_PATTERNS:
        for match in pattern.finditer(markdown):
            body = match.group(match.lastindex or 1)
            spans.append((match.start(), match.end(), kind, _environment_payload(kind, body, match)))
    spans.extend(_braced_command_spans(markdown))
    for kind, pattern in DISPLAY_PATTERNS:
        for match in pattern.finditer(markdown):
            latex = match.group(1).strip()
            if not latex or len(latex) > 4000:
                continue
            spans.append((match.start(), match.end(), kind, {"latex": latex, "signature": _plain_latex(latex)}))
    for match in IMAGE_RE.finditer(markdown):
        spans.append((match.start(), match.end(), "image", {"alt": match.group(1).strip(), "target": match.group(2).strip()}))
    for match in HTML_IMAGE_RE.finditer(markdown):
        spans.append((match.start(), match.end(), "image", {"target": match.group(1).strip(), "syntax": "html"}))
    spans.sort(key=lambda row: (row[0], row[1]))
    accepted: list[tuple[int, int, str, dict[str, Any]]] = []
    last_end = -1
    for span in spans:
        if span[0] < last_end:
            continue
        accepted.append(span)
        last_end = span[1]
    return accepted


def _block_type(lines: list[str]) -> str:
    if not lines:
        return "paragraph"
    first = lines[0].strip()
    if re.match(r"#{1,6}\s+", first):
        return "heading"
    if re.match(r"(?:[-*+]\s+|\d+[.)]\s+)", first):
        return "list"
    if len(lines) >= 2 and "|" in lines[0] and re.match(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", lines[1]):
        return "table"
    return "paragraph"


def extract_markdown_element_map(
    markdown_files: list[Path],
    out_path: Path,
    docx_path: Path | None = None,
    attach_docx_evidence: bool = False,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    for md in markdown_files:
        markdown = Path(md).read_text(encoding="utf-8", errors="replace")
        anchors = _image_page_anchors(markdown)
        position_anchors = _image_position_anchors(markdown)
        atomics = _atomic_spans(markdown)
        cursor = 0
        for start, end, kind, payload in atomics:
            _parse_markdown_blocks(md, markdown, anchors, position_anchors, cursor, start, elements)
            _add_element(elements, md, markdown, anchors, position_anchors, kind, start, end, payload)
            cursor = end
        _parse_markdown_blocks(md, markdown, anchors, position_anchors, cursor, len(markdown), elements)
    result = {
        "version": "markdown-element-map-0.2",
        "classification": "markdown blocks + LaTeX section/title/author/caption commands + figure/list/table environments + display equations + images",
        "count": len(elements),
        "typeCounts": _type_counts(elements),
        "markdownPositionSummary": _markdown_position_summary(elements),
        "records": elements,
    }
    if attach_docx_evidence:
        _attach_docx_evidence(result, docx_path)
    else:
        result["docxEvidenceSummary"] = {
            "available": False,
            "status": "disabled-maps-first",
            "source": str(docx_path) if docx_path else None,
            "reason": "DOCX evidence is inventoried in docx_donor_map instead of being embedded into Markdown records.",
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _parse_markdown_blocks(
    source: Path,
    markdown: str,
    anchors: list[tuple[int, int]],
    position_anchors: list[dict[str, Any]],
    start: int,
    end: int,
    elements: list[dict[str, Any]],
) -> None:
    segment = markdown[start:end]
    block_start: int | None = None
    block_lines: list[str] = []
    offset = start
    for raw_line in segment.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        if line.strip():
            if block_start is None:
                block_start = offset
            block_lines.append(line)
        elif block_start is not None:
            _add_block(source, markdown, anchors, position_anchors, block_start, offset, block_lines, elements)
            block_start = None
            block_lines = []
        offset += len(raw_line)
    if block_start is not None:
        _add_block(source, markdown, anchors, position_anchors, block_start, end, block_lines, elements)


def _add_block(
    source: Path,
    markdown: str,
    anchors: list[tuple[int, int]],
    position_anchors: list[dict[str, Any]],
    start: int,
    end: int,
    lines: list[str],
    elements: list[dict[str, Any]],
) -> None:
    kind = _block_type(lines)
    payload: dict[str, Any] = {"lineCount": len(lines)}
    if kind == "heading":
        match = re.match(r"\s*(#{1,6})\s+(.*)$", lines[0])
        if match:
            payload["level"] = len(match.group(1))
            payload["text"] = match.group(2).strip()
    elif kind == "list":
        payload["itemCount"] = sum(1 for line in lines if re.match(r"\s*(?:[-*+]\s+|\d+[.)]\s+)", line))
    elif kind == "table":
        payload["rowCount"] = len(lines)
    _add_element(elements, source, markdown, anchors, position_anchors, kind, start, end, payload)


def _type_counts(elements: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in elements:
        kind = str(item.get("type") or "")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _markdown_position_summary(elements: list[dict[str, Any]]) -> dict[str, Any]:
    kind_counts: dict[str, int] = {}
    page_counts: dict[str, int] = {}
    positioned = 0
    for item in elements:
        position = item.get("markdownPosition")
        if not isinstance(position, dict):
            continue
        positioned += 1
        kind = str(position.get("kind") or "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        page = position.get("page")
        if isinstance(page, int):
            key = str(page)
            page_counts[key] = page_counts.get(key, 0) + 1
    return {
        "positionedElementCount": positioned,
        "positionedElementRatio": round(positioned / len(elements), 5) if elements else 1.0,
        "kindCounts": kind_counts,
        "pageCounts": page_counts,
        "source": "Mathpix image filename geometry propagated to neighboring Markdown elements",
    }


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 12 and shorter in longer:
        return min(98.0, 72.0 + (len(shorter) / max(1, len(longer))) * 26.0)
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return 0.0
    overlap = len(a_tokens & b_tokens)
    if not overlap:
        return 0.0
    coverage = overlap / max(1, min(len(a_tokens), len(b_tokens)))
    balance = (2 * overlap) / max(1, len(a_tokens) + len(b_tokens))
    return (coverage * 70.0) + (balance * 30.0)


def _docx_evidence_records(docx_path: Path) -> dict[str, Any]:
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(docx_path)
    paragraphs: list[dict[str, Any]] = []
    styles: dict[str, int] = {}
    for index, paragraph in enumerate(doc.paragraphs):
        xml = paragraph._p.xml
        omml_count = xml.count("<m:oMath")
        drawing_count = xml.count("<w:drawing") + xml.count("<w:pict")
        omml_text = " ".join(str(node.text or "") for node in paragraph._p.iter() if node.tag == qn("m:t"))
        style = paragraph.style.name if paragraph.style else ""
        styles[style or "(none)"] = styles.get(style or "(none)", 0) + 1
        paragraphs.append({
            "id": f"docx-p{index:05d}",
            "index": index,
            "style": style,
            "text": paragraph.text,
            "matchText": _match_text(paragraph.text),
            "ommlText": omml_text,
            "ommlMatchText": _match_text(omml_text),
            "ommlCount": omml_count,
            "drawingCount": drawing_count,
        })
    return {
        "source": str(docx_path),
        "paragraphCount": len(paragraphs),
        "inlineShapeCount": len(doc.inline_shapes),
        "styles": styles,
        "paragraphs": paragraphs,
    }


def _docx_suggested_type(record: dict[str, Any], element_type: str = "") -> str:
    style = str(record.get("style") or "").casefold()
    has_text = bool(record.get("matchText"))
    if record.get("ommlCount") and (element_type == "display_equation" or not has_text):
        return "display_equation"
    if record.get("drawingCount") and (element_type in {"image", "figure"} or not has_text):
        return "figure"
    if "heading" in style or "επικεφα" in style:
        return "heading"
    if "title" in style or "τίτλ" in style:
        return "title"
    return "paragraph"


def _element_match_text(element: dict[str, Any]) -> str:
    if element.get("text"):
        return _match_text(str(element.get("text") or ""))
    if element.get("latex"):
        return _match_text(str(element.get("latex") or ""))
    if element.get("captionText"):
        return _match_text(str(element.get("captionText") or ""))
    return _match_text(str(element.get("textPreview") or ""))


def _docx_candidate_window(
    element: dict[str, Any],
    text_candidates: list[dict[str, Any]],
    math_candidates: list[dict[str, Any]],
    total_elements: int,
) -> list[dict[str, Any]]:
    if element.get("type") == "display_equation" and math_candidates:
        return math_candidates
    if not text_candidates:
        return []
    order_index = int(element.get("orderIndex") or 0)
    ratio = order_index / max(1, total_elements - 1)
    center = min(len(text_candidates) - 1, max(0, round(ratio * (len(text_candidates) - 1))))
    radius = 180
    start = max(0, center - radius)
    end = min(len(text_candidates), center + radius + 1)
    candidates = text_candidates[start:end]
    kind = str(element.get("type") or "")
    if kind in {"heading", "title"}:
        styled = [item for item in text_candidates if _docx_suggested_type(item) in {"heading", "title"}]
        seen = {id(item) for item in candidates}
        candidates.extend(item for item in styled if id(item) not in seen)
    return candidates


def _docx_agrees_with_markdown(element_type: str, suggested: str) -> bool:
    if suggested == element_type:
        return True
    if suggested == "paragraph" and element_type in {"latex_list", "caption", "author", "list"}:
        return True
    if suggested == "figure" and element_type in {"image", "figure"}:
        return True
    return False


def _attach_docx_evidence(result: dict[str, Any], docx_path: Path | None) -> None:
    if not docx_path:
        return
    try:
        evidence = _docx_evidence_records(Path(docx_path))
    except Exception as exc:
        result["docxEvidenceSummary"] = {
            "available": False,
            "source": str(docx_path),
            "error": str(exc),
        }
        return
    paragraphs = evidence["paragraphs"]
    text_candidates = [item for item in paragraphs if item.get("matchText")]
    math_candidates = [item for item in paragraphs if item.get("ommlMatchText") or item.get("ommlCount")]
    records = result.get("records", [])
    total_elements = len(records)
    matched = 0
    auto_accepted = 0
    needs_confirmation = 0
    suggested_counts: dict[str, int] = {}
    conflict_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for element in records:
        element_text = _element_match_text(element)
        if not element_text:
            continue
        best: tuple[float, dict[str, Any]] | None = None
        for paragraph in _docx_candidate_window(element, text_candidates, math_candidates, total_elements):
            target = paragraph.get("ommlMatchText") if element.get("type") == "display_equation" and paragraph.get("ommlMatchText") else paragraph.get("matchText")
            if not target:
                continue
            score = _similarity(element_text, str(target))
            if best is None or score > best[0]:
                best = (score, paragraph)
        if not best or best[0] < 62.0:
            continue
        record = best[1]
        element_type = str(element.get("type") or "")
        suggested = _docx_suggested_type(record, element_type)
        suggested_counts[suggested] = suggested_counts.get(suggested, 0) + 1
        agrees = _docx_agrees_with_markdown(element_type, suggested)
        strong = best[0] >= 74.0
        if agrees and strong:
            status = "autoAccepted"
        elif suggested == "paragraph":
            status = "docxSecondaryEvidence"
        elif not strong:
            status = "weakDocxEvidence"
        else:
            status = "needsUserConfirmation"
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "autoAccepted":
            auto_accepted += 1
        elif status == "needsUserConfirmation":
            needs_confirmation += 1
            conflict_key = f"{element_type or '(none)'}->{suggested or '(none)'}"
            conflict_counts[conflict_key] = conflict_counts.get(conflict_key, 0) + 1
        element["docxEvidence"] = {
            "matched": True,
            "paragraphId": record.get("id"),
            "paragraphIndex": record.get("index"),
            "score": round(best[0], 2),
            "style": record.get("style"),
            "ommlCount": record.get("ommlCount"),
            "drawingCount": record.get("drawingCount"),
            "suggestedType": suggested,
            "agreesWithMarkdownType": agrees,
            "status": status,
            "needsUserConfirmation": status == "needsUserConfirmation",
        }
        matched += 1
    result["docxEvidenceSummary"] = {
        "available": True,
        "source": evidence.get("source"),
        "paragraphCount": evidence.get("paragraphCount"),
        "inlineShapeCount": evidence.get("inlineShapeCount"),
        "matchedElementCount": matched,
        "autoAcceptedCount": auto_accepted,
        "needsUserConfirmationCount": needs_confirmation,
        "statusCounts": status_counts,
        "suggestedTypeCounts": suggested_counts,
        "conflictCounts": conflict_counts,
    }
