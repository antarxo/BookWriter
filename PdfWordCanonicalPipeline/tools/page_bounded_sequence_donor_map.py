from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import json
from pathlib import Path
import time
from typing import Any

from rapidfuzz import fuzz

from pdf_word_reconstructor.common import normalize_text
from pdf_word_reconstructor.docx_donor_map import (
    _association_status,
    _donor_type,
    _markdown_text,
    _math_signature,
    _semantic_compatible,
)

GAP_MD = -32.0
GAP_DOCX = -18.0
MIN_ACCEPT = 45.0


def _page_of(record: dict[str, Any]) -> int | None:
    for key in ("page", "pageNumber", "sourcePage", "pdfPage"):
        value = record.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    auth = record.get("authoritativeContent")
    if isinstance(auth, dict):
        for key in ("page", "pageNumber", "sourcePage", "pdfPage"):
            try:
                value = auth.get(key)
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                pass
    return None


def _prepare_md(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("type") or "") in {"table", "latex_table"}:
            continue
        item = dict(record)
        text = _markdown_text(record)
        item["__normalizedText"] = normalize_text(text)
        item["__mathSignature"] = _math_signature(str(record.get("latex") or text))
        item["__page"] = _page_of(record)
        out.append(item)
    return out


def _prepare_docx(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        item = dict(paragraph)
        normalized = str(paragraph.get("normalizedText") or normalize_text(paragraph.get("text", "")))
        item["normalizedText"] = normalized
        item["donorType"] = str(paragraph.get("donorType") or _donor_type({**paragraph, "normalizedText": normalized}))
        item["ommlSignature"] = str(paragraph.get("ommlSignature") or _math_signature(str(paragraph.get("ommlText") or paragraph.get("omml_text") or "")))
        out.append(item)
    return out


def _md_key(item: dict[str, Any]) -> str:
    kind = str(item.get("type") or "")
    if kind in {"display_equation", "equation"}:
        return str(item.get("__mathSignature") or "")
    return str(item.get("__normalizedText") or "")


def _docx_key(item: dict[str, Any]) -> str:
    dtype = str(item.get("donorType") or "")
    if dtype in {"math-omml", "mixed-omml"} and item.get("ommlSignature"):
        return str(item.get("ommlSignature") or "")
    return str(item.get("normalizedText") or "")


def _score(md: dict[str, Any], dx: dict[str, Any]) -> float:
    kind = str(md.get("type") or "")
    dtype = str(dx.get("donorType") or "")
    if not _semantic_compatible(kind, dtype):
        return 0.0
    if kind in {"display_equation", "equation"}:
        a = str(md.get("__mathSignature") or "")
        b = str(dx.get("ommlSignature") or "")
        return float(fuzz.ratio(a, b)) if a and b else 0.0
    if kind in {"image", "figure"} and dtype == "visual":
        a = str(md.get("__normalizedText") or "")
        b = str(dx.get("normalizedText") or "")
        if not a or not b:
            return 82.0
    a = str(md.get("__normalizedText") or "")
    b = str(dx.get("normalizedText") or "")
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    return min(100.0, 0.50 * fuzz.ratio(a, b) + 0.30 * fuzz.partial_ratio(a, b) + 0.20 * fuzz.token_set_ratio(a, b))


def _all_unique_exact(md: list[dict[str, Any]], dx: list[dict[str, Any]]) -> list[tuple[int, int]]:
    mi: dict[str, list[int]] = defaultdict(list)
    di: dict[str, list[int]] = defaultdict(list)
    for i, item in enumerate(md):
        key = _md_key(item)
        if key:
            mi[key].append(i)
    for i, item in enumerate(dx):
        key = _docx_key(item)
        if key:
            di[key].append(i)
    return sorted((m[0], di[k][0]) for k, m in mi.items() if len(m) == 1 and len(di.get(k, [])) == 1)


def _monotonic_subset(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not pairs:
        return []
    tails: list[int] = []
    tails_pos: list[int] = []
    prev = [-1] * len(pairs)
    for i, (_m, d) in enumerate(pairs):
        pos = bisect_left(tails, d)
        if pos == len(tails):
            tails.append(d)
            tails_pos.append(i)
        else:
            tails[pos] = d
            tails_pos[pos] = i
        if pos:
            prev[i] = tails_pos[pos - 1]
    k = tails_pos[-1]
    out: list[tuple[int, int]] = []
    while k >= 0:
        out.append(pairs[k])
        k = prev[k]
    return list(reversed(out))


def _page_bounds(md: list[dict[str, Any]], dx_count: int, anchors: list[tuple[int, int]]) -> dict[int, tuple[int, int]]:
    pages = sorted({int(item["__page"]) for item in md if item.get("__page") is not None})
    by_page: dict[int, list[int]] = defaultdict(list)
    for mi, di in anchors:
        page = md[mi].get("__page")
        if page is not None:
            by_page[int(page)].append(di)

    bounds: dict[int, tuple[int, int]] = {}
    for pos, page in enumerate(pages):
        cur = sorted(by_page.get(page, []))
        prev = []
        nxt = []
        for pp in reversed(pages[:pos]):
            if by_page.get(pp):
                prev = sorted(by_page[pp])
                break
        for np in pages[pos + 1:]:
            if by_page.get(np):
                nxt = sorted(by_page[np])
                break

        if cur:
            lo = 0 if not prev else max(0, (prev[-1] + cur[0]) // 2 + 1)
            hi = dx_count if not nxt else min(dx_count, (cur[-1] + nxt[0]) // 2 + 1)
        elif prev and nxt:
            lo = max(0, prev[-1] + 1)
            hi = min(dx_count, nxt[0])
        elif prev:
            lo = max(0, prev[-1] + 1)
            hi = dx_count
        elif nxt:
            lo = 0
            hi = min(dx_count, nxt[0])
        else:
            lo, hi = 0, dx_count

        if hi <= lo:
            hi = min(dx_count, lo + 1)
        bounds[page] = (lo, hi)
    return bounds


def _align_page(md: list[dict[str, Any]], dx: list[dict[str, Any]], md_indexes: list[int], d0: int, d1: int, metrics: Counter[str]) -> list[tuple[int, int, float]]:
    ds = dx[d0:d1]
    n, p = len(md_indexes), len(ds)
    if not n or not p:
        return []
    dp = [[0.0] * (p + 1) for _ in range(n + 1)]
    bt = [[0] * (p + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + GAP_MD
        bt[i][0] = 1
    for j in range(1, p + 1):
        dp[0][j] = dp[0][j - 1] + GAP_DOCX
        bt[0][j] = 2
    for i in range(1, n + 1):
        mdi = md_indexes[i - 1]
        for j in range(1, p + 1):
            metrics["pairScores"] += 1
            s = _score(md[mdi], ds[j - 1])
            match = dp[i - 1][j - 1] + (s - 50.0)
            skip_m = dp[i - 1][j] + GAP_MD
            skip_d = dp[i][j - 1] + GAP_DOCX
            best = max(match, skip_m, skip_d)
            dp[i][j] = best
            bt[i][j] = 0 if best == match else (1 if best == skip_m else 2)
    out: list[tuple[int, int, float]] = []
    i, j = n, p
    while i or j:
        op = bt[i][j]
        if i and j and op == 0:
            mdi = md_indexes[i - 1]
            s = _score(md[mdi], ds[j - 1])
            if s >= MIN_ACCEPT:
                out.append((mdi, d0 + j - 1, s))
            i -= 1
            j -= 1
        elif i and (j == 0 or op == 1):
            i -= 1
        else:
            j -= 1
    return list(reversed(out))


def _payload(md: dict[str, Any], dx: dict[str, Any], score: float, stage: str) -> dict[str, Any]:
    dtype = str(dx.get("donorType") or "")
    status = _association_status(score, str(md.get("type") or ""), dtype)
    selected = {
        "paragraphId": dx.get("id"),
        "paragraphIndex": dx.get("index"),
        "donorType": dtype,
        "score": round(float(score), 2),
        "locator": dx.get("locator"),
    }
    return {
        "markdownId": md.get("id"),
        "markdownType": md.get("type"),
        "orderIndex": md.get("orderIndex"),
        "page": md.get("__page"),
        "status": status,
        "stage": stage,
        "selected": selected,
        "candidates": [selected],
    }


def build(old_map: Path, markdown_map: Path, output: Path) -> dict[str, Any]:
    t0 = time.perf_counter()
    donor = json.loads(old_map.read_text(encoding="utf-8"))
    markdown_raw = json.loads(markdown_map.read_text(encoding="utf-8"))
    md = _prepare_md(markdown_raw.get("records", []) or [])
    dx = _prepare_docx(donor.get("paragraphs", []) or [])

    anchors = _monotonic_subset(_all_unique_exact(md, dx))
    bounds = _page_bounds(md, len(dx), anchors)
    metrics: Counter[str] = Counter()
    chosen: dict[int, tuple[int, float, str]] = {}

    for mi, di in anchors:
        page = md[mi].get("__page")
        if page is not None:
            lo, hi = bounds[int(page)]
            if lo <= di < hi:
                chosen[mi] = (di, 100.0, "page-exact-anchor")

    md_by_page: dict[int, list[int]] = defaultdict(list)
    for mi, item in enumerate(md):
        if item.get("__page") is not None:
            md_by_page[int(item["__page"])].append(mi)

    page_diagnostics: dict[str, Any] = {}
    for page in sorted(md_by_page):
        lo, hi = bounds[page]
        indexes = md_by_page[page]
        metrics["pages"] += 1
        metrics["maxMarkdownPageItems"] = max(metrics["maxMarkdownPageItems"], len(indexes))
        metrics["maxDocxPageCandidates"] = max(metrics["maxDocxPageCandidates"], hi - lo)
        aligned = _align_page(md, dx, indexes, lo, hi, metrics)
        for mi, di, score in aligned:
            chosen.setdefault(mi, (di, score, "page-sequence"))
        page_diagnostics[str(page)] = {
            "markdownItems": len(indexes),
            "docxCandidateStart": lo,
            "docxCandidateEndExclusive": hi,
            "docxCandidates": hi - lo,
            "anchorCount": sum(1 for mi, _di in anchors if md[mi].get("__page") == page),
            "matched": sum(1 for mi in indexes if mi in chosen),
            "unmatched": sum(1 for mi in indexes if mi not in chosen),
        }

    new_assoc: list[dict[str, Any]] = []
    for mi, item in enumerate(md):
        hit = chosen.get(mi)
        if hit is None:
            new_assoc.append({
                "markdownId": item.get("id"),
                "markdownType": item.get("type"),
                "orderIndex": item.get("orderIndex"),
                "page": item.get("__page"),
                "status": "unresolved",
                "stage": "page-sequence-unmatched",
                "selected": None,
                "candidates": [],
            })
        else:
            di, score, stage = hit
            new_assoc.append(_payload(item, dx[di], score, stage))

    old_tables = [row for row in donor.get("markdownAssociations", []) or [] if str(row.get("markdownType") or "") in {"table", "latex_table"}]
    new_assoc.extend(old_tables)

    donor["version"] = "docx-donor-map-page-bounded-sequence-0.1"
    donor.setdefault("policy", {})["association"] = "pdf-page-bounded-monotonic-sequence-alignment"
    donor["markdownAssociations"] = new_assoc
    donor["byMarkdown"] = {str(row.get("markdownId") or ""): row for row in new_assoc if row.get("markdownId")}
    counts = Counter(str(row.get("status") or "unknown") for row in new_assoc)
    donor.setdefault("summary", {})["associationCount"] = len(new_assoc)
    donor["summary"]["associationStatusCounts"] = dict(counts)
    donor["pageBoundedDiagnostics"] = {
        "seconds": round(time.perf_counter() - t0, 3),
        "anchorCount": len(anchors),
        "matchedNonTable": sum(1 for i in range(len(md)) if i in chosen),
        "unmatchedNonTable": sum(1 for i in range(len(md)) if i not in chosen),
        "pages": int(metrics["pages"]),
        "pairScores": int(metrics["pairScores"]),
        "maxMarkdownPageItems": int(metrics["maxMarkdownPageItems"]),
        "maxDocxPageCandidates": int(metrics["maxDocxPageCandidates"]),
        "pageBounds": {str(page): {"start": lo, "endExclusive": hi} for page, (lo, hi) in bounds.items()},
        "perPage": page_diagnostics,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(donor, ensure_ascii=False, indent=2), encoding="utf-8")
    return donor


def main() -> None:
    ap = argparse.ArgumentParser(description="Build an experimental donor map with PDF-page-bounded DOCX sequence alignment.")
    ap.add_argument("--old-donor-map", required=True, type=Path)
    ap.add_argument("--markdown-map", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    donor = build(args.old_donor_map, args.markdown_map, args.output)
    print(json.dumps(donor["pageBoundedDiagnostics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
