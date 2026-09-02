from __future__ import annotations

import re
from collections import Counter
from statistics import median
from typing import Any


VERSION = "mathpix-page-geometry-adapter-0.5"


def _box(obj: dict[str, Any]) -> list[float] | None:
    src = obj.get("bbox_pt") if isinstance(obj.get("bbox_pt"), dict) else None
    if not src:
        return None
    try:
        x0 = float(src.get("x0")); y0 = float(src.get("y0"))
        x1 = float(src.get("x1")); y1 = float(src.get("y1"))
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _norm_text(obj: dict[str, Any]) -> str:
    return " ".join(str(obj.get("text_display") or obj.get("text") or "").split()).casefold()


def _repeat_signature(text: str) -> str:
    text = " ".join(str(text or "").split()).casefold()
    return re.sub(r"\b\d+\b", "#", text)


def _page_dimensions(page: dict[str, Any]) -> tuple[float, float]:
    try:
        return float(page.get("page_width_pt") or 0), float(page.get("page_height_pt") or 0)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _page_info_objects(page: dict[str, Any]) -> list[dict[str, Any]]:
    width, height = _page_dimensions(page)
    if width <= 0 or height <= 0:
        return []
    result = []
    for obj in page.get("objects", []) or []:
        if str(obj.get("type") or "") != "page_info":
            continue
        box = _box(obj)
        if not box:
            continue
        text = _norm_text(obj)
        cy = (box[1] + box[3]) / 2.0
        result.append({
            "id": obj.get("id"),
            "bbox": box,
            "text": text,
            "signature": _repeat_signature(text),
            "conversion_output": obj.get("conversion_output"),
            "yCenterRatio": cy / height,
            "x0Ratio": box[0] / width,
            "x1Ratio": box[2] / width,
        })
    return result


def _zone_profiles(line_pages: list[dict[str, Any]]) -> dict[str, Any]:
    signatures = {"top": Counter(), "bottom": Counter()}
    y_centers = {"top": [], "bottom": []}
    for page in line_pages:
        for row in _page_info_objects(page):
            ratio = float(row["yCenterRatio"])
            if ratio <= 0.16:
                side = "top"
            elif ratio >= 0.82:
                side = "bottom"
            else:
                continue
            if row["signature"]:
                signatures[side][row["signature"]] += 1
            y_centers[side].append(ratio)
    return {
        "signatures": signatures,
        "medianY": {side: (median(values) if values else None) for side, values in y_centers.items()},
    }


def _classify_page_furniture(page: dict[str, Any], profiles: dict[str, Any]) -> dict[str, Any]:
    top: list[dict[str, Any]] = []
    bottom: list[dict[str, Any]] = []
    middle: list[dict[str, Any]] = []
    sigs = profiles.get("signatures") or {}
    medians = profiles.get("medianY") or {}

    for row in _page_info_objects(page):
        ratio = float(row["yCenterRatio"])
        if ratio <= 0.16:
            side, bucket = "top", top
        elif ratio >= 0.82:
            side, bucket = "bottom", bottom
        else:
            middle.append({**row, "classification": "middle-page-info"})
            continue

        repetition = int((sigs.get(side) or Counter()).get(row.get("signature") or "", 0))
        median_y = medians.get(side)
        band_delta = abs(ratio - float(median_y)) if median_y is not None else 1.0
        repeated = bool(row.get("signature")) and repetition >= 2
        stable_band = band_delta <= 0.035
        has_text = bool(row.get("text"))
        accepted = has_text and (repeated or stable_band)
        confidence = "high" if accepted and repeated and stable_band else ("medium" if accepted else "low")
        bucket.append({
            **row,
            "repetitionCount": repetition,
            "bandDeltaRatio": round(band_delta, 5),
            "accepted": accepted,
            "confidence": confidence,
        })

    def band(rows: list[dict[str, Any]], side: str) -> dict[str, Any] | None:
        accepted = [row for row in rows if row.get("accepted")]
        if not accepted:
            return None
        bbox = _union([row["bbox"] for row in accepted])
        if not bbox:
            return None
        high = sum(1 for row in accepted if row.get("confidence") == "high")
        return {
            "side": side,
            "bbox": bbox,
            "objects": accepted,
            "rejectedCandidates": [row for row in rows if not row.get("accepted")],
            "confidence": "high" if high >= 1 else "medium",
            "semanticPolicy": "page_info becomes header/footer only after edge-zone plus recurrence/stable-band classification",
        }

    header = band(top, "header")
    footer = band(bottom, "footer")

    def status(rows: list[dict[str, Any]], accepted_band: dict[str, Any] | None) -> str:
        if accepted_band:
            return "present-high" if accepted_band.get("confidence") == "high" else "present-medium"
        if rows:
            return "unresolved-candidates"
        return "no-page-info-evidence"

    header_status = status(top, header)
    footer_status = status(bottom, footer)
    fully_resolved = header_status.startswith("present-") and footer_status.startswith("present-")

    return {
        "headerBand": header,
        "footerBand": footer,
        "headerStatus": header_status,
        "footerStatus": footer_status,
        "middlePageInfo": middle,
        "topCandidates": top,
        "bottomCandidates": bottom,
        "classificationComplete": True,
        "safeForMarginInference": fully_resolved,
        "policy": "absence is never inferred merely from missing Mathpix page_info; unresolved/no-evidence furniture blocks margin override until cross-checked",
    }


