from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from copy import deepcopy
from pathlib import Path

from .aligner import align_pdf_to_docx
from .architecture_benchmark import build_architecture_benchmark
from .architecture_guard import build_architecture_guard
from .common import parse_page_range, write_json
from .conversion_spine import build_conversion_spine
from .content_audit import audit_content
from .docx_donor_map import build_docx_donor_map
from .docx_analyzer import analyze_docx
from .fidelity_fallback_report import build_fidelity_fallback_report
from .mapping_fidelity import build_mapping_fidelity
from .markdown_pdf_spine import build_markdown_pdf_spine
from .native_builder import build_native_page_document
from .page_structure import build_page_structure
from .page_layout_spine import build_page_layout_spine
from .pdf_analyzer import analyze_pdf
from .region_classifier import classify_pdf_regions
from .render_compare import (
    compare_pdf_to_source,
    count_docx_pages_with_word,
    export_docx_to_pdf,
    probe_typography_settings_with_word,
)
from .report import build_html_report
from .style_profile import build_style_profile


class CalibrationContractError(RuntimeError):
    """Raised when calibration violates the agreed search contract."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PDF-guided DOCX reconstruction stage for the canonical pipeline v0.9.0")
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--pages", default="17-20", help="1-based range, e.g. 17-20")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dpi", type=int, default=160)
    parser.add_argument("--external-assets", type=Path, action="append", default=[], help="Optional Mathpix asset directory; may be repeated")
    parser.add_argument("--equation-donors", type=Path, help="JSON inventory of Markdown LaTeX equation donors")
    parser.add_argument("--markdown-element-map", type=Path, help="JSON inventory of Mathpix Markdown elements for survival audit")
    parser.add_argument("--progress-report", type=Path, help="Live JSON status written during long calibration runs")
    parser.add_argument("--no-render", action="store_true", help="Skip Word/LibreOffice PDF render and font calibration")
    parser.add_argument(
        "--strict-page-count", action="store_true",
        help="Fail without publishing final DOCX/PDF unless an exact page-count candidate exists",
    )
    parser.add_argument(
        "--calibration", choices=("none", "fast", "full"), default="none",
        help="none: one deterministic candidate, fast/full: explicit diagnostic sweeps",
    )
    return parser


def _candidate_sizes(body_size: float) -> list[float]:
    values = [round(body_size * factor, 2) for factor in (0.94, 0.97, 1.00)]
    return list(dict.fromkeys(max(5.0, value) for value in values))


def _natural_word_size(value: float) -> float:
    return round(float(value) * 2.0) / 2.0


def _natural_word_candidate_floor(body_size: float, style_profile: dict | None = None) -> float:
    profile_sizes = []
    if isinstance(style_profile, dict):
        font_sizes = style_profile.get("font_sizes") or []
        total_weight = sum(float(row.get("weighted_chars") or 0.0) for row in font_sizes if isinstance(row, dict))
        threshold = max(120.0, total_weight * 0.005)
        for row in font_sizes:
            if not isinstance(row, dict):
                continue
            size = row.get("size_pt")
            weight = float(row.get("weighted_chars") or 0.0)
            if (
                isinstance(size, (int, float))
                and weight >= threshold
                and float(body_size) - 1.75 <= float(size) <= float(body_size) + 0.75
            ):
                profile_sizes.append(float(size))
    if profile_sizes:
        return max(5.0, _natural_word_size(min(profile_sizes)))
    return max(5.0, _natural_word_size(body_size - 1.5))


def _natural_word_candidate_sizes(body_size: float, style_profile: dict | None = None) -> list[float]:
    base = _natural_word_size(body_size)
    floor = min(base, _natural_word_candidate_floor(body_size, style_profile))
    values = []
    current = floor
    while current <= base + 0.5:
        values.append(round(current, 2))
        current += 0.5
    return list(dict.fromkeys(round(max(5.0, value), 2) for value in values))


def _candidate_settings(
    body_size: float,
    mode: str = "full",
    source_page_count: int = 4,
    style_profile: dict | None = None,
) -> list[tuple[float, float, float]]:
    if mode == "none":
        # Production default: one deterministic build. The older candidate sweep
        # remains available only when explicitly requested as fast/full diagnostics.
        return [(_natural_word_size(max(5.0, body_size * 0.89)), 0.0, 1.05)]
    if mode == "fast" and source_page_count >= 8:
        # PDFs exported from Word usually carry natural half-point font sizes.
        # Keep spacing independent from font size: larger text with modest Word
        # paragraph gaps is a different candidate from larger text plus loose gaps.
        sizes = _natural_word_candidate_sizes(body_size, style_profile)
        gap_profiles = (0.0, 0.25)
        line_profiles = (0.0, 1.0, 1.05)
        triples = [(size, gap, line) for size in sizes for gap in gap_profiles for line in line_profiles]
        line_rank = {0.0: 0, 1.0: 1, 1.05: 2}
        return sorted(
            triples,
            key=lambda item: (
                item[0],
                item[1],
                line_rank.get(item[2], 9),
                item[0] * (item[2] if item[2] > 0 else 1.08) + item[1] * 1.35,
            ),
        )
    sizes = _candidate_sizes(body_size)
    if mode == "fast":
        selected_sizes = sizes[-2:] if len(sizes) >= 2 else sizes
        return [(size, gap, 1.05) for gap in (0.72, 0.84) for size in selected_sizes]
    return [(size, gap, 1.05) for gap in (0.62, 0.72, 0.84) for size in sizes]


def _candidate_page_fidelity_exact(record: dict[str, Any], source_page_count: int) -> bool:
    comparison = record.get("comparison") or {}
    return (
        int(comparison.get("output_page_count") or -1) == int(source_page_count)
        and bool(comparison.get("page_boundary_pass", True))
    )


def _candidate_paragraph_fit_rank(record: dict[str, Any]) -> tuple[int, float, float]:
    summary = ((record.get("build_report") or {}).get("flow_geometry_fit_summary") or {})
    if not summary or int(summary.get("count") or 0) <= 0:
        return (10**6, 10**6.0, 10**6.0)
    return (
        int(summary.get("bad_count") or 0),
        float(summary.get("average_abs_text_end_delta_pt") or 0.0),
        float(summary.get("max_abs_text_end_delta_pt") or 0.0),
    )


def _is_renderer_unavailable_error(message: str) -> bool:
    return (
        "Δεν βρέθηκε renderer DOCX→PDF" in message
        or "Word page-count probe requires Microsoft Word" in message
        or "Word font-scale sandbox requires Microsoft Word" in message
        or "CoInitialize δεν έχει κληθεί" in message
        or "Microsoft Word + pywin32" in message
    )


def _single_page_analysis(report: dict[str, Any], page_no: int) -> dict[str, Any]:
    clone = deepcopy(report)
    clone["pages"] = [
        page for page in clone.get("pages", []) or []
        if int(page.get("page") or 0) == int(page_no)
    ]
    if "selected_pages" in clone:
        clone["selected_pages"] = [int(page_no)]
    return clone


def _candidate_neighbourhood(center_index: int, count: int, radius: int = 2) -> list[int]:
    indexes: list[int] = []
    for offset in range(0, radius + 1):
        for candidate in ({center_index} if offset == 0 else (center_index - offset, center_index + offset)):
            if 0 <= candidate < count and candidate not in indexes:
                indexes.append(candidate)
    return indexes


def _candidate_lanes(settings: list[tuple[float, float, float]]) -> list[dict[str, Any]]:
    lanes: dict[tuple[float, float], list[int]] = {}
    for index, setting in enumerate(settings):
        gap = round(float(setting[1]), 3)
        line = round(float(setting[2] if len(setting) > 2 else 1.05), 3)
        lanes.setdefault((gap, line), []).append(index)
    line_rank = {1.0: 0, 1.05: 1, 0.0: 2}
    result: list[dict[str, Any]] = []
    for (gap, line), indexes in lanes.items():
        result.append({
            "gap_scale": gap,
            "line_spacing_multiple": line,
            "indexes": sorted(indexes, key=lambda idx: float(settings[idx][0])),
        })
    return sorted(
        result,
        key=lambda lane: (
            line_rank.get(float(lane["line_spacing_multiple"]), 9),
            float(lane["gap_scale"]),
        ),
    )


def _probe_guided_full_window(
    probe_records: list[dict],
    candidate_count: int,
    expected_page_count: int,
) -> dict:
    end = max(0, candidate_count - 1)
    margin = max(3, candidate_count // 20)
    usable: list[dict] = []
    for record in probe_records:
        comparison = record.get("comparison") or {}
        if record.get("candidate_index") is None or comparison.get("output_page_count") is None:
            continue
        page_deltas = [
            float(page.get("text_end_delta_signed_pt") or 0.0)
            for page in comparison.get("pages", []) or []
            if page.get("text_end_delta_signed_pt") is not None
        ]
        signed_delta = sum(page_deltas) / len(page_deltas) if page_deltas else 0.0
        usable.append({
            "candidate_index": int(record["candidate_index"]),
            "output_page_count": int(comparison.get("output_page_count") or 0),
            "page_boundary_pass": bool(comparison.get("page_boundary_pass", True)),
            "average_text_end_delta_signed_pt": round(signed_delta, 3),
            "average_text_end_delta_pt": float(comparison.get("average_text_end_delta_pt") or 0.0),
        })
    if not usable:
        return {
            "low": 0,
            "high": end,
            "status": "unbounded-no-usable-probe-records",
            "margin": margin,
            "records": [],
        }
    exact_rows = [
        row for row in usable
        if row["output_page_count"] == expected_page_count and row["page_boundary_pass"]
    ]
    exact = [
        row["candidate_index"]
        for row in exact_rows
    ]
    under = [row["candidate_index"] for row in usable if row["output_page_count"] < expected_page_count]
    over = [row["candidate_index"] for row in usable if row["output_page_count"] > expected_page_count]
    compact_exact = [
        row["candidate_index"] for row in exact_rows
        if row["average_text_end_delta_signed_pt"] < -60.0
    ]
    loose_exact = [
        row["candidate_index"] for row in exact_rows
        if row["average_text_end_delta_signed_pt"] > 60.0
    ]
    balanced_exact = [
        row["candidate_index"] for row in exact_rows
        if abs(row["average_text_end_delta_signed_pt"]) <= 60.0
    ]
    if balanced_exact:
        low = max(0, min(balanced_exact) - margin)
        high = min(end, max(balanced_exact) + margin)
        status = "bounded-by-balanced-exact-probe-candidates"
    elif compact_exact and not loose_exact:
        low = max(0, max(compact_exact) - margin)
        high = end
        status = "bounded-high-side-by-underfilled-exact-probe"
    elif loose_exact and not compact_exact:
        low = 0
        high = min(end, min(loose_exact) + margin)
        status = "bounded-low-side-by-overfilled-exact-probe"
    elif exact:
        low = max(0, min(exact) - margin)
        high = min(end, max(exact) + margin)
        status = "bounded-by-mixed-exact-probe-candidates"
    elif under and over:
        low = max(0, max(under) - margin)
        high = min(end, min(over) + margin)
        status = "bounded-by-probe-page-count-bracket"
        if low > high:
            low, high = 0, end
            status = "unbounded-non-monotonic-probe-bracket"
    elif over:
        low = 0
        high = min(end, min(over) + margin)
        status = "bounded-low-side-by-probe-overflow"
    else:
        low = max(0, max(under) - margin)
        high = end
        status = "bounded-high-side-by-probe-underflow"
    return {
        "low": low,
        "high": high,
        "status": status,
        "margin": margin,
        "expected_page_count": expected_page_count,
        "records": sorted(usable, key=lambda row: row["candidate_index"]),
    }


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _page_density_row(
    page: dict[str, Any],
    *,
    pdf_page: dict[str, Any] | None = None,
    body_size: float | None = None,
) -> dict[str, Any] | None:
    page_no = int(page.get("page") or 0)
    fullness = page.get("page_fullness") or {}
    page_width = float(page.get("width_pt") or (pdf_page or {}).get("width_pt") or 0.0)
    page_height = float(page.get("height_pt") or (pdf_page or {}).get("height_pt") or 0.0)
    body_chars = 0
    small_chars = 0
    text_chars = 0
    visual_area = 0.0
    if pdf_page and body_size:
        for region in pdf_page.get("regions", []) or []:
            box = _bbox(region.get("bbox"))
            if box and region.get("type") == "image":
                visual_area += (box[2] - box[0]) * (box[3] - box[1])
            if region.get("type") != "text":
                continue
            semantic = region.get("semantic") or {}
            if semantic.get("type") in {"header", "footer", "page_number"}:
                continue
            for line in region.get("lines", []) or []:
                for span in line.get("spans", []) or []:
                    text = str(span.get("text") or "").strip()
                    if not text:
                        continue
                    chars = len(text)
                    text_chars += chars
                    try:
                        size = float(span.get("size_pt") or body_size)
                    except (TypeError, ValueError):
                        size = float(body_size)
                    if abs(size - float(body_size)) <= 0.75:
                        body_chars += chars
                    elif size < float(body_size) - 1.25:
                        small_chars += chars
    for group in page.get("visual_groups", []) or []:
        box = _bbox(group.get("bbox"))
        if box:
            visual_area += (box[2] - box[0]) * (box[3] - box[1])
    visual_area_ratio = 0.0
    if page_width > 0 and page_height > 0:
        visual_area_ratio = max(0.0, min(1.0, visual_area / (page_width * page_height)))
    if text_chars and body_chars:
        body_char_ratio = body_chars / max(1, text_chars)
        small_char_ratio = small_chars / max(1, text_chars)
        body_density = min(1.0, body_chars / 2200.0)
        body_text_score = (
            body_density * 0.48
            + body_char_ratio * 0.26
            + max(0.0, 1.0 - visual_area_ratio) * 0.18
            + max(0.0, 1.0 - small_char_ratio) * 0.08
        )
        return {
            "page": page_no,
            "score": round(body_text_score, 4),
            "flowItems": len(page.get("flow") or []),
            "visualGroups": len(page.get("visual_groups") or []),
            "verticalCoverage": (fullness or {}).get("verticalCoverage"),
            "areaRatio": (fullness or {}).get("areaRatio"),
            "visualAreaRatio": round(visual_area_ratio, 4),
            "textRegionCount": (fullness or {}).get("textRegionCount") or (pdf_page or {}).get("text_region_count"),
            "imageRegionCount": (fullness or {}).get("imageRegionCount") or (pdf_page or {}).get("image_region_count"),
            "bodyChars": body_chars,
            "textChars": text_chars,
            "bodyCharRatio": round(body_char_ratio, 4),
            "smallCharRatio": round(small_char_ratio, 4),
            "source": "pdf-body-font-text-density",
        }
    if page_no and isinstance(fullness, dict) and isinstance(fullness.get("score"), (int, float)):
        return {
            "page": page_no,
            "score": round(float(fullness.get("score") or 0.0), 4),
            "flowItems": len(page.get("flow") or []),
            "visualGroups": len(page.get("visual_groups") or []),
            "verticalCoverage": fullness.get("verticalCoverage"),
            "areaRatio": fullness.get("areaRatio"),
            "textRegionCount": fullness.get("textRegionCount"),
            "imageRegionCount": fullness.get("imageRegionCount"),
            "contentBBox": fullness.get("contentBBox"),
            "source": "pdf-analysis-page-fullness",
        }
    flow_boxes = [
        box for box in (_bbox(item.get("bbox")) for item in (page.get("flow") or []))
        if box is not None
    ]
    visual_boxes = [
        box for box in (_bbox(item.get("bbox")) for item in (page.get("visual_groups") or []))
        if box is not None
    ]
    if not page_no or page_height <= 0 or not flow_boxes:
        return None
    top = min(box[1] for box in flow_boxes)
    bottom = max(box[3] for box in flow_boxes)
    vertical_coverage = max(0.0, min(1.0, (bottom - top) / page_height))
    visual_area = sum((box[2] - box[0]) * (box[3] - box[1]) for box in visual_boxes)
    visual_area_ratio = 0.0
    if page_width > 0 and page_height > 0:
        visual_area_ratio = max(0.0, min(1.0, visual_area / (page_width * page_height)))
    flow_count = len(flow_boxes)
    text_weight = min(1.0, flow_count / 12.0)
    density_score = round((vertical_coverage * 0.62) + (text_weight * 0.28) + (visual_area_ratio * 0.10), 4)
    return {
        "page": page_no,
        "score": density_score,
        "flowItems": flow_count,
        "visualGroups": len(visual_boxes),
        "verticalCoverage": round(vertical_coverage, 4),
        "visualAreaRatio": round(visual_area_ratio, 4),
        "textTopPt": round(top, 3),
        "textBottomPt": round(bottom, 3),
    }


def _select_probe_pages(
    pages: list[int],
    page_structure: dict[str, Any] | None,
    *,
    max_pages: int = 3,
    pdf_analysis: dict[str, Any] | None = None,
    body_size: float | None = None,
) -> tuple[list[int], dict[str, Any]]:
    requested = [int(page) for page in pages[:max_pages]]
    page_set = {int(page) for page in pages}
    pdf_pages = {
        int(page.get("page") or 0): page
        for page in (pdf_analysis or {}).get("pages", []) or []
        if int(page.get("page") or 0)
    }
    scored = [
        row for row in (
            _page_density_row(
                page,
                pdf_page=pdf_pages.get(int(page.get("page") or 0)),
                body_size=body_size,
            )
            for page in (page_structure or {}).get("pages", []) or []
            if int(page.get("page") or 0) in page_set
        )
        if row is not None
    ]
    if not scored:
        return requested, {
            "policy": "auto-dense-pages",
            "fallback": "first-pages-no-density-score",
            "selected_pages": requested,
            "scores": [],
        }
    ranked = sorted(scored, key=lambda row: (-float(row["score"]), int(row["page"])))
    selected = sorted(int(row["page"]) for row in ranked[:max_pages])
    return selected, {
        "policy": "auto-dense-pages",
        "selected_pages": selected,
        "ranked_top": ranked[:min(12, len(ranked))],
        "score_count": len(scored),
    }


def main(argv: list[str] | None = None) -> int:
    started_at = time.perf_counter()
    args = build_parser().parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    logs = args.output / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(logs / "run.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)],
        force=True,
    )

    if not args.pdf.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε PDF: {args.pdf}")
    if not args.docx.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε DOCX: {args.docx}")

    import fitz

    with fitz.open(args.pdf) as probe:
        pages = parse_page_range(args.pages, max_pages=probe.page_count)

    work_dir = args.output / "work"
    analysis_dir = args.output / "analysis"
    report_dir = args.output / "report"
    asset_dir = args.output / "page_assets"
    calibration_dir = work_dir / "calibration"

    def write_progress(stage: str, **payload: Any) -> None:
        if not args.progress_report:
            return
        data = {
            "ok": True,
            "stage": stage,
            "elapsedSeconds": round(time.perf_counter() - started_at, 1),
            **payload,
        }
        try:
            write_json(args.progress_report, data)
        except Exception:
            logging.debug("Could not write progress report", exc_info=True)

    logging.info("Ανάλυση PDF σελίδων %s με line-gap block splitting", pages)
    write_progress("Ανάλυση PDF", sourcePages=pages, pageCount=len(pages))
    pdf_analysis = analyze_pdf(args.pdf, pages, work_dir, dpi=args.dpi)

    logging.info("Εξαγωγή profile γραμματοσειρών")
    write_progress("Εξαγωγή profile γραμματοσειρών", pageCount=len(pages))
    style_profile = build_style_profile(pdf_analysis)
    write_json(analysis_dir / "style_profile.json", style_profile)

    logging.info("Σημασιολογική ταξινόμηση PDF regions")
    write_progress("Σημασιολογική ταξινόμηση PDF regions")
    classification_summary = classify_pdf_regions(
        pdf_analysis,
        body_size=style_profile.get("inferred_body_font_size_pt"),
    )
    write_json(analysis_dir / "pdf_layout_classified.json", pdf_analysis)
    write_json(analysis_dir / "classification_summary.json", classification_summary)

    logging.info("Ανάλυση DOCX")
    write_progress("Ανάλυση Mathpix DOCX donor")
    docx_analysis = analyze_docx(args.docx)
    write_json(analysis_dir / "docx_inventory.json", docx_analysis)

    logging.info("Sequence-aware αντιστοίχιση PDF regions με DOCX paragraph spans")
    write_progress("Αντιστοίχιση PDF regions με DOCX donor")
    alignment = align_pdf_to_docx(pdf_analysis, docx_analysis)
    write_json(analysis_dir / "alignment.json", alignment)

    logging.info("Ομαδοποίηση εξισώσεων/σχημάτων και ανίχνευση κύριας στήλης ανά σελίδα")
    write_progress("Χαρτογράφηση εξισώσεων, σχημάτων και στηλών")
    page_structure = build_page_structure(
        pdf_analysis, work_dir, asset_dir,
        reference_docx=args.docx,
        external_asset_paths=[Path(path).resolve() for path in args.external_assets],
        equation_donor_path=args.equation_donors.resolve() if args.equation_donors else None,
    )
    write_json(analysis_dir / "page_structure.json", page_structure)
    if page_structure.get("markdown_equation_map"):
        write_json(analysis_dir / "markdown_equation_map.json", page_structure["markdown_equation_map"])

    markdown_element_map = {}
    if args.markdown_element_map and args.markdown_element_map.exists():
        markdown_element_map = json.loads(args.markdown_element_map.read_text(encoding="utf-8"))
    markdown_pdf_spine = {}
    if markdown_element_map:
        logging.info("Markdown-first / PDF-guided spine")
        write_progress("Markdown-first / PDF-guided spine")
        markdown_pdf_spine = build_markdown_pdf_spine(markdown_element_map, pdf_analysis)
        write_json(analysis_dir / "markdown_pdf_spine.json", markdown_pdf_spine)

    logging.info("DOCX donor map")
    write_progress("Χάρτης δότη Mathpix DOCX")
    docx_donor_map = build_docx_donor_map(docx_analysis, markdown_element_map or None, alignment)
    write_json(analysis_dir / "docx_donor_map.json", docx_donor_map)
    page_layout_spine = {}
    if markdown_pdf_spine:
        logging.info("Page layout spine Markdown/PDF → layout slots")
        write_progress("Page layout spine Markdown/PDF → θέσεις Word")
        page_layout_spine = build_page_layout_spine(markdown_pdf_spine, page_structure, docx_donor_map)
        write_json(analysis_dir / "page_layout_spine.json", page_layout_spine)
    mapping_preflight = build_mapping_fidelity(
        markdown_pdf_spine=markdown_pdf_spine or None,
        page_layout_spine=page_layout_spine or None,
        conversion_spine=None,
        require_conversion=False,
    )
    write_json(analysis_dir / "mapping_fidelity_preflight.json", mapping_preflight)
    if mapping_preflight.get("status") == "fail":
        message = (
            "FAIL: mapping fidelity preflight failed before Word render. "
            + " · ".join(
                str(item.get("message") or item.get("code") or item)
                for item in (mapping_preflight.get("violations") or [])[:6]
            )
        )
        logging.error(message)
        write_progress("Αποτυχία ελέγχου χαρτογράφησης", status="failed", reason=message)
        (args.output / "FAILED_MAPPING_FIDELITY.txt").write_text(message + "\n", encoding="utf-8")
        report_path = build_html_report(
            report_dir,
            work_dir,
            pdf_analysis,
            docx_analysis,
            alignment,
            style_profile,
            classification_summary,
            page_structure=page_structure,
            markdown_pdf_spine=markdown_pdf_spine,
            docx_donor_map=docx_donor_map,
            mapping_fidelity=mapping_preflight,
            page_layout_spine=page_layout_spine,
        )
        print("\nΑΠΟΤΥΧΙΑ MAPPING FIDELITY")
        print(f"Report: {report_path}")
        print(f"Log:    {logs / 'run.log'}")
        print("Δεν ξεκίνησε Word render/calibration.")
        return 26

    final_docx = args.output / f"native_page_structure_{args.pages.replace(',', '_')}.docx"
    final_pdf = args.output / f"native_page_structure_{args.pages.replace(',', '_')}.pdf"
    inferred_body = float(style_profile.get("inferred_body_font_size_pt") or 10.5)
    source_page_count = len(pages)
    candidate_settings = _candidate_settings(inferred_body, args.calibration, source_page_count, style_profile)
    calibration: dict = {
        "status": "skipped",
        "renderer": None,
        "inferred_pdf_body_size_pt": inferred_body,
        "source_page_count": source_page_count,
        "candidates": [],
    }
    final_build_report = None
    strict_failure_code = 0
    # Prevent stale results from a previous failed run from being mistaken for fresh output.
    for stale in (final_docx, final_pdf):
        if stale.exists():
            stale.unlink()

    if not args.no_render:
        calibration_dir.mkdir(parents=True, exist_ok=True)
        renderer_name = None
        best = None
        renderer_unavailable_error: str | None = None
        active_font_scale = 1.0
        word_free_flow_section_contract = bool(page_layout_spine)
        active_calibration_policy = (
            "word free-flow section contract: PDF-derived margins, explicit typography candidates, no hidden global font-scale"
            if word_free_flow_section_contract
            else "strict compact-seed interval search"
        )
        logging.info(
            "Typography build mode %s: %s; %s grid candidate(s) available only after contract allows it",
            args.calibration,
            active_calibration_policy,
            len(candidate_settings),
        )
        write_progress(
            "Έναρξη Word typography calibration",
            calibration=args.calibration,
            candidateCount=len(candidate_settings),
            activePolicy=active_calibration_policy,
            targetPages=source_page_count,
            inferredPdfBodySizePt=inferred_body,
        )
        calibration["word_flow_contract"] = {
            "policy": "word-free-flow-single-section",
            "active": bool(word_free_flow_section_contract),
            "hard_page_or_section_breaks_between_pdf_pages": False,
            "pagination_authority": "Microsoft Word automatic pagination",
            "margin_source": "page_layout_spine.layoutPreflight.pageSetupEstimate",
        }
        calibration["word_font_scale_sandbox"] = {
            "policy": "disabled-word-free-flow-explicit-candidates",
            "selectedFontScale": 1.0,
            "reason": (
                "Natural Word flow must be calibrated by explicit body size / gap / line-spacing candidates. "
                "Hidden global font scaling is disabled so typography changes remain visible and auditable."
            ),
        }
        compact_seed_index = 0

        def remember_candidate(record: dict[str, Any]) -> None:
            nonlocal best
            comparison = record["comparison"]
            if best is None:
                best = record
                return
            current_exact = bool(record.get("page_fidelity_exact"))
            best_exact = bool(best.get("page_fidelity_exact"))
            if current_exact and not best_exact:
                best = record
            elif current_exact == best_exact:
                current_page_count_exact = bool(record.get("page_count_exact"))
                best_page_count_exact = bool(best.get("page_count_exact"))
                if current_page_count_exact and not best_page_count_exact:
                    best = record
                    return
                if current_page_count_exact != best_page_count_exact:
                    return
                current_delta = abs(int(comparison["output_page_count"]) - source_page_count)
                best_delta = abs(int(best["comparison"]["output_page_count"]) - source_page_count)
                current_rank = _candidate_paragraph_fit_rank(record)
                best_rank = _candidate_paragraph_fit_rank(best)
                if (
                    current_delta < best_delta
                    or (
                        current_delta == best_delta
                        and (
                            current_rank < best_rank
                            or (
                                current_rank == best_rank
                                and comparison["objective"] < best["comparison"]["objective"]
                            )
                        )
                    )
                ):
                    best = record

        def evaluate_candidate(
            candidate_index: int,
            *,
            phase: str = "full",
            candidate_pdf_analysis: dict[str, Any] | None = None,
            candidate_page_structure: dict[str, Any] | None = None,
            candidate_pages: list[int] | None = None,
            word_count_only: bool = False,
            remember: bool = True,
        ) -> dict[str, Any] | None:
            nonlocal renderer_name, renderer_unavailable_error, active_font_scale
            setting = candidate_settings[candidate_index]
            size = float(setting[0])
            gap_scale = float(setting[1])
            line_spacing_multiple = float(setting[2]) if len(setting) > 2 else 1.05
            expected_page_count = len(candidate_pages or pages)
            stem = f"{phase}_candidate_{size:.2f}_g{gap_scale:.2f}_l{line_spacing_multiple:.2f}"
            candidate_docx = calibration_dir / f"{stem}.docx"
            candidate_pdf = calibration_dir / f"{stem}.pdf"
            build_report = None
            try:
                build_report = build_native_page_document(
                    candidate_pdf_analysis or pdf_analysis,
                    candidate_page_structure or page_structure,
                    alignment,
                    docx_analysis,
                    style_profile,
                    candidate_docx,
                    body_size_override=size,
                    font_scale=active_font_scale,
                    gap_scale=gap_scale,
                    body_line_spacing_multiple=line_spacing_multiple,
                    docx_donor_map=docx_donor_map,
                    page_layout_spine=page_layout_spine,
                    flow_mode="free",
                )
                page_count, page_count_renderer = count_docx_pages_with_word(candidate_docx)
                renderer_name = page_count_renderer
                if page_count == expected_page_count and not word_count_only:
                    renderer_name = export_docx_to_pdf(candidate_docx, candidate_pdf)
                    comparison = compare_pdf_to_source(
                        args.pdf,
                        candidate_pages or pages,
                        candidate_pdf,
                        calibration_dir / f"compare_{phase}_{size:.2f}_g{gap_scale:.2f}_l{line_spacing_multiple:.2f}",
                        dpi=110,
                        write_images=False,
                        page_structure=candidate_page_structure or page_structure,
                    )
                    page_count_probe_only = False
                else:
                    page_delta = abs(int(page_count) - int(expected_page_count))
                    comparison = {
                        "source_page_count": expected_page_count,
                        "output_page_count": page_count,
                        "objective": round(page_delta * 1000.0, 4),
                        "page_boundary_pass": bool(page_count == expected_page_count and word_count_only),
                        "page_boundary_failure_count": None,
                        "average_text_end_delta_pt": None,
                        "average_overflow_delta_pt": None,
                        "average_underfill_delta_pt": None,
                        "page_count_probe_only": True,
                        "page_count_probe_renderer": page_count_renderer,
                        "word_count_only": bool(word_count_only),
                    }
                    page_count_probe_only = True
                record = {
                    "phase": phase,
                    "candidate_index": candidate_index,
                    "body_size_pt": size,
                    "gap_scale": gap_scale,
                    "font_scale": active_font_scale,
                    "line_spacing_multiple": line_spacing_multiple,
                    "docx": str(candidate_docx),
                    "pdf": str(candidate_pdf),
                    "comparison": comparison,
                    "page_count_exact": comparison["output_page_count"] == expected_page_count,
                    "page_boundary_pass": bool(comparison.get("page_boundary_pass", True)),
                    "build_report": build_report,
                    "page_count_probe_only": page_count_probe_only,
                }
                record["page_fidelity_exact"] = _candidate_page_fidelity_exact(record, expected_page_count)
                if phase == "full":
                    calibration["candidates"].append(record)
                else:
                    calibration.setdefault("first_page_probe", {}).setdefault("candidates", []).append(record)
                calibration.setdefault("candidate_probe_order", []).append({"phase": phase, "index": candidate_index})
                logging.info(
                    "%s candidate %.2f pt / gap %.2f / line %.2f: %s pages, objective %.4f%s",
                    phase,
                    size,
                    gap_scale,
                    line_spacing_multiple,
                    comparison["output_page_count"],
                    comparison["objective"],
                    " (Word page-count probe)" if page_count_probe_only else "",
                )
                if remember:
                    remember_candidate(record)
                best_pages = None
                best_delta = None
                best_size = None
                if best is not None:
                    best_comparison = best.get("comparison") or {}
                    best_pages = best_comparison.get("output_page_count")
                    if best_pages is not None:
                        best_delta = abs(int(best_pages) - int(source_page_count))
                    best_size = best.get("body_size_pt")
                write_progress(
                    "Word page-count calibration",
                    phase=phase,
                    candidateIndex=candidate_index,
                    candidateBodySizePt=size,
                    fontScale=active_font_scale,
                    gapScale=gap_scale,
                    lineSpacingMultiple=line_spacing_multiple,
                    outputPages=comparison.get("output_page_count"),
                    targetPages=expected_page_count,
                    pageDelta=abs(int(comparison.get("output_page_count") or 0) - int(expected_page_count)),
                    objective=comparison.get("objective"),
                    probeOnly=page_count_probe_only,
                    bestOutputPages=best_pages,
                    bestPageDelta=best_delta,
                    bestBodySizePt=best_size,
                )
                return record
            except Exception as exc:
                error_message = str(exc)
                if _is_renderer_unavailable_error(error_message):
                    renderer_unavailable_error = error_message
                logging.warning(
                    "%s render/calibration candidate %.2f / gap %.2f / line %.2f απέτυχε: %s",
                    phase,
                    size,
                    gap_scale,
                    line_spacing_multiple,
                    exc,
                )
                failed_record = {
                    "phase": phase,
                    "candidate_index": candidate_index,
                    "body_size_pt": size,
                    "gap_scale": gap_scale,
                    "line_spacing_multiple": line_spacing_multiple,
                    "error": error_message,
                    "renderer_unavailable": _is_renderer_unavailable_error(error_message),
                    "build_report": build_report,
                }
                if phase == "full":
                    calibration["candidates"].append(failed_record)
                else:
                    calibration.setdefault("first_page_probe", {}).setdefault("candidates", []).append(failed_record)
                calibration.setdefault("candidate_probe_order", []).append({"phase": phase, "index": candidate_index})
                write_progress(
                    "Αποτυχία candidate calibration",
                    phase=phase,
                    candidateIndex=candidate_index,
                    candidateBodySizePt=size,
                    fontScale=active_font_scale,
                    gapScale=gap_scale,
                    lineSpacingMultiple=line_spacing_multiple,
                    error=error_message,
                )
                return None

        if args.calibration == "fast" and len(candidate_settings) > 2:
            seed_docx = calibration_dir / "word_sandbox_seed.docx"
            try:
                seed_index = 0
                if seed_index != 0:
                    raise CalibrationContractError("Font-scale sandbox must start from compact floor candidate 0.")
                seed_setting = candidate_settings[seed_index]
                build_native_page_document(
                    pdf_analysis,
                    page_structure,
                    alignment,
                    docx_analysis,
                    style_profile,
                    seed_docx,
                    body_size_override=float(seed_setting[0]),
                    font_scale=1.0,
                    gap_scale=float(seed_setting[1]),
                    body_line_spacing_multiple=float(seed_setting[2]) if len(seed_setting) > 2 else 1.05,
                    docx_donor_map=docx_donor_map,
                    page_layout_spine=page_layout_spine,
                    flow_mode="free",
                )
                seed_pages, seed_renderer = count_docx_pages_with_word(seed_docx)
                scale_probe = {
                    "policy": "disabled-word-free-flow-explicit-candidates",
                    "expected_pages": int(source_page_count),
                    "renderer": seed_renderer,
                    "direction": "disabled",
                    "results": [
                        {
                            "scale": 1.0,
                            "pages": int(seed_pages),
                            "page_delta": abs(int(seed_pages) - int(source_page_count)),
                        }
                    ],
                    "best": {
                        "scale": 1.0,
                        "pages": int(seed_pages),
                        "page_delta": abs(int(seed_pages) - int(source_page_count)),
                    },
                }
                scale_probe["calibration_contract"] = {
                    "status": "checking",
                    "rules": [
                        "font-scale sandbox seed must be compact floor candidate 0",
                        "first sandbox measurement must be scale 1.0",
                        "after the first measurement, every next scale must move only toward the needed page-count direction",
                        "the remaining interval must be halved instead of walked by a fixed list",
                        "when compact seed is over target pages, probe/full escalation to larger typography is forbidden",
                    ],
                }
                initial_rows = list(scale_probe.get("results") or [])
                if not initial_rows or float(initial_rows[0].get("scale") or 0.0) != 1.0:
                    raise CalibrationContractError("Font-scale sandbox must measure compact seed at scale 1.0 first.")
                initial_pages = int(initial_rows[0].get("pages") or 0)
                scale_rows = list(scale_probe.get("results") or [])
                if initial_pages > source_page_count and any(float(row.get("scale") or 0.0) > 1.0 for row in scale_rows[1:]):
                    raise CalibrationContractError(
                        "Calibration attempted font growth while compact seed is already over target pages."
                    )
                if initial_pages < source_page_count and any(float(row.get("scale") or 0.0) < 1.0 for row in scale_rows[1:]):
                    raise CalibrationContractError(
                        "Calibration attempted font shrink while compact seed is already under target pages."
                    )
                calibration["word_font_scale_sandbox"] = scale_probe
                calibration["word_font_scale_sandbox"]["calibration_contract"]["status"] = "pass"
                calibration["word_font_scale_sandbox"]["seed_candidate"] = {
                    "policy": "compact-floor-seed-before-any-growth",
                    "candidate_index": seed_index,
                    "body_size_pt": float(seed_setting[0]),
                    "gap_scale": float(seed_setting[1]),
                    "line_spacing_multiple": float(seed_setting[2]) if len(seed_setting) > 2 else 1.05,
                    "growth_allowed_only_when_seed_under_target": True,
                }
                best_scale = ((scale_probe.get("best") or {}).get("scale"))
                if best_scale:
                    active_font_scale = float(best_scale)
                    logging.info(
                        "Word font-scale interval sandbox επέλεξε scale %.5f από %s μέτρηση/εις",
                        active_font_scale,
                        len(scale_probe.get("results") or []),
                    )
                    write_progress(
                        "Word font-scale interval sandbox",
                        targetPages=source_page_count,
                        selectedFontScale=active_font_scale,
                        best=scale_probe.get("best"),
                        direction=scale_probe.get("direction"),
                        results=scale_probe.get("results", []),
                    )
            except CalibrationContractError as exc:
                calibration["word_font_scale_sandbox"] = {
                    "policy": "word-open-temporary-font-scale-repaginate-no-save",
                    "calibration_contract": {
                        "status": "fail",
                        "error": str(exc),
                    },
                }
                (args.output / "FAILED_CALIBRATION_CONTRACT.txt").write_text(
                    f"FAIL: {exc}\n",
                    encoding="utf-8",
                )
                write_progress("Αποτυχία calibration contract", status="failed", reason=str(exc))
                raise
            except Exception as exc:
                error_message = str(exc)
                calibration["word_font_scale_sandbox"] = {
                    "policy": "word-open-temporary-font-scale-repaginate-no-save",
                    "error": error_message,
                }
                if _is_renderer_unavailable_error(error_message):
                    renderer_unavailable_error = error_message
                    calibration["renderer_unavailable_error"] = renderer_unavailable_error
                    calibration["renderer_probe_aborted"] = True
                    logging.error("Word font-scale sandbox failed: %s", exc)
                    best = None
                    calibration["status"] = "failed-renderer-unavailable"
                logging.warning("Word font-scale sandbox failed: %s", exc)
            full_indexes: list[int] = []
            evaluated_full_indexes: set[int] = set()

            def evaluate_full_index(candidate_index: int, reason: str) -> dict[str, Any] | None:
                if candidate_index in evaluated_full_indexes:
                    return None
                evaluated_full_indexes.add(candidate_index)
                full_indexes.append(candidate_index)
                record = evaluate_candidate(candidate_index)
                if record is not None:
                    record["fast_search_reason"] = reason
                return record

            full_search_limit = 8
            page_count_probe_search_limit = min(len(candidate_settings), 36)
            exact_failed_index: int | None = None
            word_typography_probe: dict[str, Any] | None = None
            try:
                typography_seed_index = max(
                    range(len(candidate_settings)),
                    key=lambda idx: (
                        float(candidate_settings[idx][1]),
                        0 if float(candidate_settings[idx][2]) <= 0.0 else 1,
                        float(candidate_settings[idx][0]),
                    ),
                )
                typography_seed_setting = candidate_settings[typography_seed_index]
                typography_seed_docx = calibration_dir / "word_typography_probe_seed.docx"
                build_native_page_document(
                    pdf_analysis,
                    page_structure,
                    alignment,
                    docx_analysis,
                    style_profile,
                    typography_seed_docx,
                    body_size_override=float(typography_seed_setting[0]),
                    font_scale=active_font_scale,
                    gap_scale=float(typography_seed_setting[1]),
                    body_line_spacing_multiple=float(typography_seed_setting[2]) if len(typography_seed_setting) > 2 else 1.05,
                    docx_donor_map=docx_donor_map,
                    page_layout_spine=page_layout_spine,
                    flow_mode="free",
                )
                word_typography_probe = probe_typography_settings_with_word(
                    typography_seed_docx,
                    source_page_count,
                    candidate_settings,
                    seed_index=typography_seed_index,
                )
                calibration["word_typography_triple_probe"] = word_typography_probe
                write_progress(
                    "Word typography triple probe",
                    targetPages=source_page_count,
                    seedIndex=typography_seed_index,
                    best=word_typography_probe.get("best"),
                    resultCount=word_typography_probe.get("result_count"),
                )
            except Exception as exc:
                error_message = str(exc)
                if _is_renderer_unavailable_error(error_message):
                    renderer_unavailable_error = error_message
                calibration["word_typography_triple_probe"] = {
                    "policy": "word-open-typography-triple-probe-no-save",
                    "status": "failed",
                    "error": error_message,
                    "renderer_unavailable": _is_renderer_unavailable_error(error_message),
                }
                logging.warning("Word typography triple probe failed: %s", exc)
            seed_reason = "compact-floor-page-count-preflight"
            compact_floor_record = evaluate_full_index(compact_seed_index, seed_reason)
            compact_floor_pages = int(((compact_floor_record or {}).get("comparison") or {}).get("output_page_count") or 0)
            compact_floor_blocks_typography = bool(compact_floor_pages > source_page_count)
            calibration["compact_floor_preflight"] = {
                "policy": "stop-before-probe-or-larger-full-search-when-compact-floor-is-still-over-target",
                "candidate_index": compact_seed_index,
                "output_page_count": compact_floor_pages or None,
                "target_page_count": source_page_count,
                "stopped": compact_floor_blocks_typography,
                "reason": (
                    "compact floor already produces more pages than the PDF; typography/probe escalation cannot be the primary cure"
                    if compact_floor_blocks_typography
                    else "compact floor does not exceed target; continue guided probe/search"
                ),
            }
            if compact_floor_blocks_typography:
                calibration["early_stop"] = {
                    "reason": "compact-floor-over-target-before-probe",
                    "candidate_index": compact_seed_index,
                    "body_size_pt": compact_floor_record.get("body_size_pt") if compact_floor_record else None,
                    "gap_scale": compact_floor_record.get("gap_scale") if compact_floor_record else None,
                    "line_spacing_multiple": compact_floor_record.get("line_spacing_multiple") if compact_floor_record else None,
                    "output_page_count": compact_floor_pages,
                    "target_page_count": source_page_count,
                    "full_candidate_count": len(calibration["candidates"]),
                }
                write_progress(
                    "Στάση calibration πριν από probe: compact floor πάνω από στόχο",
                    status="failed",
                    outputPages=compact_floor_pages,
                    targetPages=source_page_count,
                    reason="Δεν γίνεται probe προς μεγαλύτερα μεγέθη: η πιο συμπαγής λογική δοκιμή παράγει ήδη περισσότερες σελίδες.",
                )
            visited: set[int] = set()
            guide_pages, probe_page_selection = _select_probe_pages(
                pages,
                page_structure,
                max_pages=min(3, len(pages)),
                pdf_analysis=pdf_analysis,
                body_size=inferred_body,
            )
            probe_pages = list(pages)
            probe_pdf_analysis = deepcopy(pdf_analysis)
            probe_pdf_analysis["pages"] = [
                page for page in probe_pdf_analysis.get("pages", []) or []
                if int(page.get("page") or 0) in set(probe_pages)
            ]
            if "selected_pages" in probe_pdf_analysis:
                probe_pdf_analysis["selected_pages"] = list(probe_pages)
            probe_page_structure = deepcopy(page_structure)
            probe_page_structure["pages"] = [
                page for page in probe_page_structure.get("pages", []) or []
                if int(page.get("page") or 0) in set(probe_pages)
            ]
            calibration["search_strategy"] = "natural-word-font-gap-line-dense-page-probe-fast"
            calibration["first_page_probe"] = {
                "source_pages": probe_pages,
                "guide_pages": guide_pages,
                "policy": "all-pages-word-only-page-count-probe-shortlist-then-windowed-binary-full-search",
                "page_selection": probe_page_selection,
                "probe_policy": "adaptive-bisection-over-candidate-space",
                "render_policy": "no PDF export/compare during probe; PDF comparison only for selected full candidates",
                "candidates": [],
            }
            best_probe: dict[str, Any] | None = None
            probe_indexes: list[int] = []
            probe_low = 0
            probe_high = len(candidate_settings) - 1
            max_probe_count = min(7, len(candidate_settings))
            if word_typography_probe and word_typography_probe.get("results"):
                for row in word_typography_probe.get("results", []):
                    candidate_index = int(row.get("candidate_index") or 0)
                    output_pages = int(row.get("pages") or 0)
                    page_delta = abs(output_pages - len(probe_pages))
                    setting = candidate_settings[candidate_index]
                    record = {
                        "phase": "probe",
                        "candidate_index": candidate_index,
                        "body_size_pt": float(setting[0]),
                        "gap_scale": float(setting[1]),
                        "font_scale": active_font_scale,
                        "line_spacing_multiple": float(setting[2]) if len(setting) > 2 else 1.05,
                        "docx": None,
                        "pdf": None,
                        "comparison": {
                            "source_page_count": len(probe_pages),
                            "output_page_count": output_pages,
                            "objective": round(page_delta * 1000.0, 4),
                            "page_boundary_pass": output_pages == len(probe_pages),
                            "page_boundary_failure_count": None,
                            "average_text_end_delta_pt": None,
                            "average_overflow_delta_pt": None,
                            "average_underfill_delta_pt": None,
                            "page_count_probe_only": True,
                            "word_typography_triple_probe": True,
                        },
                        "page_count_exact": output_pages == len(probe_pages),
                        "page_boundary_pass": output_pages == len(probe_pages),
                        "page_count_probe_only": True,
                        "probe_page_fidelity_exact": output_pages == len(probe_pages),
                    }
                    calibration["first_page_probe"]["candidates"].append(record)
                    calibration.setdefault("candidate_probe_order", []).append({"phase": "word-typography-probe", "index": candidate_index})
                    probe_indexes.append(candidate_index)
                    visited.add(candidate_index)
                    if best_probe is None:
                        best_probe = record
                    else:
                        best_delta = abs(int((best_probe.get("comparison") or {}).get("output_page_count") or 0) - len(probe_pages))
                        if page_delta < best_delta:
                            best_probe = record
                max_probe_count = 0
            while (
                not compact_floor_blocks_typography
                and probe_low <= probe_high
                and len(probe_indexes) < max_probe_count
            ):
                candidate_index = (probe_low + probe_high) // 2
                if candidate_index in probe_indexes:
                    break
                probe_indexes.append(candidate_index)
                visited.add(candidate_index)
                record = evaluate_candidate(
                    candidate_index,
                    phase="probe",
                    candidate_pdf_analysis=probe_pdf_analysis,
                    candidate_page_structure=probe_page_structure,
                    candidate_pages=probe_pages,
                    word_count_only=True,
                    remember=False,
                )
                if record is None:
                    if renderer_unavailable_error:
                        calibration["renderer_unavailable_error"] = renderer_unavailable_error
                        calibration["renderer_probe_aborted"] = True
                        break
                    continue
                comparison = record["comparison"]
                pages_exact = int(comparison["output_page_count"]) == len(probe_pages)
                boundary_pass = bool(comparison.get("page_boundary_pass", True))
                probe_is_candidate = pages_exact and boundary_pass
                record["probe_page_fidelity_exact"] = probe_is_candidate
                if best_probe is None:
                    best_probe = record
                else:
                    best_comparison = best_probe.get("comparison", {}) or {}
                    best_exact = bool(best_probe.get("probe_page_fidelity_exact"))
                    best_objective = float(best_comparison.get("objective") or 10**9)
                    objective = float(comparison.get("objective") or 10**9)
                    if probe_is_candidate and (not best_exact or objective < best_objective):
                        best_probe = record
                    elif probe_is_candidate == best_exact and objective < best_objective:
                        best_probe = record
                output_pages = int(comparison.get("output_page_count") or 0)
                if probe_is_candidate:
                    page_deltas = [
                        float(page.get("text_end_delta_signed_pt") or 0.0)
                        for page in comparison.get("pages", []) or []
                        if page.get("text_end_delta_signed_pt") is not None
                    ]
                    signed_delta = sum(page_deltas) / len(page_deltas) if page_deltas else 0.0
                    record["probe_average_text_end_delta_signed_pt"] = round(signed_delta, 3)
                    if signed_delta < -60.0:
                        probe_low = candidate_index + 1
                    elif signed_delta > 60.0:
                        probe_high = candidate_index - 1
                    else:
                        break
                    continue
                if output_pages > len(probe_pages):
                    probe_high = candidate_index - 1
                elif output_pages < len(probe_pages):
                    probe_low = candidate_index + 1
                else:
                    break

            if not compact_floor_blocks_typography:
                probe_window = _probe_guided_full_window(
                    calibration["first_page_probe"].get("candidates", []),
                    len(candidate_settings),
                    len(probe_pages),
                )
                low = int(probe_window["low"])
                high = int(probe_window["high"])
                while (
                    not renderer_unavailable_error
                    and low <= high
                    and len(evaluated_full_indexes) < full_search_limit
                ):
                    mid = (low + high) // 2
                    record = evaluate_full_index(mid, "binary-page-count")
                    if record is None:
                        break
                    if bool(record.get("page_fidelity_exact")):
                        calibration["early_stop"] = {
                            "reason": "binary-full-search-exact",
                            "candidate_index": mid,
                            "body_size_pt": record.get("body_size_pt"),
                            "gap_scale": record.get("gap_scale"),
                            "line_spacing_multiple": record.get("line_spacing_multiple"),
                            "full_candidate_count": len(calibration["candidates"]),
                        }
                        break
                    comparison = record.get("comparison") or {}
                    output_pages = int(comparison.get("output_page_count") or 0)
                    if output_pages > source_page_count:
                        high = mid - 1
                    elif output_pages < source_page_count:
                        low = mid + 1
                    else:
                        exact_failed_index = mid
                        break
            if "probe_window" not in locals():
                probe_window = _probe_guided_full_window(
                    calibration["first_page_probe"].get("candidates", []),
                    len(candidate_settings),
                    len(probe_pages),
                )

            if (
                not any(bool(record.get("page_fidelity_exact")) for record in calibration.get("candidates", []))
                and exact_failed_index is not None
                and not compact_floor_blocks_typography
            ):
                for neighbour in _candidate_neighbourhood(exact_failed_index, len(candidate_settings), radius=2):
                    if len(evaluated_full_indexes) >= full_search_limit:
                        break
                    record = evaluate_full_index(neighbour, "exact-page-neighbourhood")
                    if record and bool(record.get("page_fidelity_exact")):
                        calibration["early_stop"] = {
                            "reason": "binary-full-search-neighbour-exact",
                            "candidate_index": neighbour,
                            "body_size_pt": record.get("body_size_pt"),
                            "gap_scale": record.get("gap_scale"),
                            "line_spacing_multiple": record.get("line_spacing_multiple"),
                            "full_candidate_count": len(calibration["candidates"]),
                        }
                        break
            calibration["first_page_probe"].update({
                "visited_indexes": sorted(visited),
                "selected_probe_index": int(best_probe.get("candidate_index")) if best_probe else None,
                "full_candidate_indexes": full_indexes,
                "probe_candidate_indexes": probe_indexes,
                "full_search_window": probe_window,
            })
            calibration["dense_page_probe"] = dict(calibration["first_page_probe"])
            if (
                not any(bool(record.get("page_fidelity_exact")) for record in calibration.get("candidates", []))
                and len(evaluated_full_indexes) < full_search_limit
                and not compact_floor_blocks_typography
            ):
                evaluated_indexes = {
                    int(record["candidate_index"])
                    for record in calibration.get("candidates", [])
                    if record.get("candidate_index") is not None
                }
                page_count_records = [
                    record for record in calibration.get("candidates", [])
                    if (record.get("comparison") or {}).get("output_page_count") is not None
                ]
                expansion_direction = None
                if page_count_records and all(
                    int((record.get("comparison") or {}).get("output_page_count") or 0) > source_page_count
                    for record in page_count_records
                ):
                    expansion_direction = -1
                    next_index = min(evaluated_indexes) - 1
                elif page_count_records and all(
                    int((record.get("comparison") or {}).get("output_page_count") or 0) < source_page_count
                    for record in page_count_records
                ):
                    expansion_direction = 1
                    next_index = max(evaluated_indexes) + 1
                else:
                    next_index = -1
                expanded_indexes: list[int] = []
                while expansion_direction and 0 <= next_index < len(candidate_settings) and len(evaluated_full_indexes) < full_search_limit:
                    if next_index in evaluated_indexes:
                        next_index += expansion_direction
                        continue
                    record = evaluate_full_index(next_index, "directed-page-count-expansion")
                    expanded_indexes.append(next_index)
                    evaluated_indexes.add(next_index)
                    if record and bool(record.get("page_fidelity_exact")):
                        calibration["early_stop"] = {
                            "reason": "dense-page-probe-directed-full-expansion-exact",
                            "candidate_index": next_index,
                            "body_size_pt": record.get("body_size_pt"),
                            "gap_scale": record.get("gap_scale"),
                            "line_spacing_multiple": record.get("line_spacing_multiple"),
                            "full_candidate_count": len(calibration["candidates"]),
                        }
                        break
                    if record:
                        output_pages = int((record.get("comparison") or {}).get("output_page_count") or 0)
                        if expansion_direction < 0 and output_pages < source_page_count:
                            break
                        if expansion_direction > 0 and output_pages > source_page_count:
                            break
                    next_index += expansion_direction
                calibration["full_search_expansion"] = {
                    "policy": "continue-full-search-when-neighbourhood-does-not-bracket-page-count",
                    "direction": expansion_direction,
                    "expanded_indexes": expanded_indexes,
                    "compact_floor_reached": bool(expansion_direction < 0 and min(evaluated_indexes) <= 0),
                    "loose_ceiling_reached": bool(expansion_direction > 0 and max(evaluated_indexes) >= len(candidate_settings) - 1),
                    "candidate_floor": {
                        "candidate_index": 0,
                        "body_size_pt": candidate_settings[0][0],
                        "gap_scale": candidate_settings[0][1],
                        "line_spacing_multiple": candidate_settings[0][2],
                    },
                    "candidate_ceiling": {
                        "candidate_index": len(candidate_settings) - 1,
                        "body_size_pt": candidate_settings[-1][0],
                        "gap_scale": candidate_settings[-1][1],
                        "line_spacing_multiple": candidate_settings[-1][2],
                    },
                }
            if (
                not any(bool(record.get("page_fidelity_exact")) for record in calibration.get("candidates", []))
                and not compact_floor_blocks_typography
            ):
                lanes = _candidate_lanes(candidate_settings)
                lane_records: list[dict[str, Any]] = []
                lane_search_limit = page_count_probe_search_limit
                for lane in lanes:
                    if renderer_unavailable_error or len(evaluated_full_indexes) >= lane_search_limit:
                        break
                    indexes = list(lane["indexes"])
                    low_pos = 0
                    high_pos = len(indexes) - 1
                    evaluated_lane_positions: set[int] = set()
                    lane_log = {
                        "gap_scale": lane["gap_scale"],
                        "line_spacing_multiple": lane["line_spacing_multiple"],
                        "indexes": indexes,
                        "evaluated_indexes": [],
                        "compact_seed_index": indexes[0] if indexes else None,
                        "compact_seed_pages": None,
                        "result": "not-started",
                    }
                    if indexes and len(evaluated_full_indexes) < lane_search_limit:
                        seed_index = indexes[0]
                        evaluated_lane_positions.add(0)
                        lane_log["evaluated_indexes"].append(seed_index)
                        seed_record = evaluate_full_index(seed_index, "compact-lane-seed")
                        if seed_record and bool(seed_record.get("page_fidelity_exact")):
                            calibration["early_stop"] = {
                                "reason": "compact-lane-seed-exact",
                                "candidate_index": seed_index,
                                "body_size_pt": seed_record.get("body_size_pt"),
                                "gap_scale": seed_record.get("gap_scale"),
                                "line_spacing_multiple": seed_record.get("line_spacing_multiple"),
                                "full_candidate_count": len(calibration["candidates"]),
                            }
                            lane_log["result"] = "exact"
                        elif seed_record:
                            seed_comparison = seed_record.get("comparison") or {}
                            seed_pages = int(seed_comparison.get("output_page_count") or 0)
                            lane_log["compact_seed_pages"] = seed_pages
                            if seed_pages > source_page_count:
                                lane_log["result"] = "rejected-compact-seed-already-too-many-pages"
                            elif seed_pages == source_page_count:
                                lane_log["result"] = "compact-seed-exact-page-count-boundary-or-objective-failed"
                                low_pos = 1
                            else:
                                lane_log["result"] = "compact-seed-under-target-search-upward"
                                low_pos = 1
                        else:
                            lane_log["result"] = "compact-seed-render-failed"
                    while (
                        not renderer_unavailable_error
                        and low_pos <= high_pos
                        and len(evaluated_full_indexes) < lane_search_limit
                        and lane_log.get("result") not in {
                            "exact",
                            "rejected-compact-seed-already-too-many-pages",
                            "compact-seed-render-failed",
                        }
                    ):
                        mid_pos = (low_pos + high_pos) // 2
                        if mid_pos in evaluated_lane_positions:
                            break
                        evaluated_lane_positions.add(mid_pos)
                        candidate_index = indexes[mid_pos]
                        lane_log["evaluated_indexes"].append(candidate_index)
                        record = evaluate_full_index(candidate_index, "monotonic-lane-bisection")
                        if record is None:
                            break
                        if bool(record.get("page_fidelity_exact")):
                            calibration["early_stop"] = {
                                "reason": "monotonic-lane-bisection-exact",
                                "candidate_index": candidate_index,
                                "body_size_pt": record.get("body_size_pt"),
                                "gap_scale": record.get("gap_scale"),
                                "line_spacing_multiple": record.get("line_spacing_multiple"),
                                "full_candidate_count": len(calibration["candidates"]),
                            }
                            lane_log["result"] = "exact"
                            break
                        comparison = record.get("comparison") or {}
                        output_pages = int(comparison.get("output_page_count") or 0)
                        if output_pages > source_page_count:
                            high_pos = mid_pos - 1
                        elif output_pages < source_page_count:
                            low_pos = mid_pos + 1
                        else:
                            lane_log["result"] = "exact-page-count-boundary-or-objective-failed"
                            for neighbour_pos in (mid_pos - 1, mid_pos + 1):
                                if (
                                    0 <= neighbour_pos < len(indexes)
                                    and neighbour_pos not in evaluated_lane_positions
                                    and len(evaluated_full_indexes) < lane_search_limit
                                ):
                                    neighbour = evaluate_full_index(indexes[neighbour_pos], "monotonic-lane-neighbour")
                                    lane_log["evaluated_indexes"].append(indexes[neighbour_pos])
                                    if neighbour and bool(neighbour.get("page_fidelity_exact")):
                                        calibration["early_stop"] = {
                                            "reason": "monotonic-lane-neighbour-exact",
                                            "candidate_index": indexes[neighbour_pos],
                                            "body_size_pt": neighbour.get("body_size_pt"),
                                            "gap_scale": neighbour.get("gap_scale"),
                                            "line_spacing_multiple": neighbour.get("line_spacing_multiple"),
                                            "full_candidate_count": len(calibration["candidates"]),
                                        }
                                        lane_log["result"] = "exact"
                                        break
                            break
                    else:
                        if lane_log["result"] == "not-started":
                            lane_log["result"] = "exhausted"
                    lane_records.append(lane_log)
                    if any(bool(record.get("page_fidelity_exact")) for record in calibration.get("candidates", [])):
                        break
                calibration["monotonic_lane_search"] = {
                    "policy": "same-gap-same-line-bisection-over-font-size",
                    "lane_count": len(lanes),
                    "full_search_limit": lane_search_limit,
                    "page_count_probe_only_budget": True,
                    "lanes": lane_records,
                }
            calibration["adaptive_search"] = {
                "candidate_count": len(calibration["candidates"]),
                "visited_indexes": sorted(visited),
                "policy": "adaptive-probe-window-then-monotonic-lane-bisection",
                "probe_cost_policy": "probe candidates are Word page-count only",
                "full_search_limit": full_search_limit,
                "page_count_probe_search_limit": page_count_probe_search_limit,
            }
        else:
            calibration["search_strategy"] = "sequential"
            for candidate_index in range(len(candidate_settings)):
                evaluate_candidate(candidate_index)

        if best is not None:
            exact_page_count = bool(best.get("page_fidelity_exact"))
            selected_page_count_exact = bool(best.get("page_count_exact"))
            selected_page_boundary_pass = bool(best.get("page_boundary_pass"))
            final_build_report = best["build_report"]
            calibration.update({
                "status": "completed" if exact_page_count else (
                    (
                        "failed-page-boundary-fidelity"
                        if args.strict_page_count and selected_page_count_exact and not selected_page_boundary_pass
                        else "failed-no-exact-page-count"
                    )
                    if args.strict_page_count else "page-count-mismatch"
                ),
                "renderer": renderer_name,
                "strict_page_count": bool(args.strict_page_count),
                "selected_body_size_pt": best["body_size_pt"],
                "selected_gap_scale": best["gap_scale"],
                "selected_line_spacing_multiple": best.get("line_spacing_multiple"),
                "selected_page_count_exact": selected_page_count_exact,
                "selected_page_boundary_pass": selected_page_boundary_pass,
                "selected_page_fidelity_exact": exact_page_count,
                "selected_comparison": best["comparison"],
                "selected_build_report": final_build_report,
            })
            if exact_page_count:
                shutil.copy2(best["docx"], final_docx)
                shutil.copy2(best["pdf"], final_pdf)
                logging.info(
                    "Επιλέχθηκε body size %.2f pt / gap %.2f / line %.2f μέσω %s με ακριβές πλήθος %s σελίδων",
                    best["body_size_pt"],
                    best["gap_scale"],
                    float(best.get("line_spacing_multiple") or 0.0),
                    renderer_name,
                    source_page_count,
                )
                compare_pdf_to_source(
                    args.pdf,
                    pages,
                    final_pdf,
                    args.output / "visual_compare",
                    dpi=140,
                    write_images=True,
                    page_structure=page_structure,
                )
            elif args.strict_page_count:
                strict_failure_code = 23
                selected_comparison = best.get("comparison") or {}
                boundary_failure = (
                    bool(best.get("page_count_exact"))
                    and not bool(best.get("page_boundary_pass", True))
                )
                failed_candidates_dir = args.output / "failed_candidates"
                failed_candidates_dir.mkdir(parents=True, exist_ok=True)
                failed_docx = failed_candidates_dir / "selected_failed_candidate.docx"
                failed_pdf = failed_candidates_dir / "selected_failed_candidate.pdf"
                for source_path, target_path in (
                    (Path(str(best.get("docx") or "")), failed_docx),
                    (Path(str(best.get("pdf") or "")), failed_pdf),
                ):
                    if source_path.exists():
                        shutil.copy2(source_path, target_path)
                calibration["selected_failed_candidate_artifacts"] = {
                    "docx": str(failed_docx) if failed_docx.exists() else None,
                    "pdf": str(failed_pdf) if failed_pdf.exists() else None,
                    "reason": "page-boundary-fidelity" if boundary_failure else "page-count",
                }
                early_stop = calibration.get("early_stop") or {}
                compact_stop = early_stop.get("reason") == "compact-floor-over-target-before-probe"
                message = (
                    (
                        f"FAIL: exact {source_page_count}-page candidate failed page-boundary fidelity. "
                        f"Boundary failures: {selected_comparison.get('page_boundary_failure_count')}; "
                        f"average text-end delta: {selected_comparison.get('average_text_end_delta_pt')} pt. "
                    )
                    if boundary_failure else
                    (
                        f"FAIL: compact seed interval search stopped at "
                        f"{selected_comparison.get('output_page_count')}/{source_page_count} pages. "
                        f"Candidate {early_stop.get('candidate_index')} was already the compact floor; "
                        f"no larger typography/probe/full escalation was allowed. "
                    )
                    if compact_stop else
                    (
                        f"FAIL: no exact {source_page_count}-page candidate. "
                        f"Closest candidate produced {selected_comparison.get('output_page_count')} pages. "
                    )
                ) + (
                    "No final DOCX/PDF was published."
                )
                logging.error(message)
                (args.output / "FAILED_NO_EXACT_PAGE_COUNT.txt").write_text(message + "\n", encoding="utf-8")
            else:
                shutil.copy2(best["docx"], final_docx)
                shutil.copy2(best["pdf"], final_pdf)
                logging.warning(
                    "Κανένας candidate δεν έδωσε %s σελίδες. Επιλέχθηκε ο πλησιέστερος: %.2f pt / gap %.2f / line %.2f, %s σελίδες",
                    source_page_count,
                    best["body_size_pt"],
                    best["gap_scale"],
                    float(best.get("line_spacing_multiple") or 0.0),
                    best["comparison"]["output_page_count"],
                )
                compare_pdf_to_source(
                    args.pdf,
                    pages,
                    final_pdf,
                    args.output / "visual_compare",
                    dpi=140,
                    write_images=True,
                    page_structure=page_structure,
                )
        elif args.strict_page_count:
            strict_failure_code = 24
            calibration.update({
                "status": "failed-no-rendered-candidate",
                "strict_page_count": True,
                "selected_page_count_exact": False,
            })
            (args.output / "FAILED_NO_RENDERED_CANDIDATE.txt").write_text(
                "FAIL: no rendered calibration candidate was available. No final DOCX/PDF was published.\n",
                encoding="utf-8",
            )

    if not final_docx.exists() and not strict_failure_code:
        if args.strict_page_count:
            strict_failure_code = 25
            calibration.update({
                "status": "failed-final-output-missing",
                "strict_page_count": True,
                "selected_page_count_exact": False,
            })
        else:
            logging.info("Δημιουργία native page-structure draft χωρίς render calibration")
            final_build_report = build_native_page_document(
                pdf_analysis,
                page_structure,
                alignment,
                docx_analysis,
                style_profile,
                final_docx,
                body_size_override=inferred_body,
                gap_scale=0.72,
                body_line_spacing_multiple=1.05,
                docx_donor_map=docx_donor_map,
                page_layout_spine=page_layout_spine,
                flow_mode="free",
            )
            calibration["status"] = "renderer-unavailable" if not args.no_render else "disabled-by-flag"

    write_json(analysis_dir / "build_report.json", final_build_report or {})
    write_json(analysis_dir / "calibration.json", calibration)
    content_audit = {}
    if final_docx.exists() and final_pdf.exists():
        logging.info("Content audit PDF ↔ output DOCX/PDF")
        content_audit = audit_content(
            args.pdf,
            pages,
            final_docx,
            final_pdf,
            final_build_report or {},
            markdown_element_map or None,
            markdown_pdf_spine or None,
        )
        write_json(analysis_dir / "content_audit.json", content_audit)
    fidelity_fallback_report = build_fidelity_fallback_report(
        page_structure,
        final_build_report or {},
        content_audit,
        alignment,
        markdown_pdf_spine,
    )
    write_json(analysis_dir / "fidelity_fallback_report.json", fidelity_fallback_report)
    conversion_spine = {}
    if markdown_element_map:
        logging.info("Conversion spine Markdown → PDF → DOCX donor → output")
        conversion_spine = build_conversion_spine(
            markdown_element_map,
            markdown_pdf_spine,
            page_structure,
            final_build_report or {},
            content_audit,
            fidelity_fallback_report,
            docx_donor_map,
        )
        write_json(analysis_dir / "conversion_spine.json", conversion_spine)
    architecture_benchmark = build_architecture_benchmark(
        pdf_path=str(args.pdf.resolve()),
        docx_path=str(args.docx.resolve()),
        pages=pages,
        total_seconds=time.perf_counter() - started_at,
        docx_donor_map=docx_donor_map,
        conversion_spine=conversion_spine,
        page_layout_spine=page_layout_spine,
        fidelity_report=fidelity_fallback_report,
        content_audit=content_audit,
        build_report=final_build_report or {},
    )
    write_json(analysis_dir / "architecture_benchmark.json", architecture_benchmark)
    mapping_fidelity = build_mapping_fidelity(
        markdown_pdf_spine=markdown_pdf_spine or None,
        page_layout_spine=page_layout_spine or None,
        conversion_spine=conversion_spine or None,
    )
    write_json(analysis_dir / "mapping_fidelity.json", mapping_fidelity)
    architecture_guard = build_architecture_guard(
        markdown_element_map=markdown_element_map or None,
        markdown_pdf_spine=markdown_pdf_spine or None,
        docx_donor_map=docx_donor_map or None,
        page_layout_spine=page_layout_spine or None,
        conversion_spine=conversion_spine or None,
        fidelity_report=fidelity_fallback_report or None,
        build_report=final_build_report or {},
    )
    write_json(analysis_dir / "architecture_guard.json", architecture_guard)

    logging.info("Δημιουργία HTML report v0.8.3 strict")
    report_path = build_html_report(
        report_dir,
        work_dir,
        pdf_analysis,
        docx_analysis,
        alignment,
        style_profile,
        classification_summary,
        page_structure=page_structure,
        calibration=calibration,
        content_audit=content_audit,
        fidelity_fallback_report=fidelity_fallback_report,
        markdown_pdf_spine=markdown_pdf_spine,
        conversion_spine=conversion_spine,
        docx_donor_map=docx_donor_map,
        architecture_benchmark=architecture_benchmark,
        architecture_guard=architecture_guard,
        mapping_fidelity=mapping_fidelity,
        page_layout_spine=page_layout_spine,
    )

    if strict_failure_code:
        write_progress(
            "Αποτυχία strict gate",
            status="failed",
            strictFailureCode=strict_failure_code,
            calibrationStatus=calibration.get("status"),
            selectedComparison=calibration.get("selected_comparison"),
        )
        print("\nΑΠΟΤΥΧΙΑ STRICT GATE")
        print(f"Report: {report_path}")
        print(f"Log:    {logs / 'run.log'}")
        print("Δεν δημοσιεύτηκε τελικό DOCX/PDF.")
        return strict_failure_code

    write_progress(
        "Ολοκληρώθηκε",
        status="completed",
        calibrationStatus=calibration.get("status"),
        selectedComparison=calibration.get("selected_comparison"),
        docx=str(final_docx) if final_docx.exists() else None,
    )
    print("\nΟΛΟΚΛΗΡΩΘΗΚΕ")
    print(f"Report: {report_path}")
    if final_docx.exists():
        print(f"DOCX:   {final_docx}")
    if final_pdf.exists():
        print(f"PDF:    {final_pdf}")
    print(f"Log:    {logs / 'run.log'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
