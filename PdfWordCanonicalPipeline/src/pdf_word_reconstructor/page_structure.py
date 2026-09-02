from __future__ import annotations

from pathlib import Path
from typing import Any

from .asset_resolver import materialize_asset
from .donorless_asset_catalog import build_external_asset_catalog
from . import page_structure_legacy as _legacy
from .page_structure_legacy import *  # noqa: F401,F403
from .page_structure_legacy import build_page_structure as _build_legacy
from .mathpix_lines_input import build_mathpix_line_layout_map, summarize_mathpix_lines
from .mathpix_package_input import build_mathpix_package_map
from .mathpix_package_enrichment import enrich_with_mathpix_package
from .mathpix_margin_model import apply_mathpix_margin_model, build_mathpix_margin_model
from .mathpix_page_geometry_adapter import apply_mathpix_page_geometry, build_mathpix_page_geometry_evidence
from .mathpix_reserved_page_zones import build_reserved_page_zone_profile
from .page_furniture import analyze_page_furniture
from .page_text_style_map import enrich_page_text_styles


VERSION = "page-structure-frame-evidence-0.7"


def _box(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _line_box(record: dict[str, Any]) -> list[float] | None:
    bbox = record.get("bbox_pt") if isinstance(record.get("bbox_pt"), dict) else {}
    if not bbox:
        return None
    try:
        return _box([bbox.get("x0"), bbox.get("y0"), bbox.get("x1"), bbox.get("y1")])
    except (TypeError, ValueError):
        return None


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: list[float], b: list[float]) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _contains(outer: list[float], inner: list[float], tolerance: float = 2.5) -> bool:
    return (
        outer[0] <= inner[0] + tolerance
        and outer[1] <= inner[1] + tolerance
        and outer[2] >= inner[2] - tolerance
        and outer[3] >= inner[3] - tolerance
    )


def _rect_primitive(drawing: dict[str, Any]) -> bool:
    return any(str(item.get("kind") or "") == "re" for item in drawing.get("items", []) or [])


def _candidate_score(callout_box: list[float], drawing: dict[str, Any]) -> tuple[float, dict[str, Any]] | None:
    drawing_box = _box(drawing.get("bbox"))
    if drawing_box is None:
        return None
    callout_area = max(1.0, _area(callout_box))
    drawing_area = max(1.0, _area(drawing_box))
    overlap = _intersection(callout_box, drawing_box) / callout_area
    contains = _contains(drawing_box, callout_box)
    if overlap < 0.72 and not contains:
        return None
    area_ratio = drawing_area / callout_area
    if area_ratio > 8.0:
        return None
    edge_delta = (
        abs(callout_box[0] - drawing_box[0])
        + abs(callout_box[1] - drawing_box[1])
        + abs(callout_box[2] - drawing_box[2])
        + abs(callout_box[3] - drawing_box[3])
    )
    rectangular = _rect_primitive(drawing)
    styled = bool(drawing.get("strokeColor") or drawing.get("fillColor"))
    score = overlap * 55.0
    score += 24.0 if contains else 0.0
    score += 12.0 if rectangular else 0.0
    score += 6.0 if styled else 0.0
    score -= min(24.0, edge_delta * 0.35)
    score -= min(18.0, max(0.0, area_ratio - 1.0) * 4.0)
    return score, {
        "overlapRatio": round(overlap, 4),
        "areaRatio": round(area_ratio, 4),
        "edgeDeltaPt": round(edge_delta, 3),
        "containsTextBox": contains,
        "rectPrimitive": rectangular,
    }


