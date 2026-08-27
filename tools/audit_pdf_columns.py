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

VERSION = "pdf-column-detector-audit-0.2"


def run(pdf_path: Path, output: Path | None = None) -> dict:
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
        },
        "pages": rows,
        "policy": (
            "Diagnostic only. Uses the existing legacy PDF _detect_columns() unchanged. "
            "All accepted column coordinates come from native PDF text-line geometry."
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
    args = parser.parse_args()
    report = run(args.pdf, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("PDF column detector audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
