from __future__ import annotations

import re
from pathlib import Path
from typing import Any


VERSION = "mathpix-mmd-block-refinement-0.2"

_BLOCK_START_RE = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)+(?:\.)?\s+"
    r"|(?:i{1,3}|iv|v|vi{0,3}|ix|x)\.\s+"
    r"|[A-DΑ-ΔА-ГГ△]\)\s*"
    r"|\\\(\\Delta\)?\s*\)?"
    r"|\[[^\]]*(?:ΕΞΕΤΑΣ|EΞETAΣ)[^\]]*\]"
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
    text = re.sub(
        r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol|emph|textbf|textit)\s*\{([^{}]*)\}",
        r"\1",
        text,
    )
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
            record
            for record in (line_page.get("objects", []) or [])
            if str(record.get("type") or "").lower()
            in {"text", "paragraph", "list_item", "section_header", "heading"}
            and _record_text(record)
        ]
        records.sort(key=_reading_key)
        if page_no:
            result[page_no] = records
    return result


def _unique_page_for_text(
    text: str,
    records_by_page: dict[int, list[dict[str, Any]]],
    max_span: int = 8,
) -> int | None:
    target = _normalize(text)
    if not target:
        return None
    matches: list[int] = []
    for page_no, records in records_by_page.items():
        found = False
        for start in range(len(records)):
            joined = ""
            for index in range(start, min(len(records), start + max_span)):
                piece = _record_text(records[index])
                if not piece:
                    break
                joined += piece
                if joined == target:
                    matches.append(page_no)
                    found = True
                    break
                if len(joined) >= len(target) or not target.startswith(joined):
                    break
            if found:
                break
        if len(set(matches)) > 1:
            return None
    unique = sorted(set(matches))
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
    """Split an oversized MMD paragraph only after cross-page identity is proven.

    The proposed logical chunks are first matched against Mathpix lines. A parent
    is replaced only when at least two child chunks have unique exact identities
    on at least two distinct pages. Otherwise the original Markdown record is
    preserved byte-for-byte. No PDF/fuzzy/page-specific exception is used.
    """
    source_text = Path(mmd_path).read_text(encoding="utf-8", errors="replace")
    records_by_page = _line_records_by_page(page_structure)
    original = list(markdown_element_map.get("records", []) or [])
    output: list[dict[str, Any]] = []
    split_parent_count = 0
    child_count = 0
    page_bound_count = 0
    rejected_parent_count = 0
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

        proposed: list[tuple[int, int, str, int | None]] = []
        for local_start, local_end, text in chunks:
            proposed.append(
                (local_start, local_end, text, _unique_page_for_text(text, records_by_page))
            )

        bound_pages = [page for _, _, _, page in proposed if page is not None]
        distinct_pages = sorted(set(bound_pages))

        # Critical gate: do not refine ordinary long paragraphs. Splitting is
        # justified only when Mathpix lines prove that the parent spans pages.
        if len(bound_pages) < 2 or len(distinct_pages) < 2:
            output.append(dict(record))
            rejected_parent_count += 1
            audit_rows.append({
                "parentId": record.get("id"),
                "decision": "preserve-original",
                "originalLineCount": record.get("lineCount"),
                "proposedChildCount": len(proposed),
                "pageBoundChildCount": len(bound_pages),
                "distinctBoundPages": distinct_pages,
            })
            continue

        split_parent_count += 1
        emitted = 0
        for local_start, local_end, text, page in proposed:
            absolute_start = start + local_start
            absolute_end = start + local_end
            child = dict(record)
            child.update({
                "offset": absolute_start,
                "endOffset": absolute_end,
                "line": source_text.count("\n", 0, absolute_start) + 1,
                "lineCount": max(1, source_text[absolute_start:absolute_end].count("\n") + 1),
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
            output.append(child)
            child_count += 1
            emitted += 1

        audit_rows.append({
            "parentId": record.get("id"),
            "decision": "split-cross-page-proven",
            "originalLineCount": record.get("lineCount"),
            "childCount": emitted,
            "pageBoundChildCount": len(bound_pages),
            "distinctBoundPages": distinct_pages,
        })

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
        "rejectedParentCount": rejected_parent_count,
        "refinedChildCount": child_count,
        "pageBoundChildCount": page_bound_count,
        "policy": (
            "propose logical split for oversized paragraph, but materialize it only when "
            "unique exact Mathpix-lines identities prove at least two child blocks on at least "
            "two distinct pages; otherwise preserve original record"
        ),
        "parents": audit_rows,
    }
    return markdown_element_map


__all__ = ["refine_markdown_element_map"]
