from __future__ import annotations

from collections import Counter
import re
from typing import Any

from rapidfuzz import fuzz

from .common import normalize_text


VERSION = "docx-donor-map-0.3"


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
    text = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol|left|right|displaystyle)", "", text)
    text = re.sub(r"[^a-z0-9+\-*/=<>]", "", text)
    return text


def _markdown_text(record: dict[str, Any]) -> str:
    authoritative = record.get("authoritativeContent") if isinstance(record.get("authoritativeContent"), dict) else {}
    for value in (
        authoritative.get("text"),
        authoritative.get("plainText"),
        record.get("text"),
        record.get("captionText"),
        record.get("alt"),
        record.get("latex"),
        authoritative.get("rawMarkdown"),
        record.get("rawMarkdown"),
        record.get("textPreview"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _donor_type(paragraph: dict[str, Any]) -> str:
    text = paragraph.get("normalizedText")
    if text is None:
        text = normalize_text(paragraph.get("text", ""))
    omml_count = int(paragraph.get("omml_count", paragraph.get("ommlCount", 0)) or 0)
    drawing_count = int(paragraph.get("drawing_count", paragraph.get("drawingCount", 0)) or 0)
    numbering = paragraph.get("numbering")
    style = str(paragraph.get("style") or "").casefold()
    if omml_count and len(text) <= 3:
        return "math-omml"
    if omml_count:
        return "mixed-omml"
    if drawing_count:
        return "visual"
    if numbering:
        return "numbered-paragraph"
    if "heading" in style or "επικεφα" in style:
        return "heading"
    if "title" in style or "τίτλ" in style:
        return "title"
    if text:
        return "prose"
    return "empty"


def _semantic_compatible(markdown_type: str, donor_type: str) -> bool:
    if markdown_type in {"display_equation", "equation"}:
        return donor_type in {"math-omml", "mixed-omml"}
    if markdown_type in {"image", "figure"}:
        return donor_type == "visual"
    if markdown_type in {"list", "latex_list"}:
        return donor_type in {"numbered-paragraph", "prose"}
    if markdown_type in {"heading", "title"}:
        return donor_type in {"heading", "title", "prose"}
    if markdown_type in {"table", "latex_table"}:
        return False
    return donor_type in {"prose", "heading", "title", "mixed-omml", "numbered-paragraph"}


def _prepare_markdown_record(record: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(record)
    text = _markdown_text(record)
    prepared["__matchText"] = text
    prepared["__normalizedText"] = normalize_text(text)
    prepared["__mathSignature"] = _math_signature(str(record.get("latex") or text))
    return prepared


def _match_score(markdown: dict[str, Any], paragraph: dict[str, Any]) -> float:
    kind = str(markdown.get("type") or "")
    donor_type = str(paragraph.get("donorType") or _donor_type(paragraph))
    if not _semantic_compatible(kind, donor_type):
        return 0.0
    if kind in {"display_equation", "equation"}:
        source = str(markdown.get("__mathSignature") or "")
        target = str(paragraph.get("ommlSignature") or "")
        if not target:
            target = _math_signature(str(paragraph.get("ommlText") or paragraph.get("omml_text") or paragraph.get("text") or ""))
        return float(fuzz.ratio(source, target)) if source and target else 0.0
    source = str(markdown.get("__normalizedText") or "")
    target = str(paragraph.get("normalizedText") or "")
    if not source or not target:
        return 0.0
    if source == target:
        return 100.0
    return min(
        100.0,
        0.35 * float(fuzz.ratio(source, target))
        + 0.35 * float(fuzz.partial_ratio(source, target))
        + 0.30 * float(fuzz.token_set_ratio(source, target)),
    )


def _association_status(score: float, markdown_type: str, donor_type: str) -> str:
    if score >= 88.0:
        return "strong"
    if score >= 74.0:
        return "usable"
    if score >= 62.0 and markdown_type in {"display_equation", "equation"} and donor_type in {"math-omml", "mixed-omml"}:
        return "review"
    return "unresolved"


def _build_markdown_associations(
    markdown_element_map: dict[str, Any] | None,
    paragraphs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records = [_prepare_markdown_record(record) for record in ((markdown_element_map or {}).get("records", []) or [])]
    associations: list[dict[str, Any]] = []
    used_strong: set[str] = set()
    paragraph_count = len(paragraphs)
    record_count = len(records)

    for record in records:
        markdown_id = str(record.get("id") or "")
        kind = str(record.get("type") or "")
        order = int(record.get("orderIndex") or 0)
        if not markdown_id:
            continue
        if kind in {"table", "latex_table"}:
            associations.append({
                "markdownId": markdown_id,
                "markdownType": kind,
                "status": "table-donor-separate",
                "selected": None,
                "candidates": [],
            })
            continue

        if paragraph_count and record_count > 1:
            center = round((order / max(1, record_count - 1)) * max(0, paragraph_count - 1))
            radius = max(24, paragraph_count // 5)
            candidate_indexes = range(max(0, center - radius), min(paragraph_count, center + radius + 1))
        else:
            candidate_indexes = range(paragraph_count)

        scored: list[tuple[float, dict[str, Any]]] = []
        for index in candidate_indexes:
            paragraph = paragraphs[index]
            score = _match_score(record, paragraph)
            if score >= 45.0:
                scored.append((score, paragraph))
        scored.sort(key=lambda row: row[0], reverse=True)
        top = scored[:5]
        selected = top[0] if top else None
        if selected:
            donor_type = str(selected[1].get("donorType") or "")
            status = _association_status(float(selected[0]), kind, donor_type)
            donor_id = str(selected[1].get("id") or "")
            if status == "strong" and donor_id in used_strong:
                status = "review-duplicate-donor"
            elif status == "strong" and donor_id:
                used_strong.add(donor_id)
            selected_payload = {
                "paragraphId": selected[1].get("id"),
                "paragraphIndex": selected[1].get("index"),
                "donorType": donor_type,
                "score": round(float(selected[0]), 2),
                "locator": selected[1].get("locator"),
            }
        else:
            status = "unresolved"
            selected_payload = None

        associations.append({
            "markdownId": markdown_id,
            "markdownType": kind,
            "orderIndex": order,
            "status": status,
            "selected": selected_payload,
            "candidates": [
                {
                    "paragraphId": paragraph.get("id"),
                    "paragraphIndex": paragraph.get("index"),
                    "donorType": paragraph.get("donorType"),
                    "score": round(float(score), 2),
                    "locator": paragraph.get("locator"),
                }
                for score, paragraph in top
            ],
        })
    return associations


def _table_associations(markdown_element_map: dict[str, Any] | None, tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    markdown_tables = [
        _prepare_markdown_record(record) for record in (markdown_element_map or {}).get("records", []) or []
        if str(record.get("type") or "") in {"table", "latex_table"}
    ]
    prepared_tables = []
    for table in tables:
        prepared = dict(table)
        prepared["normalizedText"] = normalize_text(table.get("text", ""))
        prepared_tables.append(prepared)
    associations: list[dict[str, Any]] = []
    used: set[str] = set()
    for record in markdown_tables:
        source = str(record.get("__normalizedText") or "")
        scored: list[tuple[float, dict[str, Any]]] = []
        for table in prepared_tables:
            target = str(table.get("normalizedText") or "")
            if not source or not target:
                continue
            score = 0.45 * float(fuzz.partial_ratio(source, target)) + 0.55 * float(fuzz.token_set_ratio(source, target))
            scored.append((score, table))
        scored.sort(key=lambda row: row[0], reverse=True)
        best = scored[0] if scored else None
        if best and best[0] >= 70.0 and str(best[1].get("id")) not in used:
            status = "strong" if best[0] >= 86.0 else "usable"
            used.add(str(best[1].get("id")))
            selected = {
                "tableId": best[1].get("id"),
                "tableIndex": best[1].get("index"),
                "score": round(float(best[0]), 2),
                "locator": best[1].get("locator"),
            }
        else:
            status = "unresolved"
            selected = None
        associations.append({
            "markdownId": record.get("id"),
            "markdownType": record.get("type"),
            "status": status,
            "selected": selected,
        })
    return associations


def build_docx_donor_map(
    docx_analysis: dict[str, Any],
    markdown_element_map: dict[str, Any] | None = None,
    alignment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paragraphs: list[dict[str, Any]] = []
    type_counts: Counter[str] = Counter()
    for paragraph in docx_analysis.get("paragraphs", []) or []:
        normalized_text = normalize_text(paragraph.get("text", ""))
        donor_seed = dict(paragraph)
        donor_seed["normalizedText"] = normalized_text
        donor_type = _donor_type(donor_seed)
        type_counts[donor_type] += 1
        paragraphs.append({
            "id": paragraph.get("id"),
            "index": paragraph.get("index"),
            "container": paragraph.get("container"),
            "locator": paragraph.get("locator"),
            "style": paragraph.get("style"),
            "donorType": donor_type,
            "text": str(paragraph.get("text") or ""),
            "normalizedText": normalized_text,
            "paragraphFormat": paragraph.get("paragraph_format"),
            "numbering": paragraph.get("numbering"),
            "runs": paragraph.get("runs") or [],
            "ommlText": str(paragraph.get("omml_text") or ""),
            "ommlSignature": _math_signature(str(paragraph.get("omml_text") or "")),
            "ommlCount": int(paragraph.get("omml_count", 0) or 0),
            "drawingCount": int(paragraph.get("drawing_count", 0) or 0),
            "drawingRelationshipIds": paragraph.get("drawing_relationship_ids") or [],
            "nativeFlags": paragraph.get("native_flags") or {},
            "isMathOnly": bool(paragraph.get("is_math_only")),
        })

    tables = [dict(table) for table in (docx_analysis.get("tables", []) or [])]
    sections = [dict(section) for section in (docx_analysis.get("sections", []) or [])]
    markdown_associations = _build_markdown_associations(markdown_element_map, paragraphs)
    table_associations = _table_associations(markdown_element_map, tables)

    by_markdown: dict[str, dict[str, Any]] = {}
    for association in markdown_associations:
        by_markdown[str(association.get("markdownId") or "")] = association
    for association in table_associations:
        by_markdown[str(association.get("markdownId") or "")] = association

    status_counts = Counter(str(item.get("status") or "unknown") for item in by_markdown.values())
    math_candidates = [
        {
            "id": item.get("id"),
            "index": item.get("index"),
            "locator": item.get("locator"),
            "donorType": item.get("donorType"),
            "ommlSignature": item.get("ommlSignature"),
            "ommlText": item.get("ommlText"),
            "runs": item.get("runs"),
        }
        for item in paragraphs
        if int(item.get("ommlCount") or 0) > 0 and item.get("donorType") in {"math-omml", "mixed-omml"}
    ]

    return {
        "version": VERSION,
        "policy": {
            "role": "native-word-donor-only",
            "contentAuthority": False,
            "layoutAuthority": False,
            "allowedUses": ["omml", "native-table", "native-drawing", "numbering", "verified-style-hint"],
            "association": "direct-markdown-to-docx-donor-map",
            "legacyAlignmentRequired": False,
            "matchingCache": "precomputed-normalized-text-and-math-signatures",
        },
        "source": docx_analysis.get("source"),
        "sourceAnalysisVersion": docx_analysis.get("version"),
        "summary": {
            "paragraphCount": len(paragraphs),
            "tableCount": len(tables),
            "sectionCount": len(sections),
            "mathCandidateCount": len(math_candidates),
            "associationCount": len(by_markdown),
            "associationStatusCounts": dict(status_counts),
            "donorTypeCounts": dict(type_counts),
        },
        "paragraphs": paragraphs,
        "tables": tables,
        "sections": sections,
        "mathCandidates": math_candidates,
        "markdownAssociations": list(by_markdown.values()),
        "associationByMarkdownId": by_markdown,
    }
