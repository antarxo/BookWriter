from __future__ import annotations

import argparse
from pathlib import Path

from pdf_word_reconstructor.common import write_json
from pdf_word_reconstructor.lines_first_markdown_span_contract import build_lines_first_markdown_span_contract
from pdf_word_reconstructor.source_neutral_builder_adapter import build_source_neutral_document


def main() -> int:
    parser = argparse.ArgumentParser(description="Mathpix Lines-first + Markdown span-merge Word probe")
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--mmd", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--page-width-pt", type=float, default=595.276)
    parser.add_argument("--body-size-pt", type=float, default=None)
    args = parser.parse_args()

    lines = args.lines.resolve()
    mmd = args.mmd.resolve()
    output = args.output.resolve()
    if not lines.is_file():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")
    if not mmd.is_file():
        raise FileNotFoundError(f"Mathpix MMD not found: {mmd}")
    output.mkdir(parents=True, exist_ok=True)

    artifacts = build_lines_first_markdown_span_contract(lines, mmd, page_width_pt=args.page_width_pt)
    write_json(output / "LINES_FIRST_MMD_SPAN_ARTIFACTS.json", artifacts)
    write_json(output / "LINES_FIRST_MMD_SPAN_PAGE_STRUCTURE.json", artifacts["pageStructure"])
    write_json(output / "LINES_FIRST_MMD_SPAN_PAGE_LAYOUT_SPINE.json", artifacts["pageLayoutSpine"])
    write_json(output / "LINES_FIRST_MMD_SPAN_BUILD_CONTRACT.json", artifacts["buildContract"])

    docx_path = output / "lines_first_markdown_span.docx"
    report = build_source_neutral_document(
        page_structure=artifacts["pageStructure"],
        page_layout_spine=artifacts["pageLayoutSpine"],
        output_path=docx_path,
        body_size_override=args.body_size_pt,
    )
    write_json(output / "LINES_FIRST_MMD_SPAN_WORD_REPORT.json", report)

    summary = artifacts.get("summary") or {}
    adapter = report.get("sourceNeutralAdapter") or {}
    print("MODE                    : LINES_FIRST_MARKDOWN_SPAN_WORD")
    print("PDF EVIDENCE            : OFF")
    print("DOCX EVIDENCE           : OFF")
    print("DOCX DONOR              : OFF")
    print("LINES GEOMETRY           : AUTHORITY")
    print("LINES PAGE MEMBERSHIP    : AUTHORITY")
    print("MARKDOWN AUGMENTATION    : CONTENT + SEMANTICS + ADJACENT MERGE")
    print("MARKDOWN GEOMETRY        : OFF")
    print("MARKDOWN SPLIT LINES     : OFF")
    print("NARROW=>FLOAT FRAME      : OFF")
    print("RENDERER                 : EXISTING CANONICAL NATIVE BUILDER")
    print(f"PAGES                    : {len((artifacts.get('pageStructure') or {}).get('pages') or [])}")
    print(f"ORIGINAL LINES UNITS     : {int(summary.get('originalLinesUnitCount') or 0)}")
    print(f"OUTPUT UNITS             : {int(summary.get('outputUnitCount') or 0)}")
    print(f"MARKDOWN ELEMENTS        : {int(summary.get('markdownElementCount') or 0)}")
    print(f"MMD MATCHED SPANS        : {int(summary.get('markdownMatchedSpanCount') or 0)}")
    print(f"LINES UNITS ABSORBED     : {int(summary.get('linesUnitsAbsorbedByMerges') or 0)}")
    print(f"AVG MATCHED SPAN LENGTH  : {summary.get('averageMatchedSpanLength')}")
    print(f"SEMANTIC CHANGES         : {int(summary.get('markdownSemanticChangeCount') or 0)}")
    print(f"TEXT CHANGES             : {int(summary.get('markdownTextChangeCount') or 0)}")
    print(f"BUILD READY              : {int(summary.get('buildReadyCount') or 0)}")
    print(f"BUILD UNRESOLVED         : {int(summary.get('buildUnresolvedCount') or 0)}")
    print(f"OMITTED VISUALS          : {int(adapter.get('omittedVisualCount') or 0)}")
    print(f"DOCX                     : {docx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
