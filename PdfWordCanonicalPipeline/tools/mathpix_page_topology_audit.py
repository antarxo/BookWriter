from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pdf_word_reconstructor.common import write_json
from pdf_word_reconstructor.mathpix_lines_input import (
    build_mathpix_line_layout_map,
    find_mathpix_lines_json,
    load_mathpix_lines,
)
from pdf_word_reconstructor.mathpix_margin_model import build_mathpix_margin_model
from pdf_word_reconstructor.mathpix_page_geometry_adapter import build_mathpix_page_geometry_evidence
from pdf_word_reconstructor.mathpix_reserved_page_zones import build_reserved_page_zone_profile
from pdf_word_reconstructor.package_cli import _extract_package, _find_package_pdf
from pdf_word_reconstructor.page_furniture import analyze_page_furniture
from pdf_word_reconstructor.page_structure import build_page_structure
from pdf_word_reconstructor.pdf_analyzer import analyze_pdf
from pdf_word_reconstructor.region_classifier import classify_pdf_regions
from pdf_word_reconstructor.style_profile import build_style_profile


VERSION = "mathpix-page-topology-audit-0.1"


def _parse_physical_pages(spec: str | None, available: list[int]) -> list[int]:
    if not available:
        raise RuntimeError("Mathpix Lines contains no physical pages")
    if not spec:
        return list(available)
    values: set[int] = set()
    for token in str(spec).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start, end = int(left), int(right)
            if end < start:
                start, end = end, start
            values.update(range(start, end + 1))
        else:
            values.add(int(token))
    result = sorted(values)
    missing = [page for page in result if page not in set(available)]
    if missing:
        raise RuntimeError(
            "Requested physical pages are absent from Mathpix Lines: " + ", ".join(map(str, missing))
        )
    return result


def _contiguous(values: list[int]) -> bool:
    return bool(values) and values == list(range(values[0], values[-1] + 1))


