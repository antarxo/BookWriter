from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path


LINES_ONLY_TOP_LEVEL_KEYS = (
    "mathpixLineLayoutMap",
    "mathpixLinesSummary",
)
LINES_ONLY_PAGE_KEYS = (
    "mathpixLinePageMap",
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run the existing fidelity -> reconstructed DOCX -> canonical Word pipeline, "
            "optionally allowing Mathpix Lines to reconcile the existing page_structure fields."
        )
    )
    p.add_argument("--pdf", required=True, type=Path)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--lines", type=Path, default=None, help="Optional Mathpix result.lines.json")
    p.add_argument("--pages", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--calibration", choices=("none", "fast", "full"), default="none")
    p.add_argument("--strict-page-count", action="store_true")
    p.add_argument("--no-render", action="store_true")
    return p


def _strip_lines_only_interface_fields(result: dict) -> dict:
    """Keep the downstream page_structure interface identical with Lines ON/OFF.

    Lines may improve values of fields that already belong to page_structure, but
    Lines-specific witness objects are diagnostics, not part of the production
    interface consumed by the next module.
    """
    for key in LINES_ONLY_TOP_LEVEL_KEYS:
        result.pop(key, None)
    for page in result.get("pages", []) or []:
        for key in LINES_ONLY_PAGE_KEYS:
            page.pop(key, None)
    return result


def main() -> int:
    args = build_parser().parse_args()
    pdf = args.pdf.resolve()
    source = args.source.resolve()
    lines = args.lines.resolve() if args.lines else None
    output = args.output.resolve()

    for path, label in ((pdf, "PDF"), (source, "Mathpix source")):
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")
    if lines is not None and not lines.exists():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")

    import pdf_word_reconstructor.cli as recon_cli
    import pdf_word_reconstructor.page_structure as page_structure_module
    from pdf_word_canonical_pipeline.pipeline import main as canonical_pipeline_main

    # With Lines OFF there is no patch at all: the original pipeline executes as-is.
    if lines is not None:
        original_build_page_structure = page_structure_module.build_page_structure

        def lines_reconciled_build_page_structure(
            pdf_analysis,
            work_dir,
            asset_dir,
            reference_docx=None,
            external_asset_paths=None,
            equation_donor_path=None,
            mathpix_lines_path=None,
        ):
            result = original_build_page_structure(
                pdf_analysis,
                work_dir,
                asset_dir,
                reference_docx=reference_docx,
                external_asset_paths=external_asset_paths,
                equation_donor_path=equation_donor_path,
                mathpix_lines_path=lines,
            )

            # Preserve the Lines witness externally before restoring the ordinary
            # page_structure interface expected by every downstream module.
            evidence = {
                "mathpixLineLayoutMap": deepcopy(result.get("mathpixLineLayoutMap")),
                "mathpixLinesSummary": deepcopy(result.get("mathpixLinesSummary")),
                "pages": [
                    {
                        "page": page.get("page"),
                        "mathpixLinePageMap": deepcopy(page.get("mathpixLinePageMap")),
                    }
                    for page in result.get("pages", []) or []
                    if page.get("mathpixLinePageMap") is not None
                ],
                "policy": (
                    "Lines is an internal witness for page_structure reconciliation. "
                    "The downstream production schema is unchanged."
                ),
            }
            evidence_path = Path(work_dir) / "MATHPIX_LINES_PAGE_STRUCTURE_EVIDENCE.json"
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            return _strip_lines_only_interface_fields(result)

        # cli.py imports build_page_structure by name, so replace only that boundary.
        recon_cli.build_page_structure = lines_reconciled_build_page_structure

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
    mode = "LINES_ON" if lines is not None else "LINES_OFF"
    manifest = {
        "mode": mode,
        "pdf": str(pdf),
        "source": str(source),
        "lines": str(lines) if lines is not None else None,
        "pages": args.pages,
        "calibration": args.calibration,
        "contract": {
            "linesScope": "inside page_structure only",
            "downstreamSchema": "unchanged",
            "downstreamModules": "unchanged",
            "renderer": "unchanged",
            "canonicalCleanup": "unchanged",
            "futureSchemaEvolution": (
                "Allowed only as an explicit general schema revision with coordinated producer/consumer changes; "
                "never as a hidden Lines-specific extension."
            ),
        },
    }
    (output / "LINES_AB_MODE.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nFIDELITY A/B MODE: {mode}")
    print(f"PDF    : {pdf}")
    print(f"SOURCE : {source}")
    print(f"LINES  : {lines if lines is not None else 'OFF'}")
    print(f"PAGES  : {args.pages}")
    print("CONTRACT: Lines may refine page_structure values; downstream interface and modules stay unchanged")
    return int(canonical_pipeline_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
