from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_word_reconstructor.lines_page_frame_visual import build_page_frame_visual


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Lines-only page-frame and visual-occupancy diagnostics.")
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    source = args.lines.resolve()
    if not source.exists():
        raise FileNotFoundError(f"Mathpix Lines not found: {source}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    report = build_page_frame_visual(source)
    path = output / "LINES_PAGE_FRAME_VISUAL.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("MODE: MATHPIX_LINES_PAGE_FRAME_VISUAL_V01")
    print("PDF INPUT          : OFF")
    print("WORD RENDERING     : OFF")
    print("LINES ONLY         : ON")
    print("PHYSICAL PAGE      : AUTHORITATIVE")
    print("BODY FRAME         : CANDIDATE")
    print("HEADER/FOOTER      : CANDIDATE")
    print("VISUAL ASSET BYTES : DEFERRED")
    print("VISUAL BBOX         : INCLUDED")
    print(f"PAGES              : {report['summary']['pageCount']}")
    print(f"VISUALS            : {report['summary']['visualEntityCount']}")
    print(f"DECOR CANDIDATES   : {report['summary']['decorationCandidateCount']}")
    print(f"REPEATED EDGE SIGS : {report['summary']['repeatedEdgeSignatureCount']}")
    for page in report.get("pages") or []:
        frame = page.get("bodyFrameCandidate") or {}
        print(
            f"PAGE {page.get('page')}: visuals={len(page.get('visualEntities') or [])} "
            f"decor={len(page.get('pageDecorationCandidates') or [])} "
            f"margins={frame.get('marginsPx')}"
        )
    print(f"OUTPUT             : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