def _candidate_body_objects(page: dict[str, Any]) -> list[dict[str, Any]]:
    excluded_types = {
        "page_info", "column", "table_row", "table_column",
        "table_of_contents_container", "table_of_contents_row", "table_of_contents_number",
    }
    result = []
    for obj in page.get("objects", []) or []:
        typ = str(obj.get("type") or "")
        if typ in excluded_types:
            continue
        box = _box(obj)
        if box:
            result.append({"id": obj.get("id"), "type": typ, "bbox": box, "parent_id": obj.get("parent_id")})
    return result


def _robust_body_box(page: dict[str, Any], furniture: dict[str, Any]) -> dict[str, Any] | None:
    if not furniture.get("classificationComplete") or not furniture.get("safeForMarginInference"):
        return None
    width, height = _page_dimensions(page)
    if width <= 0 or height <= 0:
        return None
    objects = _candidate_body_objects(page)
    if not objects:
        return None

    header = furniture.get("headerBand") or {}
    footer = furniture.get("footerBand") or {}
    header_end = float((header.get("bbox") or [0, 0, 0, 0])[3])
    footer_start = float((footer.get("bbox") or [0, height, 0, height])[1])
    vertical = [row for row in objects if row["bbox"][3] > header_end and row["bbox"][1] < footer_start]
    if not vertical:
        return None

    x0s = sorted(row["bbox"][0] for row in vertical)
    x1s = sorted(row["bbox"][2] for row in vertical)
    y0s = sorted(row["bbox"][1] for row in vertical)
    y1s = sorted(row["bbox"][3] for row in vertical)

    def q(values: list[float], fraction: float) -> float:
        idx = min(len(values) - 1, max(0, round((len(values) - 1) * fraction)))
        return float(values[idx])

    robust = [q(x0s, 0.08), q(y0s, 0.04), q(x1s, 0.92), q(y1s, 0.96)]
    raw = _union([row["bbox"] for row in vertical])
    robust[1] = max(robust[1], header_end)
    robust[3] = min(robust[3], footer_start)
    if robust[2] <= robust[0] or robust[3] <= robust[1]:
        return None
    return {
        "bbox": robust,
        "rawObjectEnvelope": raw,
        "objectCount": len(vertical),
        "marginsPt": {
            "left": round(robust[0], 3),
            "right": round(width - robust[2], 3),
            "top": round(robust[1], 3),
            "bottom": round(height - robust[3], 3),
        },
        "confidence": "high" if len(vertical) >= 8 else "medium",
        "dependsOnFurnitureClassification": True,
        "policy": "body bounds are inferred only after both header and footer are positively classified; robust envelope prevents side furniture from redefining margins",
    }


def _column_rows(page: dict[str, Any], body_box: list[float] | None) -> list[dict[str, Any]]:
    width, height = _page_dimensions(page)
    if width <= 0 or height <= 0:
        return []
    body_h = (body_box[3] - body_box[1]) if body_box else height
    rows = []
    for obj in page.get("objects", []) or []:
        if str(obj.get("type") or "") != "column":
            continue
        box = _box(obj)
        if not box:
            continue
        rows.append({
            "id": obj.get("id"),
            "bbox": box,
            "widthRatio": (box[2] - box[0]) / width,
            "heightRatio": (box[3] - box[1]) / max(1.0, body_h),
            "parent_id": obj.get("parent_id"),
            "children_ids": list(obj.get("children_ids") or []),
        })
    return rows


