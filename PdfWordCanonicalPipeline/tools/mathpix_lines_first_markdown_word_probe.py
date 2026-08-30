from __future__ import annotations

import argparse
from pathlib import Path

from pdf_word_reconstructor.common import write_json
from pdf_word_reconstructor.lines_first_markdown_augmented_contract import build_lines_first_markdown_augmented_contract
from pdf_word_reconstructor.source_neutral_builder_adapter import build_source_neutral_document


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mathpix Lines-first + Markdown augmentation Word probe")
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--mmd", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--page-width-pt", type=float, default=595.276)
    parser.add_argument("--body-size-pt", type=float, default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    lines = args.lines.resolve()
    mmd = args.mmd.resolve()
    output = args.output.resolve()
    if not lines.is_file():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")
    if not mmd.is_file():
        raise FileNotFoundError(f"Mathpix MMD not found: {mmd}")
    output.mkdir(parents=True, exist_ok=True)

    artifacts = build_lines_first_markdown_augmented_contract(lines, mmd, page_width_pt=args.page_width_pt)
    write_json(output / "LINES_FIRST_MMD_ARTIFACTS.json", artifacts)
    write_json(output / "LINES_FIRST_MMD_PAGE_STRUCTURE.json", artifacts["pageStructure"])
    write_json(output / "LINES_FIRST_MMD_PAGE_LAYOUT_SPINE.json", artifacts["pageLayoutSpine"])
    write_json(output / "LINES_FIRST_MMD_BUILD_CONTRACT.json", artifacts["buildContract"])

    docx_path = output / "lines_first_markdown.docx"
    report = build_source_neutral_document(
        page_structure=artifacts["pageStructure"],
        page_layout_spine=artifacts["pageLayoutSpine"],
        output_path=docx_path,
        body_size_override=args.body_size_pt,
    )
    write_json(output / "LINES_FIRST_MMD_WORD_REPORT.json", report)

    summary = artifacts.get("summary") or {}
    adapter = report.get("sourceNeutralAdapter") or {}
    print("MODE                    : LINES_FIRST_MARKDOWN_WORD")
    print("PDF EVIDENCE            : OFF")
    print("DOCX EVIDENCE           : OFF")
    print("DOCX DONOR              : OFF")
    print("LINES GEOMETRY           : AUTHORITY")
    print("LINES GROUPING SKELETON  : ON")
    print("MARKDOWN AUGMENTATION    : CONTENT + SEMANTICS")
    print("MARKDOWN GEOMETRY        : OFF")
    print("NARROW=>FLOAT FRAME      : OFF")
    print("RENDERER                 : EXISTING CANONICAL NATIVE BUILDER")
    print(f"PAGES                    : {len((artifacts.get('pageStructure') or {}).get('pages') or [])}")
    print(f"LINES UNITS              : {int(summary.get('groupedUnitCount') or summary.get('groupedUnits') or 0)}")
    print(f"MARKDOWN ELEMENTS        : {int(summary.get('markdownElementCount') or 0)}")
    print(f"MMD MATCHED UNITS        : {int(summary.get('markdownMatchedUnitCount') or 0)}")
    print(f"MMD UNMATCHED UNITS      : {int(summary.get('markdownUnmatchedUnitCount') or 0)}")
    print(f"MMD MATCH COVERAGE       : {summary.get('markdownMatchCoverage')}")
    print(f"SEMANTIC CHANGES         : {int(summary.get('markdownSemanticChangeCount') or 0)}")
    print(f"TEXT CHANGES             : {int(summary.get('markdownTextChangeCount') or 0)}")
    print(f"BUILD READY              : {int(summary.get('buildReadyCount') or 0)}")
    print(f"BUILD UNRESOLVED         : {int(summary.get('buildUnresolvedCount') or 0)}")
    print(f"OMITTED VISUALS          : {int(adapter.get('omittedVisualCount') or 0)}")
    print(f"DOCX                     : {docx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
