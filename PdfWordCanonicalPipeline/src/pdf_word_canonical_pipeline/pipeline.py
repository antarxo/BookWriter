from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from zipfile import ZipFile
from pathlib import Path

from pdf_word_reconstructor.cli import main as reconstructor_main
from .word_normalizer import build_parser as build_normalizer_parser, normalize_docx
from .word_composite_rasterizer import inspect_docx_complexity, rasterize_complex_objects
from .mathpix_input_collector import collect_mathpix_inputs
from .word_page_mapper import extract_word_page_map
from .word_vector_preview_converter import convert_vector_previews_in_docx
from .word_group_surrogate_renderer import render_required_group_surrogates

VERSION = "0.4.7zg-hf55-word-typography-triple-probe"


def _find_reconstructed_docx(output_dir: Path, pages: str) -> Path:
    expected = output_dir / f"native_page_structure_{pages.replace(',', '_')}.docx"
    if expected.exists():
        return expected
    candidates = sorted(output_dir.glob("native_page_structure_*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"Ξ”ΞµΞ½ Ξ²ΟΞ­ΞΈΞ·ΞΊΞµ reconstructed DOCX ΟƒΟ„ΞΏ {output_dir}")
    return candidates[0]


def canonicalize(
    input_docx: Path,
    output_docx: Path,
    sections: str = "all",
    report: Path | None = None,
    composite_policy: str = "auto",
) -> dict:
    """Canonicalize a DOCX through one explicit composite policy.

    Policies:
      off    Never invoke Word COM. Preserve native DOCX structure for the
             normalizer/browser importer. HF8 retains this deliberately so native
             DrawingML failures remain visible instead of being rasterized.
      auto   Inspect first and invoke Word COM only when complex compounds are
             detected. Intended for ordinary user DOCX imports.
      strict Always invoke Word COM and require a successful audited pass, even
             when the static detector sees no complex compound. Intended for
             regression diagnostics.
    """
    input_docx = Path(input_docx).resolve()
    output_docx = Path(output_docx).resolve()
    report_path = Path(report or output_docx.with_suffix('.normalization_report.json')).resolve()
    policy = str(composite_policy or "auto").strip().lower()
    if policy not in {"off", "auto", "strict"}:
        raise ValueError(f"Ξ†Ξ³Ξ½Ο‰ΟƒΟ„Ξ· composite policy: {composite_policy!r}. Ξ•Ο€ΞΉΟ„ΟΞµΟ€Ο„Ξ­Ο‚: off, auto, strict.")

    complexity = inspect_docx_complexity(input_docx)
    detected = bool(complexity.get("requiresWordCompositeRasterization"))
    composite = {
        "version": VERSION,
        "policy": policy,
        "staticInventory": complexity,
        "detected": detected,
        "required": bool(detected and policy != "off"),
        "rasterizedCount": 0,
        "failedCount": 0,
        "action": "not-required",
    }

    with tempfile.TemporaryDirectory(prefix="bookwriter_canonical_gateway_") as td:
        source_for_normalizer = input_docx
        run_word_com = policy == "strict" or (policy == "auto" and detected)

        if policy == "off":
            composite["action"] = "skipped-by-policy"
            if detected:
                composite["warning"] = (
                    "HF8 native visual/anchor probe: ΞµΞ½Ο„ΞΏΟ€Ξ―ΟƒΟ„Ξ·ΞΊΞ±Ξ½ ΟƒΟΞ½ΞΈΞµΟ„Ξ± Word Ξ±Ξ½Ο„ΞΉΞΊΞµΞ―ΞΌΞµΞ½Ξ± ΞΊΞ±ΞΉ "
                    "Ξ΄ΞΉΞ±Ο„Ξ·ΟΞ®ΞΈΞ·ΞΊΞ±Ξ½ ΟƒΞΊΟΟ€ΞΉΞΌΞ± native. Ξ”ΞµΞ½ ΞµΞΊΟ„ΞµΞ»Ξ­ΟƒΟ„Ξ·ΞΊΞµ Word-COM rasterization "
                    "ΞΊΞ±ΞΉ Ξ΄ΞµΞ½ ΞµΞ½ΞµΟΞ³ΞΏΟ€ΞΏΞΉΞ®ΞΈΞ·ΞΊΞµ fallback ΞµΞΉΞΊΟΞ½Ξ±Ο‚."
                )
        elif run_word_com:
            preprocessed = Path(td) / f"{input_docx.stem}_COMPOSITES_AS_PICTURES.docx"
            composite_report = Path(td) / "composite_rasterization_report.json"
            try:
                composite = rasterize_complex_objects(input_docx, preprocessed, composite_report)
                composite["policy"] = policy
                composite["detected"] = detected
                composite["action"] = "word-com-rasterized" if composite.get("rasterizedCount") else "word-com-equation-salvage"
                if policy == "strict" and (
                    int(composite.get("backgroundMissingCount") or 0) > 0
                    or bool(composite.get("unconvertedComplexObjectsRemain"))
                ):
                    raise RuntimeError(
                        "Ξ— strict Ο€ΞΏΞ»ΞΉΟ„ΞΉΞΊΞ® Ξ±Ο€Ξ±ΞΉΟ„ΞµΞ― Ο€ΞΉΟƒΟ„Ο Ο…Ο€ΟΞ²Ξ±ΞΈΟΞΏ Ξ³ΞΉΞ± ΟΞ»Ξ± Ο„Ξ± ΟƒΟΞ½ΞΈΞµΟ„Ξ± Ξ±Ξ½Ο„ΞΉΞΊΞµΞ―ΞΌΞµΞ½Ξ±. "
                        f"Ξ›ΞµΞ―Ο€ΞΏΟ…Ξ½ {int(composite.get('backgroundMissingCount') or 0)} Ο…Ο€ΟΞ²Ξ±ΞΈΟΞ±."
                    )
                source_for_normalizer = preprocessed
            except Exception as exc:
                detailed_composite = dict(composite)
                if composite_report.exists():
                    try:
                        detailed_composite = json.loads(composite_report.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                failure = {
                    "version": VERSION,
                    "action": "stopped-before-canonicalization",
                    "originalInput": str(input_docx),
                    "output": str(output_docx),
                    "compositePolicy": policy,
                    "compositeRasterization": {
                        **detailed_composite,
                        "action": "failed-or-unavailable",
                        "error": str(exc),
                    },
                    "notDone": [
                        "No canonical DOCX was emitted because the requested composite policy could not complete safely.",
                        "No browser-side side-note fallback was attempted.",
                    ],
                }
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
                raise
        else:
            composite["action"] = "not-detected-auto-pass"

        parser = build_normalizer_parser()
        args = parser.parse_args([
            str(source_for_normalizer), str(output_docx),
            "--sections", sections,
            "--strategy", "standard",
            "--report", str(report_path),
        ])
        result = normalize_docx(args)
        # HF24: add browser-only PNG surrogates for legacy WMF/EMF/OLE without
        # changing any Word relationship. The Word-rendered page map therefore
        # remains valid even in documents with many vector/OLE objects in tables.
        vector_preview_conversion = convert_vector_previews_in_docx(output_docx)
        # HF24: Word direct-bitmap capture is the primary visual authority for
        # groups that fail the explicit native/hybrid fidelity contract.  The
        # source groups are never replaced; browser-only PNG surrogates are
        # embedded in a sidecar manifest keyed by top-level group ordinal.
        # Browser-only group surrogates must not modify the deliverable DOCX.
        # Generate them against a temporary copy and externalize the manifest/media.
        group_probe_docx = Path(td) / f"{output_docx.stem}_GROUP_SURROGATE_PROBE.docx"
        shutil.copy2(output_docx, group_probe_docx)

        group_surrogate_conversion = render_required_group_surrogates(
            input_docx,
            group_probe_docx,
        )

        group_sidecar_dir = output_docx.parent / f"{output_docx.stem}_group_surrogates"
        group_sidecar_dir.mkdir(parents=True, exist_ok=True)

        with ZipFile(group_probe_docx, "r") as zf:
            names = set(zf.namelist())
            manifest_name = "customXml/bookwriter-group-surrogates.json"

            if manifest_name in names:
                (group_sidecar_dir / "manifest.json").write_bytes(
                    zf.read(manifest_name)
                )

            media_dir = group_sidecar_dir / "media"
            for name in sorted(names):
                if name.startswith("word/media/bw_group_surrogate_"):
                    media_dir.mkdir(parents=True, exist_ok=True)
                    (media_dir / Path(name).name).write_bytes(zf.read(name))
        # Page ownership must describe the exact canonical DOCX consumed by BookWriter.
        # Repaginate the final DOCX, after canonicalization/surrogate preparation.
        word_page_map = extract_word_page_map(output_docx) if str(sections or "all").strip().lower() == "all" else {
            "version": 3,
            "source": "word-rendered-page-map-v3-list-row-fragments",
            "available": False,
            "status": "skipped-partial-section-selection",
            "pageCount": 0,
            "blocks": {},
        }
        # Page ownership is pipeline state, not Word document content.
        # Keep it beside the DOCX instead of embedding ad-hoc customXml.
        page_map_sidecar = output_docx.with_suffix(".page-map.json")
        page_map_sidecar.write_text(
            json.dumps(word_page_map, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    result["originalInput"] = str(input_docx)
    result["wordPageMapSidecar"] = str(page_map_sidecar)
    result["groupSurrogateSidecar"] = str(group_sidecar_dir)
    result["wordPageMap"] = {
        "version": word_page_map.get("version", 3),
        "source": word_page_map.get("source", "word-rendered-page-map-v3-list-row-fragments"),
        "available": bool(word_page_map.get("available")),
        "status": word_page_map.get("status", "unknown"),
        "pageCount": int(word_page_map.get("pageCount") or 0),
        "topLevelBlocks": int(word_page_map.get("topLevelBlocks") or 0),
        "mappedBlocks": int(word_page_map.get("mappedBlocks") or 0),
        "listValuesMapped": int(word_page_map.get("listValuesMapped") or 0),
        "pageQueries": int(word_page_map.get("pageQueries") or 0),
        "spanningTableRows": int(word_page_map.get("spanningTableRows") or 0),
        "tableParagraphPageQueries": int(word_page_map.get("tableParagraphPageQueries") or 0),
        "missingMarkers": len(word_page_map.get("missingMarkers") or []),
        **({"error": str(word_page_map.get("error"))} if word_page_map.get("error") else {}),
    }
    result["compositePolicy"] = policy
    result["compositeRasterization"] = composite
    result["vectorPreviewConversion"] = vector_preview_conversion
    result["groupSurrogateConversion"] = group_surrogate_conversion
    ole_attempted = int(vector_preview_conversion.get("oleUniquePayloads") or 0)
    ole_rendered = int(vector_preview_conversion.get("oleRenderedPayloads") or 0)
    result["compositeTriage"] = {
        "status": group_surrogate_conversion.get("status", "unknown"),
        "groupCount": int(group_surrogate_conversion.get("groupCount") or 0),
        "referencesCaptured": int(group_surrogate_conversion.get("referencesCaptured") or 0),
        "referencesFailed": int(group_surrogate_conversion.get("referencesFailed") or 0),
        "geometryMismatch": int(group_surrogate_conversion.get("geometryMismatch") or 0),
        "contentMismatch": int(group_surrogate_conversion.get("contentMismatch") or 0),
        "wordReferenceSurrogates": int(group_surrogate_conversion.get("rendered") or 0),
        "sourceCropRequired": int(group_surrogate_conversion.get("sourceCropRequired") or 0),
        "browserValidationRequired": int(group_surrogate_conversion.get("browserValidationRequired") or 0),
        "failedIds": [gid for gid,row in (group_surrogate_conversion.get("groups") or {}).items() if str(row.get("status") or "") not in {"word-reference-ok"}],
        "oleOccurrences": int(vector_preview_conversion.get("oleOccurrences") or 0),
        "oleUniquePayloads": ole_attempted,
        "oleRenderedPayloads": ole_rendered,
        "oleFailedPayloads": max(0, ole_attempted - ole_rendered),
        "oleMappedOccurrences": int(vector_preview_conversion.get("oleMappedOccurrences") or 0),
        "oleRenderer": str(vector_preview_conversion.get("oleOccurrenceRenderer") or ""),
        "oleDirectBitmapPayloads": int(vector_preview_conversion.get("oleDirectBitmapPayloads") or 0),
        "oleEnhMetafilePayloads": int(vector_preview_conversion.get("oleEnhMetafilePayloads") or 0),
        "oleWordPdfPayloads": int(vector_preview_conversion.get("oleWordPdfPayloads") or 0),
        "oleFreshWordRecoveredPayloads": int(vector_preview_conversion.get("oleFreshWordRecoveredPayloads") or 0),
        "olePowerPointFallbackPayloads": int(vector_preview_conversion.get("olePowerPointFallbackPayloads") or 0),
        "groupDirectBitmapReferences": int(group_surrogate_conversion.get("directBitmapReferences") or 0),
        "groupEnhMetafileReferences": int(group_surrogate_conversion.get("enhMetafileReferences") or 0),
        "groupWordPdfReferences": int(group_surrogate_conversion.get("wordPdfReferences") or 0),
        "groupPowerPointFallbackReferences": int(group_surrogate_conversion.get("powerPointFallbackReferences") or 0),
        "groupSourcePageCropReferences": int(group_surrogate_conversion.get("sourcePageCropReferences") or 0),
        "oleFailureRecords": [row for row in (vector_preview_conversion.get("oleOccurrenceRecords") or []) if not bool(row.get("rendered"))],
    }
    result["version"] = VERSION
    result["implemented"] = list(result.get("implemented", [])) + [
        "explicit composite policy: off / auto / strict",
        "PDF reconstructor path uses composite-policy off and cannot unexpectedly invoke Word COM",
        "ordinary DOCX gateway keeps legacy composite rasterization off; HF22 uses a non-mutating fidelity-boundary surrogate path instead",
        "faithful composite backgrounds are embedded by direct OOXML package replacement",
        "equations remain recoverable even when a composite background export fails",
        "rendered source-page ownership is captured from Microsoft Word Range.Information, not XML page-break markers",
        "the rendered page map is embedded as customXml/bookwriter-page-map.xml",
        "HF13 preserves the HF12 page-boundary search and adds exact table-row end pages",
        "HF14 keeps the page map unchanged and fixes Word script typography plus native grouped-text flow",
        "HF16 keeps HTML sup/sub at 60% and tightens OMML/MathML script children to 78%",
        "HF16 converts referenced WMF/EMF previews to high-resolution PNG browser surrogates through a non-mutating sidecar manifest; original relationships/vectors/OLE remain authoritative for Word",
        "HF16 imports non-table images/OLE previews nested inside Word text boxes instead of silently dropping them",
        "HF16 natively renders common DrawingML ellipse and line presets instead of diagnostic magenta placeholders",
        "HF17 preserves mixed native DrawingML groups (pic:pic + vector shapes/connectors/text) as one browser composite",
        "HF17 preserves common connector dash/arrowhead semantics and prevents native-group pictures from being emitted twice",
        "HF22 classifies top-level Word groups as native-safe / hybrid-safe / render-required and asks Word to render only the render-required groups as browser-only whole-composite surrogates",
        "HF22 never mutates the original DrawingML group or its Word relationships while generating the whole-composite browser surrogate",
        "HF24 captures Word CopyAsPicture raster data directly from the Windows clipboard before any PowerPoint conversion",
        "HF24 isolates PowerPoint fallback in a fresh process per failed payload so one COM exception cannot poison later OLE exports",
        "HF24 records OLE triage IDs and paragraph/table/row/cell/page location for deterministic per-object failure handling",
        "HF25 renders Word CF_ENHMETAFILE clipboard data directly with Windows GDI for difficult DrawingML group references",
        "HF26 restores Word-PDF/PyMuPDF as the non-bitmap fallback for legacy OLE payloads instead of accepting tiny GDI metafile surrogates",
        "HF26 uses a Word-rendered source-page crop as the terminal surrogate for inline groups already classified render-required",
        "multilevel list visible markers are preserved from Word/numbering metadata",
        "spanning table rows expose cell-paragraph page ownership only when needed",
        "Word-rendered list ordinals are preserved when available",
    ]
    result["temporary_or_incomplete"] = [
        note for note in list(result.get("temporary_or_incomplete", []))
        if "Microsoft Word COM repagination is not performed" not in str(note)
    ] + [
        "strict policy always requires desktop Microsoft Word on Windows",
        "equation-bearing grouped text boxes become editable overlays over a faithful background when available",
        "auto policy preserves equation-only fallback when a composite background cannot be rendered; strict policy rejects missing backgrounds",
        "off must not be used as a general bypass for arbitrary complex Word documents",
        "when desktop Word page mapping is unavailable the browser importer falls back to reflow rather than a synthetic source-page lock",
        "WMF/EMF web-surrogate conversion requires desktop PowerPoint on Windows when such media are present; failure leaves the original vector relationship untouched and is reported",
    ]
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["report"] = str(report_path)
    return result

def _execute_pdf_pipeline(
    *,
    pdf: Path,
    reference_docx: Path,
    pages: str,
    output: Path,
    calibration: str,
    strict_page_count: bool,
    no_render: bool,
    composite_policy: str,
    external_assets: list[Path] | None = None,
    mode: str = "pdf-to-canonical-docx",
    input_manifest: dict | None = None,
    equation_donors: Path | None = None,
    progress_report: Path | None = None,
) -> int:
    work = output.resolve()
    recon_out = work / "01_reconstructed"
    canonical_out = work / "02_canonical"
    recon_out.mkdir(parents=True, exist_ok=True)
    canonical_out.mkdir(parents=True, exist_ok=True)

    recon_argv = [
        "--pdf", str(pdf.resolve()),
        "--docx", str(reference_docx.resolve()),
        "--pages", pages,
        "--output", str(recon_out),
        "--calibration", calibration,
    ]
    for asset_root in external_assets or []:
        recon_argv.extend(["--external-assets", str(Path(asset_root).resolve())])
    if equation_donors:
        recon_argv.extend(["--equation-donors", str(Path(equation_donors).resolve())])
    if progress_report:
        recon_argv.extend(["--progress-report", str(Path(progress_report).resolve())])
    markdown_element_map = (input_manifest or {}).get("markdownElementMap") if input_manifest else None
    if markdown_element_map:
        recon_argv.extend(["--markdown-element-map", str(Path(markdown_element_map).resolve())])
    if strict_page_count:
        recon_argv.append("--strict-page-count")
    if no_render:
        recon_argv.append("--no-render")
    code = reconstructor_main(recon_argv)
    if code:
        return int(code)

    reconstructed = _find_reconstructed_docx(recon_out, pages)
    canonical = canonical_out / f"{reconstructed.stem}_BOOKWRITER.docx"
    report = canonical_out / f"{reconstructed.stem}_BOOKWRITER.normalization_report.json"
    canonicalize(reconstructed, canonical, "all", report, composite_policy)
    fidelity_fallback_report = recon_out / "analysis" / "fidelity_fallback_report.json"
    conversion_spine = recon_out / "analysis" / "conversion_spine.json"
    docx_donor_map = recon_out / "analysis" / "docx_donor_map.json"
    page_layout_spine = recon_out / "analysis" / "page_layout_spine.json"
    architecture_benchmark = recon_out / "analysis" / "architecture_benchmark.json"
    architecture_guard = recon_out / "analysis" / "architecture_guard.json"
    mapping_fidelity = recon_out / "analysis" / "mapping_fidelity.json"

    manifest = {
        "pipelineVersion": VERSION,
        "mode": mode,
        "pdf": str(pdf.resolve()),
        "referenceDocx": str(reference_docx.resolve()),
        "pages": pages,
        "reconstructedDocx": str(reconstructed),
        "canonicalDocx": str(canonical),
        "normalizationReport": str(report),
        "fidelityFallbackReport": str(fidelity_fallback_report) if fidelity_fallback_report.exists() else None,
        "conversionSpine": str(conversion_spine) if conversion_spine.exists() else None,
        "docxDonorMap": str(docx_donor_map) if docx_donor_map.exists() else None,
        "pageLayoutSpine": str(page_layout_spine) if page_layout_spine.exists() else None,
        "architectureBenchmark": str(architecture_benchmark) if architecture_benchmark.exists() else None,
        "architectureGuard": str(architecture_guard) if architecture_guard.exists() else None,
        "mappingFidelity": str(mapping_fidelity) if mapping_fidelity.exists() else None,
        "calibration": calibration,
        "strictPageCount": bool(strict_page_count),
        "compositePolicy": composite_policy,
        "externalAssetRoots": [str(Path(path).resolve()) for path in external_assets or []],
        "inputPackage": input_manifest or None,
        "markdownEquationDonors": str(equation_donors) if equation_donors else None,
        "progressReport": str(progress_report.resolve()) if progress_report else None,
        "implemented": [
            "automatic Mathpix input collection for folder or all-formats ZIP",
            "coordinate-based asset-centric reuse of positioned Mathpix images",
            "native SVG relationship writing with raster fallback when an SVG-backed asset is selected",
            "equal-width two-column detection with robust outlier rejection",
            "page-relative editable captions for floating figures when required to preserve source pagination",
            "optimized sequence-aware PDF-to-DOCX paragraph alignment",
            "Markdown LaTeX donor recovery to native editable OMML for high-confidence equation matches",
        ],
        "notDone": [
            "No full Word contour-wrap equivalence.",
            "Some equation and multi-part composite regions still require a PDF page crop.",
            "Composite figures are not decomposed into editable image, equation and text overlays.",
            "BookWriter typography scale is user-controlled at import time; this PDF pipeline does not choose a percentage automatically.",
            "Microsoft Word and BookWriter print parity require the user Windows test.",
        ],
    }
    (work / "PIPELINE_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nCANONICAL DOCX: {canonical}")
    return 0


def run_pdf(args: argparse.Namespace) -> int:
    return _execute_pdf_pipeline(
        pdf=args.pdf, reference_docx=args.reference_docx, pages=args.pages, output=args.output,
        calibration=args.calibration, strict_page_count=args.strict_page_count, no_render=args.no_render,
        composite_policy=args.composite_policy, external_assets=list(args.external_assets or []),
        progress_report=getattr(args, "progress_report", None),
    )


def run_fidelity(args: argparse.Namespace) -> int:
    work = args.output.resolve()
    collected = collect_mathpix_inputs(args.pdf.resolve(), args.source.resolve(), work / "00_input")
    selected_docx = Path(collected["selectedReferenceDocx"])
    asset_roots = [Path(path) for path in collected.get("assetRoots", [])]
    print("\nMATHPIX INPUT PACKAGE")
    print(f"PDF      : {args.pdf.resolve()}")
    print(f"SOURCE   : {args.source.resolve()}")
    print(f"DOCX     : {selected_docx}")
    print(f"ASSETS   : {collected.get('assetCounts', {})}")
    print(f"MANIFEST : {collected.get('manifestPath')}")
    return _execute_pdf_pipeline(
        pdf=args.pdf, reference_docx=selected_docx, pages=args.pages, output=args.output,
        calibration=args.calibration, strict_page_count=args.strict_page_count, no_render=args.no_render,
        composite_policy="off", external_assets=asset_roots,
        mode="mathpix-fidelity-probe", input_manifest=collected,
        equation_donors=Path(collected["markdownEquationDonors"]) if collected.get("markdownEquationDonors") else None,
        progress_report=getattr(args, "progress_report", None),
    )

def run_docx(args: argparse.Namespace) -> int:
    out = args.output.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    canonicalize(args.docx.resolve(), out, args.sections, args.report, args.composite_policy)
    print(f"\nCANONICAL DOCX: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Unified PDF/DOCX -> BookWriter canonical DOCX pipeline")
    sub = p.add_subparsers(dest="command", required=True)

    pdf = sub.add_parser("pdf", help="PDF + reference DOCX -> reconstructed DOCX -> canonical DOCX")
    pdf.add_argument("--pdf", type=Path, required=True)
    pdf.add_argument("--reference-docx", type=Path, required=True)
    pdf.add_argument("--pages", required=True)
    pdf.add_argument("--output", type=Path, required=True)
    pdf.add_argument("--calibration", choices=("none", "fast", "full"), default="none")
    pdf.add_argument("--strict-page-count", action="store_true")
    pdf.add_argument("--no-render", action="store_true")
    pdf.add_argument("--composite-policy", choices=("off", "auto", "strict"), default="off")
    pdf.add_argument("--external-assets", type=Path, action="append", default=[])
    pdf.add_argument("--progress-report", type=Path)
    pdf.set_defaults(func=run_pdf)

    fidelity = sub.add_parser("fidelity", help="PDF + Mathpix download folder/all-formats ZIP -> automatic input package -> canonical DOCX")
    fidelity.add_argument("--pdf", type=Path, required=True)
    fidelity.add_argument("--source", type=Path, required=True, help="Mathpix download folder or all-formats ZIP")
    fidelity.add_argument("--pages", required=True)
    fidelity.add_argument("--output", type=Path, required=True)
    fidelity.add_argument("--calibration", choices=("none", "fast", "full"), default="none")
    fidelity.add_argument("--strict-page-count", action="store_true")
    fidelity.add_argument("--no-render", action="store_true")
    fidelity.add_argument("--progress-report", type=Path)
    fidelity.set_defaults(func=run_fidelity)

    docx = sub.add_parser("docx", help="Any supported DOCX -> BookWriter canonical DOCX")
    docx.add_argument("--docx", type=Path, required=True)
    docx.add_argument("--output", type=Path, required=True)
    docx.add_argument("--sections", default="all")
    docx.add_argument("--report", type=Path)
    docx.add_argument("--composite-policy", choices=("off", "auto", "strict"), default="auto")
    docx.set_defaults(func=run_docx)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

