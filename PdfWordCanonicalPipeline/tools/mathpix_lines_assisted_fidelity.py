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
            "Run the existing fidelity -> reconstructed DOCX -> canonical Word pipeline "
            "with selectable Mathpix Lines priority inside page_structure."
        )
    )
    p.add_argument("--pdf", required=True, type=Path)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--lines", type=Path, default=None, help="Optional Mathpix result.lines.json")
    p.add_argument(
        "--mode",
        choices=("off", "witness", "lines-first"),
        default=None,
        help=(
            "off = ordinary pipeline; witness = current Lines-assisted reconciliation; "
            "lines-first = Lines leads existing structural page_structure fields. "
            "If omitted, mode is inferred as off without --lines and witness with --lines."
        ),
    )
    p.add_argument("--pages", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--calibration", choices=("none", "fast", "full"), default="none")
    p.add_argument("--strict-page-count", action="store_true")
    p.add_argument("--no-render", action="store_true")
    return p


def _strip_lines_only_interface_fields(result: dict) -> dict:
    """Keep the downstream page_structure interface identical in all three modes."""
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
    mode = args.mode or ("witness" if lines is not None else "off")

    for path, label in ((pdf, "PDF"), (source, "Mathpix source")):
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")
    if lines is not None and not lines.exists():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")
    if mode in {"witness", "lines-first"} and lines is None:
        raise ValueError(f"--mode {mode} requires --lines")

    import pdf_word_reconstructor.cli as recon_cli
    import pdf_word_reconstructor.page_structure as page_structure_module
    from pdf_word_canonical_pipeline.pipeline import main as canonical_pipeline_main

    # OFF has no page_structure patch at all. WITNESS and LINES_FIRST share the
    # same boundary and differ only by the internal priority policy.
    if mode != "off":
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
                mathpix_lines_mode=("lines_first" if mode == "lines-first" else "witness"),
            )

            evidence = {
                "mode": mode,
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
                    "Lines evidence is internal to page_structure. "
                    "WITNESS leaves the existing priority model intact; LINES_FIRST lets Lines lead "
                    "existing semantic/column/flow fields while PDF remains physical geometry/typography authority. "
                    "The downstream production schema remains unchanged."
                ),
            }
            evidence_path = Path(work_dir) / "MATHPIX_LINES_PAGE_STRUCTURE_EVIDENCE.json"
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            return _strip_lines_only_interface_fields(result)

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
    display_mode = {
        "off": "LINES_OFF",
        "witness": "LINES_WITNESS",
        "lines-first": "LINES_FIRST",
    }[mode]
    manifest = {
        "mode": display_mode,
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
            "linesFirstPriority": (
                "Lines leads semantic role, column ownership and flow order using existing page_structure fields; "
                "PDF remains physical geometry/typography authority; Markdown/DOCX remain content/donor sources."
                if mode == "lines-first"
                else None
            ),
            "futureSchemaEvolution": (
                "Still pending: after this priority experiment, audit Lines-only information that cannot be expressed "
                "in the current general schema and promote only proven general properties through an explicit schema revision."
            ),
        },
    }
    (output / "LINES_AB_MODE.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nFIDELITY MODE: {display_mode}")
    print(f"PDF    : {pdf}")
    print(f"SOURCE : {source}")
    print(f"LINES  : {lines if lines is not None else 'OFF'}")
    print(f"PAGES  : {args.pages}")
    if mode == "lines-first":
        print("PRIORITY: Lines structure -> PDF physical authority -> Markdown/DOCX content/donors")
    else:
        print("CONTRACT: downstream interface and modules stay unchanged")
    return int(canonical_pipeline_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
