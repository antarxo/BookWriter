from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any


VERSION = "markdown-pdf-page-alignment-0.2"

_TEXT_TYPES = {
    "paragraph", "heading", "title", "caption", "callout", "list", "latex_list",
    "list_item", "ordered_list", "unordered_list", "text",
}


def _norm(value: str) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"\\[a-zA-Z]+", " ", text)
    text = re.sub(r"[^0-9a-zα-ωά-ώ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _record_text(record: dict[str, Any]) -> str:
    authoritative = record.get("authoritativeContent") if isinstance(record.get("authoritativeContent"), dict) else {}
    for value in (
        authoritative.get("text"), authoritative.get("plainText"),
        record.get("text"), record.get("textPreview"), record.get("rawMarkdown"),
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
    """How much of one Markdown text block is witnessed by one PDF page.

    This is deliberately asymmetric: a paragraph should be mostly explainable by
    a page, while the page is naturally much longer than the paragraph.  The old
    whole-string SequenceMatcher ratio penalized exactly that length asymmetry and
    could yield zero trusted page anchors for a clean short Mathpix extract.
    """
    words = text.split()
    page_words = page_text.split()
    if len(words) < 5 or not page_words:
        return 0.0

    # Ordered token witness.  Hyphenation/line-break differences may split a few
    # words, so do not require literal substring equality.
    matcher = SequenceMatcher(None, words, page_words, autojunk=False)
    ordered = sum(block.size for block in matcher.get_matching_blocks()) / max(1, len(words))

    # Bag-of-words witness is secondary and robust to PDF extraction order around
    # side notes.  Count multiplicity so repeated common words do not over-score.
    need = Counter(words)
    have = Counter(page_words)
    overlap = sum(min(count, have.get(token, 0)) for token, count in need.items())
    bag = overlap / max(1, len(words))

    # Ordered evidence carries more authority; bag overlap only rescues benign
    # extraction reordering and word wrapping.
    return min(1.0, 0.72 * ordered + 0.28 * bag)


def _anchor_score(text: str, page_text: str) -> float:
    if not text or not page_text:
        return 0.0
    # Exact normalized containment is the strongest witness.
    if len(text) >= 24 and text in page_text:
        return 1.0
    words = text.split()
    if len(words) < 5 or len(text) < 28:
        return 0.0

    token_score = _token_coverage(text, page_text)

    # Compare a bounded leading/trailing signature as an additional witness.
    probes = [text]
    if len(words) >= 10:
        probes.extend([" ".join(words[:10]), " ".join(words[-10:])])
    best = token_score
    for probe in probes:
        if len(probe) >= 20 and probe in page_text:
            best = max(best, 0.97 if probe != text else 1.0)
            continue
        ratio = SequenceMatcher(None, probe[:220], page_text[:6000], autojunk=True).ratio()
        best = max(best, ratio)
    return best


def infer_missing_markdown_pages(
    markdown_element_map: dict[str, Any],
    pdf_analysis: dict[str, Any],
) -> dict[str, Any]:
    records = list(markdown_element_map.get("records", []) or [])
    pages = _page_texts(pdf_analysis)
    page_numbers = sorted(pages)
    if not records or not page_numbers:
        return {"version": VERSION, "anchorCount": 0, "inferredCount": 0, "policy": "no-input"}

    candidates: list[dict[str, Any]] = []
    for idx, record in enumerate(records):
        if record.get("page") is not None:
            continue
        if str(record.get("type") or "").lower() not in _TEXT_TYPES:
            continue
        text = _record_text(record)
        if len(text) < 28 or len(text.split()) < 5:
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
        # High precision only. Token coverage is asymmetric, so 0.82 already
        # means that most paragraph tokens are witnessed by one page. Keep a
        # useful margin so repeated boilerplate cannot become a page anchor.
        if best_score < 0.82 or best_score - second_score < 0.10:
            continue
        candidates.append({
            "recordIndex": idx,
            "markdownId": record.get("id"),
            "page": best_page,
            "score": round(best_score, 5),
            "margin": round(best_score - second_score, 5),
            "textPreview": text[:160],
        })

    # Longest monotonic chain by record order/page number. This rejects isolated
    # fuzzy matches that would reverse document order.
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
            "mode": "normalized-token-page-anchor",
            "score": anchor["score"],
            "margin": anchor["margin"],
        }

    inferred = len(chain)
    # Interpolate only between trusted anchors. Outside the anchored interval,
    # leave unresolved rather than inventing pagination.
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
        "policy": "high-confidence normalized token/page anchors; monotonic chain; interpolate only between trusted anchors",
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
