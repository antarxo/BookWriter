from __future__ import annotations

import argparse
from pathlib import Path

from pdf_word_reconstructor.common import write_json
from pdf_word_reconstructor.lines_first_layout_role_probe_contract import build_lines_first_layout_role_probe_contract


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lines-first layout-role diagnostic probe")
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--mmd", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--page-width-pt", type=float, default=595.276)
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

    artifacts = build_lines_first_layout_role_probe_contract(lines, mmd, page_width_pt=args.page_width_pt)
    report = artifacts["layoutRoleEvidence"]
    write_json(output / "LINES_FIRST_LAYOUT_ROLE_EVIDENCE.json", report)

    summary = report.get("summary") or {}
    print("MODE                          : LINES_FIRST_LAYOUT_ROLE_PROBE")
    print("PDF EVIDENCE                  : OFF")
    print("DOCX EVIDENCE                 : OFF")
    print("DOCX DONOR                    : OFF")
    print("CONTENT MODEL                 : LINES-FIRST + MMD SPAN + STABLE DEDUP")
    print("LINES GEOMETRY                : AUTHORITY")
    print("NARROW=>FLOAT FRAME           : OFF")
    print("WORD REGION RENDERER          : DEFERRED")
    print(f"PAGES                         : {int(summary.get('pageCount') or 0)}")
    print(f"WIDE FLOW CANDIDATES          : {int(summary.get('wideFlowCandidateCount') or 0)}")
    print(f"NARROW CANDIDATES             : {int(summary.get('narrowCandidateCount') or 0)}")
    print(f"PARALLEL LANE RELATIONS       : {int(summary.get('parallelComparableLaneRelationCount') or 0)}")
    print(f"SIDEBAR RELATIONS             : {int(summary.get('sidebarRelationCount') or 0)}")
    print(f"RELATION GROUPS               : {int(summary.get('relationGroupCount') or 0)}")
    print(f"PAGES WITH PARALLEL LANES     : {summary.get('pagesWithParallelLaneEvidence')}")
    print(f"PAGES WITH SIDEBAR EVIDENCE   : {summary.get('pagesWithSidebarEvidence')}")
    print("PER PAGE")
    for page in report.get("pageReports", []) or []:
        ps = page.get("summary") or {}
        print(
            f"  {page.get('page')}: items={page.get('itemCount')} "
            f"wide={ps.get('wideFlowCandidateCount')} narrow={ps.get('narrowCandidateCount')} "
            f"parallel={ps.get('parallelComparableLaneRelationCount')} sidebar={ps.get('sidebarRelationCount')} "
            f"groups={ps.get('relationGroupCount')}"
        )
    print(f"OUTPUT                        : {output / 'LINES_FIRST_LAYOUT_ROLE_EVIDENCE.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