def _frame_evidence(callout: dict[str, Any], drawings: list[dict[str, Any]]) -> dict[str, Any]:
    callout_box = _box(callout.get("bbox"))
    if callout_box is None:
        return {"status": "unresolved", "reason": "missing-callout-bbox", "source": "pdf-vector-drawings"}
    best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    for drawing in drawings:
        candidate = _candidate_score(callout_box, drawing)
        if candidate is None:
            continue
        score, evidence = candidate
        if best is None or score > best[0]:
            best = (score, drawing, evidence)
    if best is None:
        return {"status": "unresolved", "reason": "no-enclosing-pdf-vector-drawing", "source": "pdf-vector-drawings"}
    score, drawing, evidence = best
    if score >= 72.0:
        confidence = "high"
    elif score >= 52.0:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "status": "matched" if confidence in {"high", "medium"} else "review",
        "source": "pdf-vector-drawings",
        "drawingId": drawing.get("id"),
        "drawingBBoxPt": drawing.get("bbox"),
        "score": round(score, 2),
        "confidence": confidence,
        "stroke": {
            "color": drawing.get("strokeColor"),
            "widthPt": drawing.get("strokeWidthPt"),
            "opacity": drawing.get("strokeOpacity"),
            "dashes": drawing.get("dashes"),
            "status": "extracted" if drawing.get("strokeColor") is not None else "none-or-not-painted",
        },
        "fill": {
            "color": drawing.get("fillColor"),
            "opacity": drawing.get("fillOpacity"),
            "status": "extracted" if drawing.get("fillColor") is not None else "none-or-not-painted",
        },
        "path": {
            "type": drawing.get("type"),
            "closePath": drawing.get("closePath"),
            "rectPrimitive": evidence.get("rectPrimitive"),
        },
        "matchEvidence": evidence,
    }


def _reconcile_external_mathpix_assets(
    result: dict[str, Any],
    pdf_analysis: dict[str, Any],
    external_asset_paths: list[Path],
    work_dir: Path,
    asset_dir: Path,
) -> dict[str, Any]:
    catalog = build_external_asset_catalog(external_asset_paths)
    if not catalog:
        return {"catalogCount": 0, "positionedCatalogCount": 0, "reconciledGroupCount": 0}
    pdf_pages = {int(page.get("page") or 0): page for page in pdf_analysis.get("pages", []) or []}
    reconciled = 0
    for page in result.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        pdf_page = pdf_pages.get(page_no)
        if not pdf_page:
            continue
        groups = _legacy._merge_groups_by_mathpix_coordinates(
            list(page.get("visual_groups", []) or []),
            pdf_page,
            catalog,
        )
        main_column = page.get("main_column") or {}
        try:
            column_box = [
                float(main_column.get("x0")), 0.0,
                float(main_column.get("x1")), float(page.get("height_pt") or 842.0),
            ]
            column_width = max(1.0, column_box[2] - column_box[0])
        except (TypeError, ValueError):
            column_box = [0.0, 0.0, float(page.get("width_pt") or 595.0), float(page.get("height_pt") or 842.0)]
            column_width = max(1.0, column_box[2])

        for group in groups:
            position_match = group.get("mathpix_position_match")
            if not position_match:
                continue
            record = position_match.get("record")
            if record is None:
                continue
            materialized = materialize_asset(position_match, Path(asset_dir), str(group.get("id") or "mathpix-visual"))
            group["crop_path"] = str(materialized["raster"])
            if materialized.get("svg"):
                group["svg_path"] = str(materialized["svg"])
                group["native_vector"] = True
            group["asset_source"] = record.source
            group["asset_original"] = str(record.path)
            group["asset_match"] = {key: value for key, value in position_match.items() if key != "record"}
            overlap = _legacy._overlap_ratio(group["bbox"], column_box, denominator="a")
            group["placement"] = (
                "inline"
                if overlap >= 0.72 and float(group["bbox"][2]) - float(group["bbox"][0]) <= column_width * 1.08
                else "floating"
            )
            group["wrap"] = "square" if group["placement"] == "floating" and overlap >= 0.08 else "none"
            group.pop("mathpix_position_match", None)
            reconciled += 1

        page["visual_groups"] = groups
        flow = [
            item for item in (page.get("flow", []) or [])
            if not (item.get("type") == "visual" and item.get("semantic_type") == "figure")
        ]
        columns = list(page.get("columns", []) or [])
        for group in groups:
            if group.get("kind") != "figure" or group.get("placement") != "inline":
                continue
            item = {
                "id": "flow-" + str(group.get("id") or ""),
                "type": "visual",
                "semantic_type": "figure",
                "bbox": group.get("bbox"),
                "visual_group_id": group.get("id"),
                "crop_path": group.get("crop_path"),
                "svg_path": group.get("svg_path"),
                "native_vector": bool(group.get("native_vector")),
                "asset_source": group.get("asset_source"),
                "text": "",
            }
            if len(columns) == 2 and _box(group.get("bbox")):
                box = _box(group.get("bbox")) or [0, 0, 0, 0]
                gutter_center = (float(columns[0].get("x1") or 0.0) + float(columns[1].get("x0") or 0.0)) / 2.0
                item["column_index"] = 0 if (box[0] + box[2]) / 2.0 < gutter_center else 1
                item["spanning"] = False
            flow.append(item)
        flow.sort(key=lambda item: (
            2 if item.get("column_index") is None else int(item.get("column_index") or 0),
            float((item.get("bbox") or [0, 0, 0, 0])[1]),
            float((item.get("bbox") or [0, 0, 0, 0])[0]),
        ))
        page["flow"] = flow

    return {
        "catalogCount": len(catalog),
        "positionedCatalogCount": sum(1 for record in catalog if record.coordinate_page is not None and record.coordinate_bbox_px is not None),
        "reconciledGroupCount": reconciled,
        "policy": "external Mathpix assets are available in donorless mode; no DOCX donor is required",
    }


