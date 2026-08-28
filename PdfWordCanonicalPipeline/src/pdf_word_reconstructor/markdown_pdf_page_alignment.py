from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any


VERSION = "markdown-pdf-page-alignment-0.3"

_TEXT_TYPES = {
    "paragraph", "heading", "title", "caption", "callout", "list", "latex_list",
    "list_item", "ordered_list", "unordered_list", "text",
}


def _norm(value: str) -> str:
    text = str(value or "").casefold()
    text = text.replace("\u00ad", "").replace("\u200b", "")
    # Join words that PDF extraction split only because of end-of-line hyphenation.
    text = re.sub(r"(?<=\w)[‐‑‒–—-]\s+(?=\w)", "", text)
    text = re.sub(r"\\[a-zA-Z]+", " ", text)
    text = re.sub(r"[^0-9a-zα-ωά-ώ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _record_text(record: dict[str, Any]) -> str:
    authoritative = record.get("authoritativeContent") if isinstance(record.get("authoritativeContent"), dict) else {}
    for value in (
        authoritative.get("text"), authoritative.get("plainText"), authoritative.get("captionText"),
        authoritative.get("alt"), authoritative.get("latex"),
        record.get("text"), record.get("captionText"), record.get("alt"), record.get("latex"),
        record.get("textPreview"), record.get("rawMarkdown"),
    ):
        text = _norm(str(value or ""))
        if text:
            return text
    return ""


def _page_texts(pdf_analysis: dict[str, Any]) -> dict[int, str]:
    result: dict[int, str] = {}
    for page in pdf_analysis.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        parts: list[str] = []
        for region in page.get("regions", []) or []:
            if region.get("type") != "text":
                continue
            text = _norm(str(region.get("text") or ""))
            if text:
                parts.append(text)
        result[page_no] = " ".join(parts)
    return result


def _token_coverage(text: str, page_text: str) -> float:
    """How much of one Markdown text block is witnessed by one PDF page."""
    words = text.split()
    page_words = page_text.split()
    if len(words) < 3 or not page_words:
        return 0.0

    matcher = SequenceMatcher(None, words, page_words, autojunk=False)
    ordered = sum(block.size for block in matcher.get_matching_blocks()) / max(1, len(words))

    need = Counter(words)
    have = Counter(page_words)
    overlap = sum(min(count, have.get(token, 0)) for token, count in need.items())
    bag = overlap / max(1, len(words))
    return min(1.0, 0.68 * ordered + 0.32 * bag)


def _char_ngram_coverage(text: str, page_text: str, n: int = 4) -> float:
    """Character witness robust to whitespace, line wrapping and benign hyphenation."""
    a = re.sub(r"\s+", "", text)
    b = re.sub(r"\s+", "", page_text)
    if len(a) < max(12, n + 2) or len(b) < n:
        return 0.0
    if a in b:
        return 1.0
    need = Counter(a[i:i+n] for i in range(0, len(a) - n + 1))
    have = Counter(b[i:i+n] for i in range(0, len(b) - n + 1))
    overlap = sum(min(count, have.get(token, 0)) for token, count in need.items())
    return overlap / max(1, sum(need.values()))


def _anchor_score(text: str, page_text: str) -> float:
    if not text or not page_text:
        return 0.0
    if len(text) >= 18 and text in page_text:
        return 1.0
    words = text.split()
    if len(words) < 3 or len(text) < 16:
        return 0.0

    token_score = _token_coverage(text, page_text)
    char_score = _char_ngram_coverage(text, page_text)

    probes = [text]
    if len(words) >= 8:
        probes.extend([" ".join(words[:8]), " ".join(words[-8:])])
    best = max(token_score, char_score)
    for probe in probes:
        if len(probe) >= 16 and probe in page_text:
            best = max(best, 0.97 if probe != text else 1.0)
            continue
        ratio = SequenceMatcher(None, probe[:220], page_text[:6000], autojunk=True).ratio()
        best = max(best, ratio)
    return min(1.0, best)


def _is_exact_source_case(records: list[dict[str, Any]], pdf_analysis: dict[str, Any]) -> bool:
    """True when the Markdown sequence corresponds to the complete PDF being analysed.

    This is the normal case for a fresh Mathpix export of the exact PDF extract:
    every PDF page is selected and the Markdown records carry no pre-existing page
    hints. In that case page ownership is a segmentation problem, not 78 unrelated
    page searches.
    """
    if not records or any(record.get("page") is not None for record in records):
        return False
    selected = [int(page) for page in (pdf_analysis.get("selected_pages") or pdf_analysis.get("selectedPages") or [])]
    page_count = int(pdf_analysis.get("page_count") or pdf_analysis.get("pageCount") or 0)
    if not selected or page_count <= 0:
        return False
    return selected == list(range(1, page_count + 1))


def _page_position_centres(pdf_analysis: dict[str, Any], page_numbers: list[int]) -> dict[int, float]:
    """Weak tie-break prior based on the relative amount of physical page content."""
    by_page = {int(page.get("page") or 0): page for page in (pdf_analysis.get("pages", []) or [])}
    weights: list[float] = []
    for page_no in page_numbers:
        page = by_page.get(page_no, {})
        text_chars = 0
        for region in page.get("regions", []) or []:
            if region.get("type") == "text":
                text_chars += len(_norm(str(region.get("text") or "")))
        images = int(page.get("image_region_count") or page.get("imageRegionCount") or 0)
        drawings = int(page.get("drawing_count") or page.get("drawingCount") or 0)
        weights.append(max(1.0, float(text_chars) + images * 180.0 + drawings * 20.0))
    total = sum(weights) or 1.0
    centres: dict[int, float] = {}
    cursor = 0.0
    for page_no, weight in zip(page_numbers, weights):
        centres[page_no] = (cursor + 0.5 * weight) / total
        cursor += weight
    return centres


def _sequence_page_assignment(
    records: list[dict[str, Any]],
    pdf_analysis: dict[str, Any],
    pages: dict[int, str],
) -> dict[str, Any]:
    """Assign one ordered Markdown sequence to ordered PDF pages with Viterbi DP.

    Page ownership is globally monotonic. Text-bearing records provide evidence;
    equations/images may be weak or textless and inherit ownership from their
    sequence neighbourhood instead of independently guessing a page.
    """
    page_numbers = sorted(pages)
    n_records = len(records)
    n_pages = len(page_numbers)
    if not n_records or not n_pages:
        return {"assigned": 0, "boundaries": [], "evidenceRecords": 0, "score": 0.0}

    texts = [_record_text(record) for record in records]
    raw_scores: list[list[float]] = []
    evidence_records = 0
    for text in texts:
        scores = [_anchor_score(text, pages[page_no]) if text else 0.0 for page_no in page_numbers]
        raw_scores.append(scores)
        ordered = sorted(scores, reverse=True)
        best = ordered[0] if ordered else 0.0
        second = ordered[1] if len(ordered) > 1 else 0.0
        if best >= 0.36 and (best - second >= 0.035 or best >= 0.78):
            evidence_records += 1

    centres = _page_position_centres(pdf_analysis, page_numbers)

    # Relative evidence: the best page gets a positive margin; clearly worse
    # pages receive a penalty. Weak/ambiguous records remain nearly neutral and
    # are placed by neighbouring records plus the tiny physical-position prior.
    emissions: list[list[float]] = []
    for i, scores in enumerate(raw_scores):
        ordered = sorted(scores, reverse=True)
        best = ordered[0] if ordered else 0.0
        second = ordered[1] if len(ordered) > 1 else 0.0
        text_len = len(texts[i].split())
        weight = min(2.5, 0.75 + text_len / 18.0)
        strong = best >= 0.36 and (best - second >= 0.035 or best >= 0.78)
        row: list[float] = []
        record_pos = (i + 0.5) / max(1, n_records)
        for p_index, page_no in enumerate(page_numbers):
            if strong:
                if scores[p_index] == best:
                    evidence = (best - second + 0.15 * best) * weight
                else:
                    evidence = -0.42 * max(0.0, best - scores[p_index]) * weight
            else:
                evidence = 0.0
            # Tie-break only; never strong enough to overrule real content.
            position_prior = -0.025 * abs(record_pos - centres.get(page_no, record_pos))
            row.append(evidence + position_prior)
        emissions.append(row)

    neg_inf = -10**18
    dp = [[neg_inf] * n_pages for _ in range(n_records)]
    prev = [[-1] * n_pages for _ in range(n_records)]

    # In an exact-source run the sequence starts on the first content-bearing
    # page. Allow a leading blank page by giving all starts a very small penalty,
    # but strongly prefer the first page.
    for p in range(n_pages):
        dp[0][p] = emissions[0][p] - 0.06 * p

    for i in range(1, n_records):
        best_value = neg_inf
        best_page = 0
        for p in range(n_pages):
            candidate = dp[i - 1][p]
            if candidate > best_value:
                best_value = candidate
                best_page = p
            # Prefix maximum enforces nondecreasing page ownership. A jump over
            # pages is legal for genuinely blank pages but receives a tiny cost.
            jump = p - best_page
            dp[i][p] = best_value + emissions[i][p] - 0.01 * max(0, jump - 1)
            prev[i][p] = best_page

    # Prefer ending near the final PDF page, without forcing a false assignment
    # when the last physical page is blank.
    end_page = max(range(n_pages), key=lambda p: dp[-1][p] - 0.04 * (n_pages - 1 - p))
    assignment = [0] * n_records
    p = end_page
    for i in range(n_records - 1, -1, -1):
        assignment[i] = p
        p = prev[i][p] if i > 0 else p

    for i, page_index in enumerate(assignment):
        page_no = page_numbers[page_index]
        record = records[i]
        record["page"] = page_no
        scores = raw_scores[i]
        ordered = sorted(scores, reverse=True)
        best = ordered[0] if ordered else 0.0
        second = ordered[1] if len(ordered) > 1 else 0.0
        record["pageConfidence"] = "exact-source-sequence"
        record["pageInference"] = {
            "source": VERSION,
            "mode": "exact-source-monotonic-sequence-segmentation",
            "assignedPage": page_no,
            "assignedScore": round(scores[page_index], 5) if scores else 0.0,
            "bestScore": round(best, 5),
            "margin": round(best - second, 5),
        }

    boundaries: list[dict[str, Any]] = []
    start = 0
    current = assignment[0]
    for i in range(1, n_records + 1):
        if i == n_records or assignment[i] != current:
            boundaries.append({
                "page": page_numbers[current],
                "startRecord": start,
                "endRecordExclusive": i,
                "recordCount": i - start,
            })
            if i < n_records:
                start = i
                current = assignment[i]

    counts = Counter(page_numbers[index] for index in assignment)
    return {
        "assigned": n_records,
        "evidenceRecords": evidence_records,
        "score": round(dp[-1][end_page], 5),
        "boundaries": boundaries,
        "recordsPerPage": {str(page): int(counts.get(page, 0)) for page in page_numbers},
    }


def infer_missing_markdown_pages(
    markdown_element_map: dict[str, Any],
    pdf_analysis: dict[str, Any],
) -> dict[str, Any]:
    records = list(markdown_element_map.get("records", []) or [])
    pages = _page_texts(pdf_analysis)
    page_numbers = sorted(pages)
    if not records or not page_numbers:
        return {"version": VERSION, "anchorCount": 0, "inferredCount": 0, "policy": "no-input"}

    # Normal exact-source case: one fresh Mathpix export was produced from the
    # exact PDF being converted. Page ownership is therefore solved globally as
    # one ordered segmentation problem. This also gives page ownership to visual
    # and equation records that cannot establish a page by text alone.
    if _is_exact_source_case(records, pdf_analysis):
        sequence = _sequence_page_assignment(records, pdf_analysis, pages)
        audit = {
            "version": VERSION,
            "policy": "exact source PDF: monotonic Markdown-sequence segmentation into physical PDF pages",
            "mode": "exact-source-monotonic-sequence-segmentation",
            "recordCount": len(records),
            "candidateAnchorCount": 0,
            "anchorCount": int(sequence.get("evidenceRecords") or 0),
            "inferredCount": int(sequence.get("assigned") or 0),
            "unresolvedPageCount": max(0, len(records) - int(sequence.get("assigned") or 0)),
            "coverage": round(int(sequence.get("assigned") or 0) / max(1, len(records)), 5),
            "sequenceScore": sequence.get("score"),
            "recordsPerPage": sequence.get("recordsPerPage", {}),
            "boundaries": sequence.get("boundaries", []),
            "anchors": [],
        }
        markdown_element_map["pageAlignmentFallback"] = audit
        return audit

    candidates: list[dict[str, Any]] = []
    for idx, record in enumerate(records):
        if record.get("page") is not None:
            continue
        if str(record.get("type") or "").lower() not in _TEXT_TYPES:
            continue
        text = _record_text(record)
        if len(text) < 20 or len(text.split()) < 4:
            continue
        scored = sorted(
            ((page_no, _anchor_score(text, page_text)) for page_no, page_text in pages.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        if not scored:
            continue
        best_page, best_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        if best_score < 0.78 or best_score - second_score < 0.08:
            continue
        candidates.append({
            "recordIndex": idx,
            "markdownId": record.get("id"),
            "page": best_page,
            "score": round(best_score, 5),
            "margin": round(best_score - second_score, 5),
            "textPreview": text[:160],
        })

    chain: list[dict[str, Any]] = []
    for candidate in candidates:
        if not chain or int(candidate["page"]) >= int(chain[-1]["page"]):
            chain.append(candidate)

    for anchor in chain:
        record = records[int(anchor["recordIndex"])]
        record["page"] = int(anchor["page"])
        record["pageConfidence"] = "content-anchor-high"
        record["pageInference"] = {
            "source": VERSION,
            "mode": "normalized-token-char-page-anchor",
            "score": anchor["score"],
            "margin": anchor["margin"],
        }

    inferred = len(chain)
    for left, right in zip(chain, chain[1:]):
        li, ri = int(left["recordIndex"]), int(right["recordIndex"])
        lp, rp = int(left["page"]), int(right["page"])
        if ri <= li + 1:
            continue
        span = ri - li
        for idx in range(li + 1, ri):
            record = records[idx]
            if record.get("page") is not None:
                continue
            ratio = (idx - li) / span
            page = int(round(lp + ratio * (rp - lp)))
            page = max(lp, min(rp, page))
            record["page"] = page
            record["pageConfidence"] = "content-anchor-interpolated"
            record["pageInference"] = {
                "source": VERSION,
                "mode": "between-monotonic-text-anchors",
                "anchorPages": [lp, rp],
            }
            inferred += 1

    unresolved = sum(1 for record in records if record.get("page") is None)
    audit = {
        "version": VERSION,
        "policy": "high-confidence normalized token/character page anchors; monotonic chain; interpolate only between trusted anchors",
        "recordCount": len(records),
        "candidateAnchorCount": len(candidates),
        "anchorCount": len(chain),
        "inferredCount": inferred,
        "unresolvedPageCount": unresolved,
        "coverage": round((len(records) - unresolved) / max(1, len(records)), 5),
        "anchors": chain[:80],
    }
    markdown_element_map["pageAlignmentFallback"] = audit
    return audit
