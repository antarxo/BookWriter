from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pdf_word_reconstructor.lines_first_layout_cluster_probe_v2_contract import (  # noqa: E402
    build_lines_first_layout_cluster_probe_v2_contract,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Lines-first lane-cluster v2 diagnostic")
    parser.add_argument("--lines", type=Path, required=True)
    parser.add_argument("--mmd", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--page-width-pt", type=float, default=595.276)
    args = parser.parse_args()

    result = build_lines_first_layout_cluster_probe_v2_contract(args.lines, args.mmd, page_width_pt=args.page_width_pt)
    evidence = result["layoutClusterEvidence"]
    summary = evidence["summary"]

    args.output.mkdir(parents=True, exist_ok=True)
    out = args.output / "LINES_FIRST_LAYOUT_CLUSTER_V2_EVIDENCE.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    print("MODE                           : LINES_FIRST_LAYOUT_CLUSTER_V2_PROBE")
    print("PDF EVIDENCE                   : OFF")
    print("DOCX EVIDENCE                  : OFF")
    print("DOCX DONOR                     : OFF")
    print("CONTENT MODEL                  : LINES-FIRST + MMD SPAN + STABLE DEDUP")
    print("PAIRWISE RELATIONS AUTHORITY   : OFF")
    print("LANE PERSISTENCE               : ON")
    print("PAGE-TOP MUST BE SHALLOW       : ON")
    print("SIDEBAR MAY START INSIDE MAIN  : ON")
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
    for page in evidence["pageReports"]:
        s = page["summary"]
        print(
            f"  {page['page']}: items={page['itemCount']} top={s['pageTopCompositionCount']} "
            f"sidebar={s['sidebarCalloutCandidateCount']} highSidebar={s['highConfidenceSidebarCount']} "
            f"multicol={s['trueMulticolumnCandidateCount']} highMulticol={s['highConfidenceMulticolumnCount']}"
        )
    print(f"OUTPUT                         : {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
