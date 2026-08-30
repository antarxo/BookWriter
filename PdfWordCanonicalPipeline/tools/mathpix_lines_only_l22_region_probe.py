from __future__ import annotations

import argparse
from pathlib import Path

from pdf_word_reconstructor.common import write_json
from pdf_word_reconstructor.lines_only_region_sweep_contract import build_lines_only_region_sweep_contract


def main() -> int:
    parser = argparse.ArgumentParser(description="Mathpix Lines-only L2.2 region topology probe")
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--page-width-pt", type=float, default=595.276)
    args = parser.parse_args()

    lines = args.lines.resolve()
    if not lines.is_file():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifacts = build_lines_only_region_sweep_contract(lines, page_width_pt=args.page_width_pt)

    page_structure = artifacts["pageStructure"]
    layout_spine = artifacts["pageLayoutSpine"]
    build_contract = artifacts["buildContract"]
    summary = artifacts.get("summary") or {}

    write_json(output / "LINES_ONLY_L22_PAGE_STRUCTURE.json", page_structure)
    write_json(output / "LINES_ONLY_L22_PAGE_LAYOUT_SPINE.json", layout_spine)
    write_json(output / "LINES_ONLY_L22_BUILD_CONTRACT.json", build_contract)
    write_json(output / "LINES_ONLY_L22_REGION_TOPOLOGY.json", {
        "version": artifacts.get("version"),
        "source": str(lines),
        "regionsByPage": layout_spine.get("regionTopologyByPage") or {},
        "summary": {
            "pageCount": len(page_structure.get("pages") or []),
            "regionCount": summary.get("regionCount"),
            "multiColumnCandidateCount": summary.get("multiColumnCandidateCount"),
            "fullWidthRegionCount": summary.get("fullWidthRegionCount"),
            "narrowSingleRegionCount": summary.get("narrowSingleRegionCount"),
            "spanningFlowItemCount": summary.get("spanningFlowItemCount"),
        },
        "policy": "Diagnostic topology only; no Word section/column/frame rendering is performed.",
    })

    print("MODE                 : LINES_ONLY_L2.2_REGION_TOPOLOGY")
    print("PDF EVIDENCE         : OFF")
    print("MARKDOWN EVIDENCE    : OFF")
    print("DOCX EVIDENCE        : OFF")
    print("DOCX DONOR           : OFF")
    print("LINES GROUPING        : L2 PRESERVED")
    print("L2.1 NORMAL FLOW      : PRESERVED")
    print("NARROW=>FLOAT FRAME   : OFF")
    print("REGION INFERENCE      : COLUMN Y-SWEEP")
    print("TRANSITIVE GROUPING   : OFF")
    print("REGION RENDERER       : DEFERRED")
    print(f"PAGES                 : {len(page_structure.get('pages') or [])}")
    print(f"REGIONS               : {summary.get('regionCount', 0)}")
    print(f"MULTICOL CANDIDATES   : {summary.get('multiColumnCandidateCount', 0)}")
    print(f"FULL-WIDTH CANDIDATES : {summary.get('fullWidthRegionCount', 0)}")
    print(f"NARROW SINGLE         : {summary.get('narrowSingleRegionCount', 0)}")
    print(f"SPANNING FLOW ITEMS   : {summary.get('spanningFlowItemCount', 0)}")
    print(f"OUTPUT                : {output / 'LINES_ONLY_L22_REGION_TOPOLOGY.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
