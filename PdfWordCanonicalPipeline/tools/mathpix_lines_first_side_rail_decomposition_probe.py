from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pdf_word_reconstructor.lines_first_side_rail_decomposition_probe_contract import (  # noqa: E402
    build_lines_first_side_rail_decomposition_probe_contract,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Lines-first side rail decomposition diagnostic")
    parser.add_argument("--lines", required=True)
    parser.add_argument("--mmd", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    out_dir = Path(args.output).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    result = build_lines_first_side_rail_decomposition_probe_contract(Path(args.lines), Path(args.mmd))
    out_path = out_dir / "LINES_FIRST_SIDE_RAIL_DECOMPOSITION_EVIDENCE.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    s = result["summary"]
    print("MODE                           : LINES_FIRST_SIDE_RAIL_DECOMPOSITION_PROBE")
    print("PDF EVIDENCE                   : OFF")
    print("DOCX EVIDENCE                  : OFF")
    print("DOCX DONOR                     : OFF")
    print("LAYOUT AUTHORITY               : V3 MAIN-LANE + SIDE-RAIL")
    print("RAW LINES RAIL MEMBERS         : AUTHORITY")
    print("GROUPED SIDEBAR=>CONTAINER     : OFF")
    print("WORD RENDERER                  : DEFERRED")
    print(f"PAGES                          : {s['pageCount']}")
    print(f"SIDE RAILS                     : {s['railCount']}")
    print(f"RAW OBJECTS INSIDE RAILS       : {s['rawObjectsInsideRails']}")
    print(f"RAILS WITH MULTIPLE RAW OBJECTS: {s['railsWithMultipleRawObjects']}")
    print(f"PAGES WITH RAILS               : {s['pagesWithRails']}")
    print("PER PAGE")
    for page in result["pageReports"]:
        if not page["rails"]:
            print(f"  {page['page']}: rails=0")
            continue
        for i, rail in enumerate(page["rails"], start=1):
            print(
                f"  {page['page']} rail{i}: side={rail['side']} rawObjects={rail['rawObjectCount']} "
                f"types={rail['rawTypeCounts']}"
            )
    print(f"OUTPUT                         : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
