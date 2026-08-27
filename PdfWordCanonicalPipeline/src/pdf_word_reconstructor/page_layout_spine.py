from __future__ import annotations

# Canonical maps-first layout entry point.
# Before the builder-ready layout spine runs, bind unplaced Markdown visuals and
# display equations to evidence already frozen in the page maps. PDF remains the
# physical authority; Mathpix MMD/lines identity provides semantic ownership.
import re
from collections import defaultdict
from typing import Any

from .donorless_visual_groups import bind_visuals_to_pdf_groups
from .mathpix_lines_input import build_mathpix_line_layout_map, summarize_mathpix_lines
from .page_layout_spine_v08 import build_page_layout_spine as _build_v08


VERSION = "page-layout-spine-wrapper-0.12"
_MATH_TYPES = {"math", "equation", "display_math", "display_equation"}


def _line_map_from_page_structure(page_structure: dict[str, Any]) -> dict[str, Any] | None:
    meta = page_structure.get("mathpixLineLayoutMap")
    page_maps = [
        page.get("mathpixLinePageMap")
        for page in page_structure.get("pages", []) or []
        if isinstance(page.get("mathpixLinePageMap"), dict)
    ]
    if not isinstance(meta, dict) and not page_maps:
        return None
    return {
        "version": (meta or {}).get("version"),
        "source": (meta or {}).get("source"),
        "policy": (meta or {}).get("policy"),
        "summary": (meta or {}).get("summary") or page_structure.get("mathpixLinesSummary") or {},
        "rawTopLevel": (meta or {}).get("rawTopLevel") or {},
        "pages": page_maps,
    }


def _normalize_math(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\\[", "").replace("\\]", "").replace("$$", "")
    text = re.sub(r"\\begin\{(?:equation\*?|aligned|align\*?|gather\*?)\}", "", text)
    text = re.sub(r"\\end\{(?:equation\*?|aligned|align\*?|gather\*?)\}", "", text)
    text = re.sub(r"\\(?:left|right|displaystyle)", "", text)
    text = re.sub(r"\s+", "", text)
    return text.casefold()


def _math_item_payload(item: dict[str, Any]) -> str:
    authoritative = item.get("authoritativeContent") if isinstance(item.get("authoritativeContent"), dict) else {}
    for value in (
        authoritative.get("latex"),
        authoritative.get("rawMarkdown"),
        item.get("rawMarkdown"),
        item.get("text"),
    ):
        normalized = _normalize_math(value)
        if normalized:
            return normalized
    return ""


def _math_line_payload(record: dict[str, Any]) -> str:
    for value in (record.get("text_display"), record.get("text")):
        normalized = _normalize_math(value)
        if normalized:
            return normalized
    raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
    for key in ("text_display", "text", "latex", "value"):
        normalized = _normalize_math(raw.get(key))
        if normalized:
            return normalized
    return ""


def _bbox_pt(record: dict[str, Any]) -> list[float] | None:
    box = record.get("bbox_pt") if isinstance(record.get("bbox_pt"), dict) else {}
    try:
        x0 = float(box.get("x0"))
        y0 = float(box.get("y0"))
        x1 = float(box.get("x1"))
        y1 = float(box.get("y1"))
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]


def _item_page(item: dict[str, Any]) -> int:
    for key in ("pdfPage", "inferredPage", "markdownPageHint"):
        try:
            page = int(item.get(key) or 0)
        except (TypeError, ValueError):
            page = 0
        if page > 0:
            return page
    return 0


