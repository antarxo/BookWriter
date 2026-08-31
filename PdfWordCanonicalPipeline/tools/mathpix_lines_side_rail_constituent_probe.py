from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_word_reconstructor.lines_side_rail_constituents import build_side_rail_constituent_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze Mathpix Lines side rails and decompose them into renderer-neutral constituent candidates."
    )
    parser.add_argument("--lines", required=True, type=Path, help="Path to result.lines.json")
    parser.add_argument("--output", required=True, type=Path, help="Output directory")
    return parser


def main() -> int:
    args = _parser().parse_args()
    lines = args.lines.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not lines.is_file():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")

    output.mkdir(parents=True, exist_ok=True)
    report = build_side_rail_constituent_report(lines)
    report_path = output / "LINES_SIDE_RAIL_CONSTITUENTS.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = report.get("summary") or {}
    print("MODE LINES_SIDE_RAIL_CONSTITUENT_PROBE")
    print("PDF EVIDENCE OFF")
    print("MARKDOWN EVIDENCE OFF")
    print("DOCX EVIDENCE OFF")
    print("DOCX DONOR OFF")
    print("LINES RAW OBJECTS ON")
    print("RAIL GEOMETRY DIAGNOSTIC")
    print("RENDERER DECISION DEFERRED")
    print(f"PAGES WITH RAILS {summary.get('pageCountWithRails', 0)}")
    print(f"SIDE RAILS {summary.get('sideRailCount', 0)}")
    print(f"RAW OBJECTS IN RAILS {summary.get('rawObjectsInRails', 0)}")
    print(f"CONSTITUENTS {summary.get('constituentCount', 0)}")
    print("CLASSES " + json.dumps(summary.get("constituentClasses") or {}, ensure_ascii=False, sort_keys=True))
    for page in report.get("pages", []) or []:
        page_no = page.get("page")
        for rail in page.get("rails", []) or []:
            print(
                f"PAGE {page_no} RAIL {rail.get('side')} "
                f"RAW {rail.get('rawObjectCount')} RENDERABLE {rail.get('renderableObjectCount')} "
                f"CONSTITUENTS {rail.get('constituentCount')} TYPES "
                + json.dumps(rail.get("rawObjectTypes") or {}, ensure_ascii=False, sort_keys=True)
            )
    print(f"REPORT {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
