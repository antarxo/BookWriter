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
                "policy": [
                    "Preserve the existing DOCX donor and reconstruction path.",
                    "Use Lines only as additional page-structure/layout evidence.",
                    "Preserve the existing canonical Word normalization and cleanup stage.",
                    "Keep Lines/evidence/diagnostics external to the deliverable DOCX.",
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
    return int(canonical_pipeline_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
