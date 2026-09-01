from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_word_reconstructor.canonical_evidence_fusion import build_canonical_evidence_document


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Build renderer-neutral canonical evidence by fusing Mathpix Markdown semantics, "
            "Mathpix Lines geometry and optional PDF visual witness. No Word decisions are made."
        )
    )
    p.add_argument("--package-root", type=Path, help="Folder containing result.mmd and result.lines.json")
    p.add_argument("--mmd", type=Path, help="Explicit Mathpix result.mmd")
    p.add_argument("--lines", type=Path, help="Explicit Mathpix result.lines.json")
    p.add_argument("--pdf", type=Path, help="Optional source/package PDF visual witness")
    p.add_argument("--page", type=int, default=19, help="Physical source page to report; default 19")
    p.add_argument("--output", required=True, type=Path)
    return p


def _resolve(root: Path | None, explicit: Path | None, name: str) -> Path:
    if explicit is not None:
        path = explicit.resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        return path
    if root is None:
        raise RuntimeError(f"Provide --{name.split('.')[-1]} or --package-root")
    candidates = [root / name]
    candidates.extend(root.glob(f"**/{name}"))
    existing: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except Exception:
            continue
        if candidate.exists() and candidate not in seen:
            seen.add(candidate)
            existing.append(candidate)
    if len(existing) == 1:
        return existing[0]
    if not existing:
        raise FileNotFoundError(f"Could not find {name} below {root}")
    raise RuntimeError(f"Multiple {name} candidates; pass explicit path: {existing}")


def _discover_pdf(root: Path | None, explicit: Path | None) -> Path | None:
    if explicit is not None:
        path = explicit.resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        return path
    if root is None:
        return None
    # Search the package and then a few enclosing fixture/runtime folders. Select
    # automatically only when the evidence is unambiguous; never guess among PDFs.
    search_roots: list[Path] = []
    current = root.resolve()
    for _ in range(4):
        if current not in search_roots:
            search_roots.append(current)
        if current.parent == current:
            break
        current = current.parent
    candidates: list[Path] = []
    seen: set[Path] = set()
    for base in search_roots:
        for path in base.glob("*.pdf"):
            path = path.resolve()
            if path not in seen:
                seen.add(path)
                candidates.append(path)
        if candidates:
            break
    return candidates[0] if len(candidates) == 1 else None


def main() -> int:
    args = parser().parse_args()
    root = args.package_root.resolve() if args.package_root else None
    mmd = _resolve(root, args.mmd, "result.mmd")
    lines = _resolve(root, args.lines, "result.lines.json")
    pdf = _discover_pdf(root, args.pdf)

    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / f"CANONICAL_EVIDENCE_PAGE_{args.page}.json"
    report = build_canonical_evidence_document(
        mmd_path=mmd,
        lines_path=lines,
        pdf_path=pdf,
        target_page=args.page,
        work_dir=args.output / "work",
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = report.get("summary") or {}
    print("MODE CANONICAL_EVIDENCE_FUSION")
    print("WORD RENDERER OFF")
    print("WORD REALIZATION FORBIDDEN")
    print("SILENT FALLBACK OFF")
    print("MARKDOWN SEMANTIC PRIMARY")
    print("LINES GEOMETRY/PAGE PRIMARY")
    print("PDF VISUAL WITNESS", "ON" if pdf else "OFF (no unambiguous PDF auto-discovered)")
    print("PAGE", args.page)
    print("MMD", mmd)
    print("LINES", lines)
    print("PDF", pdf if pdf else "NONE")
    print("CANONICAL BLOCKS", summary.get("canonicalBlockCount"))
    print("GROUPS", summary.get("groupCount"))
    print("MATCHED MMD", summary.get("matchedMarkdownCount"), "/", summary.get("markdownRecordCount"))
    print("MATCHED LINES UNITS", summary.get("matchedLinesUnitCount"), "/", summary.get("linesUnitCount"))
    print("UNMATCHED MMD", summary.get("unmatchedMarkdownCount"))
    print("UNMATCHED LINES", summary.get("unmatchedLinesUnitCount"))
    print("CONFLICT BLOCKS", summary.get("conflictBlockCount"))
    print("WORD DECISIONS", summary.get("wordDecisionCount"))
    print("REPORT", report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
