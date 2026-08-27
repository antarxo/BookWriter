from __future__ import annotations

import re
from pathlib import Path
from typing import Any


VERSION = "mathpix-mmd-block-refinement-0.1"

# These are semantic block starts, not page-specific exceptions. They cover the
# common question/answer notation emitted by Mathpix in educational material.
_BLOCK_START_RE = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)+(?:\.)?\s+"          # 2.3. ...
    r"|(?:i{1,3}|iv|v|vi{0,3}|ix|x)\.\s+" # i. / ii. ...
    r"|[A-DΑ-ΔА-ГГ△]\)\s*"                 # A) / Α) / Г) / △)
    r"|\\\(\\Delta\)?\s*\)?"            # \(\Delta) ...
    r"|\[[^\]]*(?:ΕΞΕΤΑΣ|EΞETAΣ)[^\]]*\]" # [ΕΞΕΤΑΣΕΙΣ ...]
    r")",
    re.IGNORECASE,
)


def _normalize(value: Any) -> str:
    text = str(value or "")
    text = text.replace("Г", "Γ").replace("г", "γ")
    text = text.replace("△", "Δ")
    text = text.replace("\\Delta", "Δ").replace("\\delta", "δ")
    text = re.sub(r"\\begin\{[^{}]+\}|\\end\{[^{}]+\}", " ", text)
    text = re.sub(r"\\item(?:\[[^\]]*\])?", " ", text)
    text = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol|emph|textbf|textit)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+", " ", text)
    text = re.sub(r"[{}$|&]", " ", text)
    return re.sub(r"[^0-9A-Za-zΑ-Ωα-ωΆ-Ώά-ώ]+", "", text.casefold())


def _record_text(record: dict[str, Any]) -> str:
    for value in (record.get("text_display"), record.get("text")):
        norm = _normalize(value)
        if norm:
            return norm
    raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
    for key in ("text_display", "text", "latex", "value"):
        norm = _normalize(raw.get(key))
        if norm:
            return norm
    return ""


def _reading_key(record: dict[str, Any]) -> tuple[float, float, int]:
    box = record.get("bbox_pt") if isinstance(record.get("bbox_pt"), dict) else {}
    return (
        float(box.get("y0") or 0.0),
        float(box.get("x0") or 0.0),
        int(record.get("line") or 0),
    )


