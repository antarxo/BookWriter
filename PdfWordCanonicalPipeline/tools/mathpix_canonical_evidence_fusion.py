from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from pdf_word_canonical_pipeline.markdown_element_map_v03 import extract_markdown_element_map
from pdf_word_reconstructor.canonical_evidence_fusion import build_canonical_evidence_document
from pdf_word_reconstructor.canonical_frame_profile import build_canonical_frame_profile
from pdf_word_reconstructor.canonical_page_evidence import build_canonical_page_evidence
from pdf_word_reconstructor.canonical_page_recovery import recover_blocked_pages
from pdf_word_reconstructor.canonical_page_topology import build_page_topology
from pdf_word_reconstructor.canonical_pdf_visual_witness import apply_pdf_visual_witness
from pdf_word_reconstructor.mathpix_lines_input import build_mathpix_line_layout_map


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


def _markdown_index_witness(mmd: Path, work_dir: Path) -> dict[str, object]:
    mapped = extract_markdown_element_map([mmd], work_dir / "_topology_markdown_map.json")
    records = list(mapped.get("records", []) or [])
    positions: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        record_id = str(record.get("id") or "").strip()
        if record_id:
            positions[record_id].append(index)
    unique = {record_id: values[0] for record_id, values in positions.items() if len(values) == 1}
    duplicates = {record_id: values for record_id, values in positions.items() if len(values) > 1}
    return {
        "recordCount": len(records),
        "uniqueIndexById": unique,
        "duplicateIds": duplicates,
        "policy": "only unique Markdown record ids are admitted as cross-zone order witnesses",
    }


def main() -> int:
    args = parser().parse_args()
    root = args.package_root.resolve() if args.package_root else None
    mmd = _resolve(root, args.mmd, "result.mmd")
    lines = _resolve(root, args.lines, "result.lines.json")
    pdf = _discover_pdf(root, args.pdf)

    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / f"CANONICAL_EVIDENCE_PAGE_{args.page}.json"
    work_dir = args.output / "work"

    # The legacy fusion core still assumes ordinal PDF pages. Keep that path off;
    # PDF evidence is attached below only through the explicit fail-closed mapper.
    report = build_canonical_evidence_document(
        mmd_path=mmd,
        lines_path=lines,
        pdf_path=None,
        target_page=args.page,
        work_dir=work_dir,
    )

    line_map = build_mathpix_line_layout_map(lines, None)
    if pdf is not None:
        report = apply_pdf_visual_witness(report, line_map, pdf, args.page)

    page_evidence_report = build_canonical_page_evidence(line_map)
    outer_frame_profile = build_canonical_frame_profile(page_evidence_report)
    page_evidence_report = recover_blocked_pages(page_evidence_report, line_map, outer_frame_profile)
    selected_page_evidence = next(
        (row for row in page_evidence_report.get("pages", []) or [] if int(row.get("page") or 0) == args.page),
        None,
    )
    markdown_witness = _markdown_index_witness(mmd, work_dir)

    report["pageEvidenceVersions"] = {
        "canonicalPageEvidence": page_evidence_report.get("version"),
        "outerFrameProfile": outer_frame_profile.get("version"),
        "profileRecovery": (page_evidence_report.get("profileRecovery") or {}).get("version"),
    }
    report["pageEvidence"] = selected_page_evidence
    report["canonicalOuterFrameProfile"] = outer_frame_profile
    report["markdownOrderWitness"] = {
        "recordCount": markdown_witness.get("recordCount"),
        "uniqueIdCount": len(markdown_witness.get("uniqueIndexById") or {}),
        "duplicateIds": markdown_witness.get("duplicateIds"),
        "policy": markdown_witness.get("policy"),
    }
    report["pageTopology"] = build_page_topology(
        list(report.get("blocks") or []),
        args.page,
        selected_page_evidence,
        markdown_index_by_id=dict(markdown_witness.get("uniqueIndexById") or {}),
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = report.get("summary") or {}
    topology = report.get("pageTopology") or {}
    coverage = topology.get("zoneCoverageAudit") or {}
    recovered = topology.get("recoveredFrameEvidence") or {}
    cross_zone = topology.get("crossZoneReadingOrder") or {}
    pdf_witness = report.get("pdfVisualWitness") or {}
    pdf_mapping = pdf_witness.get("mapping") or {}

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
    if pdf:
        print(
            "PDF PAGE MAPPING",
            pdf_mapping.get("status"),
            "mode=", pdf_mapping.get("mode"),
            "pdfIndex=", pdf_mapping.get("pdfPageIndex"),
            "pdfPage=", pdf_mapping.get("pdfPageNumber"),
            "count=", pdf_mapping.get("pdfPageCount"),
        )
        print(
            "PDF WITNESS STATUS",
            pdf_witness.get("status"),
            "containers=", pdf_witness.get("containerCount"),
            "groups=", pdf_witness.get("groupCount"),
        )
        profile = pdf_witness.get("profile") or {}
        print(
            "PDF PAGE PROFILE",
            "textChars=", profile.get("textChars"),
            "drawings=", profile.get("drawingCount"),
            "images=", profile.get("imageCount"),
            "candidates=", profile.get("containerCandidateCount"),
        )
        for container in report.get("pdfContainers") or []:
            print(
                "PDF CONTAINER",
                container.get("id"),
                "bbox=", container.get("bboxPt"),
                "stroke=", container.get("stroke"),
                "fill=", container.get("fill"),
                "members=", container.get("memberBlockIds"),
            )
    print("CANONICAL BLOCKS", summary.get("canonicalBlockCount"))
    print("TOPOLOGY VERSION", topology.get("version"))
    print("TOPOLOGY ZONES", topology.get("zoneCount"))
    for zone in topology.get("zones") or []:
        md_order = zone.get("markdownOrderEvidence") or {}
        print(
            "TOPOLOGY ZONE", zone.get("zoneId"),
            "blocks=", zone.get("blockCount"),
            "semantics=", zone.get("semanticTypeCounts"),
            "physical=", zone.get("physicalZoneBBoxPx"),
            "canonical=", zone.get("canonicalCoverageBBoxPx"),
            "mmd=", md_order.get("status"),
            [md_order.get("indexMin"), md_order.get("indexMax")],
        )
    print("ZONE COVERAGE ALL PHYSICAL", coverage.get("allSemanticZonesPhysicallyWitnessed"))
    print("SEMANTIC ZONES MISSING PHYSICAL", coverage.get("semanticZonesMissingPhysicalWitness"))
    print("PHYSICAL ZONES WITHOUT BLOCKS", coverage.get("physicalZonesWithoutCanonicalBlocks"))
    print("RECOVERED FRAME", recovered.get("bboxPx"), "source=", recovered.get("source"))
    print("RECOVERED ZONE RELATIONSHIP", recovered.get("zoneRelationship"), f"({recovered.get('zoneRelationshipConfidence')})")
    print("MMD ORDER WITNESS", "records=", markdown_witness.get("recordCount"), "uniqueIds=", len(markdown_witness.get("uniqueIndexById") or {}), "duplicates=", len(markdown_witness.get("duplicateIds") or {}))
    print("CROSS-ZONE ORDER", cross_zone.get("status"), "order=", cross_zone.get("order"), "confidence=", cross_zone.get("confidence"))
    print("CROSS-ZONE ORDER REASON", cross_zone.get("reason"))
    print("CROSS-ZONE INTERVALS", cross_zone.get("zoneIntervals"))
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
