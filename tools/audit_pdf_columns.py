from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SRC = REPO_ROOT / "PdfWordCanonicalPipeline" / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from pdf_word_reconstructor.pdf_analyzer import analyze_pdf  # noqa: E402
from pdf_word_reconstructor.region_classifier_v02 import classify_pdf_regions  # noqa: E402
from pdf_word_reconstructor.style_profile import build_style_profile  # noqa: E402
from pdf_word_reconstructor.page_structure_legacy import _detect_columns  # noqa: E402

VERSION = "pdf-column-detector-audit-0.3"
DEFAULT_PREVIEW_PAGES = [9, 10, 11, 12, 13, 14, 27, 28, 29]


def _render_previews(pdf_path: Path, rows_by_page: dict[int, dict], preview_dir: Path, pages: list[int]) -> list[dict]:
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore

    preview_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict] = []
    with fitz.open(pdf_path) as doc:
        for page_no in pages:
            if page_no < 1 or page_no > doc.page_count:
                continue
            page = doc[page_no - 1]
            row = rows_by_page.get(page_no) or {}
            columns = list(row.get("columns") or [])
            diagnostics = dict(row.get("diagnostics") or {})

            # Draw only diagnostic overlays. The source page remains unchanged.
            shape = page.new_shape()
            for column in columns:
                try:
                    rect = fitz.Rect(
                        float(column["x0"]), float(column["y0"]),
                        float(column["x1"]), float(column["y1"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                shape.draw_rect(rect)
                shape.finish(width=1.2, color=(1, 0, 0), fill=None)
            shape.commit(overlay=True)

            scale = 1.6
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            suffix = "accepted" if bool(row.get("accepted")) else str(diagnostics.get("reason") or "rejected")
            safe_suffix = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in suffix)
            out = preview_dir / f"page-{page_no:03d}-{safe_suffix}.png"
            pix.save(out)
            written.append({
                "page": page_no,
                "accepted": bool(row.get("accepted")),
                "reason": diagnostics.get("reason"),
                "columns": columns,
                "path": str(out),
            })
    return written


def run(
    pdf_path: Path,
    output: Path | None = None,
    preview_dir: Path | None = None,
    preview_pages: list[int] | None = None,
) -> dict:
    pdf_path = pdf_path.resolve()
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore

    with fitz.open(pdf_path) as doc:
        page_numbers = list(range(1, doc.page_count + 1))

    with tempfile.TemporaryDirectory(prefix="bookwriter_pdf_columns_") as temp_name:
        work_dir = Path(temp_name)
        pdf_analysis = analyze_pdf(pdf_path, page_numbers, work_dir, dpi=72)
        style_profile = build_style_profile(pdf_analysis)
        classify_pdf_regions(pdf_analysis, body_size=style_profile.get("inferred_body_font_size_pt"))

        rows = []
        accepted_pages = []
        reasons = Counter()
        rejected_candidates = []
        for page in pdf_analysis.get("pages", []) or []:
            columns, diagnostics = _detect_columns(page)
            page_no = int(page.get("page") or 0)
            reason = str(diagnostics.get("reason") or "unknown")
            reasons[reason] += 1
            if columns:
                accepted_pages.append(page_no)
            elif bool(diagnostics.get("candidate")):
                rejected_candidates.append({
                    "page": page_no,
                    "reason": reason,
                    "leftLineCount": diagnostics.get("leftLineCount"),
                    "rightLineCount": diagnostics.get("rightLineCount"),
                    "leftChars": diagnostics.get("leftChars"),
                    "rightChars": diagnostics.get("rightChars"),
                    "robustLeftWidthPt": diagnostics.get("robustLeftWidthPt"),
                    "robustRightWidthPt": diagnostics.get("robustRightWidthPt"),
                    "equalWidthRatio": diagnostics.get("equalWidthRatio"),
                    "gutterPt": diagnostics.get("gutterPt"),
                    "verticalOverlapRatio": diagnostics.get("verticalOverlapRatio"),
                    "contentBalance": diagnostics.get("contentBalance"),
                })
            rows.append({
                "page": page_no,
                "accepted": bool(columns),
                "columns": columns,
                "diagnostics": diagnostics,
            })

    rows_by_page = {int(row["page"]): row for row in rows}
    visual_previews = []
    if preview_dir:
        visual_previews = _render_previews(
            pdf_path,
            rows_by_page,
            preview_dir.resolve(),
            preview_pages or DEFAULT_PREVIEW_PAGES,
        )

    report = {
        "version": VERSION,
        "status": "PASS",
        "sourcePdf": str(pdf_path),
        "summary": {
            "pageCount": len(rows),
            "acceptedTwoColumnPageCount": len(accepted_pages),
            "acceptedTwoColumnPages": accepted_pages,
            "reasonCounts": dict(sorted(reasons.items())),
            "rejectedCandidateCount": len(rejected_candidates),
            "rejectedCandidates": rejected_candidates,
            "visualPreviewCount": len(visual_previews),
        },
        "pages": rows,
        "visualPreviews": visual_previews,
        "policy": (
            "Diagnostic only. Uses the existing legacy PDF _detect_columns() unchanged. "
            "All accepted column coordinates come from native PDF text-line geometry. "
            "Rendered previews exist only to derive generalizable detector rules from visual inspection; "
            "they are not a runtime fallback and do not alter reconstruction decisions."
        ),
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the existing PDF-native two-column detector.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--preview-dir", type=Path, default=None)
    parser.add_argument(
        "--preview-pages",
        default=None,
        help="Comma-separated 1-based page numbers. Default: 9-14 and 27-29.",
    )
    args = parser.parse_args()
    preview_pages = None
    if args.preview_pages:
        preview_pages = [int(part.strip()) for part in str(args.preview_pages).split(",") if part.strip()]
    report = run(args.pdf, args.output, args.preview_dir, preview_pages)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if report.get("visualPreviews"):
        print("Visual previews:")
        for item in report["visualPreviews"]:
            print(f"  p{item['page']}: {item['path']}")
    print("PDF column detector audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
