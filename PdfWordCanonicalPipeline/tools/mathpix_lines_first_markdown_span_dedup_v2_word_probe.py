from __future__ import annotations

import argparse
from pathlib import Path

from pdf_word_reconstructor.common import write_json
from pdf_word_reconstructor.lines_first_markdown_span_dedup_v2_contract import build_lines_first_markdown_span_dedup_v2_contract
from pdf_word_reconstructor.source_neutral_builder_adapter import build_source_neutral_document


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lines-first + MMD span merge + refined residual dedup Word probe")
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

    artifacts = build_lines_first_markdown_span_dedup_v2_contract(lines, mmd, page_width_pt=args.page_width_pt)
    write_json(output / "LINES_FIRST_MMD_SPAN_DEDUP_V2_ARTIFACTS.json", artifacts)
    write_json(output / "LINES_FIRST_MMD_SPAN_DEDUP_V2_PAGE_STRUCTURE.json", artifacts["pageStructure"])
    write_json(output / "LINES_FIRST_MMD_SPAN_DEDUP_V2_PAGE_LAYOUT_SPINE.json", artifacts["pageLayoutSpine"])
    write_json(output / "LINES_FIRST_MMD_SPAN_DEDUP_V2_BUILD_CONTRACT.json", artifacts["buildContract"])

    docx_path = output / "lines_first_markdown_span_dedup_v2.docx"
    report = build_source_neutral_document(
        page_structure=artifacts["pageStructure"],
        page_layout_spine=artifacts["pageLayoutSpine"],
        output_path=docx_path,
        body_size_override=args.body_size_pt,
    )
    write_json(output / "LINES_FIRST_MMD_SPAN_DEDUP_V2_WORD_REPORT.json", report)

    summary = artifacts.get("summary") or {}
    adapter = report.get("sourceNeutralAdapter") or {}
    print("MODE                         : LINES_FIRST_MARKDOWN_SPAN_DEDUP_V2_WORD")
    print("PDF EVIDENCE                 : OFF")
    print("DOCX EVIDENCE                : OFF")
    print("DOCX DONOR                   : OFF")
    print("LINES GEOMETRY                : AUTHORITY")
    print("MARKDOWN AUGMENTATION         : CONTENT + SEMANTICS + ADJACENT MERGE")
    print("GENERAL DEDUP                 : OFF")
    print("RESIDUAL DEDUP                : CONSERVATIVE + CONTIGUOUS RUN")
    print("MIN CONTAINMENT               : 0.92")
    print("MIN CONTIGUOUS RUN            : 0.78")
    print("NARROW=>FLOAT FRAME           : OFF")
    print("RENDERER                      : EXISTING CANONICAL NATIVE BUILDER")
    print(f"PAGES                         : {len((artifacts.get('pageStructure') or {}).get('pages') or [])}")
    print(f"ORIGINAL LINES UNITS          : {int(summary.get('originalLinesUnitCount') or 0)}")
    print(f"PRE-DEDUP OUTPUT UNITS        : {int(summary.get('preDedupOutputUnitCount') or 0)}")
    print(f"RESIDUAL DUPLICATES SUPPRESSED: {int(summary.get('residualDuplicateSuppressedCount') or 0)}")
    print(f"OUTPUT UNITS                  : {int(summary.get('outputUnitCount') or 0)}")
    print(f"MMD MATCHED SPANS             : {int(summary.get('markdownMatchedSpanCount') or 0)}")
    print(f"LINES UNITS ABSORBED          : {int(summary.get('linesUnitsAbsorbedByMerges') or 0)}")
    print(f"BUILD READY                   : {int(summary.get('buildReadyCount') or 0)}")
    print(f"BUILD UNRESOLVED              : {int(summary.get('buildUnresolvedCount') or 0)}")
    print(f"OMITTED VISUALS               : {int(adapter.get('omittedVisualCount') or 0)}")
    print(f"DOCX                          : {docx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