def _classify_columns(page: dict[str, Any], body: dict[str, Any] | None, furniture: dict[str, Any]) -> dict[str, Any]:
    if not furniture.get("safeForMarginInference") or not body:
        return {"classification": "blocked-until-furniture-resolved", "confidence": "none", "pageColumns": [], "sidebars": [], "localColumns": []}
    width, _height = _page_dimensions(page)
    rows = _column_rows(page, body.get("bbox"))
    if not rows:
        return {"classification": "no-explicit-page-columns", "confidence": "low", "pageColumns": [], "sidebars": [], "localColumns": []}

    candidates = [r for r in rows if r["heightRatio"] >= 0.45 and r["widthRatio"] >= 0.16]
    candidates.sort(key=lambda r: r["bbox"][0])
    true_columns: list[dict[str, Any]] = []
    sidebars: list[dict[str, Any]] = []

    if len(candidates) == 2:
        a, b = candidates
        wa, wb = a["widthRatio"], b["widthRatio"]
        gap = max(0.0, b["bbox"][0] - a["bbox"][2]) / max(1.0, width)
        similar = min(wa, wb) / max(wa, wb) >= 0.72
        if similar and gap <= 0.12:
            true_columns = candidates
            classification, confidence = "true-two-column-page", "high"
        elif min(wa, wb) <= 0.28 and max(wa, wb) >= 0.48:
            sidebars = candidates
            classification, confidence = "main-plus-sidebar", "high"
        else:
            classification, confidence = "ambiguous-page-containers", "medium"
    elif len(candidates) == 1:
        classification, confidence = "single-main-column-container", "medium"
    elif len(candidates) > 2:
        classification, confidence = "multiple-layout-containers", "medium"
    else:
        classification, confidence = "local-columns-only", "medium"

    page_ids = {row["id"] for row in true_columns + sidebars}
    return {
        "classification": classification,
        "confidence": confidence,
        "pageColumns": true_columns,
        "sidebars": sidebars,
        "localColumns": [row for row in rows if row["id"] not in page_ids],
        "allMathpixColumnObjects": rows,
        "dependsOnFurnitureClassification": True,
        "policy": "Mathpix column objects become Word page columns only after positively resolved page furniture and body geometry",
    }


def _topology_boxes(column_evidence: dict[str, Any]) -> dict[str, Any]:
    if str(column_evidence.get("classification") or "") != "main-plus-sidebar":
        return {"mainFlowBox": None, "outerRailBox": None, "outerRailSide": None}
    rows = list(column_evidence.get("sidebars") or [])
    if len(rows) != 2:
        return {"mainFlowBox": None, "outerRailBox": None, "outerRailSide": None}
    narrow = min(rows, key=lambda row: float(row.get("widthRatio") or 1.0))
    wide = max(rows, key=lambda row: float(row.get("widthRatio") or 0.0))
    nbox = list(narrow.get("bbox") or [])
    wbox = list(wide.get("bbox") or [])
    if len(nbox) != 4 or len(wbox) != 4:
        return {"mainFlowBox": None, "outerRailBox": None, "outerRailSide": None}
    ncenter = (float(nbox[0]) + float(nbox[2])) / 2.0
    wcenter = (float(wbox[0]) + float(wbox[2])) / 2.0
    return {
        "mainFlowBox": wbox,
        "outerRailBox": nbox,
        "outerRailSide": "left" if ncenter < wcenter else "right",
    }


