from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SRC = REPO_ROOT / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from pdf_word_reconstructor.canonical_page_evidence import build_canonical_page_evidence  # noqa: E402
from pdf_word_reconstructor.mathpix_lines_input import build_mathpix_line_layout_map  # noqa: E402


def _resolve_lines(package_root: Path | None, explicit: Path | None) -> Path:
    if explicit is not None:
        path = explicit.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    if package_root is None:
        raise ValueError("Pass --package-root or --lines")
    root = package_root.resolve()
    exact = root / "result.lines.json"
    if exact.is_file():
        return exact
    candidates = sorted(root.rglob("result.lines.json"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(f"result.lines.json not found under {root}")
    raise RuntimeError(f"Ambiguous result.lines.json under {root}: {len(candidates)} candidates")


def main() -> int:
    parser = argparse.ArgumentParser(description="Renderer-neutral page evidence from Mathpix Lines.")
    parser.add_argument("--package-root", type=Path, default=None)
    parser.add_argument("--lines", type=Path, default=None)
    parser.add_argument("--page", type=int, default=19)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    lines_path = _resolve_lines(args.package_root, args.lines)
    line_map = build_mathpix_line_layout_map(lines_path, None)
    report = build_canonical_page_evidence(line_map)
    selected = next((row for row in report.get("pages", []) if int(row.get("page") or 0) == args.page), None)
    if selected is None:
        raise RuntimeError(f"Page {args.page} not present in Lines payload")

    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"CANONICAL_PAGE_EVIDENCE_{args.page}.json"
    payload = {
        "version": report.get("version"),
        "status": report.get("status"),
        "policy": report.get("policy"),
        "sourceLines": str(lines_path),
        "documentFurnitureProfile": report.get("documentFurnitureProfile"),
        "documentSummary": report.get("summary"),
        "page": selected,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    furniture = selected.get("furnitureEvidence") or {}
    body = selected.get("bodyEvidence") or {}
    relation = selected.get("zoneRelationship") or {}
    print("MODE CANONICAL_PAGE_EVIDENCE")
    print("PAGE_STRUCTURE MUTATION OFF")
    print("WORD RENDERER OFF")
    print("WORD REALIZATION FORBIDDEN")
    print("PDF WITNESS OFF")
    print(f"PAGE {args.page}")
    print(f"LINES {lines_path}")
    print(f"HEADER {furniture.get('headerStatus')}")
    print(f"FOOTER {furniture.get('footerStatus')}")
    print(f"BODY {body.get('status')} ({body.get('reason') or body.get('confidence')})")
    print(f"ZONES {len(selected.get('zones') or [])}")
    print(f"ZONE RELATIONSHIP {relation.get('classification')} ({relation.get('confidence')})")
    print(f"RENDERER MEANING {relation.get('rendererMeaning')}")
    print(f"CROSS-ZONE ORDER {(selected.get('crossZoneReadingOrder') or {}).get('status')}")
    print(f"WORD DECISIONS {(report.get('summary') or {}).get('wordDecisionCount')}")
    print(f"REPORT {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
