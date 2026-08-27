from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SRC = REPO_ROOT / "PdfWordCanonicalPipeline" / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from pdf_word_reconstructor.mathpix_lines_input import build_mathpix_line_layout_map, find_mathpix_lines_json  # noqa: E402
from pdf_word_reconstructor.mathpix_page_geometry_adapter import build_mathpix_page_geometry_evidence  # noqa: E402


VERSION = "mathpix-page-geometry-audit-0.1"


def _extract_recursive(source_zip: Path, target: Path) -> None:
    with zipfile.ZipFile(source_zip) as archive:
        archive.extractall(target)
    nested_root = target / "__nested__"
    for index, nested_zip in enumerate(sorted(target.rglob("*.zip")), start=1):
        nested_target = nested_root / f"zip_{index:03d}"
        nested_target.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(nested_zip) as archive:
                archive.extractall(nested_target)
        except zipfile.BadZipFile:
            continue


def _find_source_pdf(package_dir: Path) -> Path | None:
    preferred = sorted(package_dir.rglob("source.pdf"))
    if preferred:
        return preferred[0]
    pdfs = sorted(package_dir.rglob("*.pdf"))
    return pdfs[0] if pdfs else None


def _pdf_size_analysis(pdf_path: Path) -> dict:
    import fitz
    pages = []
    with fitz.open(pdf_path) as doc:
        for index, page in enumerate(doc, start=1):
            rect = page.rect
            pages.append({"page": index, "width_pt": float(rect.width), "height_pt": float(rect.height)})
    return {"pages": pages}


def run(package_zip: Path, output: Path | None = None) -> dict:
    package_zip = package_zip.resolve()
    with tempfile.TemporaryDirectory(prefix="bookwriter_geometry_audit_") as temp_name:
        package_dir = Path(temp_name) / "package"
        package_dir.mkdir(parents=True, exist_ok=True)
        _extract_recursive(package_zip, package_dir)

        lines_path = find_mathpix_lines_json(package_dir)
        source_pdf = _find_source_pdf(package_dir)
        if lines_path is None:
            raise FileNotFoundError("result.lines.json not found")
        if source_pdf is None:
            raise FileNotFoundError("source.pdf not found")

        line_map = build_mathpix_line_layout_map(lines_path, _pdf_size_analysis(source_pdf))
        geometry = build_mathpix_page_geometry_evidence(line_map)

        pages = geometry.get("pages") or []
        header_counts = Counter()
        footer_counts = Counter()
        column_counts = Counter()
        body_counts = Counter()
        no_header = []
        no_footer = []
        medium_header = []
        medium_footer = []

        for page in pages:
            page_no = int(page.get("page") or 0)
            header = page.get("headerBand") or {}
            footer = page.get("footerBand") or {}
            body = page.get("bodyBox") or {}
            columns = page.get("columnEvidence") or {}

            hc = str(header.get("confidence") or "none")
            fc = str(footer.get("confidence") or "none")
            bc = str(body.get("confidence") or "none")
            header_counts[hc] += 1
            footer_counts[fc] += 1
            body_counts[bc] += 1
            column_counts[str(columns.get("classification") or "unknown")] += 1
            if hc == "none": no_header.append(page_no)
            if fc == "none": no_footer.append(page_no)
            if hc == "medium": medium_header.append(page_no)
            if fc == "medium": medium_footer.append(page_no)

        report = {
            "version": VERSION,
            "status": "PASS",
            "package": str(package_zip),
            "sourcePdf": str(source_pdf),
            "summary": {
                "pageCount": len(pages),
                "headerConfidenceCounts": dict(sorted(header_counts.items())),
                "footerConfidenceCounts": dict(sorted(footer_counts.items())),
                "bodyConfidenceCounts": dict(sorted(body_counts.items())),
                "columnClassificationCounts": dict(sorted(column_counts.items())),
                "pagesWithoutHeader": no_header,
                "pagesWithoutFooter": no_footer,
                "pagesWithMediumHeader": medium_header,
                "pagesWithMediumFooter": medium_footer,
            },
            "policy": "diagnostic only: inspect header/footer classification before allowing body margins or columns into page_structure",
            "geometry": geometry,
        }

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Mathpix headers/footers before margins and columns.")
    parser.add_argument("package_zip", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = run(args.package_zip, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("Mathpix page geometry audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
