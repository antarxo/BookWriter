from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pdf_word_reconstructor.common import normalize_text
from pdf_word_reconstructor.docx_analyzer import analyze_docx
from pdf_word_reconstructor.docx_donor_map import (
    _donor_type,
    _match_score,
    _prepare_markdown_record,
)


VERSION = "mathpix-markdown-docx-benchmark-0.1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bidirectional Mathpix Markdown <-> Mathpix DOCX association benchmark"
    )
    parser.add_argument("--markdown-map", required=True, type=Path)
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--top", type=int, default=3)
    return parser


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _prepare_docx_paragraphs(docx_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for paragraph in docx_analysis.get("paragraphs", []) or []:
        row = dict(paragraph)
        row["normalizedText"] = normalize_text(str(paragraph.get("text") or ""))
        row["donorType"] = _donor_type(row)
        result.append(row)
    return result


def _meaningful_docx(paragraph: dict[str, Any]) -> bool:
    return bool(
        str(paragraph.get("normalizedText") or "").strip()
        or int(paragraph.get("omml_count", paragraph.get("ommlCount", 0)) or 0)
        or int(paragraph.get("drawing_count", paragraph.get("drawingCount", 0)) or 0)
        or paragraph.get("numbering")
    )


def _classify(best: float, second: float) -> str:
    margin = best - second
    if best >= 97.0 and margin >= 4.0:
        return "exact"
    if best >= 88.0 and margin >= 5.0:
        return "strong"
    if best >= 74.0 and margin >= 5.0:
        return "usable"
    if best >= 62.0:
        return "ambiguous"
    return "unmatched"


def _score_matrix(
    markdown: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
) -> list[list[float]]:
    return [
        [float(_match_score(record, paragraph)) for paragraph in paragraphs]
        for record in markdown
    ]


def _rank_row(scores: list[float], top: int) -> list[tuple[int, float]]:
    return sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)[:top]


