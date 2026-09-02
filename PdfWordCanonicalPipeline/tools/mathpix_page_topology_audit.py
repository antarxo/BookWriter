from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pdf_word_reconstructor.common import normalize_text, write_json
from pdf_word_reconstructor.mathpix_lines_input import (
    build_mathpix_line_layout_map,
    find_mathpix_lines_json,
    load_mathpix_lines,
)
from pdf_word_reconstructor.mathpix_margin_model import (
    apply_mathpix_margin_model,
    build_mathpix_margin_model,
)
from pdf_word_reconstructor.mathpix_page_geometry_adapter import (
    apply_mathpix_page_geometry,
    build_mathpix_page_geometry_evidence,
)
from pdf_word_reconstructor.mathpix_reserved_page_zones import build_reserved_page_zone_profile
from pdf_word_reconstructor.package_cli import _extract_package, _find_package_pdf
from pdf_word_reconstructor.page_furniture import analyze_page_furniture
from pdf_word_reconstructor.page_structure import build_page_structure
from pdf_word_reconstructor.pdf_analyzer import analyze_pdf
from pdf_word_reconstructor.region_classifier import classify_pdf_regions
from pdf_word_reconstructor.style_profile import build_style_profile


VERSION = "mathpix-page-topology-audit-0.4"

_STRUCTURAL_LINE_TYPES = {
    "page_info",
    "column",
    "table_row",
    "table_column",
    "table_of_contents_container",
    "table_of_contents_row",
    "table_of_contents_number",
}
_ASSET_RE = re.compile(r"-(?P<page>\d{3})_(?P<height>\d+)_(?P<width>\d+)_(?P<y>\d+)_(?P<x>\d+)\.(?:jpe?g|png)$", re.IGNORECASE)


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