def _page_aspect_from_lines(raw_page: dict[str, Any]) -> float | None:
    try:
        width = float(raw_page.get("page_width") or 0.0)
        height = float(raw_page.get("page_height") or 0.0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _resolve_page_mapping(
    pdf_path: Path,
    lines_data: dict[str, Any],
) -> dict[str, Any]:
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # type: ignore

    line_pages = sorted(
        {
            int(page.get("page") or 0)
            for page in lines_data.get("pages", []) or []
            if int(page.get("page") or 0) > 0
        }
    )
    if not line_pages:
        raise RuntimeError("Mathpix Lines contains no physical page numbers")

    with fitz.open(pdf_path) as pdf:
        pdf_count = int(pdf.page_count)
        pdf_aspects = [float(pdf[index].rect.width) / max(1.0, float(pdf[index].rect.height)) for index in range(pdf_count)]

    raw_by_physical = {
        int(page.get("page") or 0): page
        for page in lines_data.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }

    if max(line_pages) <= pdf_count:
        mapping = {page: page for page in line_pages}
        mode = "identity-physical-page"
    elif pdf_count == len(line_pages) and _contiguous(line_pages):
        mapping = {physical: ordinal for ordinal, physical in enumerate(line_pages, start=1)}
        mode = "subset-ordinal-to-contiguous-physical-pages"
    else:
        raise RuntimeError(
            "PDF/Mathpix page mapping is ambiguous: "
            f"pdfPageCount={pdf_count}, linesPhysicalPages={line_pages}. "
            "Audit is fail-closed; no page mapping was guessed."
        )

    checks = []
    for physical, ordinal in sorted(mapping.items()):
        lines_aspect = _page_aspect_from_lines(raw_by_physical.get(physical) or {})
        pdf_aspect = pdf_aspects[ordinal - 1] if 1 <= ordinal <= len(pdf_aspects) else None
        if lines_aspect is None or pdf_aspect is None:
            status = "unavailable"
            delta = None
        else:
            delta = abs(lines_aspect - pdf_aspect) / max(lines_aspect, pdf_aspect, 1e-9)
            status = "consistent" if delta <= 0.03 else "conflict"
        checks.append(
            {
                "physicalPage": physical,
                "pdfOrdinalPage": ordinal,
                "linesAspectRatio": round(lines_aspect, 6) if lines_aspect is not None else None,
                "pdfAspectRatio": round(pdf_aspect, 6) if pdf_aspect is not None else None,
                "relativeAspectDelta": round(delta, 6) if delta is not None else None,
                "status": status,
            }
        )

    conflicts = [row for row in checks if row["status"] == "conflict"]
    if conflicts:
        raise RuntimeError(
            "PDF/Mathpix page mapping failed page-size corroboration: "
            + json.dumps(conflicts, ensure_ascii=False)
        )

    return {
        "version": VERSION,
        "status": "resolved",
        "mode": mode,
        "pdfPageCount": pdf_count,
        "linesPhysicalPages": line_pages,
        "physicalToPdfOrdinal": {str(k): v for k, v in sorted(mapping.items())},
        "pdfOrdinalToPhysical": {str(v): k for k, v in sorted(mapping.items())},
        "aspectRatioChecks": checks,
        "policy": (
            "identity mapping is used only when physical Lines pages exist directly in the PDF; "
            "subset ordinal mapping is used only when PDF page count equals one contiguous Lines physical-page range; "
            "otherwise mapping is unresolved and the audit stops"
        ),
    }


def _remap_pdf_analysis(
    pdf_analysis: dict[str, Any],
    ordinal_to_physical: dict[int, int],
) -> dict[str, Any]:
    result = copy.deepcopy(pdf_analysis)
    for page in result.get("pages", []) or []:
        ordinal = int(page.get("page") or 0)
        physical = ordinal_to_physical.get(ordinal)
        if physical is None:
            raise RuntimeError(f"Analyzed PDF ordinal page {ordinal} has no resolved physical-page mapping")
        page["pdfOrdinalPage"] = ordinal
        page["page"] = physical
        page["pageMappingSource"] = "resolved-pdf-mathpix-page-map"
    return result


def _by_page(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {
        int(row.get("page") or 0): row
        for row in rows
        if int(row.get("page") or 0) > 0
    }


def _column_summary(column_evidence: dict[str, Any]) -> dict[str, Any]:
    classification = str(column_evidence.get("classification") or "unresolved")
    page_columns = list(column_evidence.get("pageColumns") or [])
    sidebars = list(column_evidence.get("sidebars") or [])
    local_columns = list(column_evidence.get("localColumns") or [])

    rail = None
    if classification == "main-plus-sidebar" and len(sidebars) == 2:
        ordered = sorted(sidebars, key=lambda row: float((row.get("bbox") or [0, 0, 0, 0])[0]))
        narrow = min(ordered, key=lambda row: float(row.get("widthRatio") or 1.0))
        wide = max(ordered, key=lambda row: float(row.get("widthRatio") or 0.0))
        nbox = list(narrow.get("bbox") or [])
        wbox = list(wide.get("bbox") or [])
        if len(nbox) == 4 and len(wbox) == 4:
            side = "left" if (nbox[0] + nbox[2]) / 2.0 < (wbox[0] + wbox[2]) / 2.0 else "right"
            rail = {
                "status": "candidate",
                "side": side,
                "bboxPt": nbox,
                "mainCandidateBBoxPt": wbox,
                "source": "existing-mathpix-page-geometry-adapter-main-plus-sidebar",
                "rendererMeaning": "deferred-until-pdf-topology-reconciliation",
            }

    return {
        "classification": classification,
        "confidence": column_evidence.get("confidence"),
        "pageColumnCount": len(page_columns),
        "sidebarContainerCount": len(sidebars),
        "localColumnCount": len(local_columns),
        "outerRailCandidate": rail,
        "rendererMeaning": "unresolved" if classification in {"ambiguous-page-containers", "multiple-layout-containers"} else "deferred",
    }


def _compact_page_structure(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "bodyBoxPt": page.get("body_box"),
        "marginsPt": page.get("margins"),
        "marginSource": page.get("margin_source"),
        "mainColumn": page.get("main_column"),
        "columnCount": len(page.get("columns", []) or []),
        "columns": page.get("columns"),
        "header": page.get("header"),
        "footer": page.get("footer"),
        "mathpixMarginEvidence": page.get("mathpixMarginEvidence"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed diagnostic audit of PDF-global page topology against Mathpix Lines. "
            "No Word renderer is invoked."
        )
    )
    parser.add_argument("--zip", required=True, type=Path, help="Full Mathpix package ZIP containing PDF and result.lines.json")
    parser.add_argument("--pages", default=None, help="Physical Mathpix page range, e.g. 17-22. Default: all Lines pages")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dpi", type=int, default=160)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    package_zip = args.zip.resolve()
    output = args.output.resolve()
    work_dir = output / "work"
    package_dir = work_dir / "package"
    asset_dir = output / "page_assets"
    output.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    asset_dir.mkdir(parents=True, exist_ok=True)

    if not package_zip.exists():
        raise FileNotFoundError(f"Mathpix package not found: {package_zip}")

    print(f"[1/6] Extract package: {package_zip.name}")
    _extract_package(package_zip, package_dir)
    lines_path = find_mathpix_lines_json(package_dir)
    if lines_path is None:
        raise FileNotFoundError("Mathpix package does not contain result.lines.json")
    pdf_path = _find_package_pdf(package_dir).resolve()
    lines_data = load_mathpix_lines(lines_path)

    print("[2/6] Resolve PDF ordinal pages <-> Mathpix physical pages [FAIL-CLOSED]")
    page_mapping = _resolve_page_mapping(pdf_path, lines_data)
    write_json(output / "PAGE_MAPPING_AUDIT.json", page_mapping)
    physical_pages = _parse_physical_pages(args.pages, page_mapping["linesPhysicalPages"])
    p2o = {int(k): int(v) for k, v in page_mapping["physicalToPdfOrdinal"].items()}
    o2p = {int(k): int(v) for k, v in page_mapping["pdfOrdinalToPhysical"].items()}
    pdf_ordinals = [p2o[page] for page in physical_pages]

    print(f"[3/6] Analyze PDF ordinals {pdf_ordinals} as physical pages {physical_pages}")
    pdf_analysis_raw = analyze_pdf(pdf_path, pdf_ordinals, work_dir, dpi=args.dpi)
    pdf_analysis = _remap_pdf_analysis(pdf_analysis_raw, o2p)
    style_profile = build_style_profile(pdf_analysis)
    classify_pdf_regions(pdf_analysis, body_size=style_profile.get("inferred_body_font_size_pt"))
    write_json(output / "PDF_ANALYSIS_PHYSICAL_PAGES.json", pdf_analysis)
    write_json(output / "PDF_STYLE_PROFILE.json", style_profile)

    print("[4/6] Reuse mature Lines geometry -> furniture -> reserved zones -> margins")
    line_map = build_mathpix_line_layout_map(lines_path, pdf_analysis)
    geometry = build_mathpix_page_geometry_evidence(line_map)
    reserved = build_reserved_page_zone_profile(line_map, geometry)
    margins = build_mathpix_margin_model(line_map, geometry, reserved)
    pdf_furniture = analyze_page_furniture(pdf_analysis, line_map)
    write_json(output / "MATHPIX_PAGE_GEOMETRY_EVIDENCE.json", geometry)
    write_json(output / "RESERVED_PAGE_ZONES.json", reserved)
    write_json(output / "MARGIN_MODEL.json", margins)
    write_json(output / "PDF_PAGE_FURNITURE.json", pdf_furniture)

    print("[5/6] Reuse mature page_structure in Lines-first mode; Word renderer OFF")
    page_structure = build_page_structure(
        pdf_analysis,
        work_dir,
        asset_dir,
        reference_docx=None,
        external_asset_paths=[package_dir],
        equation_donor_path=None,
        mathpix_lines_path=lines_path,
        mathpix_lines_mode="lines_first",
    )
    write_json(output / "PAGE_STRUCTURE_AUDIT.json", page_structure)

    geom_by_page = _by_page(list(geometry.get("pages", []) or []))
    margin_by_page = _by_page(list(margins.get("pages", []) or []))
    ps_by_page = _by_page(list(page_structure.get("pages", []) or []))
    pdf_by_page = _by_page(list(pdf_analysis.get("pages", []) or []))

    pages = []
    for physical in physical_pages:
        geom = geom_by_page.get(physical) or {}
        margin = margin_by_page.get(physical) or {}
        ps = ps_by_page.get(physical) or {}
        pdf_page = pdf_by_page.get(physical) or {}
        column_evidence = geom.get("columnEvidence") or {}
        pages.append(
            {
                "physicalPage": physical,
                "pdfOrdinalPage": p2o[physical],
                "pageSizePt": [pdf_page.get("width_pt"), pdf_page.get("height_pt")],
                "pageParityCandidate": "recto" if physical % 2 else "verso",
                "headerFooter": {
                    "headerStatus": ((geom.get("headerFooterClassification") or {}).get("headerStatus")),
                    "footerStatus": ((geom.get("headerFooterClassification") or {}).get("footerStatus")),
                    "headerBandPt": (geom.get("headerBand") or {}).get("bbox"),
                    "footerBandPt": (geom.get("footerBand") or {}).get("bbox"),
                    "reservedHeaderStatus": margin.get("headerReservedZoneStatus"),
                    "reservedFooterStatus": margin.get("footerReservedZoneStatus"),
                },
                "marginEvidence": {
                    "status": margin.get("status"),
                    "source": margin.get("source"),
                    "bodyBoxPt": margin.get("bodyBox"),
                    "marginsPt": margin.get("marginsPt"),
                    "observedMarginsPt": margin.get("observedMarginsPt"),
                },
                "mathpixColumnEvidence": _column_summary(column_evidence),
                "maturePageStructure": _compact_page_structure(ps),
                "wordRealization": None,
            }
        )

    audit = {
        "version": VERSION,
        "status": "diagnostic-only",
        "sourcePackage": str(package_zip),
        "sourcePdf": str(pdf_path),
        "sourceLines": str(lines_path),
        "physicalPages": physical_pages,
        "pageMapping": page_mapping,
        "documentMarginProfile": margins.get("documentMarginProfile"),
        "pageStructureSummary": page_structure.get("summary"),
        "pages": pages,
        "policy": {
            "pdfGlobalTopology": "must participate before Word layout classification",
            "mathpixLines": "geometry/hierarchy witness; column objects are not Word columns by themselves",
            "matureCodeReuse": "page_furniture + reserved_page_zones + margin_model + page_geometry_adapter + page_structure(lines_first)",
            "renderer": "OFF",
            "pageSpecificHardcoding": "FORBIDDEN",
            "ambiguousEvidence": "FAIL-CLOSED",
        },
    }
    write_json(output / "PAGE_TOPOLOGY_AUDIT.json", audit)

    print("[6/6] Audit complete; Word renderer was not invoked")
    print(json.dumps({
        "status": audit["status"],
        "version": VERSION,
        "physicalPages": physical_pages,
        "pageMappingMode": page_mapping["mode"],
        "output": str(output / "PAGE_TOPOLOGY_AUDIT.json"),
        "renderer": "OFF",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
