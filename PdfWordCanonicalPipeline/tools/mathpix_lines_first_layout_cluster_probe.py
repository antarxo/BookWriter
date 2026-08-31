from __future__ import annotations

import argparse
from pathlib import Path

from pdf_word_reconstructor.common import write_json
from pdf_word_reconstructor.lines_first_layout_cluster_probe_contract import build_lines_first_layout_cluster_probe_contract


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lines-first layout cluster diagnostic")
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

    artifacts = build_lines_first_layout_cluster_probe_contract(lines, mmd, page_width_pt=args.page_width_pt)
    evidence = artifacts["layoutClusterEvidence"]
    write_json(output / "LINES_FIRST_LAYOUT_CLUSTER_EVIDENCE.json", evidence)
    summary = evidence["summary"]

    print("MODE                           : LINES_FIRST_LAYOUT_CLUSTER_PROBE")
    print("PDF EVIDENCE                   : OFF")
    print("DOCX EVIDENCE                  : OFF")
    print("DOCX DONOR                     : OFF")
    print("CONTENT MODEL                  : LINES-FIRST + MMD SPAN + STABLE DEDUP")
    print("PAIRWISE RELATIONS AUTHORITY   : OFF")
    print("LANE PERSISTENCE               : ON")
    print("PAGE-TOP COMPOSITION FILTER    : ON")
    print("EQUATION=>COLUMN               : OFF")
    print("NARROW=>FLOAT FRAME            : OFF")
    print("WORD REGION RENDERER           : DEFERRED")
    print(f"PAGES                          : {summary['pageCount']}")
    print(f"PAGE-TOP COMPOSITIONS          : {summary['pageTopCompositionCount']}")
    print(f"SIDEBAR/CALLOUT CANDIDATES     : {summary['sidebarCalloutCandidateCount']}")
    print(f"HIGH-CONFIDENCE SIDEBARS       : {summary['highConfidenceSidebarCount']}")
    print(f"TRUE MULTICOLUMN CANDIDATES    : {summary['trueMulticolumnCandidateCount']}")
    print(f"HIGH-CONFIDENCE MULTICOLUMN    : {summary['highConfidenceMulticolumnCount']}")
    print(f"PAGES WITH SIDEBARS            : {summary['pagesWithSidebarCandidates']}")
    print(f"PAGES WITH TRUE MULTICOLUMN    : {summary['pagesWithTrueMulticolumnCandidates']}")
    print("PER PAGE")
    for report in evidence["pageReports"]:
        s = report["summary"]
        print(
            f"  {report['page']}: items={report['itemCount']} top={s['pageTopCompositionCount']} "
            f"sidebar={s['sidebarCalloutCandidateCount']} highSidebar={s['highConfidenceSidebarCount']} "
            f"multicol={s['trueMulticolumnCandidateCount']} highMulticol={s['highConfidenceMulticolumnCount']}"
        )
    print(f"OUTPUT                         : {output / 'LINES_FIRST_LAYOUT_CLUSTER_EVIDENCE.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
