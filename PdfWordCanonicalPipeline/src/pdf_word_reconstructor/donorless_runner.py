from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any

from pdf_word_canonical_pipeline.markdown_equation_donor import extract_markdown_equations
from pdf_word_canonical_pipeline.markdown_element_map_v03 import extract_markdown_element_map

from .common import parse_page_range, write_json
from .mapping_fidelity import build_mapping_fidelity
from .markdown_pdf_spine import build_markdown_pdf_spine
from .native_builder import build_native_page_document
from .page_layout_spine import build_page_layout_spine
from .page_structure import build_page_structure
from .pdf_analyzer import analyze_pdf
from .region_classifier import classify_pdf_regions
from .style_profile import build_style_profile


VERSION = "donorless-reconstruction-0.1"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".svg"}


def _extract_markdown_package(markdown_zip: Path, target: Path) -> tuple[list[Path], list[Path]]:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(markdown_zip) as archive:
        archive.extractall(target)

    nested_root = target / "__nested__"
    for index, archive_path in enumerate(list(target.rglob("*.zip")), start=1):
        nested_target = nested_root / f"zip_{index:03d}"
        try:
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(nested_target)
        except zipfile.BadZipFile:
            continue

    markdown_files = sorted(
        path for path in target.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".mmd"}
    )
    if not markdown_files:
        raise FileNotFoundError("Δεν βρέθηκε .md/.mmd στο Mathpix Markdown ZIP.")
    asset_files = sorted(
        path for path in target.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    return markdown_files, asset_files


def _absent_donor_map() -> dict[str, Any]:
    return {
        "version": "docx-donor-map-absent-by-policy",
        "policy": {
            "role": "absent-by-policy",
            "contentAuthority": False,
            "layoutAuthority": False,
            "nativeDonorEnabled": False,
            "reason": "donorless-baseline",
        },
        "summary": {
            "paragraphCount": 0,
            "tableCount": 0,
            "sectionCount": 0,
            "mathCandidateCount": 0,
            "associationCount": 0,
        },
        "paragraphs": [],
        "tables": [],
        "sections": [],
        "mathCandidates": [],
        "markdownAssociations": [],
        "associationByMarkdownId": {},
    }


def run_donorless_reconstruction(
    *,
    pdf_path: Path,
    markdown_zip: Path,
    pages_spec: str,
    output_dir: Path,
    dpi: int = 160,
) -> dict[str, Any]:
    pdf_path = Path(pdf_path).resolve()
    markdown_zip = Path(markdown_zip).resolve()
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    work_dir = output_dir / "work"
    analysis_dir = output_dir / "analysis"
    asset_dir = output_dir / "page_assets"
    package_dir = output_dir / "markdown_package"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    import fitz
    with fitz.open(pdf_path) as probe:
        pages = parse_page_range(pages_spec, max_pages=probe.page_count)

    markdown_files, asset_files = _extract_markdown_package(markdown_zip, package_dir)
    markdown_map_path = analysis_dir / "markdown_element_map.json"
    markdown_element_map = extract_markdown_element_map(
        markdown_files,
        markdown_map_path,
        docx_path=None,
    )
    equation_donor_path = analysis_dir / "markdown_equation_donors.json"
    extract_markdown_equations(markdown_files, equation_donor_path)

    pdf_analysis = analyze_pdf(pdf_path, pages, work_dir, dpi=dpi)
    style_profile = build_style_profile(pdf_analysis)
    classification_summary = classify_pdf_regions(
        pdf_analysis,
        body_size=style_profile.get("inferred_body_font_size_pt"),
    )
    write_json(analysis_dir / "pdf_analysis.json", pdf_analysis)
    write_json(analysis_dir / "style_profile.json", style_profile)
    write_json(analysis_dir / "classification_summary.json", classification_summary)

    page_structure = build_page_structure(
        pdf_analysis,
        work_dir,
        asset_dir,
        reference_docx=None,
        external_asset_paths=[package_dir],
        equation_donor_path=equation_donor_path,
    )
    write_json(analysis_dir / "page_structure.json", page_structure)

    markdown_pdf_spine = build_markdown_pdf_spine(markdown_element_map, pdf_analysis)
    write_json(analysis_dir / "markdown_pdf_spine.json", markdown_pdf_spine)

    donor_map = _absent_donor_map()
    write_json(analysis_dir / "docx_donor_map.json", donor_map)

    page_layout_spine = build_page_layout_spine(
        markdown_pdf_spine,
        page_structure,
        donor_map,
    )
    write_json(analysis_dir / "page_layout_spine.json", page_layout_spine)

    mapping_preflight = build_mapping_fidelity(
        markdown_pdf_spine=markdown_pdf_spine,
        page_layout_spine=page_layout_spine,
        conversion_spine=None,
        require_conversion=False,
    )
    write_json(analysis_dir / "mapping_fidelity_preflight.json", mapping_preflight)

    final_docx = output_dir / "reconstructed.docx"
    build_report = build_native_page_document(
        pdf_analysis,
        page_structure,
        alignment={"summary": {}, "matches": [], "policy": "absent-by-policy"},
        docx_analysis={
            "version": "docx-analysis-absent-by-policy",
            "paragraphs": [],
            "tables": [],
            "sections": [],
        },
        style_profile=style_profile,
        output_path=final_docx,
        body_size_override=None,
        font_scale=1.0,
        gap_scale=0.72,
        body_line_spacing_multiple=None,
        docx_donor_map=donor_map,
        page_layout_spine=page_layout_spine,
        flow_mode="free",
    )
    write_json(analysis_dir / "build_report.json", build_report)

    report = {
        "version": VERSION,
        "mode": "pdf-markdown-donorless-baseline",
        "authority": {
            "content": "mathpix-markdown",
            "layoutTypography": "pdf",
            "docxDonor": "absent-by-policy",
        },
        "inputs": {
            "pdf": str(pdf_path),
            "markdownZip": str(markdown_zip),
            "pages": pages_spec,
            "selectedPages": pages,
            "markdownFiles": [str(path) for path in markdown_files],
            "assetCount": len(asset_files),
        },
        "stagesSkipped": [
            "analyze_docx",
            "pdf_docx_alignment",
            "docx_donor_map_matching",
            "docx_native_payload_search",
        ],
        "markdownElementCount": int(markdown_element_map.get("count") or 0),
        "mappingPreflight": mapping_preflight,
        "pageLayoutSummary": page_layout_spine.get("summary") or {},
        "buildReport": build_report,
        "outputDocx": str(final_docx),
    }
    write_json(output_dir / "DONORLESS_REPORT.json", report)
    return report
