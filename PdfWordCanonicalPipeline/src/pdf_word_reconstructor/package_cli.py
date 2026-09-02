from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

from docx import Document

from pdf_word_canonical_pipeline.markdown_element_map import extract_markdown_element_map

from .canonical_evidence_fusion import build_canonical_evidence_document
from .canonical_page_structure_adapter import (
    apply_canonical_evidence_to_page_structure,
    canonicalize_markdown_pdf_spine,
)
from .common import parse_page_range, write_json
from .docx_analyzer import analyze_docx
from .markdown_pdf_spine import build_markdown_pdf_spine
from .mathpix_exact_layout_recovery import recover_exact_mathpix_layouts
from .mathpix_lines_input import find_mathpix_lines_json, load_mathpix_lines
from .mathpix_mmd_block_refinement import refine_markdown_element_map
from .native_builder import build_native_page_document
from .page_layout_spine import build_page_layout_spine
from .page_structure import build_page_structure
from .pdf_analyzer import analyze_pdf
from .preview_recovery_layer import prepare_preview_recovery_layer
from .region_classifier import classify_pdf_regions
from .style_profile import build_style_profile


VERSION = "mathpix-package-reconstruction-cli-0.8"


def _extract_package(package_zip: Path, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package_zip) as archive:
        archive.extractall(target)

    nested_root = target / "__nested__"
    for index, nested in enumerate(sorted(target.rglob("*.zip")), start=1):
        if nested.resolve() == package_zip.resolve():
            continue
        out = nested_root / f"zip_{index:03d}_{nested.stem}"
        try:
            out.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(nested) as archive:
                archive.extractall(out)
        except zipfile.BadZipFile:
            shutil.rmtree(out, ignore_errors=True)
    return target


def _find_canonical_mmd(package_dir: Path) -> Path:
    exact = sorted(path for path in package_dir.rglob("result.mmd") if path.is_file())
    if exact:
        return exact[0]
    candidates = sorted(path for path in package_dir.rglob("*.mmd") if path.is_file())
    if not candidates:
        raise FileNotFoundError("Mathpix package does not contain result.mmd or another .mmd file")
    return candidates[0]


def _find_package_pdf(package_dir: Path) -> Path:
    candidates = sorted(path for path in package_dir.rglob("*.pdf") if path.is_file())
    if not candidates:
        raise FileNotFoundError("Mathpix package does not contain the source PDF")
    if len(candidates) > 1:
        preview = ", ".join(str(path.relative_to(package_dir)) for path in candidates[:8])
        raise RuntimeError(
            "Mathpix package contains more than one PDF; source PDF is ambiguous: " + preview
        )
    return candidates[0]


