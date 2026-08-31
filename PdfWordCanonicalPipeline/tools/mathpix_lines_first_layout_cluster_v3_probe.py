from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pdf_word_reconstructor.lines_first_layout_cluster_probe_v3_contract import (  # noqa: E402
    build_lines_first_layout_cluster_probe_v3_contract,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Lines-first layout cluster v3 diagnostic")
    parser.add_argument("--lines", required=True)
    parser.add_argument("--mmd", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    out_dir = Path(args.output).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    result = build_lines_first_layout_cluster_probe_v3_contract(Path(args.lines), Path(args.mmd))
    evidence = result["layoutClusterEvidence"]
    out_path = out_dir / "LINES_FIRST_LAYOUT_CLUSTER_V3_EVIDENCE.json"
    out_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = evidence["summary"]
    print("MODE                           : LINES_FIRST_LAYOUT_CLUSTER_V3_PROBE")
    print("PDF EVIDENCE                   : OFF")
    print("DOCX EVIDENCE                  : OFF")
    print("DOCX DONOR                     : OFF")
    print("CONTENT MODEL                  : LINES-FIRST + MMD SPAN + STABLE DEDUP")
    print("PAIRWISE RELATIONS AUTHORITY   : OFF")
    print("MAIN-LANE CLUSTER AUTHORITY    : ON")
    print("PAGE-TOP MUST BE SHALLOW       : ON")
    print("SIDEBAR MAY START INSIDE MAIN  : ON")
    print("EQUATION=>COLUMN               : OFF")
    print("NARROW=>FLOAT FRAME            : OFF")
    print("WORD REGION RENDERER           : DEFERRED")
    print(f"PAGES                          : {summary['pageCount']}")
    print(f"PAGE-TOP COMPOSITIONS          : {summary['pageTopCompositionCount']}")
    print(f"MAIN-LANE CLUSTERS             : {summary['mainLaneClusterCount']}")
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
            f"mainLanes={s['mainLaneClusterCount']} sidebar={s['sidebarCalloutCandidateCount']} "
            f"highSidebar={s['highConfidenceSidebarCount']} multicol={s['trueMulticolumnCandidateCount']} "
            f"highMulticol={s['highConfidenceMulticolumnCount']}"
        )
    print(f"OUTPUT                         : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