def build_mathpix_page_geometry_evidence(line_map: dict[str, Any]) -> dict[str, Any]:
    pages = list(line_map.get("pages", []) or [])
    profiles = _zone_profiles(pages)
    out = []
    class_counts: Counter[str] = Counter()
    header_status_counts = Counter(); footer_status_counts = Counter()

    for page in pages:
        width, height = _page_dimensions(page)
        furniture = _classify_page_furniture(page, profiles)
        header = furniture.get("headerBand")
        footer = furniture.get("footerBand")
        header_status_counts[furniture.get("headerStatus") or "unknown"] += 1
        footer_status_counts[furniture.get("footerStatus") or "unknown"] += 1
        body = _robust_body_box(page, furniture)
        columns = _classify_columns(page, body, furniture)
        class_counts[columns["classification"]] += 1
        out.append({
            "page": int(page.get("page") or 0),
            "pageWidthPt": width,
            "pageHeightPt": height,
            "headerFooterClassification": furniture,
            "headerBand": header,
            "footerBand": footer,
            "bodyBox": body,
            "columnEvidence": columns,
            "topologyBoxes": _topology_boxes(columns),
            "dependencyOrder": ["header", "footer", "body-margins", "columns"],
        })

    return {
        "version": VERSION,
        "policy": "strict dependency order: positively resolve headers and footers first; only then infer body/margins; only then classify page columns",
        "summary": {
            "pageCount": len(out),
            "headerStatusCounts": dict(sorted(header_status_counts.items())),
            "footerStatusCounts": dict(sorted(footer_status_counts.items())),
            "columnClassificationCounts": dict(sorted(class_counts.items())),
        },
        "pages": out,
    }


