from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any

from pdf_word_canonical_pipeline.markdown_equation_donor import extract_markdown_equations
from pdf_word_canonical_pipeline.markdown_element_map_v03 import extract_markdown_element_map

from .build_contract import build_build_contract
from .common import compact_text, parse_page_range, write_json
from .donorless_equation_groups import bind_display_equations_to_pdf_groups
from .mapping_fidelity import build_mapping_fidelity
from .markdown_pdf_spine import build_markdown_pdf_spine
from .native_builder import build_native_page_document
from .page_layout_spine import build_page_layout_spine
from .page_structure import build_page_structure
from .pdf_analyzer import analyze_pdf
from .region_classifier_v02 import classify_pdf_regions
from .style_profile import build_style_profile


VERSION = "donorless-reconstruction-0.6"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".svg"}
_GENERATED_DIRS = ("work", "analysis", "page_assets", "markdown_package")
_GENERATED_FILES = ("reconstructed.docx", "DONORLESS_REPORT.json")


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


def _reset_generated_output(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in _GENERATED_DIRS:
        path = output_dir / name
        if path.exists():
            shutil.rmtree(path)
    for name in _GENERATED_FILES:
        path = output_dir / name
        if path.exists():
            path.unlink()


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


def _pdf_equation_candidates(pdf_analysis: dict[str, Any], page_no: int) -> list[dict[str, Any]]:
    page = next(
        (page for page in (pdf_analysis or {}).get("pages", []) or [] if int(page.get("page") or 0) == page_no),
        None,
    )
    result: list[dict[str, Any]] = []
    for region in (page or {}).get("regions", []) or []:
        if region.get("type") != "text":
            continue
        semantic = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
        if str(semantic.get("type") or "") != "equation":
            continue
        stats = semantic.get("stats") if isinstance(semantic.get("stats"), dict) else {}
        result.append({
            "id": region.get("id"),
            "bbox": region.get("bbox"),
            "text": compact_text(str(region.get("text") or ""), 260),
            "confidence": semantic.get("confidence"),
            "reasons": list(semantic.get("reasons") or []),
            "stats": {
                key: stats.get(key)
                for key in (
                    "math_ratio", "alpha_ratio", "private_use", "line_count", "char_count",
                    "width_ratio", "x0_ratio", "x1_ratio", "y0_ratio", "y1_ratio",
                )
            },
        })
    return result


def _equation_classification_audit(
    build_contract: dict[str, Any],
    page_layout_spine: dict[str, Any],
    pdf_analysis: dict[str, Any],
    equation_group_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows_by_id = {
        str(row.get("markdownId") or ""): row
        for row in (page_layout_spine or {}).get("rows", []) or []
        if row.get("markdownId")
    }
    items: list[dict[str, Any]] = []
    for contract_item in build_contract.get("items", []) or []:
        reasons = list(contract_item.get("unresolved") or [])
        if "missing-layout-contract" not in reasons:
            continue
        if str(contract_item.get("markdownType") or "") != "display_equation":
            continue
        markdown_id = str(contract_item.get("markdownId") or "")
        row = rows_by_id.get(markdown_id) or {}
        placement = contract_item.get("placement") if isinstance(contract_item.get("placement"), dict) else {}
        page_no = int(placement.get("page") or 0)
        content = contract_item.get("content") if isinstance(contract_item.get("content"), dict) else {}
        markdown_text = str(
            content.get("latex")
            or content.get("raw")
            or content.get("text")
            or content.get("plainText")
            or contract_item.get("rawMarkdown")
            or row.get("markdownText")
            or ""
        )
        items.append({
            "markdownId": markdown_id,
            "page": page_no,
            "markdownType": contract_item.get("markdownType"),
            "rawMarkdown": compact_text(str(contract_item.get("rawMarkdown") or ""), 500),
            "markdownOrLatex": compact_text(markdown_text, 500),
            "markdownPdfSpine": {
                "status": row.get("status"),
                "pdfRegion": (row.get("layout") or {}).get("slotId"),
                "bbox": (row.get("pdfGeometry") or {}).get("bbox") or placement.get("bbox"),
                "pdfTypographySource": (row.get("pdfTypography") or {}).get("source"),
            },
            "pdfEquationCandidates": _pdf_equation_candidates(pdf_analysis, page_no),
        })
    return {
        "version": "equation-classification-audit-0.3",
        "purpose": "Distinguish real Markdown/PDF equation mapping gaps from false equation classification and fragmented PDF equation regions.",
        "policy": "diagnostic-only-after-clustered-group-binding; no raw-fragment binding",
        "unresolvedDisplayEquationCount": len(items),
        "equationGroupBinding": equation_group_binding or {},
        "items": items,
    }


def _classification_error_message(build_contract: dict[str, Any], audit: dict[str, Any]) -> str:
    summary = build_contract.get("summary") or {}
    unresolved = int(summary.get("unresolvedCount") or 0)
    reasons = summary.get("unresolvedReasonCounts") or {}
    details = ", ".join(f"{key}={value}" for key, value in sorted(reasons.items()))
    group_pages = (audit.get("equationGroupBinding") or {}).get("pages") or []
    group_preview = "; ".join(
        f"p{row.get('page')}:MD={row.get('unplacedMarkdownDisplayEquationCount')}/groups={row.get('pdfEquationGroupCount')}/bound={row.get('boundCount')}"
        for row in group_pages
        if row.get("unplacedMarkdownDisplayEquationCount")
    )
    mismatch_preview_parts: list[str] = []
    for row in group_pages:
        if not row.get("unplacedMarkdownDisplayEquationCount"):
            continue
        if row.get("unplacedMarkdownDisplayEquationCount") == row.get("pdfEquationGroupCount"):
            continue
        groups = row.get("groups") or []
        group_texts = []
        for index, group in enumerate(groups[:7], start=1):
            member_text = " | ".join(
                compact_text(str(member.get("text") or ""), 70)
                for member in (group.get("members") or [])[:3]
                if str(member.get("text") or "").strip()
            )
            group_texts.append(f"g{index}={member_text or '∅'}")
        mismatch_preview_parts.append(
            f"p{row.get('page')}[{'; '.join(group_texts)}]"
        )
    samples: list[str] = []
    for item in (audit.get("items") or [])[:4]:
        candidates = item.get("pdfEquationCandidates") or []
        candidate_preview = "; ".join(
            f"{candidate.get('id')}:{candidate.get('text') or '∅'}"
            for candidate in candidates[:3]
        ) or "none"
        samples.append(
            f"{item.get('markdownId')}@p{item.get('page')} "
            f"MD={compact_text(str(item.get('markdownOrLatex') or ''), 90)!r} "
            f"PDFeqFragments={len(candidates)}[{compact_text(candidate_preview, 180)}]"
        )
    suffix = ""
    if group_preview:
        suffix += " | equation groups: " + group_preview
    if mismatch_preview_parts:
        suffix += " | group evidence: " + " || ".join(mismatch_preview_parts[:3])
    if samples:
        suffix += " | equation audit: " + " || ".join(samples)
    return (
        "Maps-first build contract unresolved: "
        f"{unresolved}/{int(summary.get('itemCount') or 0)} item(s). "
        f"{details or 'See build_contract.json.'}{suffix}"
    )


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
    _reset_generated_output(output_dir)

    if not pdf_path.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε PDF upload: {pdf_path}")
    if not markdown_zip.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε Markdown ZIP upload: {markdown_zip}")

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
    equation_group_binding = bind_display_equations_to_pdf_groups(markdown_pdf_spine, page_structure, pdf_analysis)
    write_json(analysis_dir / "equation_group_binding.json", equation_group_binding)
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

    build_contract = build_build_contract(page_layout_spine)
    write_json(analysis_dir / "build_contract.json", build_contract)
    if int((build_contract.get("summary") or {}).get("unresolvedCount") or 0):
        equation_audit = _equation_classification_audit(
            build_contract,
            page_layout_spine,
            pdf_analysis,
            equation_group_binding,
        )
        write_json(analysis_dir / "equation_classification_audit.json", equation_audit)
        raise RuntimeError(_classification_error_message(build_contract, equation_audit))

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
        "equationGroupBinding": equation_group_binding,
        "mappingPreflight": mapping_preflight,
        "pageLayoutSummary": page_layout_spine.get("summary") or {},
        "buildReport": build_report,
        "outputDocx": str(final_docx),
    }
    write_json(output_dir / "DONORLESS_REPORT.json", report)
    return report
