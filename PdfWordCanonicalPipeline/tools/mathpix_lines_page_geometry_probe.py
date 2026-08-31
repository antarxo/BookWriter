from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pdf_word_reconstructor.lines_page_geometry_map import build_page_geometry_map


def main() -> int:
    parser = argparse.ArgumentParser(description="Raw Mathpix Lines page geometry probe")
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--page", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    lines = args.lines.resolve()
    if not lines.exists():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = build_page_geometry_map(lines, args.page)
    report_path = output / f"LINES_PAGE_{args.page}_GEOMETRY_MAP.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = report.get("summary") or {}
    print("MODE LINES_PAGE_GEOMETRY_PROBE")
    print("PDF EVIDENCE OFF")
    print("MARKDOWN EVIDENCE OFF")
    print("DOCX EVIDENCE OFF")
    print("DOCX DONOR OFF")
    print("LAYOUT SEMANTIC INFERENCE OFF")
    print("RAIL SIDEBAR MULTICOLUMN LABELS OFF")
    print("WORD RENDERER OFF")
    print("PAGE", report.get("page"))
    print("SIZE_PX", report.get("page_width_px"), report.get("page_height_px"))
    print("OBJECTS WITH GEOMETRY", summary.get("objectCountWithGeometry"))
    print("RENDERABLES", summary.get("renderableCount"))
    print("COLUMN ENVELOPES", summary.get("envelopeCount"))
    print("TYPES", json.dumps(summary.get("types") or {}, ensure_ascii=False, sort_keys=True))

    for env in report.get("envelopes", []) or []:
        print(
            "ENVELOPE",
            env.get("id"),
            "BBOX",
            env.get("bbox_px"),
            "PARENT",
            env.get("parent_id"),
            "CHILDREN",
            len(env.get("children_ids") or []),
        )

    parent_counts = Counter(str(o.get("parent_id") or "<none>") for o in report.get("renderables", []) or [])
    print("TOP PARENTS", json.dumps(dict(parent_counts.most_common(12)), ensure_ascii=False))
    print("REPORT", report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
