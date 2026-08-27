from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any


VERSION = "mathpix-page-geometry-adapter-0.1"


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
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _norm_text(obj: dict[str, Any]) -> str:
    return " ".join(str(obj.get("text_display") or obj.get("text") or "").split()).casefold()


def _page_dimensions(page: dict[str, Any]) -> tuple[float, float]:
    try:
        return float(page.get("page_width_pt") or 0), float(page.get("page_height_pt") or 0)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _page_info_candidates(page: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    width, height = _page_dimensions(page)
    top: list[dict[str, Any]] = []
    bottom: list[dict[str, Any]] = []
    middle: list[dict[str, Any]] = []
    if width <= 0 or height <= 0:
        return top, bottom, middle
    for obj in page.get("objects", []) or []:
        if str(obj.get("type") or "") != "page_info":
            continue
        box = _box(obj)
        if not box:
            continue
        cy = (box[1] + box[3]) / 2.0
        row = {"id": obj.get("id"), "bbox": box, "text": _norm_text(obj), "conversion_output": obj.get("conversion_output")}
        if cy <= height * 0.16:
            top.append(row)
        elif cy >= height * 0.82:
            bottom.append(row)
        else:
            middle.append(row)
    return top, bottom, middle


def _repetition_profiles(line_pages: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    top = Counter(); bottom = Counter()
    for page in line_pages:
        t, b, _m = _page_info_candidates(page)
        top.update(row["text"] for row in t if row["text"])
        bottom.update(row["text"] for row in b if row["text"])
    return {"top": top, "bottom": bottom}


def _furniture_band(rows: list[dict[str, Any]], *, height: float, profile: Counter[str], side: str) -> dict[str, Any] | None:
    if not rows or height <= 0:
        return None
    useful = []
    for row in rows:
        text = row.get("text") or ""
        repetition = int(profile.get(text, 0)) if text else 0
        # Position is primary; repeated text increases confidence. Empty furniture
        # may remain as evidence but does not by itself define a semantic header/footer.
        score = 1 + min(3, repetition)
        useful.append({**row, "repetitionCount": repetition, "score": score})
    boxes = [row["bbox"] for row in useful]
    union = _union(boxes)
    if not union:
        return None
    confidence = "high" if any(row["repetitionCount"] >= 3 for row in useful) else "medium"
    return {
        "side": side,
        "bbox": union,
        "objects": useful,
        "confidence": confidence,
        "semanticPolicy": "page_info is header/footer only after positional classification; repetition strengthens but is not mandatory",
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
        if not box:
            continue
        result.append({"id": obj.get("id"), "type": typ, "bbox": box, "parent_id": obj.get("parent_id")})
    return result


def _robust_body_box(page: dict[str, Any], header: dict[str, Any] | None, footer: dict[str, Any] | None) -> dict[str, Any] | None:
    width, height = _page_dimensions(page)
    if width <= 0 or height <= 0:
        return None
    objects = _candidate_body_objects(page)
    if not objects:
        return None

    header_end = float((header or {}).get("bbox", [0, 0, 0, 0])[3]) if header else 0.0
    footer_start = float((footer or {}).get("bbox", [0, height, 0, height])[1]) if footer else height
    vertical = [row for row in objects if row["bbox"][3] > header_end and row["bbox"][1] < footer_start]
    if not vertical:
        vertical = objects

    # Use quantiles rather than extreme min/max so isolated side furniture does not
    # become an enormous margin distortion. The object envelope is still retained.
    x0s = sorted(row["bbox"][0] for row in vertical)
    x1s = sorted(row["bbox"][2] for row in vertical)
    y0s = sorted(row["bbox"][1] for row in vertical)
    y1s = sorted(row["bbox"][3] for row in vertical)

    def q(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        idx = min(len(values) - 1, max(0, round((len(values) - 1) * fraction)))
        return float(values[idx])

    robust = [q(x0s, 0.08), q(y0s, 0.04), q(x1s, 0.92), q(y1s, 0.96)]
    raw = _union([row["bbox"] for row in vertical])
    if header and robust[1] < header_end:
        robust[1] = header_end
    if footer and robust[3] > footer_start:
        robust[3] = footer_start
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
        "policy": "body bounds use robust Mathpix object envelope constrained by classified header/footer bands; side furniture cannot directly redefine page margins",
    }


def _column_rows(page: dict[str, Any], body_box: list[float] | None) -> list[dict[str, Any]]:
    width, height = _page_dimensions(page)
    if width <= 0 or height <= 0:
        return []
    rows = []
    for obj in page.get("objects", []) or []:
        if str(obj.get("type") or "") != "column":
            continue
        box = _box(obj)
        if not box:
            continue
        bw = box[2] - box[0]; bh = box[3] - box[1]
        body_h = (body_box[3] - body_box[1]) if body_box else height
        rows.append({
            "id": obj.get("id"),
            "bbox": box,
            "widthRatio": bw / width,
            "heightRatio": bh / max(1.0, body_h),
            "parent_id": obj.get("parent_id"),
            "children_ids": list(obj.get("children_ids") or []),
        })
    return rows


def _classify_columns(page: dict[str, Any], body: dict[str, Any] | None) -> dict[str, Any]:
    width, _height = _page_dimensions(page)
    body_box = (body or {}).get("bbox")
    rows = _column_rows(page, body_box)
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
            classification = "true-two-column-page"
            confidence = "high"
        elif min(wa, wb) <= 0.28 and max(wa, wb) >= 0.48:
            sidebars = candidates
            classification = "main-plus-sidebar"
            confidence = "high"
        else:
            classification = "ambiguous-page-containers"
            confidence = "medium"
    elif len(candidates) == 1:
        classification = "single-main-column-container"
        confidence = "medium"
    elif len(candidates) > 2:
        classification = "multiple-layout-containers"
        confidence = "medium"
    else:
        classification = "local-columns-only"
        confidence = "medium"

    page_ids = {row["id"] for row in true_columns + sidebars}
    local = [row for row in rows if row["id"] not in page_ids]
    return {
        "classification": classification,
        "confidence": confidence,
        "pageColumns": true_columns,
        "sidebars": sidebars,
        "localColumns": local,
        "allMathpixColumnObjects": rows,
        "policy": "Mathpix column objects are classified by page coverage and relative widths before they may become Word page columns",
    }


def build_mathpix_page_geometry_evidence(line_map: dict[str, Any]) -> dict[str, Any]:
    pages = list(line_map.get("pages", []) or [])
    repetition = _repetition_profiles(pages)
    out = []
    class_counts: Counter[str] = Counter()
    for page in pages:
        width, height = _page_dimensions(page)
        top, bottom, middle = _page_info_candidates(page)
        header = _furniture_band(top, height=height, profile=repetition["top"], side="header")
        footer = _furniture_band(bottom, height=height, profile=repetition["bottom"], side="footer")
        body = _robust_body_box(page, header, footer)
        columns = _classify_columns(page, body)
        class_counts[columns["classification"]] += 1
        out.append({
            "page": int(page.get("page") or 0),
            "pageWidthPt": width,
            "pageHeightPt": height,
            "headerBand": header,
            "footerBand": footer,
            "middlePageInfo": middle,
            "bodyBox": body,
            "columnEvidence": columns,
        })
    return {
        "version": VERSION,
        "policy": "builder-compatible geometry evidence; no flow reconstruction; high-confidence Mathpix structure may replace legacy heuristics only after classification",
        "summary": {
            "pageCount": len(out),
            "columnClassificationCounts": dict(sorted(class_counts.items())),
        },
        "pages": out,
    }


def apply_mathpix_page_geometry(page_structure: dict[str, Any], geometry_map: dict[str, Any]) -> dict[str, Any]:
    """Enrich/override only existing page geometry concepts.

    The legacy schema remains authoritative for compatibility. Overrides are
    limited to high-confidence page-level evidence; sidebars/local containers do
    not become page columns or margins.
    """
    evidence_by_page = {int(row.get("page") or 0): row for row in geometry_map.get("pages", []) or []}
    override_counts = Counter()
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        ev = evidence_by_page.get(page_no)
        if not ev:
            continue
        page["mathpixGeometryEvidence"] = ev

        body = ev.get("bodyBox") or {}
        body_box = body.get("bbox")
        if body_box and body.get("confidence") == "high":
            page["body_box"] = list(body_box)
            page["margins"] = dict(body.get("marginsPt") or {})
            override_counts["bodyBoxAndMargins"] += 1

        header = ev.get("headerBand")
        footer = ev.get("footerBand")
        if header and header.get("confidence") == "high":
            page["mathpix_header_band"] = header
            override_counts["headerBand"] += 1
        if footer and footer.get("confidence") == "high":
            page["mathpix_footer_band"] = footer
            override_counts["footerBand"] += 1

        col = ev.get("columnEvidence") or {}
        if col.get("classification") == "true-two-column-page" and col.get("confidence") == "high":
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
                page["main_column"] = {
                    "x0": cols[0]["x0"], "x1": cols[1]["x1"],
                    "y0": min(cols[0]["y0"], cols[1]["y0"]),
                    "y1": max(cols[0]["y1"], cols[1]["y1"]),
                    "source": "mathpix-classified-two-column-page",
                }
                override_counts["trueTwoColumnPage"] += 1
        elif col.get("classification") == "main-plus-sidebar":
            page["mathpix_sidebars"] = list(col.get("sidebars") or [])
            override_counts["sidebarEvidenceOnly"] += 1

    page_structure["mathpixPageGeometryMap"] = geometry_map
    page_structure["mathpixGeometryApplication"] = {
        "version": VERSION,
        "overrideCounts": dict(sorted(override_counts.items())),
        "policy": "true page columns may override legacy columns; sidebar/local columns never redefine page margins or Word columns",
    }
    return page_structure
