from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from rapidfuzz import fuzz, process

from .common import normalize_text


def _pdf_text_regions(pdf_analysis: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    alignable: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for page in pdf_analysis.get("pages", []):
        for region in page.get("regions", []):
            if region.get("type") != "text":
                continue
            norm = normalize_text(region.get("text", ""))
            semantic = region.get("semantic", {})
            item = {
                "page": page["page"],
                "id": region["id"],
                "text": region.get("text", ""),
                "normalized": norm,
                "bbox": region.get("bbox", [0, 0, 0, 0]),
                "lines": region.get("lines", []),
                "page_height": page.get("height_pt", 842.0),
                "semantic_type": semantic.get("type", "body"),
                "flow_zone": semantic.get("flow_zone", "main"),
                "semantic_confidence": semantic.get("confidence", 0.0),
            }
            if semantic.get("alignable", True) and len(norm) >= 4:
                alignable.append(item)
            else:
                excluded.append(item)
    return alignable, excluded


def _leading_number(text: str) -> str | None:
    m = re.match(r"^\s*(\d+(?:[.,]\d+)*)\s*[.)]?", text)
    return m.group(1) if m else None


def _candidate_window(pdf_regions: list[dict[str, Any]], docx_paras: list[dict[str, Any]]) -> tuple[int, int, list[dict[str, Any]]] | None:
    """Infer one continuous DOCX source window from boundary prose anchors.

    Heading-only discovery was adequate for four pages but becomes unstable on a
    long exercise range: question numbers and repeated headings create many false
    branches.  The wide probe now anchors the *beginning* and *end* of the selected
    PDF range with long prose passages and chooses a plausible monotonic interval.
    """
    if not pdf_regions or not docx_paras:
        return None
    pages = sorted({int(r.get("page", 0)) for r in pdf_regions})
    if not pages:
        return None
    first_pages = set(pages[: min(2, len(pages))])
    last_pages = set(pages[max(0, len(pages) - 2):])

    def eligible(region: dict[str, Any]) -> bool:
        if region.get("flow_zone") != "main":
            return False
        if region.get("semantic_type") not in {"body", "heading", "caption"}:
            return False
        n = len(region.get("normalized", ""))
        return 55 <= n <= 900

    first_regions = [r for r in pdf_regions if int(r.get("page", 0)) in first_pages and eligible(r)]
    last_regions = [r for r in pdf_regions if int(r.get("page", 0)) in last_pages and eligible(r)]
    # Prefer long, information-rich prose; retain document order for traceability.
    first_regions = sorted(first_regions, key=lambda r: len(r["normalized"]), reverse=True)[:6]
    last_regions = sorted(last_regions, key=lambda r: len(r["normalized"]), reverse=True)[:6]
    if not first_regions or not last_regions:
        return None

    choices = {p["id"]: p["normalized"] for p in docx_paras}
    para_by_id = {p["id"]: p for p in docx_paras}

    def top_candidates(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for region in regions:
            raw = process.extract(region["normalized"], choices, scorer=fuzz.WRatio, limit=12, score_cutoff=48)
            for _, score, pid in raw:
                para = para_by_id[pid]
                token = float(fuzz.token_set_ratio(region["normalized"], para["normalized"]))
                ratio = float(fuzz.ratio(region["normalized"], para["normalized"]))
                combined = 0.45 * float(score) + 0.35 * token + 0.20 * ratio
                output.append({
                    "pdf_region": region["id"], "page": region["page"],
                    "index": int(para["index"]), "id": pid,
                    "score": combined, "text": para["text"],
                })
        output.sort(key=lambda c: c["score"], reverse=True)
        # Deduplicate paragraph positions so one repeated PDF fragment cannot dominate.
        unique: list[dict[str, Any]] = []
        seen: set[int] = set()
        for cand in output:
            if cand["index"] in seen:
                continue
            seen.add(cand["index"])
            unique.append(cand)
            if len(unique) >= 30:
                break
        return unique

    starts = top_candidates(first_regions)
    ends = top_candidates(last_regions)
    if not starts or not ends:
        return None

    # A page in these converted textbooks usually occupies roughly 8-22 DOCX
    # paragraphs.  This is only a soft prior; the textual scores remain dominant.
    target_span = max(35, int(len(pages) * 14))
    best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    for a in starts:
        for b in ends:
            span = b["index"] - a["index"]
            if span < 15 or span > 1600:
                continue
            span_penalty = abs(span - target_span) * 0.035
            score = a["score"] + b["score"] - span_penalty
            # Prefer two genuinely strong boundary anchors.
            if min(a["score"], b["score"]) < 58:
                score -= 12
            if best is None or score > best[0]:
                best = (score, a, b)
    if best is None:
        return None
    _, a, b = best
    chain = [a, b]
    return max(0, a["index"] - 22), b["index"] + 35, chain


def _build_docx_spans(window_paras: list[dict[str, Any]], max_span: int = 3) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for pos, para in enumerate(window_paras):
        texts: list[str] = []
        norms: list[str] = []
        ids: list[str] = []
        indexes: list[int] = []
        styles: list[str | None] = []
        previous_index: int | None = None
        for offset in range(max_span):
            if pos + offset >= len(window_paras):
                break
            current = window_paras[pos + offset]
            if previous_index is not None and current["index"] - previous_index > 3:
                break
            previous_index = current["index"]
            if not current["normalized"]:
                continue
            texts.append(current["text"])
            norms.append(current["normalized"])
            ids.append(current["id"])
            indexes.append(current["index"])
            styles.append(current.get("style"))
            normalized = " ".join(norms).strip()
            if not normalized:
                continue
            spans.append({
                "id": f"span-{indexes[0]}-{indexes[-1]}",
                "paragraph_ids": list(ids),
                "indexes": list(indexes),
                "start_index": indexes[0],
                "end_index": indexes[-1],
                "text": "\n".join(texts),
                "normalized": normalized,
                "styles": list(styles),
            })
    return spans


def _text_similarity(pdf_text: str, candidate_text: str) -> float:
    """Fast, deterministic similarity for the already-normalized strings.

    The previous wide-range path called WRatio for every shortlisted span.  On a
    48-page probe that meant tens of thousands of expensive multi-scorer calls
    and could exceed two minutes before the actual Word build even started.
    Ratio plus token-set ratio preserve the strong-match signal used by the
    builder, while avoiding an expensive WRatio call for every candidate.
    """
    ratio = float(fuzz.ratio(pdf_text, candidate_text))
    token = float(fuzz.token_set_ratio(pdf_text, candidate_text))
    score = 0.46 * ratio + 0.54 * token
    if pdf_text and candidate_text and (pdf_text in candidate_text or candidate_text in pdf_text):
        coverage = min(len(pdf_text), len(candidate_text)) / max(len(pdf_text), len(candidate_text))
        score = max(score, 82.0 + 18.0 * coverage)
    return min(100.0, score)


def align_pdf_to_docx(pdf_analysis: dict[str, Any], docx_analysis: dict[str, Any]) -> dict[str, Any]:
    pdf_regions, excluded_regions = _pdf_text_regions(pdf_analysis)
    docx_paras = []
    for p in docx_analysis.get("paragraphs", []):
        norm = normalize_text(p.get("text", ""))
        if len(norm) < 3:
            continue
        docx_paras.append({**p, "normalized": norm})

    window_info = _candidate_window(pdf_regions, docx_paras)
    if window_info is None:
        window_start, window_end = 0, max((p["index"] for p in docx_paras), default=0)
        anchors: list[dict[str, Any]] = []
    else:
        window_start, window_end, anchors = window_info

    window_paras = [p for p in docx_paras if window_start <= p["index"] <= window_end]
    candidate_spans = _build_docx_spans(window_paras, max_span=3)
    choices = {s["id"]: s["normalized"] for s in candidate_spans}
    span_by_id = {s["id"]: s for s in candidate_spans}
    span_tokens: dict[str, set[str]] = {}
    token_index: defaultdict[str, set[str]] = defaultdict(set)
    for span in candidate_spans:
        tokens = {tok for tok in re.findall(r"[\wά-ώΆ-Ώ]+", span["normalized"]) if len(tok) >= 3}
        span_tokens[span["id"]] = tokens
        for token in tokens:
            token_index[token].add(span["id"])

    matches: list[dict[str, Any]] = []
    previous_by_zone: defaultdict[str, int] = defaultdict(lambda: window_start)
    usage: Counter[int] = Counter()

    for region in pdf_regions:
        best: tuple[float, dict[str, Any]] | None = None
        pdf_num = _leading_number(region["normalized"])
        zone = region.get("flow_zone", "main")
        previous_index = previous_by_zone[zone]

        # Wide ranges need a cheap lexical index before fuzzy scoring.  Candidate
        # spans are ranked by shared informative tokens; the local sequence
        # neighbourhood is always included to survive OCR/PDF spelling noise.
        region_tokens = {tok for tok in re.findall(r"[\wά-ώΆ-Ώ]+", region["normalized"]) if len(tok) >= 3}
        lexical_scores: Counter[str] = Counter()
        for token in region_tokens:
            hits = token_index.get(token, set())
            weight = 1.0 / max(1.0, len(hits) ** 0.5)
            for sid in hits:
                lexical_scores[sid] += weight
        # Lexical candidates carry the content signal; a compact asymmetric
        # neighbourhood carries the sequence signal.  The source normally moves
        # forward, so we retain more candidates ahead than behind the cursor.
        lexical_limit = 28 if len(region["normalized"]) >= 90 else 36
        shortlist_ids = {sid for sid, _ in lexical_scores.most_common(lexical_limit)}
        local_min = int(previous_index) - 8
        local_max = int(previous_index) + 18
        for span in candidate_spans:
            start_index = int(span["start_index"])
            if local_min <= start_index <= local_max:
                shortlist_ids.add(span["id"])
        # Very short headings may share only one token; preserve a small source
        # prefix as a last-resort diagnostic fallback without restoring the old
        # 100+ candidate fan-out.
        if len(shortlist_ids) < 10:
            shortlist_ids.update(span["id"] for span in candidate_spans[: min(16, len(candidate_spans))])
        for sid in shortlist_ids:
            span = span_by_id[sid]
            combined = _text_similarity(region["normalized"], span["normalized"])

            if zone == "main" and span["start_index"] + 8 < previous_index:
                combined -= min(22.0, (previous_index - span["start_index"]) * 0.20)
            elif zone != "main" and span["start_index"] + 28 < previous_index:
                combined -= min(10.0, (previous_index - span["start_index"]) * 0.06)

            reuse_count = sum(usage[i] for i in span["indexes"])
            if reuse_count:
                reuse_penalty = 3.0 if region["semantic_type"] == "caption" else 9.0
                combined -= min(24.0, reuse_count * reuse_penalty)

            if region["semantic_type"] == "heading":
                docx_num = _leading_number(span["normalized"])
                if pdf_num and docx_num and pdf_num != docx_num:
                    combined -= 24.0
                if any("heading" in str(style).lower() for style in span.get("styles", []) if style):
                    combined += 3.0

            if region["semantic_type"] == "caption":
                if normalize_text(span["text"]).startswith(("σχήμα ", "εικόνα ", "πίνακας ")):
                    combined += 7.0

            if best is None or combined > best[0]:
                best = (combined, span)

        if best is None or best[0] < 38.0:
            matches.append({
                "pdf_region": region["id"],
                "page": region["page"],
                "bbox": region["bbox"],
                "pdf_text": region["text"],
                "semantic_type": region["semantic_type"],
                "flow_zone": zone,
                "status": "unresolved",
                "score": round(max(0.0, best[0] if best else 0.0), 2),
            })
            continue

        combined, span = best
        combined = round(max(0.0, min(100.0, combined)), 2)
        status = "strong" if combined >= 86 else "medium" if combined >= 70 else "weak" if combined >= 52 else "unresolved"

        # Weak matches are reported for review but must never steer the sequence
        # cursor. One decorative banner or OCR fragment can otherwise jump the
        # whole remaining chapter into a later duplicate branch.
        if status in {"strong", "medium"}:
            for index in span["indexes"]:
                usage[index] += 1
            if span["start_index"] >= previous_index - (8 if zone == "main" else 28):
                previous_by_zone[zone] = max(previous_index, span["end_index"])

        matches.append({
            "pdf_region": region["id"],
            "page": region["page"],
            "bbox": region["bbox"],
            "pdf_text": region["text"],
            "semantic_type": region["semantic_type"],
            "flow_zone": zone,
            "docx_paragraphs": span["paragraph_ids"],
            "docx_indexes": span["indexes"],
            "docx_text": span["text"],
            "docx_styles": span["styles"],
            "score": combined,
            "status": status,
        })

    status_counts = Counter(m["status"] for m in matches)
    excluded_counts = Counter(r.get("semantic_type", "unknown") for r in excluded_regions)
    return {
        "summary": {
            "pdf_text_regions_total": len(pdf_regions) + len(excluded_regions),
            "pdf_alignable_regions": len(pdf_regions),
            "pdf_excluded_regions": len(excluded_regions),
            "docx_candidate_paragraphs_total": len(docx_paras),
            "docx_candidate_paragraphs_in_window": len(window_paras),
            "candidate_span_count": len(candidate_spans),
            "strong": status_counts["strong"],
            "medium": status_counts["medium"],
            "weak": status_counts["weak"],
            "unresolved": status_counts["unresolved"],
            "excluded_by_type": dict(excluded_counts),
            "candidate_docx_paragraph_range": [window_start, window_end],
            "anchor_count": len(anchors),
        },
        "anchors": anchors,
        "matches": matches,
        "excluded_regions": excluded_regions,
    }
