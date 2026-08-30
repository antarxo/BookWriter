from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pdf_word_reconstructor.lines_page_evidence_model import build_page_evidence_model


def main() -> int:
    p = argparse.ArgumentParser(description="Build Mathpix page evidence model from Lines plus optional full-package MMD/manifest.")
    p.add_argument("--lines", required=True, type=Path)
    p.add_argument("--mmd", type=Path)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()

    lines = args.lines.resolve()
    if not lines.exists():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")
    mmd = args.mmd.resolve() if args.mmd else None
    manifest = args.manifest.resolve() if args.manifest else None
    if mmd is not None and not mmd.exists():
        raise FileNotFoundError(f"Mathpix MMD not found: {mmd}")
    if manifest is not None and not manifest.exists():
        raise FileNotFoundError(f"Mathpix manifest not found: {manifest}")

    out_dir = args.output.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "LINES_PAGE_EVIDENCE_MODEL.json"
    report = build_page_evidence_model(lines, mmd, manifest)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    s = report["summary"]
    print(f"MODE: MATHPIX_PAGE_EVIDENCE_MODEL_{str(report.get('version') or 'UNKNOWN').upper().replace('-', '_')}")
    print("PDF INPUT             : OFF")
    print("WORD RENDERING        : OFF")
    print("LINES                  : ON")
    print(f"MMD PACKAGE EVIDENCE   : {'ON' if mmd else 'OFF'}")
    print(f"MANIFEST               : {'ON' if manifest else 'OFF'}")
    print("ACTIVE CONTENT ENVELOPE: DIAGNOSTIC")
    print("WORD MARGINS           : NOT INFERRED")
    print("HEADER/FOOTER          : CANDIDATE")
    print("VISUAL ASSET INSERTION : DEFERRED")
    print(f"PAGES                  : {s['pageCount']}")
    print(f"LINES VISUALS          : {s['linesVisualCount']}")
    print(f"PACKAGE VISUALS        : {s['packageVisualCount']}")
    print(f"VISUAL MATCHES         : {s['matchedVisualCount']}")
    print(f"PACKAGE NOT IN LINES   : {s['packageVisualMissingFromLinesCount']}")
    print(f"UNASSIGNED PACKAGE     : {s.get('unassignedPackageVisualCount')}")
    mapping = report.get("pageNumberMapping") or {}
    print(f"REQUESTED PAGES        : {mapping.get('requestedPages')}")
    print(f"LOCAL PACKAGE PAGES    : {mapping.get('localPackagePages')}")
    print(f"LOCAL->SOURCE MAP      : {mapping.get('localToSourcePage')}")
    print(f"PAGE MAP RESOLVED      : {mapping.get('resolved')}")
    print(f"REPEATED EDGE SIGS     : {s['repeatedEdgeSignatureCount']}")
    template = report.get("crossPageTemplateCandidate") or {}
    print(f"TEMPLATE BBOX          : {template.get('candidateBBoxPx')}")
    print(f"TEMPLATE CONFIDENCE    : {template.get('confidence')}")
    for page in report.get("pages") or []:
        audit = page.get("visualCompletenessAudit") or []
        missing = sum(1 for x in audit if x.get("status") == "package-visual-unmatched")
        matched = sum(1 for x in audit if x.get("status") == "matched-lines-visual")
        print(
            f"PAGE {page.get('page')}: linesVisuals={len(page.get('linesVisualEntities') or [])} "
            f"packageVisuals={len(page.get('packageVisualEntities') or [])} matched={matched} packageMissing={missing}"
        )
    if s.get("packageVisualCount") and not mapping.get("localPackagePages"):
        targets = []
        for page in report.get("pages") or []:
            for v in page.get("packageVisualEntities") or []:
                if v.get("target"):
                    targets.append(str(v.get("target")))
        # If page association failed, packageVisualEntities may be empty on every source page.
        # The JSON report still preserves mapping diagnostics; the explicit note prevents a
        # zero-per-page result from being mistaken for absence of package visuals.
        print("PACKAGE PAGE TOKENS    : NONE EXTRACTED")
        print("NOTE                   : Inspect package targets/page-token parser before interpreting visual completeness.")
    print(f"OUTPUT                 : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