def _line_records_by_page(page_structure: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        line_page = page.get("mathpixLinePageMap") if isinstance(page.get("mathpixLinePageMap"), dict) else {}
        records = [
            record for record in (line_page.get("objects", []) or [])
            if str(record.get("type") or "").lower() in {"text", "paragraph", "list_item", "section_header", "heading"}
            and _record_text(record)
        ]
        records.sort(key=_reading_key)
        if page_no:
            result[page_no] = records
    return result


def _unique_page_for_text(text: str, records_by_page: dict[int, list[dict[str, Any]]], max_span: int = 8) -> int | None:
    target = _normalize(text)
    if not target:
        return None
    pages: list[int] = []
    for page_no, records in records_by_page.items():
        for start in range(len(records)):
            joined = ""
            for index in range(start, min(len(records), start + max_span)):
                piece = _record_text(records[index])
                if not piece:
                    break
                joined += piece
                if joined == target:
                    pages.append(page_no)
                    break
                if len(joined) >= len(target) or not target.startswith(joined):
                    break
            if pages and pages[-1] == page_no:
                break
        if len(set(pages)) > 1:
            return None
    unique = sorted(set(pages))
    return unique[0] if len(unique) == 1 else None


def _split_logical_lines(raw: str) -> list[tuple[int, int, str]]:
    lines = list(re.finditer(r"[^\r\n]+(?:\r?\n|$)", raw))
    chunks: list[tuple[int, int, str]] = []
    current_start: int | None = None
    current_end = 0
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_start, current_end, current_lines
        if current_start is None:
            return
        text = " ".join(line.strip() for line in current_lines if line.strip()).strip()
        if text:
            chunks.append((current_start, current_end, text))
        current_start = None
        current_end = 0
        current_lines = []

    for match in lines:
        line = match.group(0).rstrip("\r\n")
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        starts = bool(_BLOCK_START_RE.match(stripped))
        if current_start is None:
            current_start = match.start()
        elif starts:
            flush()
            current_start = match.start()
        current_lines.append(line)
        current_end = match.end()
    flush()
    return chunks


def refine_markdown_element_map(
    markdown_element_map: dict[str, Any],
    mmd_path: Path,
    page_structure: dict[str, Any],
    *,
    min_large_block_lines: int = 8,
) -> dict[str, Any]:
    """Split only oversized paragraph records and page-bind exact child blocks.

    Normal Markdown records remain unchanged. Refinement is triggered only for a
    paragraph whose original mapper grouped many consecutive nonblank MMD lines.
    Child page hints come only from unique exact Mathpix-lines identity.
    """
    source_text = Path(mmd_path).read_text(encoding="utf-8", errors="replace")
    records_by_page = _line_records_by_page(page_structure)
    original = list(markdown_element_map.get("records", []) or [])
    output: list[dict[str, Any]] = []
    split_parent_count = 0
    child_count = 0
    page_bound_count = 0
    audit_rows: list[dict[str, Any]] = []

    for record in original:
        if str(record.get("type") or "") != "paragraph" or int(record.get("lineCount") or 0) < min_large_block_lines:
            output.append(dict(record))
            continue
        try:
            start = int(record.get("offset"))
            end = int(record.get("endOffset"))
        except (TypeError, ValueError):
            output.append(dict(record))
            continue
        raw = source_text[start:end]
        chunks = _split_logical_lines(raw)
        if len(chunks) <= 1:
            output.append(dict(record))
            continue

        split_parent_count += 1
        child_rows: list[dict[str, Any]] = []
        for local_start, local_end, text in chunks:
            absolute_start = start + local_start
            absolute_end = start + local_end
            page = _unique_page_for_text(text, records_by_page)
            child = dict(record)
            child.update({
                "offset": absolute_start,
                "endOffset": absolute_end,
                "line": source_text.count("\n", 0, absolute_start) + 1,
                "lineCount": max(1, text.count("\n") + 1),
                "text": text,
                "textPreview": text[:240],
                "refinedFromOversizedParagraph": True,
                "refinementSourceParentId": record.get("id"),
            })
            if page is not None:
                child["page"] = page
                child["pageConfidence"] = "high"
                child["pageWindow"] = [page, page]
                child["mathpixLinesPageIdentity"] = page
                page_bound_count += 1
            child_rows.append(child)
            output.append(child)
            child_count += 1
        audit_rows.append({
            "parentId": record.get("id"),
            "originalLineCount": record.get("lineCount"),
            "childCount": len(child_rows),
            "pageBoundChildCount": sum(1 for child in child_rows if child.get("mathpixLinesPageIdentity")),
        })

    # Renumber after splitting so downstream order/id assumptions stay simple.
    for index, record in enumerate(output):
        record["id"] = f"mdel-{index + 1:05d}"
        record["orderIndex"] = index

    type_counts: dict[str, int] = {}
    for record in output:
        kind = str(record.get("type") or "")
        type_counts[kind] = type_counts.get(kind, 0) + 1

    markdown_element_map["records"] = output
    markdown_element_map["count"] = len(output)
    markdown_element_map["typeCounts"] = type_counts
    markdown_element_map["mmdBlockRefinement"] = {
        "version": VERSION,
        "splitParentCount": split_parent_count,
        "refinedChildCount": child_count,
        "pageBoundChildCount": page_bound_count,
        "policy": "split oversized paragraph blocks by logical educational block starts; assign child page only by unique exact Mathpix-lines identity; no PDF/fuzzy/page exceptions",
        "parents": audit_rows,
    }
    return markdown_element_map


__all__ = ["refine_markdown_element_map"]
