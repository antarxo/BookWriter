from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import fitz
from docx import Document
from rapidfuzz import fuzz

from .common import normalize_text


def _clean_line(text: str) -> str:
    text = (text or "").replace("\u00ad", "")
    text = re.sub(r"([\wά-ώΆ-Ώ])-\s+([\wά-ώΆ-Ώ])", r"\1\2", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _source_pdf_lines(pdf_path: Path, pages: list[int]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as pdf:
        for page_no in pages:
            raw = pdf[page_no - 1].get_text("text", sort=True)
            for line_no, raw_line in enumerate(raw.splitlines(), start=1):
                line = _clean_line(raw_line)
                norm = normalize_text(line)
                if len(norm) < 12:
                    continue
                output.append({"page": page_no, "line": line_no, "text": line, "norm": norm})
    return output


def _output_pdf_lines(pdf_path: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if not pdf_path.exists():
        return output
    with fitz.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf, start=1):
            raw = page.get_text("text", sort=True)
            for line_no, raw_line in enumerate(raw.splitlines(), start=1):
                line = _clean_line(raw_line)
                norm = normalize_text(line)
                if len(norm) < 12:
                    continue
                output.append({"page": page_index, "line": line_no, "text": line, "norm": norm})
    return output


def _docx_text(docx_path: Path) -> str:
    if not docx_path.exists():
        return ""
    doc = Document(docx_path)
    return "\n".join(paragraph.text for paragraph in doc.paragraphs if paragraph.text)


def _docx_paragraph_number(value: Any) -> int | None:
    match = re.search(r"(?:docx|d)-p0*([0-9]+)", str(value or ""))
    return int(match.group(1)) if match else None


def _strip_latex_table_markup(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"\\begin\{tabular\}(?:\[[^\]]*\])?(?:\{[^{}]*\})?", " ", text)
    text = re.sub(r"\\end\{tabular\}", " ", text)
    text = re.sub(r"\\(?:hline|cline|toprule|midrule|bottomrule)(?:\{[^{}]*\})?", " ", text)
    text = re.sub(r"\\multicolumn\{\d+\}\{[^{}]*\}\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\multirow\{[^{}]*\}\{[^{}]*\}\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"^\s*\[[a-z]\]\s*", " ", text)
    text = re.sub(r"\|?[lcr](?:\|[lcr])+\|?", " ", text)
    text = re.sub(r"\|", " ", text)
    text = text.replace("&", " ")
    text = text.replace("\\\\", " ")
    return text


def _strip_common_latex_markup(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"<smiles\b[^>]*>.*?</smiles>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\\(?:section\*?|subsection\*?|subsubsection\*?|title|author|caption)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:begin|end)\{[^{}]+\}", " ", text)
    text = re.sub(r"\\(?:item)(?:\[[^\]]*\])?", " ", text)
    text = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol|emph|textbf|textit)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:frac)\{([^{}]*)\}\{([^{}]*)\}", r"\1 \2", text)
    text = re.sub(r"\\[a-zA-Z]+", " ", text)
    text = re.sub(r"[_^{}$]", " ", text)
    text = re.sub(r"_{4,}|\.{4,}|…{2,}", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_report_docx_paragraph_numbers(build_report: dict[str, Any] | None) -> set[int]:
    numbers: set[int] = set()
    for page in (build_report or {}).get("pages", []):
        for item in page.get("items", []):
            for paragraph_id in item.get("docx_paragraphs", []) or []:
                number = _docx_paragraph_number(paragraph_id)
                if number is not None:
                    numbers.add(number)
        for callout in page.get("callout_builds", []) or []:
            for paragraph_id in callout.get("source_paragraphs", []) or []:
                number = _docx_paragraph_number(paragraph_id)
                if number is not None:
                    numbers.add(number)
    return numbers


def _markdown_element_text(element: dict[str, Any]) -> str:
    kind = str(element.get("type") or "")
    for key in ("text", "latex", "captionText", "alt"):
        value = str(element.get(key) or "").strip()
        if value:
            if kind in {"latex_table", "table"}:
                value = _strip_latex_table_markup(value)
            return _strip_common_latex_markup(value)
    preview = str(element.get("textPreview") or "").strip()
    if kind in {"latex_table", "table"}:
        preview = _strip_latex_table_markup(preview)
    return _strip_common_latex_markup(preview)


def _semantic_token_count(text: str) -> int:
    return len(re.findall(r"[0-9a-zα-ωάέήίόύώϊϋΐΰ]{2,}", normalize_text(text)))


def _markdown_candidate_elements(
    markdown_element_map: dict[str, Any] | None,
    build_report: dict[str, Any] | None,
    *,
    selected_pages: set[int] | None = None,
    allowed_markdown_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    records = list((markdown_element_map or {}).get("records", []) or [])
    paragraph_numbers = _build_report_docx_paragraph_numbers(build_report)
    if not paragraph_numbers and not allowed_markdown_ids:
        return []
    selected: list[dict[str, Any]] = []
    for element in records:
        element_id = str(element.get("id") or "")
        if allowed_markdown_ids is not None and element_id not in allowed_markdown_ids:
            continue
        page = element.get("page")
        if selected_pages is not None and isinstance(page, int) and page not in selected_pages:
            continue
        if allowed_markdown_ids is not None:
            selected.append(element)
            continue
        evidence = element.get("docxEvidence") or {}
        number = _docx_paragraph_number(evidence.get("paragraphId"))
        if number is not None and number in paragraph_numbers:
            selected.append(element)
    return selected


def _audit_markdown_survival(
    markdown_element_map: dict[str, Any] | None,
    build_report: dict[str, Any] | None,
    output_pdf_norm: str,
    output_docx_norm: str,
    markdown_pdf_spine: dict[str, Any] | None = None,
    selected_pages: set[int] | None = None,
    limit: int = 60,
) -> dict[str, Any]:
    spine_items = (markdown_pdf_spine or {}).get("items", []) or []
    allowed_markdown_ids = {
        str(item.get("id") or "")
        for item in spine_items
        if item.get("id")
    } or None
    candidates = _markdown_candidate_elements(
        markdown_element_map,
        build_report,
        selected_pages=selected_pages,
        allowed_markdown_ids=allowed_markdown_ids,
    )
    checked: list[dict[str, Any]] = []
    for element in candidates:
        if element.get("type") == "display_equation":
            continue
        text = _markdown_element_text(element)
        norm = normalize_text(text)
        semantic_tokens = _semantic_token_count(text)
        if len(norm) < 12 or semantic_tokens < 3:
            continue
        score_pdf = _best_line_match(norm, output_pdf_norm)
        score_docx = _best_line_match(norm, output_docx_norm)
        score = max(score_pdf, score_docx)
        status = "matched" if score >= 78.0 else ("weak" if score >= 55.0 else "missing")
        checked.append({
            "id": element.get("id"),
            "type": element.get("type"),
            "page": element.get("page"),
            "line": element.get("line"),
            "status": status,
            "score": round(score, 2),
            "scorePdf": round(score_pdf, 2),
            "scoreDocx": round(score_docx, 2),
            "text": text,
            "semanticTokenCount": semantic_tokens,
            "survivalDecisionEligible": (
                element.get("type") not in {"table", "latex_table"}
                and _is_substantive_missing_prose(text)
            ),
            "docxEvidence": element.get("docxEvidence") or None,
        })
    counts = Counter(str(item["status"]) for item in checked)
    type_counts = Counter(str(item.get("type") or "") for item in checked)
    problem_rows = [item for item in checked if item["status"] != "matched"]
    problem_rows.sort(key=lambda item: (0 if item["status"] == "missing" else 1, float(item["score"]), str(item.get("id") or "")))
    matched = int(counts.get("matched", 0))
    total = len(checked)
    return {
        "status": "not-run" if not candidates else ("ok" if not problem_rows else "review"),
        "scope": "markdown-elements-from-selected-markdown-pdf-spine" if allowed_markdown_ids else "markdown-elements-with-docx-evidence-used-by-selected-output",
        "candidate_count": len(candidates),
        "checked_count": total,
        "matched_count": matched,
        "weak_count": int(counts.get("weak", 0)),
        "missing_count": int(counts.get("missing", 0)),
        "coverage": round(matched / total, 5) if total else 1.0,
        "statusCounts": dict(counts),
        "typeCounts": dict(type_counts),
        "problemElements": problem_rows[:limit],
    }


def _token_counter(lines: list[dict[str, Any]]) -> Counter[str]:
    tokens: Counter[str] = Counter()
    for line in lines:
        tokens.update(re.findall(r"[0-9a-zα-ωάέήίόύώϊϋΐΰ]+", line["norm"]))
    return tokens


def _coverage(source: Counter[str], output: Counter[str]) -> float:
    total = sum(source.values())
    if not total:
        return 1.0
    covered = sum(min(count, output.get(token, 0)) for token, count in source.items())
    return covered / total


def _best_line_match(line_norm: str, output_norm: str) -> float:
    if not line_norm:
        return 100.0
    if line_norm in output_norm:
        return 100.0
    return float(fuzz.partial_ratio(line_norm, output_norm))


_MATH_CHARS = set("=+-−–*/⋅×∙·≤≥<>λνΔδπ∞()[]{}^")
_ELEMENT_SYMBOLS = {
    "h", "he", "li", "be", "b", "c", "n", "o", "f", "ne", "na", "mg", "al", "si", "p", "s", "cl", "ar",
    "k", "ca", "sc", "ti", "v", "cr", "mn", "fe", "co", "ni", "cu", "zn", "ga", "ge", "as", "se", "br",
    "kr", "rb", "sr", "y", "zr", "nb", "mo", "tc", "ru", "rh", "pd", "ag", "cd", "in", "sn", "sb", "te",
    "i", "xe", "cs", "ba", "la", "ce", "pr", "nd", "pm", "sm", "eu", "gd", "tb", "dy", "ho", "er", "tm",
    "yb", "lu", "hf", "ta", "w", "re", "os", "ir", "pt", "au", "hg", "tl", "pb", "bi", "po", "at", "rn",
}
_CHEM_FRAGMENT_TOKENS = {
    "1s", "2s", "2p", "3s", "3p", "3d", "4s", "4p", "4d", "4f", "5s", "5p", "5d", "5f",
    "sp", "sp2", "sp3", "ch", "ch2", "ch3", "cooh", "oh", "nh2", "σ", "π", "σπ",
}


def _review_item(line: dict[str, Any], score: float) -> dict[str, Any]:
    return {
        "page": line["page"],
        "line": line["line"],
        "score": round(score, 2),
        "text": line["text"],
    }


def _is_formula_like(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) < 8:
        return False
    private_use = sum(1 for char in compact if "\uf000" <= char <= "\uf8ff")
    digit_count = sum(1 for char in compact if char.isdigit())
    math_count = sum(1 for char in compact if char in _MATH_CHARS)
    alpha_count = sum(1 for char in compact if char.isalpha())
    ratio = (digit_count + math_count + private_use) / max(1, len(compact))
    if private_use:
        return True
    if math_count >= 2 and ratio >= 0.20:
        return True
    return digit_count >= 4 and alpha_count <= digit_count and ratio >= 0.35


def _is_reference_or_decorative_line(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return True
    lowered = compact.casefold()
    if "http://" in lowered or "https://" in lowered or "www." in lowered:
        return True
    decorative = sum(1 for char in compact if char in "._-–—…·\\")
    return decorative / max(1, len(compact)) >= 0.55


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[0-9a-zα-ωάέήίόύώϊϋΐΰ]+", normalize_text(text))


def _is_table_or_symbol_fragment(text: str) -> bool:
    toks = _word_tokens(text)
    if len(toks) < 3:
        return True
    numeric = sum(1 for tok in toks if tok.isdigit())
    short = sum(1 for tok in toks if len(tok) <= 3)
    element_like = sum(1 for tok in toks if tok.casefold() in _ELEMENT_SYMBOLS or re.fullmatch(r"\d+[a-z]{1,2}", tok))
    chem_like = sum(1 for tok in toks if tok.casefold() in _CHEM_FRAGMENT_TOKENS or re.fullmatch(r"\d+[spdf][0-9]*", tok))
    greek_words = [tok for tok in toks if re.search(r"[α-ωάέήίόύώϊϋΐΰ]", tok)]
    long_greek_words = [tok for tok in greek_words if len(tok) >= 5]
    latin_words = [tok for tok in toks if re.search(r"[a-z]", tok)]
    if element_like >= 3 and element_like + numeric >= max(3, len(toks) - 1):
        return True
    if chem_like >= 4 and (chem_like + element_like + numeric + short) >= max(5, int(len(toks) * 0.65)):
        return True
    if len(toks) <= 8 and short / len(toks) >= 0.70 and (numeric or element_like):
        return True
    if len(toks) <= 6 and not long_greek_words and latin_words:
        return True
    return False


def _is_line_join_artifact(text: str) -> bool:
    value = str(text or "").strip()
    if re.search(r"[α-ωάέήίόύώϊϋΐΰ]-[Α-ΩΆΈΉΊΌΎΏA-Z]", value):
        return True
    if re.search(r"[a-zα-ωάέήίόύώϊϋΐΰ][Α-ΩΆΈΉΊΌΎΏ][α-ωάέήίόύώϊϋΐΰ]", value):
        return True
    return False


def _is_substantive_missing_prose(text: str) -> bool:
    toks = _word_tokens(text)
    if len(toks) < 7:
        return False
    stripped = str(text or "").strip()
    if stripped and stripped[0].islower():
        return False
    if _is_reference_or_decorative_line(text):
        return False
    if _is_line_join_artifact(text):
        return False
    greek_words = [tok for tok in toks if re.search(r"[α-ωάέήίόύώϊϋΐΰ]", tok)]
    long_greek_words = [tok for tok in greek_words if len(tok) >= 5]
    if len(long_greek_words) < 3:
        return False
    return not _is_table_or_symbol_fragment(text)


def _missing_lines(
    source_lines: list[dict[str, Any]],
    output_norm: str,
    limit: int = 40,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    misses: list[dict[str, Any]] = []
    formula_review: list[dict[str, Any]] = []
    layout_variations: list[dict[str, Any]] = []
    for line in source_lines:
        norm = line["norm"]
        if len(norm) < 18:
            continue
        score = _best_line_match(norm, output_norm)
        if score >= 78.0:
            continue
        item = _review_item(line, score)
        if _is_reference_or_decorative_line(line["text"]):
            layout_variations.append(item)
        elif _is_line_join_artifact(line["text"]):
            layout_variations.append(item)
        elif _is_table_or_symbol_fragment(line["text"]):
            formula_review.append(item)
        elif _is_formula_like(line["text"]):
            formula_review.append(item)
        elif score >= 55.0:
            layout_variations.append(item)
        elif not _is_substantive_missing_prose(line["text"]):
            layout_variations.append(item)
        else:
            misses.append(item)
    misses.sort(key=lambda item: (float(item["score"]), int(item["page"]), int(item["line"])))
    formula_review.sort(key=lambda item: (float(item["score"]), int(item["page"]), int(item["line"])))
    layout_variations.sort(key=lambda item: (float(item["score"]), int(item["page"]), int(item["line"])))
    return misses[:limit], formula_review[:limit], layout_variations[:limit]


def _extra_output_lines(
    output_lines: list[dict[str, Any]],
    source_norm: str,
    limit: int = 40,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    extras: list[dict[str, Any]] = []
    formula_review: list[dict[str, Any]] = []
    layout_joins: list[dict[str, Any]] = []
    for line in output_lines:
        norm = line["norm"]
        if len(norm) < 18:
            continue
        score = _best_line_match(norm, source_norm)
        if score >= 78.0:
            continue
        item = _review_item(line, score)
        if _is_formula_like(line["text"]):
            formula_review.append(item)
        elif len(line["text"]) >= 120 and score >= 50.0:
            layout_joins.append(item)
        elif score >= 55.0:
            layout_joins.append(item)
        else:
            extras.append(item)
    extras.sort(key=lambda item: (float(item["score"]), int(item["page"]), int(item["line"])))
    formula_review.sort(key=lambda item: (float(item["score"]), int(item["page"]), int(item["line"])))
    layout_joins.sort(key=lambda item: (float(item["score"]), int(item["page"]), int(item["line"])))
    return extras[:limit], formula_review[:limit], layout_joins[:limit]


def audit_content(
    source_pdf: Path,
    pages: list[int],
    output_docx: Path,
    output_pdf: Path,
    build_report: dict[str, Any] | None = None,
    markdown_element_map: dict[str, Any] | None = None,
    markdown_pdf_spine: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_lines = _source_pdf_lines(source_pdf, pages)
    output_pdf_lines = _output_pdf_lines(output_pdf)
    output_docx_text = _docx_text(output_docx)

    source_norm = normalize_text("\n".join(line["text"] for line in source_lines))
    output_pdf_norm = normalize_text("\n".join(line["text"] for line in output_pdf_lines))
    output_docx_norm = normalize_text(output_docx_text)

    source_tokens = _token_counter(source_lines)
    output_pdf_tokens = _token_counter(output_pdf_lines)
    output_docx_tokens = Counter(re.findall(r"[0-9a-zα-ωάέήίόύώϊϋΐΰ]+", output_docx_norm))

    build_pages = (build_report or {}).get("pages", [])
    raster_equation_fallbacks = 0
    native_math = 0
    for page in build_pages:
        for item in page.get("items", []):
            if item.get("type") == "equation" and str(item.get("source", "")).startswith("page-crop"):
                raster_equation_fallbacks += 1
            native_math += int(item.get("native_math_count", item.get("math_count", 0)) or 0)
        for callout in page.get("callout_builds", []):
            native_math += int(callout.get("native_math_count", 0) or 0)

    suspicious_text = output_docx_text + "\n" + "\n".join(line["text"] for line in output_pdf_lines)
    suspicious_glyphs = suspicious_text.count("□") + suspicious_text.count("�")

    pdf_coverage = _coverage(source_tokens, output_pdf_tokens)
    docx_coverage = _coverage(source_tokens, output_docx_tokens)
    missing, formula_missing, layout_variations = _missing_lines(source_lines, output_pdf_norm)
    extras, formula_extras, layout_joins = _extra_output_lines(output_pdf_lines, source_norm)
    markdown_survival = _audit_markdown_survival(
        markdown_element_map,
        build_report,
        output_pdf_norm,
        output_docx_norm,
        markdown_pdf_spine=markdown_pdf_spine,
        selected_pages=set(int(page) for page in pages),
    ) if markdown_element_map else {}

    status = "content-review"
    if pdf_coverage >= 0.86 and suspicious_glyphs <= 2:
        status = "content-usable"
    elif pdf_coverage < 0.78 or suspicious_glyphs > 5:
        status = "content-critical"

    return {
        "version": "content-audit-0.1",
        "status": status,
        "source_pdf": str(source_pdf),
        "output_docx": str(output_docx),
        "output_pdf": str(output_pdf),
        "pages": pages,
        "source_line_count": len(source_lines),
        "output_pdf_line_count": len(output_pdf_lines),
        "source_token_count": sum(source_tokens.values()),
        "output_pdf_token_count": sum(output_pdf_tokens.values()),
        "output_docx_token_count": sum(output_docx_tokens.values()),
        "source_to_output_pdf_token_coverage": round(pdf_coverage, 5),
        "source_to_output_docx_token_coverage": round(docx_coverage, 5),
        "suspicious_glyphs": suspicious_glyphs,
        "native_math_count": native_math,
        "raster_equation_fallbacks": raster_equation_fallbacks,
        "likely_missing_source_line_count": len(missing),
        "likely_extra_output_line_count": len(extras),
        "formula_review_line_count": len(formula_missing) + len(formula_extras),
        "layout_variation_line_count": len(layout_variations),
        "layout_join_artifact_count": len(layout_joins),
        "likely_missing_lines": missing,
        "likely_extra_output_lines": extras,
        "formula_review_lines": formula_missing + formula_extras,
        "layout_text_variation_lines": layout_variations,
        "layout_join_artifacts": layout_joins,
        "markdown_survival": markdown_survival,
        "markdown_pdf_spine": markdown_pdf_spine or {},
    }
