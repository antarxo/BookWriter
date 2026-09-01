from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SRC = REPO_ROOT / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from pdf_word_canonical_pipeline.markdown_element_map_v03 import extract_markdown_element_map  # noqa: E402
from pdf_word_reconstructor.canonical_evidence_fusion import build_canonical_evidence_document  # noqa: E402
from pdf_word_reconstructor.canonical_frame_profile import build_canonical_frame_profile  # noqa: E402
from pdf_word_reconstructor.canonical_page_evidence import build_canonical_page_evidence  # noqa: E402
from pdf_word_reconstructor.canonical_page_recovery import recover_blocked_pages  # noqa: E402
from pdf_word_reconstructor.canonical_page_topology import build_page_topology  # noqa: E402
from pdf_word_reconstructor.canonical_pdf_visual_witness import apply_pdf_visual_witness  # noqa: E402
from pdf_word_reconstructor.canonical_word_bridge import build_canonical_word_document  # noqa: E402
from pdf_word_reconstructor.mathpix_lines_input import build_mathpix_line_layout_map  # noqa: E402


def _resolve(root: Path, name: str) -> Path:
    root = root.resolve()
    direct = root / name
    if direct.is_file():
        return direct
    candidates = sorted(root.rglob(name))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(f"{name} not found below {root}")
    raise RuntimeError(f"Ambiguous {name}: {candidates}")


def _discover_pdf(root: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        path = explicit.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    current = root.resolve()
    for _ in range(5):
        candidates = sorted(p for p in current.glob("*.pdf") if p.is_file())
        if len(candidates) == 1:
            return candidates[0].resolve()
        if len(candidates) > 1:
            return None
        if current.parent == current:
            break
        current = current.parent
    return None


def _markdown_index_map(mmd: Path, work_dir: Path) -> dict[str, int]:
    mapped = extract_markdown_element_map([mmd], work_dir / "MARKDOWN_INDEX_MAP.json")
    positions: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(mapped.get("records", []) or []):
        record_id = str(record.get("id") or "").strip()
        if record_id:
            positions[record_id].append(index)
    return {record_id: values[0] for record_id, values in positions.items() if len(values) == 1}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one Word page directly from completed canonical evidence.")
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--pdf", type=Path, default=None)
    parser.add_argument("--page", type=int, default=19)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    package_root = args.package_root.resolve()
    mmd = _resolve(package_root, "result.mmd")
    lines = _resolve(package_root, "result.lines.json")
    pdf = _discover_pdf(package_root, args.pdf)
    if pdf is None:
        raise RuntimeError("Canonical Word proof requires one explicit/unambiguous source PDF. Pass --pdf <source.pdf>.")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = output / "work"
    work.mkdir(parents=True, exist_ok=True)

    canonical = build_canonical_evidence_document(
        mmd_path=mmd,
        lines_path=lines,
        pdf_path=None,
        target_page=args.page,
        work_dir=work,
    )
    line_map = build_mathpix_line_layout_map(lines, None)
    canonical = apply_pdf_visual_witness(canonical, line_map, pdf, args.page)

    page_report = build_canonical_page_evidence(line_map)
    frame_profile = build_canonical_frame_profile(page_report)
    page_report = recover_blocked_pages(page_report, line_map, frame_profile)
    page_evidence = next(
        (row for row in page_report.get("pages", []) or [] if int(row.get("page") or 0) == int(args.page)),
        None,
    )
    if page_evidence is None:
        raise RuntimeError(f"Canonical page evidence missing for page {args.page}")

    markdown_index = _markdown_index_map(mmd, work)
    canonical["pageEvidence"] = page_evidence
    canonical["canonicalOuterFrameProfile"] = frame_profile
    canonical["pageTopology"] = build_page_topology(
        list(canonical.get("blocks") or []),
        args.page,
        page_evidence,
        markdown_index_by_id=markdown_index,
    )

    canonical_path = output / f"CANONICAL_WORD_INPUT_PAGE_{args.page}.json"
    canonical_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2), encoding="utf-8")

    docx_path = output / f"CANONICAL_PAGE_{args.page}.docx"
    result = build_canonical_word_document(
        canonical,
        target_page=args.page,
        output_path=docx_path,
        package_root=package_root,
    )
    report_path = output / f"CANONICAL_WORD_REPORT_PAGE_{args.page}.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    validation = result.get("validation") or {}
    build = result.get("buildReport") or {}
    print("MODE CANONICAL_TO_WORD")
    print("OLD WORD BUILDERS MODIFIED: NO")
    print("REMATCHING: FORBIDDEN")
    print("LAYOUT REINFERENCE: FORBIDDEN")
    print("LEGACY FALLBACK: FORBIDDEN")
    print("PAGE", args.page)
    print("MMD", mmd)
    print("LINES", lines)
    print("PDF", pdf)
    print("CANONICAL VALIDATION", validation.get("status"))
    print("CANONICAL BLOCKS", validation.get("canonicalBlockCount"))
    print("CANONICAL ZONES", validation.get("zoneCount"))
    print("CROSS-ZONE ORDER", (validation.get("crossZoneReadingOrder") or {}).get("order"))
    print("RECOVERED FRAME PX", (validation.get("recoveredFrame") or {}).get("bboxPx"))
    print("WORD BUILDER", build.get("version"))
    print("WORD COLUMNS", build.get("columns"))
    print("WORD ITEMS", build.get("itemCount"))
    print("CANONICAL JSON", canonical_path)
    print("WORD REPORT", report_path)
    print("DOCX", docx_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
