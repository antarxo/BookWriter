from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_word_reconstructor.lines_only_contract import build_lines_only_contract


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a source-pure Mathpix Lines -> canonical page_structure/page_layout/build contract probe. "
            "No PDF, Markdown or DOCX donor is read."
        )
    )
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--page-width-pt", type=float, default=595.276)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    lines = args.lines.resolve()
    output = args.output.resolve()
    if not lines.exists():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")

    output.mkdir(parents=True, exist_ok=True)
    result = build_lines_only_contract(lines, page_width_pt=float(args.page_width_pt))

    artifacts = {
        "LINES_ONLY_LAYOUT_MAP.json": result.get("lineLayoutMap"),
        "LINES_ONLY_PAGE_STRUCTURE.json": result.get("pageStructure"),
        "LINES_ONLY_PAGE_LAYOUT_SPINE.json": result.get("pageLayoutSpine"),
        "LINES_ONLY_BUILD_CONTRACT.json": result.get("buildContract"),
    }
    for name, payload in artifacts.items():
        (output / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "mode": "LINES_ONLY_L0",
        "source": str(lines),
        "usesPdf": False,
        "usesMarkdown": False,
        "usesDocx": False,
        "usesDocxDonor": False,
        "usesOldPageLayoutSpine": False,
        "rendererInvoked": False,
        "outputContract": "existing maps-first page_structure/page_layout/build-contract shape",
        "summary": result.get("summary") or {},
        "nextBoundary": (
            "Existing native builder can be connected only after its non-contract legacy parameters are made optional or supplied "
            "through a source-neutral adapter; this probe deliberately does not introduce a Lines-specific renderer."
        ),
    }
    (output / "LINES_ONLY_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = result.get("summary") or {}
    print("\nMODE: LINES_ONLY_L0")
    print("PDF     : OFF")
    print("MARKDOWN: OFF")
    print("DOCX    : OFF")
    print("DONOR   : OFF")
    print("OLD PAGE_LAYOUT_SPINE: OFF")
    print(f"LINES   : {lines}")
    print(f"PAGES   : {summary.get('pageCount')}")
    print(f"TEXT ROWS: {summary.get('textRowCount')}")
    print(f"BUILD READY: {summary.get('buildReadyCount')}")
    print(f"BUILD UNRESOLVED: {summary.get('buildUnresolvedCount')}")
    print(f"VISUALS WITHOUT ASSET BYTES: {summary.get('visualUnresolvedCount')}")
    print(f"OUTPUT  : {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
