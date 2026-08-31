from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pdf_word_reconstructor.common import write_json  # noqa: E402
from pdf_word_reconstructor.lines_first_sidebar_renderer_probe_contract import (  # noqa: E402
    build_lines_first_sidebar_renderer_probe_contract,
)
from pdf_word_reconstructor.source_neutral_builder_adapter import build_source_neutral_document  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lines-first native sidebar single-page renderer probe")
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--mmd", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--page-width-pt", type=float, default=595.276)
    parser.add_argument("--body-size-pt", type=float, default=None)
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

    probe = build_lines_first_sidebar_renderer_probe_contract(
        lines, mmd, page_width_pt=args.page_width_pt
    )
    write_json(output / "LINES_FIRST_SIDEBAR_RENDERER_PROBE.json", probe)

    reports = []
    for job in probe.get("jobs", []) or []:
        page_no = int(job["page"])
        page_dir = output / f"page-{page_no}"
        page_dir.mkdir(parents=True, exist_ok=True)
        write_json(page_dir / "PAGE_STRUCTURE.json", job["pageStructure"])
        write_json(page_dir / "PAGE_LAYOUT_SPINE.json", job["pageLayoutSpine"])
        write_json(page_dir / "SIDEBAR_PROMOTIONS.json", {
            "page": page_no,
            "promotedSidebars": job["promotedSidebars"],
        })
        docx_path = page_dir / f"sidebar_probe_page_{page_no}.docx"
        report = build_source_neutral_document(
            page_structure=job["pageStructure"],
            page_layout_spine=job["pageLayoutSpine"],
            output_path=docx_path,
            body_size_override=args.body_size_pt,
        )
        write_json(page_dir / "WORD_REPORT.json", report)
        reports.append({
            "page": page_no,
            "docx": str(docx_path),
            "sidebarCount": len(job["promotedSidebars"]),
            "omittedVisualCount": int((report.get("sourceNeutralAdapter") or {}).get("omittedVisualCount") or 0),
        })

    write_json(output / "LINES_FIRST_SIDEBAR_RENDERER_REPORTS.json", {"pages": reports})

    summary = probe["summary"]
    print("MODE                           : LINES_FIRST_SIDEBAR_RENDERER_PROBE")
    print("PDF EVIDENCE                   : OFF")
    print("DOCX EVIDENCE                  : OFF")
    print("DOCX DONOR                     : OFF")
    print("CONTENT MODEL                  : LINES-FIRST + MMD SPAN + STABLE DEDUP")
    print("LAYOUT MODEL                   : V3 PERSISTENT MAIN-LANE CLUSTERS")
    print("NARROW=>FLOAT FRAME            : OFF")
    print("TRUE MULTICOLUMN               : OFF (NO EVIDENCE)")
    print("SIDEBAR FRAME PATH             : EXISTING NATIVE WORD w:framePr")
    print("SIDEBAR WRAP                   : AROUND")
    print("SIDEBAR BORDER/FILL            : NOT ADDED")
    print("PAGINATION TEST                : ONE SOURCE PAGE PER DOCX")
    print(f"PAGES                          : {summary['pageCount']}")
    print(f"SIDEBARS PROMOTED              : {summary['sidebarPromotedCount']}")
    print(f"PAGES WITH SIDEBAR FRAMES      : {summary['pagesWithSidebarFrames']}")
    print(f"PAGES WITHOUT SIDEBAR FRAMES   : {summary['pagesWithoutSidebarFrames']}")
    print("OUTPUT DOCX")
    for row in reports:
        print(f"  {row['page']}: sidebar={row['sidebarCount']} omittedVisuals={row['omittedVisualCount']} -> {row['docx']}")
    print(f"OUTPUT ROOT                    : {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
