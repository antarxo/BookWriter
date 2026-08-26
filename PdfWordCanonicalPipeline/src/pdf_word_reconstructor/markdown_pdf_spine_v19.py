from __future__ import annotations

from typing import Any

from .markdown_pdf_spine_v18 import build_markdown_pdf_spine as _build_v18
from .markdown_pdf_spine_v16 import _raw_text_regions
from .markdown_pdf_spine_v15 import _skeleton
from .markdown_pdf_spine_v08 import _attach, _placed
from .markdown_pdf_spine_v07 import _item_text, _page_no, _region_lookup

VERSION = "markdown-pdf-spine-0.19"

PARAGRAPH_TYPES = {"paragraph"}
FIGURE_LABEL_TYPES = {"figure_label", "caption"}


def _looks_like_single_label(text: str) -> bool:
    compact = "".join(ch for ch in str(text or "").strip() if not ch.isspace())
    alnum = [ch for ch in compact if ch.isalnum()]
    return 0 < len(alnum) <= 2


def _recover_short_exact_paragraphs(
    items: list[dict[str, Any]],
    raw_regions: dict[int, list[dict[str, Any]]],
    regions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    used = {
        (int(item.get("pdfPage") or 0), str(item.get("pdfRegion") or ""))
        for item in items
        if item.get("pdfRegion")
    }
    recovered: list[dict[str, Any]] = []

    for item in items:
        if str(item.get("type") or "") not in PARAGRAPH_TYPES or _placed(item):
            continue
        hinted_page = _page_no(item)
        text = _item_text(item)
        source_key = _skeleton(text)
        if not hinted_page or not source_key:
            continue

        exact_rows: list[dict[str, Any]] = []
        for page in (hinted_page - 1, hinted_page, hinted_page + 1):
            if page <= 0:
                continue
            for row in raw_regions.get(page, []):
                if _skeleton(str(row.get("text") or "")) != source_key:
                    continue
                candidate = dict(row)
                candidate["candidatePage"] = page
                exact_rows.append(candidate)

        # The exact textual evidence itself must be unique within the window.
        # Do not make an ambiguous label appear unique merely because another
        # matching row is already owned.
        if len(exact_rows) != 1:
            continue

        row = exact_rows[0]
        target_page = int(row.get("candidatePage") or 0)
        region_id = str(row.get("id") or "")
        if (target_page, region_id) in used:
            continue

        semantic = str(row.get("semanticType") or "")
        # Very short labels such as A), B), Γ) are only safe when the PDF itself
        # marks the row as a figure/caption label. Longer exact paragraph text can
        # use any non-header/footer text semantic.
        if _looks_like_single_label(text):
            if semantic not in FIGURE_LABEL_TYPES:
                continue
        elif semantic in {"header", "footer"}:
            continue

        _attach(item, row, target_page, 100.0, regions)
        item["matchMode"] = "adjacent-page-short-paragraph-exact-skeleton"
        used.add((target_page, region_id))
        recovered.append({
            "markdownId": item.get("id"),
            "text": text[:120],
            "hintedPage": hinted_page,
            "pdfPage": target_page,
            "pageDelta": target_page - hinted_page,
            "pdfRegion": region_id,
            "semanticType": semantic or None,
            "pdfText": str(row.get("text") or "")[:120],
            "skeleton": source_key,
        })

    return recovered


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v18(markdown_element_map, pdf_analysis)
    items = list(result.get("items", []) or [])
    raw_regions = _raw_text_regions(pdf_analysis)
    regions = _region_lookup(pdf_analysis)

    recovered = _recover_short_exact_paragraphs(items, raw_regions, regions)

    result["version"] = VERSION
    result["shortParagraphExactRecovery"] = {
        "recoveredCount": len(recovered),
        "items": recovered[:120],
        "policy": (
            "remaining paragraph items may search hinted PDF page ±1; binding requires one unique exact cross-script skeleton; "
            "candidate must be unused; single-label paragraphs require PDF semantic figure_label/caption; no ownership changes"
        ),
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
