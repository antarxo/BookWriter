from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import time
from typing import Any

from rapidfuzz import fuzz

from pdf_word_reconstructor.common import normalize_text
from pdf_word_reconstructor.docx_donor_map import (
    _donor_type,
    _markdown_text,
    _math_signature,
    _semantic_compatible,
)


WINDOWS = (8, 24, 64, 160)
TOP_K = 5
MIN_CANDIDATE_SCORE = 45.0
STOP_SCORE = 88.0


def _prepare_markdown(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("type") or "") in {"table", "latex_table"}:
            continue
        item = dict(record)
        text = _markdown_text(record)
        item["__matchText"] = text
        item["__normalizedText"] = normalize_text(text)
        item["__mathSignature"] = _math_signature(str(record.get("latex") or text))
        out.append(item)
    return out


def _prepare_paragraphs(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        item = dict(paragraph)
        normalized = normalize_text(paragraph.get("text", ""))
        item["normalizedText"] = normalized
        item["donorType"] = str(paragraph.get("donorType") or _donor_type({**paragraph, "normalizedText": normalized}))
        item["ommlSignature"] = _math_signature(str(paragraph.get("omml_text") or paragraph.get("ommlText") or ""))
        out.append(item)
    return out


def _key_md(item: dict[str, Any]) -> str:
    kind = str(item.get("type") or "")
    if kind in {"display_equation", "equation"}:
        return str(item.get("__mathSignature") or "")
    return str(item.get("__normalizedText") or "")


def _key_docx(item: dict[str, Any]) -> str:
    dtype = str(item.get("donorType") or "")
    if dtype in {"math-omml", "mixed-omml"} and item.get("ommlSignature"):
        return str(item.get("ommlSignature") or "")
    return str(item.get("normalizedText") or "")


def _score(md: dict[str, Any], paragraph: dict[str, Any]) -> float:
    kind = str(md.get("type") or "")
    donor_type = str(paragraph.get("donorType") or "")
    if not _semantic_compatible(kind, donor_type):
        return 0.0
    if kind in {"display_equation", "equation"}:
        source = str(md.get("__mathSignature") or "")
        target = str(paragraph.get("ommlSignature") or "")
        if not source or not target:
            return 0.0
        return float(fuzz.ratio(source, target))
    source = str(md.get("__normalizedText") or "")
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


def _exact_index(items: list[dict[str, Any]], key_fn) -> dict[str, list[int]]:
    index: dict[str, list[int]] = defaultdict(list)
    for i, item in enumerate(items):
        key = key_fn(item)
        if key:
            index[key].append(i)
    return dict(index)


def _top_candidates(
    md: dict[str, Any],
    paragraphs: list[dict[str, Any]],
    indexes: list[int],
    metrics: Counter[str],
) -> list[tuple[float, int]]:
    scored: list[tuple[float, int]] = []
    seen: set[int] = set()
    for idx in indexes:
        if idx < 0 or idx >= len(paragraphs) or idx in seen:
            continue
        seen.add(idx)
        metrics["candidateComparisons"] += 1
        score = _score(md, paragraphs[idx])
        if score >= MIN_CANDIDATE_SCORE:
            scored.append((score, idx))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return scored[:TOP_K]


def _anchor_centers(
    markdown: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    md_exact: dict[str, list[int]],
    docx_exact: dict[str, list[int]],
) -> dict[int, int]:
    anchors: dict[int, int] = {}
    for mi, md in enumerate(markdown):
        key = _key_md(md)
        if not key:
            continue
        m_hits = md_exact.get(key, [])
        d_hits = docx_exact.get(key, [])
        if len(m_hits) == 1 and len(d_hits) == 1:
            anchors[mi] = d_hits[0]
    return anchors


def _interpolated_center(mi: int, anchors: dict[int, int], md_count: int, docx_count: int) -> int:
    if not anchors:
        if md_count <= 1:
            return 0
        return round((mi / max(1, md_count - 1)) * max(0, docx_count - 1))
    left = [(m, d) for m, d in anchors.items() if m <= mi]
    right = [(m, d) for m, d in anchors.items() if m >= mi]
    if left and right:
        lm, ld = max(left)
        rm, rd = min(right)
        if rm == lm:
            return ld
        t = (mi - lm) / (rm - lm)
        return round(ld + t * (rd - ld))
    if left:
        lm, ld = max(left)
        return min(docx_count - 1, max(0, ld + (mi - lm)))
    rm, rd = min(right)
    return min(docx_count - 1, max(0, rd - (rm - mi)))


def _adaptive_md_to_docx(markdown: list[dict[str, Any]], paragraphs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter[str]]:
    metrics: Counter[str] = Counter()
    md_exact = _exact_index(markdown, _key_md)
    docx_exact = _exact_index(paragraphs, _key_docx)
    anchors = _anchor_centers(markdown, paragraphs, md_exact, docx_exact)
    metrics["uniqueExactAnchors"] = len(anchors)

    results: list[dict[str, Any]] = []
    last_selected = -1
    for mi, md in enumerate(markdown):
        key = _key_md(md)
        candidates: list[tuple[float, int]] = []
        stage = "unresolved"

        exact_hits = docx_exact.get(key, []) if key else []
        compatible_exact = [
            idx for idx in exact_hits
            if _semantic_compatible(str(md.get("type") or ""), str(paragraphs[idx].get("donorType") or ""))
        ]
        if len(compatible_exact) == 1:
            metrics["exactMatches"] += 1
            candidates = [(100.0, compatible_exact[0])]
            stage = "exact"
        else:
            center = _interpolated_center(mi, anchors, len(markdown), len(paragraphs))
            if last_selected >= 0:
                center = max(center, last_selected)
            for radius in WINDOWS:
                lo = max(0, center - radius)
                hi = min(len(paragraphs), center + radius + 1)
                window = list(range(lo, hi))
                candidates = _top_candidates(md, paragraphs, window, metrics)
                metrics[f"window{radius}Searches"] += 1
                if candidates and candidates[0][0] >= STOP_SCORE:
                    stage = f"local-{radius}"
                    metrics["localMatches"] += 1
                    break
            if not candidates or candidates[0][0] < STOP_SCORE:
                metrics["globalFallbacks"] += 1
                candidates = _top_candidates(md, paragraphs, list(range(len(paragraphs))), metrics)
                stage = "global" if candidates else "unresolved"

        selected = candidates[0] if candidates else None
        if selected:
            last_selected = max(last_selected, selected[1])
        results.append({
            "markdownId": md.get("id"),
            "markdownType": md.get("type"),
            "orderIndex": md.get("orderIndex"),
            "stage": stage,
            "selected": None if not selected else {
                "paragraphId": paragraphs[selected[1]].get("id"),
                "paragraphIndex": paragraphs[selected[1]].get("index"),
                "donorType": paragraphs[selected[1]].get("donorType"),
                "score": round(selected[0], 2),
                "arrayIndex": selected[1],
            },
            "candidates": [
                {
                    "paragraphId": paragraphs[idx].get("id"),
                    "paragraphIndex": paragraphs[idx].get("index"),
                    "donorType": paragraphs[idx].get("donorType"),
                    "score": round(score, 2),
                    "arrayIndex": idx,
                }
                for score, idx in candidates[:TOP_K]
            ],
        })
    return results, metrics


def _reverse_best(markdown: list[dict[str, Any]], paragraphs: list[dict[str, Any]], forward: list[dict[str, Any]], metrics: Counter[str]) -> dict[int, int]:
    # Reverse witness uses the forward neighborhood first, then a global fallback only when needed.
    md_by_para: dict[int, list[int]] = defaultdict(list)
    for mi, row in enumerate(forward):
        for cand in row.get("candidates") or []:
            idx = int(cand.get("arrayIndex"))
            md_by_para[idx].append(mi)

    reverse: dict[int, int] = {}
    for pi, paragraph in enumerate(paragraphs):
        pool = set(md_by_para.get(pi, []))
        if not pool:
            # proportional local reverse window
            center = round((pi / max(1, len(paragraphs) - 1)) * max(0, len(markdown) - 1)) if paragraphs else 0
            for radius in (8, 24, 64):
                lo = max(0, center - radius)
                hi = min(len(markdown), center + radius + 1)
                pool.update(range(lo, hi))
                if pool:
                    break
        scored: list[tuple[float, int]] = []
        for mi in sorted(pool):
            metrics["reverseComparisons"] += 1
            score = _score(markdown[mi], paragraph)
            if score >= MIN_CANDIDATE_SCORE:
                scored.append((score, mi))
        if not scored:
            continue
        scored.sort(key=lambda row: (-row[0], row[1]))
        reverse[pi] = scored[0][1]
    return reverse


def _load_old_selected(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for row in data.get("markdownAssociations", []) or []:
        selected = row.get("selected") or {}
        mid = str(row.get("markdownId") or "")
        pid = str(selected.get("paragraphId") or "")
        if mid and pid:
            out[mid] = pid
    return out


def run(docx_inventory: Path, markdown_map: Path, output: Path, old_map: Path | None) -> dict[str, Any]:
    t0 = time.perf_counter()
    docx = json.loads(docx_inventory.read_text(encoding="utf-8"))
    markdown_raw = json.loads(markdown_map.read_text(encoding="utf-8"))
    paragraphs = _prepare_paragraphs(docx.get("paragraphs", []) or [])
    markdown = _prepare_markdown(markdown_raw.get("records", []) or [])
    load_seconds = time.perf_counter() - t0

    t1 = time.perf_counter()
    forward, metrics = _adaptive_md_to_docx(markdown, paragraphs)
    reverse = _reverse_best(markdown, paragraphs, forward, metrics)
    match_seconds = time.perf_counter() - t1

    reciprocal = 0
    disagreements = 0
    unmatched = 0
    for mi, row in enumerate(forward):
        selected = row.get("selected")
        if not selected:
            unmatched += 1
            continue
        pi = int(selected["arrayIndex"])
        if reverse.get(pi) == mi:
            reciprocal += 1
            row["reciprocal"] = True
        else:
            disagreements += 1
            row["reciprocal"] = False
            if pi in reverse:
                row["reverseMarkdownId"] = markdown[reverse[pi]].get("id")

    old_selected = _load_old_selected(old_map)
    old_same = old_changed = old_missing = 0
    for row in forward:
        mid = str(row.get("markdownId") or "")
        pid = str((row.get("selected") or {}).get("paragraphId") or "")
        if not mid or mid not in old_selected:
            old_missing += 1
        elif pid == old_selected[mid]:
            old_same += 1
        else:
            old_changed += 1

    report = {
        "version": "adaptive-donor-benchmark-0.1",
        "inputs": {
            "docxInventory": str(docx_inventory),
            "markdownMap": str(markdown_map),
            "oldDonorMap": str(old_map) if old_map else None,
        },
        "summary": {
            "paragraphCount": len(paragraphs),
            "markdownRecordCount": len(markdown),
            "loadSeconds": round(load_seconds, 3),
            "matchSeconds": round(match_seconds, 3),
            "totalSeconds": round(time.perf_counter() - t0, 3),
            "candidateComparisons": int(metrics["candidateComparisons"]),
            "reverseComparisons": int(metrics["reverseComparisons"]),
            "uniqueExactAnchors": int(metrics["uniqueExactAnchors"]),
            "exactMatches": int(metrics["exactMatches"]),
            "localMatches": int(metrics["localMatches"]),
            "globalFallbacks": int(metrics["globalFallbacks"]),
            "reciprocalMatches": reciprocal,
            "reciprocalDisagreements": disagreements,
            "unmatchedMarkdown": unmatched,
            "oldMapSameSelected": old_same,
            "oldMapChangedSelected": old_changed,
            "oldMapMissingComparison": old_missing,
            "windowSearches": {str(r): int(metrics[f"window{r}Searches"]) for r in WINDOWS},
        },
        "forward": forward,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated benchmark for adaptive DOCX↔Markdown donor matching.")
    parser.add_argument("--docx-inventory", required=True, type=Path)
    parser.add_argument("--markdown-map", required=True, type=Path)
    parser.add_argument("--old-donor-map", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.docx_inventory, args.markdown_map, args.output, args.old_donor_map)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