def _reconcile_toc_column_false_positives(
    result: dict[str, Any],
    mathpix_line_layout_map: dict[str, Any] | None,
) -> dict[str, Any]:
    if not mathpix_line_layout_map:
        return {"reviewedTwoColumnPageCount": 0, "suppressedTwoColumnPageCount": 0, "suppressedPages": []}

    line_pages = {
        int(page.get("page") or 0): page
        for page in mathpix_line_layout_map.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    reviewed = 0
    suppressed: list[int] = []

    for page in result.get("pages", []) or []:
        if str(page.get("layout_mode") or "") != "two_columns":
            continue
        reviewed += 1
        page_no = int(page.get("page") or 0)
        line_page = line_pages.get(page_no) or {}
        ids_by_type = line_page.get("objectIdsByType") or {}
        toc_containers = len(ids_by_type.get("table_of_contents_container") or [])
        toc_rows = len(ids_by_type.get("table_of_contents_row") or [])
        toc_items = len(ids_by_type.get("table_of_contents_item") or [])
        toc_numbers = len(ids_by_type.get("table_of_contents_number") or [])
        mathpix_columns = len(ids_by_type.get("column") or [])
        strong_toc = toc_containers >= 1 and (toc_rows >= 3 or toc_items >= 5)
        if not strong_toc:
            continue

        prior_detection = dict(page.get("layout_detection") or {})
        prior_columns = list(page.get("columns") or [])
        page["layout_mode"] = "single_column"
        page["columns"] = []
        page["layout_detection"] = {
            **prior_detection,
            "accepted": False,
            "reason": "strong-toc-container-not-word-columns",
            "pdfTwoColumnCandidate": bool(prior_columns),
            "semanticReconciliation": {
                "source": "Mathpix lines structural witness",
                "tableOfContentsContainerCount": toc_containers,
                "tableOfContentsRowCount": toc_rows,
                "tableOfContentsItemCount": toc_items,
                "tableOfContentsNumberCount": toc_numbers,
                "mathpixColumnObjectCount": mathpix_columns,
                "policy": "PDF detects geometric two-sided flow; a strong TOC container means that geometry is not emitted as Word section columns.",
            },
        }
        page["suppressedPdfColumns"] = prior_columns
        for item in page.get("flow", []) or []:
            item.pop("column_index", None)
            item.pop("spanning", None)
        page["flow"] = sorted(
            page.get("flow", []) or [],
            key=lambda item: (
                float((item.get("bbox") or [0, 0, 0, 0])[1]),
                float((item.get("bbox") or [0, 0, 0, 0])[0]),
            ),
        )
        suppressed.append(page_no)

    return {
        "reviewedTwoColumnPageCount": reviewed,
        "suppressedTwoColumnPageCount": len(suppressed),
        "suppressedPages": suppressed,
        "policy": "No page numbers are hard-coded. Only strong Mathpix TOC structure can suppress an otherwise accepted PDF two-column candidate.",
    }


def _best_line_match(item_box: list[float], records: list[dict[str, Any]]) -> tuple[int, dict[str, Any]] | None:
    best: tuple[float, int, dict[str, Any]] | None = None
    item_area = max(1.0, _area(item_box))
    for index, record in enumerate(records):
        if str(record.get("type") or "") == "column":
            continue
        line_box = _line_box(record)
        if line_box is None:
            continue
        intersection = _intersection(item_box, line_box)
        line_area = max(1.0, _area(line_box))
        overlap = intersection / min(item_area, line_area)
        contains = _contains(line_box, item_box, tolerance=4.0) or _contains(item_box, line_box, tolerance=4.0)
        if overlap < 0.30 and not contains:
            continue
        score = overlap + (0.20 if contains else 0.0)
        if best is None or score > best[0]:
            best = (score, index, record)
    return (best[1], best[2]) if best is not None else None


def _lines_semantic_type(record_type: str, current: str | None) -> str | None:
    mapping = {
        "section_header": "heading",
        "figure_label": "caption",
        "diagram": "figure",
        "text": "paragraph",
    }
    return mapping.get(record_type, current)


def _apply_lines_first_structure(
    result: dict[str, Any],
    mathpix_line_layout_map: dict[str, Any] | None,
) -> dict[str, Any]:
    """Let Lines lead semantic witness/order without deciding Word page topology.

    Raw Mathpix ``column`` objects are deliberately not translated here into
    ``page['columns']`` or ``layout_mode``. Page-column/rail meaning is resolved
    later by the mature geometry adapter after furniture and body geometry.
    """
    if not mathpix_line_layout_map:
        return {"pageCount": 0, "matchedFlowItemCount": 0}

    line_pages = {
        int(page.get("page") or 0): page
        for page in mathpix_line_layout_map.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    matched_count = 0
    semantic_count = 0
    reordered_pages = 0

    for page in result.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        line_page = line_pages.get(page_no)
        if not line_page:
            continue
        records = list(line_page.get("objects", []) or [])
        ranked: list[tuple[int, float, float, int, dict[str, Any]]] = []
        changed_order = False
        for original_index, item in enumerate(page.get("flow", []) or []):
            item_box = _box(item.get("bbox"))
            if item_box is None:
                ranked.append((1000000 + original_index, 0.0, 0.0, original_index, item))
                continue
            match = _best_line_match(item_box, records)
            rank = 1000000 + original_index
            if match is not None:
                record_index, record = match
                rank = int(record.get("line") or record_index)
                matched_count += 1
                previous_semantic = item.get("semantic_type")
                semantic = _lines_semantic_type(str(record.get("type") or ""), previous_semantic)
                if semantic is not None and semantic != previous_semantic:
                    item["semantic_type"] = semantic
                    semantic_count += 1
            ranked.append((rank, item_box[1], item_box[0], original_index, item))

        reordered = [entry[-1] for entry in sorted(ranked, key=lambda entry: entry[:4])]
        original = list(page.get("flow", []) or [])
        if [id(item) for item in reordered] != [id(item) for item in original]:
            changed_order = True
        page["flow"] = reordered
        if changed_order:
            reordered_pages += 1

    return {
        "pageCount": len(line_pages),
        "matchedFlowItemCount": matched_count,
        "semanticTypeChangeCount": semantic_count,
        "reorderedPageCount": reordered_pages,
        "pageTopologyDecisionCount": 0,
        "policy": "Lines may lead semantic witness/local order; raw column objects never directly become Word/page columns here.",
    }


def _reconcile_columns_with_geometry_authority(
    result: dict[str, Any],
    geometry_map: dict[str, Any],
) -> dict[str, Any]:
    """Suppress pre-topology columns unless mature evidence authorizes them.

    This is fail-closed: only ``true-two-column-page`` with high confidence may
    survive as active page columns. All other legacy/PDF/Lines-first candidates
    are retained only as diagnostic evidence and removed from active layout.
    """
    by_page = {
        int(row.get("page") or 0): row
        for row in geometry_map.get("pages", []) or []
        if int(row.get("page") or 0) > 0
    }
    suppressed: list[int] = []
    authorized: list[int] = []
    for page in result.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        evidence = by_page.get(page_no) or {}
        column_evidence = evidence.get("columnEvidence") or {}
        classification = str(column_evidence.get("classification") or "unresolved")
        confidence = str(column_evidence.get("confidence") or "none")
        is_authorized = classification == "true-two-column-page" and confidence == "high"
        if is_authorized:
            authorized.append(page_no)
            continue

        prior_columns = list(page.get("columns", []) or [])
        prior_two = len(prior_columns) == 2 or str(page.get("layout_mode") or "") == "two_columns"
        if not prior_two:
            continue
        page["suppressedPreTopologyColumns"] = prior_columns
        page["columns"] = []
        page["layout_mode"] = "single_column"
        prior_detection = dict(page.get("layout_detection") or {})
        page["layout_detection"] = {
            **prior_detection,
            "accepted": False,
            "reason": "not-authorized-by-mature-page-geometry",
            "topologyReconciliation": {
                "classification": classification,
                "confidence": confidence,
                "policy": "active page columns require true-two-column-page/high",
            },
        }
        for item in page.get("flow", []) or []:
            item.pop("column_index", None)
            item.pop("spanning", None)
        suppressed.append(page_no)

    return {
        "suppressedPageCount": len(suppressed),
        "suppressedPages": suppressed,
        "authorizedTrueTwoColumnPages": authorized,
        "policy": "only mature true-two-column-page/high evidence can authorize active page columns",
    }


def build_page_structure(
    pdf_analysis: dict[str, Any],
    work_dir,
    asset_dir,
    reference_docx=None,
    external_asset_paths=None,
    equation_donor_path=None,
    mathpix_lines_path=None,
    mathpix_lines_mode="witness",
) -> dict[str, Any]:
    mathpix_line_layout_map = build_mathpix_line_layout_map(Path(mathpix_lines_path), pdf_analysis) if mathpix_lines_path else None
    if mathpix_line_layout_map:
        analyze_page_furniture(pdf_analysis, mathpix_line_layout_map=mathpix_line_layout_map)

    result = _build_legacy(
        pdf_analysis,
        work_dir,
        asset_dir,
        reference_docx=reference_docx,
        external_asset_paths=external_asset_paths,
        equation_donor_path=equation_donor_path,
    )
    mathpix_lines_summary = (mathpix_line_layout_map or {}).get("summary") or (summarize_mathpix_lines(Path(mathpix_lines_path)) if mathpix_lines_path else {
        "available": False,
        "reason": "mathpix_lines_path not provided",
    })

    package_map = None
    package_roots = [Path(path) for path in (external_asset_paths or [])]
    if package_roots:
        package_map = build_mathpix_package_map(package_roots[0], mathpix_line_layout_map)

    external_summary = _reconcile_external_mathpix_assets(
        result,
        pdf_analysis,
        package_roots,
        Path(work_dir),
        Path(asset_dir),
    ) if package_roots else {"catalogCount": 0, "positionedCatalogCount": 0, "reconciledGroupCount": 0}

    toc_column_summary = _reconcile_toc_column_false_positives(result, mathpix_line_layout_map)
    lines_first_summary = (
        _apply_lines_first_structure(result, mathpix_line_layout_map)
        if str(mathpix_lines_mode or "witness").lower().replace("-", "_") == "lines_first"
        else None
    )

    geometry_map = None
    reserved_zone_profile = None
    margin_model = None
    topology_column_reconciliation = None
    if mathpix_line_layout_map:
        geometry_map = build_mathpix_page_geometry_evidence(mathpix_line_layout_map)
        reserved_zone_profile = build_reserved_page_zone_profile(mathpix_line_layout_map, geometry_map)
        margin_model = build_mathpix_margin_model(mathpix_line_layout_map, geometry_map, reserved_zone_profile)
        topology_column_reconciliation = _reconcile_columns_with_geometry_authority(result, geometry_map)
        apply_mathpix_page_geometry(result, geometry_map)
        apply_mathpix_margin_model(result, margin_model)

    pdf_pages = {
        int(page.get("page") or 0): page
        for page in pdf_analysis.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    matched = 0
    review = 0
    unresolved = 0
    for page in result.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        drawings = list((pdf_pages.get(page_no) or {}).get("drawings", []) or [])
        page["pdf_drawings"] = drawings
        page["drawing_count"] = len(drawings)
        pdf_geometry = (pdf_pages.get(page_no) or {}).get("pageGeometry")
        if pdf_geometry:
            page["pageGeometry"] = pdf_geometry
        for callout in page.get("callouts", []) or []:
            evidence = _frame_evidence(callout, drawings)
            callout["frame_evidence"] = evidence
            status = str(evidence.get("status") or "unresolved")
            if status == "matched":
                matched += 1
            elif status == "review":
                review += 1
            else:
                unresolved += 1

    text_style_summary = enrich_page_text_styles(result, pdf_analysis)

    result["version"] = VERSION
    result["mathpixLinesSummary"] = mathpix_lines_summary
    if mathpix_line_layout_map:
        result["mathpixLineLayoutMap"] = {
            "version": mathpix_line_layout_map.get("version"),
            "source": mathpix_line_layout_map.get("source"),
            "policy": mathpix_line_layout_map.get("policy"),
            "summary": mathpix_line_layout_map.get("summary"),
            "rawTopLevel": mathpix_line_layout_map.get("rawTopLevel"),
            "linesFirstSummary": lines_first_summary,
        }
        line_pages = {
            int(page.get("page") or 0): page
            for page in mathpix_line_layout_map.get("pages", []) or []
            if int(page.get("page") or 0) > 0
        }
        for page in result.get("pages", []) or []:
            page_no = int(page.get("page") or 0)
            if page_no in line_pages:
                page["mathpixLinePageMap"] = line_pages[page_no]

    if package_map:
        enrich_with_mathpix_package(result, package_map)

    result["externalAssetReconciliation"] = external_summary
    result["tocColumnReconciliation"] = toc_column_summary
    result["topologyColumnReconciliation"] = topology_column_reconciliation
    result["mathpixReservedPageZoneProfile"] = reserved_zone_profile
    result["textStyleMapSummary"] = text_style_summary
    result["frameEvidenceSummary"] = {
        "source": "pdf_analysis.pages[].drawings",
        "matchedCalloutCount": matched,
        "reviewCalloutCount": review,
        "unresolvedCalloutCount": unresolved,
        "policy": "callout border/fill may be reconstructed only from matched PDF vector evidence",
    }
    result["pageGeometryPolicy"] = {
        "geometryAuthority": "PDF-global topology corroborated by mature Mathpix geometry evidence",
        "semanticWitness": "Mathpix page_info / structural objects",
        "dependencyOrder": ["header", "footer", "body-margins", "columns"],
        "fields": ["headers", "footers", "body_box", "margins", "columns", "sidebars", "pageGeometry", "textStyleMap"],
        "policy": (
            "raw Mathpix column objects never directly become page/Word columns; active columns require "
            "true-two-column-page/high after furniture and body geometry; unresolved/side-rail evidence is fail-closed"
        ),
    }
    return result