def refine_mathpix_page_geometry_evidence(
    line_map: dict[str, Any],
    geometry_map: dict[str, Any],
    reserved_profile: dict[str, Any] | None,
    margin_model: dict[str, Any] | None,
) -> dict[str, Any]:
    """Second topology pass after reserved zones and margin evidence exist.

    This pass may classify previously blocked Mathpix containers using a resolved
    page body box. It does not grant Word two-column authority when the body box
    came only from an inherited/document-family profile; such evidence remains
    structural only and is deliberately fail-closed for section columns.
    """
    line_pages = {
        int(page.get("page") or 0): page
        for page in line_map.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    reserved_pages = {
        int(page.get("page") or 0): page
        for page in (reserved_profile or {}).get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    margin_pages = {
        int(page.get("page") or 0): page
        for page in (margin_model or {}).get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    refined = 0
    profile_only_true_columns: list[int] = []

    for row in geometry_map.get("pages", []) or []:
        page_no = int(row.get("page") or 0)
        current = row.get("columnEvidence") or {}
        if str(current.get("classification") or "") != "blocked-until-furniture-resolved":
            row["topologyBoxes"] = _topology_boxes(current)
            continue
        line_page = line_pages.get(page_no)
        margin = margin_pages.get(page_no) or {}
        body_box = margin.get("bodyBox")
        if not line_page or margin.get("status") != "resolved" or not isinstance(body_box, list) or len(body_box) != 4:
            continue

        reserved = reserved_pages.get(page_no) or {}
        synthetic_furniture = {
            "safeForMarginInference": True,
            "classificationComplete": True,
            "headerStatus": (row.get("headerFooterClassification") or {}).get("headerStatus"),
            "footerStatus": (row.get("headerFooterClassification") or {}).get("footerStatus"),
            "reservedHeaderStatus": reserved.get("headerReservedZoneStatus"),
            "reservedFooterStatus": reserved.get("footerReservedZoneStatus"),
            "source": "resolved-margin-model-second-pass",
        }
        body = {
            "bbox": list(body_box),
            "confidence": margin.get("confidence"),
            "source": margin.get("source"),
        }
        classified = _classify_columns(line_page, body, synthetic_furniture)
        classified["refinedAfterReservedZones"] = True
        classified["bodyGeometrySource"] = margin.get("source")
        classified["bodyGeometryConfidence"] = margin.get("confidence")
        classified["wordColumnAuthorization"] = (
            "eligible"
            if classified.get("classification") == "true-two-column-page"
            and classified.get("confidence") == "high"
            and margin.get("source") == "page-evidence"
            and margin.get("confidence") == "high"
            else "not-authorized"
        )
        if classified.get("classification") == "true-two-column-page" and classified["wordColumnAuthorization"] != "eligible":
            classified["confidence"] = "medium"
            profile_only_true_columns.append(page_no)
        row["columnEvidence"] = classified
        row["topologyBoxes"] = _topology_boxes(classified)
        row["columnRefinementSource"] = {
            "reservedPageZones": reserved,
            "marginSource": margin.get("source"),
            "marginConfidence": margin.get("confidence"),
            "policy": "profile-resolved geometry may classify containers but cannot alone authorize Word section columns",
        }
        refined += 1

    geometry_map["version"] = VERSION
    geometry_map["secondPassRefinement"] = {
        "refinedPageCount": refined,
        "profileOnlyTrueColumnPages": profile_only_true_columns,
        "policy": "second-pass classification may enrich topology after reserved zones; Word columns still require direct high-confidence page evidence",
    }
    counts = Counter(
        str((row.get("columnEvidence") or {}).get("classification") or "unresolved")
        for row in geometry_map.get("pages", []) or []
    )
    geometry_map.setdefault("summary", {})["columnClassificationCountsAfterRefinement"] = dict(sorted(counts.items()))
    return geometry_map


def apply_mathpix_page_geometry(page_structure: dict[str, Any], geometry_map: dict[str, Any]) -> dict[str, Any]:
    evidence_by_page = {int(row.get("page") or 0): row for row in geometry_map.get("pages", []) or []}
    override_counts = Counter()

    for page in page_structure.get("pages", []) or []:
        ev = evidence_by_page.get(int(page.get("page") or 0))
        if not ev:
            continue
        page["mathpixGeometryEvidence"] = ev

        furniture = ev.get("headerFooterClassification") or {}
        header = ev.get("headerBand")
        footer = ev.get("footerBand")
        if header and header.get("confidence") == "high":
            page["mathpix_header_band"] = header
            override_counts["headerBand"] += 1
        if footer and footer.get("confidence") == "high":
            page["mathpix_footer_band"] = footer
            override_counts["footerBand"] += 1

        body = ev.get("bodyBox") or {}
        body_box = body.get("bbox")
        if furniture.get("safeForMarginInference") and body_box and body.get("confidence") == "high":
            page["body_box"] = list(body_box)
            page["margins"] = dict(body.get("marginsPt") or {})
            override_counts["bodyBoxAndMargins"] += 1

        col = ev.get("columnEvidence") or {}
        authorized = (
            col.get("classification") == "true-two-column-page"
            and col.get("confidence") == "high"
            and str(col.get("wordColumnAuthorization") or "eligible") == "eligible"
        )
        if authorized:
            cols = []
            for index, row in enumerate(col.get("pageColumns") or []):
                box = row.get("bbox") or [0, 0, 0, 0]
                cols.append({
                    "index": index,
                    "x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3],
                    "source": "mathpix-structural-column",
                    "mathpixObjectId": row.get("id"),
                })
            if len(cols) == 2:
                page["columns"] = cols
                page["layout_mode"] = "two_columns"
                page["main_column"] = {
                    "x0": cols[0]["x0"], "x1": cols[1]["x1"],
                    "y0": min(cols[0]["y0"], cols[1]["y0"]),
                    "y1": max(cols[0]["y1"], cols[1]["y1"]),
                    "source": "mathpix-classified-two-column-page",
                }
                override_counts["trueTwoColumnPage"] += 1
        elif col.get("classification") == "main-plus-sidebar":
            page["mathpix_sidebars"] = list(col.get("sidebars") or [])
            boxes = ev.get("topologyBoxes") or _topology_boxes(col)
            main_flow = boxes.get("mainFlowBox")
            rail_box = boxes.get("outerRailBox")
            if isinstance(main_flow, list) and len(main_flow) == 4:
                page["main_flow_box"] = list(main_flow)
            if isinstance(rail_box, list) and len(rail_box) == 4:
                page["outer_rail_box"] = list(rail_box)
                page["outer_rail_side"] = boxes.get("outerRailSide")
            page["page_topology"] = {
                "classification": "main-plus-sidebar",
                "contentEnvelope": page.get("body_box"),
                "mainFlowBox": page.get("main_flow_box"),
                "outerRailBox": page.get("outer_rail_box"),
                "outerRailSide": page.get("outer_rail_side"),
                "rendererMeaning": "main flow and rail remain distinct; no Word columns emitted",
                "source": "mature-mathpix-page-geometry-adapter",
            }
            override_counts["sidebarEvidenceOnly"] += 1

    page_structure["mathpixPageGeometryMap"] = geometry_map
    page_structure["mathpixGeometryApplication"] = {
        "version": VERSION,
        "overrideCounts": dict(sorted(override_counts.items())),
        "dependencyOrder": ["header", "footer", "body-margins", "columns"],
        "policy": "unresolved/no-evidence headers or footers block direct margin overrides; second-pass profile geometry may enrich topology but cannot alone authorize Word columns",
    }
    return page_structure
