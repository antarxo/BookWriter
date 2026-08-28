from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run the existing Mathpix fidelity -> reconstructed DOCX -> canonical Word pipeline, "
            "injecting Mathpix Lines only as additional page-structure/layout evidence."
        )
    )
    p.add_argument("--pdf", required=True, type=Path)
    p.add_argument("--source", required=True, type=Path, help="Existing Mathpix DOCX/MMD source folder or ZIP")
    p.add_argument("--lines", required=True, type=Path, help="Mathpix result.lines.json")
    p.add_argument("--pages", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--calibration", choices=("none", "fast", "full"), default="none")
    p.add_argument("--strict-page-count", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.add_argument(
        "--preview-through-preflight",
        action="store_true",
        help=(
            "Diagnostic preview only: retain the real mapping-fidelity failure report but allow the proven "
            "reconstruction + canonical Word cleanup path to continue so the user can inspect the DOCX visually. "
            "No recovery callouts, slot pruning, or production threshold changes are introduced."
        ),
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    pdf = args.pdf.resolve()
    source = args.source.resolve()
    lines = args.lines.resolve()
    output = args.output.resolve()

    for path, label in ((pdf, "PDF"), (source, "Mathpix source"), (lines, "Mathpix Lines")):
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")

    # IMPORTANT: do not fork/reimplement the proven reconstruction or canonicalization
    # path. Inject Lines only at the existing page-structure evidence boundary.
    import pdf_word_reconstructor.cli as recon_cli
    import pdf_word_reconstructor.page_structure as page_structure_module
    from pdf_word_canonical_pipeline.pipeline import main as canonical_pipeline_main

    original_build_page_structure = page_structure_module.build_page_structure

    def lines_assisted_build_page_structure(
        pdf_analysis,
        work_dir,
        asset_dir,
        reference_docx=None,
        external_asset_paths=None,
        equation_donor_path=None,
        mathpix_lines_path=None,
    ):
        return original_build_page_structure(
            pdf_analysis,
            work_dir,
            asset_dir,
            reference_docx=reference_docx,
            external_asset_paths=external_asset_paths,
            equation_donor_path=equation_donor_path,
            mathpix_lines_path=lines,
        )

    # cli.py imported build_page_structure by name, so patch that bound reference.
    # No production source file is rewritten and no local uncommitted cli.py change is touched.
    recon_cli.build_page_structure = lines_assisted_build_page_structure

    preflight_capture: dict = {}
    if args.preview_through_preflight:
        original_build_mapping_fidelity = recon_cli.build_mapping_fidelity

        def preview_build_mapping_fidelity(*a, **kw):
            report = original_build_mapping_fidelity(*a, **kw)
            if kw.get("require_conversion", True) is False and report.get("status") == "fail":
                preflight_capture.clear()
                preflight_capture.update(report)
                # Only the in-process gate is relaxed. The complete original failure,
                # violations and metrics remain externally recorded by this wrapper.
                preview_report = dict(report)
                preview_report["status"] = "preview-warning"
                preview_report["productionStatus"] = "fail"
                preview_report["previewOverride"] = {
                    "enabled": True,
                    "scope": "diagnostic visual preview only",
                    "reason": "user-requested early visual inspection before evidence reconciliation is complete",
                    "productionThresholdsChanged": False,
                    "recoveryCalloutsAdded": False,
                    "physicalSlotsPruned": False,
                }
                return preview_report
            return report

        recon_cli.build_mapping_fidelity = preview_build_mapping_fidelity

    argv = [
        "fidelity",
        "--pdf", str(pdf),
        "--source", str(source),
        "--pages", args.pages,
        "--output", str(output),
        "--calibration", args.calibration,
    ]
    if args.strict_page_count:
        argv.append("--strict-page-count")
    if args.no_render:
        argv.append("--no-render")

    output.mkdir(parents=True, exist_ok=True)
    evidence_path = output / "LINES_ASSISTED_FIDELITY_INPUT.json"
    evidence_path.write_text(
        json.dumps(
            {
                "mode": "existing-fidelity-pipeline-plus-lines-evidence",
                "pdf": str(pdf),
                "source": str(source),
                "lines": str(lines),
                "pages": args.pages,
                "calibration": args.calibration,
                "previewThroughPreflight": bool(args.preview_through_preflight),
                "policy": [
                    "Preserve the existing DOCX donor and reconstruction path.",
                    "Use Lines only as additional page-structure/layout evidence.",
                    "Preserve the existing canonical Word normalization and cleanup stage.",
                    "Keep Lines/evidence/diagnostics external to the deliverable DOCX.",
                    "If preview-through-preflight is enabled, preserve the original preflight failure externally and relax only the in-process stop gate for visual inspection.",
                    "Do not create recovery callouts and do not prune physical slots.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\nLINES-ASSISTED FIDELITY")
    print(f"PDF    : {pdf}")
    print(f"SOURCE : {source}")
    print(f"LINES  : {lines}")
    print(f"PAGES  : {args.pages}")
    print("MODE   : existing fidelity pipeline + DOCX donor + Lines evidence + canonical Word cleanup")
    if args.preview_through_preflight:
        print("PREVIEW: mapping preflight failure will be retained but will not stop this diagnostic build")

    code = int(canonical_pipeline_main(argv))

    if args.preview_through_preflight:
        preserved = output / "MAPPING_PREFLIGHT_ORIGINAL_FAILURE.json"
        preserved.write_text(
            json.dumps(
                preflight_capture or {
                    "status": "not-captured",
                    "note": "The preflight did not fail or the expected preview interception was not reached.",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"PREFLIGHT ORIGINAL: {preserved}")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
