from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_word_reconstructor.lines_only_hierarchy_contract import build_lines_only_hierarchy_contract
from pdf_word_reconstructor.source_neutral_builder_adapter import build_source_neutral_document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build LINES_ONLY_L1 native Word using Lines hierarchy/column/font evidence only. "
            "No PDF, Markdown or DOCX evidence is read."
        )
    )
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--page-width-pt", type=float, default=595.276)
    parser.add_argument("--body-size-pt", type=float, default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    lines = args.lines.resolve()
    output = args.output.resolve()
    if not lines.exists():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")

    output.mkdir(parents=True, exist_ok=True)
    artifacts = build_lines_only_hierarchy_contract(lines, page_width_pt=float(args.page_width_pt))
    page_structure = artifacts["pageStructure"]
    page_layout_spine = artifacts["pageLayoutSpine"]
    build_contract = artifacts["buildContract"]

    for name, payload in (
        ("LINES_ONLY_L1_PAGE_STRUCTURE.json", page_structure),
        ("LINES_ONLY_L1_PAGE_LAYOUT_SPINE.json", page_layout_spine),
        ("LINES_ONLY_L1_BUILD_CONTRACT.json", build_contract),
    ):
        (output / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    docx = output / "lines_only_L1.docx"
    report = build_source_neutral_document(
        page_structure=page_structure,
        page_layout_spine=page_layout_spine,
        output_path=docx,
        body_size_override=args.body_size_pt,
    )
    (output / "LINES_ONLY_L1_WORD_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = artifacts.get("summary") or {}
    adapter = report.get("sourceNeutralAdapter") or {}
    print("\nMODE: LINES_ONLY_L1_WORD")
    print("PDF EVIDENCE     : OFF")
    print("MARKDOWN EVIDENCE: OFF")
    print("DOCX EVIDENCE    : OFF")
    print("DOCX DONOR       : OFF")
    print("OLD LAYOUT SPINE : OFF")
    print("LINES HIERARCHY   : ON")
    print("LINES COLUMNS     : ON")
    print("LINES FONT_SIZE   : ON")
    print("RENDERER          : EXISTING CANONICAL NATIVE BUILDER")
    print(f"PAGES             : {summary.get('pageCount')}")
    print(f"TEXT ROWS         : {summary.get('textRowCount')}")
    print(f"HIERARCHY TEXT    : {summary.get('hierarchyBearingTextCount')}")
    print(f"COLUMN OWNED TEXT : {summary.get('columnOwnedTextCount')}")
    print(f"POSITIONED COLUMN : {summary.get('positionedColumnTextCount')}")
    print(f"BUILD READY       : {summary.get('buildReadyCount')}")
    print(f"BUILD UNRESOLVED  : {summary.get('buildUnresolvedCount')}")
    print(f"OMITTED VISUALS   : {adapter.get('omittedVisualCount')}")
    print(f"DOCX              : {docx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
