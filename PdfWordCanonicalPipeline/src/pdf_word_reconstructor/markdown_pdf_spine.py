from __future__ import annotations

# Canonical entry point. First recover missing Markdown page hints from
# high-confidence monotonic Markdown↔PDF text anchors. Then build the normal
# Markdown/PDF spine and apply page-scoped, neighbor-bounded, conservative
# directional, conflict-aware, structural, adjacent-page, short-heading,
# strict short-paragraph exact, conservative contiguous multi-region paragraph
# recovery, non-destructive page-furniture isolation, conservative adjacent-page
# sequence-bracket paragraph recovery, and finally recompute all unresolved-text
# diagnostics against a header/footer-free PDF witness.
from pathlib import Path
from typing import Any

from .markdown_pdf_page_alignment import infer_missing_markdown_pages
from .markdown_pdf_spine_v25 import build_markdown_pdf_spine as _build_v25


VERSION = "markdown-pdf-spine-wrapper-0.26"


def _source_slice(record: dict[str, Any], cache: dict[str, str]) -> str:
    source = str(record.get("source") or "")
    try:
        start = int(record.get("offset"))
        end = int(record.get("endOffset"))
    except (TypeError, ValueError):
        return ""
    if not source or end <= start:
        return ""
    if source not in cache:
        path = Path(source)
        try:
            cache[source] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            cache[source] = ""
    text = cache[source]
    if not text:
        return ""
    start = max(0, min(len(text), start))
    end = max(start, min(len(text), end))
    return text[start:end]


def _authoritative_payload(record: dict[str, Any], raw_markdown: str) -> dict[str, Any]:
    kind = str(record.get("type") or "")
    explicit_text = str(record.get("text") or "")
    caption = str(record.get("captionText") or "")
    alt = str(record.get("alt") or "")
    latex = str(record.get("latex") or "")

    if explicit_text:
        text = explicit_text
        source = "markdown-element-explicit-text"
    elif kind in {"display_equation", "equation"} and latex:
        text = latex
        source = "markdown-element-latex"
    elif caption:
        text = caption
        source = "markdown-element-caption"
    elif kind in {"image", "figure"} and alt:
        text = alt
        source = "markdown-element-alt"
    else:
        # Paragraph/list/table blocks are already bounded by the canonical MMD
        # parser. Preserve the complete source slice rather than the 240-char
        # textPreview, which is diagnostic-only and must never become content.
        text = raw_markdown.strip()
        source = "canonical-mmd-source-slice"

    return {
        "text": text,
        "plainText": text,
        "source": source,
        "authority": "Mathpix canonical MMD",
        "markdownType": kind,
    }


def _freeze_markdown_authority(
    result: dict[str, Any],
    markdown_map: dict[str, Any],
) -> dict[str, Any]:
    records = {
        str(record.get("id")): record
        for record in markdown_map.get("records", []) or []
        if record.get("id")
    }
    source_cache: dict[str, str] = {}
    frozen = 0
    missing = 0

    for item in result.get("items", []) or []:
        record = records.get(str(item.get("id") or ""))
        if record is None:
            missing += 1
            continue
        raw_markdown = _source_slice(record, source_cache)
        payload = _authoritative_payload(record, raw_markdown)
        item["rawMarkdown"] = raw_markdown
        item["authoritativeContent"] = payload
        item["contentContract"] = dict(payload)
        # Keep this compatibility field full-length as well. Downstream code may
        # read it, but authoritativeContent/contentContract remain the contract.
        item["text"] = str(payload.get("text") or "")
        frozen += 1

    return {
        "recordCount": len(records),
        "spineItemCount": len(result.get("items", []) or []),
        "frozenItemCount": frozen,
        "missingRecordCount": missing,
        "policy": "complete canonical MMD element payload is frozen before layout/build-contract; textPreview is diagnostic-only",
    }


def build_markdown_pdf_spine(
    markdown_element_map: dict[str, Any] | None,
    pdf_analysis: dict[str, Any],
) -> dict[str, Any]:
    markdown_map = markdown_element_map or {}
    page_alignment = infer_missing_markdown_pages(markdown_map, pdf_analysis)
    result = _build_v25(markdown_map, pdf_analysis)
    authority_summary = _freeze_markdown_authority(result, markdown_map)
    result["canonicalWrapperVersion"] = VERSION
    result["pageAlignmentFallback"] = page_alignment
    result["markdownAuthority"] = authority_summary
    return result


__all__ = ["build_markdown_pdf_spine"]
