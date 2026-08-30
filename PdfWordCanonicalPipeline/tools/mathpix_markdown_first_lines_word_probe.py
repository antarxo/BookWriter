from __future__ import annotations

import argparse
from pathlib import Path

from pdf_word_reconstructor.common import write_json
from pdf_word_reconstructor.markdown_first_lines_geometry_contract import build_markdown_first_lines_geometry_contract
from pdf_word_reconstructor.source_neutral_builder_adapter import build_source_neutral_document


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mathpix Markdown-first + Lines geometry Word probe")
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

    artifacts = build_markdown_first_lines_geometry_contract(lines, mmd, page_width_pt=args.page_width_pt)
    write_json(output / "MARKDOWN_FIRST_LINES_ARTIFACTS.json", artifacts)
    write_json(output / "MARKDOWN_FIRST_LINES_PAGE_STRUCTURE.json", artifacts["pageStructure"])
    write_json(output / "MARKDOWN_FIRST_LINES_PAGE_LAYOUT_SPINE.json", artifacts["pageLayoutSpine"])
    write_json(output / "MARKDOWN_FIRST_LINES_BUILD_CONTRACT.json", artifacts["buildContract"])

    docx_path = output / "markdown_first_lines.docx"
    report = build_source_neutral_document(
        page_structure=artifacts["pageStructure"],
        page_layout_spine=artifacts["pageLayoutSpine"],
        output_path=docx_path,
        body_size_override=args.body_size_pt,
    )
    write_json(output / "MARKDOWN_FIRST_LINES_WORD_REPORT.json", report)

    summary = artifacts.get("summary") or {}
    adapter = report.get("sourceNeutralAdapter") or {}
    print("MODE                     : MARKDOWN_FIRST_LINES_WORD")
    print("PDF EVIDENCE             : OFF")
    print("DOCX EVIDENCE            : OFF")
    print("DOCX DONOR               : OFF")
    print("MARKDOWN SKELETON         : AUTHORITY")
    print("MARKDOWN CONTENT          : AUTHORITY")
    print("MARKDOWN SEMANTICS        : AUTHORITY")
    print("LINES GEOMETRY            : WITNESS ONLY")
    print("LINES TYPOGRAPHY          : WITNESS ONLY")
    print("UNMATCHED MMD GEOMETRY    : NOT INVENTED")
    print("NARROW=>FLOAT FRAME       : OFF")
    print("RENDERER                  : EXISTING CANONICAL NATIVE BUILDER")
    print(f"PAGES                     : {len((artifacts.get('pageStructure') or {}).get('pages') or [])}")
    print(f"MARKDOWN ELEMENTS         : {int(summary.get('markdownElementCount') or 0)}")
    print(f"MMD MATCHED BLOCKS        : {int(summary.get('markdownMatchedBlockCount') or 0)}")
    print(f"MMD UNMATCHED BLOCKS      : {int(summary.get('markdownUnmatchedBlockCount') or 0)}")
    print(f"MMD MATCH COVERAGE        : {summary.get('markdownMatchCoverage')}")
    print(f"LINES WITNESS UNITS       : {int(summary.get('linesWitnessUnitCount') or 0)}")
    print(f"AVG LINES WITNESS SPAN    : {summary.get('averageLinesWitnessSpanLength')}")
    print(f"OUTPUT UNITS              : {int(summary.get('outputUnitCount') or 0)}")
    print(f"BUILD READY               : {int(summary.get('buildReadyCount') or 0)}")
    print(f"BUILD UNRESOLVED          : {int(summary.get('buildUnresolvedCount') or 0)}")
    print(f"OMITTED VISUALS           : {int(adapter.get('omittedVisualCount') or 0)}")
    print(f"DOCX                      : {docx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
