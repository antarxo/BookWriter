from __future__ import annotations

import argparse
from pathlib import Path

from pdf_word_reconstructor.common import write_json
from pdf_word_reconstructor.lines_only_region_refined_contract import build_lines_only_region_refined_contract


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mathpix Lines-only L2.4 refined region topology probe")
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--page-width-pt", type=float, default=595.276)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    lines = args.lines.resolve()
    output = args.output.resolve()
    if not lines.is_file():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")
    output.mkdir(parents=True, exist_ok=True)

    artifacts = build_lines_only_region_refined_contract(lines, page_width_pt=args.page_width_pt)
    out_path = output / "LINES_ONLY_L24_REGION_TOPOLOGY.json"
    write_json(out_path, artifacts)

    summary = artifacts.get("summary") or {}
    print("MODE                  : LINES_ONLY_L2.4_REFINED_REGION_TOPOLOGY")
    print("PDF EVIDENCE          : OFF")
    print("MARKDOWN EVIDENCE     : OFF")
    print("DOCX EVIDENCE         : OFF")
    print("DOCX DONOR            : OFF")
    print("LINES GROUPING         : L2 PRESERVED")
    print("L2.1 NORMAL FLOW       : PRESERVED")
    print("NARROW=>FLOAT FRAME    : OFF")
    print("REGION SKELETON        : COLUMN Y-SWEEP")
    print("ORDINARY FLOW BOUNDARY : OFF")
    print("FULL-WIDTH SPLITS      : ON")
    print("REGION RENDERER        : DEFERRED")
    print(f"PAGES                  : {len((artifacts.get('pageStructure') or {}).get('pages') or [])}")
    print(f"REGIONS                : {int(summary.get('regionCount') or 0)}")
    print(f"MULTICOL CANDIDATES    : {int(summary.get('multiColumnCandidateCount') or 0)}")
    print(f"FULL-WIDTH REGIONS     : {int(summary.get('fullWidthRegionCount') or 0)}")
    print(f"SINGLE-COLUMN REGIONS  : {int(summary.get('singleColumnRegionCount') or 0)}")
    print(f"FLOW-ONLY REGIONS      : {int(summary.get('flowOnlyRegionCount') or 0)}")
    print(f"SPANNING FLOW ITEMS    : {int(summary.get('spanningFlowItemCount') or 0)}")
    print(f"FULL-WIDTH FLOW ITEMS  : {int(summary.get('fullWidthFlowItemCount') or 0)}")
    print(f"ASSIGNED FLOW ITEMS    : {int(summary.get('assignedFlowItemCount') or 0)}")
    print(f"UNASSIGNED FLOW ITEMS  : {int(summary.get('unassignedFlowItemCount') or 0)}")
    print(f"REGIONS BY PAGE        : {summary.get('regionsByPage') or {}}")
    print(f"OUTPUT                 : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
