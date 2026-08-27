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

VERSION = "page-text-style-map-audit-0.1"
COLLECTIONS = ("flow", "headers", "footers", "callouts", "banners")


def run(pdf_path: Path, manifest_zip: Path, output: Path | None = None) -> dict:
    pdf_path = pdf_path.resolve()
    manifest_zip = manifest_zip.resolve()

    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore

    with fitz.open(pdf_path) as doc:
        pages = list(range(1, doc.page_count + 1))

    with tempfile.TemporaryDirectory(prefix="bookwriter_text_style_map_") as tmp_name:
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

        total_text_items = 0
        mapped_text_items = 0
        run_count = 0
        background_count = 0
        mixed_font_count = 0
        mixed_size_count = 0
        mixed_color_count = 0
        collection_counts = Counter()
        collection_mapped = Counter()
        font_counts = Counter()
        size_counts = Counter()
        color_counts = Counter()
        unmapped = []
        page_rows = []

        for page in structure.get("pages", []) or []:
            page_no = int(page.get("page") or 0)
            page_total = 0
            page_mapped = 0
            page_runs = 0
            page_backgrounds = 0
            for collection_name in COLLECTIONS:
                for item in page.get(collection_name, []) or []:
                    if item.get("type") == "visual":
                        continue
                    total_text_items += 1
                    page_total += 1
                    collection_counts[collection_name] += 1
                    style_map = item.get("textStyleMap") if isinstance(item.get("textStyleMap"), dict) else None
                    if not style_map:
                        unmapped.append({
                            "page": page_no,
                            "collection": collection_name,
                            "id": item.get("id"),
                            "sourceRegionId": item.get("source_region_id"),
                            "regionIds": item.get("region_ids"),
                            "text": str(item.get("text") or "")[:180],
                        })
                        continue
                    mapped_text_items += 1
                    page_mapped += 1
                    collection_mapped[collection_name] += 1
                    runs = [run for run in style_map.get("runs", []) or [] if not run.get("lineBreak")]
                    run_count += len(runs)
                    page_runs += len(runs)
                    summary = style_map.get("summary") or {}
                    if summary.get("mixedFontFamily"):
                        mixed_font_count += 1
                    if summary.get("mixedFontSize"):
                        mixed_size_count += 1
                    if summary.get("mixedColor"):
                        mixed_color_count += 1
                    backgrounds = style_map.get("backgroundEvidence") or []
                    if backgrounds:
                        background_count += 1
                        page_backgrounds += 1
                    for run in runs:
                        if run.get("fontFamily"):
                            font_counts[str(run.get("fontFamily"))] += 1
                        if run.get("fontSizePt") is not None:
                            size_counts[str(run.get("fontSizePt"))] += 1
                        if run.get("color"):
                            color_counts[str(run.get("color"))] += 1
            page_rows.append({
                "page": page_no,
                "textItemCount": page_total,
                "mappedTextItemCount": page_mapped,
                "runCount": page_runs,
                "backgroundEvidenceItemCount": page_backgrounds,
            })

    coverage = (mapped_text_items / total_text_items) if total_text_items else 1.0
    report = {
        "version": VERSION,
        "status": "PASS" if mapped_text_items == total_text_items else "REVIEW",
        "sourcePdf": str(pdf_path),
        "sourceManifestZip": str(manifest_zip),
        "summary": {
            "pageCount": len(page_rows),
            "textItemCount": total_text_items,
            "mappedTextItemCount": mapped_text_items,
            "unmappedTextItemCount": total_text_items - mapped_text_items,
            "coverage": round(coverage, 6),
            "runCount": run_count,
            "backgroundEvidenceItemCount": background_count,
            "mixedFontFamilyItemCount": mixed_font_count,
            "mixedFontSizeItemCount": mixed_size_count,
            "mixedColorItemCount": mixed_color_count,
            "collectionCounts": dict(collection_counts),
            "collectionMappedCounts": dict(collection_mapped),
            "topFontsByRun": font_counts.most_common(20),
            "topFontSizesByRun": size_counts.most_common(20),
            "topColorsByRun": color_counts.most_common(20),
            "pageStructureTextStyleMapSummary": structure.get("textStyleMapSummary"),
        },
        "unmapped": unmapped,
        "pages": page_rows,
        "policy": (
            "This is a coverage audit only. PDF-native typography/background evidence must already be frozen into page_structure; "
            "no Word-renderer inference is performed here."
        ),
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit PDF-native text-style coverage frozen into page_structure maps.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("manifest_zip", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = run(args.pdf, args.manifest_zip, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Page text style map audit: {report['status']}")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