def _bind_mathpix_equations(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
) -> dict[str, Any]:
    """Exact MMD display-equation ↔ Mathpix-lines binding.

    Same page + exact normalized math payload is required. Repeated identical
    equations are paired in source/reading order. No fuzzy or PDF search occurs.
    """
    candidates: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        line_page = page.get("mathpixLinePageMap") if isinstance(page.get("mathpixLinePageMap"), dict) else {}
        for record in line_page.get("objects", []) or []:
            if str(record.get("type") or "").strip().lower() not in _MATH_TYPES:
                continue
            normalized = _math_line_payload(record)
            box = _bbox_pt(record)
            if normalized and box:
                candidates[(page_no, normalized)].append(record)
    for rows in candidates.values():
        rows.sort(key=lambda row: (
            float(((row.get("bbox_pt") or {}).get("y0") or 0.0)),
            float(((row.get("bbox_pt") or {}).get("x0") or 0.0)),
            int(row.get("line") or 0),
        ))

    equations = [
        item for item in markdown_pdf_spine.get("items", []) or []
        if str(item.get("type") or "").strip().lower() == "display_equation"
    ]
    equations.sort(key=lambda item: int(item.get("orderIndex") or 0))
    used: dict[tuple[int, str], int] = defaultdict(int)
    bound: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for item in equations:
        if item.get("pdfRegion") and item.get("bbox"):
            continue
        page_no = _item_page(item)
        normalized = _math_item_payload(item)
        key = (page_no, normalized)
        rows = candidates.get(key, []) if page_no and normalized else []
        ordinal = used[key]
        if ordinal >= len(rows):
            unresolved.append({
                "markdownId": item.get("id"),
                "page": page_no or None,
                "candidateCount": len(rows),
                "reason": "no-unused-exact-same-page-mathpix-lines-match",
                "normalizedMath": normalized[:220],
            })
            continue
        record = rows[ordinal]
        used[key] += 1
        box = _bbox_pt(record)
        line_id = str(record.get("id") or "")
        item["pdfPage"] = page_no
        item["pdfRegion"] = line_id
        item["pdfParentRegion"] = record.get("parent_id")
        item["pdfLineIndex"] = record.get("line")
        item["pdfRowGranularity"] = "mathpix-lines-math-object"
        item["bbox"] = box
        item["status"] = "mathpix-lines-equation"
        item["manifestOutcome"] = "mathpix-mmd-lines-identity-confirmed"
        item["matchMode"] = "exact-same-page-math-content-order"
        item["score"] = 100.0
        item["mathpixEquationWitness"] = {
            "lineId": line_id,
            "type": record.get("type"),
            "subtype": record.get("subtype"),
            "bboxPt": box,
            "text": record.get("text"),
            "textDisplay": record.get("text_display"),
            "source": "page_structure.mathpixLinePageMap",
        }
        bound.append({
            "markdownId": item.get("id"),
            "page": page_no,
            "lineId": line_id,
            "bboxPt": box,
        })

    audit = {
        "version": "mathpix-equation-binding-0.1",
        "equationCount": len(equations),
        "boundCount": len(bound),
        "unresolvedCount": len(unresolved),
        "coverage": round(len(bound) / len(equations), 5) if equations else 1.0,
        "policy": "exact normalized same-page MMD↔Mathpix-lines identity; repeats paired by order; no fuzzy/PDF search",
        "bound": bound,
        "unresolved": unresolved,
    }
    markdown_pdf_spine["mathpixEquationBinding"] = audit
    return audit