def _markdown_to_docx(
    markdown: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    matrix: list[list[float]],
    top: int,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for mi, record in enumerate(markdown):
        ranked = _rank_row(matrix[mi], top)
        best_score = ranked[0][1] if ranked else 0.0
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        status = _classify(best_score, second_score)
        counts[status] += 1
        rows.append({
            "markdownIndex": mi,
            "markdownId": record.get("id"),
            "markdownType": record.get("type"),
            "orderIndex": record.get("orderIndex"),
            "textPreview": str(record.get("__matchText") or "")[:180],
            "status": status,
            "bestScore": round(best_score, 2),
            "margin": round(best_score - second_score, 2),
            "candidates": [
                {
                    "paragraphArrayIndex": pi,
                    "paragraphId": paragraphs[pi].get("id"),
                    "paragraphIndex": paragraphs[pi].get("index"),
                    "donorType": paragraphs[pi].get("donorType"),
                    "score": round(score, 2),
                    "textPreview": str(paragraphs[pi].get("text") or "")[:180],
                }
                for pi, score in ranked
            ],
        })
    return rows, counts


def _docx_to_markdown(
    markdown: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    matrix: list[list[float]],
    top: int,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for pi, paragraph in enumerate(paragraphs):
        if not _meaningful_docx(paragraph):
            continue
        scores = [matrix[mi][pi] for mi in range(len(markdown))]
        ranked = _rank_row(scores, top)
        best_score = ranked[0][1] if ranked else 0.0
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        status = _classify(best_score, second_score)
        counts[status] += 1
        rows.append({
            "paragraphArrayIndex": pi,
            "paragraphId": paragraph.get("id"),
            "paragraphIndex": paragraph.get("index"),
            "donorType": paragraph.get("donorType"),
            "textPreview": str(paragraph.get("text") or "")[:180],
            "status": status,
            "bestScore": round(best_score, 2),
            "margin": round(best_score - second_score, 2),
            "candidates": [
                {
                    "markdownIndex": mi,
                    "markdownId": markdown[mi].get("id"),
                    "markdownType": markdown[mi].get("type"),
                    "orderIndex": markdown[mi].get("orderIndex"),
                    "score": round(score, 2),
                    "textPreview": str(markdown[mi].get("__matchText") or "")[:180],
                }
                for mi, score in ranked
            ],
        })
    return rows, counts


def _sequence_metrics(forward: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [
        row for row in forward
        if row.get("status") in {"exact", "strong", "usable"} and row.get("candidates")
    ]
    paragraph_indexes = [int(row["candidates"][0]["paragraphIndex"]) for row in accepted]
    monotonic_pairs = 0
    reversals: list[dict[str, Any]] = []
    for left, right in zip(accepted, accepted[1:]):
        lp = int(left["candidates"][0]["paragraphIndex"])
        rp = int(right["candidates"][0]["paragraphIndex"])
        if rp >= lp:
            monotonic_pairs += 1
        else:
            reversals.append({
                "leftMarkdownId": left.get("markdownId"),
                "leftParagraphIndex": lp,
                "rightMarkdownId": right.get("markdownId"),
                "rightParagraphIndex": rp,
            })
    pair_count = max(0, len(accepted) - 1)
    selected_by_donor: dict[str, list[str]] = defaultdict(list)
    for row in accepted:
        selected_by_donor[str(row["candidates"][0]["paragraphId"])].append(str(row.get("markdownId") or ""))
    duplicate_donors = {
        donor: ids for donor, ids in selected_by_donor.items() if len(ids) > 1
    }
    return {
        "acceptedCount": len(accepted),
        "pairCount": pair_count,
        "monotonicPairCount": monotonic_pairs,
        "monotonicPairRatio": round(monotonic_pairs / pair_count, 5) if pair_count else 1.0,
        "reversalCount": len(reversals),
        "reversals": reversals[:40],
        "duplicateSelectedDonorCount": len(duplicate_donors),
        "duplicateSelectedDonors": dict(list(duplicate_donors.items())[:40]),
        "selectedParagraphIndexes": paragraph_indexes,
    }


def _cross_direction_metrics(
    forward: list[dict[str, Any]],
    reverse: list[dict[str, Any]],
) -> dict[str, Any]:
    reverse_best = {
        str(row.get("paragraphId") or ""): str((row.get("candidates") or [{}])[0].get("markdownId") or "")
        for row in reverse
        if row.get("status") in {"exact", "strong", "usable"} and row.get("candidates")
    }
    mutual = 0
    accepted = 0
    disagreements: list[dict[str, Any]] = []
    for row in forward:
        if row.get("status") not in {"exact", "strong", "usable"} or not row.get("candidates"):
            continue
        accepted += 1
        donor = str(row["candidates"][0].get("paragraphId") or "")
        markdown_id = str(row.get("markdownId") or "")
        reverse_markdown = reverse_best.get(donor)
        if reverse_markdown == markdown_id:
            mutual += 1
        else:
            disagreements.append({
                "markdownId": markdown_id,
                "paragraphId": donor,
                "reverseBestMarkdownId": reverse_markdown,
            })
    return {
        "forwardAcceptedCount": accepted,
        "mutualBestCount": mutual,
        "mutualBestRatio": round(mutual / accepted, 5) if accepted else 0.0,
        "disagreementCount": len(disagreements),
        "disagreements": disagreements[:60],
    }


def build_report(markdown_map: dict[str, Any], docx_path: Path, top: int) -> dict[str, Any]:
    raw_records = list(markdown_map.get("records", []) or [])
    markdown = [_prepare_markdown_record(record) for record in raw_records]
    docx_analysis = analyze_docx(docx_path)
    paragraphs = _prepare_docx_paragraphs(docx_analysis)
    matrix = _score_matrix(markdown, paragraphs)
    forward, forward_counts = _markdown_to_docx(markdown, paragraphs, matrix, top)
    reverse, reverse_counts = _docx_to_markdown(markdown, paragraphs, matrix, top)
    meaningful = sum(1 for paragraph in paragraphs if _meaningful_docx(paragraph))

    return {
        "version": VERSION,
        "purpose": "measure all information already available in isolated Mathpix Markdown and Mathpix DOCX before introducing Mathpix Lines",
        "inputs": {
            "markdownRecordCount": len(markdown),
            "docxParagraphCount": len(paragraphs),
            "meaningfulDocxParagraphCount": meaningful,
            "docxTableCount": int(docx_analysis.get("table_count") or 0),
            "docxInlineShapeCount": int(docx_analysis.get("inline_shape_count") or 0),
        },
        "markdownToDocx": {
            "statusCounts": dict(forward_counts),
            "coverageExactStrongUsable": round(
                sum(forward_counts[k] for k in ("exact", "strong", "usable")) / max(1, len(markdown)), 5
            ),
            "sequence": _sequence_metrics(forward),
            "items": forward,
        },
        "docxToMarkdown": {
            "statusCounts": dict(reverse_counts),
            "coverageExactStrongUsable": round(
                sum(reverse_counts[k] for k in ("exact", "strong", "usable")) / max(1, meaningful), 5
            ),
            "items": reverse,
        },
        "crossDirection": _cross_direction_metrics(forward, reverse),
    }


def _print_summary(report: dict[str, Any]) -> None:
    inputs = report["inputs"]
    forward = report["markdownToDocx"]
    reverse = report["docxToMarkdown"]
    cross = report["crossDirection"]
    sequence = forward["sequence"]
    print("\nMATHPIX MARKDOWN <-> DOCX BENCHMARK")
    print(f"Markdown records: {inputs['markdownRecordCount']}")
    print(f"DOCX paragraphs: {inputs['docxParagraphCount']} (meaningful {inputs['meaningfulDocxParagraphCount']})")
    print(f"Markdown -> DOCX: {forward['statusCounts']} coverage={forward['coverageExactStrongUsable']}")
    print(f"DOCX -> Markdown: {reverse['statusCounts']} coverage={reverse['coverageExactStrongUsable']}")
    print(
        "Sequence: "
        f"accepted={sequence['acceptedCount']} "
        f"monotonic={sequence['monotonicPairRatio']} "
        f"reversals={sequence['reversalCount']} "
        f"duplicateDonors={sequence['duplicateSelectedDonorCount']}"
    )
    print(
        "Mutual best: "
        f"{cross['mutualBestCount']}/{cross['forwardAcceptedCount']} "
        f"ratio={cross['mutualBestRatio']}"
    )


def main() -> int:
    args = _parser().parse_args()
    report = build_report(_load_json(args.markdown_map), args.docx, max(1, args.top))
    _print_summary(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