def _blank_docx_shim(path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.save(path)
    return analyze_docx(path)


def _parse_requested_physical_pages(spec: str, available: list[int]) -> list[int]:
    values: set[int] = set()
    for token in str(spec).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start, end = int(left), int(right)
            if end < start:
                start, end = end, start
            values.update(range(start, end + 1))
        else:
            values.add(int(token))
    requested = sorted(values)
    missing = [page for page in requested if page not in set(available)]
    if missing:
        raise RuntimeError(
            "Requested physical pages are absent from Mathpix Lines: " + ", ".join(map(str, missing))
        )
    return requested


def _resolve_package_page_mapping(lines_data: dict[str, Any], pdf_page_count: int, page_spec: str) -> dict[str, Any]:
    physical_pages = sorted(
        int(page.get("page") or 0)
        for page in lines_data.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    )
    if not physical_pages:
        raise RuntimeError("Mathpix Lines contains no physical page numbers")

    requested = _parse_requested_physical_pages(page_spec, physical_pages)
    contiguous = physical_pages == list(range(physical_pages[0], physical_pages[-1] + 1))

    if max(physical_pages) <= pdf_page_count:
        physical_to_ordinal = {page: page for page in physical_pages}
        mode = "identity-physical-page"
    elif pdf_page_count == len(physical_pages) and contiguous:
        physical_to_ordinal = {
            physical: ordinal
            for ordinal, physical in enumerate(physical_pages, start=1)
        }
        mode = "subset-ordinal-to-contiguous-physical-pages"
    else:
        raise RuntimeError(
            "PDF/Mathpix page mapping is ambiguous: "
            f"pdfPageCount={pdf_page_count}, linesPhysicalPages={physical_pages}. "
            "Package reconstruction is fail-closed; no page mapping was guessed."
        )

    requested_ordinals = [physical_to_ordinal[page] for page in requested]
    return {
        "mode": mode,
        "physicalPages": requested,
        "pdfOrdinals": requested_ordinals,
        "physicalToPdfOrdinal": {str(page): physical_to_ordinal[page] for page in requested},
        "pdfOrdinalToPhysical": {str(physical_to_ordinal[page]): page for page in requested},
    }


def _remap_pdf_analysis_to_physical_pages(
    pdf_analysis: dict[str, Any],
    ordinal_to_physical: dict[int, int],
) -> dict[str, Any]:
    result = copy.deepcopy(pdf_analysis)
    for page in result.get("pages", []) or []:
        ordinal = int(page.get("page") or 0)
        physical = ordinal_to_physical.get(ordinal)
        if physical is None:
            raise RuntimeError(f"Analyzed PDF ordinal page {ordinal} has no physical Mathpix page mapping")
        page["pdfOrdinalPage"] = ordinal
        page["page"] = physical
        page["pageMappingSource"] = "package-cli-subset-physical-page-map"
    result["selected_pages"] = sorted(int(page) for page in ordinal_to_physical.values())
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package-first PDF + Mathpix MMD/lines/assets -> maps-first reconstructed DOCX"
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        help="Optional source PDF override. If omitted, the unique PDF inside the Mathpix ZIP is used.",
    )
    parser.add_argument("--mathpix-package", required=True, type=Path)
    parser.add_argument("--pages", default="17-60", help="Physical Mathpix page range, e.g. 17-60")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dpi", type=int, default=160)
    parser.add_argument(
        "--preview-unresolved",
        action="store_true",
        help=(
            "Diagnostic preview only: render ready contract items; unresolved/ambiguous items are "
            "recorded and omitted from DOCX. Strict production behavior remains the default."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    package_zip = args.mathpix_package.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    analysis_dir = output / "analysis"
    work_dir = output / "work"
    asset_dir = output / "page_assets"
    package_dir = work_dir / "mathpix_package"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    asset_dir.mkdir(parents=True, exist_ok=True)

    if not package_zip.exists():
        raise FileNotFoundError(f"Mathpix package not found: {package_zip}")

    print(f"[1/8] Extract Mathpix package: {package_zip.name}")
    _extract_package(package_zip, package_dir)
    lines_path = find_mathpix_lines_json(package_dir)
    if lines_path is None:
        raise FileNotFoundError("Mathpix package does not contain result.lines.json")
    lines_data = load_mathpix_lines(lines_path)
    mmd_path = _find_canonical_mmd(package_dir)

    pdf_path = args.pdf.resolve() if args.pdf else _find_package_pdf(package_dir).resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # type: ignore

    with fitz.open(pdf_path) as probe:
        page_mapping = _resolve_package_page_mapping(lines_data, int(probe.page_count), args.pages)

    physical_pages = [int(page) for page in page_mapping["physicalPages"]]
    pdf_ordinals = [int(page) for page in page_mapping["pdfOrdinals"]]
    ordinal_to_physical = {
        int(k): int(v)
        for k, v in page_mapping["pdfOrdinalToPhysical"].items()
    }
    write_json(analysis_dir / "package_page_mapping.json", page_mapping)

    print(
        f"[2/8] Analyze PDF ordinals {pdf_ordinals[0]}-{pdf_ordinals[-1]} "
        f"as physical Mathpix pages {physical_pages[0]}-{physical_pages[-1]} ({len(physical_pages)} pages)"
    )
    pdf_analysis_raw = analyze_pdf(pdf_path, pdf_ordinals, work_dir, dpi=args.dpi)
    pdf_analysis = _remap_pdf_analysis_to_physical_pages(pdf_analysis_raw, ordinal_to_physical)
    style_profile = build_style_profile(pdf_analysis)
    classification_summary = classify_pdf_regions(
        pdf_analysis,
        body_size=style_profile.get("inferred_body_font_size_pt"),
    )
    write_json(analysis_dir / "pdf_layout_classified.json", pdf_analysis)
    write_json(analysis_dir / "style_profile.json", style_profile)
    write_json(analysis_dir / "classification_summary.json", classification_summary)

    print("[3/8] Build complete page maps from PDF + Mathpix lines [PDF-FIRST VISUAL OWNERSHIP]")
    page_structure = build_page_structure(
        pdf_analysis,
        work_dir,
        asset_dir,
        reference_docx=None,
        external_asset_paths=None,
        equation_donor_path=None,
        mathpix_lines_path=lines_path,
        mathpix_lines_mode="lines_first",
    )
    write_json(analysis_dir / "page_structure_pdf_first.json", page_structure)

    print("[4/8] Parse canonical Mathpix MMD with existing Markdown element mapper")
    markdown_element_map = extract_markdown_element_map(
        [mmd_path],
        analysis_dir / "markdown_element_map_raw.json",
        docx_path=None,
        attach_docx_evidence=False,
    )
    markdown_element_map = refine_markdown_element_map(
        markdown_element_map,
        mmd_path,
        page_structure,
    )
    write_json(analysis_dir / "markdown_element_map.json", markdown_element_map)
    write_json(
        analysis_dir / "mathpix_mmd_block_refinement.json",
        markdown_element_map.get("mmdBlockRefinement") or {},
    )

    print("[5/8] Build canonical MMD + Lines evidence and Markdown/PDF spine")
    markdown_pdf_spine = build_markdown_pdf_spine(markdown_element_map, pdf_analysis)
    canonical_evidence = build_canonical_evidence_document(
        mmd_path,
        lines_path,
        pdf_path=pdf_path,
        target_page=None,
        work_dir=analysis_dir / "canonical_fusion_work",
    )
    write_json(analysis_dir / "canonical_evidence.json", canonical_evidence)

    canonical_adapter = apply_canonical_evidence_to_page_structure(
        page_structure,
        canonical_evidence,
    )
    write_json(analysis_dir / "canonical_page_structure_adapter.json", canonical_adapter)
    write_json(analysis_dir / "page_structure.json", page_structure)

    markdown_pdf_spine = canonicalize_markdown_pdf_spine(
        markdown_pdf_spine,
        page_structure,
        canonical_evidence,
    )
    write_json(analysis_dir / "markdown_pdf_spine.json", markdown_pdf_spine)

    print("[6/8] Build page-layout spine from canonical page maps; DOCX donor disabled")
    page_layout_spine = build_page_layout_spine(
        markdown_pdf_spine,
        page_structure,
        None,
        mathpix_lines_path=lines_path,
    )
    exact_recovery = recover_exact_mathpix_layouts(
        page_layout_spine,
        markdown_pdf_spine,
        page_structure,
    )
    write_json(analysis_dir / "mathpix_exact_layout_recovery.json", exact_recovery)

    preview_recovery = {}
    if args.preview_unresolved:
        preview_recovery = prepare_preview_recovery_layer(
            page_layout_spine,
            page_structure,
        )
        write_json(analysis_dir / "preview_recovery_layer.json", preview_recovery)

    write_json(analysis_dir / "page_layout_spine.json", page_layout_spine)
    if args.preview_unresolved:
        write_json(analysis_dir / "page_structure_preview.json", page_structure)

    print("[7/8] Prepare neutral legacy API shim (not a content/typography donor)")
    shim_path = work_dir / "__empty_renderer_api_shim.docx"
    docx_analysis = _blank_docx_shim(shim_path)
    alignment = {
        "summary": {"candidate_docx_paragraph_range": [0, 0]},
        "matches": [],
        "policy": "package-first-no-docx-alignment",
    }

    suffix = "_preview" if args.preview_unresolved else ""
    final_docx = output / f"native_page_structure_{args.pages.replace(',', '_')}_package_first{suffix}.docx"
    mode_text = " [diagnostic preview: unresolved omitted]" if args.preview_unresolved else ""
    print(f"[8/8] Build Word document: {final_docx.name}{mode_text}")
    report = build_native_page_document(
        pdf_analysis,
        page_structure,
        alignment,
        docx_analysis,
        style_profile,
        final_docx,
        body_size_override=None,
        font_scale=1.0,
        gap_scale=0.72,
        body_line_spacing_multiple=None,
        docx_donor_map=None,
        page_layout_spine=page_layout_spine,
        flow_mode="free",
        allow_unresolved_preview=bool(args.preview_unresolved),
    )
    if isinstance(report, dict):
        report["package_first_entrypoint"] = {
            "version": VERSION,
            "pdf": str(pdf_path),
            "pdfSource": "explicit-override" if args.pdf else "inside-mathpix-package",
            "mathpixPackage": str(package_zip),
            "mathpixLines": str(lines_path),
            "mathpixLinesMode": "lines_first",
            "canonicalMmd": str(mmd_path),
            "pages": physical_pages,
            "pdfOrdinals": pdf_ordinals,
            "pageMapping": page_mapping,
            "docxDonorEnabled": False,
            "alignmentEnabled": False,
            "rendererApiShim": str(shim_path),
            "contentAuthority": "canonical Mathpix MMD + Lines evidence",
            "physicalAuthority": "package PDF page_structure; PDF-first visual ownership",
            "typographyAuthority": "Mathpix Lines local size evidence plus PDF evidence when available",
            "canonicalEvidenceSummary": canonical_evidence.get("summary") or {},
            "canonicalPageStructureAdapter": canonical_adapter,
            "mathpixExactLayoutRecovery": exact_recovery,
            "mmdBlockRefinement": markdown_element_map.get("mmdBlockRefinement") or {},
            "diagnosticPreview": bool(args.preview_unresolved),
            "previewRecoveryLayer": preview_recovery,
        }
    write_json(analysis_dir / "build_report.json", report or {})

    summary = {
        "status": "PASS",
        "version": VERSION,
        "pages": [physical_pages[0], physical_pages[-1]],
        "pdfOrdinals": [pdf_ordinals[0], pdf_ordinals[-1]],
        "pageCount": len(physical_pages),
        "outputDocx": str(final_docx),
        "pdfSource": "explicit-override" if args.pdf else "inside-mathpix-package",
        "pageMappingMode": page_mapping["mode"],
        "mathpixLinesMode": "lines_first",
        "diagnosticPreview": bool(args.preview_unresolved),
        "previewRecoveryLayer": preview_recovery,
        "canonicalEvidenceSummary": canonical_evidence.get("summary") or {},
        "canonicalPageStructureAdapter": canonical_adapter,
        "markdownRecordCount": int(markdown_element_map.get("count") or 0),
        "spineCoverage": markdown_pdf_spine.get("coverage"),
        "layoutCoverage": ((page_layout_spine.get("summary") or {}).get("coverage")),
        "textStyleMapSummary": page_structure.get("textStyleMapSummary") or {},
        "mmdBlockRefinement": markdown_element_map.get("mmdBlockRefinement") or {},
        "mathpixExactLayoutRecovery": {
            "recoveredCount": exact_recovery.get("recoveredCount"),
            "unresolvedCount": exact_recovery.get("unresolvedCount"),
        },
        "docxDonorEnabled": False,
    }
    write_json(analysis_dir / "package_first_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