def _line_text(item: dict[str, Any]) -> str:
    for key in ("text_display", "text", "conversion_output"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _lines_page_text(raw_page: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in raw_page.get("lines", []) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") in _STRUCTURAL_LINE_TYPES:
            continue
        text = _line_text(item)
        if text:
            parts.append(text)
    return " ".join(parts)


def _mapping_content_matrix(
    pdf_path: Path,
    raw_by_physical: dict[int, dict[str, Any]],
) -> tuple[list[str], dict[int, list[float]]]:
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # type: ignore

    with fitz.open(pdf_path) as pdf:
        pdf_texts = [normalize_text(str(pdf[index].get_text("text") or "")) for index in range(pdf.page_count)]

    matrix: dict[int, list[float]] = {}
    for physical, raw_page in sorted(raw_by_physical.items()):
        line_text = normalize_text(_lines_page_text(raw_page))
        matrix[physical] = [
            round(float(fuzz.token_set_ratio(line_text, pdf_text)), 2) if line_text and pdf_text else 0.0
            for pdf_text in pdf_texts
        ]
    return pdf_texts, matrix


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_visual_assets(package_dir: Path) -> dict[int, list[dict[str, Any]]]:
    by_page: dict[int, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for path in package_dir.rglob("*"):
        if not path.is_file():
            continue
        match = _ASSET_RE.search(path.name)
        if match is None or path.name in seen:
            continue
        seen.add(path.name)
        page = int(match.group("page"))
        height = int(match.group("height"))
        width = int(match.group("width"))
        y = int(match.group("y"))
        x = int(match.group("x"))
        by_page.setdefault(page, []).append({
            "path": path,
            "filename": path.name,
            "bboxPx": [x, y, x + width, y + height],
            "widthPx": width,
            "heightPx": height,
            "areaPx": width * height,
        })
    for page, rows in by_page.items():
        rows.sort(key=lambda row: (-int(row["areaPx"]), str(row["filename"])))
        by_page[page] = rows[:3]
    return by_page


def _visual_similarity(asset_path: Path, page_image: Any, bbox_px: list[int]) -> float:
    try:
        import numpy as np
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Visual page corroboration requires Pillow and numpy") from exc

    x0, y0, x1, y1 = [int(v) for v in bbox_px]
    x0 = max(0, min(x0, page_image.width - 1))
    y0 = max(0, min(y0, page_image.height - 1))
    x1 = max(x0 + 1, min(x1, page_image.width))
    y1 = max(y0 + 1, min(y1, page_image.height))
    crop = page_image.crop((x0, y0, x1, y1))
    with Image.open(asset_path) as source:
        asset = source.convert("RGB")
    target_size = (128, 128)
    asset_gray = ImageOps.grayscale(asset).resize(target_size, Image.Resampling.LANCZOS)
    crop_gray = ImageOps.grayscale(crop).resize(target_size, Image.Resampling.LANCZOS)
    a = np.asarray(asset_gray, dtype=np.float32) / 255.0
    b = np.asarray(crop_gray, dtype=np.float32) / 255.0
    a_std = float(a.std())
    b_std = float(b.std())
    if a_std < 1e-6 or b_std < 1e-6:
        correlation = 0.0
    else:
        correlation = float(np.mean(((a - float(a.mean())) / a_std) * ((b - float(b.mean())) / b_std)))
        correlation = max(-1.0, min(1.0, correlation))
    mad_score = 1.0 - float(np.mean(np.abs(a - b)))
    score = 0.75 * ((correlation + 1.0) / 2.0) + 0.25 * max(0.0, min(1.0, mad_score))
    return round(max(0.0, min(1.0, score)), 4)


def _visual_asset_corroboration(
    pdf_path: Path,
    package_dir: Path,
    raw_by_physical: dict[int, dict[str, Any]],
    mapping: dict[int, int],
) -> list[dict[str, Any]]:
    try:
        import pymupdf as fitz
        from PIL import Image
    except ImportError:
        import fitz  # type: ignore
        from PIL import Image

    assets_by_page = _collect_visual_assets(package_dir)
    rendered: dict[tuple[int, int, int], Any] = {}
    checks: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as pdf:
        for physical, mapped_ordinal in sorted(mapping.items()):
            raw_page = raw_by_physical.get(physical) or {}
            page_width_px = int(raw_page.get("page_width") or 0)
            page_height_px = int(raw_page.get("page_height") or 0)
            assets = list(assets_by_page.get(physical) or [])
            if page_width_px <= 0 or page_height_px <= 0 or not assets:
                checks.append({
                    "physicalPage": physical,
                    "pdfOrdinalPage": mapped_ordinal,
                    "status": "unavailable-no-visual-assets" if not assets else "unavailable-page-pixel-size",
                    "assetCount": len(assets),
                })
                continue

            ordinal_scores: dict[int, list[float]] = {}
            for ordinal in range(1, pdf.page_count + 1):
                key = (ordinal, page_width_px, page_height_px)
                page_image = rendered.get(key)
                if page_image is None:
                    page = pdf[ordinal - 1]
                    matrix = fitz.Matrix(
                        page_width_px / max(1.0, float(page.rect.width)),
                        page_height_px / max(1.0, float(page.rect.height)),
                    )
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    mode = "RGB" if pix.n == 3 else "RGBA"
                    page_image = Image.frombytes(mode, (pix.width, pix.height), pix.samples).convert("RGB")
                    if page_image.size != (page_width_px, page_height_px):
                        page_image = page_image.resize((page_width_px, page_height_px), Image.Resampling.LANCZOS)
                    rendered[key] = page_image
                ordinal_scores[ordinal] = [
                    _visual_similarity(row["path"], page_image, row["bboxPx"])
                    for row in assets
                ]

            aggregate = {
                ordinal: round(float(median(scores)), 4) if scores else 0.0
                for ordinal, scores in ordinal_scores.items()
            }
            ranked = sorted(aggregate.items(), key=lambda pair: (-pair[1], pair[0]))
            best_ordinal, best_score = ranked[0]
            second_score = ranked[1][1] if len(ranked) > 1 else 0.0
            mapped_score = aggregate.get(mapped_ordinal, 0.0)
            separation = best_score - second_score
            if best_ordinal == mapped_ordinal and best_score >= 0.78 and separation >= 0.06:
                status = "confirmed"
            elif best_ordinal != mapped_ordinal and best_score >= 0.78 and (best_score - mapped_score) >= 0.06:
                status = "conflict"
            else:
                status = "inconclusive"
            checks.append({
                "physicalPage": physical,
                "pdfOrdinalPage": mapped_ordinal,
                "status": status,
                "assetCount": len(assets),
                "assets": [
                    {"filename": row["filename"], "bboxPx": row["bboxPx"], "areaPx": row["areaPx"]}
                    for row in assets
                ],
                "mappedScore": round(mapped_score, 4),
                "bestPdfOrdinal": best_ordinal,
                "bestScore": round(best_score, 4),
                "secondBestScore": round(second_score, 4),
                "bestSeparation": round(separation, 4),
                "allPdfOrdinalScores": {str(k): v for k, v in aggregate.items()},
                "perAssetScores": {
                    str(ordinal): [round(v, 4) for v in scores]
                    for ordinal, scores in ordinal_scores.items()
                },
            })
    return checks


def _resolve_page_mapping(pdf_path: Path, lines_data: dict[str, Any], package_dir: Path) -> dict[str, Any]:
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # type: ignore

    raw_by_physical = {
        int(page.get("page") or 0): page
        for page in lines_data.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    line_pages = sorted(raw_by_physical)
    if not line_pages:
        raise RuntimeError("Mathpix Lines contains no physical page numbers")

    with fitz.open(pdf_path) as pdf:
        pdf_count = int(pdf.page_count)
        pdf_aspects = [
            float(pdf[index].rect.width) / max(1.0, float(pdf[index].rect.height))
            for index in range(pdf_count)
        ]

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

    aspect_checks: list[dict[str, Any]] = []
    for physical, ordinal in sorted(mapping.items()):
        lines_aspect = _page_aspect_from_lines(raw_by_physical.get(physical) or {})
        pdf_aspect = pdf_aspects[ordinal - 1] if 1 <= ordinal <= len(pdf_aspects) else None
        if lines_aspect is None or pdf_aspect is None:
            status, delta = "unavailable", None
        else:
            delta = abs(lines_aspect - pdf_aspect) / max(lines_aspect, pdf_aspect, 1e-9)
            status = "consistent" if delta <= 0.03 else "conflict"
        aspect_checks.append({
            "physicalPage": physical,
            "pdfOrdinalPage": ordinal,
            "linesAspectRatio": round(lines_aspect, 6) if lines_aspect is not None else None,
            "pdfAspectRatio": round(pdf_aspect, 6) if pdf_aspect is not None else None,
            "relativeAspectDelta": round(delta, 6) if delta is not None else None,
            "status": status,
        })

    aspect_conflicts = [row for row in aspect_checks if row["status"] == "conflict"]
    if aspect_conflicts:
        raise RuntimeError(
            "PDF/Mathpix page mapping failed page-size corroboration: "
            + json.dumps(aspect_conflicts, ensure_ascii=False)
        )

    pdf_texts, content_matrix = _mapping_content_matrix(pdf_path, raw_by_physical)
    pdf_text_page_count = sum(1 for text in pdf_texts if text)
    text_layer_available = pdf_text_page_count > 0
    content_checks: list[dict[str, Any]] = []
    content_failures: list[dict[str, Any]] = []

    if text_layer_available:
        for physical, ordinal in sorted(mapping.items()):
            scores = list(content_matrix.get(physical) or [])
            mapped_score = scores[ordinal - 1] if 1 <= ordinal <= len(scores) else 0.0
            ranked = sorted(enumerate(scores, start=1), key=lambda pair: (-pair[1], pair[0]))
            best_ordinal, best_score = ranked[0] if ranked else (None, 0.0)
            second_score = ranked[1][1] if len(ranked) > 1 else 0.0
            mapped_is_best = best_ordinal == ordinal
            separation = mapped_score - second_score if mapped_is_best else mapped_score - best_score
            status = "confirmed" if mapped_score >= 70.0 and mapped_is_best else "conflict"
            row = {
                "physicalPage": physical,
                "pdfOrdinalPage": ordinal,
                "mappedScore": round(mapped_score, 2),
                "bestPdfOrdinal": best_ordinal,
                "bestScore": round(best_score, 2),
                "secondBestScore": round(second_score, 2),
                "mappedIsBest": mapped_is_best,
                "scoreSeparation": round(separation, 2),
                "status": status,
                "allPdfOrdinalScores": {str(i): score for i, score in enumerate(scores, start=1)},
            }
            content_checks.append(row)
            if status != "confirmed":
                content_failures.append(row)
    else:
        content_checks = [
            {
                "physicalPage": physical,
                "pdfOrdinalPage": ordinal,
                "status": "unavailable-no-pdf-text-layer",
                "mappedScore": None,
                "reason": "PyMuPDF extracted no text from any PDF page; no OCR fallback is permitted in this audit",
            }
            for physical, ordinal in sorted(mapping.items())
        ]

    if content_failures:
        raise RuntimeError(
            "PDF/Mathpix page mapping failed content corroboration: "
            + json.dumps(content_failures, ensure_ascii=False)
        )

    visual_checks = _visual_asset_corroboration(pdf_path, package_dir, raw_by_physical, mapping)
    visual_confirmed = [row for row in visual_checks if row.get("status") == "confirmed"]
    visual_conflicts = [row for row in visual_checks if row.get("status") == "conflict"]
    if text_layer_available:
        mapping_status = "resolved-text-content"
    elif visual_conflicts:
        mapping_status = "conflict-visual-corroboration"
    elif len(visual_confirmed) == len(line_pages):
        mapping_status = "resolved-visual-assets"
    elif visual_confirmed:
        mapping_status = "provisional-partially-visual-corroborated"
    else:
        mapping_status = "provisional-pending-geometry-corroboration"

    return {
        "version": VERSION,
        "status": mapping_status,
        "mode": mode,
        "pdfSha256": _sha256(pdf_path),
        "pdfPageCount": pdf_count,
        "linesPhysicalPages": line_pages,
        "physicalToPdfOrdinal": {str(k): v for k, v in sorted(mapping.items())},
        "pdfOrdinalToPhysical": {str(v): k for k, v in sorted(mapping.items())},
        "aspectRatioChecks": aspect_checks,
        "contentCorroboration": content_checks,
        "visualAssetCorroboration": visual_checks,
        "visualConfirmedPages": [int(row["physicalPage"]) for row in visual_confirmed],
        "visualConflictPages": [int(row["physicalPage"]) for row in visual_conflicts],
        "textLayerEvidence": {
            "available": text_layer_available,
            "pdfPagesWithExtractedText": pdf_text_page_count,
            "pdfPageCount": pdf_count,
            "policy": "absence of a PDF text layer is unavailable evidence, never a content conflict and never an OCR trigger",
        },
        "policy": (
            "candidate identity/subset mapping must pass page-size corroboration; text corroboration is required when a PDF text layer exists; "
            "otherwise packaged Mathpix crop assets are compared pixel-wise at their own normalized source coordinates against every PDF ordinal; "
            "visual conflicts keep mapping fail-closed for production"
        ),
    }


def _remap_pdf_analysis(pdf_analysis: dict[str, Any], ordinal_to_physical: dict[int, int]) -> dict[str, Any]:
    result = copy.deepcopy(pdf_analysis)
    for page in result.get("pages", []) or []:
        ordinal = int(page.get("page") or 0)
        physical = ordinal_to_physical.get(ordinal)
        if physical is None:
            raise RuntimeError(f"Analyzed PDF ordinal page {ordinal} has no resolved physical-page mapping")
        page["pdfOrdinalPage"] = ordinal
        page["page"] = physical
        page["pageMappingSource"] = "candidate-pdf-mathpix-page-map-pending-full-topology-audit"
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
        narrow = min(sidebars, key=lambda row: float(row.get("widthRatio") or 1.0))
        wide = max(sidebars, key=lambda row: float(row.get("widthRatio") or 0.0))
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
        "layoutMode": page.get("layout_mode"),
        "bodyBoxPt": page.get("body_box"),
        "marginsPt": page.get("margins"),
        "marginSource": page.get("margin_source"),
        "mainColumn": page.get("main_column"),
        "columnCount": len(page.get("columns", []) or []),
        "columns": page.get("columns"),
        "mathpixSidebars": page.get("mathpix_sidebars"),
        "mainFlowBox": page.get("main_flow_box"),
        "outerRailBox": page.get("outer_rail_box"),
        "outerRailSide": page.get("outer_rail_side"),
        "pageTopology": page.get("page_topology"),
        "header": page.get("header"),
        "footer": page.get("footer"),
        "mathpixMarginEvidence": page.get("mathpixMarginEvidence"),
    }


def _mirrored_margin_audit(margin_pages: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, float]]] = {"odd": [], "even": []}
    signs: dict[str, Counter[str]] = {"odd": Counter(), "even": Counter()}
    for row in margin_pages:
        page = int(row.get("page") or 0)
        observed = row.get("observedMarginsPt") or {}
        try:
            left = float(observed.get("left"))
            right = float(observed.get("right"))
        except (TypeError, ValueError):
            continue
        group = "odd" if page % 2 else "even"
        sign = "left-larger" if left > right + 3.0 else ("right-larger" if right > left + 3.0 else "balanced")
        signs[group][sign] += 1
        groups[group].append({"left": left, "right": right})
        rows.append({"page": page, "parityCandidate": group, "leftPt": round(left, 3), "rightPt": round(right, 3), "horizontalPattern": sign})

    profiles: dict[str, Any] = {}
    for group, values in groups.items():
        profiles[group] = {
            "pageCount": len(values),
            "medianLeftPt": round(median([v["left"] for v in values]), 3) if values else None,
            "medianRightPt": round(median([v["right"] for v in values]), 3) if values else None,
            "patternCounts": dict(sorted(signs[group].items())),
        }

    odd_left = signs["odd"]["left-larger"]
    odd_right = signs["odd"]["right-larger"]
    even_left = signs["even"]["left-larger"]
    even_right = signs["even"]["right-larger"]
    mirrored = (
        len(groups["odd"]) >= 2
        and len(groups["even"]) >= 2
        and ((odd_left > odd_right and even_right > even_left) or (odd_right > odd_left and even_left > even_right))
    )
    return {
        "status": "mirrored-horizontal-margin-pattern-supported" if mirrored else "not-yet-resolved",
        "profilesByPhysicalPageParityCandidate": profiles,
        "observations": rows,
        "innerOuterInterpretation": "deferred-until-page-family/recto-verso-contract-is-resolved",
        "policy": "detect mirrored page families before collapsing margins into one global left/right profile; parity is evidence only, not a renderer instruction",
    }


def _legacy_geometry_conflict(page: dict[str, Any], column_evidence: dict[str, Any]) -> dict[str, Any]:
    classification = str(column_evidence.get("classification") or "unresolved")
    legacy_columns = list(page.get("columns", []) or [])
    legacy_two = len(legacy_columns) == 2 or str(page.get("layout_mode") or "") == "two_columns"
    authorized = classification == "true-two-column-page" and str(column_evidence.get("confidence") or "") == "high"
    conflict = legacy_two and not authorized
    return {
        "status": "conflict" if conflict else "consistent-or-not-applicable",
        "legacyTwoColumn": legacy_two,
        "matureGeometryClassification": classification,
        "matureGeometryConfidence": column_evidence.get("confidence"),
        "trueTwoColumnAuthorized": authorized,
        "policy": "legacy/PDF/Lines-first columns may not reach Word unless mature page-geometry evidence explicitly authorizes true-two-column-page/high",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed diagnostic audit of PDF-global topology against Mathpix Lines; Word renderer is never invoked."
    )
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--pages", default=None, help="Physical Mathpix pages, e.g. 17-22. Default: all Lines pages")
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

    print("[2/6] Resolve/corroborate PDF ordinal <-> Mathpix physical pages [FAIL-CLOSED; NO OCR; PACKAGE-INTERNAL VISUAL ASSETS]")
    page_mapping = _resolve_page_mapping(pdf_path, lines_data, package_dir)
    write_json(output / "PAGE_MAPPING_AUDIT.json", page_mapping)
    physical_pages = _parse_physical_pages(args.pages, page_mapping["linesPhysicalPages"])
    p2o = {int(k): int(v) for k, v in page_mapping["physicalToPdfOrdinal"].items()}
    o2p = {int(k): int(v) for k, v in page_mapping["pdfOrdinalToPhysical"].items()}
    pdf_ordinals = [p2o[page] for page in physical_pages]

    print(f"[3/6] Analyze PDF ordinals {pdf_ordinals} as candidate physical pages {physical_pages}")
    pdf_analysis_raw = analyze_pdf(pdf_path, pdf_ordinals, work_dir, dpi=args.dpi)
    pdf_analysis = _remap_pdf_analysis(pdf_analysis_raw, o2p)
    style_profile = build_style_profile(pdf_analysis)
    classify_pdf_regions(pdf_analysis, body_size=style_profile.get("inferred_body_font_size_pt"))
    write_json(output / "PDF_ANALYSIS_PHYSICAL_PAGES.json", pdf_analysis)
    write_json(output / "PDF_STYLE_PROFILE.json", style_profile)

    print("[4/6] Reuse mature furniture -> reserved zones -> margins -> page geometry")
    line_map = build_mathpix_line_layout_map(lines_path, pdf_analysis)
    geometry = build_mathpix_page_geometry_evidence(line_map)
    reserved = build_reserved_page_zone_profile(line_map, geometry)
    margins = build_mathpix_margin_model(line_map, geometry, reserved)
    pdf_furniture = analyze_page_furniture(pdf_analysis, line_map)
    mirror_audit = _mirrored_margin_audit(list(margins.get("pages", []) or []))
    write_json(output / "MATHPIX_PAGE_GEOMETRY_EVIDENCE.json", geometry)
    write_json(output / "RESERVED_PAGE_ZONES.json", reserved)
    write_json(output / "MARGIN_MODEL.json", margins)
    write_json(output / "MIRRORED_MARGIN_AUDIT.json", mirror_audit)
    write_json(output / "PDF_PAGE_FURNITURE.json", pdf_furniture)

    print("[5/6] Reuse mature page_structure; apply mature geometry/margin adapters on DIAGNOSTIC COPY only")
    page_structure_raw = build_page_structure(
        pdf_analysis,
        work_dir,
        asset_dir,
        reference_docx=None,
        external_asset_paths=[package_dir],
        equation_donor_path=None,
        mathpix_lines_path=lines_path,
        mathpix_lines_mode="lines_first",
    )
    page_structure_applied = copy.deepcopy(page_structure_raw)
    apply_mathpix_page_geometry(page_structure_applied, geometry)
    apply_mathpix_margin_model(page_structure_applied, margins)
    write_json(output / "PAGE_STRUCTURE_RAW_AUDIT.json", page_structure_raw)
    write_json(output / "PAGE_STRUCTURE_APPLIED_AUDIT.json", page_structure_applied)

    geom_by_page = _by_page(list(geometry.get("pages", []) or []))
    margin_by_page = _by_page(list(margins.get("pages", []) or []))
    raw_ps_by_page = _by_page(list(page_structure_raw.get("pages", []) or []))
    applied_ps_by_page = _by_page(list(page_structure_applied.get("pages", []) or []))
    pdf_by_page = _by_page(list(pdf_analysis.get("pages", []) or []))

    pages: list[dict[str, Any]] = []
    conflict_pages: list[int] = []
    refined_blocked_pages: list[int] = []
    for physical in physical_pages:
        first_geom = geom_by_page.get(physical) or {}
        margin = margin_by_page.get(physical) or {}
        raw_ps = raw_ps_by_page.get(physical) or {}
        applied_ps = applied_ps_by_page.get(physical) or {}
        pdf_page = pdf_by_page.get(physical) or {}
        refined_geom = raw_ps.get("mathpixGeometryEvidence") or first_geom
        column_evidence = refined_geom.get("columnEvidence") or {}
        classification = str(column_evidence.get("classification") or "unresolved")
        if classification.startswith("blocked"):
            refined_blocked_pages.append(physical)
        conflict = _legacy_geometry_conflict(raw_ps, column_evidence)
        if conflict["status"] == "conflict":
            conflict_pages.append(physical)
        pages.append({
            "physicalPage": physical,
            "pdfOrdinalPage": p2o[physical],
            "pageSizePt": [pdf_page.get("width_pt"), pdf_page.get("height_pt")],
            "pageParityCandidate": "recto" if physical % 2 else "verso",
            "headerFooter": {
                "headerStatus": ((refined_geom.get("headerFooterClassification") or {}).get("headerStatus")),
                "footerStatus": ((refined_geom.get("headerFooterClassification") or {}).get("footerStatus")),
                "headerBandPt": (refined_geom.get("headerBand") or {}).get("bbox"),
                "footerBandPt": (refined_geom.get("footerBand") or {}).get("bbox"),
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
            "initialMathpixColumnEvidence": _column_summary((first_geom.get("columnEvidence") or {})),
            "refinedProductionColumnEvidence": _column_summary(column_evidence),
            "productionTopologyBoxes": refined_geom.get("topologyBoxes"),
            "legacyGeometryConflict": conflict,
            "rawMaturePageStructure": _compact_page_structure(raw_ps),
            "afterExistingMatureAdaptersDiagnosticOnly": _compact_page_structure(applied_ps),
            "wordRealization": None,
        })

    audit = {
        "version": VERSION,
        "status": "diagnostic-only",
        "sourcePackage": str(package_zip),
        "sourcePdf": str(pdf_path),
        "sourcePdfSha256": page_mapping.get("pdfSha256"),
        "sourceLines": str(lines_path),
        "physicalPages": physical_pages,
        "pageMapping": page_mapping,
        "documentMarginProfile": margins.get("documentMarginProfile"),
        "mirroredMarginAudit": mirror_audit,
        "legacyGeometryConflictPages": conflict_pages,
        "refinedBlockedPages": refined_blocked_pages,
        "pages": pages,
        "policy": {
            "pdfGlobalTopology": "must participate before Word layout classification",
            "mathpixLines": "geometry/hierarchy witness; column objects are never Word columns by themselves",
            "matureCodeReuse": "page_furniture + reserved_page_zones + margin_model + page_geometry_adapter + page_structure",
            "productionPageStructureModified": False,
            "renderer": "OFF",
            "pageSpecificHardcoding": "FORBIDDEN",
            "ambiguousEvidence": "FAIL-CLOSED",
            "pdfTextLayer": "optional witness only; absence never becomes a conflict and never triggers OCR",
            "visualMapping": "package-internal Mathpix crop assets are compared against every ordinal page of the package PDF at their own source coordinates",
        },
    }
    write_json(output / "PAGE_TOPOLOGY_AUDIT.json", audit)

    print("[6/6] Audit complete; production page_structure and Word renderer were not modified/invoked")
    print(json.dumps({
        "status": audit["status"],
        "version": VERSION,
        "physicalPages": physical_pages,
        "pageMappingMode": page_mapping["mode"],
        "pageMappingStatus": page_mapping["status"],
        "sourcePdfSha256": page_mapping.get("pdfSha256"),
        "pdfTextLayerAvailable": bool((page_mapping.get("textLayerEvidence") or {}).get("available")),
        "contentMappingConfirmedPages": sum(1 for row in page_mapping.get("contentCorroboration", []) if row.get("status") == "confirmed"),
        "visualMappingConfirmedPages": page_mapping.get("visualConfirmedPages"),
        "visualMappingConflictPages": page_mapping.get("visualConflictPages"),
        "mirroredMarginStatus": mirror_audit.get("status"),
        "legacyGeometryConflictPages": conflict_pages,
        "refinedBlockedPages": refined_blocked_pages,
        "output": str(output / "PAGE_TOPOLOGY_AUDIT.json"),
        "renderer": "OFF",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
