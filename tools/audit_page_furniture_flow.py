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

from pdf_word_reconstructor.mathpix_lines_input import find_mathpix_lines_json  # noqa: E402
from pdf_word_reconstructor.page_structure import build_page_structure  # noqa: E402
from pdf_word_reconstructor.pdf_analyzer import analyze_pdf  # noqa: E402
from pdf_word_reconstructor.region_classifier_v02 import classify_pdf_regions  # noqa: E402
from pdf_word_reconstructor.style_profile import build_style_profile  # noqa: E402

VERSION = "page-furniture-flow-audit-0.1"


def _flow_region_ids(page: dict) -> set[str]:
    ids: set[str] = set()
    for item in page.get("flow", []) or []:
        for region_id in item.get("region_ids", []) or []:
            if region_id:
                ids.add(str(region_id))
    return ids


def run(pdf_path: Path, manifest_zip: Path, output: Path | None = None) -> dict:
    pdf_path = pdf_path.resolve()
    manifest_zip = manifest_zip.resolve()

    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore

    with fitz.open(pdf_path) as doc:
        pages = list(range(1, doc.page_count + 1))

    with tempfile.TemporaryDirectory(prefix="bookwriter_furniture_flow_") as tmp_name:
        root = Path(tmp_name)
        package_dir = root / "package"
        work_dir = root / "work"
        asset_dir = root / "assets"
        package_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(manifest_zip) as archive:
            archive.extractall(package_dir)
        for nested in list(package_dir.rglob("*.zip")):
            try:
                target = package_dir / (nested.stem + "_extracted")
                target.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(nested) as archive:
                    archive.extractall(target)
            except zipfile.BadZipFile:
                pass

        lines_path = find_mathpix_lines_json(package_dir)
        pdf_analysis = analyze_pdf(pdf_path, pages, work_dir, dpi=72)
        style_profile = build_style_profile(pdf_analysis)
        classify_pdf_regions(pdf_analysis, body_size=style_profile.get("inferred_body_font_size_pt"))
        structure = build_page_structure(
            pdf_analysis,
            work_dir,
            asset_dir,
            reference_docx=None,
            external_asset_paths=[package_dir],
            equation_donor_path=None,
            mathpix_lines_path=lines_path,
        )

        page_rows = []
        leakage = []
        header_count = 0
        footer_count = 0
        pages_with_headers = []
        pages_with_footers = []
        layout_counts = Counter()

        for page in structure.get("pages", []) or []:
            page_no = int(page.get("page") or 0)
            layout_counts[str(page.get("layout_mode") or "unknown")] += 1
            flow_ids = _flow_region_ids(page)
            headers = list(page.get("headers", []) or [])
            footers = list(page.get("footers", []) or [])
            header_count += len(headers)
            footer_count += len(footers)
            if headers:
                pages_with_headers.append(page_no)
            if footers:
                pages_with_footers.append(page_no)

            page_leaks = []
            for kind, rows in (("header", headers), ("footer", footers)):
                for row in rows:
                    rid = str(row.get("id") or "")
                    if rid and rid in flow_ids:
                        record = {
                            "page": page_no,
                            "kind": kind,
                            "regionId": rid,
                            "bbox": row.get("bbox"),
                            "text": str(row.get("text") or "")[:240],
                        }
                        page_leaks.append(record)
                        leakage.append(record)

            page_rows.append({
                "page": page_no,
                "layoutMode": page.get("layout_mode"),
                "flowItemCount": len(page.get("flow", []) or []),
                "headerCount": len(headers),
                "footerCount": len(footers),
                "headers": headers,
                "footers": footers,
                "leakageCount": len(page_leaks),
            })

    report = {
        "version": VERSION,
        "status": "PASS" if not leakage else "FAIL",
        "sourcePdf": str(pdf_path),
        "sourceManifestZip": str(manifest_zip),
        "summary": {
            "pageCount": len(page_rows),
            "headerCount": header_count,
            "footerCount": footer_count,
            "pagesWithHeadersCount": len(pages_with_headers),
            "pagesWithFootersCount": len(pages_with_footers),
            "pagesWithHeaders": pages_with_headers,
            "pagesWithFooters": pages_with_footers,
            "flowLeakageCount": len(leakage),
            "layoutModeCounts": dict(sorted(layout_counts.items())),
        },
        "leakage": leakage,
        "pages": page_rows,
        "policy": (
            "Headers and footers are page-furniture map objects. They must never appear in main body flow. "
            "Their PDF-native bbox/text are retained for later Word section/page-pagination reconstruction."
        ),
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit header/footer exclusion from the Word body flow map.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("manifest_zip", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = run(args.pdf, args.manifest_zip, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Page furniture flow audit: {report['status']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
