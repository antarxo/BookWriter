from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from pdf_word_reconstructor.common import normalize_text
from pdf_word_reconstructor.docx_analyzer import analyze_docx
from pdf_word_reconstructor.docx_donor_map import _donor_type, _prepare_markdown_record
from mathpix_markdown_docx_benchmark import build_report as build_md_docx_report


VERSION = "mathpix-three-witness-benchmark-0.1"
ACCEPTED = {"exact", "strong", "usable"}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Item-level Mathpix Markdown <-> DOCX <-> Lines benchmark")
    p.add_argument("--markdown-map", required=True, type=Path)
    p.add_argument("--docx", required=True, type=Path)
    p.add_argument("--lines", required=True, type=Path, help="result.lines.json or a ZIP containing it")
    p.add_argument("--lines-pages", default="17-22", help="Physical pages represented by the isolated Markdown/DOCX, e.g. 17-22")
    p.add_argument("--markdown-page-start", type=int, default=1, help="First page number used by isolated Markdown map")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--max-lines-per-span", type=int, default=24)
    return p


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_lines(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return _load_json(path)
    if path.suffix.lower() != ".zip":
        raise ValueError("--lines must be result.lines.json or a ZIP containing result.lines.json")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        exact = [name for name in names if name.lower().endswith("result.lines.json")]
        candidates = exact or [name for name in names if name.lower().endswith(".lines.json")]
        if not candidates:
            raise ValueError(f"No *.lines.json found in {path}")
        return json.loads(zf.read(candidates[0]).decode("utf-8"))


def _page_range(value: str) -> list[int]:
    pages: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
            if b < a:
                a, b = b, a
            pages.extend(range(a, b + 1))
        else:
            pages.append(int(part))
    pages = sorted(set(pages))
    if not pages:
        raise ValueError("Empty --lines-pages")
    return pages


def _text_score(a: str, b: str) -> float:
    a = normalize_text(a)
    b = normalize_text(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    ratio = float(fuzz.ratio(a, b))
    partial = float(fuzz.partial_ratio(a, b))
    token = float(fuzz.token_set_ratio(a, b))
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    containment = 0.0
    if len(shorter) >= 10 and shorter in longer:
        containment = 92.0
    raw = max(containment, 0.36 * ratio + 0.34 * partial + 0.30 * token)
    length_ratio = min(len(a), len(b)) / max(1, max(len(a), len(b)))
    return min(100.0, raw * (0.78 + 0.22 * length_ratio))


def _status(score: float) -> str:
    if score >= 97.0:
        return "exact"
    if score >= 88.0:
        return "strong"
    if score >= 74.0:
        return "usable"
    if score >= 62.0:
        return "ambiguous"
    return "unmatched"


def _line_text(line: dict[str, Any]) -> str:
    text = str(line.get("text") or "").strip()
    display = str(line.get("text_display") or "").strip()
    if str(line.get("type") or "") == "math" and display:
        return display
    return text or display


def _line_bbox(line: dict[str, Any]) -> list[float] | None:
    region = line.get("region") if isinstance(line.get("region"), dict) else {}
    try:
        x = float(region.get("top_left_x"))
        y = float(region.get("top_left_y"))
        w = float(region.get("width"))
        h = float(region.get("height"))
    except (TypeError, ValueError):
        return None
    return [x, y, x + w, y + h]


def _union_bbox(rows: list[dict[str, Any]]) -> list[float] | None:
    boxes = [_line_bbox(row) for row in rows]
    boxes = [box for box in boxes if box]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _prepare_lines(data: dict[str, Any], pages: list[int]) -> list[dict[str, Any]]:
    wanted = set(pages)
    units: list[dict[str, Any]] = []
    for page in data.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        if page_no not in wanted:
            continue
        for raw in page.get("lines", []) or []:
            kind = str(raw.get("type") or "")
            # Parent/container rows duplicate child content. Page furniture is not part of the isolated semantic stream.
            if raw.get("children_ids"):
                continue
            if kind in {"page_info", "column", "section_header", "multiple_choice_block"}:
                continue
            text = _line_text(raw)
            if not text:
                continue
            if raw.get("conversion_output") is False and kind not in {"math", "figure_label"}:
                continue
            units.append({
                "page": page_no,
                "line": int(raw.get("line") or 0),
                "id": raw.get("id"),
                "type": kind,
                "text": text,
                "normalized": normalize_text(text),
                "bbox": _line_bbox(raw),
                "confidence": raw.get("confidence"),
            })
    units.sort(key=lambda r: (int(r["page"]), int(r["line"])))
    for index, unit in enumerate(units):
        unit["unitIndex"] = index
    return units


def _md_text(record: dict[str, Any]) -> str:
    prepared = _prepare_markdown_record(record)
    return str(prepared.get("__matchText") or "")


def _docx_text(paragraph: dict[str, Any]) -> str:
    text = str(paragraph.get("text") or "").strip()
    if text:
        return text
    return str(paragraph.get("omml_text") or paragraph.get("ommlText") or "").strip()


def _meaningful_docx(paragraph: dict[str, Any]) -> bool:
    return bool(
        _docx_text(paragraph)
        or int(paragraph.get("drawing_count", paragraph.get("drawingCount", 0)) or 0)
        or int(paragraph.get("omml_count", paragraph.get("ommlCount", 0)) or 0)
        or paragraph.get("numbering")
    )


def _span_payload(units: list[dict[str, Any]], start: int, end: int, score: float) -> dict[str, Any]:
    rows = units[start:end]
    return {
        "score": round(float(score), 2),
        "status": _status(float(score)),
        "startUnit": start,
        "endUnitExclusive": end,
        "pageStart": rows[0]["page"] if rows else None,
        "pageEnd": rows[-1]["page"] if rows else None,
        "lineStart": rows[0]["line"] if rows else None,
        "lineEnd": rows[-1]["line"] if rows else None,
        "lineIds": [row.get("id") for row in rows],
        "bbox": _union_bbox(rows),
        "textPreview": " ".join(str(row.get("text") or "") for row in rows)[:240],
    }


def _align_sequence(
    elements: list[dict[str, Any]],
    units: list[dict[str, Any]],
    *,
    text_getter,
    page_getter=None,
    max_span: int = 24,
) -> list[dict[str, Any]]:
    m, n = len(elements), len(units)
    neg = -10**18
    dp = [[neg] * (n + 1) for _ in range(m + 1)]
    back: list[list[tuple[str, int, int, int] | None]] = [[None] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = 0.0
    for j in range(1, n + 1):
        dp[0][j] = dp[0][j - 1] - 0.35
        back[0][j] = ("skip-line", 0, j - 1, j)
    for i in range(1, m + 1):
        dp[i][0] = dp[i - 1][0] - 6.0
        back[i][0] = ("skip-element", i - 1, 0, 0)

    for i in range(1, m + 1):
        source = str(text_getter(elements[i - 1]) or "").strip()
        required_page = page_getter(elements[i - 1]) if page_getter else None
        for j in range(1, n + 1):
            best = dp[i - 1][j] - 6.0
            act: tuple[str, int, int, int] = ("skip-element", i - 1, j, j)
            if dp[i][j - 1] - 0.35 > best:
                best = dp[i][j - 1] - 0.35
                act = ("skip-line", i, j - 1, j)
            if source:
                lo = max(0, j - max_span)
                for start in range(lo, j):
                    rows = units[start:j]
                    if required_page is not None and any(int(row["page"]) != int(required_page) for row in rows):
                        continue
                    target = " ".join(str(row.get("text") or "") for row in rows)
                    score = _text_score(source, target)
                    if score < 50.0:
                        continue
                    gain = score - 49.0 - 0.10 * max(0, (j - start) - 1)
                    candidate = dp[i - 1][start] + gain
                    if candidate > best:
                        best = candidate
                        act = ("match", i - 1, start, j)
            dp[i][j] = best
            back[i][j] = act

    end_j = max(range(n + 1), key=lambda j: dp[m][j] - 0.02 * (n - j))
    matches: dict[int, tuple[int, int, float]] = {}
    i, j = m, end_j
    while i > 0 or j > 0:
        act = back[i][j]
        if act is None:
            break
        op, prev_i, start, end = act
        if op == "match":
            source = str(text_getter(elements[prev_i]) or "").strip()
            target = " ".join(str(row.get("text") or "") for row in units[start:end])
            matches[prev_i] = (start, end, _text_score(source, target))
            i, j = prev_i, start
        elif op == "skip-element":
            i, j = prev_i, start
        else:
            i, j = prev_i, start

    result: list[dict[str, Any]] = []
    for index, element in enumerate(elements):
        match = matches.get(index)
        if match:
            start, end, score = match
            payload = _span_payload(units, start, end, score)
        else:
            payload = {
                "score": 0.0,
                "status": "nontext" if not str(text_getter(element) or "").strip() else "unmatched",
                "startUnit": None,
                "endUnitExclusive": None,
                "pageStart": None,
                "pageEnd": None,
                "lineStart": None,
                "lineEnd": None,
                "lineIds": [],
                "bbox": None,
                "textPreview": "",
            }
        result.append(payload)
    return result


def _overlap(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if not a or not b:
        return False
    if a.get("startUnit") is None or b.get("startUnit") is None:
        return False
    return max(int(a["startUnit"]), int(b["startUnit"])) < min(int(a["endUnitExclusive"]), int(b["endUnitExclusive"]))


def build_report(markdown_map: dict[str, Any], docx_path: Path, lines_data: dict[str, Any], lines_pages: list[int], markdown_page_start: int, max_span: int) -> dict[str, Any]:
    md_docx = build_md_docx_report(markdown_map, docx_path, 3)
    raw_md = list(markdown_map.get("records", []) or [])
    line_units = _prepare_lines(lines_data, lines_pages)
    first_lines_page = min(lines_pages)

    def md_page(record: dict[str, Any]) -> int | None:
        page = record.get("page")
        if not isinstance(page, int):
            return None
        return first_lines_page + (int(page) - int(markdown_page_start))

    md_lines = _align_sequence(raw_md, line_units, text_getter=_md_text, page_getter=md_page, max_span=max_span)

    docx_analysis = analyze_docx(docx_path)
    docx_paragraphs = [row for row in (docx_analysis.get("paragraphs", []) or []) if _meaningful_docx(row)]
    docx_lines = _align_sequence(docx_paragraphs, line_units, text_getter=_docx_text, page_getter=None, max_span=max_span)
    docx_line_by_id = {
        str(paragraph.get("id") or ""): match
        for paragraph, match in zip(docx_paragraphs, docx_lines)
    }

    forward_by_md = {str(row.get("markdownId") or ""): row for row in md_docx["markdownToDocx"]["items"]}
    item_rows: list[dict[str, Any]] = []
    consensus_counts: Counter[str] = Counter()
    md_lines_counts: Counter[str] = Counter()
    docx_lines_counts: Counter[str] = Counter(match["status"] for match in docx_lines)

    for record, line_match in zip(raw_md, md_lines):
        md_id = str(record.get("id") or "")
        md_docx_row = forward_by_md.get(md_id) or {}
        md_docx_status = str(md_docx_row.get("status") or "unmatched")
        md_lines_status = str(line_match.get("status") or "unmatched")
        md_lines_counts[md_lines_status] += 1
        selected_docx = (md_docx_row.get("candidates") or [{}])[0] if md_docx_row.get("candidates") else {}
        paragraph_id = str(selected_docx.get("paragraphId") or "")
        direct_docx_lines = docx_line_by_id.get(paragraph_id)

        docx_ok = md_docx_status in ACCEPTED
        lines_ok = md_lines_status in ACCEPTED
        direct_ok = bool(direct_docx_lines and direct_docx_lines.get("status") in ACCEPTED)
        if docx_ok and lines_ok and direct_ok and _overlap(line_match, direct_docx_lines):
            consensus = "triple-agreement"
        elif not docx_ok and lines_ok:
            consensus = "lines-resolves-docx-gap"
        elif docx_ok and not lines_ok:
            consensus = "docx-resolves-lines-gap"
        elif docx_ok and lines_ok:
            consensus = "pair-agreement-direct-lines-differs"
        elif md_docx_status == "ambiguous" and lines_ok:
            consensus = "lines-resolves-docx-ambiguity"
        elif md_lines_status == "ambiguous" and docx_ok:
            consensus = "docx-resolves-lines-ambiguity"
        else:
            consensus = "unresolved"
        consensus_counts[consensus] += 1

        item_rows.append({
            "markdownId": md_id,
            "markdownType": record.get("type"),
            "markdownPage": record.get("page"),
            "textPreview": _md_text(record)[:220],
            "markdownToDocx": {
                "status": md_docx_status,
                "score": md_docx_row.get("bestScore"),
                "selected": selected_docx or None,
            },
            "markdownToLines": line_match,
            "selectedDocxToLines": direct_docx_lines,
            "consensus": consensus,
        })

    previously_not_accepted = [
        row for row in item_rows if row["markdownToDocx"]["status"] not in ACCEPTED
    ]
    rescued_by_lines = [row for row in previously_not_accepted if row["markdownToLines"]["status"] in ACCEPTED]

    return {
        "version": VERSION,
        "purpose": "compare the same isolated Mathpix semantic items against both DOCX and Lines before changing production reconstruction rules",
        "inputs": {
            "markdownRecords": len(raw_md),
            "meaningfulDocxParagraphs": len(docx_paragraphs),
            "selectedLinesPages": lines_pages,
            "lineUnits": len(line_units),
            "linesSourceContext": "Lines may originate from the full-book Mathpix job while Markdown/DOCX are from the isolated six-page job; physical page content is the same, export context is not assumed identical.",
        },
        "pairwise": {
            "markdownToDocx": {
                "statusCounts": md_docx["markdownToDocx"]["statusCounts"],
                "coverage": md_docx["markdownToDocx"]["coverageExactStrongUsable"],
                "mutualBest": md_docx["crossDirection"],
            },
            "markdownToLines": {
                "statusCounts": dict(md_lines_counts),
                "coverage": round(sum(md_lines_counts[s] for s in ACCEPTED) / max(1, len(raw_md)), 5),
            },
            "docxToLines": {
                "statusCounts": dict(docx_lines_counts),
                "coverage": round(sum(docx_lines_counts[s] for s in ACCEPTED) / max(1, len(docx_paragraphs)), 5),
            },
        },
        "consensus": {
            "statusCounts": dict(consensus_counts),
            "previousMarkdownDocxNotAccepted": len(previously_not_accepted),
            "rescuedByLines": len(rescued_by_lines),
            "rescuedMarkdownIds": [row["markdownId"] for row in rescued_by_lines],
        },
        "items": item_rows,
    }


def _print_summary(report: dict[str, Any]) -> None:
    print("\nMATHPIX THREE-WITNESS BENCHMARK")
    print(f"Markdown records: {report['inputs']['markdownRecords']}")
    print(f"DOCX meaningful paragraphs: {report['inputs']['meaningfulDocxParagraphs']}")
    print(f"Lines pages: {report['inputs']['selectedLinesPages']} units={report['inputs']['lineUnits']}")
    for name in ("markdownToDocx", "markdownToLines", "docxToLines"):
        row = report["pairwise"][name]
        print(f"{name}: {row['statusCounts']} coverage={row['coverage']}")
    c = report["consensus"]
    print(f"Consensus: {c['statusCounts']}")
    print(f"Previously MD<->DOCX not accepted: {c['previousMarkdownDocxNotAccepted']}; rescued by Lines: {c['rescuedByLines']}")
    if c["rescuedMarkdownIds"]:
        print("Rescued IDs: " + ", ".join(c["rescuedMarkdownIds"]))


def main() -> int:
    args = _parser().parse_args()
    markdown_map = _load_json(args.markdown_map)
    lines_data = _load_lines(args.lines)
    report = build_report(markdown_map, args.docx, lines_data, _page_range(args.lines_pages), args.markdown_page_start, max(1, args.max_lines_per_span))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(report)
    print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
