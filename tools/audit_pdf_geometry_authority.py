from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

VERSION = "pdf-geometry-authority-audit-0.1"


def _norm(text: str) -> str:
    text = " ".join(str(text or "").split()).casefold()
    return re.sub(r"\b\d+\b", "#", text)


def _q(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    vals = sorted(float(v) for v in values)
    idx = min(len(vals) - 1, max(0, round((len(vals) - 1) * fraction)))
    return vals[idx]


def _span_rows(page) -> list[dict]:
    data = page.get_text("dict")
    rows: list[dict] = []
    for block in data.get("blocks", []) or []:
        if int(block.get("type", -1)) != 0:
            continue
        for line in block.get("lines", []) or []:
            for span in line.get("spans", []) or []:
                bbox = span.get("bbox") or []
                if len(bbox) != 4:
                    continue
                try:
                    x0, y0, x1, y1 = [float(v) for v in bbox]
                except (TypeError, ValueError):
                    continue
                if x1 <= x0 or y1 <= y0:
                    continue
                text = str(span.get("text") or "")
                rows.append({
                    "bbox": [x0, y0, x1, y1],
                    "text": text,
                    "signature": _norm(text),
                    "font": span.get("font"),
                    "size": float(span.get("size") or 0.0),
                    "flags": int(span.get("flags") or 0),
                })
    return rows


def _classify_repeated_edge_spans(page_rows: list[list[dict]], page_heights: list[float]) -> dict:
    top = Counter(); bottom = Counter()
    for rows, height in zip(page_rows, page_heights):
        for row in rows:
            box = row["bbox"]
            cy = (box[1] + box[3]) / 2.0
            sig = row["signature"]
            if not sig:
                continue
            ratio = cy / max(height, 1.0)
            if ratio <= 0.14:
                top[sig] += 1
            elif ratio >= 0.84:
                bottom[sig] += 1
    return {"top": top, "bottom": bottom}


def run(pdf_path: Path, output: Path | None = None) -> dict:
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore

    pdf_path = pdf_path.resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    with fitz.open(pdf_path) as doc:
        page_rows = [_span_rows(page) for page in doc]
        widths = [float(page.rect.width) for page in doc]
        heights = [float(page.rect.height) for page in doc]
        profiles = _classify_repeated_edge_spans(page_rows, heights)

        page_reports = []
        body_lefts: list[float] = []
        body_rights: list[float] = []
        body_tops: list[float] = []
        body_bottoms: list[float] = []
        font_counter = Counter()
        size_counter = Counter()

        for idx, rows in enumerate(page_rows, start=1):
            width = widths[idx - 1]; height = heights[idx - 1]
            header_rows = []
            footer_rows = []
            body_rows = []
            for row in rows:
                sig = row["signature"]
                box = row["bbox"]
                cy = (box[1] + box[3]) / 2.0
                ratio = cy / max(height, 1.0)
                is_header = bool(sig) and ratio <= 0.14 and profiles["top"].get(sig, 0) >= 2
                is_footer = bool(sig) and ratio >= 0.84 and profiles["bottom"].get(sig, 0) >= 2
                if is_header:
                    header_rows.append(row)
                elif is_footer:
                    footer_rows.append(row)
                else:
                    body_rows.append(row)
                    if row.get("font"):
                        font_counter[str(row["font"])] += 1
                    if row.get("size"):
                        size_counter[round(float(row["size"]), 2)] += 1

            if body_rows:
                x0 = min(r["bbox"][0] for r in body_rows)
                x1 = max(r["bbox"][2] for r in body_rows)
                y0 = min(r["bbox"][1] for r in body_rows)
                y1 = max(r["bbox"][3] for r in body_rows)
                body_lefts.append(x0)
                body_rights.append(width - x1)
                body_tops.append(y0)
                body_bottoms.append(height - y1)
                body_bbox = [x0, y0, x1, y1]
                observed_margins = {
                    "left": round(x0, 3),
                    "right": round(width - x1, 3),
                    "top": round(y0, 3),
                    "bottom": round(height - y1, 3),
                }
            else:
                body_bbox = None
                observed_margins = None

            page_reports.append({
                "page": idx,
                "pageWidthPt": width,
                "pageHeightPt": height,
                "headerSpanCount": len(header_rows),
                "footerSpanCount": len(footer_rows),
                "bodySpanCount": len(body_rows),
                "headerSpans": header_rows,
                "footerSpans": footer_rows,
                "bodyBBoxPt": body_bbox,
                "observedMarginsPt": observed_margins,
            })

    summary = {
        "pageCount": len(page_reports),
        "pageSizePt": {
            "widthMedian": median(widths) if widths else None,
            "heightMedian": median(heights) if heights else None,
        },
        "directPdfObservedMarginQuantilesPt": {
            "left": {"p05": _q(body_lefts, .05), "p10": _q(body_lefts, .10), "median": _q(body_lefts, .50)},
            "right": {"p05": _q(body_rights, .05), "p10": _q(body_rights, .10), "median": _q(body_rights, .50)},
            "top": {"p05": _q(body_tops, .05), "p10": _q(body_tops, .10), "median": _q(body_tops, .50)},
            "bottom": {"min": min(body_bottoms) if body_bottoms else None, "p05": _q(body_bottoms, .05), "p10": _q(body_bottoms, .10), "p25": _q(body_bottoms, .25), "median": _q(body_bottoms, .50)},
        },
        "mostCommonPdfFonts": font_counter.most_common(20),
        "mostCommonPdfFontSizesPt": size_counter.most_common(20),
        "repeatedHeaderSignatures": profiles["top"].most_common(30),
        "repeatedFooterSignatures": profiles["bottom"].most_common(30),
        "densestBottomPages": sorted(
            [p for p in page_reports if p.get("observedMarginsPt")],
            key=lambda p: float(p["observedMarginsPt"]["bottom"]),
        )[:20],
    }

    report = {
        "version": VERSION,
        "status": "PASS",
        "sourcePdf": str(pdf_path),
        "authorityPolicy": "PDF native coordinates are authoritative for physical geometry; Mathpix is used separately for semantic/structural classification.",
        "summary": summary,
        "pages": page_reports,
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure authoritative page geometry directly from PDF native text spans.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = run(args.pdf, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("PDF geometry authority audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
