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
TOP_K = 5


def _prepare_md(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for record in records:
        if str(record.get("type") or "") in {"table", "latex_table"}:
            continue
        item = dict(record)
        text = _markdown_text(record)
        item["__normalizedText"] = normalize_text(text)
        item["__mathSignature"] = _math_signature(str(record.get("latex") or text))
        out.append(item)
    return out


def _prepare_docx(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
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


def _unique_monotonic_anchors(md: list[dict[str, Any]], dx: list[dict[str, Any]]) -> list[tuple[int, int]]:
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
    pairs = sorted((m[0], di[k][0]) for k, m in mi.items() if len(m) == 1 and len(di.get(k, [])) == 1)
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
    lis: list[tuple[int, int]] = []
    while k >= 0:
        lis.append(pairs[k])
        k = prev[k]
    return list(reversed(lis))


def _align_segment(md: list[dict[str, Any]], dx: list[dict[str, Any]], m0: int, m1: int, d0: int, d1: int, metrics: Counter[str]) -> list[tuple[int, int, float]]:
    ms = md[m0:m1]
    ds = dx[d0:d1]
    n, p = len(ms), len(ds)
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
        for j in range(1, p + 1):
            metrics["pairScores"] += 1
            s = _score(ms[i - 1], ds[j - 1])
            match = dp[i - 1][j - 1] + (s - 50.0)
            skip_m = dp[i - 1][j] + GAP_MD
            skip_d = dp[i][j - 1] + GAP_DOCX
            best = max(match, skip_m, skip_d)
            dp[i][j] = best
            bt[i][j] = 0 if best == match else (1 if best == skip_m else 2)
    out = []
    i, j = n, p
    while i or j:
        op = bt[i][j]
        if i and j and op == 0:
            metrics["pairScores"] += 1
            s = _score(ms[i - 1], ds[j - 1])
            if s >= MIN_ACCEPT:
                out.append((m0 + i - 1, d0 + j - 1, s))
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
    anchors = _unique_monotonic_anchors(md, dx)
    metrics: Counter[str] = Counter()
    chosen: dict[int, tuple[int, float, str]] = {}
    for m, d in anchors:
        chosen[m] = (d, 100.0, "exact-anchor")
    boundaries = [(-1, -1)] + anchors + [(len(md), len(dx))]
    for (lm, ld), (rm, rd) in zip(boundaries, boundaries[1:]):
        if rm <= lm + 1 or rd <= ld + 1:
            continue
        metrics["segments"] += 1
        metrics["maxMarkdownSegment"] = max(metrics["maxMarkdownSegment"], rm - lm - 1)
        metrics["maxDocxSegment"] = max(metrics["maxDocxSegment"], rd - ld - 1)
        for mi, di, score in _align_segment(md, dx, lm + 1, rm, ld + 1, rd, metrics):
            chosen.setdefault(mi, (di, score, "sequence"))

    new_assoc = []
    for mi, item in enumerate(md):
        hit = chosen.get(mi)
        if hit is None:
            new_assoc.append({
                "markdownId": item.get("id"),
                "markdownType": item.get("type"),
                "orderIndex": item.get("orderIndex"),
                "status": "unresolved",
                "stage": "sequence-unmatched",
                "selected": None,
                "candidates": [],
            })
        else:
            di, score, stage = hit
            new_assoc.append(_payload(item, dx[di], score, stage))

    old_tables = [row for row in donor.get("markdownAssociations", []) or [] if str(row.get("markdownType") or "") in {"table", "latex_table"}]
    new_assoc.extend(old_tables)
    donor["version"] = "docx-donor-map-sequence-0.1"
    donor.setdefault("policy", {})["association"] = "monotonic-anchor-sequence-alignment"
    donor["markdownAssociations"] = new_assoc
    donor["byMarkdown"] = {str(row.get("markdownId") or ""): row for row in new_assoc if row.get("markdownId")}
    counts = Counter(str(row.get("status") or "unknown") for row in new_assoc)
    donor.setdefault("summary", {})["associationCount"] = len(new_assoc)
    donor["summary"]["associationStatusCounts"] = dict(counts)
    donor["sequenceDiagnostics"] = {
        "seconds": round(time.perf_counter() - t0, 3),
        "anchorCount": len(anchors),
        "matchedNonTable": sum(1 for i in range(len(md)) if i in chosen),
        "unmatchedNonTable": sum(1 for i in range(len(md)) if i not in chosen),
        "segments": int(metrics["segments"]),
        "pairScores": int(metrics["pairScores"]),
        "maxMarkdownSegment": int(metrics["maxMarkdownSegment"]),
        "maxDocxSegment": int(metrics["maxDocxSegment"]),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(donor, ensure_ascii=False, indent=2), encoding="utf-8")
    return donor


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a monotonic sequence donor map from the existing donor inventory.")
    ap.add_argument("--old-donor-map", required=True, type=Path)
    ap.add_argument("--markdown-map", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    donor = build(args.old_donor_map, args.markdown_map, args.output)
    print(json.dumps(donor["sequenceDiagnostics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
