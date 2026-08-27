from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
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
from pdf_word_reconstructor.word_section_plan import build_word_section_plan  # noqa: E402


def run(pdf_path: Path, manifest_zip: Path, output: Path | None = None) -> dict:
    pdf_path = pdf_path.resolve()
    manifest_zip = manifest_zip.resolve()
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore

    with fitz.open(pdf_path) as doc:
        page_numbers = list(range(1, doc.page_count + 1))

    with tempfile.TemporaryDirectory(prefix="bookwriter_section_plan_") as tmp_name:
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
        pdf_analysis = analyze_pdf(pdf_path, page_numbers, work_dir, dpi=72)
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
        plan = build_word_section_plan(structure)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Word section families derived from the existing per-page map.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("manifest_zip", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    plan = run(args.pdf, args.manifest_zip, args.output)
    summary = {
        "pageCount": plan.get("pageCount"),
        "sectionCount": plan.get("sectionCount"),
        "sections": [
            {
                "index": section.get("index"),
                "startPage": section.get("startPage"),
                "endPage": section.get("endPage"),
                "pageCount": section.get("pageCount"),
                "columnCount": section.get("columnCount"),
                "columnGutterPt": section.get("columnGutterPt"),
                "blankPageCount": section.get("blankPageCount"),
                "headerPresenceCount": section.get("headerPresenceCount"),
                "footerPresenceCount": section.get("footerPresenceCount"),
            }
            for section in plan.get("sections", []) or []
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Word section plan audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