def _apply_equation_layout_contracts(
    result: dict[str, Any],
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
) -> dict[str, Any]:
    source_by_id = {
        str(item.get("id") or ""): item
        for item in markdown_pdf_spine.get("items", []) or []
        if item.get("id")
    }
    pages = {
        int(page.get("page") or 0): page
        for page in page_structure.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    applied: list[dict[str, Any]] = []

    for row in result.get("rows", []) or []:
        if str(row.get("markdownType") or "").strip().lower() != "display_equation":
            continue
        existing = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
        if str(existing.get("status") or "") == "usable":
            continue
        source = source_by_id.get(str(row.get("markdownId") or "")) or {}
        witness = source.get("mathpixEquationWitness") if isinstance(source.get("mathpixEquationWitness"), dict) else {}
        box = witness.get("bboxPt")
        page_no = int(source.get("pdfPage") or 0)
        line_id = str(witness.get("lineId") or "")
        if not (isinstance(box, list) and len(box) == 4 and page_no > 0 and line_id):
            continue
        page = pages.get(page_no) or {}
        try:
            width = float(page.get("width_pt") or 0.0)
            height = float(page.get("height_pt") or 0.0)
        except (TypeError, ValueError):
            width = height = 0.0
        relative = [
            round(float(box[0]) / width, 6),
            round(float(box[1]) / height, 6),
            round(float(box[2]) / width, 6),
            round(float(box[3]) / height, 6),
        ] if width > 0 and height > 0 else None

        layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
        layout.update({
            "status": "layout-slot",
            "matchMode": "mathpix-mmd-lines-identity",
            "score": 100.0,
            "page": page_no,
            "slotId": line_id,
            "slotSource": "mathpix-lines-math-object",
            "slotType": "math",
            "semanticType": "equation",
            "bbox": box,
            "spanning": False,
            "flowOrder": source.get("orderIndex"),
        })
        row["layout"] = layout
        row["layoutContract"] = {
            "status": "usable",
            "page": page_no,
            "layoutMode": page.get("layout_mode"),
            "slot": {
                "id": line_id,
                "source": "mathpix-lines-math-object",
                "type": "math",
                "semanticType": "equation",
            },
            "box": {
                "absolutePt": box,
                "relativePage": relative,
                "source": "mathpix-lines-scaled-to-pdf-points",
            },
            "placement": "equation-flow",
            "column": {"index": None, "role": "main", "spanning": False},
            "builderUse": {
                "safeForFlowOrdering": True,
                "requiresPositionedFrame": False,
                "requiresVisualPlacement": False,
            },
            "styleHint": {
                "role": "math",
                "markdownType": "display_equation",
                "semanticType": "equation",
                "source": "mathpix-mmd-lines-identity",
            },
            "evidence": witness,
        }
        applied.append({"markdownId": row.get("markdownId"), "page": page_no, "slotId": line_id})

    audit = {
        "version": "mathpix-equation-binding-0.1",
        "appliedCount": len(applied),
        "policy": "layout contracts materialized only from exact MMD↔Mathpix-lines equation bindings",
        "items": applied,
    }
    result["mathpixEquationLayoutContracts"] = audit
    return audit


def build_page_layout_spine(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
    docx_donor_map: dict[str, Any],
    mathpix_lines_path=None,
) -> dict[str, Any]:
    equation_binding = _bind_mathpix_equations(markdown_pdf_spine, page_structure)
    visual_binding = bind_visuals_to_pdf_groups(markdown_pdf_spine, page_structure)
    result = _build_v08(markdown_pdf_spine, page_structure, docx_donor_map)
    equation_layout = _apply_equation_layout_contracts(result, markdown_pdf_spine, page_structure)
    result["canonicalWrapperVersion"] = VERSION
    result["visualGroupBinding"] = visual_binding
    result["mathpixEquationBinding"] = equation_binding
    result["mathpixEquationLayoutContracts"] = equation_layout
    result.setdefault("summary", {})["mathpixEquationBinding"] = {
        "equationCount": equation_binding.get("equationCount"),
        "boundCount": equation_binding.get("boundCount"),
        "unresolvedCount": equation_binding.get("unresolvedCount"),
        "layoutContractAppliedCount": equation_layout.get("appliedCount"),
    }

    line_map = _line_map_from_page_structure(page_structure)
    if line_map is None and mathpix_lines_path:
        # Compatibility fallback. The preferred path is the page_structure map,
        # because it already contains bbox_pt scaled against pdf_analysis.
        line_map = build_mathpix_line_layout_map(mathpix_lines_path)
    if line_map:
        result["mathpixLinesSummary"] = line_map.get("summary") or summarize_mathpix_lines(mathpix_lines_path)
        result["mathpixLineLayoutMap"] = line_map
        result.setdefault("summary", {})["mathpixLinesAvailable"] = True
    else:
        result["mathpixLinesSummary"] = {"available": False, "reason": "mathpix lines evidence not provided"}
        result.setdefault("summary", {})["mathpixLinesAvailable"] = False

    package_map = page_structure.get("mathpixPackageMap")
    if isinstance(package_map, dict):
        result["mathpixPackageSummary"] = page_structure.get("mathpixPackageSummary") or {}
        result["mathpixPackageMap"] = package_map
        result["mathpixMarkdownMap"] = page_structure.get("mathpixMarkdownMap") or {}
        result["mathpixAssetMap"] = page_structure.get("mathpixAssetMap") or {}
        result["mathpixPackageCompletenessAudit"] = page_structure.get("mathpixPackageCompletenessAudit") or (package_map.get("audit") or {})
        result["mathpixEnrichment"] = dict(page_structure.get("mathpixEnrichment") or {})
        result.setdefault("summary", {})["mathpixPackageAvailable"] = True
        result.setdefault("summary", {})["mathpixPackageAuditStatus"] = (package_map.get("audit") or {}).get("status")
        result.setdefault("summary", {})["mathpixPackagedAssetCount"] = ((package_map.get("audit") or {}).get("assetCount"))
        result.setdefault("summary", {})["mathpixUnreferencedPackagedAssetCount"] = ((package_map.get("audit") or {}).get("unreferencedPackagedAssetCount"))
    else:
        result["mathpixPackageSummary"] = {"available": False, "reason": "package map not present in page_structure"}
        result.setdefault("summary", {})["mathpixPackageAvailable"] = False

    return result


__all__ = ["build_page_layout_spine"]
