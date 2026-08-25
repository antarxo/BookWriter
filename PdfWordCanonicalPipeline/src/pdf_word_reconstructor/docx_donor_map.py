from __future__ import annotations

from collections import Counter
import re
from typing import Any

from .common import compact_text, normalize_text


def _docx_number(value: Any) -> int | None:
    match = re.search(r"(\d+)$", str(value or ""))
    return int(match.group(1)) if match else None


def _math_signature(text: str) -> str:
    text = (text or "").lower()
    replacements = {
        "−": "-", "–": "-", "—": "-", "⋅": "*", "·": "*", "×": "*",
        "ν": "v", "𝜈": "v", "λ": "l", "𝜆": "l", "ε": "e", "𝜀": "e",
        "α": "a", "β": "b", "γ": "g", "θ": "th", "μ": "m", "π": "p",
        "ρ": "r", "σ": "s", "τ": "t", "φ": "f", "χ": "x", "ω": "w",
        "∞": "inf", "→": "->", "𝛥": "d", "δ": "d", "Δ": "d",
        "𝐸": "e", "𝐽": "j", "ℎ": "h", "𝑛": "n", "𝑐": "c",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"[^a-z0-9+\-*/=<>]", "", text)
    return text


def _donor_type(paragraph: dict[str, Any]) -> str:
    text = normalize_text(paragraph.get("text", ""))
    omml_count = int(paragraph.get("omml_count", 0) or 0)
    drawing_count = int(paragraph.get("drawing_count", 0) or 0)
    style = str(paragraph.get("style") or "").casefold()
    if omml_count and len(text) <= 3:
        return "math-omml"
    if omml_count:
        return "mixed-omml"
    if drawing_count:
        return "visual"
    if "heading" in style or "επικεφα" in style:
        return "heading"
    if "title" in style or "τίτλ" in style:
        return "title"
    if text:
        return "prose"
    return "empty"


def _markdown_link(record: dict[str, Any]) -> dict[str, Any] | None:
    evidence = record.get("docxEvidence") or {}
    paragraph_index = evidence.get("paragraphIndex")
    if paragraph_index is None:
        paragraph_index = _docx_number(evidence.get("paragraphId"))
    if paragraph_index is None:
        return None
    return {
        "markdownId": record.get("id"),
        "type": record.get("type"),
        "orderIndex": record.get("orderIndex"),
        "pageHint": record.get("page"),
        "line": record.get("line"),
        "score": evidence.get("score"),
        "status": evidence.get("status"),
        "suggestedType": evidence.get("suggestedType"),
        "text": compact_text(
            str(record.get("text") or record.get("latex") or record.get("captionText") or record.get("textPreview") or ""),
            260,
        ),
        "paragraphIndex": int(paragraph_index),
    }


def _pdf_links(alignment: dict[str, Any] | None) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for match in (alignment or {}).get("matches", []) or []:
        for index in match.get("docx_indexes", []) or []:
            try:
                paragraph_index = int(index)
            except Exception:
                continue
            result.setdefault(paragraph_index, []).append({
                "pdfRegion": match.get("pdf_region"),
                "page": match.get("page"),
                "bbox": match.get("bbox"),
                "status": match.get("status"),
                "score": match.get("score"),
                "semanticType": match.get("semantic_type"),
                "flowZone": match.get("flow_zone"),
                "pdfText": compact_text(str(match.get("pdf_text") or ""), 260),
            })
    return result


def build_docx_donor_map(
    docx_analysis: dict[str, Any],
    markdown_element_map: dict[str, Any] | None = None,
    alignment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    markdown_by_docx: dict[int, list[dict[str, Any]]] = {}
    for record in (markdown_element_map or {}).get("records", []) or []:
        link = _markdown_link(record)
        if not link:
            continue
        markdown_by_docx.setdefault(int(link["paragraphIndex"]), []).append(link)
    pdf_by_docx = _pdf_links(alignment)

    paragraphs: list[dict[str, Any]] = []
    type_counts: Counter[str] = Counter()
    link_counts: Counter[str] = Counter()
    for paragraph in docx_analysis.get("paragraphs", []) or []:
        index = int(paragraph.get("index") or 0)
        donor_type = _donor_type(paragraph)
        markdown_links = markdown_by_docx.get(index, [])
        pdf_links = pdf_by_docx.get(index, [])
        if markdown_links:
            link_counts["markdown"] += 1
        if pdf_links:
            link_counts["pdf"] += 1
        type_counts[donor_type] += 1
        paragraphs.append({
            "id": paragraph.get("id"),
            "index": index,
            "container": paragraph.get("container"),
            "style": paragraph.get("style"),
            "donorType": donor_type,
            "text": compact_text(str(paragraph.get("text") or ""), 360),
            "normalizedText": normalize_text(paragraph.get("text", "")),
            "ommlText": compact_text(str(paragraph.get("omml_text") or ""), 360),
            "ommlSignature": _math_signature(str(paragraph.get("omml_text") or "")),
            "ommlCount": int(paragraph.get("omml_count", 0) or 0),
            "drawingCount": int(paragraph.get("drawing_count", 0) or 0),
            "isMathOnly": bool(paragraph.get("is_math_only")),
            "markdownLinks": markdown_links,
            "pdfLinks": pdf_links,
        })

    math_candidates = [
        {
            "id": item.get("id"),
            "index": item.get("index"),
            "donorType": item.get("donorType"),
            "ommlSignature": item.get("ommlSignature"),
            "ommlText": item.get("ommlText"),
            "markdownLinks": item.get("markdownLinks", []),
            "pdfLinks": item.get("pdfLinks", []),
        }
        for item in paragraphs
        if int(item.get("ommlCount") or 0) > 0 and item.get("donorType") in {"math-omml", "mixed-omml"}
    ]
    range_value = ((alignment or {}).get("summary") or {}).get("candidate_docx_paragraph_range") or []
    return {
        "version": "docx-donor-map-0.1",
        "policy": "Mathpix DOCX is inventoried once as a secondary donor. Builder/report code should select from this map instead of re-scanning unrelated paragraphs.",
        "source": docx_analysis.get("source"),
        "summary": {
            "paragraphCount": len(paragraphs),
            "mathCandidateCount": len(math_candidates),
            "markdownLinkedParagraphCount": int(link_counts.get("markdown", 0)),
            "pdfLinkedParagraphCount": int(link_counts.get("pdf", 0)),
            "donorTypeCounts": dict(type_counts),
            "candidateDocxParagraphRange": range_value,
        },
        "paragraphs": paragraphs,
        "mathCandidates": math_candidates,
    }
